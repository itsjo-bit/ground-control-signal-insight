# GCSI Granite Benchmark Preflight

This document describes the remaining requirements before the official Granite benchmark
(`gcsi_benchmark_v1`, 60 trials) can be executed.

---

## Status: NOT YET EXECUTED

The official core benchmark has not been run. A 2-trial IAM authentication pilot was attempted
on 2026-08-26 — both trials failed with `GraniteAPIError` before any model inference was completed.

See `benchmarks/results/run-20260826-110706-530179c2/README.md` for full authentication failure details.

---

## Prerequisites Before Official Run

### 1. Valid IBM Cloud IAM Credentials

Required environment variables:
```
GCSI_GRANITE_API_KEY=<valid IBM Cloud IAM API key>
GCSI_GRANITE_PROJECT_ID=<valid watsonx.ai project ID with ibm/granite-4-h-small access>
```

The IAM API key must have:
- Access to the watsonx.ai service on IBM Cloud
- The project must have `ibm/granite-4-h-small` deployed or accessible
- Region must match `GCSI_GRANITE_API_URL` (default: us-south)

### 2. IAM Credential Preflight

Run the IAM preflight script before the benchmark:
```bash
python scripts/check_granite_connection.py
```

This script verifies:
- IAM token exchange succeeds
- watsonx.ai project is accessible
- `ibm/granite-4-h-small` is available in the project
- A 1-call pilot inference completes successfully

**Do not run the 60-trial core benchmark until this preflight passes.**

### 3. 2-Call Pilot Verification

After IAM preflight succeeds:
```bash
python -m backend.app.benchmark.runner_cli \
  --provider Granite \
  --suite quick \
  --repetitions 1 \
  --execute-live
```

Verify in the output report:
- `successful_trials: 2`
- `failed_trials: 0`
- At least one AI plan result with non-null `mission_value`

**Audit the pilot before proceeding to core.**

### 4. Pre-Core Static Issues (re-check)

The following pre-core issues were identified before Phase 5. Re-verify before core run:

**A. Connection retry classification**
Verify that a real `ConnectError` (network timeout, not HTTP error) is correctly classified as
`GraniteTransportError` and retried according to retry policy. Test with a mocked network failure.

**B. Core deadline modes use benchmark config as source**
Verify that `--suite core` uses `gcsi_benchmark_v1.json` `deadline_scales` exclusively, not
any environment variable override.

**C. Parse/schema failure taxonomy**
Verify that a malformed JSON response from Granite is classified as `GraniteResponseError`
(non-retriable), not `GraniteAPIError` (which might be retried).

If any of these are still present, create a narrow patch before running core.

---

## Official Core Benchmark Command

When all preflight checks pass:

```bash
# From ground-control-signal-insight/ directory
python -m backend.app.benchmark.runner_cli \
  --config benchmarks/configs/gcsi_benchmark_v1.json \
  --provider Granite \
  --suite core \
  --repetitions 5 \
  --execute-live \
  --save-prompts
```

Expected: ~60 provider calls (12 scenarios × 5 repetitions), runtime ~30–60 minutes.

---

## What the Official Run Will Produce

```
benchmarks/results/<run-id>/
  manifest.json        # Run metadata (no API keys)
  raw_results.jsonl    # All 60 trial + plan result records
  summary.json         # Machine-readable summary
  summary.csv          # Tabular plan results
  report.md            # Generated Markdown report with actual numbers
  audit/               # Prompt hashes for provenance verification
```

---

## After Official Run

1. Verify `successful_trials >= 40` (minimum for meaningful analysis)
2. Review `report.md` — do NOT modify results
3. Update README "Scientific Evaluation Status" section with actual results
4. Do NOT claim superiority beyond what the data actually shows
5. If results are mixed (some scenarios Granite wins, some baseline wins) — report exactly that

---

*GCSI Granite Benchmark Preflight — Phase 5*
