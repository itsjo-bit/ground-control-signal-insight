# GCSI Phase 5A — Hostile Judge Audit

> **INTERNAL ENGINEERING DOCUMENT — NOT MARKETING COPY**
>
> Adversarial pre-submission audit against five judge personas.
> Written as if the judge has just cloned the repository for the first time.

---

## Audit Summary Table

| ID | Sev | Category | Status |
|---|---|---|---|
| A01 | P1 | Product clarity — README says "150 products" but ASTERIA has 1,284 | FIXED |
| A02 | P2 | Product clarity — tagline is generic; doesn't invoke ASTERIA-7 urgency | FIXED |
| A03 | P2 | Product clarity — 3D spacecraft purpose not explained in 15-second scan | FIXED |
| B01 | P1 | AI claim — README says "≤50 products" but misses that ASTERIA screening is from 1,284 | FIXED |
| B02 | P2 | AI claim — Local provider not explained to judges as "deterministic rule-based AI" | FIXED |
| B03 | P2 | AI claim — confidence labelled "advisory" but frontend wording may imply calibration | NOTED |
| C01 | P1 | Telecom claim — README propagation delay says "~3 minutes" but ASTERIA is 608 s (~10m08s) | FIXED |
| C02 | P2 | Telecom — README states scenario distance as ~54M km (v3); ASTERIA is 182M km | FIXED |
| C03 | P3 | Telecom — elapsed_time_s vs propagation confusion in docs | DOCUMENTED |
| D01 | P1 | Test isolation — two tests fail when live Gemini key present in environment | FIXED |
| D02 | P2 | CI — no GitHub Actions workflow exists | FIXED (5B) |
| D03 | P2 | Repository — `ground-control-signal-insight/ground-control-signal-insight/` nested dir | FIXED |
| D04 | P2 | Repository — `gcsi-mvp-plan.md` / `AGENTS.md` internal planning files committed | FIXED |
| D05 | P2 | Repository — `backend/tests/` shadow tree contains only __pycache__ | FIXED |
| D06 | P3 | README — project structure table references mission_data_v3.json as primary demo, but default is ASTERIA | FIXED |
| E01 | P1 | Scientific integrity — benchmark run in results/ must be clearly labelled FAILED AUTH PILOT | FIXED |
| E02 | P2 | Scientific integrity — benchmarks/README.md references `docs/benchmark_results.md` which doesn't exist | FIXED |
| E03 | P3 | Scientific integrity — manifest.json `preregistered: false` without explanation | DOCUMENTED |

---

## 1. Judge A — Product Judge

### 1.1 What problem does GCSI solve?

**Finding**: Answering this in 15 seconds requires reading past the tagline and first table.  
The current tagline is acceptable but generic:  
> *"When bandwidth becomes a mission constraint, GCSI helps operators determine what matters most."*

The ASTERIA-7 demo makes this concrete — 1,284 products, 2.74 GB, 85.7 MB contact window, active thermal anomaly — but this concrete framing doesn't appear in the README until the demo section.  
**Score: 6/10**  
**Fix**: Move ASTERIA-7 concrete metrics to the top of README.

### 1.2 Why is AI needed?

**Finding**: Explained well in the architecture section ("Why AI is Needed"), but the text is ~500 words into the README.  
**Score: 7/10**  
**Fix**: Compress one-paragraph answer into opening.

### 1.3 Is the 3D spacecraft meaningful or decoration?

**Finding**: The 3D scene shows Earth, spacecraft at ~182M km, and a communication link. The distance is meaningful context (propagation delay visualization). However, nowhere in the quick README does it say "the 3D visualization shows the actual signal geometry — the spacecraft is real-distance from Earth."  
**Score: 6/10**  
**Fix**: Add one sentence in README under UI Workspace.

### 1.4 Does the product have a complete beginning-middle-end story?

**Finding**: YES. ASTERIA-7 provides: problem → triage → AI analysis → human approval → transmission → ground reception → evidence update.  
**Score: 9/10**

