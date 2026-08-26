"""Benchmark report generator.

Generates reports from raw benchmark result files.
Reports are always generated from actual result data — never hardcoded claims.

Output:
  summary.json    — machine-readable summary
  summary.csv     — tabular summary (one row per plan per scenario)
  report.md       — human-readable Markdown report

The report MUST include a "Where the LLM did not outperform" section.
Limitations are mandatory.  No cherry-picking.  No composite AI score.
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
    compute_pareto_frontier,
    compute_pareto_frontier_rate,
    pairwise_compare,
)
from .models import (
    BenchmarkManifest,
    BenchmarkPlanResult,
    BenchmarkStatus,
    BenchmarkTrial,
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
    """
    # Provider reliability
    successful = [t for t in trials if t.status == BenchmarkStatus.SUCCESS]
    failed = [t for t in trials if t.status != BenchmarkStatus.SUCCESS and t.status != BenchmarkStatus.SKIPPED]
    total = len(trials)
    success_rate = len(successful) / total if total > 0 else None

    # Group plan results by (scenario_id, repetition) for pareto analysis
    scenario_rep_results: dict[tuple[str, int], list[BenchmarkPlanResult]] = {}
    for pr in plan_results:
        key = (pr.scenario_id, pr.repetition)
        scenario_rep_results.setdefault(key, []).append(pr)

    # Pareto frontier analysis
    all_trial_result_groups = list(scenario_rep_results.values())
    pareto_rates = compute_pareto_frontier_rate(all_trial_result_groups)

    # AI plan results only
    ai_results = [pr for pr in plan_results if pr.plan_type == PlanType.AI_PRIORITIZED]
    sr_results = [pr for pr in plan_results if pr.plan_type == PlanType.SEMANTIC_RULE]

    # Per-metric AI stats
    ai_stats = {
        metric: aggregate_metric_stats(ai_results, metric).model_dump()
        for metric in PRIMARY_METRICS
    }

    # Pairwise comparisons: AI vs semantic-rule
    ai_vs_sr_comparisons: list[PairwiseComparison] = []
    sr_by_key = {(pr.scenario_id, pr.repetition): pr for pr in sr_results}
    for ai_pr in ai_results:
        sr_pr = sr_by_key.get((ai_pr.scenario_id, ai_pr.repetition))
        if sr_pr is not None:
            ai_vs_sr_comparisons.append(pairwise_compare(ai_pr, sr_pr))

    # Win/Tie/Loss aggregation
    wtl = aggregate_win_tie_loss(ai_vs_sr_comparisons)
    wtl_summary = {
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

    return {
        "benchmark_version": trials[0].benchmark_version if trials else "unknown",
        "provider": trials[0].provider if trials else "unknown",
        "model": trials[0].model if trials else "unknown",
        "total_trials": total,
        "successful_trials": len(successful),
        "failed_trials": len(failed),
        "success_rate": success_rate,
        "pareto_analysis": pareto_rates,
        "ai_metric_stats": ai_stats,
        "ai_vs_semantic_rule_wtl": wtl_summary,
        "comparison_tolerance": COMPARISON_TOLERANCE,
        "primary_metrics_maximize": PRIMARY_METRICS_MAXIMIZE,
        "primary_metrics_minimize": PRIMARY_METRICS_MINIMIZE,
    }


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------


def write_summary_csv(plan_results: list[BenchmarkPlanResult], output_path: Path) -> None:
    """Write one CSV row per (plan, scenario, repetition)."""
    if not plan_results:
        output_path.write_text("")
        return

    fieldnames = [
        "scenario_id", "repetition", "plan_type", "plan_order_hash",
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
        for pr in plan_results:
            pm = pr.physical_metrics
            mo = pr.mission_outcome_metrics
            writer.writerow({
                "scenario_id": pr.scenario_id,
                "repetition": pr.repetition,
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


def generate_markdown_report(
    summary: dict,
    trials: list[BenchmarkTrial],
    plan_results: list[BenchmarkPlanResult],
    manifest: Optional[BenchmarkManifest] = None,
) -> str:
    """Generate a complete Markdown benchmark report from actual result data.

    This function computes and formats all numbers from results — no hardcoded claims.

    The report MUST include:
    - A section on where the LLM did NOT outperform
    - A limitations section
    - Reproduction instructions
    """
    lines = []

    # ---------------------------------------------------------------------------
    # 1. Executive Summary
    # ---------------------------------------------------------------------------
    lines.append("# GCSI Phase 2B Benchmark Report\n")
    lines.append("> **IMPORTANT**: This report was generated from actual benchmark results.")
    lines.append("> Numbers are computed from raw data — no claims are hardcoded.\n")
    lines.append("## 1. Executive Summary\n")

    provider = summary.get("provider", "unknown")
    model = summary.get("model", "unknown")
    total_trials = summary.get("total_trials", 0)
    successful = summary.get("successful_trials", 0)
    failed = summary.get("failed_trials", 0)
    success_rate = summary.get("success_rate")

    lines.append(f"- **Provider**: {provider}")
    lines.append(f"- **Model**: {model}")
    lines.append(f"- **Total trials**: {total_trials}")
    lines.append(f"- **Successful**: {successful}")
    lines.append(f"- **Failed**: {failed}")
    lines.append(f"- **Provider success rate**: {_pct(success_rate)}")

    pareto = summary.get("pareto_analysis", {})
    frontier_rate = pareto.get("frontier_rate")
    lines.append(f"- **Granite Pareto frontier rate**: {_pct(frontier_rate)}\n")

    # ---------------------------------------------------------------------------
    # 2. Experimental Design
    # ---------------------------------------------------------------------------
    lines.append("## 2. Experimental Design\n")
    lines.append("All competitor plans are evaluated by the same deterministic")
    lines.append("`PlanEvaluator` (telecom physics) and `MissionOutcomeEvaluator`")
    lines.append("(mission-semantic outcomes).  No AI-specific scoring.  No Local")
    lines.append("fallback counted as Granite.  No composite AI score.\n")
    lines.append(f"- **Comparison tolerance**: {COMPARISON_TOLERANCE} (absolute, floating-point)\n")

    # ---------------------------------------------------------------------------
    # 5. Reliability / Failed Runs
    # ---------------------------------------------------------------------------
    lines.append("## 5. Reliability / Failed Runs\n")

    error_types: dict[str, int] = {}
    for t in trials:
        if t.status != BenchmarkStatus.SUCCESS and t.status != BenchmarkStatus.SKIPPED:
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

    lines.append(f"**Excluded from metric analysis**: {failed} failed runs are retained in raw")
    lines.append("data (`raw_results.jsonl`) but excluded from metric comparisons.\n")

    # ---------------------------------------------------------------------------
    # 6. Granite vs Semantic-Rule Results
    # ---------------------------------------------------------------------------
    lines.append("## 6. Granite vs Semantic-Rule Results\n")

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
    # 8. Pareto Analysis
    # ---------------------------------------------------------------------------
    lines.append("## 8. Pareto Analysis\n")

    lines.append(f"- Granite Pareto-frontier rate: {_pct(frontier_rate)}")
    lines.append(f"- Dominated by semantic-rule: {pareto.get('dominated_by_semantic_rule', 0)}")
    lines.append(f"- Dominates semantic-rule: {pareto.get('dominates_semantic_rule', 0)}")
    lines.append(f"- Neither dominates: {pareto.get('neither_dominates', 0)}\n")

    # ---------------------------------------------------------------------------
    # 12. Where the LLM Did Not Outperform (REQUIRED SECTION)
    # ---------------------------------------------------------------------------
    lines.append("## 12. Where the LLM Did Not Outperform\n")
    lines.append("> **This section is required and cannot be omitted.**\n")

    loss_scenarios: list[str] = []
    for pr_ai in [pr for pr in plan_results if pr.plan_type == PlanType.AI_PRIORITIZED]:
        pareto_info = compute_pareto_frontier(
            [pr for pr in plan_results
             if pr.scenario_id == pr_ai.scenario_id and pr.repetition == pr_ai.repetition]
        )
        ai_info = pareto_info.get(PlanType.AI_PRIORITIZED.value, {})
        if ai_info.get("plans_dominating_this_plan_count", 0) > 0:
            loss_scenarios.append(
                f"- {pr_ai.scenario_id} rep {pr_ai.repetition}: "
                f"Granite is dominated by {ai_info['plans_dominating_this_plan_count']} plan(s)"
            )

    if loss_scenarios:
        lines.append("### Scenarios where Granite was Pareto-dominated:\n")
        lines.extend(loss_scenarios)
        lines.append("")
    elif successful > 0:
        lines.append("Granite was not Pareto-dominated in any successful trial.\n")
        lines.append("*Note: This finding is conditional on the tested scenarios and model configuration.*\n")
    else:
        lines.append("No successful Granite trials to analyse.\n")

    # Also report metrics where Granite had more losses than wins
    if wtl:
        loss_dominant_metrics = []
        for metric in PRIMARY_METRICS:
            key = f"ai-prioritized_vs_semantic-rule-based/{metric}"
            v = wtl.get(key, {})
            losses = v.get("losses", 0)
            wins = v.get("wins", 0)
            if losses > wins:
                loss_dominant_metrics.append(f"- **{metric}**: {wins} wins, {losses} losses")
        if loss_dominant_metrics:
            lines.append("### Metrics where Granite had more losses than wins vs semantic-rule:\n")
            lines.extend(loss_dominant_metrics)
            lines.append("")

    # Provider failures
    if failed > 0:
        lines.append(f"### Provider failures:\n- {failed} trial(s) did not complete.")
        lines.append("  These failures are included in reliability statistics.\n")

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
    lines.append("  generalize to other LLM families or configurations.\n")

    # ---------------------------------------------------------------------------
    # 14. Reproduction Instructions
    # ---------------------------------------------------------------------------
    lines.append("## 14. Reproduction Instructions\n")
    lines.append("```bash")
    lines.append("# Dry run (no external API calls):")
    lines.append("python -m backend.app.benchmark.runner --provider Granite --suite core --dry-run")
    lines.append("")
    lines.append("# Live benchmark (requires Granite credentials):")
    lines.append("python -m backend.app.benchmark.runner --provider Granite --suite core \\")
    lines.append("  --repetitions 5 --execute-live")
    lines.append("")
    lines.append("# Core + ablations:")
    lines.append("python -m backend.app.benchmark.runner --provider Granite --suite core \\")
    lines.append("  --repetitions 5 --include-ablations --execute-live")
    lines.append("```")
    lines.append("")
    lines.append("Set environment variables: `GCSI_GRANITE_API_KEY`, `GCSI_GRANITE_PROJECT_ID`.")
    lines.append("No secrets should ever be committed to this file.\n")

    lines.append("---")
    lines.append("*Generated by GCSI Phase 2B benchmark framework. "
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

    # CSV summary
    write_summary_csv(plan_results, result_dir / "summary.csv")

    # Markdown report
    report_md = generate_markdown_report(summary, trials, plan_results, manifest)
    (result_dir / "report.md").write_text(report_md, encoding="utf-8")
