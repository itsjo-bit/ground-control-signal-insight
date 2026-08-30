# GCSI Mission Source Architecture

> **Phase 7E — Authoritative Reference**
>
> This document is the single authoritative source for the classification of
> mission sources and scenario files in GCSI.  It supersedes any ambiguous
> language in earlier phase-specific documents.

---

## Core Distinction

GCSI contains two distinct concepts that must not be confused:

### 1. Mission Source Catalog — Canonical User-Facing Sources

Exposed through the Mission Source Catalog API.  These are the sources a user
can select through the product UI.

```
                         USER
                           |
                  Mission Source Catalog
                           |
                    GET /sources
                 POST /sources/select
                           |
             +-------------+--------------+
             |             |              |
         ASTERIA-7     Juno PJ62 V1   Juno PJ62 V2
      (synthetic)     (historical)   (historical)
```

Exactly **three** canonical sources.  No other file is a Mission Source Catalog entry.

### 2. Internal / Compatibility Scenario Infrastructure

Stored under `data/scenarios/` and accessible through the compatibility API.
These are **not** user-facing and are **not** Mission Source Catalog entries.

```
             INTERNAL / DEVELOPMENT / TESTING

                    GET /scenarios
                POST /scenarios/switch
                           |
                   data/scenarios/*.json
                           |
       +-------------------+----------------------+
       |              |             |             |
 mission_data_v3  mission_data_v2 degraded    nominal_pass
 frozen          compatibility   regression  regression
 benchmark       fixture         fixture     fixture
```

ASTERIA-7 also physically lives under `data/scenarios/` because its canonical
source adapter loads that scenario file.  **Physical location does NOT imply
user-facing source status.**

---

## Canonical Terminology

| Term | Definition |
|---|---|
| **Mission Source** | A supported user-facing source registered in the Mission Source Catalog. |
| **Canonical Source** | A source intentionally exposed by `/sources` and selectable by the product UI. |
| **Scenario File** | A JSON input file understood by the synthetic scenario loader. |
| **Legacy / Compatibility Scenario** | A scenario file retained for regression, benchmark, tests, or backward compatibility but NOT exposed as a Mission Source Catalog entry. |
| **Historical Replay** | A canonical mission source backed by verified historical replay descriptors — not a legacy scenario file. |

*Legacy ≠ broken.  Legacy ≠ scheduled for deletion.  Legacy means the interface
is retained for compatibility but is not the canonical product source-selection interface.*

---

## API Roles

### Canonical Source Selection API

| Endpoint | Role |
|---|---|
| `GET /sources` | Returns the Mission Source Catalog — exactly three user-facing sources |
| `POST /sources/select` | Selects a catalog source by `source_id`; activates the corresponding scenario or historical replay |

These are the production endpoints used by the frontend UI.

### Internal / Compatibility Scenario API

| Endpoint | Role |
|---|---|
| `GET /scenarios` | Lists all files under `data/scenarios/` — includes internal fixtures not in the catalog |
| `POST /scenarios/switch` | Compatibility scenario loader — retained for tests and developer tooling |

These endpoints are **not** the canonical product interface.  They are labeled
as **Internal Scenario Compatibility API** throughout the codebase.

---

## Frontend Client Roles

| Function | Module | Role |
|---|---|---|
| `selectSource(sourceId)` | `frontend/src/api/client.ts` | Canonical production source switch; calls `POST /sources/select` |
| `getSources()` | `frontend/src/api/client.ts` | Canonical Mission Source Catalog fetch; calls `GET /sources` |
| `switchScenario(filename)` | `frontend/src/api/client.ts` | **Compatibility only** — retained for tests/tooling; calls `POST /scenarios/switch`; NOT used by production UI |
| `listScenarios()` | `frontend/src/api/client.ts` | **Compatibility only** — retained for tests/tooling; calls `GET /scenarios` |

---

## Per-File Classification Table

