"""
tests/test_pitc_ccms_provider.py

Tests PitcCcmsProvider.fetch()'s orchestration (search -> loop over
results -> fetch each detail page -> assemble ProviderResult) by mocking
the HTTP layer with real captured fixture content. This is the layer the
pure parser tests (test_pitc_ccms_parser.py, test_pitc_ccms_detail_parser.py)
don't cover -- they prove the parsing logic is correct in isolation, this
proves the provider wires it together correctly (status codes, error
aggregation, the OK/PARTIAL/FAILED decision, and -- the thing that
actually broke on a real run -- using ONE shared session for both the
search and every detail request instead of independent connections).

No real network access happens in this file.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from core.models import FetchStatus
from providers.pitc_ccms import PitcCcmsProvider

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_HTML = (FIXTURES / "pitc_lalamusa_grid_search.html").read_text(encoding="utf-8")
DETAIL_HTML = (FIXTURES / "pitc_feeder_detail_011613.html").read_text(encoding="utf-8")


class FakeResponse:
    """Minimal stand-in for requests.Response -- just enough surface
    area for what PitcCcmsProvider actually calls."""

    def __init__(self, text: str, status_code: int = 200, url: str = "http://fake"):
        self.text = text
        self.status_code = status_code
        self.url = url

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def default_get_side_effect(url, params=None, headers=None, timeout=None):
    if "flsfeeder_index" in url:
        return FakeResponse(SEARCH_HTML, url=url)
    if "/detail" in url:
        return FakeResponse(DETAIL_HTML, url=url)
    raise AssertionError(f"unexpected URL in test: {url}")


class FakeSession:
    """Stand-in for requests.Session. Records every call made through it
    so tests can assert the provider is really reusing ONE session
    object across the search call and all its detail calls -- that
    reuse is the actual fix being tested here, not an implementation
    detail to mock away."""

    instances: list["FakeSession"] = []  # every FakeSession created during a test

    def __init__(self):
        self.headers = {}
        self.calls: list[dict] = []
        self.get_side_effect = default_get_side_effect
        FakeSession.instances.append(self)

    def mount(self, prefix, adapter):
        pass  # real Session mounts retry adapters here; nothing to fake

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.get_side_effect(url, params=params, headers=headers, timeout=timeout)


class TestPitcCcmsProviderFetch(unittest.TestCase):
    def setUp(self):
        FakeSession.instances = []
        session_patcher = patch("providers.pitc_ccms.requests.Session", FakeSession)
        session_patcher.start()
        self.addCleanup(session_patcher.stop)

        # The provider deliberately sleeps between detail-page requests
        # to be polite to a server we don't control (see
        # REQUEST_DELAY_SECONDS in pitc_ccms.py) -- real behavior we want
        # to keep, but it shouldn't slow down the test suite.
        sleep_patcher = patch("providers.pitc_ccms.time.sleep")
        sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_full_flow_success(self):
        provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
        result = provider.fetch()

        self.assertEqual(result.status, FetchStatus.OK)
        self.assertEqual(result.source, "pitc_ccms")
        self.assertEqual(len(result.feeders), 10)
        self.assertEqual(result.feeders[0].cycles[0].start, "00:15")

    def test_disco_from_search_results_reaches_the_final_feeders(self):
        # Regression guard for the city/disco passthrough: the search
        # step (FeederListing) is the ONLY place Disco is available --
        # the detail page parser has no way to know it. If a future edit
        # breaks the listing -> schedule wiring, this is what catches it.
        provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
        result = provider.fetch()

        self.assertTrue(all(f.disco == "GEPCO" for f in result.feeders))

    def test_uses_exactly_one_shared_session_for_search_and_all_details(self):
        """This is the actual bug that showed up on a real run: search
        succeeded but every single detail fetch failed, because each
        request was an independent connection with no shared cookies --
        unlike a real browser, which carries the search page's session
        into the detail-page click automatically. Asserting there's
        exactly one Session instance, and that it made both the search
        call and every detail call, is what would have caught this
        before it ever reached a real run."""
        provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
        provider.fetch()

        self.assertEqual(len(FakeSession.instances), 1, "expected exactly one shared Session")
        session = FakeSession.instances[0]
        # 1 search call + 10 detail calls, all through the same session
        self.assertEqual(len(session.calls), 1 + 10)
        self.assertIn("flsfeeder_index", session.calls[0]["url"])
        self.assertTrue(all("/detail" in c["url"] for c in session.calls[1:]))

    def test_detail_requests_send_referer_from_the_search_page(self):
        provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
        provider.fetch()

        session = FakeSession.instances[0]
        detail_calls = session.calls[1:]
        self.assertTrue(all(c["headers"] and "Referer" in c["headers"] for c in detail_calls))

    def test_search_failure_for_one_query_does_not_abort_others(self):
        def side_effect(url, params=None, headers=None, timeout=None):
            if params and params.get("search_value") == "BrokenCity":
                raise RuntimeError("connection reset")
            return default_get_side_effect(url, params, headers, timeout)

        original_init = FakeSession.__init__

        def patched_init(self):
            original_init(self)
            self.get_side_effect = side_effect

        with patch.object(FakeSession, "__init__", patched_init):
            provider = PitcCcmsProvider(queries=[("city", "BrokenCity"), ("grid", "Lalamusa")])
            result = provider.fetch()

        self.assertEqual(result.status, FetchStatus.PARTIAL)
        self.assertEqual(len(result.feeders), 10)
        self.assertTrue(any("BrokenCity" in e for e in result.meta["errors"]))

    def test_total_failure_when_every_query_fails(self):
        def side_effect(url, params=None, headers=None, timeout=None):
            raise RuntimeError("network down")

        original_init = FakeSession.__init__

        def patched_init(self):
            original_init(self)
            self.get_side_effect = side_effect

        with patch.object(FakeSession, "__init__", patched_init):
            provider = PitcCcmsProvider(queries=[("city", "Lahore")])
            result = provider.fetch()

        self.assertEqual(result.status, FetchStatus.FAILED)
        self.assertEqual(result.feeders, [])

    def test_one_bad_detail_page_does_not_drop_the_rest(self):
        call_count = {"n": 0}

        def side_effect(url, params=None, headers=None, timeout=None):
            if "flsfeeder_index" in url:
                return FakeResponse(SEARCH_HTML, url=url)
            if "/detail" in url:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return FakeResponse("<html><body>not what we expected</body></html>", url=url)
                return FakeResponse(DETAIL_HTML, url=url)
            raise AssertionError(f"unexpected URL: {url}")

        original_init = FakeSession.__init__

        def patched_init(self):
            original_init(self)
            self.get_side_effect = side_effect

        with patch.object(FakeSession, "__init__", patched_init):
            provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
            result = provider.fetch()

        self.assertEqual(result.status, FetchStatus.PARTIAL)
        self.assertEqual(len(result.feeders), 9)  # 10 found, 1 failed to parse
        self.assertEqual(len(result.meta["errors"]), 1)

    def test_rejects_invalid_search_type_at_construction(self):
        with self.assertRaises(ValueError):
            PitcCcmsProvider(queries=[("not_a_real_type", "X")])

    def test_max_feeders_per_query_cap_is_respected(self):
        provider = PitcCcmsProvider(queries=[("grid", "Lalamusa")])
        provider.MAX_FEEDERS_PER_QUERY = 3
        result = provider.fetch()
        self.assertEqual(len(result.feeders), 3)
        session = FakeSession.instances[0]
        self.assertEqual(len(session.calls), 1 + 3)


if __name__ == "__main__":
    unittest.main()
