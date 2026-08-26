# GCSI Phase 2B Benchmark Methodology

> **Status**: Pre-registered methodology — FROZEN before benchmark results.
> Do not modify primary metrics, comparators, or analysis methods after seeing results.

## Overview

This document describes the reproducible scientific benchmark protocol for
evaluating LLM semantic prioritization (IBM Granite) against deterministic
alternatives in GCSI spacecraft data transmission planning.

**Scientific question**: Does LLM semantic prioritization produce measurably better
mission outcomes than deterministic alternatives, under what mission conditions,
and how consistently?

**Scientific principle**: All competing plans are evaluated by the same deterministic
physical and mission-outcome evaluators. The LLM does not define the benchmark
metrics, does not receive scoring bonuses, and is not allowed to substitute a
local fallback when being evaluated as an external model.

---

## 1. Competitors

| Plan ID | Type | Description |
|---|---|---|
| `baseline` | Classical | BaselineScheduler (mission-value weighted sort) |
| `deadline-first` | Classical | Earliest deadline ascending |
| `mission-critical-first` | Classical | Highest criticality descending |
| `value-per-cost` | Classical | (criticality × mission_relevance) / expected_transmission_cost |
| `semantic-rule-based` | Semantic control | Deterministic heuristic: anomaly_severity + 0.35×criticality + 0.30×mission_relevance + 0.20×scientific_value + 0.15×deadline_urgency |
| `ai-prioritized` | LLM (primary) | IBM Granite Stage-1 semantic prioritization → ranked-prefix plan |

**Fairness constraint**: All plans are evaluated by identical `PlanEvaluator` and
`MissionOutcomeEvaluator` with the same inputs. No AI-specific scoring bonus.

### Classical vs LLM data access

Classical plans see the **full authoritative packet queue** (100–150 products).
This reflects their actual operational architecture in GCSI.

The semantic-rule plan and Granite AI plan both see the same **bounded candidate set**
(≤50 products, selected by `CandidatePrioritizer`). Both use the same
`build_ranked_prefix_plan()` for plan construction. This is the critical fairness control.

---

## 2. Base Scenario

`data/scenarios/mission_data_v3.json` — the authoritative high-volume GCSI mission.

This file is **never overwritten or modified** by the benchmark. All variants are
deep copies produced by `ScenarioVariantGenerator`.

---

## 3. Scenario Variant Matrix

### 3.1 Core matrix (12 scenarios)

**Capacity dimension** (4 levels):

| Label | Ratio | Interpretation |
|---|---|---|
| CAP035 | 0.35 | Severely constrained (35% of queue fits in window) |
| CAP060 | 0.60 | Strongly constrained |
| CAP090 | 0.90 | Moderately constrained |
| CAP120 | 1.20 | Near-unconstrained (negative control) |

Window calculation:
```
total_queued_bits  = Σ(dp.size_bits for dp in data_products)
target_capacity_bits = capacity_ratio × total_queued_bits
window_s = target_capacity_bits / link_goodput_bps
```

The link model (SNR, BER, goodput) is derived from base scenario `link_inputs`
via `TelecomEngine` — unchanged across all variants.

**Anomaly dimension** (3 modes):

| Label | Mode | Description |
|---|---|---|
| ORIGINAL | Unchanged | v3 anomaly statuses as-is |
| NOANOM | No applicable anomaly | All active/monitoring → resolved; product anomaly_id links retained |
| DECOY | Resolved decoy | Highest-severity applicable anomaly resolved; others unchanged |

The DECOY transformation resolves the highest-severity applicable anomaly
(tie-breaking: lexicographic anomaly_id). This tests whether prioritizers
correctly avoid overreacting to historical high-severity anomaly linkage.

### 3.2 Scenario IDs

```
CAP035_ORIGINAL  CAP060_ORIGINAL  CAP090_ORIGINAL  CAP120_ORIGINAL
CAP035_NOANOM    CAP060_NOANOM    CAP090_NOANOM    CAP120_NOANOM
CAP035_DECOY     CAP060_DECOY     CAP090_DECOY     CAP120_DECOY
```

### 3.3 Full suite (24 scenarios, optional)

Add `deadline_scale = 0.5` (halves all deadlines):
```
python -m backend.app.benchmark.runner_cli --suite full
```

### 3.4 Capacity ratio verification

Each variant records `actual_capacity_ratio`. The benchmark asserts:
```
|actual_capacity_ratio - target| ≤ CAPACITY_TOLERANCE (0.01)
```

---

## 4. Provider and Model

**Primary subject**: IBM Granite (`ibm/granite-4-h-small` via IBM watsonx.ai)

Optional additional providers (architecture supports Gemini, Ollama) but:
- Results from different model families must NOT be combined into one "AI" result
- Every result identifies the exact provider and model

---

## 5. Strict Benchmark Provider Mode

In benchmark mode:

```
Granite failure → record GRANITE FAILURE
```

NOT:

```
Granite failure → Local → count as Granite  ← PROHIBITED
```

