# GCSI Phase 2B Benchmark Report

> **DATA SOURCE**: Numbers are computed from actual benchmark result data.
> No claims are hardcoded.

## 1. Executive Summary

- **Run type**: UNKNOWN
- **Provider**: Granite
- **Model**: ibm/granite-4-h-small
- **Total trials**: 2
- **Successful**: 0
- **Failed**: 2
- **Provider success rate**: 0.0%
- **Granite Pareto frontier rate (core, full-context)**: 0.0%

## 2. Experimental Design

All competitor plans are evaluated by the same deterministic
`PlanEvaluator` (telecom physics) and `MissionOutcomeEvaluator`
(mission-semantic outcomes).  No AI-specific scoring.  No Local
fallback counted as Granite.  No composite AI score.

- **Comparison tolerance**: 1e-09 (absolute, floating-point)
- **Core headline statistics**: Full-context (non-ablation) trials only
- **Ablation trials**: Kept separate from core statistics
- **Trial grouping**: By unique trial_id (not scenario+repetition alone)

## 3. Scenario Matrix

- **Capacity ratios**: [0.35, 0.9]
- **Anomaly modes**: ['ORIGINAL']
- **Deadline scales**: [1.0]
- **Scenarios**: ['CAP035_ORIGINAL', 'CAP090_ORIGINAL']

## 4. Provider / Model Configuration

- **Provider**: Granite
- **Model ID**: ibm/granite-4-h-small
- **Config ID**: gcsi_benchmark_v1
- **Config SHA-256**: 8f8830678a9e6ff5596a20ea3087dc3694fc1c64ec6c1fffe7f30c8d7a60b7a2
- **Preregistered**: False
- **Config overrides**: {'repetitions': {'configured': 5, 'executed': 1}, 'scenario_matrix': {'configured_count': 12, 'executed': ['CAP035_ORIGINAL', 'CAP090_ORIGINAL']}}
- **Decoding method**: greedy
- **Max new tokens**: 2048
- **Git commit**: unknown
- **Git dirty**: True
- **Run type**: pilot
- **Run status**: started

## 5. Reliability / Failed Runs

| Error type | Count |
|---|---|
| GraniteAPIError | 2 |

- **Total provider attempts**: 2
- **Trials requiring retry**: 0
- **Mean attempts per trial**: 1.00
- **Failed trials** excluded from metric analysis, retained in raw data.

## 6. Granite vs Semantic-Rule Results

_(Core full-context trials only)_

_No successful AI runs to compare._

## 7. Granite vs Classical Baselines

_(Core full-context trials only)_

### 7.1. Granite vs Baseline

_No data._

### 7.2. Granite vs Deadline-First

_No data._

### 7.3. Granite vs Mission-Critical-First

_No data._

### 7.4. Granite vs Value-Per-Cost

_No data._

## 8. Pareto Analysis

_(Core full-context trials grouped by unique trial_id)_

- Granite Pareto-frontier rate: 0.0%
- Dominated by semantic-rule: 0
- Dominates semantic-rule: 0
- Neither dominates: 0
- Total core trials evaluated: 0

## 9. Capacity-Stress Analysis

### CAP035

- Valid trials: 0
- Pareto frontier rate: 0.0%

### CAP090

- Valid trials: 0
- Pareto frontier rate: 0.0%

## 10. Anomaly-Mode Analysis

### ORIGINAL

- Valid trials: 0
- Pareto frontier rate: 0.0%

## 11. Ablation Study

> Ablation results are kept strictly separate from core statistics.

_No ablation data available._

## 12. Where the LLM Did Not Outperform

> **This section is required and cannot be omitted.**

No successful Granite trials to analyse.

### Negative Control: CAP120_NOANOM

The CAP120_NOANOM scenario is a near/unconstrained condition with all
anomalies resolved.  This is designed as a negative control where Granite's
anomaly-context advantage should not manifest.  Metrics are reported without
implying that absence of Granite advantage here is a failure.

_CAP120_NOANOM data not available._

### Provider Failures
- 2 trial(s) did not complete.
  These are included in reliability statistics and count against usable-trial rate.

## 13. Limitations

- Scenarios are controlled variants of one synthetic mission dataset
  (`mission_data_v3.json`); not flight-qualified spacecraft validation.
- The telecom model is intentionally simplified; conclusions apply to the
  current GCSI analytical model, not real deep-space communication.
- Benchmark conclusions apply only to the tested model/provider/configuration.
- External LLM behavior may change with model or service updates.
- This benchmark does NOT prove universal AI superiority.
- The deterministic semantic comparator is a heuristic, not an optimal scheduler.
- Only the tested provider (IBM Granite) is represented; results may not
  generalize to other LLM families or configurations.
- Statistical claims are descriptive only; no significance testing is applied.
  With 5 repetitions per scenario, results are indicative, not conclusive.

## 14. Reproduction Instructions

```bash
# Dry run (no external API calls):
python -m backend.app.benchmark.runner_cli \
  --config benchmarks/configs/gcsi_benchmark_v1.json --dry-run

# Core benchmark (requires Granite credentials):
python -m backend.app.benchmark.runner_cli \
  --config benchmarks/configs/gcsi_benchmark_v1.json \
  --suite core --execute-live

# Core + ablations:
python -m backend.app.benchmark.runner_cli \
  --config benchmarks/configs/gcsi_benchmark_v1.json \
  --suite core --include-ablations --execute-live
```

Set environment variables: `GCSI_GRANITE_API_KEY`, `GCSI_GRANITE_PROJECT_ID`.
Do NOT commit credentials to source control.

---
*Generated by GCSI Phase 2B.1 benchmark framework. Numbers computed from actual result data.*