"""
tests/test_pitc_ccms_detail_parser.py

Tests for the per-feeder detail/schedule page parser. The fixture is the
real, complete, unmodified HTML response captured via HAR for:

    GET https://ccms.pitc.com.pk/flsfeeder/69df528249dcf/detail?search_type=grid&search_value=Lalamusa

(feeder 011613, MAIN BAZAR (LALA MUSA), captured 2026-06-19).
"""

import unittest
from pathlib import Path

from core.models import Cycle
from providers.pitc_ccms_parser import (
    _add_one_hour,
    _merge_off_runs_to_cycles,
    diagnose_feeder_detail_html,
    feeder_detail_to_schedule,
    parse_feeder_detail_html,
)

FIXTURE = Path(__file__).parent / "fixtures" / "pitc_feeder_detail_011613.html"


class TestParseFeederDetailHtml(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text(encoding="utf-8")
        self.detail = parse_feeder_detail_html(self.html)

    def test_basic_info_fields(self):
        d = self.detail
        self.assertEqual(d.feeder_code, "011613")
        self.assertEqual(d.feeder_name, "MAIN BAZAR (LALA MUSA)")
        self.assertEqual(d.grid_station, "132 KV Lala Musa")
        self.assertEqual(d.feeder_status, "ON")
        self.assertEqual(d.category, "I")
        self.assertEqual(d.loss_percent, "2.44")

    def test_date_label_kept_raw_not_misparsed(self):
        # Real page shows "Date 2026-19-06" -- DAY-MONTH swapped vs ISO 8601.
        # This must come through as the raw string, not silently
        # reinterpreted as June (case it would be under YYYY-MM-DD).
        self.assertEqual(self.detail.date_label, "2026-19-06")

    def test_each_series_has_24_chronologically_ordered_points(self):
        for series in (
            self.detail.tomorrow_schedule,
            self.detail.today_schedule,
            self.detail.today_actual,
            self.detail.yesterday_schedule,
        ):
            self.assertEqual(len(series), 24)
            labels = [label for label, _ in series]
            self.assertEqual(
                labels,
                [f"{h:02d}:15" for h in range(24)],
                "series must be in true chronological order, not AM/PM-interleaved",
            )

    def test_today_schedule_off_hours_match_real_page(self):
        # Confirmed visually from the captured screenshot: OFF at 00:15,
        # 18:15, 20:15; ON everywhere else.
        off_labels = {label for label, status in self.detail.today_schedule if status == "OFF"}
        self.assertEqual(off_labels, {"00:15", "18:15", "20:15"})

    def test_today_actual_has_none_for_hours_not_yet_occurred(self):
        # Page was captured with "now" around 14:xx -- hours from 16:15
        # onward show "-" (not yet happened today).
        actual_by_label = dict(self.detail.today_actual)
        self.assertIsNone(actual_by_label["16:15"])
        self.assertIsNone(actual_by_label["23:15"])
        self.assertEqual(actual_by_label["14:15"], "ON")  # already happened

    def test_missing_tables_returns_none(self):
        self.assertIsNone(parse_feeder_detail_html("<html><body>nothing here</body></html>"))


class TestBareCodeFeederWithNoAttachedName(unittest.TestCase):
    """Regression coverage for a real bug found live (2026-06-22): some
    feeders' "Feeder:" cell holds ONLY the code, no " - <name>" suffix
    at all (confirmed: 061720, 105002, 000623, 075327 -- all found via
    city-name search, never seen via the original grid-name search the
    main fixture came from). The old code treated a separator-less value
    as a NAME, which left feeder_code as None and made the whole page
    register as unparseable even though a real code -- and a real,
    if much smaller, schedule grid -- were both present.

    This synthetic HTML is built from the exact real values confirmed
    against the live site (see the conversation that found this bug),
    not invented -- same standard as every other fixture in this file,
    just inline instead of a saved file since this is a small, easily
    reproduced shape rather than a 30KB real page worth saving whole."""

    BARE_CODE_HTML = """
    <html><body>
    Date 2026-22-06
    <table>
      <tr><td><b>Feeder:</b>061720</td></tr>
      <tr><td><b>Grid:</b>132 KV Bhogiwal</td></tr>
    </table>
    <table>
      <tr><td>Tomorrow</td><td>x</td><td>x</td><td>x</td><td>x</td>
          <td>Today</td><td>x</td><td>x</td></tr>
      <tr><td>00:15</td><td>ON</td><td>OFF</td><td>OFF</td><td>-</td>
          <td>12:15</td><td>ON</td><td>ON</td><td>ON</td><td>-</td></tr>
    </table>
    </body></html>
    """

    def test_bare_code_is_parsed_as_the_code_not_a_name(self):
        detail = parse_feeder_detail_html(self.BARE_CODE_HTML)
        self.assertIsNotNone(detail, "a bare code should be enough to parse the page")
        self.assertEqual(detail.feeder_code, "061720")
        self.assertIsNone(detail.feeder_name)  # genuinely not on this page
        self.assertEqual(detail.grid_station, "132 KV Bhogiwal")

    def test_schedule_to_dict_uses_fallback_name_when_detail_page_has_none(self):
        detail = parse_feeder_detail_html(self.BARE_CODE_HTML)
        schedule = feeder_detail_to_schedule(detail, fallback_name="Bhogiwal Feeder (from search)")
        self.assertEqual(schedule.feeder_name, "Bhogiwal Feeder (from search)")

    def test_schedule_falls_back_to_the_code_itself_as_a_last_resort(self):
        # No detail-page name AND no fallback_name supplied (e.g. the
        # search listing's own name cell was also blank) -- must still
        # never end up with an empty/None feeder_name on the final
        # FeederSchedule, since that's the field every consumer displays.
        detail = parse_feeder_detail_html(self.BARE_CODE_HTML)
        schedule = feeder_detail_to_schedule(detail)
        self.assertEqual(schedule.feeder_name, "061720")

    def test_sparse_schedule_grid_still_produces_whatever_cycles_it_can(self):
        # Only one 10-cell schedule row exists in this synthetic page
        # (matching the real "[8, 10]" cell-count pattern confirmed
        # live) -- not the usual 24-point grid. This must degrade
        # gracefully to a short/partial cycle list, not crash and not
        # silently produce nothing when there IS real data present.
        detail = parse_feeder_detail_html(self.BARE_CODE_HTML)
        schedule = feeder_detail_to_schedule(detail)
        # 00:15 OFF, 12:15 ON -> one OFF point at 00:15 becomes a
        # 1-hour cycle; far less than a full day, but real, not absent.
        self.assertEqual([(c.start, c.end) for c in schedule.cycles], [("00:15", "01:15")])



    def test_normal_case(self):
        self.assertEqual(_add_one_hour("14:15"), "15:15")

    def test_midnight_wraparound(self):
        self.assertEqual(_add_one_hour("23:15"), "00:15")


class TestMergeOffRunsToCycles(unittest.TestCase):
    def test_real_data_three_isolated_off_hours(self):
        with open(FIXTURE, encoding="utf-8") as f:
            detail = parse_feeder_detail_html(f.read())
        cycles = _merge_off_runs_to_cycles(detail.today_schedule)
        self.assertEqual(
            [(c.start, c.end) for c in cycles],
            [("00:15", "01:15"), ("18:15", "19:15"), ("20:15", "21:15")],
        )

    def test_consecutive_off_rows_merge_into_one_cycle(self):
        series = [
            ("10:15", "ON"),
            ("11:15", "OFF"),
            ("12:15", "OFF"),
            ("13:15", "OFF"),
            ("14:15", "ON"),
        ]
        cycles = _merge_off_runs_to_cycles(series)
        self.assertEqual(len(cycles), 1)
        self.assertEqual((cycles[0].start, cycles[0].end), ("11:15", "14:15"))

    def test_unknown_dash_breaks_a_run_same_as_on(self):
        # A "-" must never be silently absorbed into an OFF run.
        series = [("10:15", "OFF"), ("11:15", None), ("12:15", "OFF")]
        cycles = _merge_off_runs_to_cycles(series)
        self.assertEqual(
            [(c.start, c.end) for c in cycles],
            [("10:15", "11:15"), ("12:15", "13:15")],
        )

    def test_no_off_hours_returns_empty_list(self):
        series = [("10:15", "ON"), ("11:15", "ON")]
        self.assertEqual(_merge_off_runs_to_cycles(series), [])

    def test_regression_does_not_fuse_non_adjacent_hours(self):
        """The bug this guards against: if a series were ever passed in
        AM/PM-interleaved order (00:15, 12:15, 01:15, 13:15, ...) instead
        of true chronological order, two OFF entries that are actually 12
        hours apart could land next to each other and get wrongly merged
        into one enormous cycle. This test calls the merge function
        directly with an interleaved input to document the contract: the
        function trusts its input is chronological and does NOT re-sort
        internally -- so parse_feeder_detail_html() is responsible for
        producing chronological order before calling this, which
        test_each_series_has_24_chronologically_ordered_points (above)
        separately enforces."""
        interleaved_and_wrong = [("00:15", "OFF"), ("12:15", "OFF"), ("01:15", "ON")]
        cycles = _merge_off_runs_to_cycles(interleaved_and_wrong)
        # Demonstrates the function fuses whatever's adjacent in its
        # input -- proving correctness lives in the caller's ordering,
        # not here. If this assertion ever fails, the function's
        # contract changed and parse_feeder_detail_html's ordering
        # guarantee needs re-checking.
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0].start, "00:15")


