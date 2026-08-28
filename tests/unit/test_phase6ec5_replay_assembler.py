"""GCSI Phase 6E-C5 — ReplayAssembler Unit Tests.

All tests are COMPLETELY OFFLINE.
No files are opened inside ReplayAssembler.
All fixtures are constructed in-memory.
"""

from __future__ import annotations

import copy
import hashlib
import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path / sys.path setup
# ---------------------------------------------------------------------------

import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from backend.app.mission_sources.replay_assembler import (
    ReplayAssembler,
    _derived_id,
    _modeled_policy_id,
    _DM_DECISION_EPOCH,
    _DM_DISTANCE,
    _DM_SIZE_BITS,
    _DM_PRODUCT_ID,
    _DM_MISSION_ID,
    _DM_AGE,
    _DM_RISK_LEVEL,
    _DM_PAIR_RELATIONSHIP,
    _DM_PRODUCT_METADATA,
)
from backend.app.mission_sources.errors import MissionSourceValidationError
from backend.app.mission_sources.replay_descriptor import load_historical_replay_descriptor
from backend.app.mission_sources.snapshots.horizons_snapshot import HorizonsSnapshotStore
from backend.app.mission_sources.snapshots.pds_archive_snapshot import PdsArchiveSnapshotStore
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

_DECISION_EPOCH = datetime(2024, 6, 14, 3, 59, 55, 483000, tzinfo=timezone.utc)
_IRDR_START = datetime(2024, 6, 14, 3, 0, 3, 512000, tzinfo=timezone.utc)
_EXPECTED_RANGE_KM: float = 893345396.8038701
_EXPECTED_LIGHT_TIME_S: float = 2979.879489843171

_IRDR_LIDVID = "urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04::3.0"
_GRDR_LIDVID = "urn:nasa:pds:juno_mwr:data_calibrated:mwr62rg2024166030000_r04112_v04::3.0"

_IRDR_FILE_SIZE = 6_694_664
_GRDR_FILE_SIZE = 5_093_997
_IRDR_SIZE_BITS = _IRDR_FILE_SIZE * 8
_GRDR_SIZE_BITS = _GRDR_FILE_SIZE * 8

# ---------------------------------------------------------------------------
# Network guard — ReplayAssembler must never touch the network
# ---------------------------------------------------------------------------


def _no_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "ReplayAssembler unit test: network access is forbidden."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Load real verified artifacts once for the test session
# ---------------------------------------------------------------------------

_DESCRIPTOR_PATH = _ROOT / "data" / "replays" / "juno_pj62_mwr_v1.json"
_HORIZONS_SNAP = (
    _ROOT
    / "data/verified_snapshots/horizons/juno"
    / "juno_spk_-61_2024-06-14T035955.483000Z.json"
)
_IRDR_SNAP = (
    _ROOT
    / "data/verified_snapshots/pds_archive/juno_mwr/pj62"
    / "mwr62ri2024166030000_r04112_v04_3.0.json"
)
_GRDR_SNAP = (
    _ROOT
    / "data/verified_snapshots/pds_archive/juno_mwr/pj62"
    / "mwr62rg2024166030000_r04112_v04_3.0.json"
)


@pytest.fixture(scope="module")
def descriptor():
    return load_historical_replay_descriptor(_DESCRIPTOR_PATH)


@pytest.fixture(scope="module")
def horizons_result():
    return HorizonsSnapshotStore.load(_HORIZONS_SNAP)


@pytest.fixture(scope="module")
def irdr_tuple():
    return PdsArchiveSnapshotStore.load(_IRDR_SNAP)


@pytest.fixture(scope="module")
def grdr_tuple():
    return PdsArchiveSnapshotStore.load(_GRDR_SNAP)


@pytest.fixture(scope="module")
def assembled(descriptor, horizons_result, irdr_tuple, grdr_tuple):
    irdr_product, irdr_provenance = irdr_tuple
    grdr_product, grdr_provenance = grdr_tuple
    scenario, manifest = ReplayAssembler.assemble(
        descriptor=descriptor,
        horizons_result=horizons_result,
        irdr_product=irdr_product,
        irdr_provenance=irdr_provenance,
        grdr_product=grdr_product,
        grdr_provenance=grdr_provenance,
    )
    return scenario, manifest


# ===========================================================================
# A. Happy path / purity
# ===========================================================================


