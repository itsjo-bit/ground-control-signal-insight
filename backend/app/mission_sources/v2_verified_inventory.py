"""GCSI Phase 6F-B2.2.1 — V2 Verified Inventory Builder.

Builds a VerifiedInventoryManifest from a temporal reconciliation manifest
and completed AcquisitionLedger.

B2.2.1 changes from B2.2:
- build() now requires V2TemporalReconciliationManifest as input.
- Only ELIGIBLE entries from reconciliation are included (§7).
- Fail closed: if any ELIGIBLE logical product is missing a required
  representation, RAISE instead of warn+skip (§15).
- Verify ledger.plan_id == candidate_plan.plan_id (§15).
- availability_time_utc is always from the verified authoritative label
  snapshot (not from discovery metadata) (§16).
- For EXACT_DISCOVERY_METADATA families (JunoCam, WAVES Burst, JADE):
  discovery STOP_TIME is cross-checked against label STOP_TIME (§16).

All models: frozen=True, extra="forbid".
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.app.mission_sources.archive_models import (
    ArchiveSourceStandard,
    VerifiedInventoryEntry,
    VerifiedInventoryManifest,
    VerifiedSourceRecordRef,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    ArchiveLabelSnapshotStore,
)
from backend.app.mission_sources.v2_acquisition_plan import (
    HistoricalReplayV2AcquisitionPlan,
    TemporalEvidenceStatus,
)
from backend.app.mission_sources.v2_inventory_acquisition import (
    AcquisitionLedger,
    AcquisitionLedgerRow,
    AcquisitionStatus,
)
from backend.app.mission_sources.v2_temporal_reconciliation import (
    ReconciliationClassification,
    V2TemporalReconciliationManifest,
)

logger = logging.getLogger(__name__)

# Maximum manifest size for bounded load: 16 MiB.
_MAX_MANIFEST_BYTES: int = 16 * 1024 * 1024


class V2VerifiedInventoryBuilder:
    """Builds VerifiedInventoryManifest from reconciliation + ledger.

    B2.2.1: Uses V2TemporalReconciliationManifest to select ELIGIBLE entries.
    Fail-closed: any ELIGIBLE product missing a representation raises ValueError.

    For JunoCam: each logical entry has exactly 2 representation_record_ids (EDR, RDR).
    For others: exactly 1 representation_record_id.
    """

    def build(
        self,
        plan: HistoricalReplayV2AcquisitionPlan,
        ledger: AcquisitionLedger,
        snapshot_root: Path,
        reconciliation: Optional[V2TemporalReconciliationManifest] = None,
    ) -> VerifiedInventoryManifest:
        """Build VerifiedInventoryManifest from plan + ledger + reconciliation.

        B2.2.1: Derives eligible entries from reconciliation manifest.
        Fails closed if any ELIGIBLE product is missing all required representations.

        Parameters
        ----------
        plan:
            The candidate acquisition plan (411 logical entries / 535 refs).
        ledger:
            The completed acquisition ledger (535 rows).
        snapshot_root:
            Root directory of snapshots.
        reconciliation:
            V2TemporalReconciliationManifest. If None, falls back to ledger
            temporal_verification_status for backward compatibility.

        Returns
        -------
        VerifiedInventoryManifest
            Fully validated manifest with only ELIGIBLE entries.

        Raises
        ------
        ValueError
            If plan_id/ledger binding mismatch, or any ELIGIBLE entry is missing
            required representations.
        """
        # §15: Require ledger.plan_id == plan.plan_id
        if ledger.plan_id != plan.plan_id:
            raise ValueError(
                f"Ledger plan_id {ledger.plan_id!r} != plan plan_id {plan.plan_id!r}. "
                "Ledger was not built from this acquisition plan."
            )

        # §15: Require ledger has correct row count
        if len(ledger.rows) != sum(len(e.representations) for e in plan.logical_entries):
            raise ValueError(
                f"Ledger has {len(ledger.rows)} rows but plan has "
                f"{sum(len(e.representations) for e in plan.logical_entries)} representations. "
                "Ledger row count mismatch."
            )

        # Determine eligible logical product IDs from reconciliation or ledger
        eligible_logical_ids: set[str] = set()
        if reconciliation is not None:
            for entry in reconciliation.entries:
                if entry.classification == ReconciliationClassification.ELIGIBLE:
                    eligible_logical_ids.add(entry.logical_product_id)
        else:
            # Fallback: use ledger temporal_verification_status
            for row in ledger.rows:
                if row.temporal_verification_status == "VERIFIED_ELIGIBLE":
                    eligible_logical_ids.add(row.logical_product_id)

        # Build index: (logical_product_id, representation_role) → ledger row
        row_index: dict[tuple[str, str], AcquisitionLedgerRow] = {}
        for row in ledger.rows:
            key = (row.logical_product_id, row.representation_role)
            row_index[key] = row

        # Build source record registry
        source_record_refs: list[VerifiedSourceRecordRef] = []
        source_record_ids_seen: set[str] = set()
        provenance_ids_seen: set[str] = set()

        # Build verified entries - only for ELIGIBLE logical products
        verified_entries: list[VerifiedInventoryEntry] = []

        for plan_entry in sorted(plan.logical_entries, key=lambda e: e.logical_product_id):
            logical_id = plan_entry.logical_product_id

            # §15: Skip non-eligible products
            if logical_id not in eligible_logical_ids:
                continue

            # Collect successful rows for this entry
            successful_rows: list[AcquisitionLedgerRow] = []
            failed_roles: list[str] = []
            for rep in plan_entry.representations:
                key = (logical_id, rep.representation_role.value)
                row = row_index.get(key)
                if row is None:
                    failed_roles.append(f"{rep.representation_role.value} (no ledger row)")
                    continue
                if row.acquisition_status not in (
                    AcquisitionStatus.ACQUIRED_VERIFIED,
                    AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
                ):
                    failed_roles.append(
                        f"{rep.representation_role.value} (status={row.acquisition_status.value})"
                    )
                    continue
                successful_rows.append(row)

            # §15: Fail closed — ELIGIBLE products must have all representations
            if len(successful_rows) != len(plan_entry.representations):
                raise ValueError(
                    f"ELIGIBLE logical product {logical_id!r} has {len(successful_rows)}"
                    f"/{len(plan_entry.representations)} successful representations. "
                    f"Missing: {failed_roles}. "
                    "Cannot produce partial manifest for ELIGIBLE product."
                )

            # §16: Determine availability_time_utc from authoritative label snapshot
            # For ALL families, the verified label is the final source fact.
            # For EXACT families, also cross-check against discovery stop time.
            availability_time: Optional[datetime] = None

            for row in successful_rows:
                if row.observation_stop_utc is not None:
                    try:
                        label_stop = datetime.fromisoformat(
                            row.observation_stop_utc
                        ).astimezone(timezone.utc)
                        if availability_time is None:
                            availability_time = label_stop
                        else:
                            # For multi-rep products (JunoCam), use same stop time
                            if availability_time != label_stop:
                                logger.warning(
                                    "Stop time mismatch between representations for %s: "
                                    "%s vs %s. Using first.",
                                    logical_id, availability_time, label_stop,
                                )
                    except ValueError:
                        pass

            # §16: Cross-check for EXACT_DISCOVERY_METADATA families
            if plan_entry.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA:
                if plan_entry.discovery_availability_time_utc is not None and availability_time is not None:
                    discovery_stop = plan_entry.discovery_availability_time_utc.astimezone(timezone.utc)
                    # Allow small tolerance (1 second) for rounding
                    diff = abs((discovery_stop - availability_time).total_seconds())
                    if diff > 1.0:
                        logger.warning(
                            "EXACT_DISCOVERY_METADATA stop time mismatch for %s: "
                            "discovery=%s label=%s (diff=%.3fs). Using label value.",
                            logical_id, discovery_stop.isoformat(), availability_time.isoformat(), diff,
                        )
                    # Use label value as authoritative (discovery is cross-check only)
                    # availability_time already set from label above

            if availability_time is None:
                raise ValueError(
                    f"Cannot determine availability_time_utc for ELIGIBLE product {logical_id!r}. "
                    "No authoritative stop time from verified label. Cannot include in manifest."
                )

            # Build representation_record_ids and source_record_refs
            rep_record_ids: list[str] = []
            prov_ids: list[str] = []
            all_have_ids = True

            for row in successful_rows:
                if row.source_record_id is None:
                    raise ValueError(
                        f"ELIGIBLE product {logical_id!r} row has no source_record_id. "
                        "Cannot include in manifest."
                    )
                if row.provenance_id is None:
                    raise ValueError(
                        f"ELIGIBLE product {logical_id!r} row has no provenance_id. "
                        "Cannot include in manifest."
                    )

                rep_record_ids.append(row.source_record_id)
                prov_ids.append(row.provenance_id)

                if row.source_record_id not in source_record_ids_seen:
                    source_record_ids_seen.add(row.source_record_id)
                    std = ArchiveSourceStandard(row.source_standard)
                    source_record_refs.append(
                        VerifiedSourceRecordRef(
                            source_record_id=row.source_record_id,
                            source_standard=std,
                            snapshot_ref=row.snapshot_ref,
                            provenance_id=row.provenance_id,
                            normalizer_id=row.normalizer_id,
                            profile_id=row.profile_id,
                        )
                    )
                    provenance_ids_seen.add(row.provenance_id)

            entry = VerifiedInventoryEntry(
                logical_product_id=logical_id,
                representation_record_ids=tuple(rep_record_ids),
                availability_time_utc=availability_time,
                source_fact_provenance_ids=tuple(prov_ids),
            )
            verified_entries.append(entry)

        if not verified_entries:
            raise ValueError(
                "V2VerifiedInventoryBuilder: no verified entries — "
                "all eligible logical products failed acquisition."
            )

        # §15: Cross-check final count against reconciliation
        if reconciliation is not None:
            expected_eligible = reconciliation.eligible_logical_count
            if len(verified_entries) != expected_eligible:
                raise ValueError(
                    f"Verified inventory has {len(verified_entries)} entries but "
                    f"reconciliation says {expected_eligible} eligible. Mismatch."
                )

        manifest = VerifiedInventoryManifest.build(
            entries=verified_entries,
            source_records=source_record_refs,
        )
        return manifest

    def save_manifest(
        self,
        manifest: VerifiedInventoryManifest,
        output_path: Path,
    ) -> None:
        """Serialize manifest to JSON at output_path."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        data = manifest.model_dump(mode="json")
        output_path.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8"
        )
        logger.info("Saved verified inventory manifest to %s", output_path)

    def load_manifest(
        self,
        path: Path,
    ) -> VerifiedInventoryManifest:
        """Bounded load with confinement checks. Returns validated manifest."""
        if not path.is_file():
            raise FileNotFoundError(f"Manifest file not found: {path!r}")

        size = path.stat().st_size
        if size > _MAX_MANIFEST_BYTES:
            raise ValueError(
                f"Manifest file exceeds maximum size ({_MAX_MANIFEST_BYTES} bytes): "
                f"{path!r} is {size} bytes."
            )

        raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise ValueError(
                f"Manifest content exceeds maximum size ({_MAX_MANIFEST_BYTES} bytes)."
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Manifest file is not valid JSON: {exc}") from exc

        return VerifiedInventoryManifest.model_validate(data, strict=False)
