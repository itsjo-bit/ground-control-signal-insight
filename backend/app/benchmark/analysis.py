"""Benchmark analysis — Pareto frontier, win/tie/loss, pairwise comparison.

All comparison logic uses a documented floating-point tolerance to avoid
treating numerical noise as meaningful wins.

COMPARISON_TOLERANCE = 1e-9  (absolute tolerance for floating-point comparisons)

Metric directions:
    MAXIMIZE: mission_value, critical_delivery_rate, scientific_value_capture_rate,
              required_delivery_rate, active_anomaly_delivery_rate,
              high_severity_anomaly_coverage_rate, anomaly_weighted_coverage
    MINIMIZE: risk_score, deadline_miss_rate

Null metric policy:
    When a metric is None for either plan, the comparison result is N/A.
    N/A values are excluded from the win/tie/loss denominator.
    Never convert None to 0 or 1 for convenience.

Pareto dominance:
    Plan A Pareto-dominates Plan B when:
    - A is no worse than B on every applicable primary metric
    - A is strictly better than B on at least one applicable primary metric
    "Applicable" means the metric has non-null values for BOTH plans.

No composite AI score is produced.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .models import (
    BenchmarkPlanResult,
    MetricDirection,
    MissionOutcomeMetrics,
    PhysicalMetrics,
    PlanType,
    PRIMARY_METRICS_MAXIMIZE,
    PRIMARY_METRICS_MINIMIZE,
)

#: Absolute floating-point tolerance for benchmark comparisons.
#: Do not treat differences smaller than this as meaningful wins/losses.
COMPARISON_TOLERANCE: float = 1e-9

# Primary metrics with their directions
PRIMARY_METRICS_WITH_DIRECTION: dict[str, MetricDirection] = {
    **{m: MetricDirection.MAXIMIZE for m in PRIMARY_METRICS_MAXIMIZE},
    **{m: MetricDirection.MINIMIZE for m in PRIMARY_METRICS_MINIMIZE},
}

# All primary metric names (ordered for stable output)
PRIMARY_METRIC_NAMES = list(PRIMARY_METRICS_WITH_DIRECTION.keys())


# ---------------------------------------------------------------------------
# Comparison enum
# ---------------------------------------------------------------------------


class ComparisonResult(str, Enum):
    WIN = "WIN"       # plan_a is strictly better
    TIE = "TIE"       # within tolerance
    LOSS = "LOSS"     # plan_b is strictly better
    NA = "N/A"        # one or both values are None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_metric_value(result: BenchmarkPlanResult, metric: str) -> Optional[float]:
    """Extract a primary metric value from a BenchmarkPlanResult."""
    # Physical metrics
    phys = result.physical_metrics
    if hasattr(phys, metric):
        return getattr(phys, metric)
    # Mission outcome metrics
    mo = result.mission_outcome_metrics
    if hasattr(mo, metric):
        return getattr(mo, metric)
    return None


def compare_metric(
    val_a: Optional[float],
    val_b: Optional[float],
    direction: MetricDirection,
    tolerance: float = COMPARISON_TOLERANCE,
) -> ComparisonResult:
    """Compare two metric values from the perspective of plan_a.

    Returns:
        WIN  if val_a is strictly better than val_b (direction-aware)
        TIE  if abs(val_a - val_b) <= tolerance
        LOSS if val_b is strictly better than val_a
        N/A  if either value is None
    """
    if val_a is None or val_b is None:
        return ComparisonResult.NA

    diff = val_a - val_b
    if abs(diff) <= tolerance:
        return ComparisonResult.TIE

    if direction == MetricDirection.MAXIMIZE:
        return ComparisonResult.WIN if diff > 0 else ComparisonResult.LOSS
    elif direction == MetricDirection.MINIMIZE:
        return ComparisonResult.WIN if diff < 0 else ComparisonResult.LOSS
    else:
        # DESCRIPTIVE — report as TIE (no directional preference)
        return ComparisonResult.TIE


# ---------------------------------------------------------------------------
# Pairwise comparison
# ---------------------------------------------------------------------------


class PairwiseComparison(BaseModel):
    """Per-metric pairwise comparison between plan_a and plan_b."""
    plan_a: PlanType
    plan_b: PlanType
    scenario_id: str
    repetition: int
    metric_results: dict[str, ComparisonResult] = Field(default_factory=dict)

    def wins(self) -> int:
        return sum(1 for v in self.metric_results.values() if v == ComparisonResult.WIN)

    def ties(self) -> int:
        return sum(1 for v in self.metric_results.values() if v == ComparisonResult.TIE)

    def losses(self) -> int:
        return sum(1 for v in self.metric_results.values() if v == ComparisonResult.LOSS)

    def na_count(self) -> int:
        return sum(1 for v in self.metric_results.values() if v == ComparisonResult.NA)


def pairwise_compare(
    result_a: BenchmarkPlanResult,
    result_b: BenchmarkPlanResult,
    tolerance: float = COMPARISON_TOLERANCE,
) -> PairwiseComparison:
    """Compare all primary metrics of result_a vs result_b."""
    metric_results: dict[str, ComparisonResult] = {}
    for metric, direction in PRIMARY_METRICS_WITH_DIRECTION.items():
        val_a = _get_metric_value(result_a, metric)
        val_b = _get_metric_value(result_b, metric)
        metric_results[metric] = compare_metric(val_a, val_b, direction, tolerance)

    return PairwiseComparison(
        plan_a=result_a.plan_type,
        plan_b=result_b.plan_type,
        scenario_id=result_a.scenario_id,
        repetition=result_a.repetition,
        metric_results=metric_results,
    )


# ---------------------------------------------------------------------------
# Pareto analysis
# ---------------------------------------------------------------------------


def pareto_dominates(
    result_a: BenchmarkPlanResult,
    result_b: BenchmarkPlanResult,
    tolerance: float = COMPARISON_TOLERANCE,
) -> bool:
    """Return True if result_a Pareto-dominates result_b.

    A Pareto-dominates B when:
    - A is no worse than B on EVERY applicable primary metric
    - A is strictly better than B on AT LEAST ONE applicable primary metric

    "Applicable" = both values are non-null for that metric.
    Metrics where one or both values are None are ignored.
    """
    any_strictly_better = False
    for metric, direction in PRIMARY_METRICS_WITH_DIRECTION.items():
        val_a = _get_metric_value(result_a, metric)
        val_b = _get_metric_value(result_b, metric)
        if val_a is None or val_b is None:
            continue  # N/A metric — not applicable
        cmp = compare_metric(val_a, val_b, direction, tolerance)
        if cmp == ComparisonResult.LOSS:
            return False  # A is strictly worse on this metric — cannot dominate
        if cmp == ComparisonResult.WIN:
            any_strictly_better = True
    return any_strictly_better


def compute_pareto_frontier(
    results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
) -> dict[str, dict]:
    """Compute Pareto frontier status for each plan result.

    Args:
        results: List of BenchmarkPlanResult objects for one scenario/repetition.
        tolerance: Floating-point comparison tolerance.

    Returns:
        Dict mapping plan_type.value → {
            "is_pareto_frontier": bool,
            "plans_dominated_count": int,
            "plans_dominating_this_plan_count": int,
        }
    """
    n = len(results)
    dominated_by: dict[int, list[int]] = {i: [] for i in range(n)}
    dominates_list: dict[int, list[int]] = {i: [] for i in range(n)}

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if pareto_dominates(results[i], results[j], tolerance):
                dominates_list[i].append(j)
                dominated_by[j].append(i)

    frontier_info: dict[str, dict] = {}
    for i, result in enumerate(results):
        is_frontier = len(dominated_by[i]) == 0
        frontier_info[result.plan_type.value] = {
            "is_pareto_frontier": is_frontier,
            "plans_dominated_count": len(dominates_list[i]),
            "plans_dominating_this_plan_count": len(dominated_by[i]),
        }
    return frontier_info


# ---------------------------------------------------------------------------
# Aggregate statistics
# ---------------------------------------------------------------------------


class MetricStats(BaseModel):
    """Descriptive statistics for one metric across multiple repetitions."""
    metric: str
    values: list[float] = Field(default_factory=list)
    median: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    iqr: Optional[float] = None
    na_count: int = 0


def aggregate_metric_stats(
    results: list[BenchmarkPlanResult],
    metric: str,
) -> MetricStats:
    """Compute descriptive statistics for one metric across multiple results."""
    values = []
    na_count = 0
    for r in results:
        v = _get_metric_value(r, metric)
        if v is None:
            na_count += 1
        else:
            values.append(v)

    stats = MetricStats(metric=metric, values=values, na_count=na_count)
    if not values:
        return stats

    sorted_vals = sorted(values)
    n = len(sorted_vals)
    stats.minimum = sorted_vals[0]
    stats.maximum = sorted_vals[-1]

    # Median
    mid = n // 2
    if n % 2 == 0:
        stats.median = (sorted_vals[mid - 1] + sorted_vals[mid]) / 2.0
    else:
        stats.median = sorted_vals[mid]

    # IQR
    if n >= 4:
        q1_idx = n // 4
        q3_idx = (3 * n) // 4
        stats.iqr = sorted_vals[q3_idx] - sorted_vals[q1_idx]

    return stats


# ---------------------------------------------------------------------------
# Win/Tie/Loss frequency table
# ---------------------------------------------------------------------------


class WinTieLossRecord(BaseModel):
    """Aggregated win/tie/loss counts for one plan pair and one metric."""
    plan_a: PlanType
    plan_b: PlanType
    metric: str
    win_count: int = 0
    tie_count: int = 0
    loss_count: int = 0
    na_count: int = 0

    @property
    def total_applicable(self) -> int:
        return self.win_count + self.tie_count + self.loss_count

    @property
    def win_rate(self) -> Optional[float]:
        t = self.total_applicable
        return self.win_count / t if t > 0 else None

    @property
    def loss_rate(self) -> Optional[float]:
        t = self.total_applicable
        return self.loss_count / t if t > 0 else None


def aggregate_win_tie_loss(
    comparisons: list[PairwiseComparison],
) -> dict[tuple[str, str, str], WinTieLossRecord]:
    """Aggregate win/tie/loss across all comparisons.

    Args:
        comparisons: List of PairwiseComparison objects.

    Returns:
        Dict mapping (plan_a.value, plan_b.value, metric) → WinTieLossRecord.
    """
    records: dict[tuple[str, str, str], WinTieLossRecord] = {}
    for comp in comparisons:
        for metric, result in comp.metric_results.items():
            key = (comp.plan_a.value, comp.plan_b.value, metric)
            if key not in records:
                records[key] = WinTieLossRecord(
                    plan_a=comp.plan_a, plan_b=comp.plan_b, metric=metric
                )
            r = records[key]
            if result == ComparisonResult.WIN:
                r.win_count += 1
            elif result == ComparisonResult.TIE:
                r.tie_count += 1
            elif result == ComparisonResult.LOSS:
                r.loss_count += 1
            else:
                r.na_count += 1
    return records


# ---------------------------------------------------------------------------
# Pareto frontier rate
# ---------------------------------------------------------------------------


def compute_pareto_frontier_rate(
    all_trial_results: list[list[BenchmarkPlanResult]],
    target_plan_type: PlanType = PlanType.AI_PRIORITIZED,
    tolerance: float = COMPARISON_TOLERANCE,
) -> dict[str, float | int]:
    """Compute how often the target plan is on the Pareto frontier.

    Args:
        all_trial_results: List of plan-result lists, one per trial/scenario.
        target_plan_type:  The plan type to evaluate (default: AI_PRIORITIZED).
        tolerance:         Comparison tolerance.

    Returns:
        Dict with keys: total_trials, on_frontier, dominated_by_semantic_rule,
        dominates_semantic_rule, neither_dominates, frontier_rate.
    """
    total = 0
    on_frontier = 0
    dominated_by_sr = 0
    dominates_sr = 0
    neither = 0

    for trial_results in all_trial_results:
        # Extract target and semantic-rule results
        target = next((r for r in trial_results if r.plan_type == target_plan_type), None)
        sr = next((r for r in trial_results if r.plan_type == PlanType.SEMANTIC_RULE), None)
        if target is None:
            continue

        total += 1
        frontier_info = compute_pareto_frontier(trial_results, tolerance)
        target_info = frontier_info.get(target_plan_type.value, {})
        if target_info.get("is_pareto_frontier", False):
            on_frontier += 1

        if sr is not None:
            if pareto_dominates(sr, target, tolerance):
                dominated_by_sr += 1
            elif pareto_dominates(target, sr, tolerance):
                dominates_sr += 1
            else:
                neither += 1

    frontier_rate = on_frontier / total if total > 0 else 0.0
    return {
        "total_trials": total,
        "on_frontier": on_frontier,
        "dominated_by_semantic_rule": dominated_by_sr,
        "dominates_semantic_rule": dominates_sr,
        "neither_dominates": neither,
        "frontier_rate": frontier_rate,
    }
