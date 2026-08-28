# Ground Control Signal Insight (GCSI)

**Version 1.0.0**

> When a spacecraft can't send everything, GCSI helps mission operators decide what Earth needs to hear first.

GCSI is an AI-assisted communication decision-support system for spacecraft ground operations.
A spacecraft generates far more mission data than a single contact window can carry.
GCSI combines deterministic telecom analysis with AI semantic reasoning to help operators prioritize
what gets transmitted first — while keeping the human in control of every final decision.

---

## AI Builders Challenge Submission

**Selected Challenge Theme:** Space Exploration
**Primary Development Tool:** IBM Bob
**Solution Area:** Space Operations & Decision-Support Systems
**AI Role:** Semantic mission-data prioritization and advisory plan recommendation

---

## How IBM Bob Was Used

IBM Bob served as GCSI's primary AI-assisted development tool across the
project lifecycle — from architecture planning and backend implementation
to AI-provider integration, the React Three Fiber mission interface,
testing, debugging, and submission hardening.

GCSI also includes project-specific Bob guidance under `.bob/`:

- **Agent rules** — coding and domain invariants, including telecom,
  simulation, and data-model constraints
- **Plan rules** — architectural boundaries between deterministic
  evaluation, stochastic simulation, and AI reasoning
- **Ask rules** — authoritative project context and documentation guidance

These rules helped Bob work within GCSI's engineering constraints rather
than treating each task as an isolated code-generation request.

Bob-generated changes were independently reviewed and repeatedly verified
against the repository itself. Submission-critical changes were validated
with clean-environment installation, automated tests, TypeScript type
checking, and production builds rather than relying only on Bob's summary
of its own work.

This verification discipline caught real integration and packaging issues
during development and led to a deliberately iterative workflow:

**small scoped task → Bob implementation → independent review → regression
testing → refinement**

At the **Phase 6E-C8 readiness checkpoint**, the canonical verification suite reports:

- **3,590 backend tests passing** (3 skipped, 1 deselected) — run with `python -m pytest tests -q`
- **218 frontend tests passing** (6 test files) — run with `npm test` from `frontend/`
- TypeScript typecheck: **pass** — run with `npm run typecheck` from `frontend/`
- Production build: **pass** — run with `npm run build` from `frontend/`

These numbers reflect the Phase 6E-C8 state.
Reproduce them with the canonical commands above rather than relying on this static count.

---

## The Problem — ASTERIA-7 Canonical Demo

The canonical demonstration mission, **ASTERIA-7**, illustrates the scale of the challenge:

| Constraint | Value |
|---|---|
| Data products queued | **1,284** |
| Total queued volume | **2.74 GB** |
| Contact window capacity | **~85.7 MB** (~3.1% of the queue) |
| Queue-to-contact ratio | **31.98×** |
| Active thermal anomaly | **ANOM-THERM-017** (severity 0.94) |
| Spacecraft distance | 182,273,814 km |
| One-way signal propagation | **10 min 08 s** |
| Communication window | **4 min 32 s** of high-rate contact |
| Link SNR | 2.8 dB (degraded) |

The spacecraft has an active unresolved thermal anomaly. The operator has less than five minutes
of contact time. Only 3.1% of the queued data can fit. The cause of the anomaly is unknown —
and the diagnostic data is somewhere in those 1,284 products.

Without decision support, triaging 1,284 products under anomaly conditions at mission pace is
not a tractable problem.

---

## Why AI is Needed — and Why Deterministic Logic Still Matters

GCSI is **not** an LLM directly controlling spacecraft transmission.

The architecture separates three distinct responsibilities:

```
1,284 queued data products
        ↓
Deterministic candidate screening
(anomaly-linked → critical → deadline → relevance)
        ↓
50 semantic candidates (bounded set)
        ↓
AI Stage 1: semantic prioritization
(only the 50 screened candidates are sent to the AI provider)
        ↓
AI-Prioritized Candidate Plan
        │
        ├─────────────────────────┐
        │                         │
4 Deterministic Baselines        │
(baseline / deadline-first /     │
 mission-critical-first /        │
 value-per-cost)                 │
        │                         │
        └──────────┬──────────────┘
                   ↓
          Common PlanEvaluator
          (identical metrics for all 5 plans)
                   ↓
       Objective 5-Plan Comparison
                   ↓
AI Stage 2 Recommendation        ← advisory
(may recommend AI plan OR any deterministic plan)
                   ↓
Human Review
                   ↓
Human Approval                   ← operator has final authority
                   ↓
Transmission Simulation
(stochastic, seed-controlled)
```

**Key principle**: The AI-prioritized plan is not trusted automatically.
It competes against four deterministic baseline strategies under identical
evaluation metrics. Stage 2 may recommend the AI plan or any deterministic
plan based on objective evidence. The human operator retains final authority.