### 1.5 What changes after approval?

**Finding**: The transmission simulation, packet delivery, and ground evidence panels make this visible. Well implemented.  
**Score: 8/10**

**Overall Product Score: 7.2/10**

---

## 2. Judge B — AI Judge

### 2.1 Does the LLM actually see all 1,284 products?

**Finding (P1)**: The README's pipeline section says "150 Data Products" in two places (the ASCII art diagram and the `150 → Candidate Screening` section). The ASTERIA scenario has 1,284 products. A judge reading only the README pipeline section would think the demo scenario has 150 products.

**Specific evidence**: README lines 48, 278, 283, 305, 320, 337, 352, 390, 572, 613, 616, 742.

**Why a judge would care**: This is the headline scalability claim. If a judge sees "150" everywhere and then notices `asteria7_demo.md` says "1,284", they will suspect inconsistency or misleading documentation.

**Recommended fix**: Update all README references from "150" to "1,284" for the ASTERIA scenario context. Add explicit note that `mission_data_v3.json` has 150 products; ASTERIA-7 has 1,284.

**Architecture impact**: None — the pipeline structure is the same; only the numbers differ.

**Status**: FIXED — README updated to ASTERIA-7 canonical numbers with correct cross-references.

### 2.2 What exactly does AI control? What can AI NOT control?

**Finding**: README AI Trust Boundary section correctly states this. However it's ~650 lines in.  
**Score: 7/10** — good content, poor discoverability.

### 2.3 Does AI invent risk?

**Finding**: No. Risk is deterministically computed by PlanEvaluator. Explained in trust_boundary.md.  
**Score: 9/10**

### 2.4 Does AI invent packet facts?

**Finding**: No. Evidence binding from authoritative data is well-documented.  
**Score: 9/10**

### 2.5 Can Local fallback be mistaken for AI?

**Finding (P2)**: README describes Local as "rule-based provider" but doesn't immediately clarify for judges that "no API key needed" means "Local deterministic fallback is still a valid advisory layer, not a null/broken state."  
**Recommended fix**: Add explicit statement to README that Local = functional deterministic advisory.

### 2.6 Is confidence calibrated?

**Finding**: `docs/trust_boundary.md` section 9 correctly states `uncalibrated_llm` semantics. README architecture section mentions "advisory." This is accurate.  
**Score: 8/10**

**Overall AI Judge Score: 7.5/10**

---

## 3. Judge C — Telecommunications / Engineering Judge

### 3.1 Is 10m08s propagation calculated correctly?

**Finding**: ASTERIA-7 distance is 182,273,814.464 km.  
Propagation = 182,273,814,464 m / 299,792,458 m/s = 607.997 s ≈ 608 s = 10m 08s. ✓  
Formula: `distance_km × 1000 / c`. Correct.  
**Score: 10/10**

### 3.2 Is `latency_s` being confused with propagation?

**Finding**: `docs/telecom_model.md` clearly separates `latency_s` (link-layer protocol, ~1.4 s) from `propagation_delay_s` (~608 s). The ASTERIA demo doc also explicitly states this. Well-handled.  
**Score: 9/10**

### 3.3 Does distance determine SNR?

**Finding**: Correctly documented: "distance_km does NOT determine SNR in GCSI." SNR is a scenario input.  
`docs/telecom_model.md` section "Distance and SNR Independence" explains this explicitly.  
**Score: 9/10**

### 3.4 README propagation delay says "~3 minutes" — ASTERIA is ~10 minutes (P1)

**Finding**: README line 30 says `Propagation delay | ~3 minutes one-way`. This describes `mission_data_v3.json` (~54M km). ASTERIA-7 is 182M km = 10m 08s. If a judge runs the default scenario (ASTERIA-7) and sees "10m 08s" but README says "~3 minutes", they will be confused or suspicious.

**Why a judge would care**: Propagation delay is a headline physical parameter. Wrong number = credibility hit.

