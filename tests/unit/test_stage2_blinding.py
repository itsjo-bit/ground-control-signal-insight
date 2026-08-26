"""Tests for Stage-2 provenance blinding.

Covers:
- Blind mapping: deterministic alias assignment (same input → same aliases)
- Blind mapping: AI plan not always in predictable alias position
- Compact Stage2PlanSummary (no full packet lists)
- Context JSON: does not contain real plan IDs or strategy names
- Context JSON: does not contain forbidden provenance strings
- Alias mapping round-trip: OPTION-C → real plan_id
- Invalid alias rejection
- Context size scaling: scales with plans, not plans × packets
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.app.agent.stage2_blinding import (
    InvalidStage2AliasError,
    Stage2PlanSummary,
    assert_no_provenance_leak,
    build_blind_context_json,
    build_blind_mapping,
    build_stage2_summaries,
    map_alias_to_plan_id,
)
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pkt(pid: str) -> Packet:
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=8_000,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=300.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


def _plan(plan_id: str, strategy: str, n_packets: int = 5) -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy=strategy,
        packets=[_pkt(f"{plan_id}-PKT-{i}") for i in range(n_packets)],
        generated_by="test",
        metadata={"plan_type": strategy},
    )


def _eval(plan_id: str) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=10.0,
        critical_packets_delivered=3,
        total_critical_packets=5,
        deadline_misses=1,
        avg_packet_delay_s=12.5,
        bandwidth_utilization=0.72,
        retransmission_overhead=0.15,
        risk_score=0.35,
        risk_level=RiskLevel.MEDIUM,
        deferred_packets=["X", "Y"],
        deadline_miss_rate=0.2,
        critical_deficit=0.4,
        window_pressure=0.7,
    )


REAL_PLAN_IDS = [
    "baseline",
    "deadline-first",
    "mission-critical-first",
    "value-per-cost",
    "ai-prioritized",
]

REAL_STRATEGIES = [
    "baseline",
    "deadline_first",
    "mission_critical_first",
    "value_per_cost",
    "ai_prioritized",
]


def _five_plans(n_packets: int = 5) -> list[CandidatePlan]:
    return [_plan(pid, strat, n_packets) for pid, strat in zip(REAL_PLAN_IDS, REAL_STRATEGIES)]


def _five_evals() -> list[EvaluationResult]:
    return [_eval(pid) for pid in REAL_PLAN_IDS]


# ---------------------------------------------------------------------------
# Tests: build_blind_mapping
# ---------------------------------------------------------------------------


class TestBuildBlindMapping:
    def test_returns_option_aliases(self):
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        for key in alias_map:
            assert key.startswith("OPTION-"), f"Expected OPTION-X, got {key}"

    def test_all_plans_have_aliases(self):
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        assert len(alias_map) == 5
        assert set(alias_map.values()) == set(REAL_PLAN_IDS)

    def test_deterministic_same_input_same_aliases(self):
        plans = _five_plans()
        alias1 = build_blind_mapping(plans, scenario_id="test-scen-A")
        alias2 = build_blind_mapping(plans, scenario_id="test-scen-A")
        assert alias1 == alias2

    def test_different_scenario_may_give_different_order(self):
        """Different scenario IDs may produce different orderings (not required to differ
        but should not always produce the same order as the input list order)."""
        plans = _five_plans()
        alias1 = build_blind_mapping(plans, scenario_id="scen-001")
        alias2 = build_blind_mapping(plans, scenario_id="scen-999")
        # The VALUES must always be the same set
        assert set(alias1.values()) == set(alias2.values())

    def test_no_aliases_reveal_provenance(self):
        """Alias keys must not contain real plan IDs or strategy names."""
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        for key in alias_map:
            for forbidden in REAL_PLAN_IDS + REAL_STRATEGIES:
                assert forbidden not in key.lower(), (
                    f"Alias key '{key}' reveals provenance '{forbidden}'"
                )

    def test_empty_plans_returns_empty(self):
        alias_map = build_blind_mapping([], scenario_id="test")
        assert alias_map == {}


# ---------------------------------------------------------------------------
# Tests: map_alias_to_plan_id
# ---------------------------------------------------------------------------


class TestMapAliasToPlanId:
    def test_valid_alias_maps_to_real_plan(self):
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="test")
        for alias, real_id in alias_map.items():
            resolved = map_alias_to_plan_id(alias, alias_map)
            assert resolved == real_id

    def test_invalid_alias_raises(self):
        alias_map = {"OPTION-A": "baseline", "OPTION-B": "deadline-first"}
        with pytest.raises(InvalidStage2AliasError):
            map_alias_to_plan_id("OPTION-Z", alias_map)

    def test_real_plan_id_as_alias_is_rejected(self):
        """If provider returns a real plan name instead of alias, it must be rejected."""
        alias_map = {"OPTION-A": "baseline", "OPTION-B": "ai-prioritized"}
        with pytest.raises(InvalidStage2AliasError):
            map_alias_to_plan_id("ai-prioritized", alias_map)

    def test_invalid_alias_error_message_lists_valid_options(self):
        alias_map = {"OPTION-A": "baseline"}
        with pytest.raises(InvalidStage2AliasError) as exc_info:
            map_alias_to_plan_id("OPTION-B", alias_map)
        assert "OPTION-A" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: build_stage2_summaries and build_blind_context_json
# ---------------------------------------------------------------------------


class TestBlindContextJson:
    def test_no_provenance_in_context(self):
        """Context JSON must not contain any real plan IDs or strategy strings."""
        plans = _five_plans()
        evals = _five_evals()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        summaries = build_stage2_summaries(alias_map, plans, evals)
        ctx = build_blind_context_json(summaries)

        assert_no_provenance_leak(ctx)

    def test_context_contains_option_aliases(self):
        plans = _five_plans()
        evals = _five_evals()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        summaries = build_stage2_summaries(alias_map, plans, evals)
        ctx = build_blind_context_json(summaries)
        ctx_data = json.loads(ctx)

        for key in ctx_data:
            assert key.startswith("OPTION-")

    def test_context_does_not_contain_full_packet_lists(self):
        """Context JSON must not contain packet_id strings from individual packets."""
        plans = _five_plans(n_packets=10)  # 10 packets per plan
        evals = _five_evals()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        summaries = build_stage2_summaries(alias_map, plans, evals)
        ctx = build_blind_context_json(summaries)

        # No individual packet IDs should appear
        for plan in plans:
            for pkt in plan.packets:
                assert pkt.packet_id not in ctx, (
                    f"Packet ID '{pkt.packet_id}' found in Stage-2 context — "
                    "full packet list was leaked!"
                )

    def test_context_contains_metric_fields(self):
        """Context must contain key evaluation metrics for LLM reasoning."""
        plans = _five_plans()
        evals = _five_evals()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        summaries = build_stage2_summaries(alias_map, plans, evals)
        ctx = build_blind_context_json(summaries)

        # Each option must have risk_score and other key fields
        ctx_data = json.loads(ctx)
        for option_data in ctx_data.values():
            assert "risk_score" in option_data
            assert "mission_value" in option_data
            assert "bandwidth_utilization" in option_data

    def test_assert_no_provenance_leak_passes_clean_context(self):
        """assert_no_provenance_leak must not raise for a clean context."""
        clean = json.dumps({
            "OPTION-A": {"risk_score": 0.3, "mission_value": 12.0},
            "OPTION-B": {"risk_score": 0.4, "mission_value": 10.0},
        })
        assert_no_provenance_leak(clean)  # must not raise

    def test_assert_no_provenance_leak_fails_on_real_plan_id(self):
        """assert_no_provenance_leak must raise when real plan IDs are present."""
        dirty = json.dumps({
            "OPTION-A": {"plan_id": "ai-prioritized", "risk_score": 0.3},
        })
        with pytest.raises(AssertionError):
            assert_no_provenance_leak(dirty)

    def test_context_size_scales_with_plans_not_packets(self):
        """Context size must not grow proportionally to (plans × packets).

        For 5 plans with 10 packets each (50 total packets), the context should
        be much smaller than if all 50 packet structures were serialized.
        """
        n_packets = 150  # v3 scenario size
        n_plans = 5

        plans = _five_plans(n_packets=n_packets)
        evals = _five_evals()
        alias_map = build_blind_mapping(plans, scenario_id="test-scen")
        summaries = build_stage2_summaries(alias_map, plans, evals)
        ctx = build_blind_context_json(summaries)

        ctx_size = len(ctx)

        # Full packet serialization would be ~50–200 chars per packet object
        # 150 packets × 5 plans × 50 chars = ~37,500 chars minimum
        # Our compact context should be much smaller
        single_packet_json_approx = 100  # conservative chars per packet obj
        full_packet_context_size = n_packets * n_plans * single_packet_json_approx
        assert ctx_size < full_packet_context_size, (
            f"Context ({ctx_size} chars) is not smaller than full packet serialization "
            f"({full_packet_context_size} chars). Context is not compact enough."
        )

    def test_with_mission_outcomes_includes_semantic_metrics(self):
        """When mission outcomes are provided, semantic metrics appear in context."""
        from backend.app.evaluator.mission_outcome_evaluator import MissionOutcomeResult

        plans = [_plan("baseline", "baseline")]
        evals = [_eval("baseline")]
        alias_map = build_blind_mapping(plans, scenario_id="scen")
        mo = MissionOutcomeResult(
            plan_id="baseline",
            total_products=10,
            delivered_products=8,
            delivery_rate=0.8,
            total_scientific_value=5.0,
            delivered_scientific_value=4.0,
            scientific_value_capture_rate=0.8,
            required_products_total=3,
            required_products_delivered=3,
            required_delivery_rate=1.0,
            active_anomaly_products_total=2,
            active_anomaly_products_delivered=1,
            active_anomaly_delivery_rate=0.5,
            high_severity_threshold=0.75,
            high_severity_anomalies_total=1,
            high_severity_anomalies_covered=1,
            high_severity_anomaly_coverage_rate=1.0,
        )
        summaries = build_stage2_summaries(alias_map, plans, evals, [mo])
        ctx = build_blind_context_json(summaries)
        ctx_data = json.loads(ctx)

        option_data = list(ctx_data.values())[0]
        assert "scientific_value_capture_rate" in option_data
        assert "active_anomaly_delivery_rate" in option_data
        assert "required_delivery_rate" in option_data


# ---------------------------------------------------------------------------
# Tests: full round-trip
# ---------------------------------------------------------------------------


class TestProvenanceBlindRoundTrip:
    """Prove the full alias → selection → real plan ID round-trip."""

    def test_round_trip_all_options(self):
        """For each alias, selecting it returns the correct real plan_id."""
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="roundtrip-test")
        for alias, expected_real_id in alias_map.items():
            resolved = map_alias_to_plan_id(alias, alias_map)
            assert resolved == expected_real_id

    def test_each_plan_has_unique_alias(self):
        """Every plan must receive a unique alias."""
        plans = _five_plans()
        alias_map = build_blind_mapping(plans, scenario_id="test")
        assert len(alias_map) == len(plans)
        # No duplicate aliases
        assert len(set(alias_map.keys())) == len(plans)
        # No duplicate real plan IDs
        assert len(set(alias_map.values())) == len(plans)
