"""GCSI Phase 6F-B2.2 — Acquisition Execution Script.

Runs canary acquisition first, then full bulk acquisition.

Usage:
    python scripts/run_b22_acquisition.py [--canary-only] [--dry-run]

This script is NOT committed as a production artifact. It drives the acquisition
from the command line.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
import time

# Repository root
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from backend.app.mission_sources.v2_inventory_acquisition import (
    AcquisitionStatus,
    V2InventoryAcquisitionRunner,
    _DEFAULT_LEDGER_PATH,
    _DEFAULT_SNAPSHOT_ROOT,
    save_ledger,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("run_b22_acquisition")


def main() -> int:
    parser = argparse.ArgumentParser(description="GCSI B2.2 label acquisition runner")
    parser.add_argument("--canary-only", action="store_true", help="Run canary only")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (skip network)")
    parser.add_argument("--delay", type=float, default=0.15, help="Inter-request delay seconds")
    parser.add_argument("--snapshot-root", type=str, default=None, help="Snapshot root override")
    args = parser.parse_args()

    snapshot_root = (
        pathlib.Path(args.snapshot_root)
        if args.snapshot_root
        else _DEFAULT_SNAPSHOT_ROOT
    )
    snapshot_root.mkdir(parents=True, exist_ok=True)

    runner = V2InventoryAcquisitionRunner(
        snapshot_root=snapshot_root,
        inter_request_delay_s=args.delay,
        dry_run=args.dry_run,
    )

    t0 = time.monotonic()

    if args.canary_only:
        logger.info("=== CANARY ACQUISITION ONLY ===")
        results = runner.run_canary_only()
        elapsed = time.monotonic() - t0
        logger.info(f"Canary acquisition complete in {elapsed:.1f}s")

        print("\n=== CANARY RESULTS ===")
        all_ok = True
        for profile_id, row in results.items():
            status = row.acquisition_status.value
            ok = status in ("ACQUIRED_VERIFIED", "REUSED_VERIFIED_SNAPSHOT")
            indicator = "OK" if ok else "FAIL"
            print(f"  [{indicator}] {profile_id:30s} -> {status}")
            if not ok:
                print(f"    error: {row.error_class}: {row.error_detail_code}")
                all_ok = False

        if all_ok:
            print("\nAll canary acquisitions PASSED. Ready for bulk acquisition.")
            return 0
        else:
            print("\nCANARY FAILURES DETECTED. Bulk acquisition NOT started.")
            return 1

    else:
        logger.info("=== FULL BULK ACQUISITION ===")
        ledger = runner.run()
        elapsed = time.monotonic() - t0

        # Save ledger
        save_ledger(ledger, _DEFAULT_LEDGER_PATH)
        logger.info(f"Ledger saved to {_DEFAULT_LEDGER_PATH}")

        # Summary
        rows = ledger.rows
        success = [r for r in rows if r.acquisition_status in (
            AcquisitionStatus.ACQUIRED_VERIFIED, AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT
        )]
        failures = [r for r in rows if r.acquisition_status not in (
            AcquisitionStatus.ACQUIRED_VERIFIED, AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT
        )]

        print(f"\n=== ACQUISITION SUMMARY ===")
        print(f"  Total planned: {len(rows)}")
        print(f"  Successful: {len(success)}")
        print(f"  Failed: {len(failures)}")
        print(f"  Elapsed: {elapsed:.1f}s")
        print(f"  Ledger ID: {ledger.ledger_id}")

        if failures:
            print(f"\nFAILURES ({len(failures)}):")
            for r in failures[:20]:
                print(f"  [{r.acquisition_index:3d}] {r.instrument:12s} {r.profile_id:25s} {r.acquisition_status.value}: {r.error_detail_code[:80] if r.error_detail_code else ''}")
            if len(failures) > 20:
                print(f"  ... and {len(failures) - 20} more")
            return 1
        else:
            print(f"\nALL 535 representations acquired and verified.")
            return 0


if __name__ == "__main__":
    sys.exit(main())
