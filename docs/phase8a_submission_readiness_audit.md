# Phase 8A — Submission Readiness Audit

**Ground Control Signal Insight (GCSI) — Version 1.0.0**
**Audit Date**: 2026-08-26 (Phase 8A)
**Auditor**: Phase 8A automated audit process
**Scope**: Full pre-submission readiness assessment after Phase 7E

---

## 1. Executive Summary

GCSI is a well-engineered, thoroughly tested AI-assisted communication
decision-support system. The Phase 8A audit finds no release blockers. The
repository is substantially ready for demonstration and submission as an IBM
watsonx / Granite project.

**Release Recommendation: READY WITH KNOWN LIMITATIONS**

The known limitations are pre-disclosed, well-documented, technically honest,
and do not disqualify the submission. The primary limitation is that live
Granite efficacy results are pending valid IBM Cloud IAM credentials. This is
accurately and prominently disclosed in the README.

All critical acceptance criteria pass. No secrets were found in tracked files.
No path-traversal vulnerabilities were found. All canonically required tests
pass. All three mission sources are selectable and functional. The AI provider
selection hierarchy correctly identifies and labels the Local provider when no
credentials are present, never mislabeling deterministic fallback as Granite.

---

## 2. Repository Baseline

| Item | Value |
|---|---|
| Starting HEAD | `ace316b` |
| Commit message | `Phase 7E: classify legacy scenario infrastructure` |
| Branch | `main` |
| Remote tracking | `origin/main` — up to date |
| Working tree | Clean (no uncommitted changes at audit start) |
| Expected HEAD | `ace316b` ✓ matches |

```
ace316b Phase 7E: classify legacy scenario infrastructure
On branch main
Your branch is up to date with 'origin/main'.
nothing to commit, working tree clean
```

---

## 3. Test / Build Results

### 3.1 Backend Test Suite

| Metric | Result |
|---|---|
| Command | `python -m pytest tests -q --tb=short` |
| Collected | 5061 tests (1 deselected by `-m "not granite"`) |
| Passed | **5056** |
| Skipped | **4** |
| Failed | **0** |
| xfailed | 0 |
| Runtime | 219.29 s |
| Warnings | 20 (4× Pydantic `field name "schema" shadows parent` in v2 models; 7× `PytestRemovedIn10Warning` for class-scoped fixtures) |

All warnings are non-critical: the Pydantic warnings are pre-existing model
design choices; the pytest deprecation warnings relate to a future pytest
version (10.x is not yet released).

**Note on README count discrepancy**: The README states "3,590 backend tests
passing" (Phase 6E-C8 state). The current suite now collects **5,061 tests**
due to additional tests added in Phases 7A–7E. The README count is stale.
This is documented as a medium-severity finding and corrected below.

### 3.2 Frontend Test Suite

| Metric | Result |
|---|---|
| Command | `npm test` (from `frontend/`) |
| Framework | Vitest v4.1.11 |
| Test files | **12 passed** |
| Total tests | **363 passed** |
| Failed | **0** |
| Runtime | 8.08 s |
| Warnings | 2 deprecation warnings from `vite:react-babel` plugin (esbuild→oxc) — non-breaking |

### 3.3 TypeScript Typecheck

| Metric | Result |
|---|---|
| Command | `npm run typecheck` (= `tsc --noEmit`) |
| Result | **PASS** — no errors |

### 3.4 Production Build

| Metric | Result |
|---|---|
| Command | `npm run build` |
| Result | **PASS** — built in 5.95 s |
| Chunks | 667 modules transformed |
| Bundle size | `index-BYijkwsr.js` 1,356 kB minified / 357 kB gzip |
| Warning | Chunk size warning (> 500 kB after minification) — cosmetic, not a build failure |

---

## 4. Fresh-Clone Setup Assessment

A first-time judge or developer can follow the README to:

1. ✅ Clone the repository
2. ✅ Create a Python virtual environment (`.venv`)
3. ✅ Install backend: `cd backend && pip install -e ".[dev]"` — confirmed by `pyproject.toml`
4. ✅ Configure AI credentials (optional) via `cp .env.example .env` — clearly documented
5. ✅ Start backend: `cd backend && uvicorn app.main:app --reload --port 8000` — confirmed working
6. ✅ Start frontend: `cd frontend && npm install && npm run dev` — confirmed working
7. ✅ Python version: `3.11+` specified in `.python-version` (file reads `3.11`)
8. ✅ Node version: `24` specified in `.nvmrc` (file reads `24`); `package.json` `engines.node >= 24`
9. ✅ Backend tests: `python -m pytest` from project root
10. ✅ Frontend tests: `npm test` from `frontend/`
11. ✅ Offline demo: Local provider works with no credentials (clearly documented)
12. ✅ Granite path: IBM credentials documented with setup instructions
13. ✅ Historical replay: PowerShell and Bash commands both documented

**Identified discrepancy (MEDIUM)**: The README documents "3,590 backend tests
passing" from Phase 6E-C8. The current suite passes 5,056 tests. This is stale
but harmless — the README correctly instructs "Reproduce them with the canonical
commands above rather than relying on this static count."

**Fixed in Phase 8A**: README test count updated from `3,590` to `5,056` and
frontend count from `218` to `363`. This is a factually inaccurate static claim
that could mislead a judge who counts tests and finds a discrepancy.

---

## 5. Configuration / Environment Assessment

| Variable | Required? | Default | Sensitive? | Used by | Documented? | Safe example? |
|---|---|---|---|---|---|---|
| `GCSI_GRANITE_API_KEY` | Optional | — | ✅ Yes | GraniteProvider | ✅ Yes | ✅ Yes (empty placeholder) |
| `GCSI_GRANITE_PROJECT_ID` | Optional | — | ✅ Yes | GraniteProvider | ✅ Yes | ✅ Yes |
| `GCSI_GRANITE_API_URL` | Optional | `https://us-south.ml.cloud.ibm.com/ml/v1/text/generation` | No | GraniteProvider | ✅ Yes | ✅ Yes |
| `GCSI_GRANITE_MODEL_ID` | Optional | `ibm/granite-4-h-small` | No | GraniteProvider | ✅ Yes | ✅ Yes |
| `GCSI_GRANITE_IAM_URL` | Optional | `https://iam.cloud.ibm.com/identity/token` | No | GraniteAgent | ✅ Yes | ✅ Yes |
| `GCSI_GEMINI_API_KEY` | Optional | — | ✅ Yes | GeminiProvider | ✅ Yes | ✅ Yes |
| `GCSI_GEMINI_MODEL` | Optional | `gemini-2.0-flash` | No | GeminiProvider | ✅ Yes | ✅ Yes |
| `GCSI_OLLAMA_ENABLED` | Optional | `false` | No | OllamaProvider | ✅ Yes | ✅ Yes |
| `GCSI_OLLAMA_URL` | Optional | `http://localhost:11434` | No | OllamaProvider | ✅ Yes | ✅ Yes |
| `GCSI_OLLAMA_MODEL` | Optional | `llama3.2` | No | OllamaProvider | ✅ Yes | ✅ Yes |
| `GCSI_AI_PROVIDER` | Optional | auto | No | provider_factory | ✅ Yes | ✅ Yes |
| `GCSI_AI_MAX_CANDIDATES` | Optional | `50` | No | CandidatePrioritizer | ✅ Yes | ✅ Yes |
| `GCSI_SCENARIO_PATH` | Optional | `asteria7_thermal_priority_contact_v1.json` | No | main.py startup | ✅ Yes | ✅ Yes |
| `GCSI_SCENARIOS_DIR` | Optional | `data/scenarios` | No | routes_data_products | ✅ Yes | ✅ Yes |
| `GCSI_SOURCE_MODE` | Optional | `synthetic_scenario` | No | main.py startup | ✅ Yes | ✅ Yes |
| `GCSI_REPLAY_DESCRIPTOR` | Conditional | — | No | main.py startup | ✅ Yes | ✅ Yes |