Failed trials are retained in `raw_results.jsonl` with:
```json
{"status": "provider_error", "error_type": "GraniteAPIError", ...}
```

Provider success rate is always reported alongside metric results.

---

## 6. Retry Policy

| Parameter | Value |
|---|---|
| `max_attempts` | 2 |
| Delay between attempts | ~1.0 s |
| What is retried | Transient transport failures only (explicit list) |
| What is NOT retried | Valid model outputs, malformed JSON, schema errors, unknown errors |

**Retriable conditions** (explicit whitelist only — default-deny):
- `GraniteTransportError` subclasses
- `httpx.TimeoutError`, `httpx.ConnectError`, `httpx.TransportError`
- `GraniteAPIError` containing connection/timeout keywords
- `GraniteAPIError` for HTTP 429, 500, 502, 503, 504

**Non-retriable by default** (everything else):
- HTTP 400, 401, 403, 404, 409, 422
- Unknown `GraniteAPIError` (default-deny)
- `GraniteResponseError` / parse failures / schema violations
- Unexpected response shape (HTTP 200 but missing `results[0].generated_text`)

A valid ranking that produces poor metrics is accepted as the trial result.
Retrying for better performance is prohibited (cherry-picking prevention).

---

## 7. Repetitions

| Suite | Repetitions |
|---|---|
| Quick (development) | 1–3 |
| Core (scientific run) | 5 |
| High-confidence (optional) | 10 |

Deterministic control plans (classical + semantic-rule) are computed **once per
scenario** and reused for all Granite repetitions. Granite is recomputed for
every repetition.

---

## 8. Pre-registered Primary Metrics

**Maximize** (higher is better):

| Metric | Source |
|---|---|
| `mission_value` | PlanEvaluator |
| `critical_delivery_rate` | PlanEvaluator (critical_delivered / total_critical) |
| `scientific_value_capture_rate` | MissionOutcomeEvaluator |
| `required_delivery_rate` | MissionOutcomeEvaluator |
| `active_anomaly_delivery_rate` | MissionOutcomeEvaluator |
| `high_severity_anomaly_coverage_rate` | MissionOutcomeEvaluator |
| `anomaly_weighted_coverage` | MissionOutcomeEvaluator |

**Minimize** (lower is better):

| Metric | Source |
|---|---|
| `risk_score` | PlanEvaluator |
| `deadline_miss_rate` | PlanEvaluator |

**Secondary / descriptive** (not used as headline win criteria):

`delivery_rate`, `average_delivered_age_s`, `bandwidth_utilization`,
`window_pressure`, `retransmission_overhead`, `deferred_count`

---

## 9. Analysis

### 9.1 Pairwise win/tie/loss

For each Granite run, compare LLM plan against:
- baseline, deadline-first, mission-critical-first, value-per-cost, semantic-rule-based

For each primary metric: **WIN / TIE / LOSS / N/A**

**Comparison tolerance**: `1e-9` (absolute). Differences smaller than this are TIE.

**Null metric policy**: When a metric is None for either plan → N/A.
Never convert None → 0 or 1 for convenience.

### 9.2 Pareto analysis

Plan A **Pareto-dominates** Plan B when:
- No worse than B on every applicable primary metric
- Strictly better on at least one

"Applicable" = both values are non-null.

Records per plan:
- `is_pareto_frontier`
- `plans_dominated_count`
- `plans_dominating_this_plan_count`

### 9.3 Aggregate statistics

Across repetitions per scenario: **median, min, max, IQR** for each primary metric.

### 9.4 No composite AI score

Prohibited: AI_SCORE, GCSI_SCORE, SEMANTIC_SUPERIORITY_INDEX.

Use multi-dimensional Pareto comparison.

---

## 10. Ablation Study

### Ablation A: No description

CandidateSummary.description replaced with `""`.
LLM still sees all numeric fields.
Measures contribution of natural-language semantics.

### Ablation B: No active anomaly context

`active_anomalies` removed from Stage-1 context.
Candidate `anomaly_id` fields remain visible.
Measures contribution of explicit anomaly-event context.

**Representative ablation subset** (4 scenarios):
```
CAP035_ORIGINAL  CAP060_ORIGINAL  CAP035_NOANOM  CAP060_DECOY
```

Only Stage-1 LLM input changes. Plan construction and evaluation are identical.

---

## 11. Fairness Controls

| Control | Implementation |
|---|---|
| Same candidate set for Granite and semantic-rule | `CandidatePrioritizer.select()` called once; output passed to both |
| Same ranked-prefix builder | Both use `build_ranked_prefix_plan()` via respective wrappers |
| Same PlanEvaluator | Identical `PlanEvaluator` instance and inputs |
| Same MissionOutcomeEvaluator | Identical `MissionOutcomeEvaluator` instance and inputs |
| No AI-specific scoring | No bonus applied to any plan type |
| No Local fallback as Granite | `GraniteBenchmarkProvider` raises on failure; no fallback |
| No cherry-picking | Failed runs retained; no retry for poor-performing valid outputs |

