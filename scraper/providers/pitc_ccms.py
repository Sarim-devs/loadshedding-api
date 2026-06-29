"""
providers/pitc_ccms.py

CORRECTED TWICE from the original Phase 1 assumptions, both times by
real captures from the live site rather than guessing:

  1. The feeder search (flsfeeder_index) turned out to be a plain GET,
     not an AJAX/JSON call -- confirmed via a HAR capture.
  2. The per-feeder detail/schedule page (flsfeeder/<hash>/detail) ALSO
     turned out to be plain server-rendered HTML, not AJAX -- confirmed
     via a second HAR capture + saved page source. Two tables: one with
     feeder info (status, loss %, category), one with a 24-point
     ON/OFF/Actual schedule grid. See providers/pitc_ccms_parser.py for
     the full structure notes.

Full real flow, both steps confirmed and unit-tested:

    1. GET flsfeeder_index?search_type=<city|grid|feeder_name|feeder_code>
       &search_value=<text>
       -> HTML page, <table id="dynamic-table"> of matching feeders,
          each linking to step 2.

    2. GET flsfeeder/<hash>/detail?search_type=...&search_value=...
       -> HTML page, schedule grid -> converted into the normal
          FeederSchedule shape (today's OFF windows as Cycle ranges) via
          providers/pitc_ccms_parser.feeder_detail_to_schedule().

No browser, no AJAX, no Selenium -- pure `requests` + `bs4` end to end,
exactly what the project brief asked for as the primary method.

Ethical scope, unchanged since the first version of this file: this
provider only ever uses the public city/grid/feeder search. It must
never be extended to use the 14-digit customer bill reference number
lookup that the same site offers -- that's personal account data
(CNIC, address, bill amount), not public schedule data, and scraping it
would be a real ethics/ToS problem even though the page technically
allows it.
"""

from __future__ import annotations

import logging
import time

import requests
from urllib3.util import Retry

from core.models import FeederSchedule, FetchStatus, ProviderResult
from providers.base import ScheduleProvider
from providers.pitc_ccms_parser import (
    VALID_SEARCH_TYPES,
    FeederListing,
    diagnose_feeder_detail_html,
    feeder_detail_to_schedule,
    parse_feeder_detail_html,
    parse_search_results_html,
)

logger = logging.getLogger(__name__)