**Security hygiene**:
- `.env` is in `.gitignore` — confirmed
- `.env.example` contains only empty placeholders — confirmed
- No real credentials found in any tracked file
- README explicitly warns "Never commit .env to version control"
- `.env.example` header states "NEVER commit .env to version control"

---

## 6. API Smoke-Test Results

All tests run via FastAPI `TestClient` (using app lifespan context).

| Endpoint | Method | Status | Result |
|---|---|---|---|
| `/health` | GET | 200 | ✅ `{"status": "ok", "version": "1.0.0", "data_products_count": 1284}` on lifespan start |
| `/state` | GET | 503 before load | ✅ Returns 503 until source selected |
| `/state` | GET | 200 after load | ✅ Returns full mission state including `source` sub-object |
| `/sources` | GET | 200 | ✅ Returns 3 canonical sources: asteria-7, juno-pj62-v1, juno-pj62-v2 |
| `/sources/select` | POST | 200 | ✅ ASTERIA-7 → Juno V1 → Juno V2 → ASTERIA-7 all succeed |
| `/data-products` | GET | 200 | ✅ 1284 products for ASTERIA-7 (verified by integration tests) |
| `/scenarios` | GET | 200 | ✅ Returns 5 scenario files (including internal fixtures) |
| `/scenarios/switch` | POST | 200 | ✅ Switches by `filename` field (compatibility API) |
| `/plans/generate` | POST | 200 | ✅ Returns 4 deterministic plans (integration tests pass) |
| `/agent/recommend` | POST | 200 | ✅ Returns local recommendation without credentials |

**Note**: `/queue/plans` returns 404 — this endpoint is not in the API. The
correct endpoint is `/queue` (candidate summary) and `/plans/generate`.
No test or documentation references `/queue/plans` as a live endpoint; this
was a smoke-test probe error, not a product defect.

### Source-switching E2E

```
POST /sources/select {"source_id": "asteria-7"}   → 200, active_source_id: asteria-7,  mode: synthetic_scenario
POST /sources/select {"source_id": "juno-pj62-v1"} → 200, active_source_id: juno-pj62-v1, mode: historical_replay
POST /sources/select {"source_id": "juno-pj62-v2"} → 200, active_source_id: juno-pj62-v2, mode: historical_replay
POST /sources/select {"source_id": "asteria-7"}   → 200, active_source_id: asteria-7,  mode: synthetic_scenario
```

All switches succeeded. Historical mode correctly labeled. Atomicity tests pass.

---

## 7. Mission Source Validation

### 7.1 Catalog Listing (`GET /sources`)

```json
{
  "active_source_id": null,
  "sources": [
    {"source_id": "asteria-7",     "mode": "synthetic_scenario", "historical": false, "simulated": true},
    {"source_id": "juno-pj62-v1",  "mode": "historical_replay",  "historical": true,  "simulated": true},
    {"source_id": "juno-pj62-v2",  "mode": "historical_replay",  "historical": true,  "simulated": true}
  ]
}
```

✅ Exactly three canonical sources listed.
✅ Mode labels are correct (`synthetic_scenario` vs `historical_replay`).
✅ `historical` flag correctly distinguishes sources.
✅ No internal scenario files exposed.

### 7.2 Per-Source Validation

| Source | Selection | active_source_id | Mode | Data Products | Provenance |
|---|---|---|---|---|---|
| ASTERIA-7 | ✅ 200 | `asteria-7` | `synthetic_scenario` | 1284 (integration verified) | `synthetic` — no external provenance |
| Juno PJ62 V1 | ✅ 200 | `juno-pj62-v1` | `historical_replay` | 2 (MWR IRDR + GRDR) | Verified NASA/JPL/PDS snapshots |
| Juno PJ62 V2 | ✅ 200 | `juno-pj62-v2` | `historical_replay` | 403 (large multi-instrument replay) | Verified PDS archive snapshots |

All integration tests for source selection pass (74 tests in `test_source_switcher.py`,
132 total including historical replay tests).

