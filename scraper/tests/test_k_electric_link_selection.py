"""
tests/test_k_electric_link_selection.py

Regression test for a real bug: K-Electric's site lists multiple PDFs
(schedule, defaulters list, feeder losses) all sharing the link text
"Download," so the only way to tell them apart is the filename. The URLs
below are the EXACT three links captured from the live site on
2026-06-19 -- this test exists specifically so a future change to the
selection heuristic can't silently start picking the wrong PDF again.
"""

import unittest

from providers.k_electric import KElectricProvider

# Captured verbatim from https://ke.com.pk/load-shed-schedule/ on 2026-06-19
REAL_CANDIDATES = [
    "https://ke.com.pk/wp-content/uploads/2026/06/LSMnew-PDF-June-2026.pdf",
    "https://ke.com.pk/wp-content/uploads/2026/05/Defaulters-List.pdf",
    "https://ke.com.pk/wp-content/uploads/2026/05/Feeder-Losses.pdf",
]


class TestSelectSchedulePdfUrl(unittest.TestCase):
    def test_picks_the_schedule_not_the_other_reports(self):
        selected = KElectricProvider._select_schedule_pdf_url(REAL_CANDIDATES)
        self.assertEqual(selected, REAL_CANDIDATES[0])

    def test_order_in_the_list_does_not_matter(self):
        shuffled = [REAL_CANDIDATES[2], REAL_CANDIDATES[0], REAL_CANDIDATES[1]]
        selected = KElectricProvider._select_schedule_pdf_url(shuffled)
        self.assertEqual(selected, REAL_CANDIDATES[0])

    def test_single_unambiguous_candidate_is_accepted(self):
        selected = KElectricProvider._select_schedule_pdf_url([REAL_CANDIDATES[0]])
        self.assertEqual(selected, REAL_CANDIDATES[0])

    def test_raises_loudly_when_nothing_matches_the_schedule_hints(self):
        with self.assertRaises(RuntimeError):
            KElectricProvider._select_schedule_pdf_url(REAL_CANDIDATES[1:])  # only the 2 reports

    def test_raises_loudly_on_totally_unrecognized_filenames(self):
        with self.assertRaises(RuntimeError):
            KElectricProvider._select_schedule_pdf_url(
                ["https://ke.com.pk/wp-content/uploads/2026/06/some-unrelated-file.pdf"]
            )


if __name__ == "__main__":
    unittest.main()
