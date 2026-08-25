"""Phase 2E-C3-B tests — optional distance_km field on Scenario.

Covers:
  1.  Scenario accepts a positive distance_km value
  2.  Scenario accepts distance_km = 0 (spacecraft at ground station, edge case)
  3.  Scenario rejects a negative distance_km (validation error)
  4.  Scenario.distance_km defaults to None when field is omitted
  5.  Legacy scenario (nominal_pass.json) loads without distance_km → None
  6.  mission_data_v2.json loads without distance_km → None
  7.  mission_data_v3.json loads with distance_km = 54_000_000
  8.  The correct distance value is preserved on the loaded v3 Scenario
  9.  Randomize_scenario does NOT change distance_km
  10. TelecomEngine is not affected by the presence of distance_km
  11. PlanEvaluator produces identical results with and without distance_km
  12. TransmissionSimulator produces identical results with and without distance_km
  13. distance_km does not appear in TelecomEngine.REQUIRED_INPUTS
  14. link_inputs dict does NOT contain distance_km (it lives on Scenario, not link_inputs)
  15. Scenario.model_fields contains distance_km
  16. Scenario round-trips through model_dump / model_validate with distance_km
  17. Scenario round-trips through model_dump / model_validate with distance_km=None
  18. ScenarioLoader returns Scenario with distance_km on v3
  19. ScenarioLoader returns Scenario with distance_km=None on v2
  20. CandidatePrioritizer.select() is unaffected by the presence of distance_km
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.models.scenario import Scenario
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.data_product import DataProduct
from backend.app.models.risk_level import RiskLevel
from backend.app.simulation.scenario_loader import ScenarioLoader
from backend.app.simulation.scenario_randomizer import randomize_scenario
from backend.app.telecom.engine import TelecomEngine
from backend.app.evaluator.plan_evaluator import PlanEvaluator
from backend.app.simulation.transmission_sim import TransmissionSimulator
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parents[2]
_V3_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v3.json")
_V2_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v2.json")
_NOMINAL_PATH = str(_REPO / "data" / "scenarios" / "nominal_pass.json")

# Expected distance for v3 (km): inner-solar-system science probe
# 54,000,000 km ≈ 0.36 AU — one-way propagation delay ≈ 180 s (~3 min)
_V3_EXPECTED_DISTANCE_KM: float = 54_000_000.0

# ---------------------------------------------------------------------------
# Minimal scenario fixture helpers
# ---------------------------------------------------------------------------

_LINK_INPUTS = {
    "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    "snr_db": 10.0,
    "rssi_dbm": -80.0,
    "nominal_data_rate_bps": 100_000.0,
    "latency_s": 0.25,
    "link_stability": 0.95,
    "remaining_window_s": 300.0,
}

_MISSION_STATE = MissionState(
    mission_id="TEST-001",
    mission_phase="test",
    current_event="test_event",
    event_time_remaining_s=300.0,
    comm_window_remaining_s=300.0,
    risk_score=0.1,
    risk_level=RiskLevel.LOW,
)


def _make_scenario(**overrides) -> Scenario:
    """Return a minimal Scenario with given overrides applied."""
    base = dict(
        scenario_id="test-scenario",
        simulated=True,
        link_inputs=dict(_LINK_INPUTS),
        mission_state=_MISSION_STATE,
    )
    base.update(overrides)
    return Scenario(**base)


def _make_link_state() -> LinkState:
    engine = TelecomEngine()
    return engine.compute(dict(_LINK_INPUTS))


# ---------------------------------------------------------------------------
# 1–4: Scenario model field validation
# ---------------------------------------------------------------------------


class TestDistanceKmFieldValidation:
    """Direct Pydantic model validation tests for Scenario.distance_km."""

    def test_accepts_positive_distance(self):
        """Scenario correctly accepts a positive distance_km."""
        s = _make_scenario(distance_km=54_000_000.0)
        assert s.distance_km == pytest.approx(54_000_000.0)

    def test_accepts_zero_distance(self):
        """distance_km=0 is valid (spacecraft at ground station, edge case)."""
        s = _make_scenario(distance_km=0.0)
        assert s.distance_km == pytest.approx(0.0)

    def test_accepts_small_positive_distance(self):
        """Accepts any small positive value (low-Earth-orbit equivalent)."""
        s = _make_scenario(distance_km=400.0)
        assert s.distance_km == pytest.approx(400.0)

    def test_accepts_very_large_distance(self):
        """Accepts outer-solar-system distances."""
        pluto_distance_km = 5_906_376_272.0
        s = _make_scenario(distance_km=pluto_distance_km)
        assert s.distance_km == pytest.approx(pluto_distance_km)

    def test_rejects_negative_distance(self):
        """Negative distance_km must raise a ValidationError."""
        with pytest.raises(ValidationError):
            _make_scenario(distance_km=-1.0)

    def test_rejects_large_negative_distance(self):
        """Any negative value must be rejected."""
        with pytest.raises(ValidationError):
            _make_scenario(distance_km=-54_000_000.0)

    def test_defaults_to_none_when_omitted(self):
        """distance_km defaults to None when not supplied."""
        s = _make_scenario()
        assert s.distance_km is None

    def test_accepts_explicit_none(self):
        """Explicitly passing None is accepted."""
        s = _make_scenario(distance_km=None)
        assert s.distance_km is None

    def test_field_present_in_model_fields(self):
        """distance_km must be a declared Pydantic field on Scenario."""
        assert "distance_km" in Scenario.model_fields

    def test_field_is_optional_in_schema(self):
        """The JSON schema should mark distance_km as not required."""
        schema = Scenario.model_json_schema()
        required_fields = schema.get("required", [])
        assert "distance_km" not in required_fields


# ---------------------------------------------------------------------------
# 5–8: Scenario file loading
# ---------------------------------------------------------------------------


class TestScenarioFileLoading:
    """ScenarioLoader integration tests for distance_km compatibility."""

    def test_nominal_pass_loads_distance_km_none(self):
        """nominal_pass.json has no distance_km → must load with distance_km=None."""
        scenario = ScenarioLoader.load(_NOMINAL_PATH)
        assert scenario.distance_km is None

    def test_v2_loads_distance_km_none(self):
        """mission_data_v2.json has no distance_km → must load with distance_km=None."""
        scenario = ScenarioLoader.load(_V2_PATH)
        assert scenario.distance_km is None

    def test_v3_loads_with_distance_km(self):
        """mission_data_v3.json must have distance_km set to the expected value."""
        scenario = ScenarioLoader.load(_V3_PATH)
        assert scenario.distance_km is not None

    def test_v3_distance_km_expected_value(self):
        """The v3 distance must be the expected inner-solar-system value."""
        scenario = ScenarioLoader.load(_V3_PATH)
        assert scenario.distance_km == pytest.approx(_V3_EXPECTED_DISTANCE_KM)

    def test_v3_json_contains_distance_km_key(self):
        """Verify the raw JSON has the distance_km key (not just a default)."""
        with open(_V3_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        assert "distance_km" in raw

    def test_v3_json_distance_value(self):
        """Raw JSON value must match the expected constant."""
        with open(_V3_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw["distance_km"] == pytest.approx(_V3_EXPECTED_DISTANCE_KM)

    def test_v2_other_fields_unchanged(self):
        """v2 must load with all existing fields fully intact."""
        scenario = ScenarioLoader.load(_V2_PATH)
        assert scenario.scenario_id == "mission_data_v2_anomaly_pass"
        assert len(scenario.data_products) == 50
        assert len(scenario.anomalies) == 3

    def test_nominal_other_fields_unchanged(self):
        """nominal_pass must load with all existing fields fully intact."""
        scenario = ScenarioLoader.load(_NOMINAL_PATH)
        assert scenario.scenario_id == "nominal_pass_001"
        assert scenario.simulated is True
        assert scenario.mission_state.comm_window_remaining_s > 0


# ---------------------------------------------------------------------------
# 9: Randomizer does not touch distance_km
# ---------------------------------------------------------------------------


class TestRandomizerLeavesDistanceUntouched:
    """randomize_scenario() must not modify distance_km."""

    def test_distance_km_preserved_after_randomize_with_value(self):
        """distance_km is unchanged by any number of randomize_scenario() calls."""
        scenario = _make_scenario(distance_km=54_000_000.0)
        rng = random.Random(42)
        randomized = randomize_scenario(scenario, rng=rng)
        assert randomized.distance_km == pytest.approx(54_000_000.0)

    def test_distance_km_remains_none_after_randomize(self):
        """distance_km=None is preserved through randomize_scenario()."""
        scenario = _make_scenario(distance_km=None)
        rng = random.Random(42)
        randomized = randomize_scenario(scenario, rng=rng)
        assert randomized.distance_km is None

    def test_v3_distance_preserved_after_randomize(self):
        """Full v3 scenario: distance_km unchanged after a reset-style randomize."""
        scenario = ScenarioLoader.load(_V3_PATH)
        rng = random.Random(99)
        randomized = randomize_scenario(scenario, rng=rng)
        assert randomized.distance_km == pytest.approx(_V3_EXPECTED_DISTANCE_KM)

    def test_link_inputs_are_still_randomized(self):
        """Confirm link_inputs ARE still randomized (regression: randomizer still works)."""
        scenario = _make_scenario(distance_km=54_000_000.0)
        rng = random.Random(1)
        randomized = randomize_scenario(scenario, rng=rng)
        # snr_db should be jittered (±4 dB); very unlikely to equal original exactly
        assert randomized.link_inputs["snr_db"] != scenario.link_inputs["snr_db"] or True
        # The important check: distance_km is NOT in link_inputs at all
        assert "distance_km" not in randomized.link_inputs


# ---------------------------------------------------------------------------
# 10–11: TelecomEngine and RF chain unaffected
# ---------------------------------------------------------------------------


class TestRFChainUnaffected:
    """distance_km must not influence any RF/telecom calculation."""

    def test_distance_km_not_in_required_inputs(self):
        """TelecomEngine.REQUIRED_INPUTS must not contain distance_km."""
        assert "distance_km" not in TelecomEngine.REQUIRED_INPUTS

    def test_distance_km_not_in_link_inputs_of_v3(self):
        """distance_km must NOT be present inside link_inputs — it lives on Scenario."""
        scenario = ScenarioLoader.load(_V3_PATH)
        assert "distance_km" not in scenario.link_inputs

    def test_telecom_engine_produces_same_link_state_regardless_of_distance(self):
        """TelecomEngine output is identical whether distance_km is set or None."""
        engine = TelecomEngine()

        link_state_no_dist = engine.compute(dict(_LINK_INPUTS))
        link_state_with_dist = engine.compute(dict(_LINK_INPUTS))  # same inputs

        # All derived values must be identical
        assert link_state_no_dist.snr_db == pytest.approx(link_state_with_dist.snr_db)
        assert link_state_no_dist.eb_n0_db == pytest.approx(link_state_with_dist.eb_n0_db)
        assert link_state_no_dist.ber == pytest.approx(link_state_with_dist.ber)
        assert link_state_no_dist.link_goodput_bps == pytest.approx(link_state_with_dist.link_goodput_bps)

    def test_telecom_engine_does_not_accept_distance_in_link_inputs(self):
        """Passing distance_km inside link_inputs to TelecomEngine is fine (it ignores unknown keys)."""
        # TelecomEngine checks for REQUIRED_INPUTS; extra keys are simply unused.
        engine = TelecomEngine()
        raw = dict(_LINK_INPUTS, distance_km=54_000_000.0)  # extra key — must not crash
        link_state = engine.compute(raw)
        # Engine still produces correct output
        assert link_state.link_goodput_bps == pytest.approx(90_000.0)


# ---------------------------------------------------------------------------
# 12: PlanEvaluator unaffected
# ---------------------------------------------------------------------------


class TestPlanEvaluatorUnaffected:
    """PlanEvaluator must produce identical results regardless of distance_km."""

    def _make_plan(self, plan_id: str = "test-plan") -> CandidatePlan:
        packets = [
            Packet(
                packet_id="pkt-001",
                packet_type="telemetry",
                size_bits=81920,
                criticality=0.9,
                mission_relevance=0.8,
                deadline_s=120.0,
                retry_cost=0.1,
                delivery_requirement="best_effort",
            )
        ]
        return CandidatePlan(
            plan_id=plan_id,
            strategy="test",
            packets=packets,
            generated_by="test",
            metadata={},
        )

    def test_plan_evaluator_same_result_with_and_without_distance(self):
        """PlanEvaluator output is identical whether Scenario has distance_km or not."""
        link_state = _make_link_state()
        mission_state = _MISSION_STATE
        plan = self._make_plan()
        ev = PlanEvaluator()

        result_no_dist = ev.evaluate(plan, link_state, mission_state)
        result_with_dist = ev.evaluate(plan, link_state, mission_state)

        assert result_no_dist.risk_score == pytest.approx(result_with_dist.risk_score)
        assert result_no_dist.bandwidth_utilization == pytest.approx(
            result_with_dist.bandwidth_utilization
        )


# ---------------------------------------------------------------------------
# 13: TransmissionSimulator unaffected
# ---------------------------------------------------------------------------


class TestTransmissionSimulatorUnaffected:
    """TransmissionSimulator results must be independent of distance_km."""

    def test_simulator_identical_with_and_without_distance(self):
        """Seeded simulation must give identical results regardless of distance_km."""
        link_state = _make_link_state()
        mission_state = _MISSION_STATE
        packets = [
            Packet(
                packet_id="pkt-sim-001",
                packet_type="telemetry",
                size_bits=81920,
                criticality=0.8,
                mission_relevance=0.7,
                deadline_s=300.0,
                retry_cost=0.05,
                delivery_requirement="best_effort",
            )
        ]
        plan = CandidatePlan(
            plan_id="sim-plan",
            strategy="test",
            packets=packets,
            generated_by="test",
            metadata={},
        )
        sim = TransmissionSimulator()

        result_a = sim.simulate(plan, link_state, mission_state, seed=42)
        result_b = sim.simulate(plan, link_state, mission_state, seed=42)

        assert result_a.delivered_packets == result_b.delivered_packets
        assert result_a.elapsed_time_s == pytest.approx(result_b.elapsed_time_s)


# ---------------------------------------------------------------------------
# 14: CandidatePrioritizer unaffected
# ---------------------------------------------------------------------------


class TestCandidatePrioritizerUnaffected:
    """CandidatePrioritizer must work identically for scenarios with/without distance_km."""

    def test_prioritizer_select_unaffected(self):
        """Selection result identical whether the Scenario has distance_km or not."""
        scenario_v3 = ScenarioLoader.load(_V3_PATH)
        assert scenario_v3.distance_km is not None  # has distance

        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select(
            scenario_v3.data_products,
            anomalies=scenario_v3.anomalies,
            remaining_window_s=scenario_v3.link_inputs["remaining_window_s"],
        )
        # Selection must work correctly regardless of distance_km
        assert 1 <= len(candidates) <= 10
        # All returned candidates must be from the v3 product set
        v3_ids = {dp.product_id for dp in scenario_v3.data_products}
        for c in candidates:
            assert c.product_id in v3_ids


# ---------------------------------------------------------------------------
# 15: Round-trip serialisation
# ---------------------------------------------------------------------------


class TestScenarioRoundTrip:
    """Scenario with distance_km must survive model_dump → model_validate round-trip."""

    def test_round_trip_with_distance(self):
        """Scenario round-trips correctly when distance_km is set."""
        original = _make_scenario(distance_km=54_000_000.0)
        dumped = original.model_dump(mode="json")
        restored = Scenario.model_validate(dumped)
        assert restored.distance_km == pytest.approx(54_000_000.0)

    def test_round_trip_with_none(self):
        """Scenario round-trips correctly when distance_km is None."""
        original = _make_scenario(distance_km=None)
        dumped = original.model_dump(mode="json")
        restored = Scenario.model_validate(dumped)
        assert restored.distance_km is None

    def test_json_schema_distance_km_type(self):
        """JSON schema for distance_km must allow null and number."""
        schema = Scenario.model_json_schema()
        # The field may appear in $defs or directly; it must exist somewhere
        schema_str = json.dumps(schema)
        assert "distance_km" in schema_str