### 7.3 Stale-State Guard

Tests confirm that switching sources invalidates AI and approval state (verified
by `TestResetAfterSwitch` and `TestFailureAtomicity`). Cross-source stale-result
guard identity is preserved.

---

## 8. AI / Granite Readiness

### 8.1 Provider Architecture

| Path | Status |
|---|---|
| IBM Granite (primary) | Code/config READY; live credentials NOT verified |
| Google Gemini (optional) | Code/config READY; live credentials NOT verified |
| Ollama (optional) | Code/config READY; server NOT tested |
| Local (default) | VERIFIED — deterministic, offline, no credentials |

### 8.2 Provider Selection Logic

`provider_factory.py::get_provider()` implements:
1. Explicit override via `GCSI_AI_PROVIDER`
2. Auto: Granite if `GCSI_GRANITE_API_KEY` set → Gemini if `GCSI_GEMINI_API_KEY` set → Ollama if enabled and reachable → Local

**VERIFIED**: `LocalRuleBasedProvider.provider_name` = `"Local"` (not `"Granite"`).
Test `test_recommend_provider_is_local_without_key` **PASSES**.

### 8.3 Provider Labeling Truthfulness

- `LocalRuleBasedProvider.provider_name = "Local"` ✅
- `GraniteProvider.provider_name = "Granite"` ✅  
- `GeminiProvider.provider_name = "Gemini"` ✅
- Backend response includes `provider` field from authoritative source, never fabricated ✅
- Tests enforce that no substitution mislabeling occurs ✅

**CODE/CONFIGURATION READY**: ✅
**LIVE GRANITE CREDENTIALS VERIFIED**: ❌ (pending valid credentials)

---

## 9. Granite Failure UX

### 9.1 Failure Path Behavior

- When Granite credentials are absent: provider silently selects Local without crashing ✅
- `AIProviderError`, `AIResponseError`, `AIHallucinationError` hierarchy defined ✅
- Route layer is provider-agnostic; maps typed exceptions to HTTP responses ✅
- `test_recommend_returns_503_before_scenario_load` PASSES ✅

### 9.2 Previous Benchmark Authentication Failure

The committed benchmark run (`run-20260826-110706-530179c2`) correctly records
an IAM authentication failure. It is labeled:
- `"⚠ AUTHENTICATION FAILURE — NOT BENCHMARK EVIDENCE"`
- `"successful_trials": 0`
- `"Zero model inferences were completed"`

This is honest, non-overclaiming, and preserved as infrastructure provenance.

### 9.3 Deterministic Fallback Labeling

The `SourceContextBanner` component renders `"SYNTHETIC SCENARIO"` for synthetic
sources and `"HISTORICAL REPLAY"` for historical sources. The AI recommendation
response always carries the `provider` field. Tests in `SourceContextBanner.test.tsx`
confirm correct labeling in both modes. No silent provider substitution exists.

---

## 10. Historical Replay Truthfulness

### 10.1 UI Disclosure

`SourceContextBanner.tsx` explicitly renders for historical sources:
- `"HISTORICAL REPLAY"` headline
- `aria-label="Historical replay active - not live telemetry"`
- `"Simulated data - not real mission telemetry"` for synthetic sources

Tests (`SourceContextBanner.test.tsx`) verify:
- `HISTORICAL REPLAY` text rendered ✅
- "not-live-telemetry" wording rendered ✅
- NASA/JPL/PDS provider/source context rendered ✅
- Synthetic mode does NOT render historical warning ✅

### 10.2 README Disclosure

The README clearly states:
> "This is not live telemetry and not a reconstruction of an actual NASA
> transmission decision. GCSI uses verified archival facts to anchor the
> mission context. Communication constraints and product priority attributes
> are explicitly modeled GCSI policy — not NASA data."

This boundary is crisp and accurate.

### 10.3 Provenance Model

Data is classified as:
- `external_authoritative`: verified Horizons/PDS snapshots with SHA-256 locks
- `derived`: computed from authoritative inputs
- `modeled`: explicit GCSI policy (SNR, data rate, link stability)
- `synthetic`: ASTERIA-7 scenario data

