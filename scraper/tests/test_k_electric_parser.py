"""
tests/test_k_electric_parser.py

Unit tests for the pure-text parser. Uses only the standard library
(unittest) -- no pytest install required, since this is testing logic,
not hitting the network.

Run with:  python3 -m unittest discover -v
"""

import unittest
from pathlib import Path

from providers.k_electric_parser import parse_feeder_line, parse_schedule_text

FIXTURE = Path(__file__).parent / "fixtures" / "ke_sample_extracted_text.txt"


class TestParseFeederLine(unittest.TestCase):
    def test_three_active_cycles(self):
        row = parse_feeder_line(
            "36 B RMU Korangi East 1005~1205 1405~1605 1805~2005"
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.feeder_name, "36 B RMU Korangi East")
        self.assertEqual(row.cycles[0].start, "10:05")
        self.assertEqual(row.cycles[0].end, "12:05")
        self.assertEqual(row.cycles[2].end, "20:05")

    def test_dash_means_no_shed_that_cycle(self):
        row = parse_feeder_line("3A RMU Landhi - 1335~1435 -")
        self.assertIsNotNone(row)
        self.assertIsNone(row.cycles[0].start)
        self.assertEqual(row.cycles[1].start, "13:35")
        self.assertIsNone(row.cycles[2].start)

    def test_overnight_wraparound_is_kept_as_is(self):
        # 2135~0005 crosses midnight -- we deliberately do NOT try to be
        # clever about date rollover here, we just preserve both clock
        # times as given. The mobile app's consumer of this JSON is
        # responsible for date-math, not the scraper.
        row = parse_feeder_line("Afghan Basti KDA 1305~1535 1705~1935 2135~0005")
        self.assertEqual(row.cycles[2].start, "21:35")
        self.assertEqual(row.cycles[2].end, "00:05")

    def test_header_line_is_rejected(self):
        row = parse_feeder_line("Feeder Name Grids 1st Cycle 2nd Cycle 3rd Cycle")
        self.assertIsNone(row)

    def test_disclaimer_prose_is_rejected(self):
        row = parse_feeder_line(
            "Load-shed Schedule For the week of April 7 - April 14, 2021"
        )
        self.assertIsNone(row)

    def test_blank_line_is_rejected(self):
        self.assertIsNone(parse_feeder_line(""))
        self.assertIsNone(parse_feeder_line("   "))


class TestParseScheduleText(unittest.TestCase):
    def test_against_real_captured_sample(self):
        """This fixture is real text extracted from a live K-Electric
        PDF download (captured during Phase 1 research). It deliberately
        includes header/footer/disclaimer noise mixed in with data rows,
        the way the real multi-page PDF does."""
        text = FIXTURE.read_text()
        feeders = parse_schedule_text(text)

        # 16 real data rows in this fixture -- see the file itself
        self.assertEqual(len(feeders), 16)

        names = {f.feeder_name for f in feeders}
        self.assertIn("36 B RMU Korangi East", names)
        self.assertIn("Al-Hira Pumping Orangi Town", names)

        # nothing from the disclaimer paragraph should have leaked through
        self.assertNotIn("Inconvenience regretted.", names)
        for f in feeders:
            self.assertNotIn("Disclaimer", f.feeder_name)


if __name__ == "__main__":
    unittest.main()