**Recommended fix**: Update README table to ASTERIA-7 values, add note about mission_data_v3.json's separate parameters.

**Status**: FIXED.

### 3.5 Does "bandwidth" mean nominal rate or goodput?

**Finding**: `docs/telecom_model.md` clearly distinguishes nominal rate (2,800,000 bps) from goodput (2,520,000 bps = 2.52 Mbps). The ASTERIA demo doc table shows both. Well handled.  
**Score: 9/10**

### 3.6 Does `elapsed_time_s` include propagation?

**Finding**: Explicitly documented in both telecom_model.md and asteria7_demo.md: elapsed_time_s does NOT include propagation. This is clear.  
**Score: 9/10**

**Overall Telecom Score: 8.5/10**

---

## 4. Judge D — Software Engineering Judge

### 4.1 Clone → install → run

**Finding**: Instructions are correct and complete. Python 3.11+ required, documented. Frontend npm install + dev. `cd backend && pip install -e ".[dev]"` is standard.  
**Score: 8/10**

### 4.2 Test isolation failure when live API key present (P1)

**Finding**: Two tests fail when a live Gemini key is present in the environment:
- `test_phase4_1a.py::TestStageProviderIdentityAfterFallback::test_no_fallback_when_valid`
- `test_phase4_1a.py::TestNoFallbackWhenValid::test_valid_result_retained`

Both make real HTTP calls to `/agent/recommend` without mocking the AI provider. When Gemini is the configured provider and returns HTTP 429, a fallback is triggered — but the tests assert `recommendation_fallback_reason is None`.

**Why a judge would care**: Tests failing on a clean install with live keys = poor CI hygiene.

**Recommended fix**: Mock the provider to `LocalRuleBasedProvider` in these tests.

**Status**: FIXED.

### 4.3 Nested directory `ground-control-signal-insight/ground-control-signal-insight/` (P2)

**Finding**: The repository contains a nested `ground-control-signal-insight/` subdirectory inside the project root. This appears to be an accidental artifact from a previous git operation.

**Why a judge would care**: Confusing structure raises questions about repository hygiene.

**Status**: FIXED — removed.

### 4.4 Internal planning files committed (P2)

**Finding**: `gcsi-mvp-plan.md` and `AGENTS.md` are committed internal planning/agent artifacts. These do not belong in a submission repository.

**Status**: FIXED — removed.

### 4.5 Shadow `backend/tests/` tree (P2)

**Finding**: `backend/tests/` exists but contains only `__init__.py` and `__pycache__`. The actual tests live in `tests/`. This shadow tree creates confusion about where tests live.

**Status**: FIXED — shadow tree removed.

### 4.6 No CI (P2)

**Finding**: No `.github/workflows/` directory exists. A submitted repository without CI signals incomplete engineering discipline.

**Status**: FIXED (see Phase 5B).

### 4.7 Local mode works without credentials

**Finding**: Confirmed. Local provider works offline with no API keys. Default startup loads ASTERIA-7. `/health` endpoint available. This is a strength.  
**Score: 9/10**

### 4.8 README project structure references mission_data_v3.json as PRIMARY DEMO (P3)

**Finding**: The Project Structure table says `mission_data_v3.json  # PRIMARY DEMO — 150 products, 3 anomalies`, but the default scenario is now ASTERIA-7. This is stale.

**Status**: FIXED.

**Overall Engineering Score: 7/10**

---

## 5. Judge E — Scientific / Evaluation Judge

### 5.1 Failed authentication pilot must not be mistaken for evidence (P1)

**Finding**: `benchmarks/results/run-20260826-110706-530179c2/` exists with `report.md`, `summary.json`, `manifest.json`. The report clearly shows 0 successful trials, 2 GraniteAPIError failures. However, a judge scanning the directory may mistake the existence of a `results/` folder with files for real benchmark evidence.