No claim that reconstructed queue fields are raw NASA telemetry. ✅

---

## 11. ASTERIA-7 Truthfulness Audit

ASTERIA-7 is consistently presented as:
- "Fictional synthetic thermal-priority contact scenario" (sources API) ✅
- "Synthetic scenario (fictional mission)" (README table) ✅
- "ASTERIA-7 is a synthetic fictional mission; not affiliated with any real space agency" (Limitations) ✅
- `SourceContextBanner`: `"SYNTHETIC SCENARIO"` / `"Simulated data - not real mission telemetry"` ✅
- `ScenarioSwitcher.tsx`: mode badge = `"SYNTHETIC"` ✅

No claim that ASTERIA-7 is a real spacecraft or affiliated with any space agency. ✅

---

## 12. Benchmark Integrity

### 12.1 Freeze Hashes (verified live)

| Hash | Expected | Actual | Match |
|---|---|---|---|
| `mission_data_v3.json` file bytes SHA-256 | `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` | `dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9` | ✅ |
| `mission_data_v3` model_dump_json SHA-256 | `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08` | `de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08` | ✅ |

Both frozen benchmark artifacts are intact and unmodified.

### 12.2 Benchmark Integrity Tests

`test_phase2b1a_integrity.py` — 64 passed ✅  
`test_phase2a_integrity.py` — 13 passed (includes opposing-rankings ablation) ✅

### 12.3 Benchmark Status Honesty

The committed benchmark run is correctly labeled as an authentication failure
with zero successful inferences. The README states:
> "Granite efficacy result: Not available"
> "AI claim comparisons are architectural, not empirical."

No overclaim. The benchmark methodology is frozen and ready for execution
when valid credentials are available. ✅

**No benchmark results were regenerated during Phase 8A.** ✅

---

## 13. Security Assessment

### 13.1 Path Traversal

`test_scenario_path_and_security.py` — 19 passed ✅

Verified protections:
- Non-`.json` extension rejected (400/422)
- Path traversal `../` rejected
- Absolute paths rejected
- Symbolic path variants rejected
- Unknown files return 404

Source catalog uses allowlist of known `source_id` strings; arbitrary IDs
return 404 without filesystem access.

### 13.2 Source Selection Security

`test_source_catalog.py` — 10 tests pass including:
- `test_path_traversal_returns_none` ✅
- `test_absolute_path_returns_none` ✅
- `test_url_returns_none` ✅
- `test_percent_encoded_returns_none` ✅

### 13.3 Credentials Exposure

No secrets, API keys, bearer tokens, IAM tokens, private keys, or
service-account JSON found in any tracked file. ✅

Environment variable documentation uses empty placeholders only. ✅

### 13.4 Error Responses

Route layer maps typed exceptions to HTTP status codes; no internal paths
or stack traces are leaked in responses. ✅

---

## 14. Documentation Consistency

| Document | Status |
|---|---|
| `README.md` | Primary reference; accurate except test count (fixed in Phase 8A) |
| `docs/api_overview.md` | Accurate; lists all canonical endpoints with mutation labels |
| `docs/mission_source_architecture.md` | Authoritative (Phase 7E); correctly classifies 3 sources vs 5 scenario files |
| `docs/benchmark_methodology.md` | Accurate; methodology frozen and pre-registered |
| `docs/asteria7_demo.md` | Accurate mission parameters |
| `docs/juno_pj62_historical_replay_demo.md` | Accurate; includes both Bash and PowerShell commands |
| `docs/trust_boundary.md` | Accurate AI boundary documentation |
| `docs/telecom_model.md` | Present and referenced |
| `.bob/` rules | Up to date with Phase 7E architecture |
| `benchmarks/results/README.md` | Clearly labeled as authentication failure |
| CI `.github/workflows/ci.yml` | Matches documented commands; `GCSI_AI_PROVIDER: local` set |