AI operates in two advisory stages. Deterministic calculations remain
authoritative throughout. Neither AI stage controls or overrides physical
feasibility, risk scoring, or transmission outcomes.

### Deterministic system — authoritative for

- RF link calculations (Eb/N0, BER, goodput)
- Communication window capacity
- Plan feasibility evaluation
- Risk scoring and deadline tracking
- Transmission outcome

### AI Stage 1 — Semantic Prioritization

AI receives the bounded candidate set (≤50 products) and performs anomaly-aware,
mission-contextual ranking. The ranked products form the **AI-prioritized plan**:
AI-ranked products appear first (in priority order); unranked products are appended
in deterministic BaselineScheduler order. AI does not determine feasibility or compute
link metrics.

### Two Independent Evaluation Layers (Phase 2A)

Every candidate plan is evaluated by **two independent, AI-provenance-agnostic** layers:

```
CandidatePlan
├─ PlanEvaluator           ← physical / telecom feasibility
└─ MissionOutcomeEvaluator ← mission-semantic outcome
```

**`PlanEvaluator`** — determines **what can be delivered**:
- Expected transmission feasibility (window, BER, goodput)
- Deadline misses and deadline miss rate
- Critical packet delivery
- Bandwidth utilization and retransmission overhead
- Window pressure and risk score (LOW/MEDIUM/HIGH/CRITICAL)
- `deferred_packets` — the physical ground truth used by the next layer

**`MissionOutcomeEvaluator`** — determines **what that delivery means**:
- Scientific value capture rate (`Σ sci_value_delivered / Σ sci_value_total`)
- Required product delivery rate
- Active anomaly product delivery rate
- Per-anomaly coverage (product-level, with severity)
- High-severity anomaly coverage (default threshold: severity ≥ 0.75)
- Anomaly-weighted coverage: `Σ(severity_i × coverage_i) / Σ(severity_i)`
- Delivered data age metrics
- Subsystem delivery breakdown

Neither evaluator knows whether a plan came from AI, a deterministic baseline,
or any other source. Both produce identical metrics for identical inputs.

**Zero-denominator rule**: When a metric denominator is zero (e.g., no required products
in the scenario), the rate field returns `null` — not a false `1.0`.

**Authoritative denominator policy** (Phase 2A.1):
All `MissionOutcomeEvaluator` rates are computed against the **complete authoritative
`DataProduct[]` scenario inventory**, not merely the products present in the candidate plan.

> "A plan cannot improve its score simply by omitting products."

Concretely:
- `total_products` = `len(authoritative DataProduct[])`
- `total_scientific_value` = `Σ scientific_value` across ALL authoritative products
- `required_products_total` = all authoritative products where `delivery_requirement == "required"`
- `active_anomaly_products_total` = all authoritative products linked to an applicable anomaly
- Per-anomaly `total_linked_products` = all authoritative products linked to that anomaly

An omitted authoritative product therefore counts as **not delivered** rather than
disappearing from the denominator.

**Applicable anomaly semantics** (Phase 2A.1):
An anomaly is "applicable" for coverage purposes when its `status` is `"active"` or
`"monitoring"`. Anomalies with `status == "resolved"` are excluded from coverage metrics.
The canonical filter is `is_applicable_anomaly(anomaly)` in `mission_outcome_evaluator.py`.

**Strict validation** (Phase 2A.1):
`MissionOutcomeEvaluationError` is raised for:
- `plan.plan_id != evaluation_result.plan_id` (mismatched pair)
- Duplicate `product_id` in authoritative `DataProduct[]`
- `CandidatePlan` references a `packet_id` not in the authoritative inventory
- `EvaluationResult.deferred_packets` contains IDs not present in the plan

### Five-Plan Architecture

The v2/v3 path generates **five** candidate plans:

| Plan | Origin | Independent of AI? |
|---|---|---|
| `baseline` | BaselineScheduler weighted scoring | ✓ Yes |
| `deadline-first` | Earliest deadline first | ✓ Yes |
| `mission-critical-first` | Highest criticality first | ✓ Yes |
| `value-per-cost` | Highest value/cost ratio first | ✓ Yes |
| `ai-prioritized` | Stage-1 AI semantic ranking | ✗ Causal |

The four deterministic baselines are generated from the **original** packet set,
completely independent of the AI ranking. They form a scientific control group:
changing the AI ranking changes the AI plan but must NOT change any baseline.

All five plans are evaluated by the **same** `PlanEvaluator` and `MissionOutcomeEvaluator`
instances. No bonus metrics are added for the AI plan. AI provenance cannot influence
evaluation results.

### Semantic Deterministic Comparator (Phase 2A.1 Benchmark Infrastructure)

A **`semantic-rule-based`** comparator plan exists for scientific benchmarking (not shown
in the normal UI). Both this plan and the AI-prioritized plan are built by the **same
shared `build_ranked_prefix_plan()` helper** in `ranked_prefix_builder.py`:

