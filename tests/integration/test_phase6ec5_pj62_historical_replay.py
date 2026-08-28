"""GCSI Phase 6E-C5 — PJ62 Historical Replay Provider Integration Test.

This test is COMPLETELY OFFLINE.

A network guard blocks all socket access.  No live requests are made to
NASA, JPL, PDS, Horizons, or any other external service.

Scope
-----
1. Load the PJ62 historical replay bundle end-to-end.
2. Assert the complete expected Scenario values.
3. Assert the ProvenanceManifest structure.
4. Prove TelecomEngine can consume the assembled link_inputs.
5. Prove the data_products_to_packets bridge works with the assembled products.
6. Optional: run through BaselineScheduler + PlanEvaluator to confirm
   deterministic ordering and GRDR deferred.
"""

from __future__ import annotations

import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path / sys.path setup
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError(
        "GCSI offline test guard: network access is forbidden in this test."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from backend.app.mission_sources import (
    HistoricalReplayProvider,
    MissionSourceMode,
)
from backend.app.models.bridge import data_products_to_packets
from backend.app.models.risk_level import RiskLevel
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceValidationStatus,
)
from backend.app.telecom.engine import TelecomEngine

# ---------------------------------------------------------------------------
# Frozen expected values
# ---------------------------------------------------------------------------

_SOURCE_REF = "data/replays/juno_pj62_mwr_v1.json"
_DECISION_EPOCH = datetime(2024, 6, 14, 3, 59, 55, 483000, tzinfo=timezone.utc)
_EXPECTED_RANGE_KM: float = 893345396.8038701
_EXPECTED_LIGHT_TIME_S: float = 2979.879489843171
_MODELED_LATENCY_S: float = 1.5

_IRDR_FILE_SIZE = 6_694_664
_GRDR_FILE_SIZE = 5_093_997
_IRDR_SIZE_BITS = _IRDR_FILE_SIZE * 8
_GRDR_SIZE_BITS = _GRDR_FILE_SIZE * 8


# ---------------------------------------------------------------------------
# Fixtures: load once per module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bundle():
    return HistoricalReplayProvider().load(_SOURCE_REF)


@pytest.fixture(scope="module")
def scenario(bundle):
    return bundle.scenario


@pytest.fixture(scope="module")
def manifest(bundle):
    return bundle.provenance


# ===========================================================================
# Part 1 — Bundle metadata
# ===========================================================================


class TestBundleMetadata:
    def test_provider_name(self, bundle):
        assert bundle.provider_name == "GCSI-HistoricalReplayProvider"

    def test_source_mode(self, bundle):
        assert bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_source_ref_preserved(self, bundle):
        assert bundle.source_ref == _SOURCE_REF


# ===========================================================================
# Part 2 — Complete Scenario
# ===========================================================================


class TestCompleteScenario:
    def test_scenario_id(self, scenario):
        assert scenario.scenario_id == "juno_pj62_mwr_2024166030000_v04_replay_v1"

    def test_simulated(self, scenario):
        assert scenario.simulated is True

    def test_distance_km_exact(self, scenario):
        assert scenario.distance_km == _EXPECTED_RANGE_KM

    def test_packets_empty(self, scenario):
        assert scenario.packets == []

    def test_anomalies_empty(self, scenario):
        assert scenario.anomalies == []

    def test_two_data_products(self, scenario):
        assert len(scenario.data_products) == 2

    def test_irdr_first(self, scenario):
        assert scenario.data_products[0].product_id == "JUNO-MWR-PJ62-IRDR"

    def test_grdr_second(self, scenario):
        assert scenario.data_products[1].product_id == "JUNO-MWR-PJ62-GRDR"


class TestLinkInputs:
    def test_timestamp(self, scenario):
        assert scenario.link_inputs["timestamp"] == _DECISION_EPOCH

    def test_snr_db(self, scenario):
        assert scenario.link_inputs["snr_db"] == 3.0

    def test_rssi_dbm(self, scenario):
        assert scenario.link_inputs["rssi_dbm"] == -95.0

    def test_nominal_data_rate_bps(self, scenario):
        assert scenario.link_inputs["nominal_data_rate_bps"] == 100000.0

    def test_latency_s(self, scenario):
        assert scenario.link_inputs["latency_s"] == _MODELED_LATENCY_S

    def test_link_stability(self, scenario):
        assert scenario.link_inputs["link_stability"] == 0.8

    def test_remaining_window_s(self, scenario):
        assert scenario.link_inputs["remaining_window_s"] == 900.0

    def test_latency_not_light_time(self, scenario):
        assert scenario.link_inputs["latency_s"] != _EXPECTED_LIGHT_TIME_S