**Cross-document agreement**: All documents agree on 3 canonical sources,
`/sources` + `/sources/select` as canonical API, and `/scenarios` +
`/scenarios/switch` as internal compatibility API.

---

## 15. Demo-Flow Assessment

**Recommended judge/demo flow using CURRENT product:**

1. **Start backend** (`cd backend && uvicorn app.main:app --reload --port 8000`) —
   startup banner confirms 1,284 products, thermal anomaly ACTIVE, geometry loaded
2. **Start frontend** (`cd frontend && npm run dev`) — open `http://localhost:5173`
3. **Inspect 3D visualization** — Earth + spacecraft at 182M km, signal propagation 10m08s
4. **Open Data Products panel** — browse 1,284 products; note 2.74 GB queued, 85.7 MB capacity
5. **Observe mission constraint** — only 3.1% of queue fits; thermal anomaly active
6. **Choose AI workflow** — click **Analyze**; watch STANDBY → ANALYZING → READY
7. **Review AI Prioritization** — 50 screened candidates shown; anomaly-aware ranking
8. **Check provider badge** — shows "Local" when no credentials set; shows "Granite" when configured
9. **Accept or modify recommendation** — operator retains full authority
10. **Approve and simulate** — observe delivered/deferred/failed products
11. **View Mission Report** — full outcome with timing, risk, product-level detail
12. **Switch to Juno PJ62** — use Scenario dropdown; select V1 or V2
13. **Observe HistoricalReplay banner** — "HISTORICAL REPLAY / not live telemetry" clearly shown
14. **Run analysis on Juno data** — verifies pipeline works with historical replay mode

**Flow blockers**: None identified. All steps are functional. ✅

---

## 16. Submission Claims Assessment

| Claim | Classification | Evidence |
|---|---|---|
| "NASA data" / "verified NASA PDS archive evidence" | ✅ SUPPORTED | Verified snapshots committed with SHA-256 locks; sourced from JPL Horizons and NASA PDS |
| "Historical replay" (not live telemetry) | ✅ SUPPORTED | `is_historical_replay: true` in API; UI banner; README disclaimer |
| "Granite-powered" / IBM Granite primary provider | ✅ SUPPORTED with caveat | Code integrates Granite; live benchmark pending credentials |
| "IBM Bob used" | ✅ SUPPORTED | `.bob/` directory, README section |
| "AI semantic prioritization" | ✅ SUPPORTED | 5-plan architecture, candidate screening, stage-1/stage-2 pipeline |
| "Benchmark methodology frozen" | ✅ SUPPORTED | `benchmarks/configs/gcsi_benchmark_v1.json`, `docs/benchmark_methodology.md` |
| "Granite efficacy superior" | ✅ NOT CLAIMED | README: "AI claims are architectural, not empirical" |
| "Production system" | ✅ NOT CLAIMED | Described as decision-support demo/tool |
| "Live telemetry" | ✅ NOT CLAIMED | Explicitly disclaimed in UI and README |
| "Juno PJ62 data is raw NASA telemetry" | ✅ NOT CLAIMED | Critical boundary clearly stated |
| "3,590 backend tests" | ⚠ STALE (now 5,056) | Fixed in Phase 8A |
| "218 frontend tests" | ⚠ STALE (now 363) | Fixed in Phase 8A |

---

## 17. Dependency Health

### Backend (`backend/pyproject.toml`)

| Package | Pinned? | Notes |
|---|---|---|
| `fastapi>=0.111` | Lower-bound | Stable; no known critical CVEs |
| `uvicorn[standard]>=0.29` | Lower-bound | Stable |
| `pydantic>=2.7` | Lower-bound | Stable v2 |
| `pydantic-settings>=2.3` | Lower-bound | Stable |
| `numpy>=1.26` | Lower-bound | Stable |
| `scipy>=1.13` | Lower-bound | Stable |
| `httpx>=0.27` | Lower-bound | Stable |
| `python-dotenv>=1.0` | Lower-bound | Stable |
| `pytest>=8.2` (dev) | Lower-bound | Stable |
| `pytest-asyncio>=0.23` (dev) | Lower-bound | Stable |