---

## 12. Reproducibility

Every run produces a `manifest.json` containing:
- `benchmark_version`, `run_id`, `timestamp_utc`
- `git_commit_sha` (or "unknown")
- `base_scenario_sha256`
- `provider`, `model`
- `candidate_limit`, `scenario_matrix`, `repetitions`
- `retry_policy`, `primary_metrics`, `comparison_tolerance`
- `python_version`, `platform`

**No secrets** (no API keys, no IAM tokens, no project IDs) are written to any output file.

Provenance hashes per trial:
- `prompt_system_sha256` (SHA-256 of Stage-1 system prompt)
- `prompt_user_sha256` (SHA-256 of Stage-1 user context)
- `raw_response_sha256` (SHA-256 of exact raw response bytes from provider)
- `ranking_hash` (SHA-256 of parsed ranked product ID list)
- `plan_order_hash` (SHA-256 of ordered packet IDs per plan)

**Note on hash semantics**: `raw_response_sha256` is computed over the exact bytes
returned by the Granite API before any parsing. `ranking_hash` is computed over the
extracted product ID list. These represent different levels of provenance and may differ
even for the same trial. Both are retained for failed trials where applicable.

**Failed trial provenance**: When a provider call fails, the trial retains:
- `attempt_count` (actual number of attempts made, not assumed 1)
- `attempt_latencies_ms` (per-attempt latency list)
- `raw_response` and `raw_response_sha256` (if a response body was received)
- `prompt_system_sha256`, `prompt_user_sha256` (from the messages that were sent)
- `actual_model_id` (model used in the request)
- `generation_config` (parameters sent to the API)

This provenance is preserved even for parse errors, schema violations, and exhausted
retries. The raw response from a parse-failed or schema-invalid API call is always
retained for audit purposes.

**Model enforcement**: The benchmark config model is authoritative.
`GCSI_GRANITE_MODEL_ID` is rejected at preflight if it conflicts with the effective
benchmark model — it cannot silently substitute another model for official execution.

**Generation config canonical source**: `STAGE1_GENERATION_CONFIG` in `granite_agent.py`
is the single definition used by both `GraniteAgent._call_prioritization_api()` and
`GraniteBenchmarkProvider` provenance. This prevents drift between what is actually
sent and what is recorded.

---

## 13. Output Files

```
benchmarks/results/<run-id>/
  manifest.json        # Run metadata (no secrets)
  raw_results.jsonl    # All trial + plan result records (append-friendly)
  summary.json         # Machine-readable summary
  summary.csv          # Tabular (one row per plan per scenario)
  report.md            # Generated Markdown report
```

`raw_results.jsonl` format:
```
{"record_type": "trial", ...BenchmarkTrial fields...}
{"record_type": "plan_result", ...BenchmarkPlanResult fields...}
```

---

## 14. Acceptable Scientific Outcomes

All of these are valid results:
- **A**: Granite clearly beats semantic-rule under severe anomaly stress
- **B**: Granite and semantic-rule are usually trade-offs
- **C**: Semantic-rule performs as well as Granite
- **D**: Granite loses

Do not change benchmark methodology after discovering which result occurred.

---

## 15. Live Benchmark Commands

```bash
# Navigate to project root
cd ground-control-signal-insight/

# Dry run (ZERO external calls, framework validation):
python -m backend.app.benchmark.runner_cli --dry-run

# Quick pilot (infrastructure validation only — do not cite as scientific results):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite quick --repetitions 1 --execute-live

# Core benchmark (60 external calls):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite core --repetitions 5 --execute-live

# Core + ablations (~100 calls):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite core --repetitions 5 \
  --include-ablations --execute-live
```

Environment variables (never commit):
```
GCSI_GRANITE_API_KEY     # IBM Cloud IAM API key (required)
GCSI_GRANITE_PROJECT_ID  # watsonx.ai project ID (required)
```

**Note on GCSI_GRANITE_MODEL_ID**: This environment variable is NOT consulted for
benchmark execution. The benchmark model is controlled exclusively by the config
(`model: ibm/granite-4-h-small`) or the `--model` CLI flag.
If `GCSI_GRANITE_MODEL_ID` is set and conflicts with the effective benchmark model,
preflight will fail with an error rather than silently substituting another model.

---

## 16. Limitations

- Scenarios are controlled variants of one synthetic mission dataset
  (`mission_data_v3.json`); not flight-qualified spacecraft validation
- Telecom model is intentionally simplified (BPSK/AWGN analytical model)
- Benchmark conclusions apply to the current GCSI model and the tested provider/configuration
- External LLM behavior may change with model or service updates
- This benchmark does NOT prove universal AI superiority
- The deterministic semantic comparator is a heuristic, not an optimal scheduler
- Only the tested model/provider/configuration (IBM Granite) is represented

---

*GCSI Phase 2B — Scientific Benchmark Methodology v1*
*Pre-registered and frozen before benchmark execution.*
