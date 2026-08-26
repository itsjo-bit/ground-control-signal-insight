"""Tests for benchmark analysis — Pareto, win/tie/loss, pairwise.

Covers:
- compare_metric for maximize and minimize directions
- pareto_dominates (A dom B, B dom A, neither, tie, N/A)
- compute_pareto_frontier
- aggregate_win_tie_loss
- Null metric handling (never convert None to 0 or 1)
- Win/Tie/Loss logic for signed deltas
"""

from __future__ import annotations

import pytest

from backend.app.benchmark.analysis import (
    COMPARISON_TOLERANCE,
    ComparisonResult,
    MetricDirection,
    aggregate_win_tie_loss,
    compare_metric,
    compute_pareto_frontier,
    compute_pareto_frontier_rate,
    pareto_dominates,
    pairwise_compare,
)
from backend.app.benchmark.models import (
    BenchmarkPlanResult,
    MissionOutcomeMetrics,
    PhysicalMetrics,
    PlanType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_phys(
    risk_score: float = 0.3,
    mission_value: float = 5.0,
    critical_delivery_rate: float | None = 0.8,
    deadline_miss_rate: float = 0.1,
    bandwidth_utilization: float = 0.7,
    window_pressure: float = 0.5,
    deferred_count: int = 2,
    retransmission_overhead: float = 0.1,
) -> PhysicalMetrics:
    return PhysicalMetrics(
        risk_score=risk_score,
        mission_value=mission_value,
        critical_packets_delivered=8,
        total_critical_packets=10,
        critical_delivery_rate=critical_delivery_rate,
        deadline_misses=1,
        deadline_miss_rate=deadline_miss_rate,
        bandwidth_utilization=bandwidth_utilization,
        retransmission_overhead=retransmission_overhead,
        window_pressure=window_pressure,
        deferred_count=deferred_count,
    )


def _make_mo(
    scientific_value_capture_rate: float | None = 0.75,
    required_delivery_rate: float | None = 0.9,
    active_anomaly_delivery_rate: float | None = 0.8,
    high_severity_anomaly_coverage_rate: float | None = 0.7,
    anomaly_weighted_coverage: float | None = 0.72,
) -> MissionOutcomeMetrics:
    return MissionOutcomeMetrics(
        scientific_value_capture_rate=scientific_value_capture_rate,
        required_delivery_rate=required_delivery_rate,
        active_anomaly_delivery_rate=active_anomaly_delivery_rate,
        high_severity_anomaly_coverage_rate=high_severity_anomaly_coverage_rate,
        anomaly_weighted_coverage=anomaly_weighted_coverage,
    )


def _make_result(
    plan_type: PlanType,
    scenario_id: str = "CAP035_ORIGINAL",
    repetition: int = 1,
    run_id: str = "test-run",
    phys: PhysicalMetrics | None = None,
    mo: MissionOutcomeMetrics | None = None,
) -> BenchmarkPlanResult:
    return BenchmarkPlanResult(
        run_id=run_id,
        scenario_id=scenario_id,
        repetition=repetition,
        plan_type=plan_type,
        plan_order_hash="abc123",
        physical_metrics=phys or _make_phys(),
        mission_outcome_metrics=mo or _make_mo(),
    )


# ---------------------------------------------------------------------------
# compare_metric tests
# ---------------------------------------------------------------------------


class TestCompareMetric:
    def test_maximize_win(self):
        assert compare_metric(0.8, 0.7, MetricDirection.MAXIMIZE) == ComparisonResult.WIN

    def test_maximize_loss(self):
        assert compare_metric(0.7, 0.8, MetricDirection.MAXIMIZE) == ComparisonResult.LOSS

    def test_maximize_tie_within_tolerance(self):
        assert compare_metric(0.8, 0.8 + COMPARISON_TOLERANCE / 2, MetricDirection.MAXIMIZE) == ComparisonResult.TIE

    def test_minimize_win(self):
        # Lower risk = better
        assert compare_metric(0.2, 0.4, MetricDirection.MINIMIZE) == ComparisonResult.WIN

    def test_minimize_loss(self):
        assert compare_metric(0.5, 0.4, MetricDirection.MINIMIZE) == ComparisonResult.LOSS

    def test_minimize_tie(self):
        assert compare_metric(0.3, 0.3, MetricDirection.MINIMIZE) == ComparisonResult.TIE

    def test_null_a_returns_na(self):
        assert compare_metric(None, 0.5, MetricDirection.MAXIMIZE) == ComparisonResult.NA

    def test_null_b_returns_na(self):
        assert compare_metric(0.5, None, MetricDirection.MAXIMIZE) == ComparisonResult.NA

    def test_both_null_returns_na(self):
        assert compare_metric(None, None, MetricDirection.MAXIMIZE) == ComparisonResult.NA

    def test_exact_tolerance_boundary_is_tie(self):
        # Use exact arithmetic: compare 0.0 and COMPARISON_TOLERANCE (the boundary itself)
        # abs(diff) == tolerance → should be TIE (the condition is <=)
        assert compare_metric(
            0.0, COMPARISON_TOLERANCE, MetricDirection.MAXIMIZE
        ) == ComparisonResult.TIE

    def test_beyond_tolerance_is_win(self):
        assert compare_metric(
            0.3 + COMPARISON_TOLERANCE * 2,
            0.3,
            MetricDirection.MAXIMIZE
        ) == ComparisonResult.WIN


# ---------------------------------------------------------------------------
# pareto_dominates tests
# ---------------------------------------------------------------------------


class TestParetoDominates:
    def test_a_dominates_b_clearly(self):
        # A is better on everything
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(
            risk_score=0.2, mission_value=8.0, critical_delivery_rate=0.9, deadline_miss_rate=0.05
        ), mo=_make_mo(
            scientific_value_capture_rate=0.9, required_delivery_rate=0.95,
            active_anomaly_delivery_rate=0.9, anomaly_weighted_coverage=0.85
        ))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(
            risk_score=0.4, mission_value=5.0, critical_delivery_rate=0.6, deadline_miss_rate=0.2
        ), mo=_make_mo(
            scientific_value_capture_rate=0.6, required_delivery_rate=0.7,
            active_anomaly_delivery_rate=0.6, anomaly_weighted_coverage=0.5
        ))
        assert pareto_dominates(a, b) is True
        assert pareto_dominates(b, a) is False

    def test_neither_dominates_when_trade_off(self):
        # A better on scientific_value, B better on risk
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.6, mission_value=9.0), mo=_make_mo(scientific_value_capture_rate=0.9))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.2, mission_value=5.0), mo=_make_mo(scientific_value_capture_rate=0.5))
        assert pareto_dominates(a, b) is False
        assert pareto_dominates(b, a) is False

    def test_exact_tie_does_not_dominate(self):
        a = _make_result(PlanType.AI_PRIORITIZED)
        b = _make_result(PlanType.SEMANTIC_RULE)
        # Exact same values → neither dominates (need at least one strictly better)
        assert pareto_dominates(a, b) is False
        assert pareto_dominates(b, a) is False

    def test_na_metric_ignored_in_dominance(self):
        # A better on all non-null metrics; required_delivery_rate is None for both
        a = _make_result(PlanType.AI_PRIORITIZED,
            phys=_make_phys(risk_score=0.2, mission_value=8.0, critical_delivery_rate=0.9, deadline_miss_rate=0.05),
            mo=_make_mo(required_delivery_rate=None, scientific_value_capture_rate=0.9,
                       active_anomaly_delivery_rate=0.9, anomaly_weighted_coverage=0.85))
        b = _make_result(PlanType.SEMANTIC_RULE,
            phys=_make_phys(risk_score=0.5, mission_value=4.0, critical_delivery_rate=0.5, deadline_miss_rate=0.2),
            mo=_make_mo(required_delivery_rate=None, scientific_value_capture_rate=0.5,
                       active_anomaly_delivery_rate=0.5, anomaly_weighted_coverage=0.4))
        # A dominates B even though required_delivery_rate is N/A for both
        assert pareto_dominates(a, b) is True


