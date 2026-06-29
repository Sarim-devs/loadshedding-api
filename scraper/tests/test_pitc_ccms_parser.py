"""
tests/test_pitc_ccms_parser.py

Tests the search-results parser against a real captured fixture (see
the comment at the top of tests/fixtures/pitc_lalamusa_grid_search.html
for provenance).
"""

import unittest
from pathlib import Path

from providers.pitc_ccms_parser import parse_search_results_html

FIXTURE = Path(__file__).parent / "fixtures" / "pitc_lalamusa_grid_search.html"


class TestParseSearchResultsHtml(unittest.TestCase):
    def test_against_real_captured_sample(self):
        html = FIXTURE.read_text(encoding="utf-8")
        listings = parse_search_results_html(html)

        self.assertEqual(len(listings), 10)

        first = listings[0]
        self.assertEqual(first.feeder_code, "011613")
        self.assertEqual(first.feeder_name, "Main Bazar (CD)")
        self.assertIsNone(first.city)  # blank in this real sample
        self.assertEqual(first.grid_station, "132 KV Lalamusa")  # newline collapsed
        self.assertEqual(first.disco, "GEPCO")
        self.assertTrue(first.detail_url.startswith("https://ccms.pitc.com.pk/flsfeeder/"))
        self.assertIn("search_type=grid&search_value=Lalamusa", first.detail_url)

    def test_every_row_has_a_unique_detail_url(self):
        html = FIXTURE.read_text(encoding="utf-8")
        listings = parse_search_results_html(html)
        urls = [l.detail_url for l in listings]
        self.assertEqual(len(urls), len(set(urls)), "expected every detail URL to be unique")

    def test_empty_table_returns_empty_list(self):
        html = "<html><body><table id='dynamic-table'><tr><th>x</th></tr></table></body></html>"
        self.assertEqual(parse_search_results_html(html), [])

    def test_missing_table_returns_empty_list(self):
        self.assertEqual(parse_search_results_html("<html><body>no results</body></html>"), [])

    def test_row_with_blank_feeder_code_is_skipped(self):
        # Confirmed via a real screenshot of a live city=Lahore search
        # (2026-06-19): some rows have a real detail-page button but
        # completely empty Feeder Code and Feeder Name text -- genuine
        # empty placeholder rows in PITC's own database, not something
        # worth trying to fetch a schedule for.
        html = """
        <table id="dynamic-table">
          <tr><th>Feeder Code</th><th>Feeder Name</th><th>City</th><th>Grid Station</th><th>Disco</th></tr>
          <tr>
            <td><a href="https://ccms.pitc.com.pk/flsfeeder/abc123/detail?search_type=city&search_value=Lahore"></a></td>
            <td></td>
            <td></td>
            <td>132KV CHOTA LAHORE</td>
            <td>PESCO</td>
          </tr>
          <tr>
            <td><a href="https://ccms.pitc.com.pk/flsfeeder/def456/detail?search_type=city&search_value=Lahore">133301</a></td>
            <td>LAHORE CITY</td>
            <td></td>
            <td>132KV CHOTA LAHORE</td>
            <td>PESCO</td>
          </tr>
        </table>
        """
        listings = parse_search_results_html(html)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].feeder_code, "133301")


if __name__ == "__main__":
    unittest.main()
