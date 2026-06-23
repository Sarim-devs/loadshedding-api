"""
app/main.py

The FastAPI application itself: wires data_store (in-memory data) and
time_utils (next-outage math) into HTTP endpoints. This file stays thin
on purpose -- route handlers translate an HTTP request into a call
against data_store/time_utils and shape the result into a Pydantic
response model. Any real logic belongs in those other two modules, not
here, so it stays testable without spinning up an HTTP server.

Endpoint groups (see each handler's docstring for detail):
  meta:      /health, /stats, /sources
  hierarchy: /cities, /grids, /hierarchy
  feeders:   /feeders, /schedule/{feeder_id}, /next-outage/{feeder_id}

How to run this
----------------
    cd loadshedding-api
    uvicorn app.main:app --reload

Then open http://127.0.0.1:8000/docs for interactive Swagger docs --
FastAPI generates that page automatically from the type hints and
Pydantic models below; you don't write it by hand.

C++ analogy
-----------
`@app.get("/health")` written just above a function is a decorator --
think of it as roughly equivalent to registering a callback with a
dispatch table, e.g. `router.register("GET", "/health", &healthHandler)`
in a C++ HTTP framework, except Python lets you express the registration
as an annotation directly on the function instead of a separate call
somewhere else.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app import data_store, time_utils
from app.models import (
    CityInfo,
    FeederMini,
    FeederSchedule,
    FeederSummary,
    GridInfo,
    HealthResponse,
    HierarchyCity,
    HierarchyGrid,
    NextOutageResponse,
    OutageCycle,
    SourceStatus,
    StatsResponse,
)

logger = logging.getLogger("loadshedding_api.main")

# A blanket default limit, applied via SlowAPIMiddleware below -- NOT
# per-route @limiter.limit(...) decorators. The decorator approach
# requires adding a `request: Request` parameter to every single route
# function (slowapi inspects the signature to find one); the middleware
# approach applies one limit to everything from outside the route layer
# entirely, so none of the handlers below needed to change. 120/minute
# (2 req/sec sustained) is generous for how an app actually uses this --
# a few calls per screen visit, not a request storm -- while still
# meaning one client can't realistically run up hosting costs or
# degrade service for everyone else. Tune later once you have real
# traffic numbers to look at instead of a guess.
limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when uvicorn starts the process -- the FastAPI equivalent
    # of setup code in main() before you enter your server's accept loop.
    # Loading here (rather than at module import time) makes the timing
    # explicit, and means a test suite can call data_store.load() itself
    # with a fixture file instead of always reading the real one.
    data_store.load()
    yield
    # (nothing to clean up on shutdown -- this is a read-only, in-memory store)


app = FastAPI(
    title="Pakistan Load Shedding Scheduler API",
    description=(
        "Reads pre-scraped feeder outage schedules from a JSON file "
        "produced by the Python providers (k_electric, pitc_ccms, ...) "
        "and serves them as clean JSON endpoints for the Flutter app. "
        "This service does NOT scrape anything itself -- see the "
        "separate scraper project for that."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Wide open for now -- this is read-only public schedule data (no auth,
# no cookies, nothing sensitive), and during the 2-day build you'll
# likely want `flutter run -d chrome` for fast iteration, which a
# browser WILL block without this. Mobile builds (the actual target)
# don't go through CORS at all -- this only matters for web testing.
# Tighten `allow_origins` to your actual domain(s) before any real
# public deployment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last line of defense: if a route handler raises something we
    didn't anticipate (a bug, not a deliberate HTTPException), this
    turns it into a clean JSON 500 instead of an unhandled-exception
    stack trace leaking to the client. The full traceback still goes to
    the server log via logger.exception -- this only changes what the
    CLIENT sees, not what you see when debugging.

    Worth doing now even for a 2-day MVP: without this, one unexpected
    None or KeyError in a route handler returns a raw, ugly traceback to
    the Flutter app instead of JSON it can actually parse and show a
    sensible error for -- cheap to add, meaningfully better failure mode.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Is the service up, AND is data actually loaded? Kept as two
    separate questions so the Flutter app (or an uptime monitor) can
    tell 'can't reach the server' apart from 'server's fine, something's
    wrong with the data' -- those need different user-facing messages."""
    if not data_store.is_loaded():
        # Should be unreachable in practice -- lifespan loads data before
        # the app accepts any requests -- but fail loudly rather than
        # silently if it somehow happens.
        raise HTTPException(status_code=503, detail="Schedule data not loaded")

    return HealthResponse(
        status="ok",
        data_run_at=data_store.get_run_at(),
        sources_loaded=len(data_store.get_sources()),
        total_feeders=data_store.total_feeder_count(),
        skipped_feeders=data_store.get_skipped_feeder_count(),
    )


@app.get("/stats", response_model=StatsResponse, tags=["meta"])
def stats() -> StatsResponse:
    """One-call overview -- /sources and /cities combined, plus totals.
    Handy for a debug screen, or for sanity-checking a deploy without
    hitting three separate endpoints."""
    cities = data_store.get_cities()
    grids = data_store.get_grids()
    return StatsResponse(
        data_run_at=data_store.get_run_at(),
        total_feeders=data_store.total_feeder_count(),
        total_cities=len(cities),
        total_grids=len(grids),
        skipped_feeders=data_store.get_skipped_feeder_count(),
        sources=[SourceStatus(**s) for s in data_store.get_sources()],
        cities=[CityInfo(**c) for c in cities],
    )


@app.get("/sources", response_model=list[SourceStatus], tags=["meta"])
def sources() -> list[SourceStatus]:
    """Per-source status from the last scraper run -- e.g. so the app can
    show 'K-Electric: live, 621 feeders' vs 'PITC: last run failed'
    instead of presenting everything as one monolithic blob of data."""
    return [SourceStatus(**s) for s in data_store.get_sources()]