All dependencies have lower-bound pins with no lockfile for backend. This is
acceptable for a development/demo project.

### Frontend (`frontend/package.json` + `package-lock.json`)

Lockfile (`package-lock.json`) is present ✅. All frontend dependencies are pinned
via the lockfile. `npm ci` is used in CI. `vite:react-babel` deprecation warnings
(esbuild→oxc migration) are cosmetic and do not affect builds or tests.

---

## 18. Repository Hygiene

| Item | Status |
|---|---|
| `.env` tracked | ❌ Not tracked (`.gitignore` ✅) |
| `.env.local` tracked | ❌ Not tracked |
| `__pycache__` tracked | ❌ Not tracked (in `.gitignore`; directory exists locally but is untracked) |
| `.pytest_cache` tracked | ❌ Not tracked (in `.gitignore`) |
| `.venv` tracked | ❌ Not tracked (in `.gitignore`) |
| `node_modules` tracked | ❌ Not tracked (in `.gitignore`) |
| `dist/` tracked | ❌ Not tracked (in `.gitignore`) |
| Credentials/secrets in tracked files | ❌ None found |
| Editor artifacts (`.swp`, `.swo`) | ❌ Not tracked |
| OS metadata (`.DS_Store`, `Thumbs.db`) | ❌ Not tracked |
| `benchmarks/results/` | ✅ Tracked intentionally — labeled as infrastructure provenance |
| `data/verified_snapshots/` | ✅ Tracked intentionally — verified archival evidence |
| `.bob/` rules | ✅ Tracked intentionally — project-specific AI guidance |

Note: `.pytest_cache/` and `__pycache__/` directories are present locally
(listed by `list_files`) but are NOT git-tracked (confirmed by `git ls-files`
returning no matches). The `.gitignore` rules are effective. ✅

---

## 19. Findings Table

| ID | Severity | Finding | Evidence | Impact | Action | Fixed in 8A? |
|---|---|---|---|---|---|---|
| F-01 | MEDIUM | README test count stale (3,590 backend / 218 frontend) | README vs actual test run | Judge who counts tests may flag discrepancy | Update count in README | ✅ Yes |
| F-02 | LOW | Pydantic `field name "schema" shadows parent` warnings in 4 v2 models | 4 `UserWarning` at test time | Cosmetic; future Pydantic version may break | Track for future fix if Pydantic deprecates the pattern | No |
| F-03 | LOW | `PytestRemovedIn10Warning` for class-scoped fixtures in 7 test files | Test warnings | Non-breaking until pytest 10 releases | Update fixture decorators before pytest 10 | No |
| F-04 | LOW | Vite `esbuild` option deprecation warnings in frontend build | npm test stderr | Non-breaking; cosmetic | Update vite react plugin config in a future phase | No |
| F-05 | LOW | Frontend bundle size > 500 kB (Three.js + React Three Fiber) | `npm run build` warning | Expected for 3D mission visualization; not a bug | Consider dynamic import of 3D scene in future phase | No |
| F-06 | LOW | `httpx`→`httpx2` deprecation in `starlette.testclient` | Test stderr | Non-breaking; future compatibility | Upgrade when `httpx2` is stable | No |
| F-07 | INFO | Backend has no `package-lock.json`-equivalent (no pinned lockfile) | `pyproject.toml` lower bounds only | Acceptable for demo; could use `pip freeze` for reproducibility | Consider `requirements.txt` lockfile for production | No |
| F-08 | INFO | Python version at runtime (3.14.7) is newer than the documented minimum (3.11+) | `.python-version`, CI config | All tests pass; no version incompatibility | No action required | N/A |
| F-09 | INFO | `active_source_id` not exposed as top-level field in `/state` response | API smoke test | Source info available under `state.source` sub-object; no functional gap | No action required — by design | N/A |
| F-10 | INFO | Benchmark Granite efficacy results pending | benchmarks/README, main README | Pre-disclosed limitation; not a submission blocker | Execute when valid IBM credentials available | N/A |

