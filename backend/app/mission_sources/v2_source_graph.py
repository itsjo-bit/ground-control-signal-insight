"""GCSI Phase 6F-B3 — V2 Verified Source Graph Loader.

This module provides :func:`load_verified_v2_source_graph`, which verifies the
COMPLETE frozen artifact chain for the Juno PJ62 large historical replay V2:

    root V2SourceBundle (bundle_id verified)
        ↓
    candidate acquisition plan (plan_id verified, cross-checked)
        ↓
    discovery evidence sidecar (artifact_id verified, cross-checked)
        ↓
    acquisition ledger (ledger_id verified, row-level cross-check)
        ↓
    535 label snapshots (snapshot_id each verified)
        ↓
    temporal reconciliation (reconciliation_id verified, re-classification)
        ↓
    verified inventory (manifest_id verified, set equality check)
        ↓
    Horizons geometry snapshot (snapshot_id verified)

Cross-artifact mutation defence
---------------------------------
The loader rejects any scenario where:
1. A root source-bundle field is edited,
2. bundle_id is recomputed to be syntactically valid,
3. but a referenced child artifact ID disagrees.

Such an attack FAILS because the child artifact IDs are independently
recomputed from child content, not taken on trust from the bundle root.

Design
------
- NO PDS network requests.
- NO Horizons requests.
- NO re-acquisition.
- Every artifact is loaded through its existing production trust loader.
- Temporal eligibility is re-derived independently from stored classification.

B3 scope rules
--------------
- load() is OFFLINE only — no httpx, no socket.
- V1 paths are unchanged.
- The VerifiedV2SourceGraph result is immutable (frozen dataclass).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Repository root (for relative-path resolution)
# ---------------------------------------------------------------------------

_REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Frozen expected constants for PJ62 V2
# ---------------------------------------------------------------------------

_EXPECTED_BUNDLE_ID = "950432d121a3aaa8340dcb24107bb42138fd6042f2ed2b254485f14c6a6e821a"
_EXPECTED_SCHEMA_VERSION = 2
_EXPECTED_REPLAY_ID = "juno_pj62_large_replay_v2"
_EXPECTED_CANDIDATE_LOGICAL = 411
_EXPECTED_CANDIDATE_SOURCE = 535
_EXPECTED_ELIGIBLE_LOGICAL = 403
_EXPECTED_ELIGIBLE_SOURCE = 527
_EXPECTED_INELIGIBLE_LOGICAL = 8
_EXPECTED_INELIGIBLE_SOURCE = 8
_EXPECTED_LABEL_SNAPSHOT_COUNT = 535
_EXPECTED_HORIZONS_SNAPSHOT_ID = "b2e0f7a6f4b8a3221c7f74ea6f71c15a96a4210e76c5711be3c0d08b4442a3f1"
_EXPECTED_HORIZONS_TARGET = "-61"
_EXPECTED_HORIZONS_CENTER = "500@399"
_EXPECTED_DECISION_EPOCH = "2024-06-14T09:35:17.546000+00:00"
_DECISION_EPOCH_UTC = datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc)
_ACCUMULATION_START_UTC = datetime(2024, 6, 13, 10, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# VerifiedV2SourceGraph — immutable typed result
# ---------------------------------------------------------------------------


class VerifiedV2SourceGraph(BaseModel):
    """Immutable result of load_verified_v2_source_graph().

    All child artifacts have been independently loaded through their
    production trust loaders and cross-verified against the root bundle.
    Temporal eligibility has been re-derived independently from stored
    classification.

    Fields carry the loaded, verified domain objects — not raw JSON dicts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    # Root
    source_bundle: object = Field(description="Loaded, id-verified V2SourceBundle.")

    # Child artifacts
    candidate_plan: object = Field(description="Loaded, id-verified HistoricalReplayV2AcquisitionPlan.")
    discovery_sidecar: object = Field(description="Loaded, artifact_id-verified HistoricalReplayV2DiscoveryEvidenceSidecar.")
    acquisition_ledger: object = Field(description="Loaded, ledger_id-verified AcquisitionLedger.")
    temporal_reconciliation: object = Field(description="Loaded, reconciliation_id-verified V2TemporalReconciliationManifest.")
    verified_inventory: object = Field(description="Loaded, manifest_id-verified VerifiedInventoryManifest.")
    horizons_result: object = Field(description="Loaded, snapshot_id-verified HorizonsGeometryResult.")

    # All 535 snapshots indexed by source_record_id
    snapshots_by_source_record_id: object = Field(
        description=(
            "Dict[source_record_id, (ArchiveScienceProduct, ProvenanceRecord)]. "
            "All 535 label snapshots loaded and verified."
        )
    )

    # Summary counts (cross-verified)
    candidate_logical_count: int
    candidate_source_count: int
    eligible_logical_count: int
    eligible_source_count: int
    ineligible_logical_count: int
    ineligible_source_count: int
    label_snapshot_count: int

    # Horizons geometry
    horizons_range_km: float
    horizons_epoch_utc: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_utc_iso(s: str) -> str:
    """Normalize a stored UTC ISO-8601 string to canonical form for comparison."""
    try:
        dt = datetime.fromisoformat(s)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return s


