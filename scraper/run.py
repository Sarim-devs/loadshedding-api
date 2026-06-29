"""
run.py

The thing you actually run. Wires the providers into the registry, runs
everything, writes normalized JSON to output/, and prints a plain-English
summary so you can see at a glance whether each source worked.

Usage:
    python3 run.py
    python3 run.py --pretty          # human-readable JSON
    python3 run.py --output-dir out  # write somewhere other than ./output
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from core.registry import ProviderRegistry
from core.models import FetchStatus
from providers.k_electric import KElectricProvider
from providers.pitc_ccms import PitcCcmsProvider


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def build_providers(demo: bool) -> list:
    """Adding a new source is exactly this: write a class implementing
    ScheduleProvider, instantiate it here. Nothing else in the codebase
    needs to know it exists."""
    providers = [
        KElectricProvider(),
        # grid= confirmed working across two different DISCOs on real
        # runs (GEPCO via Lalamusa, LESCO-area via Lahore). city="Lahore"
        # was swapped out after a real search showed it matches more than
        # just the city -- it picked up "Chota Lahore," an unrelated PESCO
        # grid station that happens to share part of the name. grid= is a
        # more precise match for now; see README "Known limitations" for
        # the broader city-search-ambiguity note.
        PitcCcmsProvider(queries=[("grid", "Lalamusa"), ("grid", "Lahore")]),
    ]
    if demo:
        from providers._demo_offline import OfflineDemoProvider, PitcOfflineDemoProvider
        providers.append(OfflineDemoProvider())
        providers.append(PitcOfflineDemoProvider())
    return providers


def main() -> int:
    parser = argparse.ArgumentParser(description="Load shedding schedule scraper (Phase 1 POC)")
    parser.add_argument("--output-dir", default="output", help="where to write JSON files")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON output")
    parser.add_argument("--verbose", action="store_true", help="debug-level logging")
    parser.add_argument(
        "--demo", action="store_true",
        help="also run an offline demo provider (parses a captured real "
        "sample instead of hitting the live site) so you can see a "
        "successful run even before PITC's endpoint is discovered",
    )
    args = parser.parse_args()

    configure_logging(args.verbose)
    logger = logging.getLogger("run")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    registry = ProviderRegistry(build_providers(demo=args.demo))
    results = registry.run_all()

    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    combined = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "sources": [r.to_dict() for r in results],
    }

    indent = 2 if args.pretty else None
    out_path = output_dir / f"schedule_{run_timestamp}.json"
    out_path.write_text(json.dumps(combined, indent=indent, ensure_ascii=False))

    latest_path = output_dir / "schedule_latest.json"
    latest_path.write_text(json.dumps(combined, indent=2, ensure_ascii=False))

    print()
    print("=" * 60)
    print("RUN SUMMARY")
    print("=" * 60)
    ok_count = 0
    for r in results:
        symbol = {"ok": "[OK]    ", "partial": "[PARTIAL]", "failed": "[FAILED]"}[r.status.value]
        print(f"{symbol} {r.source:15s} feeders={len(r.feeders):4d}  {r.error or ''}")
        if r.status == FetchStatus.OK:
            ok_count += 1
    print("-" * 60)
    print(f"{ok_count}/{len(results)} sources returned usable data")
    print(f"Full output written to: {out_path}")
    print(f"Latest run also at:     {latest_path}")
    print("=" * 60)

    # Exit code reflects whether AT LEAST ONE source worked -- this is
    # the resilience contract in practice: the whole run only "fails"
    # (non-zero exit, useful for cron/CI) if every single source failed.
    any_usable = any(r.status in (FetchStatus.OK, FetchStatus.PARTIAL) for r in results)
    return 0 if any_usable else 1


if __name__ == "__main__":
    sys.exit(main())
