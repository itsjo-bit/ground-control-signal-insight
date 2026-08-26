"""Tests for SemanticRulePrioritizer — deterministic semantic comparator.

Covers:
- No LLM call / no network dependency
- Same input → same ranking (determinism)
- Anomaly severity influences ordering (higher severity ranked first)
- Composite urgency score factors behave as documented
- All candidates are ranked (unlike LLM which may omit some)
- Empty candidate list handled gracefully
- LocalRuleBasedProvider.prioritize_candidates delegates to SemanticRulePrioritizer
- Same algorithm in both places (same inputs → same ranking)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.agent.semantic_rule_prioritizer import SemanticRulePrioritizer
from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _link(*, window: float = 600.0) -> LinkState:
    return LinkState(
        timestamp=_TS,
        snr_db=15.0,
        eb_n0_db=20.0,
        ber=1e-6,
        rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0,
        link_goodput_bps=100_000.0,
        latency_s=0.0,
        link_stability=1.0,
        remaining_window_s=window,
    )


def _mission() -> MissionState:
    return MissionState(
        mission_id="test",
        mission_phase="science",
        current_event="downlink",
        event_time_remaining_s=600.0,
        comm_window_remaining_s=600.0,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


def _cs(
    pid: str,
    *,
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    scientific_value: float = 0.5,
    deadline_s: float = 300.0,
    anomaly_id: str | None = None,
    subsystem: str = "payload",
    description: str = "",
    age_s: float = 100.0,
) -> CandidateSummary:
    return CandidateSummary(
        product_id=pid,
        product_type="telemetry",
        description=description,
        subsystem=subsystem,
        size_bits=8_000,
        criticality=criticality,
        mission_relevance=mission_relevance,
        scientific_value=scientific_value,
        deadline_s=deadline_s,
        age_s=age_s,
        anomaly_id=anomaly_id,
    )


def _anomaly(aid: str, severity: float) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=aid,
        subsystem="propulsion",
        severity=severity,
        detected_at_s=0.0,
        description=f"Anomaly {aid}",
        status="active",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSemanticRulePrioritizerDeterminism:
    """Same input → same ranking."""

    def test_same_input_same_ranking(self):
        prioritizer = SemanticRulePrioritizer()
        candidates = [
            _cs("A", criticality=0.9, mission_relevance=0.8),
            _cs("B", criticality=0.5, mission_relevance=0.6),
            _cs("C", criticality=0.3, mission_relevance=0.4),
        ]
        anom = [_anomaly("X", 0.8)]

        result1 = prioritizer.prioritize(candidates, anomalies=anom)
        result2 = prioritizer.prioritize(candidates, anomalies=anom)

        ids1 = [r.product_id for r in result1.ranked_products]
        ids2 = [r.product_id for r in result2.ranked_products]
        assert ids1 == ids2

    def test_all_candidates_are_ranked(self):
        """SemanticRulePrioritizer ranks every candidate (unlike an LLM)."""
        prioritizer = SemanticRulePrioritizer()
        candidates = [_cs(f"P{i}") for i in range(10)]
        result = prioritizer.prioritize(candidates)
        assert len(result.ranked_products) == 10
        ranked_ids = {r.product_id for r in result.ranked_products}
        assert ranked_ids == {f"P{i}" for i in range(10)}

    def test_no_duplicate_priorities(self):
        prioritizer = SemanticRulePrioritizer()
        candidates = [_cs(f"P{i}") for i in range(5)]
        result = prioritizer.prioritize(candidates)
        priorities = [r.priority for r in result.ranked_products]
        assert len(priorities) == len(set(priorities))

    def test_priorities_start_at_1(self):
        prioritizer = SemanticRulePrioritizer()
        candidates = [_cs("A"), _cs("B"), _cs("C")]
        result = prioritizer.prioritize(candidates)
        priorities = sorted(r.priority for r in result.ranked_products)
        assert priorities[0] == 1

    def test_empty_candidates_returns_empty(self):
        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([])
        assert result.ranked_products == []
        assert result.candidate_count == 0


class TestSemanticRuleOrdering:
    """Ordering follows documented algorithm."""

    def test_anomaly_linked_ranked_first(self):
        """Products linked to active anomalies must rank before non-anomaly products."""
        prioritizer = SemanticRulePrioritizer()
        anom = _anomaly("ANOM-X", severity=0.8)
        normal = _cs("NORMAL", criticality=0.95, mission_relevance=0.95)
        anomaly_linked = _cs("ANOM-PRODUCT", anomaly_id="ANOM-X", criticality=0.1)
        result = prioritizer.prioritize([normal, anomaly_linked], anomalies=[anom])
        ranked_ids = [r.product_id for r in result.ranked_products]
        assert ranked_ids[0] == "ANOM-PRODUCT", (
            "Anomaly-linked product must rank first regardless of other scores"
        )

    def test_higher_severity_anomaly_ranked_before_lower(self):
        """Between two anomaly-linked products, higher severity anomaly wins."""
        prioritizer = SemanticRulePrioritizer()
        anom_high = _anomaly("ANOM-HIGH", severity=0.95)
        anom_low = _anomaly("ANOM-LOW", severity=0.3)
        p_high = _cs("P-HIGH", anomaly_id="ANOM-HIGH")
        p_low = _cs("P-LOW", anomaly_id="ANOM-LOW")
        result = prioritizer.prioritize([p_low, p_high], anomalies=[anom_high, anom_low])
        ranked_ids = [r.product_id for r in result.ranked_products]
        assert ranked_ids[0] == "P-HIGH"
        assert ranked_ids[1] == "P-LOW"

    def test_higher_criticality_ranked_before_lower_among_non_anomaly(self):
        """Among non-anomaly products, higher criticality wins."""
        prioritizer = SemanticRulePrioritizer()
        p_high = _cs("HIGH", criticality=0.9, mission_relevance=0.5)
        p_low = _cs("LOW", criticality=0.1, mission_relevance=0.5)
        result = prioritizer.prioritize([p_low, p_high])
        ranked_ids = [r.product_id for r in result.ranked_products]
        assert ranked_ids[0] == "HIGH"

    def test_deadline_urgency_increases_ranking(self):
        """Short deadline (< 600s) should boost urgency and rank earlier."""
        prioritizer = SemanticRulePrioritizer()
        p_urgent = _cs("URGENT", criticality=0.5, deadline_s=10.0)
        p_normal = _cs("NORMAL", criticality=0.5, deadline_s=590.0)
        result = prioritizer.prioritize([p_normal, p_urgent])
        ranked_ids = [r.product_id for r in result.ranked_products]
        assert ranked_ids[0] == "URGENT"

    def test_tiebreak_by_product_id(self):
        """Ties are broken lexicographically by product_id for determinism."""
        prioritizer = SemanticRulePrioritizer()
        # Exactly equal on all scored dimensions
        p_b = _cs("BBB", criticality=0.5, mission_relevance=0.5, scientific_value=0.5, deadline_s=300.0)
        p_a = _cs("AAA", criticality=0.5, mission_relevance=0.5, scientific_value=0.5, deadline_s=300.0)
        result = prioritizer.prioritize([p_b, p_a])
        ranked_ids = [r.product_id for r in result.ranked_products]
        assert ranked_ids[0] == "AAA"  # lexicographically first

    def test_description_forwarded_from_candidate(self):
        """Description from CandidateSummary must appear in RankedProduct."""
        prioritizer = SemanticRulePrioritizer()
        p = _cs("P1", description="Thruster chamber pressure")
        result = prioritizer.prioritize([p])
        assert result.ranked_products[0].description == "Thruster chamber pressure"

    def test_authoritative_subsystem_in_output(self):
        """subsystem in RankedProduct must match the CandidateSummary."""
        prioritizer = SemanticRulePrioritizer()
        p = _cs("P1", subsystem="thermal")
        result = prioritizer.prioritize([p])
        assert result.ranked_products[0].subsystem == "thermal"


class TestLocalProviderDelegatesToSemanticRule:
    """LocalRuleBasedProvider.prioritize_candidates delegates to SemanticRulePrioritizer."""

    def test_local_provider_produces_same_ranking_as_prioritizer(self):
        """LocalProvider and SemanticRulePrioritizer rank identically."""
        candidates = [
            _cs("A", criticality=0.9, anomaly_id="ANOM-X"),
            _cs("B", criticality=0.7),
            _cs("C", criticality=0.3),
        ]
        anomalies = [_anomaly("ANOM-X", severity=0.8)]

        prioritizer = SemanticRulePrioritizer()
        direct_result = prioritizer.prioritize(candidates, anomalies=anomalies)

        provider = LocalRuleBasedProvider()
        local_result = provider.prioritize_candidates(
            candidates, _link(), _mission(), anomalies=anomalies
        )

        direct_order = [r.product_id for r in direct_result.ranked_products]
        local_order = [r.product_id for r in local_result.ranked_products]
        assert direct_order == local_order

    def test_local_provider_empty_candidates(self):
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates([], _link(), _mission())
        assert result.ranked_products == []


class TestSemanticRuleComparatorPlanBuilding:
    """semantic-rule-based comparator plan infrastructure."""

    def test_build_semantic_rule_plan_contains_all_packets(self):
        """SemanticRulePlan must contain every original packet exactly once."""
        from backend.app.candidate_generator.semantic_rule_plan_builder import (
            build_semantic_rule_plan,
            SEMANTIC_RULE_PLAN_ID,
            SEMANTIC_RULE_PLAN_STRATEGY,
        )
        from backend.app.models.packet import Packet
        from backend.app.config import SchedulerWeights

        packets = [
            Packet(
                packet_id=f"P{i:03d}",
                packet_type="telemetry",
                size_bits=8_000,
                criticality=float(i % 10) / 10.0,
                mission_relevance=0.5,
                deadline_s=300.0,
                retry_cost=0.1,
                delivery_requirement="best_effort",
            )
            for i in range(20)
        ]
        candidates = [
            _cs(f"P{i:03d}", criticality=float(i % 10) / 10.0)
            for i in range(10)  # Only 10 candidates
        ]
        anomalies = [_anomaly("ANOM-T", severity=0.9)]

        plan = build_semantic_rule_plan(
            packets, candidates, anomalies, _link(), _mission(), SchedulerWeights()
        )

        assert plan.plan_id == SEMANTIC_RULE_PLAN_ID
        assert plan.strategy == SEMANTIC_RULE_PLAN_STRATEGY
        assert len(plan.packets) == 20
        assert len({p.packet_id for p in plan.packets}) == 20
        assert {p.packet_id for p in plan.packets} == {f"P{i:03d}" for i in range(20)}

    def test_semantic_rule_plan_not_in_normal_workflow(self):
        """Verify semantic-rule-based is separate from the 4 normal deterministic plans."""
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.models.packet import Packet
        from backend.app.config import SchedulerWeights
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState

        packets = [
            Packet(
                packet_id=f"P{i}",
                packet_type="telemetry",
                size_bits=8_000,
                criticality=0.5,
                mission_relevance=0.5,
                deadline_s=300.0,
                retry_cost=0.1,
                delivery_requirement="best_effort",
            )
            for i in range(5)
        ]
        plans = CandidateGenerator.generate(packets, _link(), _mission(), SchedulerWeights())
        plan_ids = {p.plan_id for p in plans}
        assert "semantic-rule-based" not in plan_ids, (
            "semantic-rule-based must NOT appear in normal 4-plan generation"
        )