```
LLM semantic plan          deterministic semantic-rule plan
        ↓                              ↓
    same candidate set             same candidate set
    same structured metadata       same structured metadata
    ── SAME build_ranked_prefix_plan() ──
    same duplicate-ID checks       same duplicate-ID checks
    same completeness invariants   same completeness invariants
    same evaluators                same evaluators
    but: LLM ranking source        but: SemanticRulePrioritizer ranking
```

The `SemanticRulePrioritizer` applies the same structured metadata (anomaly severity,
criticality, scientific value, deadline urgency) via a documented composite heuristic
rather than generative inference. This enables fair ablation experiments.

**Shared builder guarantees:**
- Duplicate authoritative packet IDs are rejected in both builders
- Both use identical prefix + BaselineScheduler-tail construction
- No mechanical implementation advantage exists between the two plans
- Their only experimental difference is the ranking source

### AI Stage 2 — Plan Recommendation (Phase 2A.1: Compact Provenance-Blind Summaries)

For **external LLM providers** (Granite, Gemini, Ollama), Stage-2 recommendation uses
**compact, provenance-blind `Stage2PlanSummary` objects** via `recommend_from_summaries()`.

**Production Stage-2 flow:**

```
plans / evaluations / mission outcomes
        ↓
build_blind_mapping()          ← OPTION-A … OPTION-E
        ↓
build_stage2_summaries()       ← compact metrics, no plan identities
        ↓
build_stage2_user_message()    ← final JSON sent to external LLM
        ↓
provider.recommend_from_summaries()  ← actual LLM call
        ↓
parse_stage2_response()        ← validate OPTION alias
        ↓
map_alias_to_plan_id()         ← backend mapping to real plan
        ↓
rebind risk_score, risk_level, packet_actions from authoritative data
        ↓
EvidenceItem values bound from authoritative summary (not LLM-echoed)
```

**What the external LLM receives:**
- `candidate_options`: compact metrics per OPTION alias (no real plan IDs)
- `mission_context`: phase, event, risk level
- `link_context`: window, BER, goodput
- `active_anomalies`: severity, subsystem (applicable anomalies only)

**What the external LLM does NOT receive:**
- CandidatePlan objects or packet arrays
- `ai-prioritized`, `baseline`, `strategy`, `generated_by`, `plan_type`
- Any other provenance information

**Stage-2 response contract:**
The external LLM returns `recommended_option_id` (an OPTION alias), `reasoning`,
`confidence`, `evidence` (field interpretations, no values), and `alternative_option_id`.
The backend supplies `risk_score`, `risk_level`, `packet_actions`, and evidence values
from authoritative data. The LLM never invents these.

**Provenance blinding prevents:**
- **Self-preference bias** — the LLM cannot favour the plan it may have generated
- **Automation bias** — "AI-prioritized" branding cannot influence the selection
- **Provenance leakage** — strategy, generated_by, and plan_type are absent

The `LocalRuleBasedProvider` is deterministic and is not subject to these biases, so it
operates on real plan data directly via `recommend()`.

The alias→real-plan mapping is deterministic (SHA-256 based on scenario ID + plan ID),
reproducible across runs, and never exposed to the external provider.

### Human operator — maintains final authority

The operator may accept the AI recommendation, modify it, or reject it entirely.
No transmission occurs without explicit human approval.

---

## The Queue → Candidate Screening → AI Ranking → Five-Plan Pipeline

This is the core architectural innovation for high-volume scenarios.

GCSI **deterministically screens** the full queued product set into a bounded semantic
candidate set **before** any AI reasoning. This is a design strength: the AI sees only
the most operationally relevant candidates, reducing noise and prompt size.

```
All queued data products (e.g. 1,284 in ASTERIA-7)
        ↓
Deterministic candidate screening
  • anomaly-linked products (highest priority)
  • critical products (criticality ≥ 0.7)
  • near-deadline products
  • high mission-relevance products
  • high scientific-value products
  • freshest data products
  • related-product completion
  • fill-up by composite urgency
        ↓
Bounded candidate set (default max 50, configurable via GCSI_AI_MAX_CANDIDATES)
        ↓
AI Stage 1: semantic prioritization
(only the screened candidates are sent to the AI provider)
        ↓
Valid ranked products (typically 40–50)
        ↓
Build ai-prioritized plan:
  • AI-ranked products first (in priority order)
  • Unranked products appended in BaselineScheduler order
  • All products from the full queue present exactly once
        │
        ├──── ALSO generate 4 deterministic baselines from ORIGINAL packet set
        │     (independent of AI ranking — the scientific control group)
        ↓
All 5 plans → same PlanEvaluator → objective comparison
        ↓
AI Stage 2 recommendation (from evidence only, no invented metrics)
        ↓
Human operator review and approval
```

