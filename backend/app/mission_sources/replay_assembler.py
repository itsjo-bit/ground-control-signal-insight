"""GCSI Phase 6E-C5 — Historical Replay Assembler.

Cross-source semantic validation and deterministic Scenario/ProvenanceManifest
construction from verified offline snapshot artifacts.

Architecture separation
-----------------------
``ReplayAssembler`` is PURE.  It must NOT:

- Open files
- Load snapshots
- Call HTTP
- Access sockets
- Read environment variables
- Read current time
- Call TelecomEngine, scheduler, evaluator, or AI

All inputs are pre-validated domain objects supplied by
:class:`~backend.app.mission_sources.historical_provider.HistoricalReplayProvider`.

Provenance design
-----------------
The manifest contains:

1. Three external VALIDATED EXTERNAL_AUTHORITATIVE source records
   (Horizons, IRDR, GRDR) — retained exactly from the snapshot stores.
2. One MODELED record for the GCSI replay policy.
3. A set of DERIVED records for each derivation step, with deterministic IDs.
4. FieldProvenanceBindings covering every leaf value in the assembled Scenario.

All derived and modeled record IDs are deterministic SHA-256 hashes of
domain-separated content.  No UUID, no datetime.now(), no random.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import NamedTuple

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
from .replay_descriptor import (
    HistoricalReplayDescriptorV1,
    replay_risk_level_from_score,
)

# We import these lazily to avoid circular deps — but since they are type-only
# references in the signature we can use TYPE_CHECKING.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .adapters.horizons_models import HorizonsGeometryResult
    from .adapters.pds_models import PdsScienceProduct

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_JUNO_SPK_ID: str = "-61"
_EARTH_CENTER: str = "500@399"
_HORIZONS_SOURCE_SYSTEM: str = "NASA/JPL Horizons API"
_PDS_SOURCE_SYSTEM: str = "NASA Planetary Data System Atmospheres Node"
_MODELED_SOURCE_SYSTEM: str = "GCSI-historical-replay-policy"

# Expected MWR context LIDs (exact).
_MWR_INSTRUMENT_LIDS = ("urn:nasa:pds:context:instrument:mwr.jno",)
_MWR_INSTRUMENT_HOST_LIDS = ("urn:nasa:pds:context:instrument_host:spacecraft.jno",)
_MWR_INVESTIGATION_LIDS = ("urn:nasa:pds:context:investigation:mission.juno",)
_MWR_TARGET_LIDS = ("urn:nasa:pds:context:target:planet.jupiter",)

# Expected MWR product class and processing level.
_PRODUCT_CLASS = "Product_Observational"
_PROCESSING_LEVEL = "Calibrated"

# LIDVID role parser — strict role-aware grammar.
# Expected form:
#   urn:nasa:pds:juno_mwr:data_calibrated:
#   mwr<PJ2>r<role><ts13>_<record>_<lv>::<pds_version>
_MWR_LIDVID_RE = re.compile(
    r"^urn:nasa:pds:juno_mwr:data_calibrated:"
    r"mwr(?P<pj>[0-9]{2})r(?P<role>[ig])"
    r"(?P<timestamp>[0-9]{13})"
    r"_(?P<record>r[0-9]{5})"
    r"_(?P<local_version>v[0-9]{2})"
    r"::(?P<pds_version>[A-Za-z0-9._-]+)$"
)

# Frozen C4B derivation method identifiers.
_DM_DECISION_EPOCH = "historical_replay_decision_epoch_from_mwr_stop_v1"
_DM_DISTANCE = "historical_replay_distance_from_exact_horizons_range_v1"
_DM_SIZE_BITS = "historical_replay_product_size_bits_from_pds_file_size_v1"
_DM_PRODUCT_ID = "historical_replay_product_id_from_mwr_role_v1"
_DM_MISSION_ID = "historical_replay_mission_id_from_juno_context_v1"
_DM_AGE = "historical_replay_product_age_from_decision_epoch_v1"
_DM_RISK_LEVEL = "historical_replay_risk_level_from_policy_score_v1"
_DM_PAIR_RELATIONSHIP = "historical_replay_product_relationship_from_mwr_pair_v1"
# C5 narrow addition.
_DM_PRODUCT_METADATA = "historical_replay_product_metadata_from_mwr_identity_v1"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_domain(domain_prefix: str, payload: str) -> str:
    """Return a deterministic 64-char hex SHA-256 of (domain_prefix + payload)."""
    raw = (domain_prefix + payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _derived_id(derivation_method: str, parent_ids: tuple[str, ...]) -> str:
    """Compute a deterministic derived provenance record ID.

    Formula:
        SHA-256(
            "gcsi:historical_replay_derived:v1:"
            + derivation_method
            + ":"
            + comma_join(sorted(parent_ids))
        )
    """
    parents_joined = ",".join(sorted(parent_ids))
    payload = f"gcsi:historical_replay_derived:v1:{derivation_method}:{parents_joined}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _modeled_policy_id(canonical_json: str) -> str:
    """Compute the deterministic modeled policy provenance record ID.

    Formula:
        SHA-256("gcsi:historical_replay_policy:v1:" + canonical_json)
    """
    payload = "gcsi:historical_replay_policy:v1:" + canonical_json
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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


# ---------------------------------------------------------------------------
# MWR LIDVID parse result
# ---------------------------------------------------------------------------


class _MWRLidvidParts(NamedTuple):
    pj: str
    role: str
    timestamp: str
    record: str
    local_version: str
    pds_version: str


def _parse_mwr_lidvid(lidvid: str, role_expected: str) -> _MWRLidvidParts:
    """Parse a MWR LIDVID and verify the expected role character.

    Args:
        lidvid: Full versioned PDS4 LIDVID.
        role_expected: ``"i"`` for IRDR or ``"g"`` for GRDR.

    Returns:
        Parsed parts.

    Raises:
        MissionSourceValidationError: if the LIDVID does not match grammar or role.
    """
    m = _MWR_LIDVID_RE.match(lidvid)
    if m is None:
        raise MissionSourceValidationError(
            f"MWR LIDVID does not match expected grammar "
            f"(role={role_expected!r})."
        )
    role = m.group("role")
    if role != role_expected:
        raise MissionSourceValidationError(
            f"MWR LIDVID has wrong role character; expected {role_expected!r}, "
            f"got {role!r}."
        )
    return _MWRLidvidParts(
        pj=m.group("pj"),
        role=role,
        timestamp=m.group("timestamp"),
        record=m.group("record"),
        local_version=m.group("local_version"),
        pds_version=m.group("pds_version"),
    )


# ---------------------------------------------------------------------------
# ReplayAssembler
# ---------------------------------------------------------------------------


class ReplayAssembler:
    """Pure assembler: validates cross-source semantics, constructs Scenario and
    ProvenanceManifest.

    This class performs ZERO IO.  It must not open files, call HTTP, read the
    filesystem, touch environment variables, or read the current time.
    """

    @staticmethod
    def assemble(
        *,
        descriptor: "HistoricalReplayDescriptorV1",
        horizons_result: "HorizonsGeometryResult",
        irdr_product: "PdsScienceProduct",
        irdr_provenance: ProvenanceRecord,
        grdr_product: "PdsScienceProduct",
        grdr_provenance: ProvenanceRecord,
    ) -> tuple[Scenario, ProvenanceManifest]:
        """Assemble a canonical Scenario and ProvenanceManifest.

        Parameters
        ----------
        descriptor:
            Fully validated descriptor (from load_historical_replay_descriptor).
        horizons_result:
            Verified HorizonsGeometryResult from HorizonsSnapshotStore.load().
        irdr_product:
            Verified IRDR PdsScienceProduct from PdsArchiveSnapshotStore.load().
        irdr_provenance:
            Corresponding IRDR provenance record.
        grdr_product:
            Verified GRDR PdsScienceProduct from PdsArchiveSnapshotStore.load().
        grdr_provenance:
            Corresponding GRDR provenance record.

        Returns
        -------
        (Scenario, ProvenanceManifest)
            Deterministically assembled canonical objects.

        Raises
        ------
        MissionSourceValidationError
            If any cross-source semantic validation check fails.
        """
        # ----------------------------------------------------------------
        # PART C: Source authority validation
        # ----------------------------------------------------------------
        ReplayAssembler._validate_horizons(horizons_result)
        irdr_parts, grdr_parts = ReplayAssembler._validate_mwr_pair(
            irdr_product, irdr_provenance, grdr_product, grdr_provenance
        )

        # ----------------------------------------------------------------
        # PART D: IRDR / GRDR family cross-binding
        # ----------------------------------------------------------------
        ReplayAssembler._validate_pair_identity(irdr_parts, grdr_parts)

        # ----------------------------------------------------------------
        # Derive decision epoch
        # ----------------------------------------------------------------
        decision_epoch = ReplayAssembler._derive_decision_epoch(
            irdr_product, grdr_product
        )

        # ----------------------------------------------------------------
        # Exact Horizons alignment
        # ----------------------------------------------------------------
        if horizons_result.geometry.epoch_utc != decision_epoch:
            raise MissionSourceValidationError(
                "Horizons geometry epoch does not exactly match the "
                "decision epoch (observation_stop_utc). "
                "No tolerance, no interpolation."
            )

        # ----------------------------------------------------------------
        # Descriptor ↔ source identity cross-binding
        # ----------------------------------------------------------------
        pj = irdr_parts.pj
        ts = irdr_parts.timestamp
        lv = irdr_parts.local_version  # e.g. "v04"

        expected_replay_id = (
            f"juno_pj{pj}_mwr_{ts}_{lv}_replay_v1"
        )
        if descriptor.replay_id != expected_replay_id:
            raise MissionSourceValidationError(
                f"Descriptor replay_id does not match the source family identity. "
                f"Expected {expected_replay_id!r}."
            )

        expected_policy_version = f"pj{pj}-mwr-v1"
        if descriptor.replay_policy_version != expected_policy_version:
            raise MissionSourceValidationError(
                f"Descriptor replay_policy_version does not match the PJ family. "
                f"Expected {expected_policy_version!r}."
            )

        # ----------------------------------------------------------------
        # PART E: Scenario construction
        # ----------------------------------------------------------------
        scenario_id = descriptor.replay_id
        mission_id = "JUNO"

        # Risk level
        risk_level_str = replay_risk_level_from_score(
            descriptor.mission_policy.risk_score
        )
        risk_level = RiskLevel(risk_level_str)

        # link_inputs — exactly the seven required keys
        lp = descriptor.link_policy
        link_inputs = {
            "timestamp": decision_epoch,
            "snr_db": lp.snr_db,
            "rssi_dbm": lp.rssi_dbm,
            "nominal_data_rate_bps": lp.nominal_data_rate_bps,
            "latency_s": lp.latency_s,
            "link_stability": lp.link_stability,
            "remaining_window_s": lp.remaining_window_s,
        }

        # PART F: Mission state
        mp = descriptor.mission_policy
        mission_state = MissionState(
            mission_id=mission_id,
            mission_phase=mp.mission_phase,
            current_event=mp.current_event,
            event_time_remaining_s=mp.event_time_remaining_s,
            comm_window_remaining_s=mp.comm_window_remaining_s,
            risk_score=mp.risk_score,
            risk_level=risk_level,
        )

        # PART G: Data products
        irdr_product_obj = ReplayAssembler._build_irdr(
            pj=pj, irdr_product=irdr_product,
            decision_epoch=decision_epoch,
            irdr_policy=descriptor.irdr_policy,
        )
        grdr_product_obj = ReplayAssembler._build_grdr(
            pj=pj, grdr_product=grdr_product,
            decision_epoch=decision_epoch,
            grdr_policy=descriptor.grdr_policy,
        )

        scenario = Scenario(
            scenario_id=scenario_id,
            simulated=True,
            distance_km=horizons_result.geometry.range_km,
            link_inputs=link_inputs,
            mission_state=mission_state,
            packets=[],          # frozen empty — PDS files ≠ historical packets
            data_products=[irdr_product_obj, grdr_product_obj],
            anomalies=[],        # no GCSI modeled anomaly
        )

        # ----------------------------------------------------------------
        # PART H–J: Provenance manifest
        # ----------------------------------------------------------------
        manifest = ReplayAssembler._build_manifest(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
            decision_epoch=decision_epoch,
            mission_id=mission_id,
            risk_level=risk_level,
            pj=pj,
            scenario=scenario,
        )

        return scenario, manifest

    # ----------------------------------------------------------------
    # Validation helpers
    # ----------------------------------------------------------------

    @staticmethod
    def _validate_horizons(
        horizons_result: "HorizonsGeometryResult",
    ) -> None:
        """Validate Horizons source authority contracts."""
        from .adapters.horizons_models import HorizonsGeometryResult  # noqa: F401
        geo = horizons_result.geometry
        req = horizons_result.request
        prov = horizons_result.provenance

        # SPK and center
        if geo.target_spk_id != _JUNO_SPK_ID:
            raise MissionSourceValidationError(
                f"Horizons geometry target_spk_id must be {_JUNO_SPK_ID!r}; "
                f"got {geo.target_spk_id!r}."
            )
        if geo.center != _EARTH_CENTER:
            raise MissionSourceValidationError(
                f"Horizons geometry center must be {_EARTH_CENTER!r}; "
                f"got {geo.center!r}."
            )
        if req.target_spk_id != _JUNO_SPK_ID:
            raise MissionSourceValidationError(
                f"Horizons request target_spk_id must be {_JUNO_SPK_ID!r}."
            )
        if geo.target_spk_id != req.target_spk_id:
            raise MissionSourceValidationError(
                "Horizons request/geometry target_spk_id mismatch."
            )
        if geo.epoch_utc != req.epoch_utc:
            raise MissionSourceValidationError(
                "Horizons request/geometry epoch_utc mismatch."
            )

        # Provenance kind and status
        if prov.kind.value != "external_authoritative":
            raise MissionSourceValidationError(
                "Horizons provenance kind must be EXTERNAL_AUTHORITATIVE."
            )
        if prov.validation_status.value != "validated":
            raise MissionSourceValidationError(
                "Horizons provenance validation_status must be VALIDATED."
            )
        if prov.source_system != _HORIZONS_SOURCE_SYSTEM:
            raise MissionSourceValidationError(
                f"Horizons provenance source_system must be {_HORIZONS_SOURCE_SYSTEM!r}."
            )

        # observed_at == epoch_utc
        if prov.observed_at is None:
            raise MissionSourceValidationError(
                "Horizons provenance observed_at must be present."
            )
        if prov.observed_at != geo.epoch_utc:
            raise MissionSourceValidationError(
                "Horizons provenance observed_at does not match geometry epoch_utc."
            )

        # Physical constraints
        if not (geo.range_km > 0):
            raise MissionSourceValidationError(
                "Horizons range_km must be > 0."
            )
        if not (geo.one_way_light_time_s > 0):
            raise MissionSourceValidationError(
                "Horizons one_way_light_time_s must be > 0."
            )
        if not math.isfinite(geo.range_rate_km_s):
            raise MissionSourceValidationError(
                "Horizons range_rate_km_s must be finite."
            )

    @staticmethod
    def _validate_mwr_pair(
        irdr_product: "PdsScienceProduct",
        irdr_provenance: ProvenanceRecord,
        grdr_product: "PdsScienceProduct",
        grdr_provenance: ProvenanceRecord,
    ) -> tuple[_MWRLidvidParts, _MWRLidvidParts]:
        """Validate MWR product class, context, provenance, and parse LIDVIDs."""
        for label, product, provenance, role_char in (
            ("IRDR", irdr_product, irdr_provenance, "i"),
            ("GRDR", grdr_product, grdr_provenance, "g"),
        ):
            # Product class
            if product.product_class != _PRODUCT_CLASS:
                raise MissionSourceValidationError(
                    f"{label} product_class must be {_PRODUCT_CLASS!r}; "
                    f"got {product.product_class!r}."
                )
            # Processing level
            if product.processing_level != _PROCESSING_LEVEL:
                raise MissionSourceValidationError(
                    f"{label} processing_level must be {_PROCESSING_LEVEL!r}; "
                    f"got {product.processing_level!r}."
                )
            # Exactly one data file
            if len(product.data_files) != 1:
                raise MissionSourceValidationError(
                    f"{label} must have exactly 1 data file; "
                    f"got {len(product.data_files)}."
                )
            # total_data_size_bytes > 0
            if product.total_data_size_bytes <= 0:
                raise MissionSourceValidationError(
                    f"{label} total_data_size_bytes must be > 0."
                )
            # total == data_files[0].file_size_bytes
            if product.total_data_size_bytes != product.data_files[0].file_size_bytes:
                raise MissionSourceValidationError(
                    f"{label} total_data_size_bytes != data_files[0].file_size_bytes."
                )
            # Instrument context
            if product.instrument_lids != _MWR_INSTRUMENT_LIDS:
                raise MissionSourceValidationError(
                    f"{label} instrument_lids mismatch."
                )
            if product.instrument_host_lids != _MWR_INSTRUMENT_HOST_LIDS:
                raise MissionSourceValidationError(
                    f"{label} instrument_host_lids mismatch."
                )
            if product.investigation_lids != _MWR_INVESTIGATION_LIDS:
                raise MissionSourceValidationError(
                    f"{label} investigation_lids mismatch."
                )
            if product.target_lids != _MWR_TARGET_LIDS:
                raise MissionSourceValidationError(
                    f"{label} target_lids mismatch."
                )
            # Provenance kind and status
            if provenance.kind.value != "external_authoritative":
                raise MissionSourceValidationError(
                    f"{label} provenance kind must be EXTERNAL_AUTHORITATIVE."
                )
            if provenance.validation_status.value != "validated":
                raise MissionSourceValidationError(
                    f"{label} provenance validation_status must be VALIDATED."
                )
            if provenance.source_system != _PDS_SOURCE_SYSTEM:
                raise MissionSourceValidationError(
                    f"{label} provenance source_system must be {_PDS_SOURCE_SYSTEM!r}."
                )
            # source_record_id == product.lidvid
            if provenance.source_record_id != product.lidvid:
                raise MissionSourceValidationError(
                    f"{label} provenance source_record_id does not match lidvid."
                )

        # Parse LIDVIDs
        irdr_parts = _parse_mwr_lidvid(irdr_product.lidvid, "i")
        grdr_parts = _parse_mwr_lidvid(grdr_product.lidvid, "g")
        return irdr_parts, grdr_parts

    @staticmethod
    def _validate_pair_identity(
        irdr_parts: _MWRLidvidParts,
        grdr_parts: _MWRLidvidParts,
    ) -> None:
        """Require exact equality of PJ, timestamp, record, local_version, pds_version."""
        for attr in ("pj", "timestamp", "record", "local_version", "pds_version"):
            iv = getattr(irdr_parts, attr)
            gv = getattr(grdr_parts, attr)
            if iv != gv:
                raise MissionSourceValidationError(
                    f"IRDR/GRDR pair mismatch in {attr}: IRDR={iv!r}, GRDR={gv!r}."
                )

    @staticmethod
    def _derive_decision_epoch(
        irdr_product: "PdsScienceProduct",
        grdr_product: "PdsScienceProduct",
    ) -> datetime:
        """Derive and validate the decision epoch from observation stops.

        Raises:
            MissionSourceValidationError: if stops are None, mismatched, or
                start > stop.
        """
        irdr_stop = irdr_product.observation_stop_utc
        grdr_stop = grdr_product.observation_stop_utc
        irdr_start = irdr_product.observation_start_utc
        grdr_start = grdr_product.observation_start_utc

        if irdr_stop is None or grdr_stop is None:
            raise MissionSourceValidationError(
                "IRDR and GRDR observation_stop_utc must both be present."
            )
        if irdr_stop != grdr_stop:
            raise MissionSourceValidationError(
                "IRDR and GRDR observation_stop_utc must be exactly equal."
            )
        if irdr_start is None or grdr_start is None:
            raise MissionSourceValidationError(
                "IRDR and GRDR observation_start_utc must both be present."
            )
        if irdr_start != grdr_start:
            raise MissionSourceValidationError(
                "IRDR and GRDR observation_start_utc must be exactly equal."
            )
        if irdr_start > irdr_stop:
            raise MissionSourceValidationError(
                "Observation start must be <= stop."
            )
        return irdr_stop

    # ----------------------------------------------------------------
    # DataProduct builders
    # ----------------------------------------------------------------

    @staticmethod
    def _build_irdr(
        *,
        pj: str,
        irdr_product: "PdsScienceProduct",
        decision_epoch: datetime,
        irdr_policy: object,
    ) -> DataProduct:
        """Build the IRDR DataProduct."""
        product_id = f"JUNO-MWR-PJ{pj}-IRDR"
        experiment_id = f"JUNO-MWR-PJ{pj}"
        grdr_id = f"JUNO-MWR-PJ{pj}-GRDR"
        size_bits = irdr_product.data_files[0].file_size_bytes * 8
        stop = irdr_product.observation_stop_utc
        age_s = (decision_epoch - stop).total_seconds()
        if age_s < 0.0:
            raise MissionSourceValidationError(
                "IRDR age_s is negative (observation_stop_utc > decision_epoch)."
            )
        return DataProduct(
            product_id=product_id,
            product_type=irdr_policy.product_type,
            description=(
                f"Juno MWR PJ{pj} calibrated Instrument Reduced Data Record "
                "(primary radiometric science product)."
            ),
            subsystem="payload",
            size_bits=size_bits,
            criticality=irdr_policy.criticality,
            mission_relevance=irdr_policy.mission_relevance,
            scientific_value=irdr_policy.scientific_value,
            deadline_s=irdr_policy.deadline_s,
            age_s=age_s,
            anomaly_id=irdr_policy.anomaly_id,
            experiment_id=experiment_id,
            related_ids=[grdr_id],
            delivery_requirement=irdr_policy.delivery_requirement,
            retry_cost=irdr_policy.retry_cost,
        )

    @staticmethod
    def _build_grdr(
        *,
        pj: str,
        grdr_product: "PdsScienceProduct",
        decision_epoch: datetime,
        grdr_policy: object,
    ) -> DataProduct:
        """Build the GRDR DataProduct."""
        product_id = f"JUNO-MWR-PJ{pj}-GRDR"
        experiment_id = f"JUNO-MWR-PJ{pj}"
        irdr_id = f"JUNO-MWR-PJ{pj}-IRDR"
        size_bits = grdr_product.data_files[0].file_size_bytes * 8
        stop = grdr_product.observation_stop_utc
        age_s = (decision_epoch - stop).total_seconds()
        if age_s < 0.0:
            raise MissionSourceValidationError(
                "GRDR age_s is negative (observation_stop_utc > decision_epoch)."
            )
        return DataProduct(
            product_id=product_id,
            product_type=grdr_policy.product_type,
            description=(
                f"Juno MWR PJ{pj} calibrated Geometry Reduced Data Record "
                "(companion geometry and ancillary product)."
            ),
            subsystem="payload",
            size_bits=size_bits,
            criticality=grdr_policy.criticality,
            mission_relevance=grdr_policy.mission_relevance,
            scientific_value=grdr_policy.scientific_value,
            deadline_s=grdr_policy.deadline_s,
            age_s=age_s,
            anomaly_id=grdr_policy.anomaly_id,
            experiment_id=experiment_id,
            related_ids=[irdr_id],
            delivery_requirement=grdr_policy.delivery_requirement,
            retry_cost=grdr_policy.retry_cost,
        )

    # ----------------------------------------------------------------
    # Provenance manifest builder
    # ----------------------------------------------------------------

    @staticmethod
    def _build_manifest(
        *,
        descriptor: "HistoricalReplayDescriptorV1",
        horizons_result: "HorizonsGeometryResult",
        irdr_product: "PdsScienceProduct",
        irdr_provenance: ProvenanceRecord,
        grdr_product: "PdsScienceProduct",
        grdr_provenance: ProvenanceRecord,
        decision_epoch: datetime,
        mission_id: str,
        risk_level: RiskLevel,
        pj: str,
        scenario: Scenario,
    ) -> ProvenanceManifest:
        """Build the full ProvenanceManifest."""
        # ---- Source IDs (from external records) ----
        horizons_id = horizons_result.provenance.provenance_id
        irdr_id = irdr_provenance.provenance_id
        grdr_id = grdr_provenance.provenance_id

        # ---- Modeled policy record ----
        modeled_record, modeled_id = ReplayAssembler._build_modeled_record(
            descriptor=descriptor,
        )

        # ---- Derived records ----
        rec_decision_epoch_id = _derived_id(
            _DM_DECISION_EPOCH, (irdr_id, grdr_id, modeled_id)
        )
        rec_distance_id = _derived_id(
            _DM_DISTANCE, (horizons_id,)
        )
        rec_mission_id_id = _derived_id(
            _DM_MISSION_ID, (irdr_id, grdr_id)
        )
        rec_risk_level_id = _derived_id(
            _DM_RISK_LEVEL, (modeled_id,)
        )
        rec_irdr_size_id = _derived_id(
            _DM_SIZE_BITS, (irdr_id,)
        )
        rec_grdr_size_id = _derived_id(
            _DM_SIZE_BITS, (grdr_id,)
        )
        rec_irdr_product_id_id = _derived_id(
            _DM_PRODUCT_ID, (irdr_id,)
        )
        rec_grdr_product_id_id = _derived_id(
            _DM_PRODUCT_ID, (grdr_id,)
        )
        rec_irdr_age_id = _derived_id(
            _DM_AGE, (rec_decision_epoch_id, irdr_id)
        )
        rec_grdr_age_id = _derived_id(
            _DM_AGE, (rec_decision_epoch_id, grdr_id)
        )
        rec_pair_rel_id = _derived_id(
            _DM_PAIR_RELATIONSHIP, (irdr_id, grdr_id)
        )
        rec_irdr_meta_id = _derived_id(
            _DM_PRODUCT_METADATA, (irdr_id,)
        )
        rec_grdr_meta_id = _derived_id(
            _DM_PRODUCT_METADATA, (grdr_id,)
        )

        # Build derived ProvenanceRecord objects
        def _drec(
            pid: str,
            method: str,
            parents: tuple[str, ...],
            notes: str | None = None,
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

        rec_decision_epoch = _drec(
            rec_decision_epoch_id,
            _DM_DECISION_EPOCH,
            (irdr_id, grdr_id, modeled_id),
            "Decision epoch = IRDR/GRDR observation_stop_utc "
            "(mwr_observation_stop policy).",
        )
        rec_distance = _drec(
            rec_distance_id,
            _DM_DISTANCE,
            (horizons_id,),
            "distance_km derived from Horizons exact range_km.",
        )
        rec_mission_id = _drec(
            rec_mission_id_id,
            _DM_MISSION_ID,
            (irdr_id, grdr_id),
            "mission_id=JUNO derived from validated Juno MWR product context.",
        )
        rec_risk_level = _drec(
            rec_risk_level_id,
            _DM_RISK_LEVEL,
            (modeled_id,),
            "risk_level derived from modeled risk_score via gcsi_risk_thresholds_v1.",
        )
        rec_irdr_size = _drec(
            rec_irdr_size_id,
            _DM_SIZE_BITS,
            (irdr_id,),
            "IRDR size_bits = file_size_bytes × 8.",
        )
        rec_grdr_size = _drec(
            rec_grdr_size_id,
            _DM_SIZE_BITS,
            (grdr_id,),
            "GRDR size_bits = file_size_bytes × 8.",
        )
        rec_irdr_product_id = _drec(
            rec_irdr_product_id_id,
            _DM_PRODUCT_ID,
            (irdr_id,),
            "IRDR product_id derived from MWR role 'i' and PJ.",
        )
        rec_grdr_product_id = _drec(
            rec_grdr_product_id_id,
            _DM_PRODUCT_ID,
            (grdr_id,),
            "GRDR product_id derived from MWR role 'g' and PJ.",
        )
        rec_irdr_age = _drec(
            rec_irdr_age_id,
            _DM_AGE,
            (rec_decision_epoch_id, irdr_id),
            "IRDR age_s = decision_epoch - observation_stop_utc.",
        )
        rec_grdr_age = _drec(
            rec_grdr_age_id,
            _DM_AGE,
            (rec_decision_epoch_id, grdr_id),
            "GRDR age_s = decision_epoch - observation_stop_utc.",
        )
        rec_pair_rel = _drec(
            rec_pair_rel_id,
            _DM_PAIR_RELATIONSHIP,
            (irdr_id, grdr_id),
            "IRDR/GRDR pair relationship and experiment_id derived from MWR context.",
        )
        rec_irdr_meta = _drec(
            rec_irdr_meta_id,
            _DM_PRODUCT_METADATA,
            (irdr_id,),
            "IRDR human-readable metadata (description, subsystem) derived from MWR identity.",
        )
        rec_grdr_meta = _drec(
            rec_grdr_meta_id,
            _DM_PRODUCT_METADATA,
            (grdr_id,),
            "GRDR human-readable metadata (description, subsystem) derived from MWR identity.",
        )

        all_records: tuple[ProvenanceRecord, ...] = (
            # External source records (in order: Horizons, IRDR, GRDR)
            horizons_result.provenance,
            irdr_provenance,
            grdr_provenance,
            # Modeled policy record
            modeled_record,
            # Derived records
            rec_decision_epoch,
            rec_distance,
            rec_mission_id,
            rec_risk_level,
            rec_irdr_size,
            rec_grdr_size,
            rec_irdr_product_id,
            rec_grdr_product_id,
            rec_irdr_age,
            rec_grdr_age,
            rec_pair_rel,
            rec_irdr_meta,
            rec_grdr_meta,
        )

        # ---- Field provenance bindings ----
        sid = scenario.scenario_id
        mid = mission_id
        irdr_pid = f"JUNO-MWR-PJ{pj}-IRDR"
        grdr_pid = f"JUNO-MWR-PJ{pj}-GRDR"

        bindings: list[FieldProvenanceBinding] = []

        # Scenario top-level
        for fp in ("scenario_id", "simulated"):
            bindings.append(_binding("scenario", sid, fp, modeled_id))
        bindings.append(_binding("scenario", sid, "link_inputs", modeled_id))
        bindings.append(_binding("scenario", sid, "mission_state", modeled_id))
        bindings.append(_binding("scenario", sid, "packets", modeled_id))
        bindings.append(_binding("scenario", sid, "data_products", modeled_id))
        bindings.append(_binding("scenario", sid, "anomalies", modeled_id))
        bindings.append(_binding("scenario", sid, "distance_km", rec_distance_id))

        # link_inputs leaf keys
        bindings.append(
            _binding("scenario", sid, "link_inputs.timestamp", rec_decision_epoch_id)
        )
        for k in ("snr_db", "rssi_dbm", "nominal_data_rate_bps",
                  "latency_s", "link_stability", "remaining_window_s"):
            bindings.append(_binding("scenario", sid, f"link_inputs.{k}", modeled_id))

        # MissionState fields
        bindings.append(_binding("mission_state", mid, "mission_id", rec_mission_id_id))
        for fp in ("mission_phase", "current_event", "event_time_remaining_s",
                   "comm_window_remaining_s", "risk_score"):
            bindings.append(_binding("mission_state", mid, fp, modeled_id))
        bindings.append(_binding("mission_state", mid, "risk_level", rec_risk_level_id))

        # IRDR DataProduct fields
        bindings.append(_binding("data_product", irdr_pid, "product_id", rec_irdr_product_id_id))
        for fp in ("product_type", "criticality", "mission_relevance", "scientific_value",
                   "deadline_s", "anomaly_id", "delivery_requirement", "retry_cost"):
            bindings.append(_binding("data_product", irdr_pid, fp, modeled_id))
        bindings.append(_binding("data_product", irdr_pid, "size_bits", rec_irdr_size_id))
        bindings.append(_binding("data_product", irdr_pid, "age_s", rec_irdr_age_id))
        bindings.append(_binding("data_product", irdr_pid, "experiment_id", rec_pair_rel_id))
        bindings.append(_binding("data_product", irdr_pid, "related_ids", rec_pair_rel_id))
        bindings.append(_binding("data_product", irdr_pid, "description", rec_irdr_meta_id))
        bindings.append(_binding("data_product", irdr_pid, "subsystem", rec_irdr_meta_id))

        # GRDR DataProduct fields
        bindings.append(_binding("data_product", grdr_pid, "product_id", rec_grdr_product_id_id))
        for fp in ("product_type", "criticality", "mission_relevance", "scientific_value",
                   "deadline_s", "anomaly_id", "delivery_requirement", "retry_cost"):
            bindings.append(_binding("data_product", grdr_pid, fp, modeled_id))
        bindings.append(_binding("data_product", grdr_pid, "size_bits", rec_grdr_size_id))
        bindings.append(_binding("data_product", grdr_pid, "age_s", rec_grdr_age_id))
        bindings.append(_binding("data_product", grdr_pid, "experiment_id", rec_pair_rel_id))
        bindings.append(_binding("data_product", grdr_pid, "related_ids", rec_pair_rel_id))
        bindings.append(_binding("data_product", grdr_pid, "description", rec_grdr_meta_id))
        bindings.append(_binding("data_product", grdr_pid, "subsystem", rec_grdr_meta_id))

        manifest = ProvenanceManifest(
            records=all_records,
            bindings=tuple(bindings),
        )
        return manifest

    @staticmethod
    def _build_modeled_record(
        *,
        descriptor: "HistoricalReplayDescriptorV1",
    ) -> tuple[ProvenanceRecord, str]:
        """Build the MODELED replay-policy provenance record.

        Returns (record, provenance_id).
        """
        lp = descriptor.link_policy
        mp = descriptor.mission_policy

        # Canonical policy payload — replay policy only, no authoritative facts.
        policy_payload = {
            "replay_policy_version": descriptor.replay_policy_version,
            "simulated": descriptor.simulated,
            "decision_epoch_policy": descriptor.decision_epoch_policy,
            "geometry_alignment_policy": descriptor.geometry_alignment_policy,
            "product_availability_policy": descriptor.product_availability_policy,
            "risk_level_policy": descriptor.risk_level_policy,
            "link_policy": {
                "snr_db": lp.snr_db,
                "rssi_dbm": lp.rssi_dbm,
                "nominal_data_rate_bps": lp.nominal_data_rate_bps,
                "latency_s": lp.latency_s,
                "link_stability": lp.link_stability,
                "remaining_window_s": lp.remaining_window_s,
            },
            "mission_policy": {
                "mission_phase": mp.mission_phase,
                "current_event": mp.current_event,
                "event_time_remaining_s": mp.event_time_remaining_s,
                "comm_window_remaining_s": mp.comm_window_remaining_s,
                "risk_score": mp.risk_score,
            },
            "irdr_policy": {
                "product_type": descriptor.irdr_policy.product_type,
                "criticality": descriptor.irdr_policy.criticality,
                "mission_relevance": descriptor.irdr_policy.mission_relevance,
                "scientific_value": descriptor.irdr_policy.scientific_value,
                "deadline_s": descriptor.irdr_policy.deadline_s,
                "delivery_requirement": descriptor.irdr_policy.delivery_requirement,
                "retry_cost": descriptor.irdr_policy.retry_cost,
                "anomaly_id": descriptor.irdr_policy.anomaly_id,
            },
            "grdr_policy": {
                "product_type": descriptor.grdr_policy.product_type,
                "criticality": descriptor.grdr_policy.criticality,
                "mission_relevance": descriptor.grdr_policy.mission_relevance,
                "scientific_value": descriptor.grdr_policy.scientific_value,
                "deadline_s": descriptor.grdr_policy.deadline_s,
                "delivery_requirement": descriptor.grdr_policy.delivery_requirement,
                "retry_cost": descriptor.grdr_policy.retry_cost,
                "anomaly_id": descriptor.grdr_policy.anomaly_id,
            },
        }

        canonical_json = json.dumps(
            policy_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        provenance_id = _modeled_policy_id(canonical_json)

        record = ProvenanceRecord(
            provenance_id=provenance_id,
            kind=ProvenanceKind.MODELED,
            source_system=_MODELED_SOURCE_SYSTEM,
            source_version=descriptor.replay_policy_version,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            # All timestamp fields must be None — no datetime.now()
            observed_at=None,
            retrieved_at=None,
            normalized_at=None,
            notes=f"GCSI replay policy canonical JSON payload for {descriptor.replay_id}.",
        )

        return record, provenance_id
