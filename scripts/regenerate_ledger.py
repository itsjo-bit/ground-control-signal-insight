#!/usr/bin/env python3
"""Regenerate the B2.2 acquisition ledger from existing verified snapshots.

This script rebuilds the ledger by loading the current acquisition plan (plan_id=7ede995f...)
and reloading each of the 527 existing verified snapshots from disk — no network activity.

Usage:
    python scripts/regenerate_ledger.py
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

# Ensure repo root is on path
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from backend.app.mission_sources.v2_inventory_acquisition import (
    AcquisitionLedger,
    AcquisitionLedgerRow,
    AcquisitionStatus,
    SizeVerificationStatus,
    TemporalVerificationStatus,
    _build_representation_sequence,
    _check_temporal_eligibility,
    _compute_ledger_id,
    _derive_size_verification_status,
    _snapshot_path_for_url,
    _DEFAULT_SNAPSHOT_ROOT,
    _DEFAULT_LEDGER_PATH,
    save_ledger,
)
from backend.app.mission_sources.v2_acquisition_plan_builder import load_bound_v2_acquisition_plan
from backend.app.mission_sources.snapshots.archive_label_snapshot import ArchiveLabelSnapshotStore


def main() -> None:
    logger.info("Loading acquisition plan...")
    bound = load_bound_v2_acquisition_plan()
    plan = bound.plan
    logger.info("  plan_id = %s", plan.plan_id)
    logger.info("  logical_entries = %d", len(plan.logical_entries))

    sequence = _build_representation_sequence(plan)
    logger.info("  representations = %d", len(sequence))

    rows: list[AcquisitionLedgerRow] = []
    success_count = 0
    failure_count = 0

    for acq_idx, entry, rep in sequence:
        label_url = rep.label_url
        instrument = entry.instrument
        source_standard = rep.source_standard.value
        normalizer_id = rep.normalizer_id
        profile_id = rep.profile_id

        snapshot_path = _snapshot_path_for_url(instrument, label_url, _DEFAULT_SNAPSHOT_ROOT)

        if not snapshot_path.exists():
            logger.error("[%d] Snapshot missing for %s", acq_idx, label_url.split("/")[-1])
            row = AcquisitionLedgerRow(
                acquisition_index=acq_idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=0,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                error_class="SnapshotMissing",
                error_detail_code=f"No snapshot at {snapshot_path.relative_to(_REPO_ROOT)}",
            )
            rows.append(row)
            failure_count += 1
            continue

        # Load snapshot (zero-network)
        try:
            product, provenance = ArchiveLabelSnapshotStore.load(snapshot_path)
        except Exception as exc:
            logger.error("[%d] Snapshot reload failed: %s", acq_idx, exc)
            row = AcquisitionLedgerRow(
                acquisition_index=acq_idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=1,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                error_class="SnapshotReloadError",
                error_detail_code=str(exc)[:120],
            )
            rows.append(row)
            failure_count += 1
            continue

        # Read snapshot envelope for snapshot_id and raw SHA
        try:
            env_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot_id = env_data.get("snapshot_id")
            raw_sha256 = env_data.get("raw_label_sha256")
            retrieved_at = env_data.get("retrieved_at")
        except Exception:
            snapshot_id = None
            raw_sha256 = None
            retrieved_at = None

        temporal_status = _check_temporal_eligibility(product.observation_stop_utc)
        size_status = _derive_size_verification_status(product)

        # Check snapshot source_ref matches planned URL
        if (product.source_label_ref is not None and
                product.source_label_ref != label_url):
            logger.warning(
                "[%d] source_label_ref mismatch: snapshot has %r, plan has %r",
                acq_idx, product.source_label_ref, label_url,
            )

        row = AcquisitionLedgerRow(
            acquisition_index=acq_idx,
            logical_product_id=entry.logical_product_id,
            instrument=instrument,
            representation_role=rep.representation_role.value,
            source_standard=source_standard,
            label_url=label_url,
            normalizer_id=normalizer_id,
            profile_id=profile_id,
            attempt_count=1,
            acquisition_status=AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
            retrieved_at=retrieved_at,
            raw_label_sha256=raw_sha256,
            source_record_id=product.source_record_id,
            archive_product_id=product.source_product_id,
            archive_version=product.source_version,
            snapshot_ref=str(snapshot_path.relative_to(_REPO_ROOT)),
            snapshot_id=snapshot_id,
            provenance_id=provenance.provenance_id,
            observation_start_utc=(
                product.observation_start_utc.isoformat()
                if product.observation_start_utc else None
            ),
            observation_stop_utc=(
                product.observation_stop_utc.isoformat()
                if product.observation_stop_utc else None
            ),
            temporal_verification_status=temporal_status.value,
            archive_total_size_bytes=product.total_data_size_bytes,
            size_verification_status=size_status.value,
        )
        rows.append(row)
        success_count += 1
        if acq_idx % 50 == 0:
            logger.info("  [%d/%d] %s — OK", acq_idx + 1, len(sequence),
                        label_url.split("/")[-1])

    rows.sort(key=lambda r: r.acquisition_index)

    # Uniqueness checks
    source_record_ids = [r.source_record_id for r in rows if r.source_record_id]
    provenance_ids = [r.provenance_id for r in rows if r.provenance_id]
    snapshot_refs = [r.snapshot_ref for r in rows if r.snapshot_ref]

    logger.info("\n--- Results ---")
    logger.info("  Total rows: %d", len(rows))
    logger.info("  Successful: %d", success_count)
    logger.info("  Failed:     %d", failure_count)
    logger.info("  Unique source_record_ids: %d", len(set(source_record_ids)))
    logger.info("  Unique provenance_ids:    %d", len(set(provenance_ids)))
    logger.info("  Unique snapshot_refs:     %d", len(set(snapshot_refs)))

    if len(set(source_record_ids)) != len(source_record_ids):
        logger.error("DUPLICATE source_record_ids detected!")
    if len(set(provenance_ids)) != len(provenance_ids):
        logger.error("DUPLICATE provenance_ids detected!")
    if len(set(snapshot_refs)) != len(snapshot_refs):
        logger.error("DUPLICATE snapshot_refs detected!")

    ledger_id = _compute_ledger_id(rows, plan.replay_id, plan.plan_id)
    ledger = AcquisitionLedger(
        ledger_id=ledger_id,
        replay_id=plan.replay_id,
        plan_id=plan.plan_id,
        rows=tuple(rows),
    )

    save_ledger(ledger, _DEFAULT_LEDGER_PATH)
    logger.info("\nLedger saved to: %s", _DEFAULT_LEDGER_PATH.relative_to(_REPO_ROOT))
    logger.info("  ledger_id = %s", ledger_id)
    logger.info("  plan_id   = %s", plan.plan_id)
    logger.info("  rows      = %d", len(rows))

    temporal_counts = {}
    size_counts = {}
    for r in rows:
        t = r.temporal_verification_status
        s = r.size_verification_status
        if t:
            temporal_counts[t] = temporal_counts.get(t, 0) + 1
        if s:
            size_counts[s] = size_counts.get(s, 0) + 1

    logger.info("  Temporal: %s", temporal_counts)
    logger.info("  Size:     %s", size_counts)


if __name__ == "__main__":
    main()