**AI plan ordering policy:**

The AI cannot legitimately determine the ordering of every unseen product (it only
ranked the ≤50 screened candidates). The hybrid policy gives AI authority over what
it ranked:

- **AI-ranked prefix**: products in `ranked_products` appear first, in priority order
- **Deterministic tail**: all other products follow BaselineScheduler order

This is a defensible, auditable policy. The provenance is recorded in the plan metadata.

**Why the control group matters:**

- The four deterministic baselines produce identical results regardless of AI ranking
- If `deadline-first` is objectively better, Stage 2 recommends it
- The comparison is scientifically fair: no AI bonus, same evaluator, same inputs

The UI reflects this pipeline accurately:

| Label | Meaning |
|---|---|
| **Total Products** | All queued data products (1,284 in ASTERIA-7; 150 in mission_data_v3) |
| **AI Candidates** | The screened subset sent to the AI provider (≤50) |
| **Ranked** | Products the AI successfully ranked |

This is intentional architecture, not a limitation.

---

## Two Decision Workflows

### Manual Decision

The operator does not need AI. They can:

```
Browse all queued mission products
→ Search / filter / sort by criticality, deadline, subsystem, etc.
→ Select individual products
→ Build a manual priority list
→ Review deterministic feasibility and risk
→ Approve
→ Simulate transmission
```

AI is never required in Manual mode.

### AI Assisted

```
AI starts in STANDBY (does NOT run automatically at application open)
→ Operator explicitly clicks "Analyze"
→ Mission context is prepared
→ Candidate screening (deterministic)
→ AI semantic prioritization (provider call)
→ Plan evaluation (deterministic — authoritative)
→ AI recommendation + explanation rendered
→ Operator: accept / modify / reject
→ Human approval
→ Transmission simulation
```

> **AI does not automatically run when the application opens.**
> The operator must explicitly invoke analysis. This is intentional:
> AI analysis costs resources and should reflect the operator's decision to engage it.

---

## Scenario System

### Default demo scenario: `asteria7_thermal_priority_contact_v1.json`

The canonical ASTERIA-7 mission — the primary hackathon demo. Capabilities:

- **1,284** data products across 10 product families
- Active thermal anomaly (ANOM-THERM-017, severity 0.94)
- 182M km spacecraft distance — 10 min 08 s one-way signal propagation
- Contact window: 4 min 32 s, ~85.7 MB capacity
- Full AI product prioritization pipeline (50-candidate screening from 1,284)
- Manual planning support
- Severely constrained: 31.98× queue-to-contact ratio
- Mission state: `GCSI-ASTERIA-7 / pre_contact_anomaly_triage`

See [`docs/asteria7_demo.md`](docs/asteria7_demo.md) for full mission parameters.

### Alternative scenario: `mission_data_v3.json`

A medium-scale scenario (150 products, 3 anomalies, ~54M km, ~3-min propagation).
Useful for testing the same pipeline with a smaller dataset.

- Mission state: `GCSI-MISSION-003 / high_volume_pass`
- 150 data products, 3 active anomalies

### Legacy scenarios

`nominal_pass.json` and `degraded_link.json` are legacy packet-mode scenarios retained for
compatibility and unit testing. They use a small packet set and do not include:

- Data product model
- Anomaly model
- Spacecraft geometry
- AI product prioritization

If you load a legacy scenario intentionally (e.g., for quick smoke tests), the application
displays a clear warning that high-volume AI prioritization is unavailable. This is expected.
Legacy scenarios are not the demo target.

`mission_data_v2.json` is an intermediate data-product scenario, also retained for compatibility.

---

## Historical Replay — Juno PJ62

GCSI's second demonstration mode anchors the mission context in verified real-world archival data
rather than a synthetic scenario.

### Purpose

| Demo | Purpose | Source |
|---|---|---|
| **ASTERIA-7** | Stress-test triage at large queue scale | Synthetic scenario (fictional mission) |
| **Juno PJ62** | Demonstrate trustworthy real-world archive integration | NASA/JPL/PDS archival facts + explicit modeled GCSI policy |

They demonstrate different capabilities. ASTERIA-7 is not replaced — it remains the primary
stress-test and high-volume pipeline demonstration.

### What the Juno PJ62 replay is

Juno is a real NASA spacecraft in orbit around Jupiter. Perijove 62 (PJ62) was a real
close flyby in June 2024.

GCSI reconstructs a **decision scenario** from verified archival facts:

| Source | What GCSI uses |
|---|---|
| **JPL Horizons** (external authoritative) | Exact-epoch Juno–Sun geometry at 2024-06-14T03:59:55.483Z |
| **NASA PDS archive** (external authoritative) | MWR PJ62 IRDR and GRDR product metadata (size, LIDVID, observation window) |
| **GCSI modeled policy** (modeled) | SNR, data rate, link stability, decision window, risk score, product priority attributes |