class TestFeederDetailToSchedule(unittest.TestCase):
    def test_maps_onto_minimal_cross_source_shape(self):
        with open(FIXTURE, encoding="utf-8") as f:
            detail = parse_feeder_detail_html(f.read())
        schedule = feeder_detail_to_schedule(detail)
        self.assertEqual(schedule.feeder_name, "MAIN BAZAR (LALA MUSA)")
        self.assertEqual(schedule.grid_or_area, "132 KV Lala Musa")
        self.assertEqual(len(schedule.cycles), 3)
        self.assertIn("011613", schedule.raw_location)

    def test_city_and_disco_default_to_none_when_not_supplied(self):
        # feeder_detail_to_schedule has no way to know city/disco on its
        # own -- the detail page doesn't carry them, only the search
        # RESULTS page does (FeederListing). Calling it the old way
        # (just `detail`, no keywords) must keep working unchanged.
        with open(FIXTURE, encoding="utf-8") as f:
            detail = parse_feeder_detail_html(f.read())
        schedule = feeder_detail_to_schedule(detail)
        self.assertIsNone(schedule.city)
        self.assertIsNone(schedule.disco)

    def test_city_and_disco_pass_through_when_supplied(self):
        # This is what PitcCcmsProvider._fetch_one_schedule actually
        # does: pulls city/disco off the FeederListing from the search
        # step and threads them through here, since the detail page
        # alone can't supply them.
        with open(FIXTURE, encoding="utf-8") as f:
            detail = parse_feeder_detail_html(f.read())
        schedule = feeder_detail_to_schedule(detail, city="Gujrat", disco="GEPCO")
        self.assertEqual(schedule.city, "Gujrat")
        self.assertEqual(schedule.disco, "GEPCO")
        self.assertIn("city", schedule.to_dict())
        self.assertIn("disco", schedule.to_dict())


