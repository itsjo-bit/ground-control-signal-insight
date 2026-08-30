# Phase 7C — Legacy Scenario Dependency Audit

**Phase:** 7C  
**Status:** ANALYSIS ONLY — no runtime behavior changed  
**Starting HEAD:** `95d4d54` — Phase 7B: canonicalize legacy mode recovery  
**Audit date:** 2025

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current Canonical Source Architecture](#2-current-canonical-source-architecture)
3. [Audit Methodology](#3-audit-methodology)
4. [Direct-Reference Findings](#4-direct-reference-findings)
5. [Indirect and Dynamic Dependency Findings](#5-indirect-and-dynamic-dependency-findings)
6. [Per-File Analysis](#6-per-file-analysis)
7. [Dependency Matrix](#7-dependency-matrix)
8. [Legacy /scenarios API Analysis](#8-legacy-scenarios-api-analysis)
9. [Move/Delete Impact Analysis](#9-movedelete-impact-analysis)
10. [Recommended Disposition for Each File](#10-recommended-disposition-for-each-file)
11. [Risks](#11-risks)
12. [Recommended Next Phase](#12-recommended-next-phase)
13. [No-Change Confirmation](#13-no-change-confirmation)

---

## 1. Executive Summary

Five scenario JSON files reside in `data/scenarios/`. One (`asteria7_thermal_priority_contact_v1.json`) is the canonical user-facing mission source. The other four are legacy files with varying levels of active dependency.

**Key findings in priority order:**

- **`mission_data_v3.json`** is a **frozen benchmark input** with a SHA256 integrity test hardcoded into the test suite. It is also the explicit `base_scenario` in the frozen benchmark configuration file `benchmarks/configs/gcsi_benchmark_v1.json`. Its path, filename, and byte content must remain stable. It has the highest dependency count of any non-canonical scenario file.

- **`nominal_pass.json`** is the **primary test fixture** for the entire backend test suite. It is used as a scenario fixture in well over 30 individual test files and is the default example in `backend/app/state.py`'s module docstring. It also appears in `tests/scenarios/`, integration tests, unit tests, randomizer tests, AI provider tests, and state-management tests. It is actively exercised in CI.

- **`mission_data_v2.json`** is an **active backward-compatibility test fixture**. It is the reference scenario for schema-compatibility testing (missing `distance_km` field), anomaly loading, and ScenarioLoader backward-compatibility assertions. Multiple test files require it by path.

- **`degraded_link.json`** is a **lightweight test fixture** providing a unique degraded-link condition (poor SNR, unstable link, short contact window). It is used in `test_scenario_e2e.py` and `test_candidate_generator.py`. It provides unique link parameters not present in `nominal_pass.json`.

- **All four files are discoverable by `GET /scenarios`** because the route handler performs a directory glob of `data/scenarios/*.json`. Merely existing in the directory makes a file appear in the legacy API response.

- **`POST /scenarios/switch` can load all four legacy files** because they are present in the configured scenarios directory. This API remains functional but is no longer called by production frontend code (after Phase 7B).

- **`switchScenario()`** is exported in `frontend/src/api/client.ts` but is **not called by any production frontend component** after Phase 7B. It is only mocked in test files.

- **No scenario file needs to be deleted or moved** to complete this audit. The audit establishes evidence only.

---

## 2. Current Canonical Source Architecture

**FACT.** As of HEAD `95d4d54`, the user-facing architecture is:

```
GET  /sources               ← returns 3 catalog entries
POST /sources/select        ← production user-facing switch

Catalog (source_catalog.py):
  1. asteria-7        → data/scenarios/asteria7_thermal_priority_contact_v1.json
  2. juno-pj62-v1     → data/replays/juno_pj62_mwr_v1.json
  3. juno-pj62-v2     → data/replays/juno_pj62_large_replay_v2_descriptor.json
```

**FACT.** The default startup path is:

```python
# backend/app/main.py line 70
_DEFAULT_SCENARIO_PATH = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")
```

If `GCSI_SCENARIO_PATH` is not set and `GCSI_SOURCE_MODE` is `synthetic_scenario` (the default), ASTERIA-7 is loaded unconditionally at startup.

**FACT.** The CI workflow sets `GCSI_SCENARIO_PATH: data/scenarios/asteria7_thermal_priority_contact_v1.json` explicitly (`.github/workflows/ci.yml` line 35).

**FACT.** The legacy compatibility API still exists in parallel:

```
GET  /scenarios             ← lists all *.json in data/scenarios/
POST /scenarios/switch      ← loads any file in data/scenarios/ by filename
```

**FACT.** None of the four legacy scenario files appears in `source_catalog.py`. They are not part of the Mission Source Catalog.

---

## 3. Audit Methodology

The following investigation methods were used:

1. **Starting baseline check**: `git log --oneline -5` and `git status` confirmed HEAD `95d4d54`, clean working tree, no uncommitted changes.

2. **Exhaustive filename search**: Each of the five scenario basenames was searched across all file types (`.py`, `.ts`, `.tsx`, `.js`, `.json`, `.md`, `.txt`, `.yml`, `.yaml`, `.sh`, `.env`, `.cfg`) using the grep tool, covering backend, frontend, tests, benchmarks, docs, scripts, tools, CI workflows, and configuration files.

3. **Indirect mechanism investigation**: The route handler implementing `GET /scenarios` was read in full to document the directory-glob behavior. The `ScenarioLoader` module was identified across 65 files.

4. **Environment-variable tracing**: All occurrences of `GCSI_SCENARIO_PATH` and `GCSI_SCENARIOS_DIR` were found and classified.

5. **Frozen hash discovery**: SHA256 integrity assertions for `mission_data_v3.json` were located in `test_phase4_2e_ground_reception.py` and `test_phase4_2f5_ground_reception.py`. The hash values are recorded below.

6. **Git history**: `git log --follow` was run for each file to establish introduction commit and ancestry.

7. **Frontend API tracing**: `switchScenario()` occurrences were located across `.tsx`/`.ts` files to determine production vs. test usage.

8. **State module**: `backend/app/state.py` was read to confirm the module docstring references `nominal_pass.json` as the usage example.

---

## 4. Direct-Reference Findings

### 4.1 `mission_data_v3.json`

**FACT.** More than 131 distinct line occurrences across the codebase. Key classifications:

| Location | Type | Purpose |
|---|---|---|
| `benchmarks/configs/gcsi_benchmark_v1.json` line 4 | Frozen config | `"base_scenario": "data/scenarios/mission_data_v3.json"` — the authoritative benchmark input |
| `backend/app/benchmark/runner_cli.py` lines 56–74 | Production tool | `_find_base_scenario()` hard-codes path candidates for `mission_data_v3.json` |
| `backend/app/benchmark/models.py` line 164 | Production model | `base_scenario_id: str = Field(default="mission_data_v3_high_volume_pass")` |
| `backend/app/benchmark/report.py` line 676 | Production report | References `mission_data_v3.json` in generated report disclaimer text |
| `backend/app/main.py` line 172 | Production startup | Legacy packet banner references `mission_data_v3.json` as "alternative lightweight scenario" |
| `tests/unit/test_phase4_2e_ground_reception.py` lines 44, 247–256 | Test — **frozen hash** | SHA256 must equal `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` |
| `tests/unit/test_phase4_2f5_ground_reception.py` lines 29–33, 292–295 | Test — **frozen hash** | Same frozen SHA256 assertion |
| `tests/unit/benchmark/test_phase2b1.py` line 92 | Benchmark unit test | `BASE_SCENARIO_PATH` |
| `tests/unit/benchmark/test_phase2b1a_integrity.py` line 70 | Benchmark integrity test | `BASE_SCENARIO_PATH` |
| `tests/unit/benchmark/test_scenario_variants.py` line 31 | Benchmark unit test | `BASE_SCENARIO_PATH` |
| `tests/unit/benchmark/test_runner.py` line 38 | Benchmark unit test | `BASE_SCENARIO_PATH` |
| `tests/integration/test_data_products_and_scenarios.py` lines 14, 31, 100, 197, 212, 235, 253, 260, 278, 280, 288, 315, 319 | Integration test | Switch-to tests, state assertions |
| `tests/integration/test_scenario_path_and_security.py` lines 31, 103, 132, 139, 157, 161, 219 | Integration test | Security rejection tests, valid switch |
| `tests/integration/test_v3_pipeline.py` line 24 | Integration test | V3 pipeline |
| `tests/integration/test_assess_endpoint_schema.py` line 20 | Integration test | Schema validation |
| `tests/unit/test_phase4_2a_asteria.py` lines 24–25, 43, 499, 506, 518, 526, 556 | Unit test | ASTERIA-7 vs v3 comparison |
| `tests/unit/test_phase4_*.py` (multiple files) | Unit test | Load fixture |
| `tests/unit/test_phase3.py`, `test_phase4.py`, `test_phase4_1.py` | Unit test | Path constant |
| `benchmarks/results/run-20260826-110706-530179c2/raw_results.jsonl` | Frozen result | `base_scenario_sha256: de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08` |
| `docs/benchmark_methodology.md` lines 50, 412 | Documentation | Describes v3 as authoritative benchmark scenario |
| `docs/architecture.md` line 129 | Documentation | Architecture diagram base |
| `docs/asteria7_demo.md` lines 296–299 | Documentation | Alternative startup example |
| `docs/judge_audit.md` lines 28, 83, 140, 144, 214, 216, 364 | Documentation | Historical audit trail |
| `docs/juno_pj62_historical_replay_demo.md` line 518 | Documentation | Example switch command |
| `docs/submission/release_checklist.md` lines 69, 105 | Documentation | Pre-release manual check |
| `README.md` lines 395, 459, 732, 735, 987 | Documentation | Usage examples and structure table |
| `frontend/src/components/__tests__/phase7b.legacy.banner.test.tsx` line 213 | Frontend test | Mock state reference |
| `frontend/src/components/__tests__/phase51g.integration.test.tsx` lines 347–376 | Frontend test | Mock scenario state |
| `frontend/src/components/__tests__/phase51f.test.ts` lines 627, 640 | Frontend test | Scenario path constant |
| `frontend/src/components/__tests__/phase51.test.tsx` line 61 | Frontend test | Product count assertion |
| `frontend/src/components/__tests__/ConfigPanel.test.tsx` line 81 | Frontend test | Negative assertion — filename NOT shown in UI |

**FACT.** There are two different SHA256 values in the codebase for `mission_data_v3.json`:
- `test_phase4_2e_ground_reception.py`: `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9`
- `benchmarks/results/raw_results.jsonl` (embedded in benchmark records): `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08`

**INFERENCE.** The discrepancy suggests the benchmark was run at a different point in the file's history than when the test hash was recorded, or the file was updated between the benchmark run and the freeze assertion. This is a **pre-existing inconsistency** and is documented here as a finding only; it must not be resolved in Phase 7C.

### 4.2 `mission_data_v2.json`

**FACT.** 27 occurrences. Key classifications:

| Location | Type | Purpose |
|---|---|---|
| `tests/unit/test_phase2e_c3b.py` lines 56, 179, 208 | Unit test — **schema compat** | `distance_km` absent → must load with `None` |
| `tests/unit/test_phase2e_c3c.py` line 44 | Unit test — **schema compat** | Geometry nulls in `/state` |
| `tests/unit/test_phase2e_b_v3.py` lines 36, 402 | Unit test | `scenario_id` assertion |
| `tests/unit/test_phase2e_c1.py` line 34 | Unit test | Schema validation |
| `tests/unit/test_phase2e_d3.py` line 63 | Unit test | Path constant |
| `tests/unit/test_phase2e_c3e.py` line 433 | Unit test | Optional — skips if not found |
| `tests/unit/test_phase2c.py` lines 547, 634 | Unit test | Data-product loading |
| `tests/unit/test_phase2d.py` lines 547, 634 | Unit test | Path usage |
| `tests/unit/test_candidate_generator.py` lines 193–215 | Unit test — **ScenarioLoader compat** | Must load, `scenario_id` check, `packets` empty |
| `tests/integration/test_v2_pipeline.py` line 22 | Integration test | V2 pipeline |
| `tests/integration/test_v3_pipeline.py` line 25 | Integration test | Cross-version load |
| `tests/integration/test_data_products_and_scenarios.py` line 33 | Integration test | Path constant |
| `tests/integration/test_ai_plan_route.py` line 24 | Integration test | Path constant |
| `data/scenarios/mission_data_v2.json` line 2 | Fixture | Self-identifying: `"scenario_id": "mission_data_v2_anomaly_pass"` |
| `README.md` lines 481, 988 | Documentation | Retained for compatibility |
| `frontend/src/components/__tests__/ConfigPanel.test.tsx` lines 76–78 | Frontend test | Negative assertion — NOT shown in UI |

### 4.3 `degraded_link.json`

**FACT.** 13 occurrences. Key classifications:

| Location | Type | Purpose |
|---|---|---|
| `tests/unit/test_candidate_generator.py` lines 184–189 | Unit test | Must load cleanly; has packets |
| `tests/scenarios/test_scenario_e2e.py` lines 104–116 | E2E test | Load, generate plans, evaluate — tests the packet-mode pipeline under degraded conditions |
| `data/scenarios/degraded_link.json` line 2 | Fixture | Self: `"scenario_id": "degraded_link_001"` |
| `README.md` lines 469, 990 | Documentation | Retained for compatibility |
| `frontend/src/components/__tests__/ConfigPanel.test.tsx` lines 66–68 | Frontend test | Negative assertion — NOT shown in UI |

### 4.4 `nominal_pass.json`

**FACT.** 91 occurrences. Key classifications:

| Location | Type | Purpose |
|---|---|---|
| `backend/app/state.py` line 10 | **Production module docstring** | Usage example |
| `.env.example` lines 110, 112 | Developer docs | Example `GCSI_SCENARIO_PATH` values |
| `tests/scenarios/test_scenario_e2e.py` (multiple) | E2E test | Primary fixture for full-pipeline test |
| `tests/unit/test_scenario_randomizer.py` line 28 | Unit test | Only scenario used in randomizer tests |
| `tests/unit/test_ai_providers.py` (6 uses) | Unit test | AI provider tests |
| `tests/unit/test_agent.py` line 854 | Unit test | Agent test |
| `tests/unit/test_phase2e_c3b.py` lines 57, 173, 213 | Unit test — **schema compat** | `distance_km` absent → `None`; `nominal_pass_001` id check |
| `tests/unit/test_phase2e_c3c.py` lines 45, 255 | Unit test — **geometry nulls** | |
| `tests/unit/test_phase2e_c1.py` lines 35, 178, 186 | Unit test — **packet bits** | `queued_data_bits == 349_184` — **hardcoded regression value** |
| `tests/unit/test_phase2e_c3e.py` lines 442–445 | Unit test — geometry | |
| `tests/unit/test_phase4_1.py`, `test_phase4_1a.py`, `test_phase3.py`, `test_phase4.py` | Unit tests | Path constant |
| `tests/unit/test_phase2e_d3.py`, `test_phase2e_b_v3.py` | Unit tests | Path constant / load test |
| `tests/integration/test_data_products_and_scenarios.py` (many) | Integration test | Listing, switching, state assertions |
| `tests/integration/test_scenario_path_and_security.py` lines 32, 99, 102, 209 | Integration test | `GCSI_SCENARIO_PATH` env override; switch via API |
| `tests/integration/test_phase6ec6_historical_runtime.py` (5 uses) | Integration test | Runtime mode regression |
| `tests/unit/test_phase6ec6_runtime_source_activation.py` (17 uses) | Unit test | Source activation logic |
| `tests/integration/test_api_plans.py`, `test_api_simulate.py`, `test_api_queue.py`, `test_api_state.py` | Integration test | Basic API tests all load nominal_pass |
| `tests/integration/test_1011_fixes.py` line 51 | Integration test | Path constant |
| `tests/integration/test_ai_plan_route.py` line 25 | Integration test | Path constant |
| `tests/integration/test_v2_pipeline.py` line 23 | Integration test | Path constant |
| `frontend/src/components/__tests__/ConfigPanel.test.tsx` lines 71–73 | Frontend test | Negative assertion — NOT shown in UI |

### 4.5 `asteria7_thermal_priority_contact_v1.json` (Control)

**FACT.** 59 occurrences. Canonical status is encoded in:
- `backend/app/mission_sources/source_catalog.py` line 86 — only synthetic source in `AVAILABLE_MISSION_SOURCES`
- `backend/app/main.py` line 70 — `_DEFAULT_SCENARIO_PATH`
- `.github/workflows/ci.yml` line 35 — explicit CI env var
- `docs/submission/release_checklist.md` line 106 — frozen integrity check
- `tools/generate_asteria7_demo.py` — reproducible generator
- `backend/app/api/routes_experience.py` line 25 — experience manifest keyed on scenario ID

---

## 5. Indirect and Dynamic Dependency Findings

### 5A. Directory Scanning (`GET /scenarios`)

**FACT.** `backend/app/api/routes_data_products.py` line 131 performs:

```python
json_files = sorted(scenarios_dir.glob("*.json"))
```

This scans `GCSI_SCENARIOS_DIR` (defaulting to `data/scenarios/`) and returns every `.json` file. **Merely existing in `data/scenarios/` makes a scenario discoverable via `GET /scenarios` and loadable via `POST /scenarios/switch`.** There is no allowlist or blocklist.

**FACT.** The directory path can be overridden via `GCSI_SCENARIOS_DIR` env var (`routes_data_products.py` line 35–37).

**FACT.** After Phase 7A and 7B, this endpoint is no longer the user-facing source selector. However, tests still explicitly assert that `mission_data_v3.json` and `nominal_pass.json` appear in `GET /scenarios` results (e.g., `test_data_products_and_scenarios.py` lines 197, 204).

**INFERENCE.** If any of the four legacy files were moved out of `data/scenarios/`, it would: (a) immediately disappear from `GET /scenarios` responses, and (b) cause tests that assert its presence in that listing to fail.

### 5B. Tests Asserting Specific Files in `/scenarios`

**FACT.** `tests/integration/test_data_products_and_scenarios.py` line 197 asserts:
```python
assert "mission_data_v3.json" in filenames
```
Line 204 asserts:
```python
assert "nominal_pass.json" in filenames
```

These are direct assertions about directory contents via API. Moving either file would break these tests.

### 5C. Startup / Environment Configuration

**FACT.** `GCSI_SCENARIO_PATH` can point to any scenario file. The `.env.example` shows `nominal_pass.json` as an example value. No legacy file is the default startup scenario.

**FACT.** `GCSI_SCENARIOS_DIR` defaults to `data/scenarios/` and is used exclusively by `GET /scenarios` and `POST /scenarios/switch`.

**FACT.** When `GCSI_SCENARIO_PATH` is not set and `GCSI_SOURCE_MODE=synthetic_scenario`, only `asteria7_thermal_priority_contact_v1.json` is loaded at startup (via `_DEFAULT_SCENARIO_PATH`). None of the four legacy files is a startup default.

**FACT.** `test_integration_data_products_and_scenarios.py` line 14 still says "Default startup uses mission_data_v3.json" — this is a **stale comment** in a test file. After Phase 4.2A, the default startup is ASTERIA-7, not v3. This stale comment is a pre-existing inconsistency and is documented here; it must not be fixed in Phase 7C.

### 5D. Benchmark Tools

**FACT.** `backend/app/benchmark/runner_cli.py` hard-codes path candidates for `mission_data_v3.json` in `_find_base_scenario()`. This is production benchmark tooling, not a test.

**FACT.** `benchmarks/configs/gcsi_benchmark_v1.json` references `"base_scenario": "data/scenarios/mission_data_v3.json"` and carries the comment `FROZEN before results. Do not edit after running.`

**FACT.** The frozen benchmark results in `benchmarks/results/run-20260826-110706-530179c2/raw_results.jsonl` embed `base_scenario_sha256` in every trial record, confirming that the specific byte content of `mission_data_v3.json` was the cryptographic input for all benchmark trials.

### 5E. SHA256 Integrity Tests (Frozen Byte-Stability Requirement)

**FACT.** Two separate test files assert the exact SHA256 of `mission_data_v3.json`:

- `tests/unit/test_phase4_2e_ground_reception.py` line 60: `_V3_SHA256 = "dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9"`
- `tests/unit/test_phase4_2f5_ground_reception.py` line 295 (assertion exists; exact constant value in that file)

These tests enforce that `mission_data_v3.json` must **remain byte-for-byte stable**. Any modification, reencoding, or move that changes byte content would fail them. A move that preserves byte content but changes the path would break path-based references.

**FACT.** A similar integrity test exists for `benchmarks/configs/gcsi_benchmark_v1.json` (`_BENCHMARK_SHA256 = "932bedd0dc6aacf255517ec62d812c8be6306358e0dff27bc0a227462fae6fc8"`).

### 5F. Hardcoded Regression Values

**FACT.** `test_phase2e_c1.py` line 186 asserts `queued_data_bits == 349_184` for `nominal_pass.json`. This is a hardcoded regression constant derived from the specific packet sizes in that file. If `nominal_pass.json` content were ever modified, this test would fail.

### 5G. Frontend `switchScenario()` — Production vs. Test

**FACT.** `switchScenario()` is defined in `frontend/src/api/client.ts` lines 172–183. It calls `POST /scenarios/switch`.

**FACT.** After Phase 7B, no production frontend component calls `switchScenario()`. The grep confirms:
- `frontend/src/MissionControl.tsx` calls `selectSource()` only.
- `frontend/src/App.tsx` calls `selectSource()` only.
- `switchScenario` appears only in `vi.fn()` mock setups in test files (`phase7b.legacy.banner.test.tsx`, `phase51g.integration.test.tsx`, `fourzone.layout.test.tsx`).

**FACT.** The Phase 7B test `phase7b.legacy.banner.test.tsx` explicitly asserts that `switchScenario` is **never called** during recovery operations, confirming the intent of Phase 7B.

---

## 6. Per-File Analysis

### 6.1 ASTERIA-7 (`asteria7_thermal_priority_contact_v1.json`)

**FACT.** Canonical status encoded in `source_catalog.py` — only synthetic entry in `AVAILABLE_MISSION_SOURCES`.

**FACT.** Default startup path (`main.py` line 70).

**FACT.** CI env explicitly points to it.

**FACT.** Reproducible generator: `tools/generate_asteria7_demo.py` with seed `20240923` produces it byte-identically.

**FACT.** Experience manifest keyed on its `scenario_id` in `routes_experience.py`.

**FACT.** Integrity check in `docs/submission/release_checklist.md` — "UNCHANGED from original."

**FACT.** Multiple test files assert it loads, has correct `scenario_id`, and drives the canonical banner.

**INFERENCE.** ASTERIA-7 must remain at `data/scenarios/asteria7_thermal_priority_contact_v1.json` because `source_catalog.py` resolves its path relative to `__file__` and the path is used as a trusted `source_ref`. Any relocation would break the Mission Source Catalog lookup.

**Classification:** Canonical production source, frozen, must remain in place.

---

### 6.2 `mission_data_v3.json`

**Q1: Can normal user-facing UI select it?**  
**FACT:** No. It is not in `source_catalog.py`. The user-facing dropdown uses `GET /sources`, which returns only the three catalog entries. The UI cannot select it through the current production interface.

**Q2: Can the backend still load it?**  
**FACT:** Yes. `app_state.load_scenario("data/scenarios/mission_data_v3.json")` works. The file exists and passes `ScenarioLoader`.

**Q3: Can `/scenarios/switch` load it?**  
**FACT:** Yes. It is in `data/scenarios/`, so `POST /scenarios/switch {"filename": "mission_data_v3.json"}` succeeds. Integration tests exercise this.

**Q4: Can `GCSI_SCENARIO_PATH` load it?**  
**FACT:** Yes. `GCSI_SCENARIO_PATH=data/scenarios/mission_data_v3.json` is documented in README (lines 732–735) and `docs/asteria7_demo.md` (line 296) as a developer-facing override.

**Q5: Is it involved in normal startup?**  
**FACT:** No. Normal startup loads ASTERIA-7. This file is only loaded if `GCSI_SCENARIO_PATH` is explicitly set to it.

**Q6: Is it used in automated tests?**  
**FACT:** Yes, heavily. At least 20 test files reference it directly by path as a test fixture. It is the input for all benchmark unit tests.

**Q7: Is it used in benchmark methodology or frozen benchmark evidence?**  
**FACT:** Yes. It is the `base_scenario` in the frozen `gcsi_benchmark_v1.json` config. It is the cryptographic input for all 12+ benchmark trial records in `raw_results.jsonl`. Two test files enforce its exact SHA256 hash.

**Q8: Is it used by a tool/script?**  
**FACT:** Yes. `backend/app/benchmark/runner_cli.py` hard-codes path candidates for it. The benchmark runner requires it by name and path.

**Q9: Is it referenced by current user documentation?**  
**FACT:** Yes. README (§ "Alternative scenario"), `docs/benchmark_methodology.md`, `docs/asteria7_demo.md`, `docs/juno_pj62_historical_replay_demo.md`, `docs/architecture.md`.

**Q10: Is it referenced only historically?**  
**FACT:** No. It has active, currently-executable dependencies (benchmark runner, benchmark tests, benchmark config, multiple integration tests).

**BENCHMARK / FROZEN STATUS:** `mission_data_v3.json` has a hardcoded SHA256 in the test suite and its hash is embedded in the frozen benchmark results. Its byte content **must not change**. Its path `data/scenarios/mission_data_v3.json` is hardcoded in `benchmarks/configs/gcsi_benchmark_v1.json` (frozen) and `runner_cli.py`. The path **must not change** without updating both the frozen config and the runner tool.

**Note on SHA256 discrepancy:** The frozen test hash (`dea5339...`) differs from the hash embedded in benchmark run records (`de43388...`). This pre-existing inconsistency requires investigation in a future phase before any freeze-stability claim can be considered complete.

---

### 6.3 `mission_data_v2.json`

**Q1: Can normal user-facing UI select it?**  
**FACT:** No. Not in source catalog.

**Q2: Can the backend still load it?**  
**FACT:** Yes.

**Q3: Can `/scenarios/switch` load it?**  
**FACT:** Yes. Present in `data/scenarios/`.

**Q4: Can `GCSI_SCENARIO_PATH` load it?**  
**FACT:** Yes. Any path can be passed.

**Q5: Is it involved in normal startup?**  
**FACT:** No.

**Q6: Is it used in automated tests?**  
**FACT:** Yes, in 17 test locations across 12 files. It is the reference for:
- `distance_km` absent → loads with `None` (backward-compatibility schema test)
- `scenario_id == "mission_data_v2_anomaly_pass"` assertion
- 50 data products + 3 anomalies structure check
- ScenarioLoader backward-compatibility validation (`TestScenarioLoaderV2`)
- Integration test `test_v2_pipeline.py`

**Q7: Is it used in benchmark methodology?**  
**FACT:** No. Benchmark config and runner reference only `mission_data_v3.json`.

**Q8: Is it used by a tool/script?**  
**FACT:** No direct tool usage found.

**Q9: Is it referenced by current documentation?**  
**FACT:** README line 481 describes it as "retained for compatibility." This is accurate.

**Q10: Does it provide a unique test condition?**  
**FACT:** Yes. It is specifically the scenario without `distance_km`, used to test that the `ScenarioLoader` handles the absent field correctly. It contains 50 `data_products` and 3 `anomalies` (different schema era than `nominal_pass.json`'s packet-based schema). It validates the data-product loading path for the intermediate schema version.

**INFERENCE.** `mission_data_v2.json` is currently an active backward-compatibility test fixture. Its specific structural characteristics (absent `distance_km`, 50 products, 3 anomalies) are under test assertion. Removing it would break at least 12 test files.

---

### 6.4 `degraded_link.json`

**Q1: Can normal user-facing UI select it?**  
**FACT:** No. Not in source catalog.

**Q2: Can the backend still load it?**  
**FACT:** Yes.

**Q3: Can `/scenarios/switch` load it?**  
**FACT:** Yes.

**Q4: Can `GCSI_SCENARIO_PATH` load it?**  
**FACT:** Yes.

**Q5: Is it involved in normal startup?**  
**FACT:** No.

**Q6: Is it used in automated tests?**  
**FACT:** Yes, in 2 test locations:
- `test_candidate_generator.py` line 184: load test (schema compatibility)
- `test_scenario_e2e.py` line 104: E2E test: load → generate plans → evaluate all plans

**Q7: Is it used in benchmark methodology?**  
**FACT:** No.

**Q8: Is it used by a tool/script?**  
**FACT:** No.

**Q9: Is it referenced by current documentation?**  
**FACT:** README line 469 mentions it.

**Q10: Does it provide a unique test condition?**  
**FACT:** Yes. Its link parameters are unique:
- `snr_db: -5.7` (negative — degraded signal quality, below 0 dB)
- `link_stability: 0.45` (high instability)
- `remaining_window_s: 90.0` (short contact window)
- `risk_score: 0.6`, `risk_level: "HIGH"`

This contrasts with `nominal_pass.json`:
- `snr_db: 12.0` (healthy signal)
- `link_stability: 0.92` (high stability)
- `remaining_window_s: 300.0` (normal contact window)
- `risk_score: 0.1`, `risk_level: "LOW"`

**INFERENCE.** These are not redundant. `degraded_link.json` exercises the transmission simulator and plan evaluator under adversarial link conditions that `nominal_pass.json` cannot provide. The E2E test `test_degraded_link_scenario_loads_and_evaluates` specifically exercises the full plan-generation pipeline against a degraded link.

---

### 6.5 `nominal_pass.json`

**Q1: Can normal user-facing UI select it?**  
**FACT:** No. Not in source catalog.

**Q2: Can the backend still load it?**  
**FACT:** Yes.

**Q3: Can `/scenarios/switch` load it?**  
**FACT:** Yes. An integration test explicitly verifies this (`test_scenario_path_and_security.py` line 209).

**Q4: Can `GCSI_SCENARIO_PATH` load it?**  
**FACT:** Yes. `.env.example` uses it as an example.

**Q5: Is it involved in normal startup?**  
**FACT:** No. However, `backend/app/state.py` module docstring uses `load_scenario("data/scenarios/nominal_pass.json")` as the canonical usage example. This is a documentation dependency, not a runtime dependency.

**Q6: Is it used in automated tests?**  
**FACT:** Yes — heavily. The **most widely used test fixture in the codebase**: 91 occurrences across 30+ test files. It is the only scenario used in:
- `test_scenario_randomizer.py`
- `test_scenario_e2e.py` (3 of 4 tests)
- All basic API integration tests: `test_api_plans.py`, `test_api_simulate.py`, `test_api_queue.py`, `test_api_state.py`
- `test_ai_providers.py` (6 test functions)
- `test_agent.py`
- `test_phase6ec6_runtime_source_activation.py` (17 test functions)
- `test_phase6ec6_historical_runtime.py` (5 test functions)
- All `test_phase2e_*` backward-compatibility tests

**Q7: Is it used in benchmark methodology?**  
**FACT:** No.

**Q8: Is it used by a tool/script?**  
**FACT:** No.

**Q9: Is it referenced by current documentation?**  
**FACT:** Yes — `backend/app/state.py` docstring (developer-facing), `.env.example`, and README line 469.

**Q10: Is it a hardcoded regression value anchor?**  
**FACT:** Yes. `test_phase2e_c1.py` line 186 asserts `queued_data_bits == 349_184`. This value is derived from the specific packet sizes in `nominal_pass.json` (sum of 4,096 + 65,536 + 1,024 + 16,384 + 262,144 = 349,184 bits). Any change to `nominal_pass.json` content would break this regression.

**INFERENCE.** `nominal_pass.json` is the most broadly depended-upon test fixture in the codebase. It is effectively the "default test scenario" for all backend integration tests and many unit tests, even though it is not the production default. Removing or moving it would require updating 30+ test files.

---

## 7. Dependency Matrix

| Attribute | ASTERIA-7 | mission_data_v3 | mission_data_v2 | degraded_link | nominal_pass |
|---|:---:|:---:|:---:|:---:|:---:|
| **In source catalog** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Production UI selectable** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Default startup scenario** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Backend loadable (load_scenario)** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **GET /scenarios discoverable** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **POST /scenarios/switch loadable** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **GCSI_SCENARIO_PATH loadable** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Backend tests (direct reference)** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Frontend tests** | ✅ | ✅ | ✅ | ❌ | ✅ |
| **Backend integration tests** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Benchmark config reference** | ❌ | ✅ (frozen) | ❌ | ❌ | ❌ |
| **Benchmark runner hard-codes path** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Benchmark results embed hash** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Frozen SHA256 in test** | ❌ | ✅ | ❌ | ❌ | ❌ |
| **Hardcoded regression value** | ❌ | ✅ (hash) | ❌ | ❌ | ✅ (bits=349,184) |
| **Tools/scripts** | ✅ (generator) | ✅ (runner) | ❌ | ❌ | ❌ |
| **Current documentation** | ✅ | ✅ | ✅ (README) | ✅ (README) | ✅ (state.py docstring) |
| **test count (approx)** | ~30 files | ~25 files | ~12 files | 2 files | ~33 files |
| **Unique test condition** | High-volume thermal | High-volume v3 | Intermediate schema, no dist_km | Degraded link/SNR | Nominal packets |
| **Must remain byte-stable** | ✅ | ✅ (hash frozen) | ❌ | ❌ | INFERENCE: yes (regression value) |
| **Must remain path-stable** | ✅ | ✅ | INFERENCE: yes (tests) | INFERENCE: yes (tests) | INFERENCE: yes (tests) |

---

## 8. Legacy `/scenarios` API Analysis

### Routes

```
GET  /scenarios           → lists all *.json in data/scenarios/
POST /scenarios/switch    → loads named file from data/scenarios/
```

Implemented in `backend/app/api/routes_data_products.py`.

### A. Are they called by production frontend after Phase 7B?

**FACT:** No production frontend component calls `GET /scenarios` or `POST /scenarios/switch` after Phase 7B. The production user-facing paths are `GET /sources` and `POST /sources/select`.

**FACT:** `listScenarios()` is exported in `client.ts` line 156 but no production component calls it. **INFERENCE:** It is dead code in the production UI path.

**FACT:** `switchScenario()` is exported in `client.ts` line 172 but no production component calls it. The Phase 7B test explicitly asserts it is **not called** by any recovery or selection action.

### B. Are they called only by tests/tooling?

**FACT:** Yes, after Phase 7B. The `GET /scenarios` and `POST /scenarios/switch` routes are tested by:
- `test_data_products_and_scenarios.py` (primary test suite)
- `test_scenario_path_and_security.py`
- `test_1011_fixes.py`
- `test_phase4_2a_asteria.py`

### C. Does GET /scenarios still expose all five JSON files?

**FACT:** Yes. The route globs `data/scenarios/*.json` and returns all five files. Tests assert that `mission_data_v3.json` and `nominal_pass.json` appear in the listing.

### D. Is POST /scenarios/switch required by tests?

**FACT:** Yes. Multiple integration tests use `POST /scenarios/switch` directly with filenames `mission_data_v3.json`, `nominal_pass.json`, and security-test filenames.

### E. Is switchScenario() imported anywhere in production code?

**FACT:** `switchScenario` is defined in `client.ts` and imported by test mocks (`vi.fn()`). It is **not imported or called** in any production component (`MissionControl.tsx`, `App.tsx`, or any component file).

**INFERENCE:** `switchScenario()` in `client.ts` is an exported function that is not currently used by production frontend code. It could be made internal/private in a future phase without breaking any production behavior.

### F. Is this compatibility API documented as public/current or legacy/internal?

**FACT:** `docs/api_overview.md` lists `GET /scenarios` and `POST /scenarios/switch` without any "legacy" or "deprecated" marker.

**FACT:** `docs/juno_pj62_historical_replay_demo.md` line 518 uses `POST /scenarios/switch` as an example of switching from historical to synthetic mode — implying it is still a valid developer tool even if not the primary UI path.

**INFERENCE:** The API is currently undifferentiated in public documentation. There is no "deprecated" or "internal" marker.

### G. Would removing the API today break anything?

**FACT:** Removing these routes today would break at least:
- `test_data_products_and_scenarios.py` — primary integration test suite
- `test_scenario_path_and_security.py`
- `test_1011_fixes.py`
- Frontend tests that mock `switchScenario`

**RECOMMENDATION (evidence-based, for a future phase):**

`KEEP BUT MARK INTERNAL/DEVELOPER COMPATIBILITY`

Rationale:
- The routes are not part of user-facing production flow after Phase 7B.
- They are used by developer tools (developer scenario switching, testing).
- Multiple tests depend on them.
- `docs/juno_pj62_historical_replay_demo.md` references them for developer switching.
- Removing them would require test refactoring.
- Marking them as "developer/internal" in the API overview and adding a deprecation notice would accurately describe their current role without breaking anything.

**This recommendation must not be implemented in Phase 7C.**

---

## 9. Move/Delete Impact Analysis

### 9.1 If `mission_data_v3.json` were moved to `tests/fixtures/scenarios/`

**Breaks:**
- `benchmarks/configs/gcsi_benchmark_v1.json` (frozen config, line 4: `"base_scenario": "data/scenarios/mission_data_v3.json"`)
- `backend/app/benchmark/runner_cli.py` (hard-coded path candidates, lines 64–66)
- `backend/app/main.py` line 172 (startup banner reference path)
- `backend/app/benchmark/models.py` line 164 (default field value references `base_scenario_id`)
- All ~25 test files that use `_V3_PATH = Path(...) / "data" / "scenarios" / "mission_data_v3.json"`
- `tests/integration/test_data_products_and_scenarios.py` line 197 (GET /scenarios listing assertion)
- `GET /scenarios` response (no longer discoverable)
- `POST /scenarios/switch` for this filename (no longer present in directory)
- `docs/benchmark_methodology.md`, `docs/architecture.md`, README

**Stays the same if documented as internal/legacy without moving:**
No technical downside. The file remains loadable, the benchmark runner finds it, tests pass, and the GET /scenarios listing still includes it. Documentation can note it is not user-facing.

### 9.2 If `mission_data_v2.json` were moved to `tests/fixtures/scenarios/`

**Breaks:**
- All ~12 test files with `Path(...) / "data" / "scenarios" / "mission_data_v2.json"` paths
- `test_candidate_generator.py` ScenarioLoaderV2 test class
- `GET /scenarios` listing (no longer discoverable)
- Any test asserting the file appears in the scenarios listing

**Stays the same if documented as internal/legacy without moving:**
No technical downside. All tests continue to pass.

### 9.3 If `degraded_link.json` were moved to `tests/fixtures/scenarios/`

**Breaks:**
- `test_candidate_generator.py` line 186: `Path(...) / "data" / "scenarios" / "degraded_link.json"`
- `test_scenario_e2e.py` line 106: `app_state.load_scenario("data/scenarios/degraded_link.json")`
- `GET /scenarios` listing

**Stays the same if documented as internal/legacy without moving:**
No technical downside.

### 9.4 If `nominal_pass.json` were moved to `tests/fixtures/scenarios/`

**Breaks:**
- All ~33 test files that reference `"data/scenarios/nominal_pass.json"` by path
- `backend/app/state.py` docstring (documentation only, not runtime)
- `.env.example` examples (documentation only)
- `GET /scenarios` listing assertion (`test_data_products_and_scenarios.py` line 204)
- `POST /scenarios/switch` for this filename

**Stays the same if documented as internal/legacy without moving:**
No technical downside. All tests pass, all backend behavior remains identical.

### 9.5 Summary

| File | Move would break | Stay in place but mark legacy: any downside? |
|---|---|---|
| ASTERIA-7 | Mission Source Catalog, CI, tests, experience manifest | N/A — must stay |
| mission_data_v3 | Frozen benchmark config, benchmark runner, ~25 test files, GET /scenarios listing | None |
| mission_data_v2 | ~12 test files, GET /scenarios listing | None |
| degraded_link | 2 test files, GET /scenarios listing | None |
| nominal_pass | ~33 test files, state.py docstring, GET /scenarios listing | None |

---

## 10. Recommended Disposition for Each File

**These are recommendations for future phases. Nothing is changed in Phase 7C.**

### ASTERIA-7 — `A: KEEP IN PLACE`

**Evidence:** Canonical production source. Path stability required by source catalog, CI, and experience manifest.  
**Risk if moved:** Production breakage.  
**Prerequisite to act:** None — do not move.

---

### `mission_data_v3.json` — `E: FROZEN — DO NOT MOVE`

**Evidence:** SHA256 integrity test in two test files. Frozen benchmark config references exact path. Benchmark runner hard-codes path candidates. Benchmark result records embed the SHA256. Docs declare it frozen ("UNCHANGED from original").

**Classification:** benchmark fixture + frozen regression input

**Risk if moved:** Breaks frozen benchmark config (which itself must not be modified), benchmark runner, all benchmark unit tests, and approximately 25 additional test files.

**Prerequisite before acting:** Cannot be acted upon without:
1. Resolving the SHA256 discrepancy between test assertions and benchmark run records.
2. Updating `benchmarks/configs/gcsi_benchmark_v1.json` (which is itself declared frozen).
3. Updating `backend/app/benchmark/runner_cli.py`.
4. Updating all test files.
These prerequisites represent a significant multi-file change incompatible with Phase 7C scope.

**Note:** If only documentation were to be updated to mark it as internal/legacy, there is no technical downside. The file would stay loadable and discoverable.

---

### `mission_data_v2.json` — `C: CANDIDATE TO MOVE`

**Evidence:** Active backward-compatibility test fixture. Provides unique schema (absent `distance_km`, data_products format). Not referenced in benchmark tooling. Not in production source catalog.

**Classification:** test fixture + schema backward-compatibility regression fixture

**Risk if moved:** Breaks 12 test files (all with hardcoded `data/scenarios/mission_data_v2.json` path). Tests would need path updates.

**No technical downside to leaving in place** with an internal/legacy documentation marker.

**Prerequisites before moving:** Update all path references in 12 test files. Update GET /scenarios listing assertions if any assert its presence.

---

### `degraded_link.json` — `C: CANDIDATE TO MOVE`

**Evidence:** Provides unique degraded-link parameters. Used in 2 test files. No benchmark dependency. Not in production source catalog.

**Classification:** test fixture (unique degraded-link regression)

**Risk if moved:** Breaks 2 test files (path references). Minimal blast radius.

**No technical downside to leaving in place** with an internal/legacy documentation marker.

**Prerequisites before moving:** Update 2 test file path references.

---

### `nominal_pass.json` — `B: KEEP BUT RECLASSIFY`

**Evidence:** Broadest dependency footprint of any non-canonical scenario (33+ test files). Primary test fixture for all basic API integration tests. Hardcoded regression value (`349_184 bits`). Used in randomizer tests, AI provider tests, source-activation tests. Referenced in `state.py` docstring and `.env.example`.

**Classification:** primary test fixture

**Conceptual home:** Tests — not a production mission source. Its current location in `data/scenarios/` makes it discoverable via `GET /scenarios`, which is technically not wrong but conceptually misleading for a test fixture.

**Risk of moving:** Would break 33+ test files. Very high blast radius.

**No technical downside to leaving in place** with reclassification in documentation as "primary test fixture, not a mission source."

**Recommended action (future phase):** Document it clearly as a test fixture in `README.md` and any developer guide. Consider a multi-phase test-file migration to `tests/fixtures/scenarios/` at low risk.

---

## 11. Risks

1. **SHA256 discrepancy in `mission_data_v3.json`**: The hash in frozen test assertions (`dea5339...`) differs from the hash embedded in `benchmarks/results/raw_results.jsonl` (`de43388...`). This inconsistency means either (a) the file changed after the benchmark run, or (b) the benchmark was run from a different version. This must be investigated before any future freeze-stability claim.

2. **Stale test comment**: `test_data_products_and_scenarios.py` line 14 says "Default startup uses mission_data_v3.json" — this is false after Phase 4.2A. This stale comment could mislead future engineers. Should be corrected in a future phase.

3. **Broad nominal_pass.json blast radius**: Any move or modification of `nominal_pass.json` would require changes to 33+ test files simultaneously. This is a risk for any future migration phase.

4. **Frozen benchmark config**: `benchmarks/configs/gcsi_benchmark_v1.json` is marked as frozen with its own SHA256 integrity check. Any future migration of `mission_data_v3.json` would require this frozen config to be updated, which may conflict with its "do not edit after running" constraint.

5. **switchScenario() dead code**: The `switchScenario()` function in `client.ts` is exported but not called by any production component. It is a dead code path in the production UI. Tests mock it but do not exercise it through the real network path. This should be cleaned up in a future phase.

6. **GET /scenarios listing without filtering**: The `GET /scenarios` route returns all *.json files including files that are not intended as user-facing mission sources. This could be confusing in documentation and developer experience. A future phase could add documentation or filtering.

---

## 12. Recommended Next Phase

**Phase 7D** should be limited to the following, in increasing risk order:

**Tier 1 (documentation-only, zero technical risk):**
1. Add explicit "internal/legacy test fixture" markers to README for `nominal_pass.json`, `degraded_link.json`, `mission_data_v2.json`.
2. Correct the stale comment in `test_data_products_and_scenarios.py` line 14.
3. Update `docs/api_overview.md` to mark `GET /scenarios` and `POST /scenarios/switch` as developer/internal compatibility APIs.
4. Mark `switchScenario()` in `client.ts` with a deprecation comment.

**Tier 2 (low-risk test refactoring):**
5. Migrate `degraded_link.json` to `tests/fixtures/scenarios/` (update 2 test files).
6. Migrate `mission_data_v2.json` to `tests/fixtures/scenarios/` (update ~12 test files).

**Tier 3 (high-risk, requires SHA256 investigation first):**
7. Investigate and resolve the `mission_data_v3.json` SHA256 discrepancy.
8. Only after resolution: consider whether to migrate it — blocked by frozen benchmark config.

**Do not proceed to Phase 7D until Phase 7C audit is committed and reviewed.**

---

## 13. No-Change Confirmation

> **No scenario files or runtime behavior were changed in Phase 7C.**

This phase adds exactly one file:

```
docs/phase7c_legacy_scenario_dependency_audit.md
```

No production source code, no test code, no scenario JSON, no benchmark configuration, no CI configuration, no README, and no other existing files were modified.

All five scenario files (`asteria7_thermal_priority_contact_v1.json`, `mission_data_v3.json`, `mission_data_v2.json`, `degraded_link.json`, `nominal_pass.json`) remain byte-identical to their state at HEAD `95d4d54`.

---

*This document was produced by static analysis, grep, file reads, and git history inspection only. No tests were modified. No production behavior was changed.*
