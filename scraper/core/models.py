"""
core/models.py

Defines the NORMALIZED shape that every provider must produce.

Why this exists
----------------
PITC's portal, K-Electric's PDF, and any future source all describe the
same real-world thing (a feeder's on/off cycles) but in totally different
raw formats (HTML tables, AJAX JSON, PDF text). If every provider returned
its own raw shape, the rest of the system (and eventually the mobile app)
would need to special-case each source. Instead, every provider converts
its raw data into THESE dataclasses before handing it back. This is the
"adapter" half of the Adapter pattern -- see providers/base.py for the
interface that enforces it.

C++ analogy
-----------
This is the equivalent of defining a plain `struct FeederSchedule` and a
`struct ProviderResult` that every driver/backend in a plugin system must
populate -- like how a HAL (hardware abstraction layer) defines a common
struct that every device driver fills in, regardless of the physical bus
(I2C, SPI, USB) it actually talks to underneath.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class FetchStatus(str, Enum):
    """Outcome of a single provider run. Stored as a string so it serializes
    cleanly to JSON without a custom encoder."""

    OK = "ok"
    PARTIAL = "partial"   # got *some* feeders, but parsing degraded somewhere
    FAILED = "failed"     # provider could not get usable data at all


@dataclass
class Cycle:
    """One on/off window within a day, e.g. 10:05-12:05."""

    start: Optional[str]  # "HH:MM" 24-hour, or None if this cycle is "-" (not shed)
    end: Optional[str]

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end}


@dataclass
class FeederSchedule:
    """One row of the normalized output: a single feeder's schedule."""

    feeder_name: str
    grid_or_area: Optional[str]
    cycles: list[Cycle] = field(default_factory=list)
    raw_location: Optional[str] = None  # unsplit "feeder + grid" text, kept
    # as a fallback when we can't confidently split feeder_name vs grid
    city: Optional[str] = None    # source-reported city, when the source
    # actually says (PITC's search results do; K-Electric's PDF doesn't,
    # so this stays None there -- the consuming backend already has its
    # own city-inference fallback for exactly that case).
    disco: Optional[str] = None   # source-reported DISCO (e.g. "GEPCO"),
    # same story: present from PITC, absent from K-Electric.

    def to_dict(self) -> dict:
        return {
            "feeder_name": self.feeder_name,
            "grid_or_area": self.grid_or_area,
            "cycles": [c.to_dict() for c in self.cycles],
            "raw_location": self.raw_location,
            "city": self.city,
            "disco": self.disco,
        }


@dataclass
class ProviderResult:
    """What every provider.fetch() call returns, success or failure.

    Bundling status + data + error together (instead of raising exceptions
    for "no data") is what lets the registry keep going when one provider
    fails -- see core/registry.py. A provider should basically never let
    an exception escape fetch(); it should catch internally and return
    FAILED with a message instead.
    """

    source: str                      # e.g. "k_electric", "pitc_ccms"
    status: FetchStatus
    feeders: list[FeederSchedule] = field(default_factory=list)
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    meta: dict = field(default_factory=dict)   # e.g. {"week_label": "..."}
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "status": self.status.value,
            "fetched_at": self.fetched_at,
            "feeder_count": len(self.feeders),
            "meta": self.meta,
            "error": self.error,
            "feeders": [f.to_dict() for f in self.feeders],
        }