# ---------------------------------------------------------------------------
# compute_pareto_frontier tests
# ---------------------------------------------------------------------------


class TestComputeParetoFrontier:
    def test_single_plan_is_on_frontier(self):
        a = _make_result(PlanType.BASELINE)
        frontier = compute_pareto_frontier([a])
        assert frontier[PlanType.BASELINE.value]["is_pareto_frontier"] is True

    def test_dominated_plan_not_on_frontier(self):
        # A clearly dominates B
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(
            risk_score=0.1, mission_value=10.0, critical_delivery_rate=1.0, deadline_miss_rate=0.0
        ), mo=_make_mo(
            scientific_value_capture_rate=1.0, required_delivery_rate=1.0,
            active_anomaly_delivery_rate=1.0, anomaly_weighted_coverage=1.0
        ))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(
            risk_score=0.9, mission_value=1.0, critical_delivery_rate=0.1, deadline_miss_rate=0.9
        ), mo=_make_mo(
            scientific_value_capture_rate=0.1, required_delivery_rate=0.1,
            active_anomaly_delivery_rate=0.1, anomaly_weighted_coverage=0.1
        ))
        frontier = compute_pareto_frontier([a, b])
        assert frontier[PlanType.AI_PRIORITIZED.value]["is_pareto_frontier"] is True
        assert frontier[PlanType.SEMANTIC_RULE.value]["is_pareto_frontier"] is False
        assert frontier[PlanType.SEMANTIC_RULE.value]["plans_dominating_this_plan_count"] == 1

    def test_trade_off_both_on_frontier(self):
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.6, mission_value=9.0), mo=_make_mo(scientific_value_capture_rate=0.9))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.2, mission_value=5.0), mo=_make_mo(scientific_value_capture_rate=0.5))
        frontier = compute_pareto_frontier([a, b])
        assert frontier[PlanType.AI_PRIORITIZED.value]["is_pareto_frontier"] is True
        assert frontier[PlanType.SEMANTIC_RULE.value]["is_pareto_frontier"] is True


