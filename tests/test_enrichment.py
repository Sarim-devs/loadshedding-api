"""
tests/test_enrichment.py

Unit tests for app/enrichment.py -- the derive_city/derive_grid rules
that turn raw (source, grid_or_area) pairs into the City -> Grid ->
Feeder hierarchy the Flutter app's dropdowns rely on.
"""

from __future__ import annotations

import unittest

from app.enrichment import derive_city, derive_grid, get_aliases


class TestDeriveCity(unittest.TestCase):
    def test_k_electric_is_always_karachi(self):
        # Known fact, not an inference -- K-Electric only serves Karachi.
        self.assertEqual(derive_city("k_electric", None), "Karachi")
        self.assertEqual(derive_city("k_electric", "anything"), "Karachi")

    def test_pitc_strips_voltage_prefix(self):
        self.assertEqual(derive_city("pitc_ccms", "132 KV Lala Musa"), "Lala Musa")
        self.assertEqual(derive_city("pitc_ccms", "11kV Some Feeder Area"), "Some Feeder Area")

    def test_pitc_with_no_grid_is_unknown(self):
        self.assertEqual(derive_city("pitc_ccms", None), "Unknown")

    def test_manual_override_takes_precedence_over_mechanical_fallback(self):
        from app import enrichment

        enrichment.GRID_CITY_OVERRIDES["132 KV Test Grid"] = "Manually Verified City"
        try:
            self.assertEqual(
                derive_city("pitc_ccms", "132 KV Test Grid"), "Manually Verified City"
            )
        finally:
            del enrichment.GRID_CITY_OVERRIDES["132 KV Test Grid"]

    def test_explicit_city_wins_over_everything_else(self):
        # If the scraper ever passes through PITC's own "City" column,
        # that's more authoritative than our guesses -- it should win
        # even over a manual override or the K-Electric hardcode.
        self.assertEqual(
            derive_city("pitc_ccms", "132 KV Lala Musa", explicit_city="Gujrat"),
            "Gujrat",
        )
        self.assertEqual(
            derive_city("k_electric", None, explicit_city="Some Future Sub-Area"),
            "Some Future Sub-Area",
        )


class TestDeriveGrid(unittest.TestCase):
    def test_real_grid_string_is_used_verbatim(self):
        self.assertEqual(derive_grid("132 KV Lala Musa", "Lala Musa"), "132 KV Lala Musa")

    def test_missing_grid_falls_back_to_city(self):
        # K-Electric's case: no real grid concept in the scraped data, so
        # the city itself becomes the pseudo-grid -- every feeder ends up
        # with a non-null grid, so Flutter never has to special-case a
        # missing middle dropdown tier.
        self.assertEqual(derive_grid(None, "Karachi"), "Karachi")


class TestAliases(unittest.TestCase):
    def test_unknown_city_has_no_aliases(self):
        self.assertEqual(get_aliases("Some City Not In The Table"), [])


if __name__ == "__main__":
    unittest.main()
