# GCSI Phase 2B Benchmark

Reproducible scientific benchmark comparing LLM semantic prioritization against
deterministic alternatives for spacecraft data transmission planning.

## Competitors

| Plan | Description |
|---|---|
| `baseline` | BaselineScheduler (classical) |
| `deadline-first` | Earliest deadline first |
| `mission-critical-first` | Highest criticality first |
| `value-per-cost` | (criticality × mission_relevance) / expected_cost |
| `semantic-rule-based` | Deterministic semantic heuristic (scientific control) |
| `ai-prioritized` | IBM Granite LLM semantic prioritization |

## Scenario Matrix (Core: 12 scenarios)

| Capacity ratio | ORIGINAL | NOANOM | DECOY |
|---|---|---|---|
| 0.35 (severe) | CAP035_ORIGINAL | CAP035_NOANOM | CAP035_DECOY |
| 0.60 (strong) | CAP060_ORIGINAL | CAP060_NOANOM | CAP060_DECOY |
| 0.90 (moderate) | CAP090_ORIGINAL | CAP090_NOANOM | CAP090_DECOY |
| 1.20 (unconstrained) | CAP120_ORIGINAL | CAP120_NOANOM | CAP120_DECOY |

## Integrity Gates (all must pass before benchmark)

- Gate 0.1: Unknown Stage-2 evidence sources rejected
- Gate 0.2: Stage-1 prompt distinguishes historical from applicable anomaly
- Gate 0.3: SemanticRulePrioritizer counts only applicable anomaly-linked products
- Gate 0.4: None candidate metrics cannot become evidence

## Quick Start

```bash
# From ground-control-signal-insight/ directory:

# Dry run (ZERO external API calls):
python -m backend.app.benchmark.runner_cli --dry-run

# Live pilot (2 scenarios × 1 repetition):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite quick --repetitions 1 --execute-live

# Full core benchmark (12 scenarios × 5 repetitions = 60 calls):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite core --repetitions 5 --execute-live

# Core + ablations (~100 calls):
python -m backend.app.benchmark.runner_cli \
  --provider Granite --suite core --repetitions 5 \
  --include-ablations --execute-live
```

## Environment Variables

```
GCSI_GRANITE_API_KEY     # IBM Cloud IAM API key (REQUIRED for Granite)
GCSI_GRANITE_PROJECT_ID  # watsonx.ai project ID (REQUIRED for Granite)
GCSI_GRANITE_MODEL_ID    # Model override (default: ibm/granite-4-h-small)
```

**Never commit API keys. Configure via environment variables only.**

## Output Structure

```
benchmarks/results/<run-id>/
  manifest.json      # Run metadata (no secrets)
  raw_results.jsonl  # All trial and plan result records
  summary.json       # Machine-readable summary
  summary.csv        # Tabular plan results
  report.md          # Generated Markdown report
```

## Scientific Integrity

- The benchmark config is versioned (`configs/gcsi_benchmark_v1.json`)
- Results are never cherry-picked; failed runs are retained in raw data
- Granite failure → `status=provider_error`, NOT Local fallback
- Same evaluators for all plans; no AI-specific scoring bonus
- No composite AI score; multi-dimensional Pareto comparison used

## See Also

- `docs/benchmark_methodology.md` — Full methodology documentation
- `docs/benchmark_results.md` — Generated after real execution