The archival facts are committed as verified snapshots with SHA-256 integrity locks.

> **Critical boundary**: This is not live telemetry and not a reconstruction of
> an actual NASA transmission decision. GCSI uses verified archival facts to anchor
> the mission context. Communication constraints and product priority attributes are
> explicitly modeled GCSI policy — not NASA data.

### The decision pressure

- **Available capacity**: 81,000,000 bits (modeled 90 s window × 90,000 bps goodput)
- **IRDR queued**: 53,557,312 bits (6,694,664 bytes — from PDS archive)
- **GRDR queued**: 40,751,976 bits (5,093,997 bytes — from PDS archive)
- **Total queued**: 94,309,288 bits — **does not fit in one window**

Both products cannot be transmitted sequentially within the modeled window.
This capacity shortfall is the core decision problem.

### Running the PJ62 historical replay

**Bash / macOS / Linux:**

```bash
export GCSI_SOURCE_MODE=historical_replay
export GCSI_REPLAY_DESCRIPTOR=data/replays/juno_pj62_mwr_v1.json
export GCSI_AI_PROVIDER=local
# from project root:
uvicorn backend.app.main:app --port 8000
```

**Windows PowerShell:**

```powershell
$env:GCSI_SOURCE_MODE = "historical_replay"
$env:GCSI_REPLAY_DESCRIPTOR = "data/replays/juno_pj62_mwr_v1.json"
$env:GCSI_AI_PROVIDER = "local"
# from project root:
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000
```

**Frontend (same as any other mode):**

```bash
cd frontend
npm run dev
```

The backend startup banner will confirm:
```
[GCSI] Source mode      : HISTORICAL REPLAY
[GCSI] Provider         : GCSI-HistoricalReplayProvider
[GCSI] Mission          : JUNO
[GCSI] Data products    : 2
[GCSI] Geometry         : available
[GCSI] Replay semantics : reconstructed historical scenario
[GCSI]                    NOT live spacecraft telemetry
```

**AI provider notes:**

| Provider | Use | Credentials |
|---|---|---|
| `local` | Reliable offline demo — **recommended** | None |
| `granite` | Optional external AI advisory | IBM watsonx.ai credentials required |

Setting `GCSI_AI_PROVIDER=local` does not affect the historical source data.
The replay loads from verified NASA/JPL/PDS snapshots regardless of AI provider.

See [`docs/juno_pj62_historical_replay_demo.md`](docs/juno_pj62_historical_replay_demo.md)
for the complete operator/judge demonstration guide.

---

## UI Workspace

```
┌────────────────────────────────────────────────────────────┐
│ Left: Navigation sidebar                                   │
│ Center: 3D Mission Visualization (Earth + spacecraft)      │
│ Right: Main Control (scrollable panels)                    │
└────────────────────────────────────────────────────────────┘
```

**Workspace modes:**

| Mode | Description |
|---|---|
| **Normal** | Default three-column layout |
| **Expanded** | Right panel wider (~58vw); 3D still visible |
| **Focus** | Right panel fills workspace; 3D hidden |

Keyboard shortcuts: `Ctrl+Shift+F` toggles Focus mode; `Esc` exits Focus.

---

## AI Provider Support

GCSI uses a provider-agnostic AI layer. No paid API key is required for the default
development/demo path — the Local provider works offline with no dependencies.