@app.get("/cities", response_model=list[CityInfo], tags=["hierarchy"])
def cities() -> list[CityInfo]:
    """Top level of the City -> Grid -> Feeder picker. See
    app/enrichment.py for exactly how 'city' is derived -- short version:
    K-Electric is hardcoded to Karachi (a known fact, not a guess), PITC
    grids fall back to a stripped version of the grid name unless a
    manual override has been added."""
    return [CityInfo(**c) for c in data_store.get_cities()]


@app.get("/grids", response_model=list[GridInfo], tags=["hierarchy"])
def grids(
    city: str | None = Query(default=None, max_length=100),
) -> list[GridInfo]:
    """Second level of the picker. /grids?city=Karachi narrows to one
    city; /grids with no query param returns every (city, grid) pair --
    useful for debugging the enrichment output across the whole
    dataset at a glance."""
    return [GridInfo(**g) for g in data_store.get_grids(city=city)]


@app.get("/hierarchy", response_model=list[HierarchyCity], tags=["hierarchy"])
def hierarchy() -> list[HierarchyCity]:
    """The full City -> Grid -> Feeder tree in ONE response. This is
    what the Flutter Area Selection screen should call exactly once on
    load -- populate all three dropdowns from this single payload and
    filter client-side as the user picks city then grid, rather than
    making a new network request every time a dropdown changes. At the
    current ~636-feeder scale this is a small enough payload (a few
    hundred KB) that one fetch beats three round-trips."""
    return [
        HierarchyCity(
            city=c["city"],
            feeder_count=c["feeder_count"],
            grids=[
                HierarchyGrid(
                    grid=g["grid"],
                    feeder_count=g["feeder_count"],
                    feeders=[FeederMini(**f) for f in g["feeders"]],
                )
                for g in c["grids"]
            ],
        )
        for c in data_store.get_hierarchy()
    ]


@app.get("/feeders", response_model=list[FeederSummary], tags=["feeders"])
def feeders(
    city: str | None = Query(default=None, max_length=100),
    grid: str | None = Query(default=None, max_length=100),
    source: str | None = Query(default=None, max_length=100),
    search: str | None = Query(default=None, max_length=100),
) -> list[FeederSummary]:
    """Lists feeders for the area-selection screen. All filters are
    optional and combine with AND:

        /feeders                                  -> everything
        /feeders?city=Karachi                      -> just Karachi's 621
        /feeders?city=Karachi&grid=Karachi          -> same, explicit grid
        /feeders?grid=132 KV Lala Musa               -> just that PITC grid
        /feeders?source=k_electric                  -> filter by raw source
        /feeders?search=lahore                       -> case-insensitive
                                                          substring match
                                                          on feeder_name

    `city` and `grid` are the filters the Flutter onboarding flow
    actually needs; `source` is kept for debugging/parity with how the
    scraper itself reports results. For the one-shot onboarding fetch,
    prefer /hierarchy over repeated /feeders calls -- see that
    endpoint's docstring.
    """
    results = data_store.get_all_feeders()

    if city:
        results = [f for f in results if f["city"] == city]
    if grid:
        results = [f for f in results if f["grid"] == grid]
    if source:
        results = [f for f in results if f["source"] == source]
    if search:
        needle = search.lower()
        results = [f for f in results if needle in f["feeder_name"].lower()]

    return [
        FeederSummary(
            feeder_id=f["feeder_id"],
            feeder_name=f["feeder_name"],
            source=f["source"],
            grid_or_area=f["grid_or_area"],
            city=f["city"],
            grid=f["grid"],
            disco=f.get("disco"),
        )
        for f in results
    ]


@app.get("/schedule/{feeder_id}", response_model=FeederSchedule, tags=["feeders"])
def schedule(feeder_id: str = Path(max_length=200)) -> FeederSchedule:
    """Full cycle schedule for one feeder -- what Screen 2 (Today's
    Schedule) renders as a timeline. feeder_id comes from the /feeders
    list; it's an opaque generated ID, not the raw feeder name (see
    data_store._make_feeder_id for why a composite ID, not just the name)."""
    feeder = data_store.get_feeder(feeder_id)
    if feeder is None:
        raise HTTPException(status_code=404, detail=f"Unknown feeder_id: {feeder_id!r}")

    return FeederSchedule(
        feeder_id=feeder["feeder_id"],
        feeder_name=feeder["feeder_name"],
        source=feeder["source"],
        grid_or_area=feeder["grid_or_area"],
        city=feeder["city"],
        grid=feeder["grid"],
        disco=feeder.get("disco"),
        cycles=[OutageCycle(**c) for c in feeder["cycles"]],
    )


@app.get("/next-outage/{feeder_id}", response_model=NextOutageResponse, tags=["feeders"])
def next_outage(feeder_id: str = Path(max_length=200)) -> NextOutageResponse:
    """The single most useful endpoint for the app's home screen: 'is the
    power out right now, and if not, when does it next go out.' All the
    actual time-math lives in time_utils.next_outage() -- this handler
    just looks the feeder up, calls it, and reshapes the result."""
    feeder = data_store.get_feeder(feeder_id)
    if feeder is None:
        raise HTTPException(status_code=404, detail=f"Unknown feeder_id: {feeder_id!r}")

    now = datetime.now(time_utils.PAKISTAN_TZ)
    result = time_utils.next_outage(feeder["cycles"], now=now)

    return NextOutageResponse(
        feeder_id=feeder["feeder_id"],
        feeder_name=feeder["feeder_name"],
        as_of=now.isoformat(),
        **result,
    )
