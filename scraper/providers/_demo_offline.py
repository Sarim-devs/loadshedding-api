"""
providers/_demo_offline.py

NOT a real data source. Wraps the (real, unit-tested) parsers around
captured fixtures instead of live downloads, so the full pipeline --
parse -> normalize -> registry -> JSON output -- can be demonstrated
end-to-end without needing network access.

Why this exists: this sandbox's outbound network is limited to package
registries (pip/npm/etc), so I can't execute the live HTTP calls in
KElectricProvider or PitcCcmsProvider from here to show a real successful
run. Rather than just asserting "trust me, it'll work," this lets you
see the actual JSON a successful run produces, built from real data
captured from both sites during development -- only the network
round-trip is swapped out, not the parsing logic.

Run with:  python3 run.py --demo
"""

from __future__ import annotations

from pathlib import Path

from core.models import FetchStatus, ProviderResult
from providers.base import ScheduleProvider
from providers.k_electric_parser import parse_schedule_text
from providers.pitc_ccms_parser import feeder_detail_to_schedule, parse_feeder_detail_html

FIXTURES = Path(__file__).parent.parent / "tests" / "fixtures"
KE_FIXTURE = FIXTURES / "ke_sample_extracted_text.txt"
PITC_FIXTURE = FIXTURES / "pitc_feeder_detail_011613.html"


class OfflineDemoProvider(ScheduleProvider):
    @property
    def name(self) -> str:
        return "k_electric_DEMO_OFFLINE_SAMPLE"

    def fetch(self) -> ProviderResult:
        text = KE_FIXTURE.read_text()
        feeders = parse_schedule_text(text)
        return ProviderResult(
            source=self.name,
            status=FetchStatus.OK,
            feeders=feeders,
            meta={
                "note": "DEMO ONLY -- parsed from a captured fixture file, "
                "not a live request. Real provider: providers/k_electric.py",
            },
        )


class PitcOfflineDemoProvider(ScheduleProvider):
    @property
    def name(self) -> str:
        return "pitc_ccms_DEMO_OFFLINE_SAMPLE"

    def fetch(self) -> ProviderResult:
        html = PITC_FIXTURE.read_text(encoding="utf-8")
        detail = parse_feeder_detail_html(html)
        if detail is None:
            return ProviderResult(
                source=self.name, status=FetchStatus.FAILED, error="demo fixture failed to parse"
            )
        schedule = feeder_detail_to_schedule(detail)
        return ProviderResult(
            source=self.name,
            status=FetchStatus.OK,
            feeders=[schedule],
            meta={
                "note": "DEMO ONLY -- parsed from a captured fixture file (one "
                "real feeder, 011613 MAIN BAZAR), not a live request. Real "
                "provider: providers/pitc_ccms.py",
            },
        )

