"""GCSI Phase 6F-B3 — Historical Replay V2 Assembler.

Deterministic assembler that transforms the frozen verified source graph
+ descriptor into a 403-product GCSI Scenario + ProvenanceManifest +
MissionSourceBundle.

Architecture separation
-----------------------
ReplayAssemblerV2 is PURE.  It must NOT:
- open files
- load snapshots
- call HTTP
- access sockets
- read environment variables
- read current time
- call TelecomEngine, scheduler, evaluator, or AI provider
- modify V1 replay infrastructure

All inputs are pre-validated domain objects.

Provenance design
-----------------
The manifest contains:
1. Per-product EXTERNAL_AUTHORITATIVE source records (from snapshot provenances)
2. DERIVED records for age, product_id, logical identity
3. MODELED records for size proxy, queue membership, policy scores, deadlines
4. Shared policy records (one per policy, reused across products)
5. Geometry record from Horizons snapshot
6. FieldProvenanceBindings for every leaf value in the assembled Scenario

Determinism
-----------
Two assembler runs against unchanged frozen artifacts produce semantically
identical Scenario, DataProducts, provenance records, field bindings,
and MissionSourceBundle metadata.

No uuid.uuid4(), no datetime.now(), no random, no unordered-set serialization.
All IDs are deterministic SHA-256 hashes of canonical semantic content.

Size fallback
-------------
For logical products with no exact archive size, uses:
    statistics.median_low(eligible SIZE_METADATA_EXACT archive_total_size_bytes)

This is a MODELED relay burden proxy. Provenance = MODELED.

Age derivation
--------------
For each logical product:
    age_s = decision_epoch - authoritative observation_stop
    age_s >= 0 enforced
    provenance kind = DERIVED

Geometry
--------
Scenario.distance_km = exact committed Horizons range_km at decision epoch.
latency_s (link input) is SEPARATE — protocol-stack overhead.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..models.data_product import DataProduct
from ..models.mission_state import MissionState
from ..models.risk_level import RiskLevel
from ..models.scenario import Scenario
from ..provenance.models import (
    FieldProvenanceBinding,
    ProvenanceKind,
    ProvenanceManifest,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)
from .errors import MissionSourceValidationError
from .models import MissionSourceBundle, MissionSourceMode
from .replay_descriptor import replay_risk_level_from_score
from .v2_replay_descriptor import HistoricalReplayV2Descriptor
from .v2_replay_policy import (
    PJ62_V2_MODELED_LINK_INPUTS,
    PJ62_V2_MODELED_MISSION_STATE,
    resolve_semantic_role,
)
from .v2_source_graph import VerifiedV2SourceGraph

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------

_PROVIDER_NAME = "GCSI-HistoricalReplayV2Provider"
_MODELED_SOURCE_SYSTEM = "GCSI-historical-replay-v2-policy"
_EXTERNAL_SOURCE_SYSTEM = "NASA Planetary Data System"
_HORIZONS_SOURCE_SYSTEM = "NASA/JPL Horizons API"

# Frozen expected decision epoch — used only as a replay assertion constant, NOT
# as the operative age-derivation value.  The operative epoch comes from the
# validated descriptor (see assemble()).
_FROZEN_DECISION_EPOCH_UTC = datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Derivation method identifiers (frozen B3)
# ---------------------------------------------------------------------------

_DM_AGE = "historical_replay_v2_product_age_from_decision_epoch_v1"
_DM_SIZE_EXACT = "historical_replay_v2_size_bits_from_exact_archive_size_v1"
_DM_SIZE_FALLBACK = "historical_replay_v2_size_bits_from_median_low_fallback_v1"
_DM_PRODUCT_ID = "historical_replay_v2_product_id_from_logical_id_v1"
_DM_DISTANCE = "historical_replay_v2_distance_from_horizons_range_v1"
_DM_MISSION_ID = "historical_replay_v2_mission_id_from_juno_context_v1"
_DM_RISK_LEVEL = "historical_replay_v2_risk_level_from_policy_score_v1"
_DM_QUEUE = "historical_replay_v2_queue_membership_from_modeled_policy_v1"
_DM_METADATA = "historical_replay_v2_product_metadata_from_archive_identity_v1"


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _derived_id(derivation_method: str, parent_ids: tuple[str, ...]) -> str:
    parents_joined = ",".join(sorted(parent_ids))
    payload = f"gcsi:historical_replay_v2_derived:v1:{derivation_method}:{parents_joined}"
    return _sha256_hex(payload)


def _modeled_id(canonical_json: str) -> str:
    payload = "gcsi:historical_replay_v2_policy:v1:" + canonical_json
    return _sha256_hex(payload)


def _binding(
    entity_type: str,
    entity_id: str,
    field_path: str,
    provenance_id: str,
) -> FieldProvenanceBinding:
    return FieldProvenanceBinding(
        entity_type=entity_type,
        entity_id=entity_id,
        field_path=field_path,
        provenance_id=provenance_id,
    )


def _drec(
    pid: str,
    method: str,
    parents: tuple[str, ...],
    notes: Optional[str] = None,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=pid,
        kind=ProvenanceKind.DERIVED,
        source_system=_MODELED_SOURCE_SYSTEM,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        derivation_method=method,
        parent_provenance_ids=parents,
        notes=notes,
    )


def _mrec(
    pid: str,
    policy_version: str,
    notes: Optional[str] = None,
    parents: tuple[str, ...] = (),
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id=pid,
        kind=ProvenanceKind.MODELED,
        source_system=_MODELED_SOURCE_SYSTEM,
        source_version=policy_version,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        parent_provenance_ids=parents,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# ReplayAssemblerV2
# ---------------------------------------------------------------------------


class ReplayAssemblerV2:
    """Pure deterministic assembler for the V2 historical replay.

    Takes a verified source graph + descriptor and produces:
    - 403 DataProducts
    - Scenario
    - ProvenanceManifest
    - MissionSourceBundle

    Performs ZERO IO. No network. No file access. No current time.
    """

    @staticmethod
    def assemble(
        *,
        descriptor: HistoricalReplayV2Descriptor,
        source_graph: VerifiedV2SourceGraph,
        source_ref: Optional[str] = None,
    ) -> MissionSourceBundle:
        """Assemble a complete MissionSourceBundle from descriptor + source graph.

        Parameters
        ----------
        descriptor:
            Validated V2 descriptor.
        source_graph:
            Fully verified V2 source graph.
        source_ref:
            Caller-provided opaque source reference (descriptor path).
            If None, falls back to descriptor.source_bundle_ref (legacy).
            The provider MUST pass the original caller source_ref so that
            reset semantics work correctly.

        Returns
        -------
        MissionSourceBundle
            Ready for use; scenario.simulated=True, source_mode=HISTORICAL_REPLAY.

        Raises
        ------
        MissionSourceValidationError
            If any cross-verification fails.
        """
        # ---- Cross-bind descriptor with source graph ----
        if descriptor.source_bundle_id != source_graph.source_bundle.bundle_id:
            raise MissionSourceValidationError(
                f"Descriptor source_bundle_id {descriptor.source_bundle_id!r} "
                f"!= source graph bundle_id {source_graph.source_bundle.bundle_id!r}."
            )
        if descriptor.replay_id != source_graph.source_bundle.replay_id:
            raise MissionSourceValidationError(
                f"Descriptor replay_id {descriptor.replay_id!r} "
                f"!= source graph replay_id {source_graph.source_bundle.replay_id!r}."
            )

        # ---- Defect A fix: cross-bind and validate decision_epoch_utc ----
        # Step 1: Parse descriptor.decision_epoch_utc as a timezone-aware datetime.
        try:
            desc_epoch_dt = datetime.fromisoformat(descriptor.decision_epoch_utc)
        except (ValueError, TypeError) as exc:
            raise MissionSourceValidationError(
                "Descriptor decision_epoch_utc is not a valid ISO-8601 datetime."
            ) from exc
        if desc_epoch_dt.tzinfo is None:
            raise MissionSourceValidationError(
                "Descriptor decision_epoch_utc is timezone-naive; a UTC-aware timestamp is required."
            )
        desc_epoch_utc = desc_epoch_dt.astimezone(timezone.utc)

        # Step 2: Cross-check against source_bundle.decision_epoch_utc
        try:
            bundle_epoch_dt = datetime.fromisoformat(
                source_graph.source_bundle.decision_epoch_utc
            )
        except (ValueError, TypeError) as exc:
            raise MissionSourceValidationError(
                "Source bundle decision_epoch_utc is not a valid ISO-8601 datetime."
            ) from exc
        if bundle_epoch_dt.tzinfo is None:
            bundle_epoch_utc = bundle_epoch_dt.replace(tzinfo=timezone.utc)
        else:
            bundle_epoch_utc = bundle_epoch_dt.astimezone(timezone.utc)

        if desc_epoch_utc != bundle_epoch_utc:
            raise MissionSourceValidationError(
                f"Decision epoch mismatch: descriptor={desc_epoch_utc.isoformat()!r} "
                f"!= source_bundle={bundle_epoch_utc.isoformat()!r}."
            )

        # Step 3: Cross-check against source_graph.horizons_epoch_utc
        try:
            horizons_epoch_dt = datetime.fromisoformat(source_graph.horizons_epoch_utc)
        except (ValueError, TypeError) as exc:
            raise MissionSourceValidationError(
                "Source graph horizons_epoch_utc is not a valid ISO-8601 datetime."
            ) from exc
        if horizons_epoch_dt.tzinfo is None:
            horizons_epoch_utc = horizons_epoch_dt.replace(tzinfo=timezone.utc)
        else:
            horizons_epoch_utc = horizons_epoch_dt.astimezone(timezone.utc)

        if desc_epoch_utc != horizons_epoch_utc:
            raise MissionSourceValidationError(
                f"Decision epoch mismatch: descriptor={desc_epoch_utc.isoformat()!r} "
                f"!= horizons_epoch={horizons_epoch_utc.isoformat()!r}."
            )

        # The validated descriptor epoch is the operative age-derivation authority.
        decision_epoch_utc = desc_epoch_utc

        # ---- Cross-bind: queue count == source graph eligible logical count ----
        if descriptor.queue_membership_policy.eligible_logical_count != source_graph.eligible_logical_count:
            raise MissionSourceValidationError(
                f"Descriptor queue_membership_policy.eligible_logical_count "
                f"{descriptor.queue_membership_policy.eligible_logical_count} != "
                f"source_graph.eligible_logical_count {source_graph.eligible_logical_count}."
            )

        # ---- Build descriptor-based product policy lookup (Section 8 authority) ----
        # This is the canonical policy authority for runtime assembly.
        # Module-level constants may remain as default builder values but
        # runtime assembly MUST consume the descriptor's policy.
        descriptor_policy_by_role: dict[str, object] = {
            entry.semantic_role: entry
            for entry in descriptor.product_policy.entries
        }

        # ---- Compute size fallback from eligible exact-size evidence ----
        exact_bytes_pool: list[int] = []
        snapshots = source_graph.snapshots_by_source_record_id
        inventory = source_graph.verified_inventory

        from .archive_models import ArchiveDataFileSizeCertainty, ArchiveSourceStandard

        # Build index: source_record_id → snapshot (product, provenance)
        snap_idx: dict[str, tuple] = dict(snapshots)

        # Collect exact sizes from eligible source records
        eligible_srids: set[str] = set()
        for inv_entry in inventory.entries:
            eligible_srids.update(inv_entry.representation_record_ids)

        for srid in eligible_srids:
            if srid not in snap_idx:
                continue
            product, _ = snap_idx[srid]
            if product.total_data_size_bytes is not None and product.total_data_size_bytes > 0:
                # Check all files are SIZE_METADATA_EXACT
                all_exact = all(
                    f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                    for f in product.data_files
                )
                if all_exact:
                    exact_bytes_pool.append(product.total_data_size_bytes)

        if not exact_bytes_pool:
            raise MissionSourceValidationError(
                "No exact-size eligible source records found. "
                "Cannot compute size fallback."
            )

        fallback_bytes = statistics.median_low(exact_bytes_pool)
        fallback_size_bits = fallback_bytes * 8

        # ---- Collect exact-source provenance IDs for the evidence pool ----
        # (source_record_ids whose snapshots contributed to the exact_bytes_pool)
        exact_source_prov_ids: list[str] = []
        for srid in sorted(eligible_srids):  # sorted for determinism
            if srid not in snap_idx:
                continue
            product, prov = snap_idx[srid]
            if product.total_data_size_bytes is not None and product.total_data_size_bytes > 0:
                all_exact = all(
                    f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                    for f in product.data_files
                )
                if all_exact:
                    exact_source_prov_ids.append(prov.provenance_id)

        # ---- Build shared modeled policy provenance records ----
        # Policy record — covers all modeled policy fields (scores, deadline, queue, etc.)
        # decision_epoch_utc and decision_epoch_policy are included so the age-derivation
        # authority is semantically bound to the decision-epoch configuration.
        policy_payload = json.dumps({
            "deadline_s": descriptor.product_policy.deadline_s,
            "decision_epoch_policy": descriptor.decision_epoch_policy,
            "decision_epoch_utc": decision_epoch_utc.isoformat(),
            "delivery_requirement": descriptor.product_policy.delivery_requirement,
            "entries": [
                {
                    "criticality": e.criticality,
                    "mission_relevance": e.mission_relevance,
                    "retry_cost": e.retry_cost,
                    "scientific_value": e.scientific_value,
                    "semantic_role": e.semantic_role,
                }
                for e in sorted(descriptor.product_policy.entries, key=lambda x: x.semantic_role)
            ],
            "link_inputs": descriptor.modeled_link_inputs.model_dump(mode="json"),
            "mission_state": descriptor.modeled_mission_state.model_dump(mode="json"),
            "queue_membership_policy": descriptor.queue_membership_policy.policy_id,
            "replay_id": descriptor.replay_id,
            "simulated": descriptor.simulated,
            "size_policy_id": descriptor.size_policy_id,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

        policy_prov_id = _modeled_id(policy_payload)
        policy_prov_rec = _mrec(
            policy_prov_id,
            descriptor.product_policy.policy_id,
            f"GCSI modeled V2 replay policy for {descriptor.replay_id}.",
        )

        # ---- Size provenance lineage: shared evidence-pool DERIVED record ----
        # One reusable record that parents all exact-source provenances.
        # This avoids attaching N source parents to every fallback product.
        evidence_pool_parents = tuple(sorted(set(exact_source_prov_ids)))
        evidence_pool_payload = json.dumps({
            "derivation_method": _DM_SIZE_FALLBACK,
            "exact_source_count": len(evidence_pool_parents),
            "fallback_archive_proxy_bytes": fallback_bytes,
            "policy_id": descriptor.size_policy_id,
        }, sort_keys=True, separators=(",", ":"))
        evidence_pool_prov_id = _derived_id(_DM_SIZE_FALLBACK, evidence_pool_parents)
        evidence_pool_prov_rec = _drec(
            evidence_pool_prov_id,
            _DM_SIZE_FALLBACK,
            evidence_pool_parents,
            (
                f"PJ62 V2 replay-size proxy evidence pool: "
                f"{len(evidence_pool_parents)} exact eligible archive-size source provenances. "
                f"median_low = {fallback_bytes} bytes."
            ),
        )

        # ---- MODELED fallback record parents the evidence-pool record ----
        size_fallback_payload = json.dumps({
            "evidence_pool_prov_id": evidence_pool_prov_id,
            "fallback_archive_proxy_bytes": fallback_bytes,
            "policy_id": descriptor.size_policy_id,
            "rule": descriptor.size_policy.fallback_rule,
        }, sort_keys=True, separators=(",", ":"))
        size_fallback_prov_id = _modeled_id(size_fallback_payload)
        size_fallback_prov_rec = _mrec(
            size_fallback_prov_id,
            descriptor.size_policy_id,
            (
                f"PJ62 V2 replay-size proxy: median_low of exact eligible archive product sizes "
                f"({fallback_bytes} bytes) used when authoritative source labels expose no exact "
                f"payload size metadata."
            ),
            parents=(evidence_pool_prov_id,),
        )

        # Queue membership record
        queue_payload = json.dumps({
            "eligible_logical_count": descriptor.queue_membership_policy.eligible_logical_count,
            "policy_id": descriptor.queue_membership_policy.policy_id,
            "source_mode": "MODELED",
        }, sort_keys=True, separators=(",", ":"))
        queue_prov_id = _modeled_id(queue_payload)
        queue_prov_rec = _mrec(
            queue_prov_id,
            descriptor.queue_membership_policy.policy_id,
            "GCSI modeled queue membership: 403 eligible logical products selected by "
            "temporal reconciliation. NOT a historical NASA TX-queue claim.",
        )

        # Horizons geometry record
        horizons_geo = source_graph.horizons_result.geometry
        horizons_prov = source_graph.horizons_result.provenance
        horizons_prov_id = horizons_prov.provenance_id

        # Distance derived record
        distance_prov_id = _derived_id(_DM_DISTANCE, (horizons_prov_id,))
        distance_prov_rec = _drec(
            distance_prov_id,
            _DM_DISTANCE,
            (horizons_prov_id,),
            "distance_km derived from Horizons exact range_km at decision epoch.",
        )

        # Risk level derived record
        risk_level_prov_id = _derived_id(_DM_RISK_LEVEL, (policy_prov_id,))
        risk_level_prov_rec = _drec(
            risk_level_prov_id,
            _DM_RISK_LEVEL,
            (policy_prov_id,),
            "risk_level derived from modeled risk_score via gcsi_risk_thresholds_v1.",
        )

        # Mission ID derived record
        mission_id_prov_id = _derived_id(_DM_MISSION_ID, (policy_prov_id,))
        mission_id_prov_rec = _drec(
            mission_id_prov_id,
            _DM_MISSION_ID,
            (policy_prov_id,),
            f"mission_id={descriptor.modeled_mission_state.mission_id!r} "
            "from GCSI V2 replay policy context.",
        )

        # ---- Build MissionState ----
        ms_spec = descriptor.modeled_mission_state
        risk_level_str = replay_risk_level_from_score(ms_spec.risk_score)
        risk_level = RiskLevel(risk_level_str)

        mission_state = MissionState(
            mission_id=ms_spec.mission_id,
            mission_phase=ms_spec.mission_phase,
            current_event=ms_spec.current_event,
            event_time_remaining_s=ms_spec.event_time_remaining_s,
            comm_window_remaining_s=ms_spec.comm_window_remaining_s,
            risk_score=ms_spec.risk_score,
            risk_level=risk_level,
        )

        # ---- Build link_inputs ----
        li = descriptor.modeled_link_inputs
        link_inputs = {
            "timestamp": source_graph.horizons_epoch_utc,
            "snr_db": li.snr_db,
            "rssi_dbm": li.rssi_dbm,
            "nominal_data_rate_bps": li.nominal_data_rate_bps,
            "latency_s": li.latency_s,
            "link_stability": li.link_stability,
            "remaining_window_s": li.remaining_window_s,
        }

        # ---- Build index for efficient per-product access ----
        # logical_product_id → inventory entry
        inv_by_lid: dict[str, object] = {
            e.logical_product_id: e for e in inventory.entries
        }
        # logical_product_id → reconciliation entry
        recon_by_lid: dict[str, object] = {
            e.logical_product_id: e
            for e in source_graph.temporal_reconciliation.entries
        }
        # logical_product_id → plan entry
        plan_by_lid: dict[str, object] = {
            e.logical_product_id: e
            for e in source_graph.candidate_plan.logical_entries
        }

        # ---- Collect all source provenance records from snapshots ----
        source_prov_records: dict[str, ProvenanceRecord] = {}
        for srid in eligible_srids:
            if srid in snap_idx:
                _, prov = snap_idx[srid]
                if prov.provenance_id not in source_prov_records:
                    source_prov_records[prov.provenance_id] = prov

        # ---- Assemble DataProducts ----
        all_records: list[ProvenanceRecord] = [
            horizons_prov,
            policy_prov_rec,
            evidence_pool_prov_rec,   # DERIVED evidence-pool for fallback size lineage
            size_fallback_prov_rec,   # MODELED fallback, parents evidence_pool
            queue_prov_rec,
            distance_prov_rec,
            risk_level_prov_rec,
            mission_id_prov_rec,
        ]
        # Add all EXTERNAL_AUTHORITATIVE source provenance records
        for src_prov in sorted(source_prov_records.values(), key=lambda r: r.provenance_id):
            all_records.append(src_prov)
        bindings: list[FieldProvenanceBinding] = []
        data_products: list[DataProduct] = []

        # Stats tracking
        exact_proxy_count = 0
        fallback_count = 0

        # Process in sorted order for determinism
        sorted_inv_entries = sorted(inventory.entries, key=lambda e: e.logical_product_id)

        for inv_entry in sorted_inv_entries:
            lid = inv_entry.logical_product_id
            plan_entry = plan_by_lid.get(lid)
            recon_entry = recon_by_lid.get(lid)
            if plan_entry is None or recon_entry is None:
                raise MissionSourceValidationError(
                    f"Missing plan or reconciliation entry for logical_product_id={lid!r}."
                )

            # Instrument and semantic role
            instrument = plan_entry.instrument
            profile_id = plan_entry.representations[0].profile_id if plan_entry.representations else ""
            semantic_role = resolve_semantic_role(instrument, profile_id)
            # Use descriptor-based policy as the authoritative runtime source (Section 8)
            policy_entry = descriptor_policy_by_role.get(semantic_role)
            if policy_entry is None:
                raise MissionSourceValidationError(
                    f"No product policy entry in descriptor for semantic role {semantic_role!r} "
                    f"(logical_product_id={lid!r}). Descriptor product policy is incomplete."
                )

            # Source provenances for this logical product (collect from inv_entry)
            source_prov_ids = tuple(inv_entry.source_fact_provenance_ids)

            # Determine size_bits
            # Try to find exact size from eligible representations
            exact_sizes: list[int] = []
            for srid in inv_entry.representation_record_ids:
                if srid in snap_idx:
                    product, _ = snap_idx[srid]
                    if product.total_data_size_bytes is not None and product.total_data_size_bytes > 0:
                        all_exact = all(
                            f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                            for f in product.data_files
                        )
                        if all_exact:
                            exact_sizes.append(product.total_data_size_bytes)

            if exact_sizes:
                logical_archive_proxy_bytes = max(exact_sizes)
                size_bits = logical_archive_proxy_bytes * 8
                exact_proxy_count += 1
                # MODELED size proxy record.
                # parent_provenance_ids = source provenance IDs so the lineage is:
                #   PDS authoritative source provenance
                #       → MODELED replay-size proxy
                #       → DataProduct.size_bits
                # This satisfies the required provenance DAG (Section 9).
                size_prov_id_used = _modeled_id(json.dumps({
                    "exact_bytes": logical_archive_proxy_bytes,
                    "lid": lid,
                    "policy_id": descriptor.size_policy_id,
                    "rule": "max_exact",
                }, sort_keys=True, separators=(",", ":")))
                size_prov_rec_final = _mrec(
                    size_prov_id_used,
                    descriptor.size_policy_id,
                    f"PJ62 V2 exact-size proxy for {lid!r}: "
                    f"max(exact archive_total_size_bytes) = {logical_archive_proxy_bytes} bytes, "
                    f"size_bits = {size_bits}. "
                    "Archive byte count is EXTERNAL_AUTHORITATIVE; "
                    "interpretation as replay transmission burden is MODELED.",
                    parents=source_prov_ids,
                )
                all_records.append(size_prov_rec_final)
            else:
                size_bits = fallback_size_bits
                fallback_count += 1
                size_prov_id_used = size_fallback_prov_id

            # Age derivation
            # authoritative_observation_stop_utc from reconciliation entry
            auth_stop_str = recon_entry.authoritative_observation_stop_utc
            if auth_stop_str is None:
                raise MissionSourceValidationError(
                    f"No authoritative observation_stop for eligible product {lid!r}."
                )
            auth_stop = datetime.fromisoformat(auth_stop_str)
            if auth_stop.tzinfo is None:
                auth_stop = auth_stop.replace(tzinfo=timezone.utc)
            else:
                auth_stop = auth_stop.astimezone(timezone.utc)
            # Use validated descriptor decision epoch as the operative age-derivation authority.
            age_s_raw = (decision_epoch_utc - auth_stop).total_seconds()
            if age_s_raw < 0.0:
                raise MissionSourceValidationError(
                    f"Negative age_s for {lid!r}: "
                    f"decision_epoch={decision_epoch_utc.isoformat()!r} "
                    f"stop={auth_stop.isoformat()!r}."
                )
            age_s = age_s_raw

            # Age provenance
            if source_prov_ids:
                age_prov_id = _derived_id(_DM_AGE, source_prov_ids + (policy_prov_id,))
            else:
                age_prov_id = _derived_id(_DM_AGE, (lid, policy_prov_id))
            age_prov_rec = _drec(
                age_prov_id,
                _DM_AGE,
                source_prov_ids + (policy_prov_id,) if source_prov_ids else (policy_prov_id,),
                f"age_s = decision_epoch - authoritative_observation_stop = "
                f"{age_s:.3f}s for {lid!r}.",
            )
            all_records.append(age_prov_rec)

            # Product_id provenance
            if source_prov_ids:
                pid_prov_id = _derived_id(_DM_PRODUCT_ID, source_prov_ids)
            else:
                pid_prov_id = _derived_id(_DM_PRODUCT_ID, (lid,))
            pid_prov_rec = _drec(
                pid_prov_id,
                _DM_PRODUCT_ID,
                source_prov_ids,
                f"product_id={lid!r} derived from logical source grouping.",
            )
            all_records.append(pid_prov_rec)

            # Metadata provenance
            if source_prov_ids:
                meta_prov_id = _derived_id(_DM_METADATA, source_prov_ids)
            else:
                meta_prov_id = _derived_id(_DM_METADATA, (lid,))
            meta_prov_rec = _drec(
                meta_prov_id,
                _DM_METADATA,
                source_prov_ids,
                f"DataProduct description/subsystem for {lid!r} derived from archive identity.",
            )
            all_records.append(meta_prov_rec)

            # Build DataProduct
            dp = DataProduct(
                product_id=lid,
                product_type=semantic_role,
                description=(
                    f"Juno PJ62 {instrument} {semantic_role.replace('_', ' ')} product "
                    f"(logical id: {lid})."
                ),
                subsystem=instrument.lower(),
                size_bits=size_bits,
                criticality=policy_entry.criticality,
                mission_relevance=policy_entry.mission_relevance,
                scientific_value=policy_entry.scientific_value,
                deadline_s=descriptor.product_policy.deadline_s,
                age_s=age_s,
                anomaly_id=None,
                experiment_id=None,
                related_ids=[],
                delivery_requirement=descriptor.product_policy.delivery_requirement,
                retry_cost=policy_entry.retry_cost,
            )
            data_products.append(dp)

            # Field bindings for this DataProduct
            bindings.extend([
                _binding("data_product", lid, "product_id", pid_prov_id),
                _binding("data_product", lid, "product_type", policy_prov_id),
                _binding("data_product", lid, "description", meta_prov_id),
                _binding("data_product", lid, "subsystem", meta_prov_id),
                _binding("data_product", lid, "size_bits", size_prov_id_used),
                _binding("data_product", lid, "criticality", policy_prov_id),
                _binding("data_product", lid, "mission_relevance", policy_prov_id),
                _binding("data_product", lid, "scientific_value", policy_prov_id),
                _binding("data_product", lid, "deadline_s", policy_prov_id),
                _binding("data_product", lid, "age_s", age_prov_id),
                _binding("data_product", lid, "anomaly_id", policy_prov_id),
                _binding("data_product", lid, "experiment_id", policy_prov_id),
                _binding("data_product", lid, "related_ids", policy_prov_id),
                _binding("data_product", lid, "delivery_requirement", policy_prov_id),
                _binding("data_product", lid, "retry_cost", policy_prov_id),
            ])

        # Require exactly 403 products
        if len(data_products) != 403:
            raise MissionSourceValidationError(
                f"Assembled {len(data_products)} DataProducts; expected 403."
            )

        # ---- Build Scenario ----
        scenario_id = descriptor.replay_id
        scenario = Scenario(
            scenario_id=scenario_id,
            simulated=True,
            distance_km=horizons_geo.range_km,
            link_inputs=link_inputs,
            mission_state=mission_state,
            packets=[],
            data_products=data_products,
            anomalies=[],
        )

        # ---- Scenario-level field bindings ----
        sid = scenario_id
        mid = ms_spec.mission_id
        bindings.extend([
            _binding("scenario", sid, "scenario_id", policy_prov_id),
            _binding("scenario", sid, "simulated", policy_prov_id),
            _binding("scenario", sid, "distance_km", distance_prov_id),
            _binding("scenario", sid, "link_inputs", policy_prov_id),
            _binding("scenario", sid, "link_inputs.snr_db", policy_prov_id),
            _binding("scenario", sid, "link_inputs.rssi_dbm", policy_prov_id),
            _binding("scenario", sid, "link_inputs.nominal_data_rate_bps", policy_prov_id),
            _binding("scenario", sid, "link_inputs.latency_s", policy_prov_id),
            _binding("scenario", sid, "link_inputs.link_stability", policy_prov_id),
            _binding("scenario", sid, "link_inputs.remaining_window_s", policy_prov_id),
            _binding("scenario", sid, "link_inputs.timestamp", distance_prov_id),
            _binding("scenario", sid, "mission_state", policy_prov_id),
            _binding("scenario", sid, "packets", policy_prov_id),
            _binding("scenario", sid, "data_products", queue_prov_id),
            _binding("scenario", sid, "anomalies", policy_prov_id),
        ])

        bindings.extend([
            _binding("mission_state", mid, "mission_id", mission_id_prov_id),
            _binding("mission_state", mid, "mission_phase", policy_prov_id),
            _binding("mission_state", mid, "current_event", policy_prov_id),
            _binding("mission_state", mid, "event_time_remaining_s", policy_prov_id),
            _binding("mission_state", mid, "comm_window_remaining_s", policy_prov_id),
            _binding("mission_state", mid, "risk_score", policy_prov_id),
            _binding("mission_state", mid, "risk_level", risk_level_prov_id),
        ])

        # ---- Build ProvenanceManifest ----
        # De-duplicate records (keep first occurrence by provenance_id)
        seen_ids: set[str] = set()
        unique_records: list[ProvenanceRecord] = []
        for rec in all_records:
            if rec.provenance_id not in seen_ids:
                seen_ids.add(rec.provenance_id)
                unique_records.append(rec)

        manifest = ProvenanceManifest(
            records=tuple(unique_records),
            bindings=tuple(bindings),
        )

        # ---- Build MissionSourceBundle ----
        # Use caller-provided source_ref (descriptor path) so reset semantics work:
        #   active_source_ref → HistoricalReplayProvider.load(active_source_ref)
        # Fall back to source_bundle_ref only when no caller source_ref provided.
        final_source_ref = source_ref if source_ref is not None else descriptor.source_bundle_ref
        bundle = MissionSourceBundle(
            scenario=scenario,
            provenance=manifest,
            provider_name=_PROVIDER_NAME,
            source_mode=MissionSourceMode.HISTORICAL_REPLAY,
            source_ref=final_source_ref,
        )

        return bundle

    @staticmethod
    def get_size_stats(
        *,
        source_graph: VerifiedV2SourceGraph,
    ) -> dict:
        """Return size policy stats without assembling a full bundle.

        Useful for reporting fallback_archive_proxy_bytes, etc.
        """
        from .archive_models import ArchiveDataFileSizeCertainty

        snap_idx: dict[str, tuple] = dict(source_graph.snapshots_by_source_record_id)
        inventory = source_graph.verified_inventory

        eligible_srids: set[str] = set()
        for inv_entry in inventory.entries:
            eligible_srids.update(inv_entry.representation_record_ids)

        exact_bytes_pool: list[int] = []
        for srid in eligible_srids:
            if srid not in snap_idx:
                continue
            product, _ = snap_idx[srid]
            if product.total_data_size_bytes is not None and product.total_data_size_bytes > 0:
                all_exact = all(
                    f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                    for f in product.data_files
                )
                if all_exact:
                    exact_bytes_pool.append(product.total_data_size_bytes)

        fallback_bytes = statistics.median_low(exact_bytes_pool) if exact_bytes_pool else 0
        return {
            "eligible_exact_source_count": len(exact_bytes_pool),
            "eligible_unknown_source_count": len(eligible_srids) - len(exact_bytes_pool),
            "fallback_archive_proxy_bytes": fallback_bytes,
            "fallback_size_bits": fallback_bytes * 8,
        }