class TestMissionState:
    def test_mission_id(self, scenario):
        assert scenario.mission_state.mission_id == "JUNO"

    def test_mission_phase(self, scenario):
        assert scenario.mission_state.mission_phase == "science_downlink"

    def test_current_event(self, scenario):
        assert scenario.mission_state.current_event == "PJ62 MWR historical replay downlink decision"

    def test_event_time_remaining_s(self, scenario):
        assert scenario.mission_state.event_time_remaining_s == 900.0

    def test_comm_window_remaining_s(self, scenario):
        assert scenario.mission_state.comm_window_remaining_s == 900.0

    def test_risk_score(self, scenario):
        assert scenario.mission_state.risk_score == 0.35

    def test_risk_level_medium(self, scenario):
        assert scenario.mission_state.risk_level == RiskLevel.MEDIUM


class TestIRDRProduct:
    @pytest.fixture
    def irdr(self, scenario):
        return scenario.data_products[0]

    def test_product_id(self, irdr):
        assert irdr.product_id == "JUNO-MWR-PJ62-IRDR"

    def test_product_type(self, irdr):
        assert irdr.product_type == "science"

    def test_subsystem(self, irdr):
        assert irdr.subsystem == "payload"

    def test_size_bits(self, irdr):
        assert irdr.size_bits == _IRDR_SIZE_BITS

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


class TestGRDRProduct:
    @pytest.fixture
    def grdr(self, scenario):
        return scenario.data_products[1]

    def test_product_id(self, grdr):
        assert grdr.product_id == "JUNO-MWR-PJ62-GRDR"

    def test_product_type(self, grdr):
        assert grdr.product_type == "science"

    def test_subsystem(self, grdr):
        assert grdr.subsystem == "payload"

    def test_size_bits(self, grdr):
        assert grdr.size_bits == _GRDR_SIZE_BITS

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


# ===========================================================================
# Part 3 — ProvenanceManifest assertions
# ===========================================================================


class TestProvenanceManifest:
    def test_three_external_records_present(self, manifest):
        external = [
            r for r in manifest.records
            if r.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        ]
        assert len(external) == 3

    def test_external_records_all_validated(self, manifest):
        for r in manifest.records:
            if r.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE:
                assert r.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_modeled_record_present(self, manifest):
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED]
        assert len(modeled) == 1

    def test_modeled_source_system(self, manifest):
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.source_system == "GCSI-historical-replay-policy"

    def test_modeled_source_version(self, manifest):
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.source_version == "pj62-mwr-v1"

    def test_modeled_no_timestamps(self, manifest):
        modeled = [r for r in manifest.records if r.kind == ProvenanceKind.MODELED][0]
        assert modeled.observed_at is None
        assert modeled.retrieved_at is None
        assert modeled.normalized_at is None

    def test_derived_records_present(self, manifest):
        derived = [r for r in manifest.records if r.kind == ProvenanceKind.DERIVED]
        assert len(derived) >= 5  # at minimum several derived records

    def test_no_duplicate_record_ids(self, manifest):
        ids = [r.provenance_id for r in manifest.records]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_bindings(self, manifest):
        keys = [
            (b.entity_type, b.entity_id, b.field_path)
            for b in manifest.bindings
        ]
        assert len(keys) == len(set(keys))

    def test_all_binding_references_valid(self, manifest):
        record_ids = {r.provenance_id for r in manifest.records}
        for b in manifest.bindings:
            assert b.provenance_id in record_ids

    def test_distance_km_bound_to_horizons_derived(self, manifest, scenario):
        sid = scenario.scenario_id
        binding = next(
            b for b in manifest.bindings
            if b.entity_type == "scenario"
            and b.entity_id == sid
            and b.field_path == "distance_km"
        )
        rec_idx = {r.provenance_id: r for r in manifest.records}
        rec = rec_idx[binding.provenance_id]
        assert rec.kind == ProvenanceKind.DERIVED
        # Its parent must be the Horizons external record
        horizons_external = next(
            r for r in manifest.records
            if r.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
            and r.source_system == "NASA/JPL Horizons API"
        )
        assert horizons_external.provenance_id in rec.parent_provenance_ids

    def test_manifest_record_count(self, manifest):
        # 3 external + 1 modeled + 13 derived = 17
        assert len(manifest.records) == 17

    def test_manifest_bindings_exist(self, manifest):
        assert len(manifest.bindings) > 0


# ===========================================================================
# Part 4 — TelecomEngine compatibility
# ===========================================================================


