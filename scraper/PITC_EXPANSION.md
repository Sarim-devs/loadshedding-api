# PITC coverage expansion

Two new files, added on top of the existing, unchanged provider code:

- `candidate_cities.py` — ~206 Pakistani cities/towns, grouped by province.
- `expand_pitc_coverage.py` — drives the real `PitcCcmsProvider` across
  that whole list, one query at a time, with checkpointing.

## Why this exists

PITC's portal has no enumerable "list every city" endpoint — confirmed,
not assumed (a maintained third-party integration that reverse-engineered
the same backend independently documents the same conclusion). The
`queries=[("grid", "Lalamusa"), ("grid", "Lahore")]` list in `run.py` is
correct as far as it goes, it's just two grids. The actual missing piece
was always candidate search terms, not a missing capability in
`pitc_ccms.py` — that file already does everything needed per-query, it
was just never pointed at more than two of them.

## What's genuinely new vs. what's just reused

**Reused unchanged:** the HTTP session handling, retry/backoff,
`MAX_FEEDERS_PER_QUERY` cap, rate limiting, and all parsing in
`pitc_ccms.py` / `pitc_ccms_parser.py`. `expand_pitc_coverage.py` doesn't
reimplement any of that — it imports `PitcCcmsProvider` directly and
calls `.fetch()` once per candidate city.

**New:** checkpointing between queries (so a long run survives a Ctrl-C
or a dropped connection without losing completed work), and a small
"15 cities in a row with zero results" warning, to help tell apart
"these towns genuinely aren't in PITC's database" (expected, most of the
list will look like this) from "something's blocking every request"
(stop, don't burn through the rest of the list the same way).

**Also new, in the core models:** `FeederSchedule` now carries optional
`city` and `disco` fields, sourced from the search-results page
(`FeederListing.city` / `.disco`) and threaded through
`feeder_detail_to_schedule()`. Previously this data was fetched (it's
right there in every search result) and then discarded once the detail
page was parsed. Backward compatible — both fields default to `None`,
every existing call site and test still passes unchanged (see
`tests/test_pitc_ccms_detail_parser.py` and
`tests/test_pitc_ccms_provider.py` for the new tests covering this
specifically). This closes a loop with the FastAPI backend, which
already reads `feeder.get("city")` / `feeder.get("disco")` and prefers
them over its own city-guessing heuristic whenever they're present.

## How to run it

```bash
# ALWAYS smoke-test first -- confirms the request format still works
# against the live site before committing to a long run.
python3 expand_pitc_coverage.py --limit 10

# Check the output looks real (non-zero feeders for at least a few
# cities), then run the full list:
python3 expand_pitc_coverage.py

# If it gets interrupted (Ctrl-C, connection drop, your laptop sleeps):
python3 expand_pitc_coverage.py --resume
```

Output goes to `output/pitc_expanded_feeders.json` (one entry per
searched city, including the ones that found nothing) and
`output/pitc_expansion_checkpoint.json` (the resume state — safe to
delete once a run finishes and you're happy with the output).

### Realistic expectations

Most of the 206 candidates will come back with 0 feeders — that's
normal, not a bug. PITC's grid/feeder database doesn't cover every town
by that exact name. A `"status": "failed"` per city in the output is
literally just `PitcCcmsProvider.fetch()`'s existing behavior for "zero
results," reused here per-city instead of per the whole `queries` list.

### What to do with the output

This intentionally does NOT auto-merge into `output/schedule_latest.json`
or auto-edit `run.py`'s `queries` list — that's a judgment call worth
making with your eyes on the actual results, the same way Lalamusa and
Lahore were added to `run.py` after a real search confirmed they were
good. Once you've reviewed `pitc_expanded_feeders.json`, fold whichever
`(search_type, search_value)` pairs found real, useful data into
`run.py`'s permanent `queries` list.

## Testing

`tests/test_expand_pitc_coverage.py` covers this the same way the rest
of the suite covers everything else — `FakeSession`-mocked, no real
network, run via:

```bash
python3 -m unittest discover -v
```
