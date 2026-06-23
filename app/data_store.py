"""
app/data_store.py

Loads the scraper's output JSON ONCE at process startup and keeps it in
memory as plain dicts. This is intentionally not a real database -- per
the project plan, the point of this FastAPI layer is "read from generated
JSON, do not rerun scrapers per request," and at ~600-1000 feeders the
whole dataset comfortably fits in RAM (the file on disk is well under
1 MB).

C++ analogy
-----------
This plays the role a small embedded cache layer would in C++: load a
flat file into an in-memory hash map once at startup, and every "query"
afterward is just a dict lookup -- no disk I/O, no SQL, on the request
path. Swapping this module out for a real database later (Postgres,
SQLite, whatever) means changing ONLY this file -- main.py's route
handlers call get_feeder(feeder_id) etc. and don't know or care whether
that's backed by a dict or a DB connection pool. That's the same
separation of concerns as hiding a data structure behind an interface
and giving callers only accessor functions.

Why a module-level singleton instead of a class you instantiate
-----------------------------------------------------------------
FastAPI route handlers are plain functions; there's no natural "this" to
hang state on the way a C++ singleton might hang off a static instance.
A module IS Python's natural singleton -- it's imported once, its
top-level code runs once, and every importer shares the same objects.
load() is called explicitly from main.py's startup hook rather than at
import time, so a test suite can call it with a fixture file instead of
always reading the real one.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app import enrichment

logger = logging.getLogger("loadshedding_api.data_store")

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "schedule_latest.json"

# ---------------------------------------------------------------------------
# In-memory state. Everything here is populated exactly once by load(),
# then only ever read -- no request handler mutates it, so there's no need
# for locks even though uvicorn can serve requests concurrently.
# ---------------------------------------------------------------------------
_raw: dict[str, Any] | None = None
_feeders_by_id: dict[str, dict[str, Any]] = {}
_sources: list[dict[str, Any]] = []
_skipped_feeder_count: int = 0


def _slugify(value: str) -> str:
    """'BLOCK # 5 RMU AIRPORT' -> 'block-5-rmu-airport'.

    Lowercases, collapses every run of non-alphanumeric characters into a
    single '-', and trims leading/trailing '-'. This makes the resulting
    feeder_id safe to drop straight into a URL path segment with no
    percent-encoding -- raw feeder names contain spaces, '#', '~', '+',
    none of which belong unescaped in a URL.
    """
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def _make_feeder_id(source: str, grid_or_area: str | None, feeder_name: str) -> str:
    """Builds a stable, human-readable, URL-safe ID for one feeder.

    Format: '<source>__<grid-slug>__<name-slug>', e.g.:
        k_electric__none__block-5-rmu-airport-1135-1335
        pitc_ccms__132-kv-lala-musa__paswal

    Why namespace by source AND grid, not just the feeder name: K-Electric
    and PITC don't currently share any feeder names, but PITC alone
    already spans multiple grids, and the README's roadmap adds more
    DISCOs through the same PITC backend. A generic name like "LAHORE
    CITY" reappearing under a different grid later is plausible, not
    paranoid. Namespacing up front turns a future silent ID collision
    into a non-issue, instead of a confusing bug somebody has to track
    down via a support ticket months from now.
    """
    grid_slug = _slugify(grid_or_area) if grid_or_area else "none"
    name_slug = _slugify(feeder_name)
    return f"{source}__{grid_slug}__{name_slug}"


def load(path: Path | str | None = None) -> None:
    """Reads the JSON file from disk and (re)builds the in-memory indexes.

    Called once at FastAPI startup (see main.py's lifespan handler). Safe
    to call again later (e.g. after a fresh scraper run drops a new
    schedule_latest.json) to hot-reload data without restarting the
    process -- though nothing currently triggers that automatically; see
    the project README for the manual-reload note.
    """
    global _raw, _feeders_by_id, _sources, _skipped_feeder_count

    data_path = Path(path) if path else DEFAULT_DATA_PATH
    logger.info("Loading schedule data from %s", data_path)

    with open(data_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    feeders_by_id: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    collision_counts: dict[str, int] = {}
    skipped = 0

    for source_block in raw["sources"]:
        source_name = source_block["source"]
        sources.append(
            {
                "source": source_name,
                "status": source_block["status"],
                "feeder_count": source_block["feeder_count"],
                "fetched_at": source_block.get("fetched_at"),
                "error": source_block.get("error"),
            }
        )

        for feeder in source_block.get("feeders", []):
            # One malformed feeder entry (missing "feeder_name", a bad
            # cycles shape, whatever) should not take down the entire
            # API at startup -- skip just that entry and keep going.
            # This matters more than it might look like: the scraper
            # runs unattended against sites that change their HTML/PDF
            # layout without warning, so "one record came out wrong" is
            # a realistic failure mode, not a hypothetical one.
            try:
                feeder_name = feeder["feeder_name"]
                grid_or_area = feeder.get("grid_or_area")
                cycles = feeder.get("cycles", [])

                # Not present in today's data from any provider -- these
                # are read defensively so that the day a scraper starts
                # passing PITC's own "City"/"Disco" search-result columns
                # through, this layer picks them up with zero code
                # changes. See enrichment.derive_city's docstring.
                explicit_city = feeder.get("city")
                disco = feeder.get("disco")

                feeder_id = _make_feeder_id(source_name, grid_or_area, feeder_name)

                # Defensive collision handling: if the slug scheme ever
                # produces a duplicate (e.g. two raw names differing only
                # in punctuation _slugify() strips out), don't silently
                # let the second one overwrite the first -- append a
                # numeric suffix instead. Same instinct as checking a
                # hash map insert's result in C++ rather than assuming
                # every key is unique.
                if feeder_id in collision_counts:
                    collision_counts[feeder_id] += 1
                    deduped_id = f"{feeder_id}-{collision_counts[feeder_id]}"
                    logger.warning(
                        "feeder_id collision on %r -- using %r instead",
                        feeder_id,
                        deduped_id,
                    )
                    feeder_id = deduped_id
                else:
                    collision_counts[feeder_id] = 1

                city = enrichment.derive_city(source_name, grid_or_area, explicit_city)
                grid = enrichment.derive_grid(grid_or_area, city)

                feeders_by_id[feeder_id] = {
                    "feeder_id": feeder_id,
                    "feeder_name": feeder_name,
                    "source": source_name,
                    "grid_or_area": grid_or_area,
                    "city": city,
                    "grid": grid,
                    "disco": disco,
                    "cycles": cycles,
                }
            except (KeyError, TypeError) as exc:
                skipped += 1
                logger.warning(
                    "Skipping malformed feeder entry from %s: %s -- raw entry: %r",
                    source_name,
                    exc,
                    feeder,
                )

    _raw = raw
    _feeders_by_id = feeders_by_id
    _sources = sources
    _skipped_feeder_count = skipped
    logger.info(
        "Loaded %d feeders across %d sources (run_at=%s, skipped=%d)",
        len(_feeders_by_id),
        len(_sources),
        raw.get("run_at"),
        skipped,
    )


def is_loaded() -> bool:
    return _raw is not None


def get_run_at() -> str:
    assert _raw is not None, "data_store.load() must be called before use"
    return _raw["run_at"]


def get_sources() -> list[dict[str, Any]]:
    return _sources


def get_all_feeders() -> list[dict[str, Any]]:
    return list(_feeders_by_id.values())


def get_feeder(feeder_id: str) -> dict[str, Any] | None:
    return _feeders_by_id.get(feeder_id)


def total_feeder_count() -> int:
    return len(_feeders_by_id)


def get_skipped_feeder_count() -> int:
    """How many raw feeder entries failed to load (missing fields, bad
    shape) and were skipped rather than crashing startup. Surfaced
    through /health and /stats so a malformed scraper run is visible,
    not silent."""
    return _skipped_feeder_count


# ---------------------------------------------------------------------------
# City / grid / hierarchy aggregation.
#
# These all just iterate the (already in-memory) feeder list -- no
# separate index is pre-built and cached for this. At ~636 rows that
# linear pass takes well under a millisecond, so a cache would add
# invalidation complexity for a speed gain nobody would ever notice.
# Revisit ONLY if the feeder count grows by orders of magnitude.
# ---------------------------------------------------------------------------


def get_cities() -> list[dict[str, Any]]:
    """One row per distinct city: how many feeders, how many distinct
    grids, and any known search aliases (see enrichment.CITY_ALIASES)."""
    by_city: dict[str, dict[str, Any]] = {}
    for feeder in _feeders_by_id.values():
        city = feeder["city"]
        bucket = by_city.setdefault(
            city, {"city": city, "feeder_count": 0, "grids": set()}
        )
        bucket["feeder_count"] += 1
        bucket["grids"].add(feeder["grid"])

    return [
        {
            "city": city,
            "feeder_count": bucket["feeder_count"],
            "grid_count": len(bucket["grids"]),
            "aliases": enrichment.get_aliases(city),
        }
        for city, bucket in sorted(by_city.items())
    ]


def get_grids(city: str | None = None) -> list[dict[str, Any]]:
    """One row per distinct (city, grid) pair, optionally filtered to a
    single city. A grid name alone isn't guaranteed globally unique
    (two different cities could each have a "Grid A" some day), so the
    grouping key is the pair, not the grid name by itself."""
    by_pair: dict[tuple[str, str], int] = {}
    for feeder in _feeders_by_id.values():
        if city is not None and feeder["city"] != city:
            continue
        key = (feeder["city"], feeder["grid"])
        by_pair[key] = by_pair.get(key, 0) + 1

    return [
        {"city": c, "grid": g, "feeder_count": count}
        for (c, g), count in sorted(by_pair.items())
    ]


def get_hierarchy() -> list[dict[str, Any]]:
    """The full City -> Grid -> Feeder tree in one payload, built for a
    single Flutter onboarding network call (fetch once, drive all three
    dropdowns client-side, no round-trip per dropdown change). Each leaf
    feeder is trimmed to just {feeder_id, feeder_name} -- the full
    schedule is fetched separately via /schedule/{feeder_id} only once
    the user actually picks one, so this payload stays light even though
    it covers every feeder."""
    cities: dict[str, dict[str, Any]] = {}

    for feeder in _feeders_by_id.values():
        city_bucket = cities.setdefault(
            feeder["city"], {"city": feeder["city"], "grids": {}}
        )
        grid_bucket = city_bucket["grids"].setdefault(
            feeder["grid"], {"grid": feeder["grid"], "feeders": []}
        )
        grid_bucket["feeders"].append(
            {"feeder_id": feeder["feeder_id"], "feeder_name": feeder["feeder_name"]}
        )

    result = []
    for city_name, city_bucket in sorted(cities.items()):
        grids = []
        city_feeder_count = 0
        for grid_name, grid_bucket in sorted(city_bucket["grids"].items()):
            feeders = sorted(grid_bucket["feeders"], key=lambda f: f["feeder_name"])
            grids.append(
                {"grid": grid_name, "feeder_count": len(feeders), "feeders": feeders}
            )
            city_feeder_count += len(feeders)
        result.append(
            {"city": city_name, "feeder_count": city_feeder_count, "grids": grids}
        )

    return result
