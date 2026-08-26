"""Benchmark report generator.

Generates reports from raw benchmark result files.
Reports are always generated from actual result data — never hardcoded claims.

Output:
  summary.json    — machine-readable summary
  summary.csv     — tabular summary (one row per plan per scenario)
  report.md       — human-readable Markdown report

The report MUST include a "Where the LLM did not outperform" section.
Limitations are mandatory.  No cherry-picking.  No composite AI score.

Report sections (1–14 as per benchmark methodology):
    1. Executive Summary
    2. Experimental Design
    3. Scenario Matrix
    4. Provider / Model Configuration
    5. Reliability / Failed Runs
    6. Granite vs Semantic-Rule
    7. Granite vs Classical Baselines
    8. Pareto Analysis
    9. Capacity-Stress Analysis
   10. Anomaly-Mode Analysis
   11. Ablation Study
   12. Where the LLM Did Not Outperform
   13. Limitations
   14. Reproduction Instructions
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Optional

from .analysis import (
    COMPARISON_TOLERANCE,
    ComparisonResult,
    MetricStats,
    PairwiseComparison,
    WinTieLossRecord,
    aggregate_metric_stats,
    aggregate_win_tie_loss,
    compute_ablation_analysis,
    compute_all_comparisons,
    compute_anomaly_analysis,
    compute_capacity_analysis,
    compute_pareto_frontier,
    compute_pareto_frontier_rate,
    compute_scenario_variability,
    enrich_pareto_metadata,
    filter_core_results,
    group_by_trial_id,
    pairwise_compare,
)
from .models import (
    BenchmarkManifest,
    BenchmarkPlanResult,
    BenchmarkStatus,
    BenchmarkTrial,
    ExperimentVariant,
    PRIMARY_METRICS,
    PRIMARY_METRICS_MAXIMIZE,
    PRIMARY_METRICS_MINIMIZE,
    PlanType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Raw result loader
# ---------------------------------------------------------------------------


def load_raw_results(result_dir: Path) -> tuple[list[BenchmarkTrial], list[BenchmarkPlanResult]]:
    """Load trials and plan results from raw_results.jsonl."""
    jsonl_path = result_dir / "raw_results.jsonl"
    if not jsonl_path.exists():
        return [], []

    trials: list[BenchmarkTrial] = []
    plan_results: list[BenchmarkPlanResult] = []

    with jsonl_path.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                record_type = obj.pop("record_type", None)
                if record_type == "trial":
                    trials.append(BenchmarkTrial.model_validate(obj))
                elif record_type == "plan_result":
                    plan_results.append(BenchmarkPlanResult.model_validate(obj))
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse line %d: %s", line_num, exc)

    return trials, plan_results


# ---------------------------------------------------------------------------
# Summary computation
# ---------------------------------------------------------------------------


def compute_summary(
    trials: list[BenchmarkTrial],
    plan_results: list[BenchmarkPlanResult],
) -> dict:
    """Compute the full benchmark summary dict.

    Never invented values — all numbers come from actual result data.
    Core stats use full-context (non-ablation) results only.
    """
    # Provider reliability — all trials
    successful = [t for t in trials if t.status == BenchmarkStatus.SUCCESS]
    failed = [
        t for t in trials
        if t.status not in (BenchmarkStatus.SUCCESS, BenchmarkStatus.SKIPPED)
    ]
    total = len(trials)
    success_rate = len(successful) / total if total > 0 else None

    # Core plan results (non-ablation only)
    core_plan_results = filter_core_results(plan_results)

    # Group plan results by unique trial_id for Pareto analysis
    trial_groups = group_by_trial_id(core_plan_results)
    all_trial_result_groups = list(trial_groups.values())
    pareto_rates = compute_pareto_frontier_rate(all_trial_result_groups)

    # AI plan results — core only
    ai_results = [pr for pr in core_plan_results if pr.plan_type == PlanType.AI_PRIORITIZED]

    # Per-metric AI stats
    ai_stats = {
        metric: aggregate_metric_stats(ai_results, metric).model_dump()
        for metric in PRIMARY_METRICS
    }

    # Granite vs all 5 comparators (core only, grouped by trial_id)
    all_comparisons = compute_all_comparisons(core_plan_results, core_only=False)  # already filtered

    wtl_by_comparator: dict[str, dict] = {}
    for comp_pt_val, comparisons in all_comparisons.items():
        wtl = aggregate_win_tie_loss(comparisons)
        wtl_by_comparator[comp_pt_val] = {
            f"{k[0]}_vs_{k[1]}/{k[2]}": {
                "wins": v.win_count,
                "ties": v.tie_count,
                "losses": v.loss_count,
                "na": v.na_count,
                "win_rate": v.win_rate,
                "loss_rate": v.loss_rate,
            }
            for k, v in wtl.items()
        }

    # Capacity analysis
    capacity_analysis = compute_capacity_analysis(core_plan_results)

    # Anomaly analysis
    anomaly_analysis = compute_anomaly_analysis(core_plan_results)

    # Per-scenario variability
    variability = compute_scenario_variability(core_plan_results)

    # Ablation analysis (uses all plan_results including ablations)
    ablation = compute_ablation_analysis(plan_results)

    # Attempt/retry stats
    total_attempts = sum(t.attempt_count for t in trials if t.status != BenchmarkStatus.SKIPPED)
    trials_needing_retry = sum(1 for t in trials if t.attempt_count > 1)

    return {
        "benchmark_version": trials[0].benchmark_version if trials else "unknown",
        "provider": trials[0].provider if trials else "unknown",
        "model": trials[0].actual_model_id or trials[0].model if trials else "unknown",
        "run_type": getattr(trials[0], "run_type", "unknown") if trials else "unknown",
        "total_trials": total,
        "successful_trials": len(successful),
        "failed_trials": len(failed),
        "success_rate": success_rate,
        "total_provider_attempts": total_attempts,
        "trials_requiring_retry": trials_needing_retry,
        "mean_attempts_per_trial": (total_attempts / total) if total > 0 else None,
        "pareto_analysis": pareto_rates,
        "ai_metric_stats": ai_stats,
        # Backward compat key
        "ai_vs_semantic_rule_wtl": wtl_by_comparator.get(PlanType.SEMANTIC_RULE.value, {}),
        "all_comparator_wtl": wtl_by_comparator,
        "capacity_analysis": capacity_analysis,
        "anomaly_analysis": anomaly_analysis,
        "per_scenario_variability": variability,
        "ablation_analysis": ablation,
        "comparison_tolerance": COMPARISON_TOLERANCE,
        "primary_metrics_maximize": PRIMARY_METRICS_MAXIMIZE,
        "primary_metrics_minimize": PRIMARY_METRICS_MINIMIZE,
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def write_summary_csv(plan_results: list[BenchmarkPlanResult], output_path: Path) -> None:
    """Write one CSV row per (plan, scenario, repetition).

    Pareto metadata is populated before writing via enrich_pareto_metadata.
    """
    if not plan_results:
        output_path.write_text("")
        return

    # Enrich with Pareto metadata
    enriched = enrich_pareto_metadata(plan_results)

    fieldnames = [
        "trial_id", "scenario_id", "repetition", "experiment_variant",
        "plan_type", "plan_order_hash",
        "risk_score", "mission_value", "critical_delivery_rate",
        "deadline_miss_rate", "bandwidth_utilization", "window_pressure",
        "deferred_count", "scientific_value_capture_rate",
        "required_delivery_rate", "active_anomaly_delivery_rate",
        "high_severity_anomaly_coverage_rate", "anomaly_weighted_coverage",
        "average_delivered_age_s", "delivery_rate",
        "is_pareto_frontier", "plans_dominated_count",
        "plans_dominating_this_plan_count",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for pr in enriched:
            pm = pr.physical_metrics
            mo = pr.mission_outcome_metrics
            ev = getattr(pr, "experiment_variant", ExperimentVariant.FULL)
            ev_val = ev.value if hasattr(ev, "value") else str(ev)
            writer.writerow({
                "trial_id": getattr(pr, "trial_id", pr.run_id),
                "scenario_id": pr.scenario_id,
                "repetition": pr.repetition,
                "experiment_variant": ev_val,
                "plan_type": pr.plan_type.value,
                "plan_order_hash": pr.plan_order_hash,
                "risk_score": pm.risk_score,
                "mission_value": pm.mission_value,
                "critical_delivery_rate": pm.critical_delivery_rate,
                "deadline_miss_rate": pm.deadline_miss_rate,
                "bandwidth_utilization": pm.bandwidth_utilization,
                "window_pressure": pm.window_pressure,
                "deferred_count": pm.deferred_count,
                "scientific_value_capture_rate": mo.scientific_value_capture_rate,
                "required_delivery_rate": mo.required_delivery_rate,
                "active_anomaly_delivery_rate": mo.active_anomaly_delivery_rate,
                "high_severity_anomaly_coverage_rate": mo.high_severity_anomaly_coverage_rate,
                "anomaly_weighted_coverage": mo.anomaly_weighted_coverage,
                "average_delivered_age_s": mo.average_delivered_age_s,
                "delivery_rate": mo.delivery_rate,
                "is_pareto_frontier": pr.is_pareto_frontier,
                "plans_dominated_count": pr.plans_dominated_count,
                "plans_dominating_this_plan_count": pr.plans_dominating_this_plan_count,
            })


# ---------------------------------------------------------------------------
# Markdown report generator
# ---------------------------------------------------------------------------


def _fmt(v: Optional[float], digits: int = 4) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}"


def _pct(v: Optional[float]) -> str:
    if v is None:
        return "N/A"
    return f"{v * 100:.1f}%"


def _run_type_label(summary: dict) -> str:
    rt = summary.get("run_type", "unknown")
    if rt == "pilot":
        return "PILOT RUN"
    elif rt == "dev":
        return "DEVELOPMENT RUN (non-official)"
    elif rt == "core":
        return "CORE BENCHMARK"
    return rt.upper()


def generate_markdown_report(
    summary: dict,
    trials: list[BenchmarkTrial],
    plan_results: list[BenchmarkPlanResult],
    manifest: Optional[BenchmarkManifest] = None,
) -> str:
    """Generate a complete Markdown benchmark report from actual result data.

    This function computes and formats all numbers from results — no hardcoded claims.
    Contains all 14 sections as required by the benchmark methodology.

    The report MUST include:
    - A section on where the LLM did NOT outperform
    - A limitations section
    - Reproduction instructions
    - Clear identification of run type (pilot / core / dev)
    """
    lines = []
    has_live_data = any(
        t.status not in (BenchmarkStatus.SKIPPED,) for t in trials
    )
    run_type = summary.get("run_type", "unknown")

    # ---------------------------------------------------------------------------
    # Header
    # ---------------------------------------------------------------------------
    lines.append("# GCSI Phase 2B Benchmark Report\n")

    if not has_live_data:
        lines.append("> **NOTE**: No live benchmark data exists yet.")
        lines.append("> This report contains framework validation only — no Granite inference results.\n")
    elif run_type == "pilot":
        lines.append("> **PILOT RUN**: This is a limited pilot run, not the full core benchmark.")
        lines.append("> Results are provisional and for infrastructure verification only.\n")
    else:
        lines.append("> **DATA SOURCE**: Numbers are computed from actual benchmark result data.")
        lines.append("> No claims are hardcoded.\n")

    # ---------------------------------------------------------------------------
    # 1. Executive Summary
    # ---------------------------------------------------------------------------
    lines.append("## 1. Executive Summary\n")

    provider = summary.get("provider", "unknown")
    model = summary.get("model", "unknown")
    total_trials = summary.get("total_trials", 0)
    successful = summary.get("successful_trials", 0)
    failed = summary.get("failed_trials", 0)
    success_rate = summary.get("success_rate")
    run_type_label = _run_type_label(summary)

    lines.append(f"- **Run type**: {run_type_label}")
    lines.append(f"- **Provider**: {provider}")
    lines.append(f"- **Model**: {model}")
    lines.append(f"- **Total trials**: {total_trials}")
    lines.append(f"- **Successful**: {successful}")
    lines.append(f"- **Failed**: {failed}")
    lines.append(f"- **Provider success rate**: {_pct(success_rate)}")

    pareto = summary.get("pareto_analysis", {})
    frontier_rate = pareto.get("frontier_rate")
    lines.append(f"- **Granite Pareto frontier rate (core, full-context)**: {_pct(frontier_rate)}\n")

    # ---------------------------------------------------------------------------
    # 2. Experimental Design
    # ---------------------------------------------------------------------------
    lines.append("## 2. Experimental Design\n")
    lines.append("All competitor plans are evaluated by the same deterministic")
    lines.append("`PlanEvaluator` (telecom physics) and `MissionOutcomeEvaluator`")
    lines.append("(mission-semantic outcomes).  No AI-specific scoring.  No Local")
    lines.append("fallback counted as Granite.  No composite AI score.")
    lines.append("")
    lines.append(f"- **Comparison tolerance**: {COMPARISON_TOLERANCE} (absolute, floating-point)")
    lines.append("- **Core headline statistics**: Full-context (non-ablation) trials only")
    lines.append("- **Ablation trials**: Kept separate from core statistics")
    lines.append("- **Trial grouping**: By unique trial_id (not scenario+repetition alone)\n")

    # ---------------------------------------------------------------------------
    # 3. Scenario Matrix
    # ---------------------------------------------------------------------------
    lines.append("## 3. Scenario Matrix\n")
    if manifest:
        lines.append(f"- **Capacity ratios**: {manifest.executed_capacity_ratios}")
        lines.append(f"- **Anomaly modes**: {manifest.executed_anomaly_modes}")
        lines.append(f"- **Deadline scales**: {manifest.executed_deadline_scales}")
        lines.append(f"- **Scenarios**: {manifest.scenario_matrix}\n")
    else:
        scenario_ids = sorted({t.scenario_id for t in trials})
        if scenario_ids:
            lines.append(f"Scenarios covered: {scenario_ids}\n")
        else:
            lines.append("_No scenario data available._\n")

    # ---------------------------------------------------------------------------
    # 4. Provider / Model Configuration
    # ---------------------------------------------------------------------------
    lines.append("## 4. Provider / Model Configuration\n")
    if manifest:
        lines.append(f"- **Provider**: {manifest.provider}")
        lines.append(f"- **Model ID**: {manifest.actual_model_id or manifest.model}")
        lines.append(f"- **Config ID**: {manifest.config_id or 'N/A'}")
        lines.append(f"- **Config SHA-256**: {manifest.config_sha256 or 'N/A'}")
        lines.append(f"- **Preregistered**: {manifest.preregistered}")
        if manifest.config_overrides:
            lines.append(f"- **Config overrides**: {manifest.config_overrides}")
        gen_cfg = manifest.generation_config
        if gen_cfg:
            lines.append(f"- **Decoding method**: {gen_cfg.get('decoding_method', 'N/A')}")
            lines.append(f"- **Max new tokens**: {gen_cfg.get('max_new_tokens', 'N/A')}")
        lines.append(f"- **Git commit**: {manifest.git_commit_sha}")
        if manifest.git_dirty is not None:
            lines.append(f"- **Git dirty**: {manifest.git_dirty}")
        lines.append(f"- **Run type**: {manifest.run_type}")
        lines.append(f"- **Run status**: {manifest.run_status}\n")
    else:
        if trials:
            t0 = trials[0]
            lines.append(f"- **Provider**: {t0.provider}")
            lines.append(f"- **Model**: {getattr(t0, 'actual_model_id', None) or t0.model}\n")
        else:
            lines.append("_No manifest or trial data available._\n")

    # ---------------------------------------------------------------------------
    # 5. Reliability / Failed Runs
    # ---------------------------------------------------------------------------
    lines.append("## 5. Reliability / Failed Runs\n")

    error_types: dict[str, int] = {}
    for t in trials:
        if t.status not in (BenchmarkStatus.SUCCESS, BenchmarkStatus.SKIPPED):
            et = t.error_type or t.status.value
            error_types[et] = error_types.get(et, 0) + 1

    if error_types:
        lines.append("| Error type | Count |")
        lines.append("|---|---|")
        for et, count in sorted(error_types.items()):
            lines.append(f"| {et} | {count} |")
        lines.append("")
    else:
        if total_trials > 0:
            lines.append("No provider failures recorded.\n")
        else:
            lines.append("No trials executed yet.\n")

    total_attempts = summary.get("total_provider_attempts", 0)
    trials_retry = summary.get("trials_requiring_retry", 0)
    lines.append(f"- **Total provider attempts**: {total_attempts}")
    lines.append(f"- **Trials requiring retry**: {trials_retry}")
    lines.append(f"- **Mean attempts per trial**: {_fmt(summary.get('mean_attempts_per_trial'), 2)}")
    lines.append(f"- **Failed trials** excluded from metric analysis, retained in raw data.\n")

    # ---------------------------------------------------------------------------
    # 6. Granite vs Semantic-Rule Results
    # ---------------------------------------------------------------------------
    lines.append("## 6. Granite vs Semantic-Rule Results\n")
    lines.append("_(Core full-context trials only)_\n")

    wtl = summary.get("ai_vs_semantic_rule_wtl", {})
    if wtl:
        lines.append("| Metric | Wins | Ties | Losses | N/A | Win rate | Loss rate |")
        lines.append("|---|---|---|---|---|---|---|")
        for metric in PRIMARY_METRICS:
            key = f"ai-prioritized_vs_semantic-rule-based/{metric}"
            v = wtl.get(key, {})
            row = (
                f"| {metric} "
                f"| {v.get('wins', 0)} "
                f"| {v.get('ties', 0)} "
                f"| {v.get('losses', 0)} "
                f"| {v.get('na', 0)} "
                f"| {_pct(v.get('win_rate'))} "
                f"| {_pct(v.get('loss_rate'))} |"
            )
            lines.append(row)
        lines.append("")
    else:
        lines.append("_No successful AI runs to compare._\n")

    # ---------------------------------------------------------------------------
    # 7. Granite vs Classical Baselines
    # ---------------------------------------------------------------------------
    lines.append("## 7. Granite vs Classical Baselines\n")
    lines.append("_(Core full-context trials only)_\n")

    all_comp_wtl = summary.get("all_comparator_wtl", {})
    classical_comparators = [
        ("baseline", "Baseline"),
        ("deadline-first", "Deadline-First"),
        ("mission-critical-first", "Mission-Critical-First"),
        ("value-per-cost", "Value-Per-Cost"),
    ]

    for comp_val, comp_label in classical_comparators:
        comp_wtl = all_comp_wtl.get(comp_val, {})
        if not comp_wtl:
            lines.append(f"### 7.{classical_comparators.index((comp_val, comp_label))+1}. Granite vs {comp_label}\n")
            lines.append("_No data._\n")
            continue

        lines.append(f"### Granite vs {comp_label}\n")
        lines.append("| Metric | Wins | Ties | Losses | N/A | Win rate | Loss rate |")
        lines.append("|---|---|---|---|---|---|---|")
        for metric in PRIMARY_METRICS:
            key = f"ai-prioritized_vs_{comp_val}/{metric}"
            v = comp_wtl.get(key, {})
            row = (
                f"| {metric} "
                f"| {v.get('wins', 0)} "
                f"| {v.get('ties', 0)} "
                f"| {v.get('losses', 0)} "
                f"| {v.get('na', 0)} "
                f"| {_pct(v.get('win_rate'))} "
                f"| {_pct(v.get('loss_rate'))} |"
            )
            lines.append(row)
        lines.append("")

    # ---------------------------------------------------------------------------
    # 8. Pareto Analysis
    # ---------------------------------------------------------------------------
    lines.append("## 8. Pareto Analysis\n")
    lines.append("_(Core full-context trials grouped by unique trial_id)_\n")

    lines.append(f"- Granite Pareto-frontier rate: {_pct(frontier_rate)}")
    lines.append(f"- Dominated by semantic-rule: {pareto.get('dominated_by_semantic_rule', 0)}")
    lines.append(f"- Dominates semantic-rule: {pareto.get('dominates_semantic_rule', 0)}")
    lines.append(f"- Neither dominates: {pareto.get('neither_dominates', 0)}")
    lines.append(f"- Total core trials evaluated: {pareto.get('total_trials', 0)}\n")

    # ---------------------------------------------------------------------------
    # 9. Capacity-Stress Analysis
    # ---------------------------------------------------------------------------
    lines.append("## 9. Capacity-Stress Analysis\n")

    cap_analysis = summary.get("capacity_analysis", {})
    if cap_analysis:
        for cap_label in sorted(cap_analysis.keys()):
            cap_data = cap_analysis[cap_label]
            lines.append(f"### {cap_label}\n")
            lines.append(f"- Valid trials: {cap_data.get('valid_trials', 0)}")
            pareto_c = cap_data.get("pareto", {})
            lines.append(f"- Pareto frontier rate: {_pct(pareto_c.get('frontier_rate'))}")
            lines.append("")
    else:
        lines.append("_No capacity analysis data._\n")

    # ---------------------------------------------------------------------------
    # 10. Anomaly-Mode Analysis
    # ---------------------------------------------------------------------------
    lines.append("## 10. Anomaly-Mode Analysis\n")

    anom_analysis = summary.get("anomaly_analysis", {})
    if anom_analysis:
        for mode in sorted(anom_analysis.keys()):
            anom_data = anom_analysis[mode]
            lines.append(f"### {mode}\n")
            lines.append(f"- Valid trials: {anom_data.get('valid_trials', 0)}")
            pareto_a = anom_data.get("pareto", {})
            lines.append(f"- Pareto frontier rate: {_pct(pareto_a.get('frontier_rate'))}")
            lines.append("")
    else:
        lines.append("_No anomaly-mode analysis data._\n")

    # ---------------------------------------------------------------------------
    # 11. Ablation Study
    # ---------------------------------------------------------------------------
    lines.append("## 11. Ablation Study\n")
    lines.append("> Ablation results are kept strictly separate from core statistics.\n")

    ablation = summary.get("ablation_analysis", {})
    if ablation:
        for scenario_id, abl_data in sorted(ablation.items()):
            lines.append(f"### {scenario_id}\n")
            metrics = abl_data.get("metrics", {})
            lines.append("| Metric | Full | No-Description | No-Anomaly | Δ No-Desc | Δ No-Anom |")
            lines.append("|---|---|---|---|---|---|")
            for metric in PRIMARY_METRICS:
                m = metrics.get(metric, {})
                row = (
                    f"| {metric} "
                    f"| {_fmt(m.get('full_median'))} "
                    f"| {_fmt(m.get('no_description_median'))} "
                    f"| {_fmt(m.get('no_anomaly_median'))} "
                    f"| {_fmt(m.get('delta_no_description'))} "
                    f"| {_fmt(m.get('delta_no_anomaly'))} |"
                )
                lines.append(row)
            lines.append("")
    else:
        lines.append("_No ablation data available._\n")

    # ---------------------------------------------------------------------------
    # 12. Where the LLM Did Not Outperform (REQUIRED SECTION)
    # ---------------------------------------------------------------------------
    lines.append("## 12. Where the LLM Did Not Outperform\n")
    lines.append("> **This section is required and cannot be omitted.**\n")

    # Find scenarios where AI was dominated
    core_plan_results = filter_core_results(plan_results)
    by_trial = group_by_trial_id(core_plan_results)

    dominated_cases: list[str] = []
    for trial_id, trial_results in sorted(by_trial.items()):
        ai_result = next((r for r in trial_results if r.plan_type == PlanType.AI_PRIORITIZED), None)
        if ai_result is None:
            continue
        pareto_info = compute_pareto_frontier(trial_results)
        ai_info = pareto_info.get(PlanType.AI_PRIORITIZED.value, {})
        dom_count = ai_info.get("plans_dominating_this_plan_count", 0)
        if dom_count and dom_count > 0:
            dominated_cases.append(
                f"- **{ai_result.scenario_id}** rep {ai_result.repetition}: "
                f"Granite dominated by {dom_count} plan(s)"
            )

    if dominated_cases:
        lines.append("### Scenarios where Granite was Pareto-dominated:\n")
        lines.extend(dominated_cases)
        lines.append("")
    elif successful > 0:
        lines.append("Granite was not Pareto-dominated in any successful full-context trial.\n")
        lines.append("*Note: This finding is conditional on the tested scenarios and model configuration.*\n")
    else:
        lines.append("No successful Granite trials to analyse.\n")

    # Report metrics where Granite had more losses than wins
    wtl = summary.get("ai_vs_semantic_rule_wtl", {})
    if wtl:
        loss_dominant: list[str] = []
        for metric in PRIMARY_METRICS:
            key = f"ai-prioritized_vs_semantic-rule-based/{metric}"
            v = wtl.get(key, {})
            losses = v.get("losses", 0)
            wins = v.get("wins", 0)
            if losses > wins:
                loss_dominant.append(f"- **{metric}**: {wins} wins, {losses} losses vs semantic-rule")
        if loss_dominant:
            lines.append("### Metrics where Granite had more losses than wins (vs semantic-rule):\n")
            lines.extend(loss_dominant)
            lines.append("")

    # Negative control section (CAP120_NOANOM)
    lines.append("### Negative Control: CAP120_NOANOM\n")
    lines.append("The CAP120_NOANOM scenario is a near/unconstrained condition with all")
    lines.append("anomalies resolved.  This is designed as a negative control where Granite's")
    lines.append("anomaly-context advantage should not manifest.  Metrics are reported without")
    lines.append("implying that absence of Granite advantage here is a failure.\n")

    nc_results = [
        pr for pr in core_plan_results
        if pr.scenario_id == "CAP120_NOANOM"
    ]
    if nc_results:
        ai_nc = [r for r in nc_results if r.plan_type == PlanType.AI_PRIORITIZED]
        sr_nc = [r for r in nc_results if r.plan_type == PlanType.SEMANTIC_RULE]
        if ai_nc and sr_nc:
            lines.append("| Metric | Granite median | SR median |")
            lines.append("|---|---|---|")
            for metric in PRIMARY_METRICS:
                ai_stat = aggregate_metric_stats(ai_nc, metric)
                sr_stat = aggregate_metric_stats(sr_nc, metric)
                lines.append(f"| {metric} | {_fmt(ai_stat.median)} | {_fmt(sr_stat.median)} |")
            lines.append("")
    else:
        lines.append("_CAP120_NOANOM data not available._\n")

    # Provider failures
    if failed > 0:
        lines.append(f"### Provider Failures\n- {failed} trial(s) did not complete.")
        lines.append("  These are included in reliability statistics and count against usable-trial rate.\n")

    # ---------------------------------------------------------------------------
    # 13. Limitations (REQUIRED SECTION)
    # ---------------------------------------------------------------------------
    lines.append("## 13. Limitations\n")
    lines.append("- Scenarios are controlled variants of one synthetic mission dataset")
    lines.append("  (`mission_data_v3.json`); not flight-qualified spacecraft validation.")
    lines.append("- The telecom model is intentionally simplified; conclusions apply to the")
    lines.append("  current GCSI analytical model, not real deep-space communication.")
    lines.append("- Benchmark conclusions apply only to the tested model/provider/configuration.")
    lines.append("- External LLM behavior may change with model or service updates.")
    lines.append("- This benchmark does NOT prove universal AI superiority.")
    lines.append("- The deterministic semantic comparator is a heuristic, not an optimal scheduler.")
    lines.append("- Only the tested provider (IBM Granite) is represented; results may not")
    lines.append("  generalize to other LLM families or configurations.")
    lines.append("- Statistical claims are descriptive only; no significance testing is applied.")
    lines.append("  With 5 repetitions per scenario, results are indicative, not conclusive.\n")

    # ---------------------------------------------------------------------------
    # 14. Reproduction Instructions
    # ---------------------------------------------------------------------------
    lines.append("## 14. Reproduction Instructions\n")

    if run_type == "pilot":
        lines.append("**This is a pilot run.** To reproduce this exact pilot:\n")
        lines.append("```bash")
        lines.append("# Pilot run (2 scenarios, 1 repetition each):")
        lines.append("python -m backend.app.benchmark.runner_cli \\")
        lines.append("  --config benchmarks/configs/gcsi_benchmark_v1.json \\")
        lines.append("  --suite quick --repetitions 1 --save-prompts --execute-live")
        lines.append("```")
    else:
        lines.append("```bash")
        lines.append("# Dry run (no external API calls):")
        lines.append("python -m backend.app.benchmark.runner_cli \\")
        lines.append("  --config benchmarks/configs/gcsi_benchmark_v1.json --dry-run")
        lines.append("")
        lines.append("# Core benchmark (requires Granite credentials):")
        lines.append("python -m backend.app.benchmark.runner_cli \\")
        lines.append("  --config benchmarks/configs/gcsi_benchmark_v1.json \\")
        lines.append("  --suite core --execute-live")
        lines.append("")
        lines.append("# Core + ablations:")
        lines.append("python -m backend.app.benchmark.runner_cli \\")
        lines.append("  --config benchmarks/configs/gcsi_benchmark_v1.json \\")
        lines.append("  --suite core --include-ablations --execute-live")
        lines.append("```")
    lines.append("")
    lines.append("Set environment variables: `GCSI_GRANITE_API_KEY`, `GCSI_GRANITE_PROJECT_ID`.")
    lines.append("Do NOT commit credentials to source control.\n")
    lines.append("---")
    lines.append("*Generated by GCSI Phase 2B.1 benchmark framework. "
                 "Numbers computed from actual result data.*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Write all output files
# ---------------------------------------------------------------------------


def write_benchmark_outputs(
    result_dir: Path,
    trials: list[BenchmarkTrial],
    plan_results: list[BenchmarkPlanResult],
    manifest: Optional[BenchmarkManifest] = None,
) -> None:
    """Write summary.json, summary.csv, and report.md to result_dir."""
    result_dir.mkdir(parents=True, exist_ok=True)

    summary = compute_summary(trials, plan_results)

    # JSON summary
    (result_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str),
        encoding="utf-8",
    )

    # CSV summary — Pareto metadata populated inside write_summary_csv
    write_summary_csv(plan_results, result_dir / "summary.csv")

    # Markdown report
    report_md = generate_markdown_report(summary, trials, plan_results, manifest)
    (result_dir / "report.md").write_text(report_md, encoding="utf-8")
