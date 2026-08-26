# ⚠️ AUTHENTICATION FAILURE — NOT BENCHMARK EVIDENCE

## Run ID: run-20260826-110706-530179c2

**This run is NOT scientific evidence.**

| Field | Value |
|---|---|
| Run type | `pilot` (infrastructure validation only) |
| Run date | 2026-08-26 |
| Total trials | 2 |
| Successful trials | **0** |
| Failed trials | **2** |
| Failure reason | `GraniteAPIError` — IBM Cloud IAM authentication failure |
| Model inferences completed | **0** |

## What happened

This was a 2-trial authentication pilot intended to verify IAM connectivity before running the
full core benchmark. Both trials failed with `GraniteAPIError` before any model inference was
performed. The IBM Cloud IAM credentials used were invalid or lacked the required watsonx.ai
project permissions.

**Zero model inferences were completed. No Granite ranking was produced.**

## What this is NOT

- ❌ Not a successful benchmark run
- ❌ Not scientific evidence of AI superiority or inferiority
- ❌ Not representative of Granite model behavior
- ❌ Not a valid comparison between AI and deterministic baselines

## Official benchmark status

The official core benchmark (`gcsi_benchmark_v1`, 60 trials, 5 repetitions per scenario)
has not yet been executed. It requires valid IBM Cloud IAM credentials with watsonx.ai access.

See `benchmarks/configs/gcsi_benchmark_v1.json` for the pre-registered benchmark configuration.
See `docs/benchmark_methodology.md` for the full methodology.

## Why this run is retained

The raw result files (`manifest.json`, `raw_results.jsonl`, `summary.json`, `report.md`)
are retained as infrastructure provenance — they document that a pilot attempt was made,
that the connection failure was recorded correctly, and that the benchmark framework
correctly handles authentication failures without fabricating results.