class PitcCcmsProvider(ScheduleProvider):
    SEARCH_URL = "https://ccms.pitc.com.pk/flsfeeder_index"
    REQUEST_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    TIMEOUT_SECONDS = 15

    # Each feeder found by a search costs one extra HTTP request (the
    # detail page). A city search could plausibly return a lot of rows --
    # cap it per query so one broad search can't turn into hundreds of
    # sequential requests against someone else's server. Bump this once
    # you have a real sense of typical result-set sizes.
    MAX_FEEDERS_PER_QUERY = 20

    # Small pause between detail-page requests. Bumped up from an
    # original 0.2s after a real session produced a RemoteDisconnected
    # error mid-run -- the server (or something in front of it) silently
    # killed a connection after a sustained burst of automated requests,
    # which looks like the server pushing back on request volume rather
    # than a one-off network blip. Still a guess at the "right" pace, not
    # a number PITC told us to use -- but a more conservative guess than
    # before, on purpose, after real evidence the prior pace wasn't free.
    REQUEST_DELAY_SECONDS = 1.0

    # A few automatic retries with backoff for exactly the failure class
    # that showed up in practice (connection reset/aborted mid-request) --
    # NOT a workaround for getting blocked-as-a-bot; if the server is
    # actively rejecting this client, retrying immediately just adds to
    # the problem. This is for ordinary transient network hiccups, scoped
    # narrowly to connection-level failures, not HTTP error status codes
    # (a real 403/429 should surface as a real error, not get silently
    # retried into looking like it succeeded).
    RETRY_TOTAL = 2
    RETRY_BACKOFF_FACTOR = 1.5

    def __init__(self, queries: list[tuple[str, str]] | None = None):
        """queries: list of (search_type, search_value) pairs, e.g.
        [("city", "Lahore"), ("grid", "Islamabad")]. Valid search_type
        values are confirmed from the page's own dropdown -- see
        providers.pitc_ccms_parser.VALID_SEARCH_TYPES."""
        self.queries = queries or [("city", "Lahore")]
        for search_type, _ in self.queries:
            if search_type not in VALID_SEARCH_TYPES:
                raise ValueError(
                    f"invalid search_type {search_type!r}, must be one of {VALID_SEARCH_TYPES}"
                )

    @property
    def name(self) -> str:
        return "pitc_ccms"

    def fetch(self) -> ProviderResult:
        all_schedules: list[FeederSchedule] = []
        errors: list[str] = []

        # A single shared Session for the whole fetch() call, not a bare
        # requests.get() per call. This matters: a real browser carries
        # whatever cookies the search page sets into the detail-page
        # request automatically; two independent requests.get() calls
        # don't share anything by default. The fixture-based tests never
        # caught this because the fixture was captured BY a real browser
        # (cookies already applied), so the parser itself looked fine in
        # isolation -- the bug was in how the provider made requests, not
        # in how it parsed them. See README Changelog for how this showed
        # up: search succeeded, every single detail fetch failed to parse.
        session = requests.Session()
        session.headers.update(self.REQUEST_HEADERS)
        # Retries cover connection-level failures (reset, aborted, DNS
        # hiccup) -- NOT HTTP error statuses. status_forcelist is left
        # empty on purpose: a real 403/429 from the server is meaningful
        # signal (possibly "you're being blocked") and should surface as
        # an error, not get masked by an automatic retry.
        retry = Retry(
            total=self.RETRY_TOTAL,
            backoff_factor=self.RETRY_BACKOFF_FACTOR,
            status_forcelist=[],
        )
        adapter = requests.adapters.HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        for search_type, search_value in self.queries:
            try:
                search_resp = session.get(
                    self.SEARCH_URL,
                    params={"search_type": search_type, "search_value": search_value},
                    timeout=self.TIMEOUT_SECONDS,
                )
                search_resp.raise_for_status()
                listings = parse_search_results_html(search_resp.text)
            except Exception as exc:  # noqa: BLE001
                logger.error("pitc_ccms: search failed for %s=%s: %s", search_type, search_value, exc)
                errors.append(f"search {search_type}={search_value}: {exc}")
                continue

            logger.info(
                "pitc_ccms: %s=%s -> %d feeders found", search_type, search_value, len(listings)
            )

            if len(listings) > self.MAX_FEEDERS_PER_QUERY:
                logger.warning(
                    "pitc_ccms: %s=%s returned %d feeders, capping detail "
                    "fetches at %d -- see MAX_FEEDERS_PER_QUERY",
                    search_type, search_value, len(listings), self.MAX_FEEDERS_PER_QUERY,
                )
                listings = listings[: self.MAX_FEEDERS_PER_QUERY]

            for listing in listings:
                schedule, error = self._fetch_one_schedule(session, listing, referer=search_resp.url)
                if schedule is not None:
                    all_schedules.append(schedule)
                else:
                    errors.append(error)
                time.sleep(self.REQUEST_DELAY_SECONDS)

        if not all_schedules:
            return ProviderResult(
                source=self.name,
                status=FetchStatus.FAILED,
                error="; ".join(errors) or "no feeders found for any configured query",
            )

        status = FetchStatus.OK if not errors else FetchStatus.PARTIAL
        return ProviderResult(
            source=self.name,
            status=status,
            feeders=all_schedules,
            meta={"errors": errors} if errors else {},
        )

    def _fetch_one_schedule(
        self, session: requests.Session, listing: FeederListing, referer: str
    ) -> tuple[FeederSchedule | None, str | None]:
        """Returns (schedule, None) on success or (None, error_message) on
        failure -- never raises, so one bad feeder in a batch can't stop
        the rest from being fetched (same isolate-the-failure principle
        as core/registry.py, just one level down). `referer` is set to
        the search results page's own URL -- accurate (that IS where this
        link was found) and a common thing real sites check before
        honoring a detail-page request."""
        ident = listing.feeder_code or listing.detail_url  # fall back if code came back blank
        try:
            resp = session.get(
                listing.detail_url,
                headers={"Referer": referer},
                timeout=self.TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.error("pitc_ccms: detail fetch failed for %s: %s", ident, exc)
            return None, f"{ident}: detail fetch failed: {exc}"

        detail = parse_feeder_detail_html(resp.text)
        if detail is None:
            # Structural diagnosis instead of a flat text snippet -- a
            # snippet of a real ~30KB page is almost always just <head>
            # boilerplate, useless for figuring out which assumption
            # broke. This pinpoints table count / row shape directly.
            diagnosis = diagnose_feeder_detail_html(resp.text)
            logger.error(
                "pitc_ccms: detail parse failed for %s (status=%d, len=%d) -- %s",
                ident, resp.status_code, len(resp.text), diagnosis,
            )
            return None, f"{ident}: detail page did not match expected structure"

        return (
            feeder_detail_to_schedule(
                detail, city=listing.city, disco=listing.disco, fallback_name=listing.feeder_name
            ),
            None,
        )
