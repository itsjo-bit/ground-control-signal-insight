# Phase 7D — Benchmark Provenance Integrity

> **Status**: Complete. Investigation and correction committed at Phase 7D.
> No scenario bytes were modified.

---

## 1. Purpose

Phase 7C identified a "SHA256 discrepancy" for `data/scenarios/mission_data_v3.json`,
reporting two different hash values in two different locations:

| Location | SHA256 prefix | Value type |
|---|---|---|
| `test_phase4_2e_ground_reception.py` and `test_phase4_2f5_ground_reception.py` | `dea5339...` | Raw file bytes |
| `benchmarks/results/run-20260826-110706-530179c2/manifest.json` and `raw_results.jsonl` | `de43388...` | Pydantic model JSON |

Phase 7D was tasked with determining which, if either, is incorrect and resolving
the inconsistency.

---

## 2. Root Cause — FACT

**These two hashes measure different representations of the same source file.
There is no data corruption, no historical version discrepancy, and no benchmark
integrity violation.**

The distinction is:

### `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9`
**SHA256 of the raw file bytes** of `data/scenarios/mission_data_v3.json`.

Computed as:
```python
hashlib.sha256(Path("data/scenarios/mission_data_v3.json").read_bytes()).hexdigest()
```

Used by:
- `tests/unit/test_phase4_2e_ground_reception.py` (`_V3_SHA256`)
- `tests/unit/test_phase4_2f5_ground_reception.py` (`_V3_SHA256`)

These tests enforce that the file has not been modified on disk.

### `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08`
**SHA256 of `scenario.model_dump_json()`** — the Pydantic-parsed and re-serialized
JSON representation of the loaded `Scenario` object.

Computed by `_sha256_scenario()` in `backend/app/benchmark/scenario_variants.py`:
```python
def _sha256_scenario(scenario: Scenario) -> str:
    raw = scenario.model_dump_json()
    return hashlib.sha256(raw.encode()).hexdigest()
```

Used by:
- `ScenarioVariantGenerator.base_sha256` property
- All `ScenarioVariantSpec.base_scenario_sha256` fields
- `benchmarks/results/run-20260826-110706-530179c2/manifest.json` (`base_scenario_sha256`)
- Every trial record in `benchmarks/results/run-20260826-110706-530179c2/raw_results.jsonl`

This hash identifies which **logical scenario object** was used as benchmark input.
It is structurally different from the file-byte hash because:
- Pydantic re-serializes the object with canonical field ordering
- JSON whitespace/formatting differences in the source file do not affect it
- Field defaults that are absent in the file may be included in model output

The two representations can never be equal unless the file happens to contain
exactly the same bytes that Pydantic's `model_dump_json()` would produce.

---

## 3. Git History Reconstruction — FACT

`git log --oneline --follow -- data/scenarios/mission_data_v3.json` reports only
one content-introducing commit:

```
f75b745  feat: finalize mission control AI demo
```

SHA256 of `mission_data_v3.json` at every inspected commit in the full repository
history is **identical**:

| Commit range | File SHA256 | File size |
|---|---|---|
| `f75b745` through `7a99c18` (HEAD) | `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` | 115,767 bytes |

**FACT**: `mission_data_v3.json` has never changed. The historical version confusion
reported by Phase 7C was caused by the PowerShell `git show … | python` pipeline
reading the commit message rather than the file bytes. The Python subprocess method
confirmed the above invariant across all commits.

---

## 4. Benchmark Provenance — FACT

The frozen benchmark result at `benchmarks/results/run-20260826-110706-530179c2/`
was committed at `bce0c61` ("Phase 3: telecom rigor and physical consistency").

The manifest records:
```json
"git_commit_sha": "unknown",
"git_dirty": true,
"base_scenario_sha256": "de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08"
```

The `git_dirty: true` flag confirms the benchmark was run from a dirty worktree
and committed afterward. `base_scenario_sha256` is the model-dump hash, not the
file-byte hash. This is the designed behaviour of `_sha256_scenario()`.

At commit `bce0c61`, `mission_data_v3.json` already had the file-byte SHA
`dea5339...`. The model-dump hash `de43388...` was therefore computed from
**the same bytes** as are frozen today. The frozen result is internally consistent.

---

## 5. Discrepancy Classification

**Classification: A — STALE BENCHMARK METADATA (documentation only)**

The benchmark metadata is correctly written by the runner. The test hash is correctly
enforced by the freeze tests. Phase 7C's description of them as contradictory was
itself incorrect — there is no semantic inconsistency in the repository.

