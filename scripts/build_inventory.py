#!/usr/bin/env python3
"""Build the B2.2 verified inventory manifest from the acquisition ledger.

Usage:
    python scripts/build_inventory.py
"""
from __future__ import annotations

import logging
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from backend.app.mission_sources.v2_acquisition_plan_builder import load_bound_v2_acquisition_plan
from backend.app.mission_sources.v2_inventory_acquisition import (
    load_ledger,
    _DEFAULT_LEDGER_PATH,
    _DEFAULT_SNAPSHOT_ROOT,
)
from backend.app.mission_sources.v2_verified_inventory import V2VerifiedInventoryBuilder
from backend.app.mission_sources.archive_models import ArchiveSourceStandard

_MANIFEST_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_verified_inventory.json"


def main() -> None:
    logger.info("Loading acquisition plan...")
    bound = load_bound_v2_acquisition_plan()
    plan = bound.plan
    logger.info("  plan_id = %s", plan.plan_id)
    logger.info("  logical_entries = %d", len(plan.logical_entries))

    logger.info("Loading acquisition ledger...")
    ledger = load_ledger(_DEFAULT_LEDGER_PATH)
    logger.info("  ledger_id = %s", ledger.ledger_id)
    logger.info("  plan_id   = %s", ledger.plan_id)
    logger.info("  rows      = %d", len(ledger.rows))

    if ledger.plan_id != plan.plan_id:
        logger.error(
            "Plan/ledger mismatch! plan=%s, ledger=%s",
            plan.plan_id, ledger.plan_id,
        )
        sys.exit(1)

    logger.info("Building verified inventory manifest...")
    builder = V2VerifiedInventoryBuilder()
    manifest = builder.build(plan, ledger, _DEFAULT_SNAPSHOT_ROOT)

    # Print summary
    entries = manifest.entries
    source_records = manifest.source_records
    logger.info("\n--- Manifest Summary ---")
    logger.info("  manifest_id       = %s", manifest.manifest_id)
    logger.info("  logical entries   = %d", len(entries))
    logger.info("  source records    = %d", len(source_records))

    pds4_count = sum(
        1 for r in source_records
        if r.source_standard == ArchiveSourceStandard.PDS4
    )
    pds3_count = sum(
        1 for r in source_records
        if r.source_standard == ArchiveSourceStandard.PDS3
    )
    logger.info("  PDS4 source refs  = %d", pds4_count)
    logger.info("  PDS3 source refs  = %d", pds3_count)

    # Check JunoCam
    junocam_entries = [
        e for e in entries
        if e.logical_product_id.startswith("gcsi.junocam")
    ]
    logger.info("  JunoCam entries   = %d", len(junocam_entries))
    if junocam_entries:
        junocam_rep_counts = [len(e.representation_record_ids) for e in junocam_entries]
        all_two = all(c == 2 for c in junocam_rep_counts)
        logger.info("  JunoCam all have 2 reps: %s", all_two)

    # Validate counts
    assert len(entries) == 403, f"Expected 403 entries, got {len(entries)}"
    assert len(source_records) == 527, f"Expected 527 source records, got {len(source_records)}"
    assert pds4_count == 154, f"Expected 154 PDS4, got {pds4_count}"
    assert pds3_count == 373, f"Expected 373 PDS3, got {pds3_count}"
    logger.info("\n✓ All count assertions pass")

    # Save manifest
    builder.save_manifest(manifest, _MANIFEST_PATH)
    logger.info("Saved to: %s", _MANIFEST_PATH.relative_to(_REPO_ROOT))


if __name__ == "__main__":
    main()