class TestDiagnoseFeederDetailHtml(unittest.TestCase):
    def test_reports_table_count_when_too_few(self):
        msg = diagnose_feeder_detail_html("<html><body>nothing here</body></html>")
        self.assertIn("0 <table>", msg)
        self.assertIn("stopping here", msg)

    def test_reports_wrong_cell_count_in_schedule_rows(self):
        html = (
            "<table><tr><td><b>Feeder:</b>X - Y</td></tr></table>"
            "<table><tr>" + "<td>a</td>" * 9 + "</tr></table>"  # 9, not 10
        )
        msg = diagnose_feeder_detail_html(html)
        self.assertIn("cell-counts=[9]", msg)

    def test_against_real_fixture_reports_healthy_structure(self):
        with open(FIXTURE, encoding="utf-8") as f:
            msg = diagnose_feeder_detail_html(f.read())
        self.assertIn("2 <table>", msg)
        self.assertIn("'Feeder:'", msg)
        self.assertIn("'Grid:'", msg)
        # 12 real data rows should show cell-counts of 10
        self.assertIn("10, 10, 10", msg)

    def test_never_raises_on_garbage_input(self):
        # Should degrade gracefully, not throw, even on input that isn't
        # really HTML at all -- this runs inside an error-logging path,
        # the last thing we want is a diagnostic helper crashing there.
        try:
            diagnose_feeder_detail_html("")
            diagnose_feeder_detail_html("not html at all {{{")
        except Exception as exc:  # noqa: BLE001
            self.fail(f"diagnose_feeder_detail_html raised: {exc}")


if __name__ == "__main__":
    unittest.main()
