"""
tests/test_time_utils.py

Unit tests for app/time_utils.py -- specifically the midnight-crossing
logic, since that's the one piece of math in this whole API that's easy
to get subtly wrong and hard to notice if you do (it only misbehaves for
feeders whose outage happens to straddle 00:00, and only at certain times
of day -- exactly the kind of bug that survives manual spot-checking).

Run with:
    python3 -m unittest discover -v
"""

from __future__ import annotations

import unittest
from datetime import datetime

from app.time_utils import PAKISTAN_TZ, next_outage


def pkt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=PAKISTAN_TZ)


class TestNextOutageSameDayCycle(unittest.TestCase):
    """A simple cycle that doesn't cross midnight: 15:05-18:05."""

    CYCLES = [{"start": "15:05", "end": "18:05"}]

    def test_before_the_window_counts_down_to_it(self):
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 19, 10, 0))
        self.assertFalse(result["currently_in_outage"])
        self.assertEqual(result["next_outage_starts_at"][:16], "2026-06-19T15:05")
        self.assertEqual(result["minutes_until_next_outage"], 5 * 60 + 5)

    def test_inside_the_window_is_currently_in_outage(self):
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 19, 16, 0))
        self.assertTrue(result["currently_in_outage"])
        self.assertEqual(result["minutes_remaining_in_current_outage"], 2 * 60 + 5)

    def test_after_the_window_rolls_to_tomorrow(self):
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 19, 20, 0))
        self.assertFalse(result["currently_in_outage"])
        self.assertEqual(result["next_outage_starts_at"][:10], "2026-06-20")


class TestNextOutageMidnightCrossing(unittest.TestCase):
    """A cycle that crosses midnight: 23:35-02:05 -- this shape covers
    302 of the ~1,933 real cycles in the current dataset, so it has to
    behave correctly, not just 'mostly work'."""

    CYCLES = [{"start": "23:35", "end": "02:05"}]

    def test_just_before_midnight_start_is_not_yet_in_outage(self):
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 19, 23, 30))
        self.assertFalse(result["currently_in_outage"])
        self.assertEqual(result["minutes_until_next_outage"], 5)

    def test_just_after_midnight_is_still_in_yesterdays_outage(self):
        # 00:30 on the 20th falls inside the cycle that STARTED at 23:35
        # on the 19th. This is the case that breaks a naive
        # "is now.time() between start and end" check, because in
        # plain time-of-day terms 00:30 is "before" 23:35.
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 20, 0, 30))
        self.assertTrue(result["currently_in_outage"])
        self.assertEqual(result["current_outage_ends_at"][:16], "2026-06-20T02:05")
        self.assertEqual(result["minutes_remaining_in_current_outage"], 95)

    def test_after_it_ends_counts_down_to_the_next_nights_start(self):
        result = next_outage(self.CYCLES, now=pkt(2026, 6, 20, 3, 0))
        self.assertFalse(result["currently_in_outage"])
        self.assertEqual(result["next_outage_starts_at"][:16], "2026-06-20T23:35")


class TestNextOutageEdgeCases(unittest.TestCase):
    def test_no_cycles_returns_has_schedule_false(self):
        result = next_outage([], now=pkt(2026, 6, 19, 12, 0))
        self.assertFalse(result["has_schedule"])
        self.assertIsNone(result["next_outage_starts_at"])

    def test_multiple_cycles_picks_the_nearest_one(self):
        cycles = [
            {"start": "06:00", "end": "07:00"},
            {"start": "18:00", "end": "19:00"},
        ]
        result = next_outage(cycles, now=pkt(2026, 6, 19, 12, 0))
        self.assertEqual(result["next_outage_starts_at"][:16], "2026-06-19T18:00")


if __name__ == "__main__":
    unittest.main()