| Provider | Activated when | Requirements |
|---|---|---|
| **IBM Granite** (primary IBM) | `GCSI_GRANITE_API_KEY` is set | IBM watsonx.ai account |
| **Google Gemini** (optional) | `GCSI_GEMINI_API_KEY` is set, Granite absent | Google AI API key |
| **Ollama** (local LLM) | `GCSI_OLLAMA_ENABLED=true` and server reachable | [Ollama](https://ollama.com) running locally |
| **Local** (default) | No API key configured | None — works offline |

**Automatic provider selection order:** Granite → Gemini → Ollama → Local

You can force a specific provider:

```bash
GCSI_AI_PROVIDER=granite   # force IBM Granite
GCSI_AI_PROVIDER=gemini    # force Google Gemini
GCSI_AI_PROVIDER=ollama    # force Ollama
GCSI_AI_PROVIDER=local     # force local rule-based
```

The **Local provider** is a deterministic rule-based reasoner that produces a valid,
explainable `AIRecommendation` with no network calls and no fabrication.

### IBM Granite (primary IBM provider)

IBM Granite is the primary IBM AI integration and takes priority when both
`GCSI_GRANITE_API_KEY` and `GCSI_GEMINI_API_KEY` are present.

| Variable | Where to find it | Required? |
|---|---|---|
| `GCSI_GRANITE_API_KEY` | [IBM Cloud → IAM → API keys](https://cloud.ibm.com/iam/apikeys) | Yes |
| `GCSI_GRANITE_PROJECT_ID` | watsonx.ai → your project → Manage → General → Project ID | Yes |
| `GCSI_GRANITE_API_URL` | Change region prefix if not `us-south` | No (default: us-south) |
| `GCSI_GRANITE_MODEL_ID` | Override for a different Granite model | No (default: `ibm/granite-4-h-small`) |

> **Security note**: keep credentials secret. Never paste them into logs, chat, or source code.

### Google Gemini (optional alternative)

| Variable | Where to find it | Required? |
|---|---|---|
| `GCSI_GEMINI_API_KEY` | [Google AI Studio → API keys](https://aistudio.google.com/apikeys) | Yes |
| `GCSI_GEMINI_MODEL` | Override for a different model | No (default: `gemini-2.0-flash`) |

### Ollama

```bash
# Install Ollama, pull a model, then:
GCSI_OLLAMA_ENABLED=true GCSI_OLLAMA_MODEL=llama3.2 uvicorn app.main:app ...
```

---

## Quick Start (Fresh Clone)

**Supported versions**: Python 3.11+, Node.js 24 (Node.js 24 is the supported frontend development/CI version)

### 1. Clone and navigate

```bash
git clone <repo-url> ground-control-signal-insight
cd ground-control-signal-insight
```

### 2. Create and activate a Python environment

```bash
python -m venv .venv

# Linux / macOS:
source .venv/bin/activate

# Windows PowerShell:
.venv\Scripts\Activate.ps1
```

### 3. Install backend dependencies

```bash
cd backend
pip install -e ".[dev]"
cd ..
```

### 4. Configure AI credentials (optional — skip for offline Local mode)

**No API key is required.** The default **Local** provider works offline.

To use IBM Granite or Google Gemini, create a `.env` file in the project root:

```bash
cp .env.example .env
# Edit .env — set GCSI_GRANITE_API_KEY and GCSI_GRANITE_PROJECT_ID for IBM Granite,
# or GCSI_GEMINI_API_KEY for Google Gemini.
# Leave all keys blank (or set GCSI_AI_PROVIDER=local) to use the offline Local provider.
```

### 5. Start the backend

The backend can be started from either the project root or the `backend/` directory.
The canonical ASTERIA-7 scenario loads by default — no environment variable required.

**From the project root (recommended):**

```bash
# Linux / macOS:
cd backend && uvicorn app.main:app --reload --port 8000

# Windows PowerShell:
cd backend; uvicorn app.main:app --reload --port 8000
```

**From the project root without changing directory:**

```bash
uvicorn backend.app.main:app --reload --port 8000
```

To use an explicit scenario path (or a different scenario):

```bash
# Linux / macOS (from backend/):
GCSI_SCENARIO_PATH=../data/scenarios/mission_data_v3.json uvicorn app.main:app --reload --port 8000

# Windows PowerShell (from backend\):
$env:GCSI_SCENARIO_PATH = "..\data\scenarios\mission_data_v3.json"
uvicorn app.main:app --reload --port 8000
```

The startup banner confirms the active scenario:

```
  [GCSI] Active scenario : .../asteria7_thermal_priority_contact_v1.json
  [GCSI] Mission         : GCSI-ASTERIA-7
  [GCSI] ASTERIA-7       : THERMAL PRIORITY CONTACT
  [GCSI] 1,284 data products
  [GCSI] Thermal anomaly : ACTIVE
  [GCSI] Geometry        : 182273814 km
  [GCSI] One-way signal  : 608.000 s
  [GCSI] Canonical mission experience : READY
```

### 6. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

### 7. Run backend tests

```bash
# From ground-control-signal-insight/
python -m pytest                          # all tests (live API tests skipped by default)
python -m pytest -m "not granite"        # explicitly skip IBM Granite live tests
python -m pytest tests/unit/             # unit tests only
python -m pytest tests/integration/     # integration tests only
```

### 8. Frontend tests, typecheck, and build

```bash
cd frontend
npm test              # Vitest component/unit tests (non-watch)
npm run typecheck     # TypeScript type check (no emit)
npm run build         # production build
```

---

## Demo Walkthrough

This is the recommended flow for evaluating GCSI in a hackathon setting.

The default scenario is **ASTERIA-7**: 1,284 products, active thermal anomaly, 10m08s propagation.

1. **Start GCSI** — the default ASTERIA-7 scenario loads automatically.
2. **Observe the startup banner** confirming 1,284 products, thermal anomaly, geometry available.
3. **Open the browser** at `http://localhost:5173`.
4. **Inspect the 3D visualization** — Earth, spacecraft at 182M km, and the communication link.
   The displayed mission distance and propagation delay are authoritative scenario values.
   The 3D Earth-spacecraft spacing is a presentation visualization and is not spatially to scale.
   The one-way signal delay is 10 minutes 8 seconds (authoritative geometry-derived value).
5. **Open Data Products** — browse all 1,284 products. Filter by criticality or anomaly.
   Notice: 1,284 products, 2.74 GB total, only ~85.7 MB fits in the contact window.
6. **Choose a workflow:**
   - **Manual Decision**: Select products manually, build a priority list, review deterministic
     feasibility, approve, and simulate transmission. No AI required.
   - **AI Assisted**: Click **Analyze**. Watch the AI lifecycle: STANDBY → ANALYZING → READY.
     The backend screens 1,284 products to the 50 most relevant candidates before AI reasoning.
     Review the AI Prioritization panel — see which 50 were screened, how they were ranked,
     and the anomaly-aware reasoning.
7. **Accept or modify** the AI recommendation.
8. **Simulate transmission** — observe delivered, deferred, and failed products.
9. **Open Mission Report** — review the full outcome with timing, risk, and product-level detail.
10. **Switch scenarios** — try `mission_data_v3.json` for a lighter 150-product version.

---

## Architecture Reference

```
Raw scenario JSON (asteria7_thermal_priority_contact_v1.json)
        ↓
TelecomEngine  ←  formulas.py  (single source of truth for RF math)
        ↓
LinkState  (Eb/N0, BER, goodput, remaining window)
        ↓
CandidatePrioritizer  (deterministic screening → ≤50 CandidateSummary objects)
        ↓
[AI Stage 1]  AI Provider  →  CandidatePrioritization
(semantic product ranking — advisory)
        ↓
CandidateGenerator  (4 deterministic CandidatePlans, AI-agnostic)
   +  build_ai_prioritized_plan  (1 AI-ordered plan)
        ↓
       All 5 plans  →  PlanEvaluator  (authoritative: telecom / feasibility)
                    →  MissionOutcomeEvaluator  (authoritative: semantic value)
        ↓
[AI Stage 2]  External provider receives provenance-blind OPTION-A…E aliases
(plan recommendation over compact summaries — advisory)
        ↓
Backend maps alias → real plan ID, binds authoritative metrics
        ↓
Human approval
        ↓
TransmissionSimulator  (stochastic simulation, seed-controlled)
        ↓
SimulationResult  (delivered / deferred / failed products)
```

| Layer | Responsibility |
|---|---|
| **Deterministic Python** | RF link calculations, candidate screening, plan evaluation, mission outcome, transmission simulation |
| **AI Stage 1** | Semantic product ranking over the bounded candidate set (advisory) |
| **AI Stage 2** | Plan selection from provenance-blind option summaries (advisory; cannot see plan origin) |
| **Human operator** | Final approval authority; can modify or reject AI recommendation |

### AI Boundary — What AI Does and Does Not Do

**AI DOES:**
- Semantic interpretation of the bounded 50-candidate set
- Anomaly-aware product prioritization and ranking
- Advisory plan recommendation (from provenance-blind evidence only)
- Explainable reasoning over mission context

**AI DOES NOT:**
- Alter packet facts (ID, size, subsystem, anomaly linkage)
- Compute BER, Eb/N0, goodput, or any RF metric
- Define or override risk score / risk level
- Decide physical transmission feasibility
- Automatically authorize or initiate transmission
- Replace the operator's approval authority
- Substitute a Local fallback and report it as Granite/Gemini

The LLM controls *why* to prioritize. The backend controls *what is true*.

For the complete trust architecture, see [`docs/trust_boundary.md`](docs/trust_boundary.md).

### Telecom Model

GCSI uses a simplified deterministic BPSK/AWGN analytical link model for
decision-support experimentation. Distance-derived signal propagation is
represented separately. It is not a full spacecraft RF link-budget or
CCSDS network simulator.

For full model documentation, see [`docs/telecom_model.md`](docs/telecom_model.md).

### Architecture Diagram

A detailed architecture diagram with Mermaid rendering is in [`docs/architecture.md`](docs/architecture.md).

### API Documentation

All API endpoints with mutation labels are documented in [`docs/api_overview.md`](docs/api_overview.md).

---

## Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `GCSI_SCENARIO_PATH` | `data/scenarios/asteria7_thermal_priority_contact_v1.json` | Override the startup scenario |
| `GCSI_SCENARIOS_DIR` | `data/scenarios` | Scenarios directory for runtime switching |
| `GCSI_AI_PROVIDER` | (auto) | Force a specific AI provider: `granite` / `gemini` / `ollama` / `local` |
| `GCSI_AI_MAX_CANDIDATES` | `50` | Maximum products sent to AI for prioritization |
| `GCSI_GRANITE_API_KEY` | — | IBM Cloud IAM API key (activates Granite provider) |
| `GCSI_GRANITE_PROJECT_ID` | — | watsonx.ai project UUID |
| `GCSI_GRANITE_API_URL` | `https://us-south.ml.cloud.ibm.com/ml/v1/text/generation` | Inference endpoint |
| `GCSI_GRANITE_MODEL_ID` | `ibm/granite-4-h-small` | Granite model identifier |
| `GCSI_GEMINI_API_KEY` | — | Google AI API key (activates Gemini provider) |
| `GCSI_GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model identifier |
| `GCSI_OLLAMA_ENABLED` | `false` | Enable Ollama provider |
| `GCSI_OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `GCSI_OLLAMA_MODEL` | `llama3.2` | Ollama model to use |

---

## Scientific Evaluation Status

The benchmark infrastructure is implemented and the methodology is frozen.
The final official Granite evaluation is pending valid provider access.

Current status:

| Item | Status |
|---|---|
| Benchmark methodology | Frozen and pre-registered (`docs/benchmark_methodology.md`) |
| Benchmark configuration | Frozen (`benchmarks/configs/gcsi_benchmark_v1.json`) |
| IAM authentication pilot | ⚠️ **Authentication failure — 0 inferences completed** |
| Official core benchmark | **Not yet executed** — requires valid IBM Cloud IAM + watsonx.ai credentials |
| Granite efficacy result | **Not available** |

**What this means**: AI claim comparisons are architectural, not empirical.
GCSI does not claim "AI beats baseline by X%" because that experiment has not been run.
When valid Granite access is available, the methodology is ready to execute.

See [`benchmarks/README.md`](benchmarks/README.md) and [`docs/benchmark_methodology.md`](docs/benchmark_methodology.md).

---

## Project Limitations

- **Telecom model**: simplified BPSK/AWGN analytical model; not a flight-qualified RF link-budget tool
- **Propagation in simulator**: `elapsed_time_s` does not include the ~608 s one-way propagation delay
- **AI confidence**: values are not calibrated; they are indicative, not probabilistic
- **Benchmark**: Granite efficacy results are pending; AI claims are architectural, not empirical
- **Scenario**: ASTERIA-7 is a synthetic fictional mission; not affiliated with any real space agency
- **ARQ model**: retransmissions are abstract Bernoulli retries; not real stop-and-wait ACK/NACK
- **Mobile**: UI is optimized for 1366×768+ desktop; mobile layout is not a primary target

---

## IBM Technology Attribution

- **IBM Granite** (`ibm/granite-4-h-small`) is the primary supported AI provider via IBM watsonx.ai
- IBM Granite is used for Stage-1 semantic prioritization and Stage-2 plan recommendation
- **IBM Bob** was the primary AI-assisted development tool for GCSI; see
  [How IBM Bob Was Used](#how-ibm-bob-was-used) for the development workflow
  and project-specific `.bob/` configuration.
- GCSI does not claim empirical Granite superiority — the benchmark experiment is pending

---

## Attribution

**Earth imagery**: NASA Blue Marble — *Next Generation* imagery.
Source: [NASA Visible Earth](https://visibleearth.nasa.gov/collection/1484/blue-marble).
Used for the 3D Earth visualization in the mission viewport.

---

## Project Structure

```
ground-control-signal-insight/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI application, startup, health endpoint
│   │   ├── state.py             # Global scenario/link state
│   │   ├── config.py            # Pydantic-settings configuration
│   │   ├── agent/               # AI providers (Granite, Gemini, Ollama, Local)
│   │   │   └── candidate_prioritizer.py  # Deterministic candidate screening
│   │   ├── api/                 # Route handlers
│   │   ├── candidate_generator/ # Four-plan candidate generation
│   │   ├── evaluator/           # Deterministic plan evaluation
│   │   ├── models/              # Pydantic domain models
│   │   ├── simulation/          # Scenario loader + transmission simulator
│   │   ├── scheduler/           # Baseline packet scheduler
│   │   └── telecom/             # RF engine + formulas
│   └── pyproject.toml
├── data/
│   └── scenarios/
│       ├── asteria7_thermal_priority_contact_v1.json  # DEFAULT — 1,284 products, thermal anomaly
│       ├── mission_data_v3.json  # Alternative — 150 products, 3 anomalies
│       ├── mission_data_v2.json  # Intermediate data-product scenario
│       ├── nominal_pass.json     # Legacy packet scenario
│       └── degraded_link.json    # Legacy packet scenario (degraded link)
├── docs/
│   └── telecom_model.md         # Telecom model reference
├── frontend/
│   └── src/
│       ├── MissionControl.tsx   # Primary layout: normal/expanded/focus workspace
│       ├── api/                 # Backend API client
│       ├── components/          # UI panels (AIDecisionPanel, DataProducts, etc.)
│       ├── hooks/               # React hooks
│       └── types/               # TypeScript domain types
├── tests/
│   ├── unit/                    # Unit tests for all domain modules
│   ├── integration/             # API integration tests
│   └── scenarios/               # End-to-end scenario tests
├── .env.example                 # Environment variable template
└── README.md
```