The manifest correctly records `run_type: "pilot"` and `run_status: "completed"` (with 0 successes). The report title says "GCSI Phase 2B Benchmark Report" which sounds like a completed benchmark.

**Why a judge would care**: A sophisticated judge who checks "benchmark results" and sees files may not read carefully enough to notice all trials failed.

**Recommended fix**: Add a `README.md` inside the results directory clearly marking this as an authentication failure pilot.

**Status**: FIXED — added explicit warning README.

### 5.2 Benchmark README references non-existent file (P2)

**Finding**: `benchmarks/README.md` references `docs/benchmark_results.md` ("See Also" section) which does not exist.

**Why a judge would care**: Dead link in documentation suggests incomplete follow-through.

**Status**: FIXED — removed dead link, replaced with honest status.

### 5.3 Are AI claims empirical or merely architectural?

**Finding**: README and docs correctly state benchmark infrastructure exists but official Granite result is pending. No fabricated improvement percentages are present. Scientific status is honest.  
**Score: 9/10** — excellent restraint.

### 5.4 Does the LLM grade itself?

**Finding**: Correctly not. Stage-2 uses provenance-blind OPTION aliases. MissionOutcomeEvaluator is deterministic and AI-agnostic.  
**Score: 10/10**

### 5.5 Are denominators independent from selected plan?

**Finding**: Phase 2A.1 authoritative denominator policy: denominators are computed against the complete authoritative DataProduct[] inventory, not the plan. Tested.  
**Score: 10/10**

**Overall Scientific Score: 8.5/10**

---

## Priority Issue Resolution Log

### P1 Issues Fixed in 5A

| ID | Issue | Fix |
|---|---|---|
| A01 | README says "150 products" everywhere; ASTERIA has 1,284 | Updated README to ASTERIA-7 numbers with correct references |
| B01 | Same as A01 — AI candidate context | Same fix |
| C01 | README propagation delay says ~3 min; ASTERIA is 10m08s | Updated README table |
| C02 | README scenario distance shows ~54M km; ASTERIA is 182M km | Updated README table |
| D01 | Two tests fail with live Gemini key in environment | Fixed test isolation via provider mock |
| E01 | Failed auth pilot could be mistaken for benchmark evidence | Added explicit warning README in results dir |

### P2 Issues Fixed in 5A

| ID | Issue | Fix |
|---|---|---|
| D03 | Nested `ground-control-signal-insight/ground-control-signal-insight/` dir | Removed |
| D04 | `gcsi-mvp-plan.md` and `AGENTS.md` planning artifacts committed | Removed |
| D05 | `backend/tests/` shadow tree with only __pycache__ | Removed |
| D06 | Project structure table stale default scenario reference | Updated |
| E02 | `benchmarks/README.md` references non-existent `docs/benchmark_results.md` | Fixed |

### Findings Not Fixed (Acceptable Limitations / Documented)

| ID | Issue | Disposition |
|---|---|---|
| B03 | Frontend confidence wording nuance | Reviewed — wording is "advisory" throughout; acceptable |
| C03 | elapsed_time_s vs propagation | Well-documented in telecom_model.md and asteria7_demo.md |
| E03 | manifest `preregistered: false` without explanation | Pilot run; methodology doc explains preregistration |

---

## Architecture Freeze Verification

No changes were made to:
- PlanEvaluator formulas
- MissionOutcomeEvaluator formulas
- CandidatePrioritizer algorithm
- SemanticRulePrioritizer algorithm
- Any deterministic scheduler
- AI prompt semantics (Stage 1 or Stage 2)
- Stage-2 provenance blinding
- Telecom formulas
- BER formulas
- Transmission simulator behavior
- `benchmarks/configs/gcsi_benchmark_v1.json`
- `data/scenarios/mission_data_v3.json`
- `data/scenarios/asteria7_thermal_priority_contact_v1.json`

*GCSI Phase 5A — Hostile Judge Audit*
*Document purpose: internal pre-submission engineering review only.*
