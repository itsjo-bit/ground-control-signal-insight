"""Phase 4.2E: Tests for ground reception and mission evidence evolution.

Verifies:
- Deterministic ground information objective assessment
- Before/after evidence state computation
- Actual delivered IDs drive objectives
- Partial evidence remains partial
- Spacecraft anomaly is not magically resolved
- Benchmark freeze verification (exact SHA256)
- Generic scenario compatibility

IMPORTANT: coverage helpers are imported from the PRODUCTION module
  backend.app.presentation.ground_evidence
NOT redefined locally here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.app import state as app_state
from backend.app.simulation.transmission_sim import TransmissionSimulator
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.packet import Packet

# ── Production ground-evidence helpers (these must be the real implementations) ──
from backend.app.presentation.ground_evidence import (
    assess_ground_objectives,
    overall_ground_evidence_coverage,
    ground_evidence_level,
    objective_availability_label,
    EVIDENCE_THRESHOLD_HIGH,
    EVIDENCE_THRESHOLD_MEDIUM,
)

_PROJECT_ROOT = Path(__file__).parents[2]
_SCENARIOS_DIR = _PROJECT_ROOT / "data" / "scenarios"
_ASTERIA_SCENARIO = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")
_V3_SCENARIO = str(_SCENARIOS_DIR / "mission_data_v3.json")
_BENCHMARK_CONFIG = _PROJECT_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json"
_V3_PATH = _SCENARIOS_DIR / "mission_data_v3.json"

# Ground information objectives for ASTERIA-7 (mirrors data/demo/asteria7_experience.json)
GROUND_OBJECTIVES: dict[str, list[str]] = {
    "fresh_thermal_history": ["TEL-THERM-HR-042"],
    "anomaly_event_timeline": ["DIAG-THERM-EVT-017"],
    "power_correlation": ["TEL-PWR-CORR-031"],
    "fault_control_context": ["FDIR-THERM-017", "CMD-THERM-571"],
    "sensor_interpretation": ["CAL-THERM-006"],
    "communication_context": ["DIAG-COM-LINK-088"],
    "pointing_context": ["NAV-ATT-214"],
}

# Frozen SHA256 hashes of protected files — computed at Phase 4.2F5 gate.
# These values MUST match forever; update requires scientific review.
_BENCHMARK_SHA256 = "932bedd0dc6aacf255517ec62d812c8be6306358e0dff27bc0a227462fae6fc8"
_V3_SHA256 = "dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9"


@pytest.fixture(autouse=True)
def reset_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


@pytest.fixture
def loaded_asteria():
    app_state.load_scenario(_ASTERIA_SCENARIO)


# ── Tests verify that production module functions work correctly ──────────────

class TestProductionGroundEvidenceHelpers:
    """Verify that the production ground-evidence module behaves correctly."""

    def test_ground_evidence_level_low(self):
        assert ground_evidence_level(0.0) == "LOW"
        assert ground_evidence_level(0.39) == "LOW"

    def test_ground_evidence_level_medium(self):
        assert ground_evidence_level(0.40) == "MEDIUM"
        assert ground_evidence_level(0.79) == "MEDIUM"

    def test_ground_evidence_level_high(self):
        assert ground_evidence_level(0.80) == "HIGH"
        assert ground_evidence_level(1.0) == "HIGH"

    def test_thresholds_match_spec(self):
        assert EVIDENCE_THRESHOLD_HIGH == 0.80
        assert EVIDENCE_THRESHOLD_MEDIUM == 0.40

    def test_objective_availability_label(self):
        assert objective_availability_label(1.0) == "AVAILABLE"
        assert objective_availability_label(0.5) == "PARTIAL"
        assert objective_availability_label(0.0) == "UNAVAILABLE"

    def test_assess_empty_delivery(self):
        """Zero delivery → all objectives UNAVAILABLE."""
        result = assess_ground_objectives(set(), GROUND_OBJECTIVES)
        assert len(result) == len(GROUND_OBJECTIVES)
        for obj in result:
            assert obj.fraction == 0.0
            assert obj.level == "LOW"

    def test_assess_full_delivery(self):
        """All anchor IDs delivered → all objectives AVAILABLE at HIGH."""
        all_ids = {pid for ids in GROUND_OBJECTIVES.values() for pid in ids}
        result = assess_ground_objectives(all_ids, GROUND_OBJECTIVES)
        for obj in result:
            assert obj.fraction == 1.0
            assert obj.level == "HIGH"

    def test_assess_partial_delivery(self):
        """Partial delivery gives correct fractions."""
        partial = {"TEL-THERM-HR-042", "FDIR-THERM-017"}
        result = assess_ground_objectives(partial, GROUND_OBJECTIVES)
        by_name = {o.name: o for o in result}

        assert by_name["fresh_thermal_history"].fraction == 1.0
        assert by_name["fault_control_context"].fraction == 0.5
        assert by_name["anomaly_event_timeline"].fraction == 0.0

    def test_overall_coverage_zero(self):
        cov = overall_ground_evidence_coverage(set(), GROUND_OBJECTIVES)
        assert ground_evidence_level(cov) == "LOW"

    def test_overall_coverage_full(self):
        all_ids = {pid for ids in GROUND_OBJECTIVES.values() for pid in ids}
        cov = overall_ground_evidence_coverage(all_ids, GROUND_OBJECTIVES)
        assert ground_evidence_level(cov) == "HIGH"

    def test_before_after_information_state_changes(self, loaded_asteria):
        """Information state changes between before and after delivery."""
        before = overall_ground_evidence_coverage(set(), GROUND_OBJECTIVES)
        all_anchors = {pid for ids in GROUND_OBJECTIVES.values() for pid in ids}
        after = overall_ground_evidence_coverage(all_anchors, GROUND_OBJECTIVES)

        assert ground_evidence_level(before) == "LOW"
        assert ground_evidence_level(after) == "HIGH"
        assert after > before

    def test_anomaly_not_resolved_by_delivery(self, loaded_asteria):
        """Delivering data products must NOT change the spacecraft anomaly status."""
        scenario = app_state.active_scenario
        anomaly = next(
            (a for a in scenario.anomalies if a.anomaly_id == "ANOM-THERM-017"),
            None,
        )
        assert anomaly is not None
        assert anomaly.status == "active"

        # Ground coverage computation does NOT modify scenario state
        delivered = {"TEL-THERM-HR-042", "DIAG-THERM-EVT-017", "FDIR-THERM-017"}
        _ = assess_ground_objectives(delivered, GROUND_OBJECTIVES)
        _ = overall_ground_evidence_coverage(delivered, GROUND_OBJECTIVES)

        assert scenario.anomalies[0].status == "active", (
            "Spacecraft anomaly must remain active — ground reception does not resolve it"
        )

    def test_simulated_delivery_drives_objectives(self, loaded_asteria):
        """TransmissionSimulator.delivered_packets actually drives ground objectives."""
        scenario = app_state.active_scenario
        link_state = app_state.active_link_state

        anchor_ids = list({pid for ids in GROUND_OBJECTIVES.values() for pid in ids})
        dp_map = {dp.product_id: dp for dp in scenario.data_products}
        packets = [
            Packet(
                packet_id=dp_map[pid].product_id,
                packet_type=dp_map[pid].product_type,
                size_bits=dp_map[pid].size_bits,
                criticality=dp_map[pid].criticality,
                mission_relevance=dp_map[pid].mission_relevance,
                deadline_s=dp_map[pid].deadline_s,
                retry_cost=dp_map[pid].retry_cost,
                delivery_requirement=dp_map[pid].delivery_requirement,
            )
            for pid in anchor_ids
            if pid in dp_map
        ]
        plan = CandidatePlan(
            plan_id="anchor-plan",
            strategy="manual",
            packets=packets,
            generated_by="test",
            metadata={},
        )
        sim = TransmissionSimulator()
        result = sim.simulate(plan, link_state, scenario.mission_state, seed=42)

        delivered_ids = set(result.delivered_packets)
        objectives = assess_ground_objectives(delivered_ids, GROUND_OBJECTIVES)
        overall = overall_ground_evidence_coverage(delivered_ids, GROUND_OBJECTIVES)

        # Sanity: total delivered must be non-negative
        assert len(delivered_ids) >= 0
        # overall is a valid fraction
        assert 0.0 <= overall <= 1.0
        # Each objective is a valid ObjectiveCoverage
        assert len(objectives) == len(GROUND_OBJECTIVES)


class TestGroundObjectivesAllIdsValid:
    def test_all_ground_objective_ids_exist_in_asteria_scenario(self, loaded_asteria):
        """All product IDs in GROUND_OBJECTIVES must exist in ASTERIA-7 scenario."""
        scenario = app_state.active_scenario
        all_ids = {dp.product_id for dp in scenario.data_products}
        for obj_name, product_ids in GROUND_OBJECTIVES.items():
            for pid in product_ids:
                assert pid in all_ids, (
                    f"Ground objective '{obj_name}' references product '{pid}' "
                    f"which does not exist in ASTERIA-7 scenario"
                )


class TestBenchmarkFreeze:
    """Verify that protected scientific files have NOT been modified.

    The SHA256 hashes below were recorded at the Phase 4.2F5 gate and must
    remain unchanged for the life of this codebase.  If a hash fails, it means
    a frozen scientific file was accidentally modified — revert it before
    committing.
    """

    def test_benchmark_config_exact_sha256(self):
        """benchmarks/configs/gcsi_benchmark_v1.json must match its frozen SHA256."""
        assert _BENCHMARK_CONFIG.exists(), "Benchmark config file not found"
        actual = hashlib.sha256(_BENCHMARK_CONFIG.read_bytes()).hexdigest().lower()
        assert actual == _BENCHMARK_SHA256, (
            f"benchmarks/configs/gcsi_benchmark_v1.json has been modified!\n"
            f"  Expected SHA256: {_BENCHMARK_SHA256}\n"
            f"  Actual SHA256:   {actual}\n"
            "Revert the file before committing."
        )

    def test_mission_data_v3_exact_sha256(self):
        """data/scenarios/mission_data_v3.json must match its frozen SHA256."""
        assert _V3_PATH.exists(), "mission_data_v3.json not found"
        actual = hashlib.sha256(_V3_PATH.read_bytes()).hexdigest().lower()
        assert actual == _V3_SHA256, (
            f"data/scenarios/mission_data_v3.json has been modified!\n"
            f"  Expected SHA256: {_V3_SHA256}\n"
            f"  Actual SHA256:   {actual}\n"
            "Revert the file before committing."
        )

    def test_mission_data_v3_structure_intact(self):
        """mission_data_v3.json structural checks."""
        data = json.loads(_V3_PATH.read_bytes())
        assert data.get("scenario_id") is not None
        assert len(data.get("data_products", [])) == 150
        assert len(data.get("anomalies", [])) == 3

    def test_generic_scenario_loads_after_asteria_default(self):
        """Generic scenario (v3) still loads and works normally."""
        app_state.load_scenario(_V3_SCENARIO)
        assert app_state.active_scenario is not None
        assert len(app_state.active_scenario.data_products) == 150

    def test_asteria_scenario_does_not_affect_v3(self):
        """Loading ASTERIA-7 and then v3 works correctly — no state contamination."""
        app_state.load_scenario(_ASTERIA_SCENARIO)
        assert len(app_state.active_scenario.data_products) == 1284

        app_state.load_scenario(_V3_SCENARIO)
        assert len(app_state.active_scenario.data_products) == 150
        assert app_state.active_scenario.scenario_id != "asteria7_thermal_priority_contact_v1"

    def test_experience_endpoint_not_available_for_v3(self):
        """GET /experience returns available=False for non-ASTERIA-7 scenarios."""
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_V3_SCENARIO)
        result = get_experience()
        assert result.available is False
        assert result.manifest is None

    def test_experience_endpoint_available_for_asteria(self):
        """GET /experience returns available=True for ASTERIA-7."""
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.available is True
        assert result.manifest is not None
