"""
app/enrichment.py

The scraper's raw output gives every feeder a `source` (k_electric,
pitc_ccms, ...) and sometimes a `grid_or_area` string (e.g. "132 KV Lala
Musa"). Neither of those is a "city" -- the Flutter onboarding flow needs
City -> Grid -> Feeder, but the raw data only reliably gives you
Source -> (maybe Grid) -> Feeder. This module is the small, honest bridge
between the two.

Design goal: be USEFUL today with only 2 sources, and cheap to IMPROVE
incrementally as more PITC areas get added, without ever requiring a
rewrite. Concretely:

  1. A few facts are simply known and safe to hardcode (K-Electric only
     serves Karachi -- that's not a guess, it's how K-Electric works).
  2. Everything else falls back to a mechanical transformation (strip the
     voltage-class prefix off the grid name) that's honest about being
     an approximation, not a verified administrative city. "132 KV Lala
     Musa" -> "Lala Musa" is a reasonable readable label; it is NOT a
     claim that "Lala Musa" is officially classified as a city.
  3. A manual override table (GRID_CITY_OVERRIDES) sits between the two,
     so when you (or a user bug report) notice a grid that's mislabeled
     by the mechanical fallback, fixing it is a one-line dict entry --
     no code changes, no redeploy logic changes.

This is the "lightweight enrichment strategy that can gradually improve
coverage" -- gradual means you fill in overrides over time as PITC adds
more DISCOs behind it, not that today's output is meant to be perfectly
accurate Pakistani geography.

C++ analogy
-----------
Think of this as a small lookup-table-with-fallback, the same pattern as
a device driver that checks a vendor-ID/product-ID table for a known
quirky device, and falls back to a generic driver if there's no entry --
you don't need every device modeled before the generic path is useful.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 1. Facts that are simply known, not guessed.
# ---------------------------------------------------------------------------
SOURCE_CITY_OVERRIDES: dict[str, str] = {
    # K-Electric is the sole utility for Karachi -- every feeder it
    # reports is in Karachi by construction, not by inference.
    "k_electric": "Karachi",
}

# ---------------------------------------------------------------------------
# 2. Manual corrections for specific raw grid_or_area strings, keyed
#    EXACTLY as they appear in the scraper output. Empty today -- this is
#    where you add a line whenever the mechanical fallback below gets a
#    grid's city label wrong. Nothing else in the codebase needs to
#    change when you add an entry here.
# ---------------------------------------------------------------------------
GRID_CITY_OVERRIDES: dict[str, str] = {
    # "132 KV Some Grid Name": "Actual City Name",
}

# Matches a leading voltage-class prefix like "132 KV ", "11 kV ", "220KV "
# so "132 KV Lala Musa" -> "Lala Musa". This is the mechanical fallback,
# not a geography database.
_VOLTAGE_PREFIX_RE = re.compile(r"^\d+\s*kv\s+", re.IGNORECASE)

# ---------------------------------------------------------------------------
# 3. Search foundation (NOT a search engine -- just the data shape future
#    search will read from). Empty today because none of the cities
#    currently in the dataset (Karachi, Lala Musa, the PITC grids) need
#    an alias yet. When Rawalpindi-style data shows up, this is where
#    "Pindi", "RWP" etc. get added -- the /cities endpoint already
#    returns an `aliases` field sourced from here, so wiring up real
#    substring/fuzzy search later only touches the search code itself,
#    not this data layer or the API response shape.
# ---------------------------------------------------------------------------
CITY_ALIASES: dict[str, list[str]] = {
    # "Rawalpindi": ["Pindi", "RWP"],
}


def derive_city(
    source: str, grid_or_area: str | None, explicit_city: str | None = None
) -> str:
    """Best-effort city label for a feeder. Order of precedence:
    1. An explicit city the SOURCE ITSELF reported (e.g. PITC's search
       results have their own "City" column -- if the scraper starts
       passing that through, it's strictly more authoritative than
       anything we'd guess, so it wins outright).
    2. A known fact about the source itself (K-Electric -> Karachi).
    3. A manually-verified override for this exact grid string.
    4. Mechanical fallback: strip the voltage prefix off the grid name.
    5. If there's no grid string at all, "Unknown" -- honest, not a guess.

    Today's dataset has no `city` field from any provider yet, so step 1
    never fires in practice -- this parameter exists so that the moment
    it does start arriving, the backend uses it automatically, with no
    further code changes anywhere else.
    """
    if explicit_city:
        return explicit_city

    if source in SOURCE_CITY_OVERRIDES:
        return SOURCE_CITY_OVERRIDES[source]

    if grid_or_area is None:
        return "Unknown"

    if grid_or_area in GRID_CITY_OVERRIDES:
        return GRID_CITY_OVERRIDES[grid_or_area]

    return _VOLTAGE_PREFIX_RE.sub("", grid_or_area).strip() or "Unknown"


def derive_grid(grid_or_area: str | None, city: str) -> str:
    """The display 'grid' value. If the source gave us a real grid
    string, use it verbatim (it's the more specific, technically
    accurate field). If not (K-Electric has no grid concept in the
    scraped data), fall back to the city itself, so every feeder has a
    non-null grid and the City -> Grid -> Feeder hierarchy never has to
    special-case a missing middle tier in the Flutter dropdowns."""
    return grid_or_area if grid_or_area else city


def get_aliases(city: str) -> list[str]:
    return CITY_ALIASES.get(city, [])