| File | Role | User-facing? | Mutable? | Notes |
|---|---|---|---|---|
| `asteria7_thermal_priority_contact_v1.json` | Canonical synthetic backing file | **Yes** — via Mission Source Catalog (`asteria-7`) | Controlled/generated artifact | `source_id = asteria-7`; default startup scenario |
| `mission_data_v3.json` | Frozen benchmark / developer scenario | **No** | **Frozen** — do not modify | Benchmark base scenario; file-byte SHA-256 enforced by freeze tests; model-dump SHA-256 recorded in benchmark manifest |
| `mission_data_v2.json` | Compatibility / test scenario | **No** | Fixture | Backward-compatibility regression fixture; potential future relocation after dependency migration |
| `degraded_link.json` | Degraded legacy-packet regression fixture | **No** | Fixture | Unique degraded link condition; used in legacy-packet regression tests |
| `nominal_pass.json` | Nominal legacy-packet regression fixture | **No** | Fixture | Heavily used in regression tests; do not remove without full dependency audit |

---

## mission_data_v3.json — Authoritative Hash Constants

`mission_data_v3.json` is the frozen benchmark input.  Two SHA-256 values exist for it,
measuring two different representations of the same file.  Both are correct.

| Constant | Value | What it measures |
|---|---|---|
| File-byte SHA-256 | `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` | Raw bytes on disk — enforced by freeze tests in `test_phase4_2e` and `test_phase4_2f5` |
| Model-dump SHA-256 | `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08` | `scenario.model_dump_json()` Pydantic serialisation — recorded in benchmark manifest and all trial records |

The two values differ by design: Pydantic re-serialises with canonical field ordering and
may include defaults absent from the file.  See [`docs/phase7d_benchmark_provenance_integrity.md`](phase7d_benchmark_provenance_integrity.md).

---

## Recommended Disposition of Internal Scenario Files

### mission_data_v3.json — DO NOT MOVE

- Frozen benchmark input with SHA-256 locks in two separate test classes
- Hard-coded path in `backend/app/benchmark/runner_cli.py`
- Moving it would require updating multiple test hash constants and benchmark configs
- **Recommendation: leave in `data/scenarios/` indefinitely**

### nominal_pass.json — DO NOT MOVE

- Heavily used in regression tests
- Moving it requires updating all referencing test fixtures
- Benefit does not justify path churn
- **Recommendation: leave in `data/scenarios/`**

### mission_data_v2.json — OPTIONAL FUTURE RELOCATION

- Used primarily for backward-compatibility tests
- Could migrate to `tests/fixtures/scenarios/` if all test dependencies are updated
- **Recommendation: leave for now; revisit only if a clear benefit emerges**

### degraded_link.json — OPTIONAL FUTURE RELOCATION

- Unique degraded link condition; used in legacy-packet regression tests
- Could migrate to `tests/fixtures/scenarios/` if all test dependencies are updated
- **Recommendation: leave for now; revisit only if a clear benefit emerges**

---

## Whether Phase 7F Is Necessary

After Phase 7E reclassification:

- The architecture boundary is explicit and documented
- All internal scenario files are clearly labeled as non-user-facing
- No scenario files need to move for the architecture to be understandable
- `/scenarios` and `/scenarios/switch` are clearly labeled as compatibility APIs
- No dead compatibility code has been identified that warrants removal

**Conclusion: No Phase 7F scenario file relocation is necessary.**

If compatibility APIs naturally become unused in a future phase, their removal can be
evaluated at that time with a focused dependency audit.  That is a separate decision
from the classification work done in Phase 7E.

---

## Architectural Invariants (Enforced by Tests)

The following invariants are enforced by `tests/unit/test_phase7e_architecture_boundary.py`:

1. Mission Source Catalog contains exactly three entries: `asteria-7`, `juno-pj62-v1`, `juno-pj62-v2`
2. `mission_data_v3` is NOT a Mission Source Catalog entry
3. `mission_data_v2` is NOT a Mission Source Catalog entry
4. `degraded_link` is NOT a Mission Source Catalog entry
5. `nominal_pass` is NOT a Mission Source Catalog entry
6. All five scenario JSON files are present under `data/scenarios/`
7. Production `MissionControl.tsx` imports `selectSource`, not `switchScenario`

---

*GCSI Mission Source Architecture — Phase 7E*
