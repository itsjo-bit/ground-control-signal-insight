#!/usr/bin/env python3
"""B2.2.1: Rebuild acquisition ledger from 535 snapshots and create temporal reconciliation.

Rebuilds the ledger with all 535 candidate rows, adding the 8 previously
excluded ineligible products. Also creates the temporal reconciliation manifest.

Changes from B2.2 ledger:
- 535 rows (was 527): adds 8 ineligible snapshot rows
- Acquisition status: REUSED_VERIFIED_SNAPSHOT for all (existing snapshots)
- Temporal status: properly classifies VERIFIED_ELIGIBLE vs FAILED_PRE/FAILED_POST
- All temporal_verification_status fields populated for all successful rows
- POSIX snapshot_ref paths (not OS-specific)
- ledger_id includes all semantic fields per §11
"""
from __future__ import annotations

import sys
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import hashlib
import json
from datetime import datetime, timezone

from backend.app.mission_sources.v2_acquisition_plan_builder import (
    build_plan,
    load_bound_v2_acquisition_plan,
)
from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_UTC,
    HistoricalReplayV2AcquisitionPlan,
    AcquisitionLogicalProductEntry,
    AcquisitionSourceRepresentation,
)
from backend.app.mission_sources.v2_inventory_acquisition import (
    AcquisitionLedger,
    AcquisitionLedgerRow,
    AcquisitionStatus,
    TemporalVerificationStatus,
    SizeVerificationStatus,
    _build_representation_sequence,
    _compute_ledger_id,
    _check_temporal_eligibility,
    _derive_size_verification_status,
    _snapshot_path_for_url,
    save_ledger,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    ArchiveLabelSnapshotStore,
)
from backend.app.mission_sources.v2_temporal_reconciliation import (
    V2ReconciliationEntry,
    V2TemporalReconciliationManifest,
    ReconciliationClassification,
    compute_reconciliation_id,
    save_reconciliation_manifest,
)

_SNAPSHOT_ROOT = _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"
_LEDGER_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_ledger.json"
_RECONCILIATION_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_temporal_reconciliation.json"


def posix_snapshot_ref(path: pathlib.Path) -> str:
    """Return POSIX repository-relative path for snapshot_ref."""
    try:
        rel = path.relative_to(_REPO_ROOT)
    except ValueError:
        rel = path
    return rel.as_posix()


def build_ledger_row(
    idx: int,
    entry: AcquisitionLogicalProductEntry,
    rep: AcquisitionSourceRepresentation,
) -> AcquisitionLedgerRow:
    """Build one ledger row from existing snapshot."""
    label_url = rep.label_url
    source_standard = rep.source_standard.value
    normalizer_id = rep.normalizer_id
    profile_id = rep.profile_id
    instrument = entry.instrument

    snapshot_path = _snapshot_path_for_url(instrument, label_url, _SNAPSHOT_ROOT)

    if not snapshot_path.exists():
        print(f"  WARNING: No snapshot at {snapshot_path}")
        return AcquisitionLedgerRow(
            acquisition_index=idx,
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
            error_detail_code="No snapshot file found",
        )

    try:
        existing_product, existing_prov = ArchiveLabelSnapshotStore.load(snapshot_path)
        env_data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        env_normalizer = env_data.get("normalizer_id", "")
        env_profile = env_data.get("profile_id", "")

        if env_normalizer != normalizer_id or env_profile != profile_id:
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=0,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                error_class="SnapshotProfileMismatch",
                error_detail_code=f"expected {normalizer_id}/{profile_id} got {env_normalizer}/{env_profile}",
            )

        temporal_status = _check_temporal_eligibility(existing_product.observation_stop_utc)
        size_status = _derive_size_verification_status(existing_product)

        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=entry.logical_product_id,
            instrument=instrument,
            representation_role=rep.representation_role.value,
            source_standard=source_standard,
            label_url=label_url,
            normalizer_id=normalizer_id,
            profile_id=profile_id,
            attempt_count=1,
            acquisition_status=AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
            retrieved_at=(
                existing_prov.retrieved_at.isoformat() if existing_prov.retrieved_at else None
            ),
            raw_label_sha256=existing_prov.content_sha256,
            source_record_id=existing_product.source_record_id,
            archive_product_id=existing_product.source_product_id,
            archive_version=existing_product.source_version,
            snapshot_ref=posix_snapshot_ref(snapshot_path),
            snapshot_id=env_data.get("snapshot_id"),
            provenance_id=existing_prov.provenance_id,
            observation_start_utc=(
                existing_product.observation_start_utc.isoformat()
                if existing_product.observation_start_utc else None
            ),
            observation_stop_utc=(
                existing_product.observation_stop_utc.isoformat()
                if existing_product.observation_stop_utc else None
            ),
            temporal_verification_status=temporal_status.value,
            archive_total_size_bytes=existing_product.total_data_size_bytes,
            size_verification_status=size_status.value,
        )
    except Exception as exc:
        print(f"  ERROR loading snapshot for {label_url}: {exc}")
        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=entry.logical_product_id,
            instrument=instrument,
            representation_role=rep.representation_role.value,
            source_standard=source_standard,
            label_url=label_url,
            normalizer_id=normalizer_id,
            profile_id=profile_id,
            attempt_count=0,
            acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
            error_class="SnapshotLoadError",
            error_detail_code=str(exc)[:120],
        )


