"""
app/time_utils.py

Pure time-math for predicting the next power outage from a feeder's daily
on/off cycle pattern. No FastAPI, no Pydantic, no I/O -- just dates and
datetimes in, a dict out. That makes it unit-testable in isolation (see
tests/test_time_utils.py) and reusable outside this API later, e.g. a
cron job that sends "power's about to go out" push notifications could
call next_outage() directly without spinning up an HTTP server.

Why this needs its own module
------------------------------
The trickiest part of this whole API isn't the HTTP layer -- it's that
outage cycles are stored as wall-clock times ("23:35" to "02:05") that
repeat every day, and a lot of them cross midnight. In the current
dataset, 302 of ~1,933 total cycles (about 1 in 6) have an end time
earlier than their start time. That's routine, not a rare edge case, so
it has to be handled correctly rather than patched around.

The core trick: a single HH:MM pair is ambiguous on its own ("23:35 to
02:05" -- did it start yesterday, today, or does it start tonight?). To
answer "what's happening right now" or "what's next", every cycle gets
anchored to a concrete calendar date -- yesterday, today, AND tomorrow --
producing real datetime ranges, which can then simply be compared against
the current moment like any other datetime. Three anchor days are enough
because no single cycle is longer than 24h, so an occurrence anchored
more than a day away can never be the current or very-next one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Pakistan does not observe daylight saving time, so a fixed IANA zone is
# safe to hardcode here -- no "did the UTC offset change" edge case to
# worry about, unlike e.g. "America/New_York".
PAKISTAN_TZ = ZoneInfo("Asia/Karachi")


@dataclass(frozen=True)
class _Occurrence:
    """One concrete (dated) occurrence of a cycle -- not just
    '23:35-02:05' but '23:35 on 19 June to 02:05 on 20 June'."""

    start: datetime
    end: datetime


def _time_to_minutes(value: str) -> int:
    """'23:35' -> 1415 (minutes since midnight)."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _anchor_cycle_to_date(start_str: str, end_str: str, anchor_date: date) -> _Occurrence:
    """Turn a wall-clock cycle ('23:35', '02:05') into one concrete
    datetime range, assuming the cycle STARTS on `anchor_date`.

    If end <= start (e.g. 23:35 -> 02:05), the cycle crosses midnight, so
    the end datetime lands on the day AFTER anchor_date. This one branch
    is the entire crux of correct outage prediction here.
    """
    start_minutes = _time_to_minutes(start_str)
    end_minutes = _time_to_minutes(end_str)

    start_dt = datetime.combine(
        anchor_date, time(start_minutes // 60, start_minutes % 60), tzinfo=PAKISTAN_TZ
    )

    end_date = anchor_date + timedelta(days=1) if end_minutes <= start_minutes else anchor_date
    end_dt = datetime.combine(
        end_date, time(end_minutes // 60, end_minutes % 60), tzinfo=PAKISTAN_TZ
    )

    return _Occurrence(start=start_dt, end=end_dt)


def next_outage(cycles: list[dict], now: datetime | None = None) -> dict:
    """Given a feeder's list of {"start": "HH:MM", "end": "HH:MM"} cycles
    (which repeat every day), figure out -- relative to `now` -- whether
    an outage is happening right now, and when the next one starts.

    Returns a plain dict (deliberately not a Pydantic model -- this
    function has zero dependency on the web framework) with keys:
        has_schedule: bool
        currently_in_outage: bool
        current_outage_ends_at: str | None       (ISO 8601, only if currently in outage)
        minutes_remaining_in_current_outage: int | None
        next_outage_starts_at: str | None         (ISO 8601)
        next_outage_ends_at: str | None
        minutes_until_next_outage: int | None      (0 if currently in outage)
    """
    if now is None:
        now = datetime.now(PAKISTAN_TZ)

    if not cycles:
        return {
            "has_schedule": False,
            "currently_in_outage": False,
            "current_outage_ends_at": None,
            "minutes_remaining_in_current_outage": None,
            "next_outage_starts_at": None,
            "next_outage_ends_at": None,
            "minutes_until_next_outage": None,
        }

    # Anchor every cycle to yesterday, today, and tomorrow. A cycle that
    # starts late at night might already be "in progress" from
    # yesterday's anchor; a cycle anchored to today might already be in
    # the past, in which case the real "next" occurrence is tomorrow's.
    occurrences: list[_Occurrence] = []
    today = now.date()
    for day_offset in (-1, 0, 1):
        anchor = today + timedelta(days=day_offset)
        for cycle in cycles:
            occurrences.append(_anchor_cycle_to_date(cycle["start"], cycle["end"], anchor))

    current = next((o for o in occurrences if o.start <= now < o.end), None)
    upcoming = sorted((o for o in occurrences if o.start > now), key=lambda o: o.start)
    next_occ = upcoming[0] if upcoming else None

    return {
        "has_schedule": True,
        "currently_in_outage": current is not None,
        "current_outage_ends_at": current.end.isoformat() if current else None,
        "minutes_remaining_in_current_outage": (
            int((current.end - now).total_seconds() // 60) if current else None
        ),
        "next_outage_starts_at": next_occ.start.isoformat() if next_occ else None,
        "next_outage_ends_at": next_occ.end.isoformat() if next_occ else None,
        "minutes_until_next_outage": (
            int((next_occ.start - now).total_seconds() // 60) if next_occ else None
        ),
    }
