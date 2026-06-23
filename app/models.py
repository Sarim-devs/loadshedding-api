"""
app/models.py

Pydantic models = the HTTP response "shapes" for this API.

How this relates to the scraper project's core/models.py
----------------------------------------------------------
The scraper side already has dataclasses (FeederSchedule, ProviderResult)
that define what a provider hands back internally. These Pydantic models
are a separate, deliberately thinner set of shapes for what THIS API
sends over HTTP to a client (the Flutter app). They look similar but do
different jobs:

  - core/models.py (dataclasses) -> internal data, Python-only
  - app/models.py  (Pydantic)    -> the public API contract; FastAPI turns
                                     this into JSON Schema + the auto-
                                     generated /docs page

Keeping them separate means you can change the scraper's internal
representation later without silently breaking the app's API contract,
and vice versa. Same reason you wouldn't hand out a pointer to an
internal C++ struct across a DLL boundary -- you'd define a stable
public interface struct and convert into/out of it internally.

C++ analogy
-----------
A Pydantic model is closest to a `struct` with a validating constructor
that throws on bad input -- like a struct whose fields are all set
through a validating setter, except FastAPI generates that validation
(and the OpenAPI/Swagger docs) for you, straight from the type hints.
"""

from __future__ import annotations

from pydantic import BaseModel


class OutageCycle(BaseModel):
    """One on/off window, e.g. start='23:35', end='02:05'.

    Times are 24-hour "HH:MM" strings in local Pakistan time, exactly as
    the scraper produced them -- NOT parsed into datetimes here, because
    a bare cycle has no date attached (it repeats daily). Turning it into
    a concrete datetime only makes sense once you also know "relative to
    which day" -- that's what time_utils.py is for.
    """

    start: str
    end: str


class FeederSummary(BaseModel):
    """One row in the /feeders list. Just enough to populate a picker UI
    (Screen 1 in the Flutter plan: city/grid/feeder selection) -- not the
    full schedule, so the list stays light even at ~600+ feeders."""

    feeder_id: str
    feeder_name: str
    source: str
    grid_or_area: str | None = None
    city: str
    grid: str
    disco: str | None = None


class FeederSchedule(BaseModel):
    """Full detail for /schedule/{feeder_id} -- what Screen 2 (Today's
    Schedule) renders as a timeline."""

    feeder_id: str
    feeder_name: str
    source: str
    grid_or_area: str | None = None
    city: str
    grid: str
    disco: str | None = None
    cycles: list[OutageCycle]


class SourceStatus(BaseModel):
    """One row in /sources. Mirrors ProviderResult from the scraper
    project, minus the actual feeder list (that's what /feeders is for) --
    deliberately a status summary, not a data dump."""

    source: str
    status: str
    feeder_count: int
    fetched_at: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    """Answers two different questions at once: 'is the process up' and
    'is data actually loaded' -- so the app can distinguish 'no internet'
    from 'server's fine, something's wrong with the data'."""

    status: str
    data_run_at: str
    sources_loaded: int
    total_feeders: int
    skipped_feeders: int


class CityInfo(BaseModel):
    """One row in /cities. `aliases` is a search foundation, not a
    finished search feature -- see app/enrichment.py CITY_ALIASES. It's
    an empty list today for every city in the current dataset; the field
    exists now so adding real aliases later is a data change, not an API
    contract change Flutter would need to handle."""

    city: str
    feeder_count: int
    grid_count: int
    aliases: list[str] = []


class GridInfo(BaseModel):
    """One row in /grids?city=... -- deliberately just counts, not the
    feeder list (use /feeders?city=&grid= or /hierarchy for that)."""

    city: str
    grid: str
    feeder_count: int


class FeederMini(BaseModel):
    """The leaf of /hierarchy -- trimmed to exactly what a dropdown
    needs (an id to send back, a name to display), not the full
    FeederSummary, to keep the all-feeders hierarchy payload light."""

    feeder_id: str
    feeder_name: str


class HierarchyGrid(BaseModel):
    grid: str
    feeder_count: int
    feeders: list[FeederMini]


class HierarchyCity(BaseModel):
    city: str
    feeder_count: int
    grids: list[HierarchyGrid]


class StatsResponse(BaseModel):
    """A one-call overview combining /sources and /cities -- handy for a
    debug screen, or just for sanity-checking a deploy without hitting
    three endpoints."""

    data_run_at: str
    total_feeders: int
    total_cities: int
    total_grids: int
    skipped_feeders: int
    sources: list[SourceStatus]
    cities: list[CityInfo]


class NextOutageResponse(BaseModel):
    """The home-screen answer: is the power out right now, and when's
    the next change either way. See time_utils.next_outage() for the
    actual prediction logic -- this model just shapes its output."""

    feeder_id: str
    feeder_name: str
    as_of: str
    has_schedule: bool
    currently_in_outage: bool
    current_outage_ends_at: str | None = None
    minutes_remaining_in_current_outage: int | None = None
    next_outage_starts_at: str | None = None
    next_outage_ends_at: str | None = None
    minutes_until_next_outage: int | None = None