def build_reconciliation_entry(
    entry: AcquisitionLogicalProductEntry,
    rows: list[AcquisitionLedgerRow],
    plan: HistoricalReplayV2AcquisitionPlan,
) -> V2ReconciliationEntry:
    """Build one temporal reconciliation entry from ledger rows for a logical product."""
    # Get all rows for this logical product
    product_rows = [r for r in rows if r.logical_product_id == entry.logical_product_id]

    source_record_ids = []
    snapshot_refs = []
    provenance_ids = []
    stop_times = []

    for row in sorted(product_rows, key=lambda r: r.representation_role):
        if row.acquisition_status in (
            AcquisitionStatus.ACQUIRED_VERIFIED,
            AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
        ):
            source_record_ids.append(row.source_record_id or "")
            snapshot_refs.append(row.snapshot_ref or "")
            provenance_ids.append(row.provenance_id or "")
            if row.observation_stop_utc:
                stop_times.append(row.observation_stop_utc)

    # Use the first stop time (for JunoCam pairs, they should be equal)
    authoritative_stop = stop_times[0] if stop_times else None

    # Classify based on temporal status from first row
    temporal_status = None
    for row in product_rows:
        if row.temporal_verification_status:
            temporal_status = row.temporal_verification_status
            break

    if temporal_status == TemporalVerificationStatus.VERIFIED_ELIGIBLE.value:
        classification = ReconciliationClassification.ELIGIBLE
        reason_code = "STOP_WITHIN_WINDOW"
    elif temporal_status == TemporalVerificationStatus.FAILED_PRE.value:
        classification = ReconciliationClassification.INELIGIBLE_PRE_WINDOW
        reason_code = "STOP_PRE_ACCUMULATION_START"
    elif temporal_status == TemporalVerificationStatus.FAILED_POST.value:
        classification = ReconciliationClassification.INELIGIBLE_POST_DECISION
        reason_code = "STOP_POST_DECISION_EPOCH"
    else:
        # For EXACT_DISCOVERY_METADATA entries that have discovery_availability_time_utc
        if entry.discovery_availability_time_utc is not None:
            stop_utc = entry.discovery_availability_time_utc
            if stop_utc <= ACCUMULATION_START_UTC:
                classification = ReconciliationClassification.INELIGIBLE_PRE_WINDOW
                reason_code = "STOP_PRE_ACCUMULATION_START"
            elif stop_utc > DECISION_EPOCH_UTC:
                classification = ReconciliationClassification.INELIGIBLE_POST_DECISION
                reason_code = "STOP_POST_DECISION_EPOCH"
            else:
                classification = ReconciliationClassification.ELIGIBLE
                reason_code = "STOP_WITHIN_WINDOW"
        else:
            classification = ReconciliationClassification.ELIGIBLE
            reason_code = "STOP_WITHIN_WINDOW"

    return V2ReconciliationEntry(
        logical_product_id=entry.logical_product_id,
        source_record_ids=tuple(source_record_ids),
        snapshot_refs=tuple(snapshot_refs),
        provenance_ids=tuple(provenance_ids),
        authoritative_observation_stop_utc=authoritative_stop,
        classification=classification,
        reason_code=reason_code,
    )


