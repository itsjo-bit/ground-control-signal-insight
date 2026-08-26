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

Trial grouping:
    Analysis always groups by trial_id (the unique trial identifier).
    Ablation trials (experiment_variant != FULL) are NEVER mixed with core trials.
    Core headline statistics use experiment_variant=FULL only.

No composite AI score is produced.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .models import (
    BenchmarkPlanResult,
    ExperimentVariant,
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

# Capacity level labels by ratio
_CAPACITY_LABELS: dict[float, str] = {
    0.35: "CAP035",
    0.60: "CAP060",
    0.90: "CAP090",
    1.20: "CAP120",
}


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
    trial_id: str = ""          # unique trial identifier
    scenario_id: str
    repetition: int
    experiment_variant: str = ExperimentVariant.FULL.value
    metric_results: dict[str, ComparisonResult] = Field(default_factory=dict)
    # Raw deltas: Granite_value - comparator_value (direction recorded separately)
    raw_deltas: dict[str, Optional[float]] = Field(default_factory=dict)

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
    raw_deltas: dict[str, Optional[float]] = {}
    for metric, direction in PRIMARY_METRICS_WITH_DIRECTION.items():
        val_a = _get_metric_value(result_a, metric)
        val_b = _get_metric_value(result_b, metric)
        metric_results[metric] = compare_metric(val_a, val_b, direction, tolerance)
        # Always record raw_delta = a - b (regardless of direction)
        if val_a is not None and val_b is not None:
            raw_deltas[metric] = val_a - val_b
        else:
            raw_deltas[metric] = None

    # Use trial_id from result_a if available
    trial_id = getattr(result_a, "trial_id", result_a.run_id)
    ev = getattr(result_a, "experiment_variant", ExperimentVariant.FULL)
    ev_val = ev.value if hasattr(ev, "value") else str(ev)

    return PairwiseComparison(
        plan_a=result_a.plan_type,
        plan_b=result_b.plan_type,
        trial_id=trial_id,
        scenario_id=result_a.scenario_id,
        repetition=result_a.repetition,
        experiment_variant=ev_val,
        metric_results=metric_results,
        raw_deltas=raw_deltas,
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
        results: List of BenchmarkPlanResult objects for one unique trial.
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


def group_by_trial_id(
    plan_results: list[BenchmarkPlanResult],
) -> dict[str, list[BenchmarkPlanResult]]:
    """Group plan results by their unique trial_id.

    This is the correct grouping for Pareto and pairwise analyses.
    Ablation trials have different trial_ids than full-context trials.
    """
    groups: dict[str, list[BenchmarkPlanResult]] = {}
    for pr in plan_results:
        # Use trial_id when available, fall back to run_id
        tid = getattr(pr, "trial_id", None) or pr.run_id
        groups.setdefault(tid, []).append(pr)
    return groups


def filter_core_results(
    plan_results: list[BenchmarkPlanResult],
) -> list[BenchmarkPlanResult]:
    """Return only full-context (non-ablation) plan results for core analysis."""
    core = []
    for pr in plan_results:
        ev = getattr(pr, "experiment_variant", ExperimentVariant.FULL)
        ev_val = ev.value if hasattr(ev, "value") else str(ev)
        if ev_val == ExperimentVariant.FULL.value:
            core.append(pr)
    return core


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
    """Compute descriptive statistics for one metric across multiple results.

    IQR uses inclusive quartile method: Q1 = floor(n/4), Q3 = floor(3n/4).
    """
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

    # IQR — requires at least 4 values for a meaningful result
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

    Each inner list must represent exactly ONE unique trial (grouped by trial_id).
    Ablation trials must be filtered out before calling this function.

    Args:
        all_trial_results: List of plan-result lists, one per unique trial.
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


# ---------------------------------------------------------------------------
# Populate Pareto metadata on plan results (for CSV export)
# ---------------------------------------------------------------------------


def enrich_pareto_metadata(
    plan_results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
) -> list[BenchmarkPlanResult]:
    """Populate is_pareto_frontier and domination counts on plan results.

    Groups by trial_id (unique trial identity) before computing Pareto.
    Returns a new list of plan results with Pareto fields populated.
    """
    by_trial = group_by_trial_id(plan_results)
    enriched: list[BenchmarkPlanResult] = []

    for trial_id, trial_results in by_trial.items():
        frontier_info = compute_pareto_frontier(trial_results, tolerance)
        for pr in trial_results:
            info = frontier_info.get(pr.plan_type.value, {})
            updated = pr.model_copy(update={
                "is_pareto_frontier": info.get("is_pareto_frontier"),
                "plans_dominated_count": info.get("plans_dominated_count"),
                "plans_dominating_this_plan_count": info.get("plans_dominating_this_plan_count"),
            })
            enriched.append(updated)

    return enriched


# ---------------------------------------------------------------------------
# Multi-comparator pairwise analysis (Granite vs all 5 comparators)
# ---------------------------------------------------------------------------


_AI_PLAN_TYPES = {PlanType.AI_PRIORITIZED, PlanType.AI_NO_DESCRIPTION, PlanType.AI_NO_ANOMALY_CONTEXT}

_COMPARATOR_PLAN_TYPES = [
    PlanType.SEMANTIC_RULE,
    PlanType.BASELINE,
    PlanType.DEADLINE_FIRST,
    PlanType.MISSION_CRITICAL_FIRST,
    PlanType.VALUE_PER_COST,
]


def compute_all_comparisons(
    plan_results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
    *,
    core_only: bool = True,
) -> dict[str, list[PairwiseComparison]]:
    """Compute Granite vs all comparator pairwise comparisons.

    Groups by trial_id to ensure only plans from the same trial are paired.
    When core_only=True, only full-context (non-ablation) trials are included.

    Returns:
        Dict mapping comparator plan_type.value → list of PairwiseComparison.
    """
    if core_only:
        plan_results = filter_core_results(plan_results)

    by_trial = group_by_trial_id(plan_results)
    comparisons_by_comparator: dict[str, list[PairwiseComparison]] = {
        pt.value: [] for pt in _COMPARATOR_PLAN_TYPES
    }

    for trial_id, trial_results in by_trial.items():
        # Find the AI result for this trial (full context only)
        ai_result = next(
            (r for r in trial_results if r.plan_type == PlanType.AI_PRIORITIZED), None
        )
        if ai_result is None:
            continue

        for comp_pt in _COMPARATOR_PLAN_TYPES:
            comp_result = next(
                (r for r in trial_results if r.plan_type == comp_pt), None
            )
            if comp_result is None:
                continue
            cmp = pairwise_compare(ai_result, comp_result, tolerance)
            comparisons_by_comparator[comp_pt.value].append(cmp)

    return comparisons_by_comparator


# ---------------------------------------------------------------------------
# Capacity-stress analysis
# ---------------------------------------------------------------------------


def _capacity_label(scenario_id: str) -> str:
    """Extract capacity label from scenario ID, e.g. 'CAP035'."""
    if scenario_id.startswith("CAP"):
        return scenario_id[:6]  # e.g. "CAP035"
    return "UNKNOWN"


def compute_capacity_analysis(
    plan_results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
) -> dict[str, dict]:
    """Aggregate analysis by capacity level (CAP035, CAP060, CAP090, CAP120).

    Uses only full-context (core) results.

    Returns:
        Dict mapping capacity_label → analysis dict.
    """
    core_results = filter_core_results(plan_results)
    by_capacity: dict[str, list[BenchmarkPlanResult]] = {}
    for pr in core_results:
        label = _capacity_label(pr.scenario_id)
        by_capacity.setdefault(label, []).append(pr)

    result: dict[str, dict] = {}
    for cap_label, results in by_capacity.items():
        ai_results = [r for r in results if r.plan_type == PlanType.AI_PRIORITIZED]
        sr_results = [r for r in results if r.plan_type == PlanType.SEMANTIC_RULE]

        # Group by trial_id for Pareto
        by_trial = group_by_trial_id(results)
        ai_trial_groups = []
        for tid, trial_res in by_trial.items():
            if any(r.plan_type == PlanType.AI_PRIORITIZED for r in trial_res):
                ai_trial_groups.append(trial_res)

        pareto = compute_pareto_frontier_rate(ai_trial_groups, tolerance=tolerance)

        # Pairwise AI vs SR
        ai_vs_sr = []
        sr_by_trial = {
            getattr(r, "trial_id", r.run_id): r for r in sr_results
        }
        for ai_r in ai_results:
            tid = getattr(ai_r, "trial_id", ai_r.run_id)
            sr_r = sr_by_trial.get(tid)
            if sr_r:
                ai_vs_sr.append(pairwise_compare(ai_r, sr_r, tolerance))

        wtl = aggregate_win_tie_loss(ai_vs_sr)

        # Median AI metrics
        ai_metric_stats = {}
        for metric in PRIMARY_METRIC_NAMES:
            stats = aggregate_metric_stats(ai_results, metric)
            ai_metric_stats[metric] = {
                "median": stats.median,
                "min": stats.minimum,
                "max": stats.maximum,
            }

        result[cap_label] = {
            "valid_trials": len(ai_results),
            "pareto": pareto,
            "ai_metric_stats": ai_metric_stats,
            "ai_vs_sr_wtl_count": {
                metric: {
                    "wins": rec.win_count,
                    "ties": rec.tie_count,
                    "losses": rec.loss_count,
                    "win_rate": rec.win_rate,
                }
                for (a, b, metric), rec in wtl.items()
                if a == PlanType.AI_PRIORITIZED.value and b == PlanType.SEMANTIC_RULE.value
            },
        }

    return result


# ---------------------------------------------------------------------------
# Anomaly-mode analysis
# ---------------------------------------------------------------------------


def _anomaly_mode_label(scenario_id: str) -> str:
    """Extract anomaly mode from scenario ID, e.g. 'ORIGINAL'."""
    # scenario IDs like CAP035_ORIGINAL, CAP060_NOANOM, CAP090_DECOY
    parts = scenario_id.split("_", 1)
    if len(parts) > 1:
        return parts[1]  # e.g. "ORIGINAL" or "NOANOM" or "DECOY"
    return "UNKNOWN"


def compute_anomaly_analysis(
    plan_results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
) -> dict[str, dict]:
    """Aggregate analysis by anomaly mode (ORIGINAL, NOANOM, DECOY).

    Uses only full-context (core) results.
    """
    core_results = filter_core_results(plan_results)
    by_mode: dict[str, list[BenchmarkPlanResult]] = {}
    for pr in core_results:
        mode = _anomaly_mode_label(pr.scenario_id)
        by_mode.setdefault(mode, []).append(pr)

    result: dict[str, dict] = {}
    for mode, results in by_mode.items():
        ai_results = [r for r in results if r.plan_type == PlanType.AI_PRIORITIZED]
        sr_results = [r for r in results if r.plan_type == PlanType.SEMANTIC_RULE]

        by_trial = group_by_trial_id(results)
        ai_trial_groups = [
            trial_res for tid, trial_res in by_trial.items()
            if any(r.plan_type == PlanType.AI_PRIORITIZED for r in trial_res)
        ]
        pareto = compute_pareto_frontier_rate(ai_trial_groups, tolerance=tolerance)

        ai_vs_sr = []
        sr_by_trial = {getattr(r, "trial_id", r.run_id): r for r in sr_results}
        for ai_r in ai_results:
            tid = getattr(ai_r, "trial_id", ai_r.run_id)
            sr_r = sr_by_trial.get(tid)
            if sr_r:
                ai_vs_sr.append(pairwise_compare(ai_r, sr_r, tolerance))

        wtl = aggregate_win_tie_loss(ai_vs_sr)

        result[mode] = {
            "valid_trials": len(ai_results),
            "pareto": pareto,
            "ai_vs_sr_wtl_count": {
                metric: {
                    "wins": rec.win_count,
                    "ties": rec.tie_count,
                    "losses": rec.loss_count,
                    "win_rate": rec.win_rate,
                }
                for (a, b, metric), rec in wtl.items()
                if a == PlanType.AI_PRIORITIZED.value and b == PlanType.SEMANTIC_RULE.value
            },
        }

    return result


# ---------------------------------------------------------------------------
# Per-scenario variability
# ---------------------------------------------------------------------------


def compute_scenario_variability(
    plan_results: list[BenchmarkPlanResult],
    target_plan_type: PlanType = PlanType.AI_PRIORITIZED,
) -> dict[str, dict]:
    """For each scenario, report variability across repetitions for the target plan.

    Uses only full-context (core) results.

    Returns:
        Dict mapping scenario_id → {metric → MetricStats, valid_count, failed_count}
    """
    core_results = filter_core_results(plan_results)
    by_scenario: dict[str, list[BenchmarkPlanResult]] = {}
    for pr in core_results:
        if pr.plan_type == target_plan_type:
            by_scenario.setdefault(pr.scenario_id, []).append(pr)

    result: dict[str, dict] = {}
    for scenario_id, results in sorted(by_scenario.items()):
        metric_stats: dict[str, dict] = {}
        for metric in PRIMARY_METRIC_NAMES:
            stats = aggregate_metric_stats(results, metric)
            metric_stats[metric] = {
                "median": stats.median,
                "min": stats.minimum,
                "max": stats.maximum,
                "iqr": stats.iqr,
                "n": len(stats.values),
                "na_count": stats.na_count,
            }
        result[scenario_id] = {
            "valid_repetition_count": len(results),
            "metrics": metric_stats,
        }

    return result


# ---------------------------------------------------------------------------
# Ablation analysis
# ---------------------------------------------------------------------------


def compute_ablation_analysis(
    plan_results: list[BenchmarkPlanResult],
    tolerance: float = COMPARISON_TOLERANCE,
) -> dict[str, dict]:
    """Compare FULL vs NO_DESCRIPTION vs NO_ANOMALY_CONTEXT ablations.

    Groups results by scenario_id and repetition, comparing the three
    AI plan variants against the same deterministic SR controls.

    Returns:
        Dict mapping scenario_id → {
            metric → {full_median, no_desc_median, no_anom_median, delta_no_desc, delta_no_anom}
        }
    """
    # Group by scenario and experiment variant
    by_scenario_variant: dict[tuple[str, str], list[BenchmarkPlanResult]] = {}

    for pr in plan_results:
        ev = getattr(pr, "experiment_variant", ExperimentVariant.FULL)
        ev_val = ev.value if hasattr(ev, "value") else str(ev)
        key = (pr.scenario_id, ev_val)
        by_scenario_variant.setdefault(key, []).append(pr)

    # Find ablation scenarios (those that have non-FULL variants)
    scenarios_with_ablations: set[str] = set()
    for (scenario_id, ev_val) in by_scenario_variant:
        if ev_val != ExperimentVariant.FULL.value:
            scenarios_with_ablations.add(scenario_id)

    result: dict[str, dict] = {}
    for scenario_id in sorted(scenarios_with_ablations):
        full_ai = [
            r for r in by_scenario_variant.get((scenario_id, ExperimentVariant.FULL.value), [])
            if r.plan_type == PlanType.AI_PRIORITIZED
        ]
        no_desc_ai = [
            r for r in by_scenario_variant.get((scenario_id, ExperimentVariant.NO_DESCRIPTION.value), [])
            if r.plan_type == PlanType.AI_NO_DESCRIPTION
        ]
        no_anom_ai = [
            r for r in by_scenario_variant.get((scenario_id, ExperimentVariant.NO_ANOMALY_CONTEXT.value), [])
            if r.plan_type == PlanType.AI_NO_ANOMALY_CONTEXT
        ]

        metric_summary: dict[str, dict] = {}
        for metric in PRIMARY_METRIC_NAMES:
            full_stats = aggregate_metric_stats(full_ai, metric)
            no_desc_stats = aggregate_metric_stats(no_desc_ai, metric)
            no_anom_stats = aggregate_metric_stats(no_anom_ai, metric)

            full_med = full_stats.median
            no_desc_med = no_desc_stats.median
            no_anom_med = no_anom_stats.median

            delta_no_desc = (no_desc_med - full_med) if (no_desc_med is not None and full_med is not None) else None
            delta_no_anom = (no_anom_med - full_med) if (no_anom_med is not None and full_med is not None) else None

            metric_summary[metric] = {
                "full_median": full_med,
                "no_description_median": no_desc_med,
                "no_anomaly_median": no_anom_med,
                "delta_no_description": delta_no_desc,
                "delta_no_anomaly": delta_no_anom,
                "full_valid_count": len(full_ai),
                "no_description_valid_count": len(no_desc_ai),
                "no_anomaly_valid_count": len(no_anom_ai),
            }

        result[scenario_id] = {"metrics": metric_summary}

    return result
