#!/usr/bin/env python3
"""Build the B2.2 source bundle index.

Aggregates all B2.2 artifact IDs into an immutable source bundle.

Usage:
    python scripts/build_source_bundle.py
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from backend.app.mission_sources.v2_acquisition_plan_builder import load_bound_v2_acquisition_plan
from backend.app.mission_sources.v2_inventory_acquisition import load_ledger, _DEFAULT_LEDGER_PATH
from backend.app.mission_sources.v2_verified_inventory import V2VerifiedInventoryBuilder
from backend.app.mission_sources.v2_source_bundle import build_source_bundle, save_source_bundle, load_source_bundle

# Frozen constants
_SIDECAR_ARTIFACT_ID = "3eb9f16df6c92c1cede71feb6b3ed111d2154452491cbaf1625aff6c24b4661f"
_DECISION_EPOCH_UTC = "2024-06-14T09:35:17.546000+00:00"

_MANIFEST_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_verified_inventory.json"
_BUNDLE_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_source_bundle.json"

_HORIZONS_SNAP_PATH = (
    _REPO_ROOT
    / "data"
    / "verified_snapshots"
    / "horizons"
    / "juno"
    / "juno_spk_-61_2024-06-14T093517.546000Z.json"
)
_SNAP_ROOT = _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"


def main() -> None:
    logger.info("Building B2.2 source bundle index...")

    # 1. Load plan
    bound = load_bound_v2_acquisition_plan()
    plan = bound.plan
    logger.info("  plan_id = %s", plan.plan_id)

    # 2. Load ledger
    ledger = load_ledger(_DEFAULT_LEDGER_PATH)
    logger.info("  ledger_id = %s", ledger.ledger_id)
    assert ledger.plan_id == plan.plan_id, f"plan/ledger mismatch"

    # 3. Load manifest
    builder = V2VerifiedInventoryBuilder()
    manifest = builder.load_manifest(_MANIFEST_PATH)
    logger.info("  manifest_id = %s", manifest.manifest_id)

    # 4. Count verified snapshots on disk
    snap_count = sum(1 for _ in _SNAP_ROOT.rglob("*.json"))
    logger.info("  label snapshots on disk = %d", snap_count)
    assert snap_count == 527, f"Expected 527 snapshots, found {snap_count}"

    # 5. Load Horizons snapshot envelope for snapshot_id
    assert _HORIZONS_SNAP_PATH.exists(), f"Horizons snapshot missing: {_HORIZONS_SNAP_PATH}"
    horizons_env = json.loads(_HORIZONS_SNAP_PATH.read_text(encoding="utf-8"))
    horizons_snapshot_id = horizons_env["snapshot_id"]
    horizons_snapshot_ref = str(_HORIZONS_SNAP_PATH.relative_to(_REPO_ROOT)).replace("\\", "/")
    logger.info("  horizons_snapshot_id = %s", horizons_snapshot_id)

    # 6. Relative path for manifest
    manifest_ref = str(_MANIFEST_PATH.relative_to(_REPO_ROOT)).replace("\\", "/")

    # 7. Verify entry counts
    assert len(manifest.entries) == 403
    assert len(manifest.source_records) == 527

    # 8. Build bundle
    bundle = build_source_bundle(
        replay_id=plan.replay_id,
        acquisition_plan_id=plan.plan_id,
        discovery_evidence_artifact_id=_SIDECAR_ARTIFACT_ID,
        acquisition_ledger_id=ledger.ledger_id,
        verified_inventory_manifest_id=manifest.manifest_id,
        verified_inventory_manifest_ref=manifest_ref,
        label_snapshot_count=snap_count,
        logical_product_count=len(manifest.entries),
        source_record_count=len(manifest.source_records),
        decision_epoch_utc=_DECISION_EPOCH_UTC,
        horizons_snapshot_id=horizons_snapshot_id,
        horizons_snapshot_ref=horizons_snapshot_ref,
    )

    logger.info("\n--- Source Bundle ---")
    logger.info("  bundle_id                       = %s", bundle.bundle_id)
    logger.info("  replay_id                       = %s", bundle.replay_id)
    logger.info("  acquisition_plan_id             = %s", bundle.acquisition_plan_id)
    logger.info("  discovery_evidence_artifact_id  = %s", bundle.discovery_evidence_artifact_id)
    logger.info("  acquisition_ledger_id           = %s", bundle.acquisition_ledger_id)
    logger.info("  verified_inventory_manifest_id  = %s", bundle.verified_inventory_manifest_id)
    logger.info("  label_snapshot_count            = %d", bundle.label_snapshot_count)
    logger.info("  logical_product_count           = %d", bundle.logical_product_count)
    logger.info("  source_record_count             = %d", bundle.source_record_count)
    logger.info("  horizons_snapshot_id            = %s", bundle.horizons_snapshot_id)
    logger.info("  decision_epoch_utc              = %s", bundle.decision_epoch_utc)

    # 9. Save
    save_source_bundle(bundle, _BUNDLE_PATH)
    logger.info("\nSaved to: %s", _BUNDLE_PATH.relative_to(_REPO_ROOT))

    # 10. Reload and verify
    reloaded = load_source_bundle(_BUNDLE_PATH)
    assert reloaded.bundle_id == bundle.bundle_id, "Bundle reload ID mismatch!"
    logger.info("  ✓ Bundle reload verified")
    logger.info("\nSource bundle COMPLETE ✓")


if __name__ == "__main__":
    main()