class TestTelecomCompatibility:
    @pytest.fixture(scope="class")
    def link_state(self, scenario):
        return TelecomEngine().compute(scenario.link_inputs)

    def test_telecom_accepts_link_inputs(self, link_state):
        """TelecomEngine must process the assembled link_inputs without error."""
        assert link_state is not None

    def test_eb_n0_approx_13_db(self, link_state):
        assert abs(link_state.eb_n0_db - 13.0) < 0.001

    def test_ber_approx(self, link_state):
        assert link_state.ber == pytest.approx(1.33293101753005e-10, rel=1e-3)

    def test_link_goodput_90000(self, link_state):
        assert link_state.link_goodput_bps == pytest.approx(90000.0)

    def test_latency_preserved(self, link_state):
        assert link_state.latency_s == _MODELED_LATENCY_S

    def test_remaining_window_preserved(self, link_state):
        assert link_state.remaining_window_s == 900.0

    def test_latency_is_not_light_time(self, link_state):
        assert link_state.latency_s != _EXPECTED_LIGHT_TIME_S


# ===========================================================================
# Part 5 — Legacy packet bridge compatibility
# ===========================================================================


class TestPacketBridgeCompatibility:
    @pytest.fixture(scope="class")
    def bridged(self, scenario):
        return data_products_to_packets(scenario.data_products)

    def test_bridge_produces_two_packets(self, bridged):
        assert len(bridged) == 2

    def test_irdr_packet_id(self, bridged):
        assert bridged[0].packet_id == "JUNO-MWR-PJ62-IRDR"

    def test_grdr_packet_id(self, bridged):
        assert bridged[1].packet_id == "JUNO-MWR-PJ62-GRDR"

    def test_irdr_size_bits_preserved(self, bridged):
        assert bridged[0].size_bits == _IRDR_SIZE_BITS

    def test_grdr_size_bits_preserved(self, bridged):
        assert bridged[1].size_bits == _GRDR_SIZE_BITS

    def test_scenario_packets_remain_empty(self, scenario):
        """Bridging does not modify the Scenario.packets collection."""
        assert scenario.packets == []


# ===========================================================================
# Part 6 — End-to-end deterministic pipeline check
# ===========================================================================


class TestEndToEndPipeline:
    """Optional high-value deterministic check: bridged products → TelecomEngine
    → BaselineScheduler → PlanEvaluator.

    Verifies that:
    - IRDR is ranked first (higher criticality/relevance)
    - GRDR is deferred (combined sequential cost > 900 s window)
    """

    @pytest.fixture(scope="class")
    def e2e_result(self, scenario):
        from backend.app.config import GCSIConfig, SchedulerWeights
        from backend.app.scheduler.baseline import BaselineScheduler
        from backend.app.evaluator.plan_evaluator import PlanEvaluator

        # Bridge products to packets
        packets = data_products_to_packets(scenario.data_products)

        # Compute link state
        link_state = TelecomEngine().compute(scenario.link_inputs)

        # Default weights
        weights = SchedulerWeights()

        # Rank
        plan = BaselineScheduler.rank(
            packets=packets,
            link_state=link_state,
            mission_state=scenario.mission_state,
            weights=weights,
        )

        # Evaluate
        result = PlanEvaluator().evaluate(
            plan=plan,
            link_state=link_state,
            mission_state=scenario.mission_state,
        )
        return plan, result

    def test_plan_has_two_packets(self, e2e_result):
        plan, _ = e2e_result
        assert len(plan.packets) == 2

    def test_irdr_ranked_first(self, e2e_result):
        plan, _ = e2e_result
        assert plan.packets[0].packet_id == "JUNO-MWR-PJ62-IRDR"

    def test_grdr_ranked_second(self, e2e_result):
        plan, _ = e2e_result
        assert plan.packets[1].packet_id == "JUNO-MWR-PJ62-GRDR"

    def test_grdr_deferred(self, e2e_result):
        """GRDR must be deferred because combined sequential cost > 900 s."""
        _, result = e2e_result
        assert "JUNO-MWR-PJ62-GRDR" in result.deferred_packets, (
            "GRDR must be deferred — combined sequential cost exceeds the 900 s window."
        )

    def test_irdr_not_deferred(self, e2e_result):
        """IRDR must NOT be deferred — it fits within the window."""
        _, result = e2e_result
        assert "JUNO-MWR-PJ62-IRDR" not in result.deferred_packets, (
            "IRDR must not be deferred — it fits within the 900 s window."
        )

    def test_plan_is_deterministic(self, scenario):
        """Running the pipeline twice produces the exact same plan."""
        from backend.app.config import SchedulerWeights
        from backend.app.scheduler.baseline import BaselineScheduler

        packets = data_products_to_packets(scenario.data_products)
        link_state = TelecomEngine().compute(scenario.link_inputs)
        weights = SchedulerWeights()

        plan1 = BaselineScheduler.rank(
            packets=packets,
            link_state=link_state,
            mission_state=scenario.mission_state,
            weights=weights,
        )
        plan2 = BaselineScheduler.rank(
            packets=packets,
            link_state=link_state,
            mission_state=scenario.mission_state,
            weights=weights,
        )

        ids1 = [p.packet_id for p in plan1.packets]
        ids2 = [p.packet_id for p in plan2.packets]
        assert ids1 == ids2