# ---------------------------------------------------------------------------
# Null metric tests
# ---------------------------------------------------------------------------


class TestNullMetrics:
    def test_null_vs_null_is_na(self):
        a = _make_result(PlanType.AI_PRIORITIZED, mo=_make_mo(required_delivery_rate=None))
        b = _make_result(PlanType.SEMANTIC_RULE, mo=_make_mo(required_delivery_rate=None))
        comp = pairwise_compare(a, b)
        assert comp.metric_results["required_delivery_rate"] == ComparisonResult.NA

    def test_null_vs_value_is_na(self):
        a = _make_result(PlanType.AI_PRIORITIZED, mo=_make_mo(required_delivery_rate=None))
        b = _make_result(PlanType.SEMANTIC_RULE, mo=_make_mo(required_delivery_rate=0.8))
        comp = pairwise_compare(a, b)
        assert comp.metric_results["required_delivery_rate"] == ComparisonResult.NA

    def test_na_excluded_from_win_count(self):
        a = _make_result(PlanType.AI_PRIORITIZED, mo=_make_mo(required_delivery_rate=None))
        b = _make_result(PlanType.SEMANTIC_RULE, mo=_make_mo(required_delivery_rate=None))
        comp = pairwise_compare(a, b)
        # N/A should not be counted in wins/losses
        assert comp.na_count() > 0


# ---------------------------------------------------------------------------
# Win/Tie/Loss aggregation
# ---------------------------------------------------------------------------


class TestAggreateWinTieLoss:
    def test_aggregation_counts_correctly(self):
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.2, mission_value=8.0))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.4, mission_value=6.0))
        comp = pairwise_compare(a, b)
        records = aggregate_win_tie_loss([comp])
        # risk_score: 0.2 vs 0.4, minimize → WIN for a
        key = (PlanType.AI_PRIORITIZED.value, PlanType.SEMANTIC_RULE.value, "risk_score")
        assert key in records
        assert records[key].win_count == 1
        # mission_value: 8.0 vs 6.0, maximize → WIN for a
        key2 = (PlanType.AI_PRIORITIZED.value, PlanType.SEMANTIC_RULE.value, "mission_value")
        assert records[key2].win_count == 1

    def test_loss_recorded_separately(self):
        # a has worse risk (higher is worse for minimize)
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.8))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.3))
        comp = pairwise_compare(a, b)
        records = aggregate_win_tie_loss([comp])
        key = (PlanType.AI_PRIORITIZED.value, PlanType.SEMANTIC_RULE.value, "risk_score")
        assert records[key].loss_count == 1
        assert records[key].win_count == 0


# ---------------------------------------------------------------------------
# Pareto frontier rate
# ---------------------------------------------------------------------------


class TestParetoFrontierRate:
    def test_rate_when_ai_always_on_frontier(self):
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.1, mission_value=10.0, critical_delivery_rate=1.0, deadline_miss_rate=0.0), mo=_make_mo(scientific_value_capture_rate=1.0, required_delivery_rate=1.0, active_anomaly_delivery_rate=1.0, anomaly_weighted_coverage=1.0))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.9, mission_value=1.0, critical_delivery_rate=0.1, deadline_miss_rate=0.9), mo=_make_mo(scientific_value_capture_rate=0.1, required_delivery_rate=0.1, active_anomaly_delivery_rate=0.1, anomaly_weighted_coverage=0.1))
        rate = compute_pareto_frontier_rate([[a, b]])
        assert rate["frontier_rate"] == 1.0

    def test_rate_when_ai_never_on_frontier(self):
        # b dominates a in all metrics
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.9, mission_value=1.0, critical_delivery_rate=0.1, deadline_miss_rate=0.9), mo=_make_mo(scientific_value_capture_rate=0.1, required_delivery_rate=0.1, active_anomaly_delivery_rate=0.1, anomaly_weighted_coverage=0.1))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.1, mission_value=10.0, critical_delivery_rate=1.0, deadline_miss_rate=0.0), mo=_make_mo(scientific_value_capture_rate=1.0, required_delivery_rate=1.0, active_anomaly_delivery_rate=1.0, anomaly_weighted_coverage=1.0))
        rate = compute_pareto_frontier_rate([[a, b]])
        assert rate["frontier_rate"] == 0.0
        assert rate["dominated_by_semantic_rule"] == 1

    def test_trade_off_counted_as_neither(self):
        a = _make_result(PlanType.AI_PRIORITIZED, phys=_make_phys(risk_score=0.6, mission_value=9.0), mo=_make_mo(scientific_value_capture_rate=0.9))
        b = _make_result(PlanType.SEMANTIC_RULE, phys=_make_phys(risk_score=0.2, mission_value=5.0), mo=_make_mo(scientific_value_capture_rate=0.5))
        rate = compute_pareto_frontier_rate([[a, b]])
        assert rate["neither_dominates"] == 1
