# Load Shedding Scheduler — FastAPI Layer (Phase 2)

This is the API layer that sits between the Python scrapers (which you
already built and proved working — 621 K-Electric feeders, 15 PITC
feeders) and the future Flutter app. It does **not** scrape anything
itself. It reads `data/schedule_latest.json` once at startup and serves
it as clean, purpose-built JSON endpoints.

This matches the architecture from the project README:

```
Python Scraper  →  schedule_latest.json  →  FastAPI  →  Flutter
```

## Why this layer exists at all

You could point Flutter straight at the JSON file, but that file is a
**dump of everything** — both sources, every feeder, every field the
scraper happened to produce. Flutter would have to do filtering,
ID-generation, and "is the power out right now" time-math on-device, in
Dart, for a problem that's identical for every user. An API layer means:

- That logic is written once, in one place, in a language you already know.
- The app downloads only what one screen needs (one feeder's schedule),
  not the whole 316 KB file every time.
- You can swap the JSON file for a real database later (see
  `app/data_store.py`) without touching Flutter at all — the HTTP
  contract stays identical.

## Quickstart

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open **http://127.0.0.1:8000/docs** — FastAPI auto-generates an
interactive Swagger page from the code below; you can click "Try it
out" on any endpoint without writing a single line of client code.

Run the tests (no server needed — these test the time-math directly):

```bash
python3 -m unittest discover -v
```

## File-by-file

| File | Responsibility |
|---|---|
| `app/main.py` | The FastAPI app and all route handlers. Stays thin — just HTTP plumbing. |
| `app/data_store.py` | Loads `data/schedule_latest.json` once into memory, builds a `feeder_id → feeder` index, plus city/grid aggregation helpers. The "database" for now. |
| `app/enrichment.py` | Derives a `city` and `grid` for every feeder from raw `(source, grid_or_area)` data. Growable override tables, not a finished geography database — see its own docstring. |
| `app/time_utils.py` | Pure date/time math: given a feeder's cycles and the current moment, is it out right now, and when's the next outage. No FastAPI dependency — testable on its own. |
| `app/models.py` | Pydantic models — the public shape of every HTTP response. |
| `tests/test_time_utils.py` | Unit tests for the midnight-crossing cycle math. |
| `tests/test_enrichment.py` | Unit tests for the city/grid derivation rules. |
| `tests/test_api.py` | Integration tests that boot the real app + real data and hit actual HTTP routes — catches wiring bugs the unit tests can't. |
| `data/schedule_latest.json` | Your scraper's output, copied in as-is. Replace this file with a fresh run's output to update the served data (see "Updating the data" below). |

This mirrors the shape of the scraper project on purpose: `app/models.py`
here is the HTTP-facing twin of `core/models.py` there, the same way
`app/data_store.py` plays the role `core/registry.py` did — a layer that
isolates the rest of the code from the messy details of one specific data
source.

## All endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Is the service up and is data loaded? For the app's "can't reach server" state. |
| `GET /stats` | One-call overview: per-source status + per-city counts + totals. Debug/dashboard use. |
| `GET /sources` | Per-source status (K-Electric: ok, 621 feeders / PITC: ok, 15 feeders). |
| `GET /cities` | Top level of the picker — every city, with feeder/grid counts and (currently empty) search aliases. |
| `GET /grids?city=` | Second level of the picker — grids within a city (or all grids if `city` omitted). |
| `GET /hierarchy` | The full City → Grid → Feeder tree in **one** response — what the Flutter Area Selection screen should fetch once. |
| `GET /feeders` | Flat feeder list. Filters: `?city=`, `?grid=`, `?source=`, `?search=` (all combine with AND). |
| `GET /schedule/{feeder_id}` | Full cycle list for one feeder — what the "Today's Schedule" timeline renders. |
| `GET /next-outage/{feeder_id}` | Is the power out *right now*, and when's the next change. The home-screen headline number. |

`feeder_id` is a generated ID (e.g. `pitc_ccms__132-kv-lala-musa__paswal`),
not the raw feeder name — get it from `/feeders` or `/hierarchy` first.
See `data_store._make_feeder_id` for why a composite ID was chosen over
the raw name.

## The one genuinely tricky part: midnight-crossing cycles

Outage cycles are stored as `"23:35"` to `"02:05"` — a wall-clock window
that repeats daily. About 1 in 6 cycles in your actual dataset cross
midnight like this, so "is the power out right now" can't just compare
two `HH:MM` strings — it has to know whether `02:05` means *tonight* or
*tomorrow morning*, relative to the cycle's start.

`time_utils.next_outage()` handles this by turning each abstract cycle
into three concrete, dated occurrences (anchored to yesterday, today, and
tomorrow), then comparing real datetimes. `tests/test_time_utils.py`
specifically exercises the case that breaks a naive implementation: a
cycle that started *yesterday* but is still in progress *after* midnight.
This is the part of the codebase most worth re-reading if you're trying
to build C++ → Python instincts, since it's a place where the "obvious"
direct-translation approach (compare `now.time()` against two `HH:MM`
strings) silently gives wrong answers.

## City / Grid enrichment (new)

The raw scraper data only reliably gives you `source` and sometimes
`grid_or_area` — neither is a "city." `app/enrichment.py` bridges that
gap with a small, growable rule set:

1. **Known facts get hardcoded.** K-Electric only serves Karachi, so
   every K-Electric feeder is Karachi — that's not a guess.
2. **Everything else falls back mechanically.** A PITC grid like
   `"132 KV Lala Musa"` becomes city `"Lala Musa"` by stripping the
   voltage-class prefix. This is a readable label, not a verified
   administrative claim — `"132 KV Chota Lahore"` becomes city
   `"Chota Lahore"`, which may or may not be how that area is officially
   classified. Good enough for an MVP dropdown; not a geography database.
3. **`GRID_CITY_OVERRIDES`** is where you fix a specific grid's city
   label by hand once you notice it's wrong or want to merge it under a
   bigger city name — one dict entry, no code changes elsewhere.

Right now this gives you 3 cities (Karachi, Lala Musa, Chota Lahore),
each with exactly 1 grid. That's expected at 2-source scale — the
hierarchy will naturally fill out as more PITC areas get scraped, with
zero structural changes needed here.

**Search foundation:** `enrichment.CITY_ALIASES` and the `aliases` field
on every `/cities` row exist now, populated with nothing, specifically so
that adding real aliases later (`"Rawalpindi": ["Pindi", "RWP"]`) is a
pure data change — no endpoint, model, or Flutter-side shape changes
required when you actually build search.

## Error handling

What's actually worth doing at this layer (and what isn't):

