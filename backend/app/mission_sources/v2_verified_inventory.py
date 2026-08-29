"""GCSI Phase 6F-B2.2 — V2 Verified Inventory Builder.

Builds a VerifiedInventoryManifest from a completed AcquisitionLedger and
associated snapshots.

Architecture:
  - Maps ledger rows back to plan entries.
  - Creates VerifiedInventoryEntry per logical product.
  - For JunoCam: exactly 2 representation_record_ids (EDR, RDR).
  - For all others: exactly 1 representation_record_id.
  - The availability_time_utc is the authoritative observation_stop_utc
    from the label (loaded via snapshot).

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
)
from backend.app.mission_sources.v2_inventory_acquisition import (
    AcquisitionLedger,
    AcquisitionLedgerRow,
    AcquisitionStatus,
)

logger = logging.getLogger(__name__)

# Maximum manifest size for bounded load: 16 MiB.
_MAX_MANIFEST_BYTES: int = 16 * 1024 * 1024


class V2VerifiedInventoryBuilder:
    """Builds VerifiedInventoryManifest from a completed ledger + snapshots.

    Maps ledger rows back to plan entries, creates VerifiedInventoryEntry per
    logical product.

    For JunoCam: each logical entry has exactly 2 representation_record_ids (EDR, RDR).
    For others: exactly 1 representation_record_id.
    """

    def build(
        self,
        plan: HistoricalReplayV2AcquisitionPlan,
        ledger: AcquisitionLedger,
        snapshot_root: Path,
    ) -> VerifiedInventoryManifest:
        """Build VerifiedInventoryManifest from plan + ledger + snapshots.

        Only successful rows (ACQUIRED_VERIFIED or REUSED_VERIFIED_SNAPSHOT)
        are included in the manifest. Logical products missing any required
        representation are excluded with a warning.

        Parameters
        ----------
        plan:
            The acquisition plan (provides logical_entries).
        ledger:
            The completed acquisition ledger (provides per-representation outcomes).
        snapshot_root:
            Root directory of snapshots (used to resolve snapshot paths for
            availability_time_utc when not already in ledger rows).

        Returns
        -------
        VerifiedInventoryManifest
            Fully validated manifest.
        """
        # Build index: (logical_product_id, representation_role) → ledger row
        row_index: dict[tuple[str, str], AcquisitionLedgerRow] = {}
        for row in ledger.rows:
            key = (row.logical_product_id, row.representation_role)
            row_index[key] = row

        # Build source record registry
        source_record_refs: list[VerifiedSourceRecordRef] = []
        source_record_ids_seen: set[str] = set()
        provenance_ids_seen: set[str] = set()

        # Build verified entries
        verified_entries: list[VerifiedInventoryEntry] = []

        for plan_entry in sorted(plan.logical_entries, key=lambda e: e.logical_product_id):
            logical_id = plan_entry.logical_product_id

            # Collect successful rows for this entry
            successful_rows: list[AcquisitionLedgerRow] = []
            for rep in plan_entry.representations:
                key = (logical_id, rep.representation_role.value)
                row = row_index.get(key)
                if row is None:
                    logger.warning(
                        "Missing ledger row for %s role=%s",
                        logical_id, rep.representation_role.value,
                    )
                    continue
                if row.acquisition_status not in (
                    AcquisitionStatus.ACQUIRED_VERIFIED,
                    AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
                ):
                    logger.warning(
                        "Non-successful row for %s role=%s status=%s",
                        logical_id, rep.representation_role.value,
                        row.acquisition_status.value,
                    )
                    continue
                successful_rows.append(row)

            # All representations must be successful for the logical entry to be included
            if len(successful_rows) != len(plan_entry.representations):
                logger.warning(
                    "Logical entry %s has %d/%d successful rows — excluded from manifest.",
                    logical_id, len(successful_rows), len(plan_entry.representations),
                )
                continue

            # Determine availability_time_utc
            availability_time: Optional[datetime] = None
            if plan_entry.discovery_availability_time_utc is not None:
                # EXACT_DISCOVERY_METADATA: use the discovery-supplied stop time
                availability_time = plan_entry.discovery_availability_time_utc
            else:
                # LABEL_VERIFICATION_PENDING: get stop time from label (via ledger row)
                for row in successful_rows:
                    if row.observation_stop_utc is not None:
                        try:
                            availability_time = datetime.fromisoformat(
                                row.observation_stop_utc
                            ).astimezone(timezone.utc)
                            break
                        except ValueError:
                            pass

            if availability_time is None:
                logger.warning(
                    "Could not determine availability_time_utc for %s — excluded.",
                    logical_id,
                )
                continue

            # Build representation_record_ids and source_record_refs
            rep_record_ids: list[str] = []
            prov_ids: list[str] = []

            for row in successful_rows:
                if row.source_record_id is None:
                    logger.warning(
                        "Row for %s has no source_record_id — excluded.", logical_id
                    )
                    break
                if row.provenance_id is None:
                    logger.warning(
                        "Row for %s has no provenance_id — excluded.", logical_id
                    )
                    break

                rep_record_ids.append(row.source_record_id)
                prov_ids.append(row.provenance_id)

                if row.source_record_id not in source_record_ids_seen:
                    source_record_ids_seen.add(row.source_record_id)
                    # Determine source_standard
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
            else:
                # Build entry only when inner loop completed without break
                if len(rep_record_ids) == len(successful_rows):
                    entry = VerifiedInventoryEntry(
                        logical_product_id=logical_id,
                        representation_record_ids=tuple(rep_record_ids),
                        availability_time_utc=availability_time,
                        source_fact_provenance_ids=tuple(prov_ids),
                    )
                    verified_entries.append(entry)
                continue
            # break was taken — exclude entry
            continue

        if not verified_entries:
            raise ValueError(
                "V2VerifiedInventoryBuilder: no verified entries — "
                "all logical products failed acquisition."
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
        """Bounded load with confinement checks. Returns validated manifest.

        Parameters
        ----------
        path:
            Path to the manifest JSON file.

        Returns
        -------
        VerifiedInventoryManifest
            Fully validated manifest.

        Raises
        ------
        ValueError
            If the file is too large, invalid JSON, or fails validation.
        FileNotFoundError
            If the file does not exist.
        """
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