The only real problem is the absence of documentation explaining that two different
hash subjects exist. That is corrected in Phase 7D.

---

## 6. Reproducibility Assessment

The frozen benchmark run (`run-20260826-110706-530179c2`) shows `run_type: "pilot"`
with `repetitions: 1` (configured: 5), and all trials have `status: "provider_error"`
(`IAM token request returned HTTP 400`). No successful Granite outputs were recorded.

**Exact reproduction of the committed benchmark output is not possible** because:
- External Granite API calls are required (network + credential dependency)
- The committed trials all failed with auth errors — no rankings were produced
- The `git_commit_sha` was recorded as "unknown" (dirty tree)

The **deterministic components** (scenario variant generation, plan construction,
metrics computation) can be reproduced from the current frozen `mission_data_v3.json`
bytes because the model-dump hash is stable (`de43388...`).

---

## 7. Authoritative Hash Constants

| Constant name | Value | Scope |
|---|---|---|
| File-byte SHA256 | `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` | Enforced by freeze tests in `test_phase4_2e` and `test_phase4_2f5` |
| Model-dump SHA256 | `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08` | Recorded in benchmark manifest and all trial records; enforced by Phase 7D regression tests |

Both values are pinned in `tests/unit/benchmark/test_scenario_variants.py`
(class `TestBenchmarkProvenanceIntegrity`) as of Phase 7D.

---

## 8. Integrity Enforcement After Phase 7D

The repository now enforces provenance through:

1. **File-byte freeze tests** (pre-existing):
   - `test_phase4_2e_ground_reception.py::TestFreezeIntegrityGates::test_mission_data_v3_exact_sha256`
   - `test_phase4_2f5_ground_reception.py::TestFreezeVerification::test_mission_data_v3_exact_sha256`

2. **Benchmark provenance regression tests** (added Phase 7D):
   - `test_scenario_variants.py::TestBenchmarkProvenanceIntegrity::test_file_bytes_sha256_matches_frozen_constant`
   - `test_scenario_variants.py::TestBenchmarkProvenanceIntegrity::test_model_dump_json_sha256_matches_frozen_constant`
   - `test_scenario_variants.py::TestBenchmarkProvenanceIntegrity::test_frozen_manifest_base_scenario_sha256_matches_model_hash`
   - `test_scenario_variants.py::TestBenchmarkProvenanceIntegrity::test_two_sha256_values_differ_by_design`

3. **Documentation** (updated Phase 7D):
   - `docs/benchmark_methodology.md` Section 12 — explicit note on `base_scenario_sha256` hash semantics

---

## 9. Why mission_data_v3.json Must Remain Frozen

`mission_data_v3.json` is frozen for the following reasons:

- It is the `base_scenario` in `benchmarks/configs/gcsi_benchmark_v1.json`
  (marked "FROZEN before results. Do not edit after running.")
- Its path is hard-coded in `backend/app/benchmark/runner_cli.py`
- Its file-byte SHA256 is hard-coded in two test files
- Every trial record in the frozen benchmark result embeds its model-dump SHA256
- Any modification would invalidate the connection between the frozen benchmark
  evidence and the scenario that generated it

---

## 10. ASTERIA-7 vs mission_data_v3 — Distinction

| Property | mission_data_v3.json | ASTERIA-7 |
|---|---|---|
| User-facing scenario | NO | YES — canonical default |
| Benchmark base scenario | YES — frozen | NO |
| Appears in Mission Source Catalog | NO | YES |
| File-byte SHA256 frozen | YES | YES (independent test) |
| Default startup scenario | NO | YES |
| Developer override (`GCSI_SCENARIO_PATH`) | YES (documented) | YES (default) |
| Phase 7D touched | Documented/tested only | NOT TOUCHED |

---

## 11. Corrections Made in Phase 7D

| Item | Change |
|---|---|
| `tests/integration/test_data_products_and_scenarios.py` line 14 | Stale comment "Default startup uses mission_data_v3.json" → "Default startup uses ASTERIA-7; mission_data_v3.json is the frozen benchmark input, not the default" |
| `docs/benchmark_methodology.md` Section 12 | Added explicit note explaining `base_scenario_sha256` hash semantics (model-dump, not file bytes) |
| `tests/unit/benchmark/test_scenario_variants.py` | Added `TestBenchmarkProvenanceIntegrity` class with four regression tests |
| `docs/phase7d_benchmark_provenance_integrity.md` | Created this document |

**No scenario files were modified. No benchmark results were modified.
No test expectations were weakened.**