def _recompute_eligibility(stop_utc_str: Optional[str]) -> str:
    """Re-derive temporal classification string from authoritative stop time."""
    if stop_utc_str is None:
        return "INELIGIBLE_NO_STOP"
    try:
        stop = datetime.fromisoformat(stop_utc_str)
        if stop.tzinfo is None:
            stop = stop.replace(tzinfo=timezone.utc)
        else:
            stop = stop.astimezone(timezone.utc)
    except Exception:
        return "INELIGIBLE_PARSE_ERROR"
    if stop <= _ACCUMULATION_START_UTC:
        return "INELIGIBLE_PRE_WINDOW"
    if stop > _DECISION_EPOCH_UTC:
        return "INELIGIBLE_POST_DECISION"
    return "ELIGIBLE"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_verified_v2_source_graph(
    bundle_path: Optional[Path] = None,
) -> VerifiedV2SourceGraph:
    """Load and fully verify the complete PJ62 V2 source artifact graph.

    Parameters
    ----------
    bundle_path:
        Optional override for the source bundle JSON path.
        Defaults to data/replays/juno_pj62_large_replay_v2_source_bundle.json.

    Returns
    -------
    VerifiedV2SourceGraph
        Immutable, fully verified source graph.

    Raises
    ------
    ValueError
        If any cross-verification step fails (ID mismatch, count mismatch,
        ineligible record in eligible set, etc.).
    FileNotFoundError
        If any required artifact file is missing.
    RuntimeError
        If temporal re-classification contradicts committed reconciliation.
        Use 6F_B3_STATUS = SOURCE_GRAPH_CONTRADICTION in calling context.
    """
    from backend.app.mission_sources.v2_source_bundle import load_source_bundle
    from backend.app.mission_sources.v2_acquisition_plan import load_acquisition_plan
    from backend.app.mission_sources.v2_acquisition_plan_builder import _load_sidecar
    from backend.app.mission_sources.v2_inventory_acquisition import load_ledger, AcquisitionStatus
    from backend.app.mission_sources.v2_temporal_reconciliation import (
        load_reconciliation_manifest,
        ReconciliationClassification,
    )
    from backend.app.mission_sources.v2_verified_inventory import V2VerifiedInventoryBuilder
    from backend.app.mission_sources.snapshots.horizons_snapshot import HorizonsSnapshotStore
    from backend.app.mission_sources.snapshots.archive_label_snapshot import ArchiveLabelSnapshotStore
    from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty

    # -----------------------------------------------------------------------
    # Step 1: Load root source bundle
    # -----------------------------------------------------------------------
    if bundle_path is None:
        bundle_path = (
            _REPO_ROOT / "data" / "replays"
            / "juno_pj62_large_replay_v2_source_bundle.json"
        )

    logger.info("Loading source bundle from %s", bundle_path)
    bundle = load_source_bundle(bundle_path)

    # Require known fixed values
    if bundle.bundle_id != _EXPECTED_BUNDLE_ID:
        raise ValueError(
            f"Source bundle bundle_id {bundle.bundle_id!r} != "
            f"expected {_EXPECTED_BUNDLE_ID!r}."
        )
    if bundle.schema_version != _EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Source bundle schema_version {bundle.schema_version} != "
            f"expected {_EXPECTED_SCHEMA_VERSION}."
        )
    if bundle.replay_id != _EXPECTED_REPLAY_ID:
        raise ValueError(
            f"Source bundle replay_id {bundle.replay_id!r} != "
            f"expected {_EXPECTED_REPLAY_ID!r}."
        )
    if bundle.candidate_logical_count != _EXPECTED_CANDIDATE_LOGICAL:
        raise ValueError(
            f"Source bundle candidate_logical_count {bundle.candidate_logical_count} != "
            f"expected {_EXPECTED_CANDIDATE_LOGICAL}."
        )
    if bundle.candidate_source_count != _EXPECTED_CANDIDATE_SOURCE:
        raise ValueError(
            f"Source bundle candidate_source_count {bundle.candidate_source_count} != "
            f"expected {_EXPECTED_CANDIDATE_SOURCE}."
        )
    if bundle.eligible_logical_count != _EXPECTED_ELIGIBLE_LOGICAL:
        raise ValueError(
            f"Source bundle eligible_logical_count {bundle.eligible_logical_count} != "
            f"expected {_EXPECTED_ELIGIBLE_LOGICAL}."
        )
    if bundle.eligible_source_count != _EXPECTED_ELIGIBLE_SOURCE:
        raise ValueError(
            f"Source bundle eligible_source_count {bundle.eligible_source_count} != "
            f"expected {_EXPECTED_ELIGIBLE_SOURCE}."
        )
    if bundle.ineligible_logical_count != _EXPECTED_INELIGIBLE_LOGICAL:
        raise ValueError(
            f"Source bundle ineligible_logical_count {bundle.ineligible_logical_count} != "
            f"expected {_EXPECTED_INELIGIBLE_LOGICAL}."
        )
    if bundle.ineligible_source_count != _EXPECTED_INELIGIBLE_SOURCE:
        raise ValueError(
            f"Source bundle ineligible_source_count {bundle.ineligible_source_count} != "
            f"expected {_EXPECTED_INELIGIBLE_SOURCE}."
        )
    if bundle.label_snapshot_count != _EXPECTED_LABEL_SNAPSHOT_COUNT:
        raise ValueError(
            f"Source bundle label_snapshot_count {bundle.label_snapshot_count} != "
            f"expected {_EXPECTED_LABEL_SNAPSHOT_COUNT}."
        )

    # -----------------------------------------------------------------------
    # Step 2: Load candidate acquisition plan
    # -----------------------------------------------------------------------
    plan_path = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_plan.json"
    logger.info("Loading candidate acquisition plan from %s", plan_path)
    plan = load_acquisition_plan(str(plan_path))

    # Cross-check: bundle.candidate_plan_id == plan.plan_id
    if bundle.candidate_plan_id != plan.plan_id:
        raise ValueError(
            f"Source bundle candidate_plan_id {bundle.candidate_plan_id!r} != "
            f"plan.plan_id {plan.plan_id!r}. "
            "Cross-artifact mutation detected."
        )

    # Require 411 logical / 535 source from plan
    plan_logical_count = len(plan.logical_entries)
    plan_source_count = sum(len(e.representations) for e in plan.logical_entries)
    if plan_logical_count != _EXPECTED_CANDIDATE_LOGICAL:
        raise ValueError(
            f"Candidate plan has {plan_logical_count} logical entries; "
            f"expected {_EXPECTED_CANDIDATE_LOGICAL}."
        )
    if plan_source_count != _EXPECTED_CANDIDATE_SOURCE:
        raise ValueError(
            f"Candidate plan has {plan_source_count} source representations; "
            f"expected {_EXPECTED_CANDIDATE_SOURCE}."
        )

    # Require unique logical_product_id and unique label_url (enforced by plan model,
    # but re-assert here for the source graph trust layer)
    plan_ids = [e.logical_product_id for e in plan.logical_entries]
    if len(plan_ids) != len(set(plan_ids)):
        raise ValueError("Duplicate logical_product_id in candidate plan.")
    plan_urls = [r.label_url for e in plan.logical_entries for r in e.representations]
    if len(plan_urls) != len(set(plan_urls)):
        raise ValueError("Duplicate label_url in candidate plan.")

    # Require plan.discovery_evidence_artifact_id == bundle.discovery_evidence_artifact_id
    if plan.discovery_evidence_artifact_id != bundle.discovery_evidence_artifact_id:
        raise ValueError(
            f"Plan discovery_evidence_artifact_id {plan.discovery_evidence_artifact_id!r} "
            f"!= bundle discovery_evidence_artifact_id {bundle.discovery_evidence_artifact_id!r}. "
            "Cross-artifact mutation detected."
        )

    # -----------------------------------------------------------------------
    # Step 3: Load discovery sidecar
    # -----------------------------------------------------------------------
    logger.info("Loading discovery evidence sidecar")
    sidecar = _load_sidecar()

    from backend.app.mission_sources.v2_sidecar_models import compute_sidecar_artifact_id
    sidecar_data_path = (
        _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_discovery_evidence.json"
    )
    import json as _json
    raw_sidecar = _json.loads(sidecar_data_path.read_text(encoding="utf-8"))
    computed_artifact_id = compute_sidecar_artifact_id(raw_sidecar)

    if computed_artifact_id != bundle.discovery_evidence_artifact_id:
        raise ValueError(
            f"Sidecar artifact_id (computed={computed_artifact_id!r}) != "
            f"bundle.discovery_evidence_artifact_id ({bundle.discovery_evidence_artifact_id!r})."
        )

    # -----------------------------------------------------------------------
    # Step 4: Load acquisition ledger
    # -----------------------------------------------------------------------
    ledger_path = (
        _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_ledger.json"
    )
    logger.info("Loading acquisition ledger from %s", ledger_path)
    ledger = load_ledger(ledger_path)

    # Cross-check ledger IDs
    if ledger.ledger_id != bundle.acquisition_ledger_id:
        raise ValueError(
            f"Ledger ledger_id {ledger.ledger_id!r} != "
            f"bundle.acquisition_ledger_id {bundle.acquisition_ledger_id!r}. "
            "Cross-artifact mutation detected."
        )
    if ledger.plan_id != plan.plan_id:
        raise ValueError(
            f"Ledger plan_id {ledger.plan_id!r} != plan.plan_id {plan.plan_id!r}."
        )
    if ledger.replay_id != bundle.replay_id:
        raise ValueError(
            f"Ledger replay_id {ledger.replay_id!r} != bundle.replay_id {bundle.replay_id!r}."
        )
    if len(ledger.rows) != _EXPECTED_CANDIDATE_SOURCE:
        raise ValueError(
            f"Ledger has {len(ledger.rows)} rows; expected {_EXPECTED_CANDIDATE_SOURCE}."
        )

    # Build indexes for cross-verification
    # Index: (logical_product_id, representation_role, label_url) → ledger row
    ledger_index: dict[tuple[str, str, str], object] = {}
    for row in ledger.rows:
        key = (row.logical_product_id, row.representation_role, row.label_url)
        if key in ledger_index:
            raise ValueError(
                f"Duplicate ledger key: {key!r}."
            )
        ledger_index[key] = row

    # For every plan representation: require exactly one ledger row
    for entry in plan.logical_entries:
        for rep in entry.representations:
            key = (entry.logical_product_id, rep.representation_role.value, rep.label_url)
            if key not in ledger_index:
                raise ValueError(
                    f"Plan representation not found in ledger: "
                    f"logical_product_id={entry.logical_product_id!r}, "
                    f"role={rep.representation_role.value!r}, url={rep.label_url!r}."
                )
            row = ledger_index[key]
            if row.normalizer_id != rep.normalizer_id:
                raise ValueError(
                    f"Ledger normalizer_id mismatch for {key!r}: "
                    f"ledger={row.normalizer_id!r} plan={rep.normalizer_id!r}."
                )
            if row.profile_id != rep.profile_id:
                raise ValueError(
                    f"Ledger profile_id mismatch for {key!r}: "
                    f"ledger={row.profile_id!r} plan={rep.profile_id!r}."
                )
            if row.source_standard != rep.source_standard.value:
                raise ValueError(
                    f"Ledger source_standard mismatch for {key!r}: "
                    f"ledger={row.source_standard!r} plan={rep.source_standard.value!r}."
                )

    # -----------------------------------------------------------------------
    # Step 5: Load all 535 snapshots
    # -----------------------------------------------------------------------
    logger.info("Loading all 535 label snapshots")
    snapshot_root = (
        _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"
    )

    snapshots_by_source_record_id: dict[str, tuple] = {}
    snapshots_by_snapshot_id: dict[str, tuple] = {}

    successful_rows = [
        row for row in ledger.rows
        if row.acquisition_status in (
            AcquisitionStatus.ACQUIRED_VERIFIED.value,
            AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT.value,
        )
    ]

    for row in successful_rows:
        if row.snapshot_ref is None or row.source_record_id is None:
            continue
        snap_ref = row.snapshot_ref.replace("\\", "/")
        snap_path = _REPO_ROOT / snap_ref
        try:
            product, provenance = ArchiveLabelSnapshotStore.load(snap_path)
        except Exception as exc:
            raise ValueError(
                f"Failed to load snapshot for source_record_id={row.source_record_id!r} "
                f"at {snap_path!r}: {exc}"
            ) from exc

        # Cross-check: source_record_id matches
        if product.source_record_id != row.source_record_id:
            raise ValueError(
                f"Snapshot product.source_record_id {product.source_record_id!r} "
                f"!= ledger source_record_id {row.source_record_id!r}."
            )
        # Cross-check: provenance_id matches
        if row.provenance_id is not None and provenance.provenance_id != row.provenance_id:
            raise ValueError(
                f"Snapshot provenance_id {provenance.provenance_id!r} "
                f"!= ledger provenance_id {row.provenance_id!r} "
                f"for source_record_id={row.source_record_id!r}."
            )
        # Cross-check: snapshot_id matches
        if row.snapshot_id is not None:
            from backend.app.mission_sources.snapshots.archive_label_snapshot import (
                _compute_snapshot_id as _snap_id_fn,
                _canonical_retrieved_at as _canon_ret,
            )
            computed_snap_id = _snap_id_fn(
                product.source_standard.value,
                provenance.provenance_id,
                _canon_ret(provenance.retrieved_at),
            )
            if computed_snap_id != row.snapshot_id:
                raise ValueError(
                    f"Snapshot_id mismatch for {row.source_record_id!r}: "
                    f"computed={computed_snap_id!r} stored={row.snapshot_id!r}."
                )

        # Cross-check: observation_stop_utc — FAIL CLOSED (B4 Step 0 / B4.1 Defect C)
        # Rules:
        #   both absent      → allowed
        #   ledger present, snapshot absent  → FAIL
        #   ledger absent, snapshot present  → FAIL
        #   both present     → canonical UTC equality required
        ledger_stop = row.observation_stop_utc
        snapshot_stop = product.observation_stop_utc
        if ledger_stop is None and snapshot_stop is not None:
            raise ValueError(
                f"observation_stop_utc mismatch for {row.source_record_id!r}: "
                f"ledger=None but snapshot has stop={snapshot_stop.isoformat()!r}. "
                "Present/missing mismatch — fail closed."
            )
        if ledger_stop is not None and snapshot_stop is None:
            raise ValueError(
                f"observation_stop_utc mismatch for {row.source_record_id!r}: "
                f"ledger={ledger_stop!r} but snapshot has no stop time. "
                "Present/missing mismatch — fail closed."
            )
        if ledger_stop is not None and snapshot_stop is not None:
            stored_stop = _normalize_utc_iso(ledger_stop)
            product_stop = snapshot_stop.astimezone(timezone.utc).isoformat()
            if stored_stop != product_stop:
                raise ValueError(
                    f"observation_stop_utc mismatch for {row.source_record_id!r}: "
                    f"ledger={stored_stop!r} snapshot={product_stop!r}. "
                    "Source integrity check failed — fail closed."
                )

        # Cross-check: observation_start_utc where both sides provide it — FAIL CLOSED (B4 Step 0)
        if (
            hasattr(row, "observation_start_utc")
            and row.observation_start_utc is not None
            and product.observation_start_utc is not None
        ):
            stored_start = _normalize_utc_iso(row.observation_start_utc)
            product_start = product.observation_start_utc.astimezone(timezone.utc).isoformat()
            if stored_start != product_start:
                raise ValueError(
                    f"observation_start_utc mismatch for {row.source_record_id!r}: "
                    f"ledger={stored_start!r} snapshot={product_start!r}. "
                    "Source integrity check failed — fail closed."
                )

        snapshots_by_source_record_id[row.source_record_id] = (product, provenance)
        if row.snapshot_id:
            snapshots_by_snapshot_id[row.snapshot_id] = (product, provenance)

    # Require exactly 535 snapshots
    actual_snapshot_count = len(snapshots_by_source_record_id)
    if actual_snapshot_count != _EXPECTED_LABEL_SNAPSHOT_COUNT:
        raise ValueError(
            f"Expected {_EXPECTED_LABEL_SNAPSHOT_COUNT} label snapshots; "
            f"got {actual_snapshot_count}. "
            "Some snapshots failed to load."
        )

    # -----------------------------------------------------------------------
    # Step 6: Load temporal reconciliation
    # -----------------------------------------------------------------------
    reconciliation_path = (
        _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_temporal_reconciliation.json"
    )
    logger.info("Loading temporal reconciliation from %s", reconciliation_path)
    reconciliation = load_reconciliation_manifest(reconciliation_path)

    # Cross-check root binding
    if reconciliation.reconciliation_id != bundle.temporal_reconciliation_id:
        raise ValueError(
            f"Reconciliation reconciliation_id {reconciliation.reconciliation_id!r} "
            f"!= bundle.temporal_reconciliation_id {bundle.temporal_reconciliation_id!r}. "
            "Cross-artifact mutation detected."
        )
    if reconciliation.candidate_plan_id != plan.plan_id:
        raise ValueError(
            f"Reconciliation candidate_plan_id {reconciliation.candidate_plan_id!r} "
            f"!= plan.plan_id {plan.plan_id!r}."
        )
    if reconciliation.discovery_evidence_artifact_id != bundle.discovery_evidence_artifact_id:
        raise ValueError(
            f"Reconciliation discovery_evidence_artifact_id "
            f"{reconciliation.discovery_evidence_artifact_id!r} != "
            f"bundle.discovery_evidence_artifact_id {bundle.discovery_evidence_artifact_id!r}."
        )

    # Require counts
    if reconciliation.candidate_logical_count != _EXPECTED_CANDIDATE_LOGICAL:
        raise ValueError(
            f"Reconciliation candidate_logical_count {reconciliation.candidate_logical_count} "
            f"!= expected {_EXPECTED_CANDIDATE_LOGICAL}."
        )
    if reconciliation.candidate_source_count != _EXPECTED_CANDIDATE_SOURCE:
        raise ValueError(
            f"Reconciliation candidate_source_count {reconciliation.candidate_source_count} "
            f"!= expected {_EXPECTED_CANDIDATE_SOURCE}."
        )
    if reconciliation.eligible_logical_count != _EXPECTED_ELIGIBLE_LOGICAL:
        raise ValueError(
            f"Reconciliation eligible_logical_count {reconciliation.eligible_logical_count} "
            f"!= expected {_EXPECTED_ELIGIBLE_LOGICAL}."
        )
    if reconciliation.eligible_source_count != _EXPECTED_ELIGIBLE_SOURCE:
        raise ValueError(
            f"Reconciliation eligible_source_count {reconciliation.eligible_source_count} "
            f"!= expected {_EXPECTED_ELIGIBLE_SOURCE}."
        )
    if reconciliation.ineligible_logical_count != _EXPECTED_INELIGIBLE_LOGICAL:
        raise ValueError(
            f"Reconciliation ineligible_logical_count {reconciliation.ineligible_logical_count} "
            f"!= expected {_EXPECTED_INELIGIBLE_LOGICAL}."
        )
    if reconciliation.ineligible_source_count != _EXPECTED_INELIGIBLE_SOURCE:
        raise ValueError(
            f"Reconciliation ineligible_source_count {reconciliation.ineligible_source_count} "
            f"!= expected {_EXPECTED_INELIGIBLE_SOURCE}."
        )

    # Re-derive temporal eligibility from verified snapshots
    for entry in reconciliation.entries:
        stored_class = entry.classification.value  # e.g. "ELIGIBLE"
        stop_utc = entry.authoritative_observation_stop_utc
        recomputed = _recompute_eligibility(stop_utc)
        stored_normalized = stored_class
        if stored_class == "ELIGIBLE" and recomputed != "ELIGIBLE":
            raise RuntimeError(
                f"SOURCE_GRAPH_CONTRADICTION: reconciliation entry "
                f"{entry.logical_product_id!r} is stored as ELIGIBLE but "
                f"re-derived classification is {recomputed!r} "
                f"(stop={stop_utc!r})."
            )
        if stored_class.startswith("INELIGIBLE") and recomputed == "ELIGIBLE":
            raise RuntimeError(
                f"SOURCE_GRAPH_CONTRADICTION: reconciliation entry "
                f"{entry.logical_product_id!r} is stored as {stored_class!r} but "
                f"re-derived classification is ELIGIBLE "
                f"(stop={stop_utc!r})."
            )

        # Verify all source_record_ids resolve to loaded snapshots
        for srid in entry.source_record_ids:
            if srid not in snapshots_by_source_record_id:
                raise ValueError(
                    f"Reconciliation entry {entry.logical_product_id!r} "
                    f"references source_record_id {srid!r} not in loaded snapshots."
                )

        # Verify all provenance_ids resolve — FAIL CLOSED (B4 Step 0)
        for prov_id in entry.provenance_ids:
            found = False
            for srid in entry.source_record_ids:
                if srid in snapshots_by_source_record_id:
                    _, prov = snapshots_by_source_record_id[srid]
                    if prov.provenance_id == prov_id:
                        found = True
                        break
            if not found:
                raise ValueError(
                    f"Reconciliation entry {entry.logical_product_id!r}: "
                    f"provenance_id {prov_id!r} does not resolve to any loaded snapshot "
                    "for this logical product. "
                    "Source provenance trust boundary violated — fail closed."
                )

    # -----------------------------------------------------------------------
    # Step 7: Load verified inventory
    # -----------------------------------------------------------------------
    inventory_path = (
        _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_verified_inventory.json"
    )
    logger.info("Loading verified inventory from %s", inventory_path)
    builder = V2VerifiedInventoryBuilder()
    inventory = builder.load_manifest(inventory_path)

    # Cross-check manifest_id
    if inventory.manifest_id != bundle.verified_inventory_manifest_id:
        raise ValueError(
            f"Inventory manifest_id {inventory.manifest_id!r} != "
            f"bundle.verified_inventory_manifest_id {bundle.verified_inventory_manifest_id!r}. "
            "Cross-artifact mutation detected."
        )

    # Require 403 logical entries, 527 source records
    if len(inventory.entries) != _EXPECTED_ELIGIBLE_LOGICAL:
        raise ValueError(
            f"Inventory has {len(inventory.entries)} entries; expected {_EXPECTED_ELIGIBLE_LOGICAL}."
        )
    inventory_source_count = sum(len(e.representation_record_ids) for e in inventory.entries)
    if inventory_source_count != _EXPECTED_ELIGIBLE_SOURCE:
        raise ValueError(
            f"Inventory has {inventory_source_count} source records; "
            f"expected {_EXPECTED_ELIGIBLE_SOURCE}."
        )

    # Exact set equality: inventory logical IDs == eligible reconciliation logical IDs
    eligible_recon_ids = {
        e.logical_product_id
        for e in reconciliation.entries
        if e.classification == ReconciliationClassification.ELIGIBLE
    }
    inventory_logical_ids = {e.logical_product_id for e in inventory.entries}
    if inventory_logical_ids != eligible_recon_ids:
        extra = inventory_logical_ids - eligible_recon_ids
        missing = eligible_recon_ids - inventory_logical_ids
        raise ValueError(
            f"Inventory logical ID set != eligible reconciliation set. "
            f"Extra in inventory: {sorted(extra)[:5]!r}. "
            f"Missing from inventory: {sorted(missing)[:5]!r}."
        )

    # Exact set equality: inventory source_record_ids == eligible reconciliation source_record_ids
    eligible_recon_srids = set()
    for e in reconciliation.entries:
        if e.classification == ReconciliationClassification.ELIGIBLE:
            eligible_recon_srids.update(e.source_record_ids)
    inventory_srids = set()
    for e in inventory.entries:
        inventory_srids.update(e.representation_record_ids)
    if inventory_srids != eligible_recon_srids:
        extra = inventory_srids - eligible_recon_srids
        missing = eligible_recon_srids - inventory_srids
        raise ValueError(
            f"Inventory source_record_id set != eligible reconciliation source_record_id set. "
            f"Extra in inventory: {sorted(extra)[:5]!r}. "
            f"Missing from inventory: {sorted(missing)[:5]!r}."
        )

    # Require ineligible source records are ABSENT from inventory
    ineligible_srids = set()
    for e in reconciliation.entries:
        if e.classification != ReconciliationClassification.ELIGIBLE:
            ineligible_srids.update(e.source_record_ids)
    contamination = ineligible_srids & inventory_srids
    if contamination:
        raise ValueError(
            f"Ineligible source_record_ids found in verified inventory: "
            f"{sorted(contamination)[:5]!r}. Fail closed."
        )

    # -----------------------------------------------------------------------
    # Step 8: Load Horizons snapshot
    # -----------------------------------------------------------------------
    horizons_ref = bundle.horizons_snapshot_ref
    if horizons_ref is None:
        raise ValueError("Source bundle has no horizons_snapshot_ref.")
    horizons_path = _REPO_ROOT / horizons_ref.replace("\\", "/")
    logger.info("Loading Horizons snapshot from %s", horizons_path)
    horizons_result = HorizonsSnapshotStore.load(horizons_path)

    # Cross-check snapshot_id
    from backend.app.mission_sources.snapshots.horizons_snapshot import _compute_snapshot_id as _h_snap_id
    from backend.app.mission_sources.snapshots.horizons_snapshot import _canonical_retrieved_at as _h_canon_ret
    computed_horizons_snap_id = _h_snap_id(
        horizons_result.provenance.provenance_id,
        _h_canon_ret(horizons_result.provenance.retrieved_at),
    )
    if computed_horizons_snap_id != bundle.horizons_snapshot_id:
        raise ValueError(
            f"Horizons snapshot_id (computed={computed_horizons_snap_id!r}) "
            f"!= bundle.horizons_snapshot_id ({bundle.horizons_snapshot_id!r})."
        )

    # Require exact target/center
    geo = horizons_result.geometry
    if geo.target_spk_id != _EXPECTED_HORIZONS_TARGET:
        raise ValueError(
            f"Horizons target_spk_id {geo.target_spk_id!r} "
            f"!= expected {_EXPECTED_HORIZONS_TARGET!r}."
        )
    if geo.center != _EXPECTED_HORIZONS_CENTER:
        raise ValueError(
            f"Horizons center {geo.center!r} != expected {_EXPECTED_HORIZONS_CENTER!r}."
        )

    # Require Horizons epoch == decision epoch
    # geo.epoch_utc is a datetime object; normalize both to UTC for comparison
    geo_epoch_dt = geo.epoch_utc
    if geo_epoch_dt.tzinfo is None:
        geo_epoch_dt = geo_epoch_dt.replace(tzinfo=timezone.utc)
    else:
        geo_epoch_dt = geo_epoch_dt.astimezone(timezone.utc)
    if geo_epoch_dt != _DECISION_EPOCH_UTC:
        raise ValueError(
            f"Horizons epoch {geo_epoch_dt.isoformat()!r} != decision epoch "
            f"{_DECISION_EPOCH_UTC.isoformat()!r}. "
            "Horizons snapshot must be at the decision epoch."
        )

    logger.info(
        "Source graph loaded and verified: %d logical / %d source eligible, "
        "%d ineligible, %d snapshots, Horizons range_km=%.3f",
        _EXPECTED_ELIGIBLE_LOGICAL, _EXPECTED_ELIGIBLE_SOURCE,
        _EXPECTED_INELIGIBLE_LOGICAL, actual_snapshot_count, geo.range_km,
    )

    return VerifiedV2SourceGraph(
        source_bundle=bundle,
        candidate_plan=plan,
        discovery_sidecar=sidecar,
        acquisition_ledger=ledger,
        temporal_reconciliation=reconciliation,
        verified_inventory=inventory,
        horizons_result=horizons_result,
        snapshots_by_source_record_id=snapshots_by_source_record_id,
        candidate_logical_count=_EXPECTED_CANDIDATE_LOGICAL,
        candidate_source_count=_EXPECTED_CANDIDATE_SOURCE,
        eligible_logical_count=_EXPECTED_ELIGIBLE_LOGICAL,
        eligible_source_count=_EXPECTED_ELIGIBLE_SOURCE,
        ineligible_logical_count=_EXPECTED_INELIGIBLE_LOGICAL,
        ineligible_source_count=_EXPECTED_INELIGIBLE_SOURCE,
        label_snapshot_count=actual_snapshot_count,
        horizons_range_km=geo.range_km,
        horizons_epoch_utc=geo_epoch_dt.isoformat(),
    )