---

## 20. Release Recommendation

### **READY WITH KNOWN LIMITATIONS**

### Exact Blockers

**None.**

### High-Severity Findings

**None.**

### Medium-Severity Findings

- **F-01**: README test count stale — **FIXED in Phase 8A** (updated from 3,590 → 5,056 backend; 218 → 363 frontend).

### Low-Severity Findings

F-02 through F-06: cosmetic warnings requiring no action before submission.

### Known Limitations (pre-disclosed, honest)

1. **Benchmark**: Granite efficacy experiment not yet run (requires valid IBM Cloud IAM credentials). Disclosed in README, benchmarks/README, and docs/benchmark_methodology.md.
2. **Telecom model**: Simplified BPSK/AWGN analytical model — not a flight-qualified RF tool. Disclosed in README Limitations and docs/telecom_model.md.
3. **AI confidence values**: Indicative, not calibrated probabilistic estimates. Disclosed in README Limitations.
4. **Mobile layout**: Not a primary target. Disclosed.
5. **Propagation in simulator**: `elapsed_time_s` does not include the ~608 s one-way delay. Disclosed.

---

## 21. Acceptance Criteria PASS/FAIL Summary

| Criterion | Status |
|---|---|
| Clean working tree at audit start | ✅ PASS |
| Expected HEAD `ace316b` | ✅ PASS |
| No secrets in tracked files | ✅ PASS |
| Backend tests: 0 failures | ✅ PASS (5056 passed) |
| Frontend tests: 0 failures | ✅ PASS (363 passed) |
| TypeScript typecheck: PASS | ✅ PASS |
| Production build: PASS | ✅ PASS |
| `/sources` returns exactly 3 canonical sources | ✅ PASS |
| All 3 sources selectable via `/sources/select` | ✅ PASS |
| ASTERIA-7 truthfulness (synthetic, not real spacecraft) | ✅ PASS |
| Juno historical replay truthfulness (not live telemetry) | ✅ PASS |
| Benchmark freeze hashes intact | ✅ PASS |
| No benchmark results regenerated in Phase 8A | ✅ PASS |
| Path traversal protections tested | ✅ PASS |
| Local provider not mislabeled as Granite | ✅ PASS |
| Granite failure UX: no silent substitution | ✅ PASS |
| AI overclaim: none | ✅ PASS |
| Compatibility API (`/scenarios`) operational | ✅ PASS |
| Demo flow coherent end-to-end | ✅ PASS |
| Documentation consistent across all docs | ✅ PASS |

---

## 22. Corrections Applied in Phase 8A

**One permitted correction was applied:**

The README static test count was stale:
- Backend: `3,590` → `5,056` (actual current passing count)
- Frontend: `218` → `363` (actual current passing count)

This is an objective factual correction that improves submission accuracy
with no semantic or product changes. It meets all Phase 8A modification criteria:
objective defect, small and localized, zero regression risk, does not change
product semantics.

---

## 23. Recommended Next Phase

**Phase 8B (optional)** — Only if valid IBM Cloud IAM credentials become available:
- Execute the pre-registered `gcsi_benchmark_v1` core suite (60 trials)
- Record and commit Granite efficacy results
- Update benchmark status in README

No other immediate engineering work is required for submission.

---

## 24. Exact Test Commands

```bash
# Backend full suite (from project root):
python -m pytest tests -q --tb=short

# Backend unit tests only:
python -m pytest tests/unit/ -q

# Backend integration tests only:
python -m pytest tests/integration/ -q

# Backend with Granite live tests (requires credentials):
python -m pytest tests -m "granite" -q

# Frontend tests (from frontend/):
npm test

# TypeScript typecheck (from frontend/):
npm run typecheck

# Production build (from frontend/):
npm run build
```

---

*Phase 8A audit completed. No modifications were needed beyond the README test-count correction.*
*Repository state: READY WITH KNOWN LIMITATIONS for hackathon submission.*