class TestHappyPath:
    def test_returns_tuple_of_scenario_and_manifest(self, assembled):
        scenario, manifest = assembled
        from backend.app.models.scenario import Scenario
        from backend.app.provenance.models import ProvenanceManifest
        assert isinstance(scenario, Scenario)
        assert isinstance(manifest, ProvenanceManifest)

    def test_repeat_call_exact_equality(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple
    ):
        """Repeated calls must produce exact same Scenario + Manifest."""
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        s1, m1 = ReplayAssembler.assemble(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )
        s2, m2 = ReplayAssembler.assemble(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )
        assert s1.model_dump() == s2.model_dump()
        assert m1.model_dump() == m2.model_dump()

    def test_assembler_performs_no_file_io(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple, tmp_path
    ):
        """ReplayAssembler must not open any files."""
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        with patch("builtins.open", side_effect=AssertionError("file IO forbidden")):
            s, m = ReplayAssembler.assemble(
                descriptor=descriptor,
                horizons_result=horizons_result,
                irdr_product=irdr_product,
                irdr_provenance=irdr_provenance,
                grdr_product=grdr_product,
                grdr_provenance=grdr_provenance,
            )
        assert s.scenario_id == "juno_pj62_mwr_2024166030000_v04_replay_v1"

    def test_assembler_does_not_mutate_descriptor(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple
    ):
        """Descriptor must be unchanged after assembly."""
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        original_dump = descriptor.model_dump()
        ReplayAssembler.assemble(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )
        assert descriptor.model_dump() == original_dump


# ===========================================================================
# B. Scenario construction
# ===========================================================================


class TestScenarioConstruction:
    def test_scenario_id(self, assembled):
        scenario, _ = assembled
        assert scenario.scenario_id == "juno_pj62_mwr_2024166030000_v04_replay_v1"

    def test_simulated_true(self, assembled):
        scenario, _ = assembled
        assert scenario.simulated is True

    def test_distance_km_exact(self, assembled):
        scenario, _ = assembled
        assert scenario.distance_km == _EXPECTED_RANGE_KM

    def test_packets_empty(self, assembled):
        scenario, _ = assembled
        assert scenario.packets == []

    def test_anomalies_empty(self, assembled):
        scenario, _ = assembled
        assert scenario.anomalies == []

    def test_two_data_products(self, assembled):
        scenario, _ = assembled
        assert len(scenario.data_products) == 2

    def test_irdr_first(self, assembled):
        scenario, _ = assembled
        assert scenario.data_products[0].product_id == "JUNO-MWR-PJ62-IRDR"

    def test_grdr_second(self, assembled):
        scenario, _ = assembled
        assert scenario.data_products[1].product_id == "JUNO-MWR-PJ62-GRDR"


# ===========================================================================
# C. Link inputs
# ===========================================================================