def main():
    print("Building 411-entry candidate acquisition plan...")
    plan = build_plan()
    print(f"  plan_id: {plan.plan_id}")
    print(f"  logical: {len(plan.logical_entries)}")
    total_refs = sum(len(e.representations) for e in plan.logical_entries)
    print(f"  refs:    {total_refs}")

    print("\nBuilding acquisition sequence...")
    sequence = _build_representation_sequence(plan)
    print(f"  sequence length: {len(sequence)}")

    print("\nBuilding ledger rows from 535 snapshots...")
    rows = []
    for idx, entry, rep in sequence:
        row = build_ledger_row(idx, entry, rep)
        rows.append(row)

    # Sort by acquisition_index
    rows.sort(key=lambda r: r.acquisition_index)

    # Verify counts
    successful = [r for r in rows if r.acquisition_status in (
        AcquisitionStatus.ACQUIRED_VERIFIED,
        AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
    )]
    failed = [r for r in rows if r.acquisition_status not in (
        AcquisitionStatus.ACQUIRED_VERIFIED,
        AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
    )]
    print(f"  successful: {len(successful)}")
    print(f"  failed:     {len(failed)}")

    if failed:
        for r in failed:
            print(f"  FAILED: {r.logical_product_id} ({r.acquisition_status.value})")

    from collections import Counter
    temporal_counts = Counter(r.temporal_verification_status for r in rows if r.temporal_verification_status)
    print(f"  temporal status: {dict(temporal_counts)}")

    # Compute ledger_id with all semantic fields
    ledger_id = _compute_ledger_id(rows, plan.replay_id, plan.plan_id)
    ledger = AcquisitionLedger(
        ledger_id=ledger_id,
        replay_id=plan.replay_id,
        plan_id=plan.plan_id,
        rows=tuple(rows),
    )
    print(f"\nLedger rows: {len(ledger.rows)}")
    print(f"ledger_id: {ledger_id}")

    # Save ledger
    save_ledger(ledger, _LEDGER_PATH)
    print(f"Saved ledger to {_LEDGER_PATH}")

    print("\nBuilding temporal reconciliation manifest...")
    reconciliation_entries = []
    for entry in sorted(plan.logical_entries, key=lambda e: e.logical_product_id):
        rec_entry = build_reconciliation_entry(entry, rows, plan)
        reconciliation_entries.append(rec_entry)

    eligible_logical = sum(
        1 for e in reconciliation_entries
        if e.classification == ReconciliationClassification.ELIGIBLE
    )
    ineligible_logical = len(reconciliation_entries) - eligible_logical
    eligible_source = sum(
        len(e.source_record_ids)
        for e in reconciliation_entries
        if e.classification == ReconciliationClassification.ELIGIBLE
    )
    ineligible_source = sum(
        len(e.source_record_ids)
        for e in reconciliation_entries
        if e.classification != ReconciliationClassification.ELIGIBLE
    )

    print(f"  candidate logical: {len(reconciliation_entries)}")
    print(f"  eligible logical:  {eligible_logical}")
    print(f"  ineligible logical:{ineligible_logical}")
    print(f"  eligible source:   {eligible_source}")
    print(f"  ineligible source: {ineligible_source}")

    # Check against spec expectations
    if eligible_logical != 403:
        print(f"  WARNING: Expected 403 eligible, got {eligible_logical}")
    if eligible_source != 527:
        print(f"  WARNING: Expected 527 eligible source, got {eligible_source}")
    if ineligible_logical != 8:
        print(f"  WARNING: Expected 8 ineligible, got {ineligible_logical}")

    # Compute reconciliation_id
    entries_raw = [
        {
            "authoritative_observation_stop_utc": e.authoritative_observation_stop_utc,
            "classification": e.classification.value,
            "logical_product_id": e.logical_product_id,
            "provenance_ids": list(e.provenance_ids),
            "reason_code": e.reason_code,
            "snapshot_refs": list(e.snapshot_refs),
            "source_record_ids": list(e.source_record_ids),
        }
        for e in reconciliation_entries
    ]
    reconciliation_id = compute_reconciliation_id(
        replay_id=plan.replay_id,
        candidate_plan_id=plan.plan_id,
        discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
        accumulation_start_utc=plan.accumulation_start_utc,
        decision_epoch_utc=plan.decision_epoch_utc,
        candidate_logical_count=len(reconciliation_entries),
        candidate_source_count=total_refs,
        eligible_logical_count=eligible_logical,
        eligible_source_count=eligible_source,
        ineligible_logical_count=ineligible_logical,
        ineligible_source_count=ineligible_source,
        entries=entries_raw,
    )

    manifest = V2TemporalReconciliationManifest(
        schema="gcsi.v2_temporal_reconciliation",
        schema_version=1,
        reconciliation_id=reconciliation_id,
        replay_id=plan.replay_id,
        candidate_plan_id=plan.plan_id,
        discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
        accumulation_start_utc=plan.accumulation_start_utc,
        decision_epoch_utc=plan.decision_epoch_utc,
        candidate_logical_count=len(reconciliation_entries),
        candidate_source_count=total_refs,
        eligible_logical_count=eligible_logical,
        eligible_source_count=eligible_source,
        ineligible_logical_count=ineligible_logical,
        ineligible_source_count=ineligible_source,
        entries=tuple(reconciliation_entries),
    )
    print(f"  reconciliation_id: {reconciliation_id}")

    save_reconciliation_manifest(manifest, _RECONCILIATION_PATH)
    print(f"Saved reconciliation to {_RECONCILIATION_PATH}")

    # Print ineligible products
    ineligible_entries = [
        e for e in reconciliation_entries
        if e.classification != ReconciliationClassification.ELIGIBLE
    ]
    print("\nIneligible products:")
    for e in sorted(ineligible_entries, key=lambda x: x.logical_product_id):
        print(f"  {e.logical_product_id}")
        print(f"    classification: {e.classification.value}")
        print(f"    stop_utc: {e.authoritative_observation_stop_utc}")
        print(f"    reason: {e.reason_code}")

    print(f"\nB2.2.1 reconciliation complete.")
    print(f"  ELIGIBLE: {eligible_logical} logical / {eligible_source} source")
    print(f"  INELIGIBLE: {ineligible_logical} logical / {ineligible_source} source")

    if eligible_logical == 403 and eligible_source == 527 and ineligible_logical == 8:
        print("  6F_B221_STATUS = RECONCILED_SOURCE_BUNDLE_READY_FOR_REVIEW")
    else:
        print("  6F_B221_STATUS = RECONCILIATION_REVIEW_REQUIRED")
        print(f"  Expected: 403/527 eligible, 8 ineligible")
        print(f"  Got:      {eligible_logical}/{eligible_source} eligible, {ineligible_logical} ineligible")


if __name__ == "__main__":
    main()