- **Per-feeder error isolation in `data_store.load()`.** One malformed
  feeder entry (missing a field, unexpected shape) is skipped and
  logged, not a startup crash. The skip count is surfaced through
  `/health` and `/stats` so a bad scraper run is visible, not silent.
- **A global exception handler** turns any unanticipated bug into a
  clean JSON 500 instead of a raw traceback reaching the Flutter app.
- **Retries and timeouts are deliberately NOT here.** This API makes
  zero outbound network calls on the request path — every response is
  an in-memory dict lookup. Retry/backoff logic belongs in the
  *scraper* project (already flagged there as a known gap, separate
  concern), not in this API layer, which has nothing to retry against.
- **Rate limiting** (`slowapi`, 120 requests/minute per IP, applied
  blanket via `SlowAPIMiddleware` — not per-route decorators, so it
  didn't require touching every handler's signature). Live-tested, not
  just configured: firing 130 rapid requests at a running instance
  produced exactly 120×`200` then 10×`429` with a clean JSON body.
  Added once public deployment was imminent — there's nothing for an
  attacker to corrupt (no database, no writes), but nothing stopped
  someone from running up hosting costs or degrading service for real
  users with a request flood either.
- **Length bounds on every query/path string param** (`city`, `grid`,
  `source`, `search`, `feeder_id` — all capped at 100-200 chars via
  `Query(max_length=...)` / `Path(max_length=...)`). Not a real exploit
  path (filtering is just an in-memory list scan regardless of input
  length), but free to add and means oversized input gets rejected at
  the validation layer with a clean `422` instead of being scanned
  against the full feeder list for no reason.

## Updating the data

Right now this reads a static file, exactly as the project plan asked
("read from generated JSON, do not rerun scrapers per request"). To pick
up a fresh scraper run:

1. Copy the new `schedule_latest.json` over `data/schedule_latest.json`.
2. Restart the server (or, since `data_store.load()` is a plain function,
   you could later add a `POST /admin/reload` endpoint that calls it
   again without a restart — not built yet, since you don't have a
   scheduled scraper job running unattended yet either).

## Design decisions, and why they're not overengineered

- **No database.** ~636 feeders is ~316 KB of JSON; it fits in RAM
  comfortably. A database would add a moving part (connection pooling,
  migrations, a process to keep running) for no benefit at this scale.
- **No caching layer.** The whole dataset already lives in memory after
  startup — there's nothing slower underneath it to cache.
  Every request is just a dict lookup.
- **No async I/O.** FastAPI route handlers here are plain `def`, not
  `async def`, because there's no actual I/O happening per-request (no
  network calls, no disk reads) — just in-memory dict lookups and some
  date math. `async def` would add complexity for zero benefit until a
  real database or external call shows up.
- **Sequential filtering in `/feeders`** (three `if` + list comprehension
  passes) instead of a query-planner or indexed lookup — at 636 rows,
  three linear scans take microseconds. Optimize this only if/when the
  feeder count grows by orders of magnitude.

## Next steps (not built yet, on purpose)

- A `POST /admin/reload` endpoint (or a file-watcher) so a fresh scraper
  run doesn't require a server restart.
- Deploying this somewhere always-on and free-tier-friendly (Railway,
  Render, Fly.io) once you're ready to point a real device at it instead
  of `127.0.0.1`.
- Then: Flutter Area Selection screen, calling `/hierarchy`, per the plan.