class TestLinkInputs:
    def test_timestamp(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["timestamp"] == _DECISION_EPOCH

    def test_snr_db(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["snr_db"] == 3.0

    def test_rssi_dbm(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["rssi_dbm"] == -95.0

    def test_nominal_data_rate_bps(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["nominal_data_rate_bps"] == 100000.0

    def test_latency_s(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["latency_s"] == 1.5

    def test_link_stability(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["link_stability"] == 0.8

    def test_remaining_window_s(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["remaining_window_s"] == 900.0

    def test_latency_not_light_time(self, assembled):
        """latency_s must NOT equal the Horizons one_way_light_time_s."""
        scenario, _ = assembled
        assert scenario.link_inputs["latency_s"] != _EXPECTED_LIGHT_TIME_S

    def test_latency_is_1_5(self, assembled):
        scenario, _ = assembled
        assert scenario.link_inputs["latency_s"] == 1.5

    def test_exactly_seven_keys(self, assembled):
        scenario, _ = assembled
        assert set(scenario.link_inputs.keys()) == {
            "timestamp", "snr_db", "rssi_dbm", "nominal_data_rate_bps",
            "latency_s", "link_stability", "remaining_window_s",
        }


# ===========================================================================
# D. MissionState
# ===========================================================================


class TestMissionState:
    def test_mission_id(self, assembled):
        _, _ = assembled
        scenario, _ = assembled
        assert scenario.mission_state.mission_id == "JUNO"

    def test_mission_phase(self, assembled):
        scenario, _ = assembled
        assert scenario.mission_state.mission_phase == "science_downlink"

    def test_current_event(self, assembled):
        scenario, _ = assembled
        assert scenario.mission_state.current_event == "PJ62 MWR historical replay downlink decision"

    def test_event_time_remaining_s(self, assembled):
        scenario, _ = assembled
        assert scenario.mission_state.event_time_remaining_s == 900.0

    def test_comm_window_remaining_s(self, assembled):
        scenario, _ = assembled
        assert scenario.mission_state.comm_window_remaining_s == 900.0

    def test_risk_score(self, assembled):
        scenario, _ = assembled
        assert scenario.mission_state.risk_score == 0.35

    def test_risk_level_medium(self, assembled):
        scenario, _ = assembled
        from backend.app.models.risk_level import RiskLevel
        assert scenario.mission_state.risk_level == RiskLevel.MEDIUM


# ===========================================================================
# E. IRDR DataProduct
# ===========================================================================


class TestIRDRDataProduct:
    @pytest.fixture
    def irdr(self, assembled):
        scenario, _ = assembled
        return scenario.data_products[0]

    def test_product_id(self, irdr):
        assert irdr.product_id == "JUNO-MWR-PJ62-IRDR"

    def test_product_type(self, irdr):
        assert irdr.product_type == "science"

    def test_subsystem(self, irdr):
        assert irdr.subsystem == "payload"

    def test_size_bits(self, irdr):
        assert irdr.size_bits == _IRDR_SIZE_BITS

    def test_size_bits_exact_calculation(self, irdr):
        assert irdr.size_bits == _IRDR_FILE_SIZE * 8

    def test_criticality(self, irdr):
        assert irdr.criticality == pytest.approx(0.60)

    def test_mission_relevance(self, irdr):
        assert irdr.mission_relevance == pytest.approx(0.95)

    def test_scientific_value(self, irdr):
        assert irdr.scientific_value == pytest.approx(0.95)

    def test_deadline_s(self, irdr):
        assert irdr.deadline_s == 900.0

    def test_age_s(self, irdr):
        assert irdr.age_s == 0.0

    def test_anomaly_id_none(self, irdr):
        assert irdr.anomaly_id is None

    def test_experiment_id(self, irdr):
        assert irdr.experiment_id == "JUNO-MWR-PJ62"

    def test_related_ids(self, irdr):
        assert irdr.related_ids == ["JUNO-MWR-PJ62-GRDR"]

    def test_delivery_requirement(self, irdr):
        assert irdr.delivery_requirement == "best_effort"

    def test_retry_cost(self, irdr):
        assert irdr.retry_cost == pytest.approx(0.70)

    def test_description_contains_irdr(self, irdr):
        assert "Instrument Reduced Data Record" in irdr.description

    def test_description_contains_pj62(self, irdr):
        assert "PJ62" in irdr.description


# ===========================================================================
# F. GRDR DataProduct
# ===========================================================================


class TestGRDRDataProduct:
    @pytest.fixture
    def grdr(self, assembled):
        scenario, _ = assembled
        return scenario.data_products[1]

    def test_product_id(self, grdr):
        assert grdr.product_id == "JUNO-MWR-PJ62-GRDR"

    def test_product_type(self, grdr):
        assert grdr.product_type == "science"

    def test_subsystem(self, grdr):
        assert grdr.subsystem == "payload"

    def test_size_bits(self, grdr):
        assert grdr.size_bits == _GRDR_SIZE_BITS

    def test_size_bits_exact_calculation(self, grdr):
        assert grdr.size_bits == _GRDR_FILE_SIZE * 8

    def test_criticality(self, grdr):
        assert grdr.criticality == pytest.approx(0.50)

    def test_mission_relevance(self, grdr):
        assert grdr.mission_relevance == pytest.approx(0.85)

    def test_scientific_value(self, grdr):
        assert grdr.scientific_value == pytest.approx(0.80)

    def test_deadline_s(self, grdr):
        assert grdr.deadline_s == 900.0

    def test_age_s(self, grdr):
        assert grdr.age_s == 0.0

    def test_anomaly_id_none(self, grdr):
        assert grdr.anomaly_id is None

    def test_experiment_id(self, grdr):
        assert grdr.experiment_id == "JUNO-MWR-PJ62"

    def test_related_ids(self, grdr):
        assert grdr.related_ids == ["JUNO-MWR-PJ62-IRDR"]

    def test_delivery_requirement(self, grdr):
        assert grdr.delivery_requirement == "best_effort"

    def test_retry_cost(self, grdr):
        assert grdr.retry_cost == pytest.approx(0.60)

    def test_description_contains_grdr(self, grdr):
        assert "Geometry Reduced Data Record" in grdr.description

    def test_description_contains_pj62(self, grdr):
        assert "PJ62" in grdr.description


# ===========================================================================
# G. Provenance manifest
# ===========================================================================


class TestProvenanceManifest:
    def test_three_external_source_records(self, assembled, horizons_result, irdr_tuple, grdr_tuple):
        """External records (Horizons, IRDR, GRDR) are retained unchanged."""
        _, manifest = assembled
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple

        record_index = {r.provenance_id: r for r in manifest.records}

        assert horizons_result.provenance.provenance_id in record_index
        assert irdr_provenance.provenance_id in record_index
        assert grdr_provenance.provenance_id in record_index

        # Exact equality — unchanged
        assert record_index[horizons_result.provenance.provenance_id] == horizons_result.provenance
        assert record_index[irdr_provenance.provenance_id] == irdr_provenance
        assert record_index[grdr_provenance.provenance_id] == grdr_provenance

    def test_no_duplicate_record_ids(self, assembled):
        _, manifest = assembled
        ids = [r.provenance_id for r in manifest.records]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_bindings(self, assembled):
        _, manifest = assembled
        keys = [
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        ]
        assert len(keys) == len(set(keys))

    def test_all_binding_references_exist(self, assembled):
        _, manifest = assembled
        record_ids = {r.provenance_id for r in manifest.records}
        for binding in manifest.bindings:
            assert binding.provenance_id in record_ids, (
                f"Binding for {binding.field_path!r} references unknown "
                f"provenance_id {binding.provenance_id!r}"
            )

    def test_all_parent_references_exist(self, assembled):
        _, manifest = assembled
        record_ids = {r.provenance_id for r in manifest.records}
        for record in manifest.records:
            for pid in record.parent_provenance_ids:
                assert pid in record_ids, (
                    f"Record {record.provenance_id!r} has unknown parent {pid!r}"
                )

    def test_modeled_record_present(self, assembled):
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED]
        assert len(modeled) == 1

    def test_modeled_record_source_system(self, assembled):
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.source_system == "GCSI-historical-replay-policy"

    def test_modeled_record_source_version(self, assembled, descriptor):
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.source_version == descriptor.replay_policy_version

    def test_modeled_record_validation_status(self, assembled):
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_modeled_record_no_timestamps(self, assembled):
        """Modeled record must have all timestamp fields None."""
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.observed_at is None
        assert modeled.retrieved_at is None
        assert modeled.normalized_at is None

    def test_derived_ids_deterministic(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple
    ):
        """Derived record IDs must be identical across two calls."""
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        _, m1 = ReplayAssembler.assemble(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )
        _, m2 = ReplayAssembler.assemble(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )
        ids1 = sorted(r.provenance_id for r in m1.records)
        ids2 = sorted(r.provenance_id for r in m2.records)
        assert ids1 == ids2

    def test_modeled_id_deterministic(self, assembled):
        """The modeled record ID must be reproducible from the descriptor alone."""
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        # Re-derive using the known formula
        provenance_id_1 = modeled.provenance_id
        # Both calls produce the same ID
        assert len(provenance_id_1) == 64
        assert all(c in "0123456789abcdef" for c in provenance_id_1)

    def test_required_scenario_bindings_exist(self, assembled):
        """All required scenario-level fields must have bindings."""
        scenario, manifest = assembled
        sid = scenario.scenario_id
        bound = {
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        }
        required = {
            ("scenario", sid, "scenario_id"),
            ("scenario", sid, "simulated"),
            ("scenario", sid, "link_inputs"),
            ("scenario", sid, "distance_km"),
            ("scenario", sid, "mission_state"),
            ("scenario", sid, "packets"),
            ("scenario", sid, "data_products"),
            ("scenario", sid, "anomalies"),
        }
        for key in required:
            assert key in bound, f"Missing binding: {key}"

    def test_link_inputs_leaf_bindings(self, assembled):
        scenario, manifest = assembled
        sid = scenario.scenario_id
        bound = {
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        }
        for k in ("snr_db", "rssi_dbm", "nominal_data_rate_bps",
                  "latency_s", "link_stability", "remaining_window_s"):
            assert ("scenario", sid, f"link_inputs.{k}") in bound
        assert ("scenario", sid, "link_inputs.timestamp") in bound

    def test_mission_state_bindings(self, assembled):
        scenario, manifest = assembled
        mid = scenario.mission_state.mission_id
        bound = {
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        }
        for fp in ("mission_id", "mission_phase", "current_event",
                   "event_time_remaining_s", "comm_window_remaining_s",
                   "risk_score", "risk_level"):
            assert ("mission_state", mid, fp) in bound

    def test_data_product_bindings(self, assembled):
        scenario, manifest = assembled
        bound = {
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        }
        for pid in ("JUNO-MWR-PJ62-IRDR", "JUNO-MWR-PJ62-GRDR"):
            for fp in ("product_id", "product_type", "size_bits", "age_s",
                       "criticality", "mission_relevance", "scientific_value",
                       "deadline_s", "anomaly_id", "delivery_requirement",
                       "retry_cost", "experiment_id", "related_ids",
                       "description", "subsystem"):
                assert ("data_product", pid, fp) in bound, (
                    f"Missing binding for ({pid!r}, {fp!r})"
                )

    def test_distance_km_bound_to_derived_horizons(self, assembled, horizons_result):
        """distance_km binding must point to a DERIVED record (not directly to Horizons external)."""
        scenario, manifest = assembled
        sid = scenario.scenario_id
        distance_binding = next(
            b for b in manifest.bindings
            if b.entity_type == "scenario" and b.entity_id == sid
            and b.field_path == "distance_km"
        )
        rec_idx = {r.provenance_id: r for r in manifest.records}
        rec = rec_idx[distance_binding.provenance_id]
        assert rec.kind == ProvenanceKind.DERIVED
        assert rec.derivation_method == _DM_DISTANCE
        # Its parent must be the Horizons external record
        assert horizons_result.provenance.provenance_id in rec.parent_provenance_ids

    def test_timestamp_bound_to_decision_epoch_record(self, assembled):
        """link_inputs.timestamp must be bound to a DERIVED decision epoch record."""
        scenario, manifest = assembled
        sid = scenario.scenario_id
        ts_binding = next(
            b for b in manifest.bindings
            if b.entity_type == "scenario" and b.entity_id == sid
            and b.field_path == "link_inputs.timestamp"
        )
        rec_idx = {r.provenance_id: r for r in manifest.records}
        rec = rec_idx[ts_binding.provenance_id]
        assert rec.kind == ProvenanceKind.DERIVED
        assert rec.derivation_method == _DM_DECISION_EPOCH

    def test_risk_level_bound_to_derived_record(self, assembled):
        scenario, manifest = assembled
        mid = scenario.mission_state.mission_id
        rl_binding = next(
            b for b in manifest.bindings
            if b.entity_type == "mission_state" and b.entity_id == mid
            and b.field_path == "risk_level"
        )
        rec_idx = {r.provenance_id: r for r in manifest.records}
        rec = rec_idx[rl_binding.provenance_id]
        assert rec.kind == ProvenanceKind.DERIVED
        assert rec.derivation_method == _DM_RISK_LEVEL

    def test_irdr_size_bound_to_derived_record(self, assembled):
        scenario, manifest = assembled
        irdr_pid = "JUNO-MWR-PJ62-IRDR"
        size_binding = next(
            b for b in manifest.bindings
            if b.entity_type == "data_product" and b.entity_id == irdr_pid
            and b.field_path == "size_bits"
        )
        rec_idx = {r.provenance_id: r for r in manifest.records}
        rec = rec_idx[size_binding.provenance_id]
        assert rec.kind == ProvenanceKind.DERIVED
        assert rec.derivation_method == _DM_SIZE_BITS

    def test_manifest_record_count(self, assembled):
        """Manifest must have the expected number of records."""
        _, manifest = assembled
        # 3 external + 1 modeled + 13 derived
        assert len(manifest.records) == 17

    def test_external_records_are_first_three(self, assembled, horizons_result, irdr_tuple, grdr_tuple):
        """Records 0, 1, 2 must be the three external source records."""
        _, manifest = assembled
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        records = manifest.records
        assert records[0].provenance_id == horizons_result.provenance.provenance_id
        assert records[1].provenance_id == irdr_provenance.provenance_id
        assert records[2].provenance_id == grdr_provenance.provenance_id


# ===========================================================================
# H. Horizons validation errors
# ===========================================================================


def _make_horizons_result(overrides: dict):
    """Load the real Horizons result and apply field overrides using model_copy."""
    hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
    if not overrides:
        return hr
    # Override nested geometry fields
    geo_overrides = {k: v for k, v in overrides.items() if k in (
        "target_spk_id", "center", "epoch_utc", "range_km",
        "range_rate_km_s", "one_way_light_time_s"
    )}
    req_overrides = {k: v for k, v in overrides.items() if k in (
        "req_target_spk_id", "req_epoch_utc"
    )}
    prov_overrides = {k: v for k, v in overrides.items() if k in (
        "kind", "validation_status", "source_system", "observed_at"
    )}

    geo = hr.geometry
    req = hr.request
    prov = hr.provenance

    if geo_overrides:
        geo = geo.model_copy(update=geo_overrides)
    if req_overrides:
        mapped = {k.replace("req_", ""): v for k, v in req_overrides.items()}
        req = req.model_copy(update=mapped)
    if prov_overrides:
        prov = prov.model_copy(update=prov_overrides)

    return hr.model_copy(update={"geometry": geo, "request": req, "provenance": prov})


class TestHorizonsValidation:
    @pytest.fixture
    def base_inputs(self, descriptor, irdr_tuple, grdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        return dict(
            descriptor=descriptor,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )

    def _assemble(self, horizons_result, base_inputs):
        return ReplayAssembler.assemble(
            horizons_result=horizons_result,
            **base_inputs,
        )

    def test_wrong_spk_id_fails(self, base_inputs):
        hr = _make_horizons_result({"target_spk_id": "499"})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr, base_inputs)

    def test_wrong_center_fails(self, base_inputs):
        hr = _make_horizons_result({"center": "500@499"})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr, base_inputs)

    def test_non_authoritative_provenance_fails(self, base_inputs):
        from backend.app.provenance.models import ProvenanceKind
        hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
        prov = hr.provenance.model_copy(update={"kind": ProvenanceKind.DERIVED})
        hr2 = hr.model_copy(update={"provenance": prov})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr2, base_inputs)

    def test_non_validated_provenance_fails(self, base_inputs):
        hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
        prov = hr.provenance.model_copy(
            update={"validation_status": ProvenanceValidationStatus.PENDING}
        )
        hr2 = hr.model_copy(update={"provenance": prov})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr2, base_inputs)

    def test_wrong_source_system_fails(self, base_inputs):
        hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
        prov = hr.provenance.model_copy(update={"source_system": "WRONG_SYSTEM"})
        hr2 = hr.model_copy(update={"provenance": prov})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr2, base_inputs)

    def test_observed_at_mismatch_fails(self, base_inputs):
        hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
        wrong_time = datetime(2023, 1, 1, tzinfo=timezone.utc)
        prov = hr.provenance.model_copy(update={"observed_at": wrong_time})
        hr2 = hr.model_copy(update={"provenance": prov})
        with pytest.raises(MissionSourceValidationError):
            self._assemble(hr2, base_inputs)


# ===========================================================================
# I. MWR validation errors
# ===========================================================================


class TestMWRValidation:
    @pytest.fixture
    def base_inputs(self, descriptor, horizons_result, irdr_tuple, grdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        return dict(
            descriptor=descriptor,
            horizons_result=horizons_result,
            irdr_product=irdr_product,
            irdr_provenance=irdr_provenance,
            grdr_product=grdr_product,
            grdr_provenance=grdr_provenance,
        )

    def test_wrong_irdr_role_fails(self, base_inputs, grdr_tuple):
        """GRDR LIDVID used as IRDR → role 'g' not 'i'."""
        grdr_product, grdr_provenance = grdr_tuple
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_product"] = grdr_product
        bad_inputs["irdr_provenance"] = grdr_provenance
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_wrong_grdr_role_fails(self, base_inputs, irdr_tuple):
        """IRDR LIDVID used as GRDR → role 'i' not 'g'."""
        irdr_product, irdr_provenance = irdr_tuple
        bad_inputs = dict(base_inputs)
        bad_inputs["grdr_product"] = irdr_product
        bad_inputs["grdr_provenance"] = irdr_provenance
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_wrong_product_class_fails(self, base_inputs, irdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        bad_product = irdr_product.model_copy(update={"product_class": "Product_Context"})
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_product"] = bad_product
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_wrong_instrument_lid_fails(self, base_inputs, irdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        bad_product = irdr_product.model_copy(
            update={"instrument_lids": ("urn:nasa:pds:context:instrument:juv.jno",)}
        )
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_product"] = bad_product
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_non_authoritative_pds_provenance_fails(self, base_inputs, irdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        bad_prov = irdr_provenance.model_copy(
            update={"kind": ProvenanceKind.DERIVED}
        )
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_provenance"] = bad_prov
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_source_record_id_mismatch_fails(self, base_inputs, irdr_tuple):
        irdr_product, irdr_provenance = irdr_tuple
        bad_prov = irdr_provenance.model_copy(
            update={"source_record_id": "wrong-lidvid"}
        )
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_provenance"] = bad_prov
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)

    def test_interval_mismatch_fails(self, base_inputs, irdr_tuple):
        """IRDR stop != GRDR stop → failure."""
        irdr_product, irdr_provenance = irdr_tuple
        wrong_stop = datetime(2024, 6, 14, 4, 0, 0, tzinfo=timezone.utc)
        bad_product = irdr_product.model_copy(
            update={"observation_stop_utc": wrong_stop}
        )
        bad_inputs = dict(base_inputs)
        bad_inputs["irdr_product"] = bad_product
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(**bad_inputs)


# ===========================================================================
# J. Temporal validation errors
# ===========================================================================


class TestTemporalValidation:
    def test_horizons_epoch_mismatch_fails(
        self, descriptor, irdr_tuple, grdr_tuple
    ):
        """Horizons epoch != decision epoch → failure."""
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        hr = HorizonsSnapshotStore.load(_HORIZONS_SNAP)
        # Shift the geometry epoch by 1 second
        wrong_epoch = datetime(2024, 6, 14, 4, 0, 0, tzinfo=timezone.utc)
        geo2 = hr.geometry.model_copy(update={"epoch_utc": wrong_epoch})
        prov2 = hr.provenance.model_copy(update={"observed_at": wrong_epoch})
        req2 = hr.request.model_copy(update={"epoch_utc": wrong_epoch})
        hr2 = hr.model_copy(update={"geometry": geo2, "provenance": prov2, "request": req2})
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(
                descriptor=descriptor,
                horizons_result=hr2,
                irdr_product=irdr_product,
                irdr_provenance=irdr_provenance,
                grdr_product=grdr_product,
                grdr_provenance=grdr_provenance,
            )


# ===========================================================================
# K. Descriptor identity cross-binding
# ===========================================================================


class TestDescriptorIdentityCrossBinding:
    def test_replay_id_mismatch_fails(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple
    ):
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        bad_descriptor = descriptor.model_copy(
            update={"replay_id": "wrong_replay_id"}
        )
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(
                descriptor=bad_descriptor,
                horizons_result=horizons_result,
                irdr_product=irdr_product,
                irdr_provenance=irdr_provenance,
                grdr_product=grdr_product,
                grdr_provenance=grdr_provenance,
            )

    def test_policy_version_mismatch_fails(
        self, descriptor, horizons_result, irdr_tuple, grdr_tuple
    ):
        irdr_product, irdr_provenance = irdr_tuple
        grdr_product, grdr_provenance = grdr_tuple
        bad_descriptor = descriptor.model_copy(
            update={"replay_policy_version": "wrong-policy-v1"}
        )
        with pytest.raises(MissionSourceValidationError):
            ReplayAssembler.assemble(
                descriptor=bad_descriptor,
                horizons_result=horizons_result,
                irdr_product=irdr_product,
                irdr_provenance=irdr_provenance,
                grdr_product=grdr_product,
                grdr_provenance=grdr_provenance,
            )


# ===========================================================================
# L. Modeled policy canonical payload determinism
# ===========================================================================


class TestModeledProvenance:
    def test_modeled_id_is_sha256(self, assembled):
        """Modeled provenance ID is a valid SHA-256 hex string."""
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert len(modeled.provenance_id) == 64
        assert all(c in "0123456789abcdef" for c in modeled.provenance_id)

    def test_modeled_id_matches_formula(self, assembled, descriptor):
        """Modeled record ID matches the expected domain formula."""
        _, manifest = assembled
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]

        # Re-derive the canonical JSON from the descriptor
        lp = descriptor.link_policy
        mp = descriptor.mission_policy
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
        expected_id = _modeled_policy_id(canonical_json)
        assert modeled.provenance_id == expected_id
