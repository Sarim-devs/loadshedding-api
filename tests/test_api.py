"""
tests/test_api.py

Integration tests that boot the real FastAPI app (via TestClient) against
the real schedule_latest.json on disk, and hit the actual HTTP routes.
These don't replace tests/test_time_utils.py or tests/test_enrichment.py
(which test logic in isolation) -- they exist to catch wiring mistakes
that unit tests can't: a route registered under the wrong path, a
Pydantic model missing a field a handler tries to set, a query param
that doesn't actually get read, etc. -- the kind of bug that only shows
up when the pieces are plugged together.

Run with:
    python3 -m unittest discover -v
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # `with TestClient(app) as client` runs the lifespan startup hook
        # (data_store.load()) exactly once for the whole test class,
        # the same way uvicorn would on a real boot -- not per test.
        cls._client_cm = TestClient(app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._client_cm.__exit__(None, None, None)

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertGreater(body["total_feeders"], 0)

    def test_stats(self):
        # Deliberately NOT a hardcoded feeder count -- this dataset
        # grows every time a fresh scrape runs (it already has twice:
        # 636 -> 785 feeders during this project), so a magic number
        # here goes stale on the next data refresh. Checking /stats
        # against /health's own total instead verifies internal
        # consistency (the two endpoints agree with each other), which
        # is the thing actually worth guarding -- not a specific count.
        health = self.client.get("/health").json()
        response = self.client.get("/stats")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total_feeders"], health["total_feeders"])
        self.assertGreater(body["total_feeders"], 0)
        self.assertGreaterEqual(body["total_cities"], 2)  # at least Karachi + 1 PITC city

    def test_cities_includes_karachi(self):
        response = self.client.get("/cities")
        self.assertEqual(response.status_code, 200)
        cities = {row["city"] for row in response.json()}
        self.assertIn("Karachi", cities)

    def test_grids_filtered_by_city(self):
        response = self.client.get("/grids", params={"city": "Karachi"})
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertTrue(all(row["city"] == "Karachi" for row in rows))

    def test_hierarchy_shape(self):
        response = self.client.get("/hierarchy")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(len(body), 0)
        first_city = body[0]
        self.assertIn("grids", first_city)
        self.assertIn("feeders", first_city["grids"][0])
        self.assertIn("feeder_id", first_city["grids"][0]["feeders"][0])

    def test_feeders_filtered_by_city(self):
        response = self.client.get("/feeders", params={"city": "Karachi"})
        self.assertEqual(response.status_code, 200)
        feeders = response.json()
        self.assertEqual(len(feeders), 621)
        self.assertTrue(all(f["city"] == "Karachi" for f in feeders))

    def test_schedule_and_next_outage_for_a_real_feeder(self):
        feeders = self.client.get("/feeders", params={"city": "Karachi"}).json()
        feeder_id = feeders[0]["feeder_id"]

        schedule_response = self.client.get(f"/schedule/{feeder_id}")
        self.assertEqual(schedule_response.status_code, 200)
        self.assertIn("cycles", schedule_response.json())

        outage_response = self.client.get(f"/next-outage/{feeder_id}")
        self.assertEqual(outage_response.status_code, 200)
        self.assertIn("currently_in_outage", outage_response.json())

    def test_unknown_feeder_id_is_404(self):
        response = self.client.get("/schedule/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_overlong_query_param_is_rejected_not_scanned(self):
        # Robustness, not a real exploit path (filtering is just an
        # in-memory list scan either way) -- but rejecting absurd input
        # at the validation layer is free and avoids relying on "it's
        # cheap anyway" as the only line of defense.
        too_long = "a" * 101
        response = self.client.get("/feeders", params={"city": too_long})
        self.assertEqual(response.status_code, 422)

        exactly_the_limit = "a" * 100
        response = self.client.get("/feeders", params={"city": exactly_the_limit})
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
