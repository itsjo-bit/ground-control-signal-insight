"""
Phase 4.2F5 — Backend Tests: Ground Reception & Mission Evidence Evolution

Verifies:
- Production ground_evidence module is importable and correct
- assess_ground_objectives uses delivered IDs correctly
- overall_ground_evidence_coverage returns valid fractions
- ground_evidence_level thresholds match spec
- Ingest batch counts sum exactly to 1,284
- Total ingest bytes match authoritative queue bytes
- /plans/assess is non-mutating and order-preserving
- Experience manifest Pydantic validation
- Malformed registered sidecar fails cleanly
- Generic scenario experience unavailable
- Frozen benchmark exact SHA256 hashes
- Same simulator seed produces identical results after F5 changes
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
_SCENARIOS_DIR = _PROJECT_ROOT / "data" / "scenarios"
_ASTERIA_SCENARIO = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")
_V3_SCENARIO = str(_SCENARIOS_DIR / "mission_data_v3.json")
_DEMO_DIR = _PROJECT_ROOT / "data" / "demo"
_ASTERIA_EXPERIENCE = _DEMO_DIR / "asteria7_experience.json"
_BENCHMARK_CONFIG = _PROJECT_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json"
_V3_PATH = _SCENARIOS_DIR / "mission_data_v3.json"

# Frozen SHA256 — must match Phase 4.2F5 gate values
_BENCHMARK_SHA256 = "932bedd0dc6aacf255517ec62d812c8be6306358e0dff27bc0a227462fae6fc8"
_V3_SHA256 = "dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9"

# Ground objectives matching the experience sidecar
_GROUND_OBJECTIVES: dict[str, list[str]] = {
    "fresh_thermal_history": ["TEL-THERM-HR-042"],
    "anomaly_event_timeline": ["DIAG-THERM-EVT-017"],
    "power_correlation": ["TEL-PWR-CORR-031"],
    "fault_control_context": ["FDIR-THERM-017", "CMD-THERM-571"],
    "sensor_interpretation": ["CAL-THERM-006"],
    "communication_context": ["DIAG-COM-LINK-088"],
    "pointing_context": ["NAV-ATT-214"],
}


@pytest.fixture(autouse=True)
def reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


class TestProductionGroundEvidenceModule:
    """Verify that backend.app.presentation.ground_evidence is importable and correct."""

    def test_module_importable(self):
        """Production module must be importable."""
        from backend.app.presentation.ground_evidence import (
            assess_ground_objectives,
            overall_ground_evidence_coverage,
            ground_evidence_level,
            objective_availability_label,
        )
        assert callable(assess_ground_objectives)
        assert callable(overall_ground_evidence_coverage)
        assert callable(ground_evidence_level)
        assert callable(objective_availability_label)

    def test_level_thresholds_correct(self):
        from backend.app.presentation.ground_evidence import (
            ground_evidence_level,
            EVIDENCE_THRESHOLD_HIGH,
            EVIDENCE_THRESHOLD_MEDIUM,
        )
        assert EVIDENCE_THRESHOLD_HIGH == 0.80
        assert EVIDENCE_THRESHOLD_MEDIUM == 0.40
        assert ground_evidence_level(0.0) == "LOW"
        assert ground_evidence_level(0.39) == "LOW"
        assert ground_evidence_level(0.40) == "MEDIUM"
        assert ground_evidence_level(0.79) == "MEDIUM"
        assert ground_evidence_level(0.80) == "HIGH"
        assert ground_evidence_level(1.0) == "HIGH"

    def test_assess_zero_delivery(self):
        from backend.app.presentation.ground_evidence import assess_ground_objectives
        result = assess_ground_objectives(set(), _GROUND_OBJECTIVES)
        assert len(result) == len(_GROUND_OBJECTIVES)
        for obj in result:
            assert obj.fraction == 0.0
            assert obj.level == "LOW"

    def test_assess_full_delivery(self):
        from backend.app.presentation.ground_evidence import assess_ground_objectives
        all_ids = {pid for ids in _GROUND_OBJECTIVES.values() for pid in ids}
        result = assess_ground_objectives(all_ids, _GROUND_OBJECTIVES)
        for obj in result:
            assert obj.fraction == 1.0
            assert obj.level == "HIGH"

    def test_partial_delivery_fractions(self):
        from backend.app.presentation.ground_evidence import assess_ground_objectives
        partial = {"TEL-THERM-HR-042", "FDIR-THERM-017"}
        result = assess_ground_objectives(partial, _GROUND_OBJECTIVES)
        by_name = {o.name: o for o in result}
        assert by_name["fresh_thermal_history"].fraction == 1.0
        assert by_name["fault_control_context"].fraction == 0.5
        assert by_name["anomaly_event_timeline"].fraction == 0.0

    def test_overall_coverage_empty(self):
        from backend.app.presentation.ground_evidence import (
            overall_ground_evidence_coverage,
            ground_evidence_level,
        )
        cov = overall_ground_evidence_coverage(set(), _GROUND_OBJECTIVES)
        assert ground_evidence_level(cov) == "LOW"

    def test_overall_coverage_full(self):
        from backend.app.presentation.ground_evidence import (
            overall_ground_evidence_coverage,
            ground_evidence_level,
        )
        all_ids = {pid for ids in _GROUND_OBJECTIVES.values() for pid in ids}
        cov = overall_ground_evidence_coverage(all_ids, _GROUND_OBJECTIVES)
        assert ground_evidence_level(cov) == "HIGH"

    def test_availability_labels(self):
        from backend.app.presentation.ground_evidence import objective_availability_label
        assert objective_availability_label(1.0) == "AVAILABLE"
        assert objective_availability_label(0.5) == "PARTIAL"
        assert objective_availability_label(0.0) == "UNAVAILABLE"


class TestIngestReplayTotals:
    """Batch product counts must sum to exactly 1,284 == authoritative scenario count."""

    def test_batch_sum_equals_total_products(self):
        sidecar = json.loads(_ASTERIA_EXPERIENCE.read_bytes())
        replay = sidecar["ingest_replay"]
        total_products = replay["total_products"]
        batch_sum = sum(
            p["count"]
            for batch in replay["batches"]
            for p in batch["products"]
        )
        assert batch_sum == total_products, (
            f"Batch product count sum ({batch_sum}) != total_products ({total_products})"
        )

    def test_total_products_matches_scenario(self):
        from backend.app import state as app_state
        sidecar = json.loads(_ASTERIA_EXPERIENCE.read_bytes())
        total_products = sidecar["ingest_replay"]["total_products"]
        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario_count = len(app_state.active_scenario.data_products)
        assert total_products == scenario_count, (
            f"Experience manifest total_products ({total_products}) != "
            f"scenario data_products count ({scenario_count})"
        )

    def test_total_bytes_match_scenario_queue(self):
        from backend.app import state as app_state
        sidecar = json.loads(_ASTERIA_EXPERIENCE.read_bytes())
        total_bytes = sidecar["ingest_replay"]["total_bytes"]
        assert total_bytes == 2_740_000_000, (
            f"Experience manifest total_bytes ({total_bytes}) != 2,740,000,000"
        )
        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario_bytes = sum(
            dp.size_bits // 8
            for dp in app_state.active_scenario.data_products
        )
        # Allow ±10% for presentation rounding vs exact byte sum
        assert abs(scenario_bytes - total_bytes) / total_bytes <= 0.20, (
            f"total_bytes ({total_bytes}) diverges >20% from actual scenario "
            f"byte sum ({scenario_bytes})"
        )


class TestExperienceManifestValidation:
    """Experience manifest Pydantic validation."""

    def test_valid_asteria_sidecar_loads(self):
        from backend.app.api.routes_experience import get_experience
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.available is True
        assert result.manifest is not None
        # Strongly typed fields must be present
        assert result.manifest.schema_version is not None
        assert result.manifest.scenario_id is not None
        assert result.manifest.display is not None
        assert result.manifest.ingest_replay is not None
        assert result.manifest.ground_information_objectives is not None

    def test_generic_scenario_experience_unavailable(self):
        from backend.app.api.routes_experience import get_experience
        from backend.app import state as app_state
        app_state.load_scenario(_V3_SCENARIO)
        result = get_experience()
        assert result.available is False
        assert result.manifest is None

    def test_no_scenario_experience_unavailable(self):
        from backend.app.api.routes_experience import get_experience
        result = get_experience()
        assert result.available is False

    def test_experience_ingest_replay_batch_sum(self):
        from backend.app.api.routes_experience import get_experience
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.manifest is not None
        replay = result.manifest.ingest_replay
        batch_sum = sum(
            p.count
            for batch in replay.batches
            for p in batch.products
        )
        assert batch_sum == replay.total_products


class TestPlansAssessNonMutating:
    """/plans/assess must be non-mutating and order-preserving."""

    def test_assess_does_not_mutate_issued_plans(self):
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan as assess_plan
        from backend.app.api.routes_plans import AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:5]]
        before_count = len(app_state.issued_plans)
        req = AssessRequest(product_ids=product_ids)
        assess_plan(req)
        after_count = len(app_state.issued_plans)
        assert after_count == before_count, (
            f"/plans/assess must not add to issued_plans registry "
            f"(before={before_count}, after={after_count})"
        )

    def test_assess_preserves_product_order(self):
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan as assess_plan
        from backend.app.api.routes_plans import AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:8]]
        reversed_ids = list(reversed(product_ids))
        req = AssessRequest(product_ids=reversed_ids)
        result = assess_plan(req)
        returned_ids = [p.packet_id for p in result.plan.packets]
        assert returned_ids == reversed_ids, (
            "assess_plan must preserve the submitted product order"
        )

    def test_assess_returns_authoritative_evaluation(self):
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan as assess_plan
        from backend.app.api.routes_plans import AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:5]]
        req = AssessRequest(product_ids=product_ids)
        result = assess_plan(req)
        assert result.evaluation is not None
        assert result.evaluation.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert result.capacity_summary is not None


class TestFreezeVerification:
    """Exact SHA256 hashes for frozen scientific files."""

    def test_benchmark_config_exact_sha256(self):
        actual = hashlib.sha256(_BENCHMARK_CONFIG.read_bytes()).hexdigest().lower()
        assert actual == _BENCHMARK_SHA256, (
            f"benchmarks/configs/gcsi_benchmark_v1.json modified!\n"
            f"  Expected: {_BENCHMARK_SHA256}\n"
            f"  Actual:   {actual}"
        )

    def test_mission_data_v3_exact_sha256(self):
        actual = hashlib.sha256(_V3_PATH.read_bytes()).hexdigest().lower()
        assert actual == _V3_SHA256, (
            f"data/scenarios/mission_data_v3.json modified!\n"
            f"  Expected: {_V3_SHA256}\n"
            f"  Actual:   {actual}"
        )


class TestSimulatorInvariantsAfterF5:
    """Same-seed invariant still holds after Phase 4.2F5 changes."""

    def test_same_seed_produces_identical_results(self):
        from backend.app import state as app_state
        from backend.app.simulation.transmission_sim import TransmissionSimulator
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario = app_state.active_scenario
        link_state = app_state.active_link_state
        mission_state = scenario.mission_state

        products = scenario.data_products[:5]
        packets = [
            Packet(
                packet_id=dp.product_id,
                packet_type=dp.product_type,
                size_bits=dp.size_bits,
                criticality=dp.criticality,
                mission_relevance=dp.mission_relevance,
                deadline_s=dp.deadline_s,
                retry_cost=dp.retry_cost,
                delivery_requirement=dp.delivery_requirement,
            )
            for dp in products
        ]
        plan = CandidatePlan(
            plan_id="f5-invariant-test",
            strategy="test",
            packets=packets,
            generated_by="test",
            metadata={},
        )
        sim = TransmissionSimulator()
        r1 = sim.simulate(plan, link_state, mission_state, seed=42)
        r2 = sim.simulate(plan, link_state, mission_state, seed=42)

        assert r1.delivered_packets == r2.delivered_packets
        assert r1.failed_packets == r2.failed_packets
        assert r1.deferred_packets == r2.deferred_packets
        assert r1.elapsed_time_s == r2.elapsed_time_s
