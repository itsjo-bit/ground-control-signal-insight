# GCSI MVP Implementation Plan

## Top-Level Overview

Build the Ground Control Signal Insight (GCSI) MVP: a decision-support system for spacecraft
ground operators that recommends which packets to transmit first when bandwidth is constrained.

The pipeline is:

  Scenario/Telemetry → Telecom Engine → LinkState + MissionState
  → BaselineScheduler (baseline plan) + CandidateGenerator (variants)
  → PlanEvaluator → Granite Agent → AIRecommendation
  → Human Approval UI → TransmissionSimulator → Updated State

**Hard constraints (from design rules)**

- LLM never performs RF/telecom math.
- All metrics are produced by deterministic Python code.
- No database; state lives in-process or in JSON files. Single active scenario per server process.
- No orbital mechanics, no DTN, no real spacecraft control.
- No external API calls until local simulation works reliably.
- No custom deep-learning model. No 3D visualization.
- Scheduling priority is NOT an intrinsic packet field; it is computed by the scheduler.
- Telecom config includes only constants the current model actually consumes.
- `simulate_what_if` is a backend/agent capability even though the What-if UI screen is deferred.

**Scope of this plan**

Phases 1–5 cover the deterministic pipeline, API, and end-to-end scenario tests.
Phase 6 covers Granite AI agent integration.
Phase 7 covers the React frontend.
Phase 8 is documentation, AGENTS.md, and deferred-items record.

---

## Proposed Directory Tree

```
ground-control-signal-insight/
├── backend/
│   ├── app/
│   │   ├── main.py                     # FastAPI app factory + router registration
│   │   ├── config.py                   # Pydantic Settings (weights, modulation scheme)
│   │   ├── state.py                    # Module-level single active scenario store
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── link_state.py           # LinkState domain model
│   │   │   ├── mission_state.py        # MissionState domain model (with MissionRisk)
│   │   │   ├── packet.py               # Packet domain model (no priority field)
│   │   │   ├── scenario.py             # Scenario domain model
│   │   │   ├── candidate_plan.py       # CandidatePlan domain model
│   │   │   ├── evaluation_result.py    # EvaluationResult domain model
│   │   │   └── recommendation.py       # AIRecommendation + EvidenceItem models
│   │   ├── telecom/
│   │   │   ├── __init__.py
│   │   │   ├── formulas.py             # Pure scalar functions: SNR→Eb/N0→BER→Psucc, throughput
│   │   │   └── engine.py               # TelecomEngine: raw inputs → LinkState
│   │   ├── scheduler/
│   │   │   ├── __init__.py
│   │   │   └── baseline.py             # BaselineScheduler: weighted-priority → CandidatePlan
│   │   ├── candidate_generator/
│   │   │   ├── __init__.py
│   │   │   └── generator.py            # CandidateGenerator: named strategy variants
│   │   ├── evaluator/
│   │   │   ├── __init__.py
│   │   │   └── plan_evaluator.py       # PlanEvaluator: CandidatePlan → EvaluationResult
│   │   ├── simulation/
│   │   │   ├── __init__.py
│   │   │   ├── scenario_loader.py      # Loads + validates Scenario from JSON
│   │   │   └── transmission_sim.py     # TransmissionSimulator (seed-controllable)
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── tools.py                # Tool definitions callable by Granite
│   │   │   └── granite_agent.py        # GraniteAgent: produces AIRecommendation
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── routes_state.py         # GET /state
│   │       ├── routes_queue.py         # GET /queue
│   │       ├── routes_plans.py         # POST /plans/generate, POST /plans/evaluate
│   │       ├── routes_agent.py         # POST /agent/recommend
│   │       ├── routes_simulate.py      # POST /simulate, POST /simulate/what-if
│   │       └── routes_approve.py       # POST /approve
├── frontend/
│   ├── index.html
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/
│       │   └── client.ts               # Typed fetch wrappers (one per backend route)
│       ├── types/
│       │   └── domain.ts               # TypeScript mirrors of Pydantic models
│       ├── components/
│       │   ├── LinkHealthPanel.tsx
│       │   ├── MissionStatePanel.tsx
│       │   ├── TransmissionQueuePanel.tsx
│       │   ├── RecommendationPanel.tsx
│       │   └── ApprovalBar.tsx
│       └── pages/
│           └── MissionControl.tsx      # Primary MVP screen
├── tests/
│   ├── unit/
│   │   ├── test_telecom_formulas.py
│   │   ├── test_telecom_engine.py
│   │   ├── test_scheduler.py
│   │   ├── test_candidate_generator.py
│   │   ├── test_evaluator.py
│   │   └── test_models.py
│   ├── integration/
│   │   ├── test_api_state.py
│   │   ├── test_api_queue.py
│   │   ├── test_api_plans.py
│   │   └── test_api_simulate.py
│   └── scenarios/
│       └── test_scenario_e2e.py        # Full pipeline from scenario JSON → simulated state
├── data/
│   ├── scenarios/
│   │   ├── nominal_pass.json           # Reference scenario (generic mission, good link)
│   │   └── degraded_link.json          # Stress scenario (poor link conditions)
│   └── config/
│       └── scheduler_weights.json      # Default baseline scheduler weights
├── docs/
│   ├── architecture.md
│   ├── telecom_model.md                # BPSK/AWGN model, formulas, assumptions
│   └── api_reference.md
├── scripts/
│   ├── run_dev.sh                      # Starts backend + frontend in parallel (dev)
│   └── generate_scenario.py            # Helper to produce scenario JSON files
├── AGENTS.md
├── gcsi-mvp-plan.md                    # This file
└── README.md
```

---

## Module Responsibilities

### `backend/app/config.py`
Single source of truth for all configurable values that the current model actually uses.
- Scheduler weights (criticality, deadline urgency, mission relevance, efficiency, cost, risk).
- Risk score weights (deadline miss rate, critical packet delivery deficit, window pressure).
- Modulation scheme identifier (e.g., `"BPSK"`).
- Channel bandwidth `B` (Hz) and bit rate `Rb` (bps) used for the Eb/N0 formula.
- `protocol_efficiency: float` (0–1) — model assumption for link-layer overhead (e.g., 0.9).
- Uses Pydantic `BaseSettings` so values can be overridden via environment variables or a `.env`.

Do NOT include noise figure, frequency band, or other constants not consumed by the current model.

### `backend/app/state.py`
Module-level singleton holding the single active `Scenario` and derived state (current
`LinkState`, baseline `CandidatePlan`). No session management; one active scenario per process.
Updated atomically when a new scenario is loaded or a simulation completes.

### `backend/app/models/`
All domain models as Pydantic `BaseModel` subclasses. No logic; pure data contracts.

- **`LinkState`** — link snapshot at a point in time:
  `timestamp`, `snr_db`, `eb_n0_db`, `ber`, `rssi_dbm`, `nominal_data_rate_bps`,
  `link_goodput_bps` (link-level; derived from `nominal_data_rate_bps * protocol_efficiency`),
  `latency_s`, `link_stability`, `remaining_window_s`.
  Note: `packet_loss_rate` is no longer a raw input field; link goodput is derived from
  `protocol_efficiency` instead. BER-derived `packet_success_probability` is packet-level only.

- **`MissionState`** — mission context snapshot:
  `mission_id`, `mission_phase`, `current_event`, `event_time_remaining_s`,
  `comm_window_remaining_s`, `risk_score: float` (0–1), `risk_level: RiskLevel`.

- **`RiskLevel`** — enum: `LOW | MEDIUM | HIGH | CRITICAL`.

- **`Packet`** — data unit awaiting transmission (no priority field):
  `packet_id`, `packet_type`, `size_bits`, `criticality`, `mission_relevance`,
  `deadline_s`, `retry_cost`, `delivery_requirement`.

- **`Scenario`** — typed container for a loaded scenario:
  `scenario_id`, `simulated: bool`, `link_inputs: dict`, `mission_state: MissionState`,
  `packets: list[Packet]`.

- **`CandidatePlan`** — ordered transmission plan:
  `plan_id`, `strategy: str`, `packets: list[Packet]`, `generated_by: str`, `metadata: dict`.

- **`EvaluationResult`** — **expected/analytical** metrics for one plan (deterministic, no RNG):
  `plan_id`, `mission_value`, `critical_packets_delivered`, `total_critical_packets`,
  `deadline_misses`, `avg_packet_delay_s`, `bandwidth_utilization`, `retransmission_overhead`,
  `risk_score: float`, `risk_level: RiskLevel`, `deferred_packets: list[str]`.
  Packets that cannot fit in the window are listed in `deferred_packets`, never silently dropped.
  All fields are derived analytically from `link_goodput_bps`, `expected_transmission_cost`,
  and `packet_success_probability`. No stochastic draws.

- **`SimulationResult`** — **realized** outcomes from stochastic transmission simulation:
  `plan_id`, `delivered_packets: list[str]`, `deferred_packets: list[str]`,
  `failed_packets: list[str]`, `elapsed_time_s: float`, `retransmission_counts: dict[str, int]`,
  `link_state: LinkState` (updated after realization), `mission_state: MissionState` (updated).
  Produced only by `TransmissionSimulator`. Must NOT be used as input to `PlanEvaluator`.

- **`EvidenceItem`** — structured evidence unit cited by the AI:
  `source: str` (e.g., `"LinkState"`), `field: str`, `value: Any`, `interpretation: str`.
  Human-readable summaries are rendered from these fields in the UI.

- **`AIRecommendation`** — structured Granite output:
  `recommended_plan_id`, `packet_actions: list[dict]`, `risk_score: float`,
  `risk_level: RiskLevel`, `confidence: float`, `reasoning: str`,
  `evidence: list[EvidenceItem]`, `alternative_plan_id: str | None`.

### `backend/app/telecom/formulas.py`
Pure scalar functions only. No side effects. No Pydantic types at this layer.

The model pipeline:

```
SNR (dB) + B (Hz) + Rb (bps)
         ↓
  Eb/N0 = SNR_dB + 10·log10(B / Rb)               [snr_to_eb_n0]
         ↓
  BER = 0.5 · erfc(sqrt(Eb/N0_linear))             [bpsk_ber]      BPSK/AWGN only
         ↓
  p_success(packet) = exp(size_bits · log1p(-BER)) [packet_success_probability]
         ↓
  link_goodput = nominal_rate · protocol_efficiency [link_goodput]
         ↓
  tx_time(packet) = size_bits / link_goodput_bps    [transmission_time]
         ↓
  expected_tx_cost(packet) = tx_time / p_success    [expected_transmission_cost]
                             returns math.inf when p_success <= 0
```

**Key separation:**
- `link_goodput_bps` is a **link-level** quantity representing achievable throughput after
  protocol overhead. It is derived from `nominal_data_rate_bps * protocol_efficiency`.
  `protocol_efficiency` is a configurable model assumption (not derived from BER or packet
  loss rate). `link_goodput_bps` does NOT depend on individual packet size.
- `packet_success_probability` is a **packet-level** reliability metric derived from the
  BER and `size_bits`. It is orthogonal to `link_goodput_bps`.
- `expected_transmission_cost` combines both: time cost of transmitting a packet accounting
  for the probability of retransmission. Returns `math.inf` when `p_success <= 0`; callers
  (scheduler, candidate generator) must handle infinity explicitly.
- `transmission_time` uses `link_goodput_bps`, not `nominal_rate * p_success`.

Required inputs to `snr_to_eb_n0`: `snr_db: float`, `bandwidth_hz: float`, `bit_rate_bps: float`.

### `backend/app/telecom/engine.py`
`TelecomEngine` class. Accepts raw inputs and emits a complete `LinkState`.
Internally calls `formulas.py`. All assumptions are documented and sourced from config.
Raw inputs, derived metrics, and config/assumptions are clearly separated in the implementation.

### `backend/app/scheduler/baseline.py`
`BaselineScheduler` class.
Accepts `list[Packet]`, `LinkState`, `MissionState`, and a weight config.
Computes a deterministic weighted score per packet across: criticality, deadline urgency,
mission relevance, transmission efficiency, expected transmission cost, delivery risk.
Returns one `CandidatePlan` with `strategy="baseline"`.
Does NOT generate variants. Does NOT set a priority field on Packet.

### `backend/app/candidate_generator/generator.py`
`CandidateGenerator` class.
Accepts the same inputs as `BaselineScheduler`.
Produces a named list of `CandidatePlan` variants via distinct, semantically meaningful
ordering strategies — not arbitrary weight perturbation:

| Strategy name | Ordering principle |
|---|---|
| `"baseline"` | Output of `BaselineScheduler` (included for comparison) |
| `"deadline_first"` | Earliest deadline ascending; ties broken by criticality |
| `"mission_critical_first"` | Highest criticality descending; ties broken by mission relevance |
| `"value_per_cost"` | `(criticality * mission_relevance) / expected_transmission_cost` descending |

Each plan's `strategy` field is set to its strategy name. All strategies are deterministic.
New strategies may be added without modifying `BaselineScheduler`.

### `backend/app/evaluator/plan_evaluator.py`
`PlanEvaluator` class.
Accepts `CandidatePlan`, `LinkState`, `MissionState`.
Returns `EvaluationResult`.

**Fully deterministic and non-stochastic.** Computes the *expected/analytical* performance
of a plan using telecom-derived quantities (`link_goodput_bps`, `expected_transmission_cost`,
`packet_success_probability`) without drawing random outcomes. All metrics are reproducible
given the same inputs. This is the planning layer — it answers "what do we expect to happen?"

Exposes a stable interface usable for benchmarking: the same evaluator measures the baseline
and any AI-recommended plan so results are directly comparable.

### `backend/app/simulation/scenario_loader.py`
Loads a JSON file from `data/scenarios/`, validates it into a `Scenario` model.
Rejects files where `simulated != true` with an explicit `ValueError` (never silently proceeds).
Returns `Scenario`; does not write to global state (that is `state.py`'s job).

### `backend/app/simulation/transmission_sim.py`
`TransmissionSimulator` class.
`simulate(plan, link_state, mission_state, seed: int | None = None) -> SimulationResult`

**Stochastic and seed-controllable.** Realizes *actual* packet delivery outcomes by drawing
random Bernoulli trials against each packet's `packet_success_probability`. Tracks:
- Realized delivered / deferred / failed packets per transmission attempt.
- Elapsed transmission time (actual, including retransmission attempts drawn stochastically).
- Retransmission count per packet (realized, not expected).
- Updated `LinkState` (remaining window after realized elapsed time).
- Updated `MissionState` (risk derived from realized outcomes).

Returns a `SimulationResult` containing the above realized fields **plus** the updated
`LinkState` and `MissionState`. This is the execution layer — it answers "what actually happened?"

`seed=None` (default) → non-deterministic (live/demo use).
`seed=<int>` → fully reproducible (testing use).
Never mutates inputs. Never calls the AI agent.

**Do not use `SimulationResult` fields as inputs to `PlanEvaluator`.** The evaluator works
from expected metrics; the simulator tracks realized outcomes. They are separate layers.

### `backend/app/agent/tools.py`
Tool definitions callable by Granite during its reasoning turn.
Each tool is a thin wrapper delegating to the appropriate deterministic module.

| Tool | Delegates to |
|---|---|
| `get_link_state` | `state.py` |
| `get_mission_state` | `state.py` |
| `get_transmission_queue` | `BaselineScheduler` |
| `generate_candidate_plans` | `CandidateGenerator` |
| `evaluate_plan` | `PlanEvaluator` |
| `simulate_what_if` | `TransmissionSimulator` (no state mutation; explicit seed optional) |

Tools never compute RF metrics directly.

### `backend/app/agent/granite_agent.py`
`GraniteAgent` class.
`recommend(link_state, mission_state, plans, evaluations) -> AIRecommendation`

- Constructs a structured system prompt describing agent role and hard constraints.
- Passes pre-evaluated structured context as tool call results.
- Parses structured JSON response into `AIRecommendation`.
- Validates that each `EvidenceItem` in the response cites a field present in the provided state.
- Fails loudly with a typed exception if the response is malformed or the AI invents values.

### `backend/app/api/`
FastAPI routers. One file per concern. Route handlers contain no business logic.

### `frontend/src/`
React + TypeScript + Vite SPA.
`MissionControl.tsx` composes the five panel components.
`api/client.ts` provides one typed fetch function per backend endpoint.
`types/domain.ts` mirrors Pydantic models as TypeScript interfaces (maintained manually for MVP).

---

## Core Interfaces Between Modules

```
TelecomEngine.compute(raw_inputs: dict) -> LinkState

BaselineScheduler.rank(
    packets: list[Packet],
    link_state: LinkState,
    mission_state: MissionState,
    weights: SchedulerWeights
) -> CandidatePlan

CandidateGenerator.generate(
    packets: list[Packet],
    link_state: LinkState,
    mission_state: MissionState,
    weights: SchedulerWeights
) -> list[CandidatePlan]

PlanEvaluator.evaluate(
    plan: CandidatePlan,
    link_state: LinkState,
    mission_state: MissionState
) -> EvaluationResult          # deterministic expected/analytical metrics; no RNG

TransmissionSimulator.simulate(
    plan: CandidatePlan,
    link_state: LinkState,
    mission_state: MissionState,
    seed: int | None = None
) -> SimulationResult          # stochastic realized outcomes; separate from EvaluationResult

GraniteAgent.recommend(
    link_state: LinkState,
    mission_state: MissionState,
    plans: list[CandidatePlan],
    evaluations: list[EvaluationResult]
) -> AIRecommendation
```

API endpoints (all JSON in/out):

| Method | Path | In | Out |
|--------|------|----|-----|
| GET | `/state` | — | `{link_state, mission_state}` |
| GET | `/queue` | — | `CandidatePlan` (baseline) |
| POST | `/plans/generate` | `{}` (uses active scenario) | `list[CandidatePlan]` |
| POST | `/plans/evaluate` | `CandidatePlan` | `EvaluationResult` |
| POST | `/agent/recommend` | `{plans, evaluations}` | `AIRecommendation` |
| POST | `/simulate` | `{plan_id, seed?}` | `{link_state, mission_state}` |
| POST | `/simulate/what-if` | `{plan, seed?}` | `{link_state, mission_state}` |
| POST | `/approve` | `{plan_id, operator_notes}` | `{status, simulation_result}` |

**Removed from initial API**: `POST /queue/reorder`. Queue ordering is produced by the scheduler
and candidate generator; the frontend does not reorder arbitrarily.

---

## Recommended Dependency List

### Backend (`backend/pyproject.toml` or `backend/requirements.txt`)

| Package | Purpose |
|---------|---------|
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `pydantic>=2` | Domain models and validation |
| `pydantic-settings` | Config from env / `.env` |
| `numpy` | Telecom math (erfc, log1p) |
| `scipy` | `scipy.special.erfc` if preferred over `numpy` |
| `httpx` | Async HTTP for Granite API calls; also used as test client |
| `python-dotenv` | Local env file loading |
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support |

### Frontend (`frontend/package.json`)

| Package | Purpose |
|---------|---------|
| `react`, `react-dom` | UI library |
| `typescript` | Type safety |
| `vite` | Build tool and dev server |
| `@vitejs/plugin-react` | React HMR |

No UI component library for MVP. Avoid premature lock-in.

---

## Implementation Phases

Phases are ordered by dependency. Each phase is independently completable and testable before
the next begins.

---

### Phase 1 — Domain Models + Config

**Intent**: Establish the typed contracts that every other module depends on.

**Expected Outcomes**
- Nine Pydantic domain model classes (`LinkState`, `MissionState`, `Packet`, `Scenario`,
  `CandidatePlan`, `EvaluationResult`, `SimulationResult`, `EvidenceItem`, `AIRecommendation`)
  and one enum type (`RiskLevel`) exist and are importable.
- `MissionState.risk_score` (float 0–1) and `risk_level` (enum) are separate fields.
- `EvaluationResult.deferred_packets` exists; packets are never silently dropped.
- Config loads only constants the current model consumes, including `protocol_efficiency`.
- Model schema tests pass.

**Todo List**
1. Create `backend/app/config.py` with Pydantic Settings:
   - Scheduler weights (criticality, deadline urgency, mission relevance, efficiency, cost, risk).
   - Risk score weights: `w_deadline_miss`, `w_critical_deficit`, `w_window_pressure` (all float,
     default sum to 1.0; documented in `docs/telecom_model.md`).
   - `modulation: str = "BPSK"`.
   - `channel_bandwidth_hz: float` and `bit_rate_bps: float` (required by Eb/N0 formula).
   - `protocol_efficiency: float = 0.9` (link-layer overhead model assumption, range 0–1).
2. Create `data/config/scheduler_weights.json` with default float values for each weight.
3. Implement `RiskLevel` enum: `LOW | MEDIUM | HIGH | CRITICAL` (used in both `MissionState`
   and `EvaluationResult`).
4. Implement `LinkState` with fields: `timestamp`, `snr_db`, `eb_n0_db`, `ber`, `rssi_dbm`,
   `nominal_data_rate_bps`, `link_goodput_bps`, `latency_s`, `link_stability`,
   `remaining_window_s`. Remove `packet_loss_rate` — goodput is now derived from
   `protocol_efficiency`, not a loss rate input.
5. Implement `MissionState` with fields: `mission_id`, `mission_phase`, `current_event`,
   `event_time_remaining_s`, `comm_window_remaining_s`, `risk_score: float`, `risk_level: RiskLevel`.
6. Implement `Packet` with fields: `packet_id`, `packet_type`, `size_bits`, `criticality`,
   `mission_relevance`, `deadline_s`, `retry_cost`, `delivery_requirement`. No priority field.
7. Implement `Scenario` with fields: `scenario_id`, `simulated: bool`, `link_inputs: dict`,
   `mission_state: MissionState`, `packets: list[Packet]`.
8. Implement `CandidatePlan` with fields: `plan_id`, `strategy: str`, `packets: list[Packet]`,
   `generated_by: str`, `metadata: dict`.
9. Implement `EvaluationResult` with fields: `plan_id`, `mission_value`, `critical_packets_delivered`,
   `total_critical_packets`, `deadline_misses`, `avg_packet_delay_s`, `bandwidth_utilization`,
   `retransmission_overhead`, `risk_score: float`, `risk_level: RiskLevel`,
   `deferred_packets: list[str]`.
   All fields must be analytically derived — no stochastic draws allowed in this model.
10. Implement `SimulationResult` with fields: `plan_id`, `delivered_packets: list[str]`,
    `deferred_packets: list[str]`, `failed_packets: list[str]`, `elapsed_time_s: float`,
    `retransmission_counts: dict[str, int]`, `link_state: LinkState`, `mission_state: MissionState`.
    These are realized outcomes, not expected metrics.
11. Implement `EvidenceItem` with fields: `source: str`, `field: str`, `value: Any`,
    `interpretation: str`.
11. Implement `AIRecommendation` with fields: `recommended_plan_id`, `packet_actions: list[dict]`,
    `risk_score: float`, `risk_level: RiskLevel`, `confidence: float`, `reasoning: str`,
    `evidence: list[EvidenceItem]`, `alternative_plan_id: str | None`.
13. Write `tests/unit/test_models.py` — validate construction, required fields, rejection of
    invalid inputs, and that `risk_score` is in [0,1].

**Status**: [x] done

---

### Phase 2 — Telecom Engine

**Intent**: Implement the deterministic RF pipeline. All formulas are pure scalar functions
testable in isolation. The Eb/N0 model is unambiguous and its required inputs are explicit.

**Expected Outcomes**
- `formulas.py` contains pure functions covering the complete chain.
- `TelecomEngine` accepts raw inputs and emits a complete `LinkState`.
- `link_goodput_bps` is a link-level quantity independent of individual packet size.
- `packet_success_probability` is packet-level and uses log-space computation.
- `expected_transmission_cost` combines both into a per-packet cost estimate.
- BPSK/AWGN model is documented in `docs/telecom_model.md`.
- All formula tests pass against known reference values.

**Status**: [x] done — 60/60 tests pass

**Todo List**
1. Implement `formulas.py` with the following pure scalar functions (inputs/outputs are floats):
   - `snr_to_eb_n0(snr_db: float, bandwidth_hz: float, bit_rate_bps: float) -> float`
     Formula: `Eb/N0_dB = snr_db + 10 * log10(bandwidth_hz / bit_rate_bps)`
     All three inputs are required. Document that this assumes noise power is measured over
     the full bandwidth `B` and signal uses bit rate `Rb`.
   - `bpsk_ber(eb_n0_db: float) -> float`
     Formula: `0.5 * erfc(sqrt(10 ** (eb_n0_db / 10)))`
     Valid for AWGN channel only. Use `scipy.special.erfc` or `math.erfc`.
   - `packet_success_probability(ber: float, size_bits: int) -> float`
     Formula: `exp(size_bits * log1p(-ber))` to avoid float underflow.
     This is a packet-level metric; it depends on `size_bits`. BER is derived from the
     link Eb/N0; it is independent of `protocol_efficiency`.
   - `link_goodput(nominal_rate_bps: float, protocol_efficiency: float) -> float`
     Formula: `nominal_rate_bps * protocol_efficiency`
     `protocol_efficiency` is a configurable model assumption (range 0–1) sourced from config.
     This is a link-level quantity; it does NOT depend on individual packet size or BER.
   - `transmission_time(size_bits: int, goodput_bps: float) -> float`
     Formula: `size_bits / goodput_bps`
     Uses link-level goodput, not `nominal_rate * p_success`.
   - `expected_transmission_cost(tx_time: float, p_success: float) -> float`
     Returns `math.inf` when `p_success <= 0` (do not use an arbitrary floor).
     Otherwise returns `tx_time / p_success`.
     Callers (BaselineScheduler, CandidateGenerator) must handle `math.inf` explicitly —
     packets with zero delivery probability sort last in all strategies.
2. Implement `TelecomEngine.compute(raw_inputs: dict) -> LinkState`:
   - Section 1 — raw inputs: `snr_db`, `rssi_dbm`, `nominal_data_rate_bps`,
     `latency_s`, `link_stability`, `remaining_window_s`.
     Note: `packet_loss_rate` is NOT a raw input; remove it from this section.
   - Section 2 — derived metrics: call `snr_to_eb_n0`, `bpsk_ber`,
     `link_goodput(nominal_data_rate_bps, protocol_efficiency)`.
   - Section 3 — config/assumptions: `bandwidth_hz`, `bit_rate_bps`, and `protocol_efficiency`
     come from config; document that modulation is BPSK, channel model is AWGN, and
     `protocol_efficiency` represents link-layer overhead.
   - Returns a complete `LinkState`.
3. Create `docs/telecom_model.md` documenting:
   - AWGN, BPSK, no fading, no Doppler, no antenna gain.
   - Explicit Eb/N0 formula with required inputs.
   - `link_goodput = nominal_rate * protocol_efficiency` — motivation and default value.
   - `packet_success_probability` derivation via BER (separate from goodput model).
   - Risk score formula (see Phase 4).
4. Write `tests/unit/test_telecom_formulas.py` — one test per function, including:
   - BPSK BER at Eb/N0 = 10 dB → expected ≈ 3.87×10⁻⁶ (literature reference).
   - `snr_to_eb_n0` at SNR=10 dB, B=1 MHz, Rb=100 kbps → expected 20 dB.
   - `packet_success_probability` at high BER with large packet (confirms no underflow).
   - `link_goodput` takes `protocol_efficiency`, not `packet_loss_rate`.
   - `expected_transmission_cost` returns `math.inf` when `p_success=0`.
5. Write `tests/unit/test_telecom_engine.py` — fixture input → expected `LinkState` fields;
   confirm `link_goodput_bps = nominal_data_rate_bps * protocol_efficiency`.

**Status**: [ ] pending

---

### Phase 3 — Scenario Loader + Baseline Scheduler + Candidate Generator

**Intent**: Load a typed scenario, produce a deterministic baseline plan, and generate named
strategy variants for agent evaluation. Candidate generation and baseline scheduling are
separate concerns in separate modules.

**Expected Outcomes**
- Two reference scenario JSON files exist; both have `simulated: true`; neither uses orbit-specific
  names (`nominal_pass.json`, `degraded_link.json`).
- `ScenarioLoader` rejects scenarios missing `"simulated": true` with an explicit error.
- `BaselineScheduler` produces a reproducible `CandidatePlan` with `strategy="baseline"`.
- `CandidateGenerator` produces four named strategy plans using distinct ordering logic.
- No strategy uses arbitrary weight perturbation as its mechanism.
- Scheduler and generator tests cover edge cases.

**Todo List**
1. Define the scenario JSON schema (must match `Scenario` model): `scenario_id`, `simulated`,
   `link_inputs` (keys matching `TelecomEngine` raw input names), `mission_state`, `packets`.
2. Create `data/scenarios/nominal_pass.json` (good link, mixed criticality packets).
3. Create `data/scenarios/degraded_link.json` (poor SNR, tight window, high-criticality packets).
4. Implement `scenario_loader.py`:
   - `ScenarioLoader.load(path: str) -> Scenario`
   - Raises `ValueError` if `scenario.simulated is False`.
5. Implement `BaselineScheduler.rank()`:
   - Weighted scoring across: criticality, deadline urgency, mission relevance,
     transmission efficiency (inverse of `expected_transmission_cost`), delivery risk.
   - Weights sourced from config; no literals in scoring logic.
   - Ties broken deterministically (e.g., by `packet_id`).
   - Returns `CandidatePlan` with `strategy="baseline"`, `generated_by="BaselineScheduler"`.
6. Implement `CandidateGenerator.generate()` producing four `CandidatePlan` objects:
   - `"baseline"` — delegates to `BaselineScheduler.rank()`.
   - `"deadline_first"` — sort by `deadline_s` ascending; ties broken by `criticality` desc.
   - `"mission_critical_first"` — sort by `criticality` desc; ties broken by `mission_relevance` desc.
   - `"value_per_cost"` — sort by `(criticality * mission_relevance) / expected_transmission_cost`
     desc; requires per-packet cost from `formulas.expected_transmission_cost`.
7. Write `tests/unit/test_scheduler.py` — deterministic output, weight config sensitivity,
   empty queue, all-tied criticality, deadline-expired packets.
   Verify scheduler handles packets whose `expected_transmission_cost` is `math.inf`
   (zero p_success) by sorting them last.
8. Write `tests/unit/test_candidate_generator.py`:
   - Tests must NOT assert that all four strategies produce a different ordering on every
     scenario. Instead, design purpose-built scenario fixtures that are guaranteed to
     distinguish each strategy from the others:
     - `deadline_first` fixture: packets with identical criticality but different deadlines.
       Assert earliest-deadline packet is first.
     - `mission_critical_first` fixture: packets with identical deadlines but different
       criticality. Assert highest-criticality packet is first.
     - `value_per_cost` fixture: packets where value/cost ratio clearly separates them.
       Assert highest ratio is first.
   - Verify each plan's `strategy` field matches its strategy name.
   - Verify that a packet with `p_success=0` (infinite cost) sorts last in `value_per_cost`.

**Status**: [ ] pending

---

### Phase 4 — Plan Evaluator + Transmission Simulator + Benchmark Interface

**Intent**: Implement deterministic plan evaluation and seeded transmission simulation.
Clearly separate expected/analytical planning metrics from realized simulation outcomes.
Establish the evaluation interface that enables benchmarking baseline vs. AI-recommended plans.

**Expected Outcomes**
- `PlanEvaluator.evaluate()` is fully deterministic: no RNG, no stochastic draws.
  It produces `EvaluationResult` with expected/analytical metrics only.
- `TransmissionSimulator.simulate()` is stochastic: draws realized delivery outcomes per
  packet. It produces `SimulationResult` with realized fields only.
- `EvaluationResult` and `SimulationResult` are never mixed or used as inputs to each other.
- `EvaluationResult` contains separate `risk_score` and `risk_level` fields.
- `TransmissionSimulator.simulate()` is reproducible when called with a seed.
- The evaluator interface is stable enough to be used as a benchmark harness in later phases.
- All evaluator tests pass, including constraint-violation cases.

**Todo List**
1. Implement `PlanEvaluator.evaluate(plan, link_state, mission_state) -> EvaluationResult`:
   - **No RNG calls anywhere in this method or its callees.** All metrics are analytical.
   - Walk packets in plan order; use `transmission_time` and `link_goodput_bps` from `LinkState`.
   - Enforce `comm_window_remaining_s` analytically: once cumulative expected elapsed time
     exceeds window, mark remaining packets as deferred (add to `deferred_packets`).
   - `mission_value`: weighted sum of `criticality * mission_relevance` for non-deferred packets.
   - `critical_packets_delivered`: count of non-deferred packets where `criticality` ≥ threshold.
   - `deadline_misses`: count of packets where expected cumulative delivery time > `deadline_s`.
   - `avg_packet_delay_s`: mean of per-packet expected delivery timestamps.
   - `bandwidth_utilization`: total bits of non-deferred packets / (link_goodput_bps * window_s).
   - `retransmission_overhead`: analytically derived from `packet_success_probability` per
     packet and plan total size (expected retransmissions = `1/p_success - 1` per packet).
   - `risk_score`: deterministic weighted combination, clamped to [0,1]:
     ```
     deadline_miss_rate    = deadline_misses / max(total_packets, 1)
     critical_deficit      = 1 - (critical_packets_delivered / max(total_critical_packets, 1))
     window_pressure       = 1 - min(comm_window_remaining_s / initial_window_s, 1.0)

     risk_score = clamp(
         w_deadline_miss   * deadline_miss_rate
       + w_critical_deficit * critical_deficit
       + w_window_pressure  * window_pressure,
       0.0, 1.0
     )
     ```
     Weights `w_deadline_miss`, `w_critical_deficit`, `w_window_pressure` come from config.
     Document formula and default weights in `docs/telecom_model.md`.
   - `risk_level`: derived from `risk_score` thresholds: <0.25 → LOW, <0.5 → MEDIUM,
     <0.75 → HIGH, ≥0.75 → CRITICAL.
2. Implement `TransmissionSimulator.simulate(plan, link_state, mission_state, seed=None) -> SimulationResult`:
   - Accept optional `seed: int | None`; if provided, seed numpy RNG at method entry.
   - Walk packets in order; for each, draw a Bernoulli outcome against `packet_success_probability`.
   - Track per packet: delivered / deferred (window exceeded) / failed (draw failed after retries).
   - Track `elapsed_time_s`: actual elapsed time including realized retransmission delays.
   - Track `retransmission_counts`: number of realized retransmission attempts per packet_id.
   - Derive updated `LinkState` (remaining window = original - elapsed_time_s) and
     `MissionState` (risk derived from realized delivery outcomes, not expected ones).
   - Return a `SimulationResult`; never return an `EvaluationResult`.
   - Never mutate inputs. Never call `PlanEvaluator` or any evaluator method.
3. Implement `backend/app/state.py` module-level store:
   - `active_scenario: Scenario | None`
   - `active_link_state: LinkState | None`
   - `load_scenario(path: str) -> None` — calls `ScenarioLoader`, runs `TelecomEngine`,
     stores results.
4. Write `tests/unit/test_evaluator.py`:
   - Correct expected metrics for nominal scenario (all analytical, no RNG dependency).
   - Deferred packets named when expected elapsed time exceeds window.
   - Zero-packet plan returns zero metrics.
   - `risk_level` matches `risk_score` thresholds.
   - Confirm calling `evaluate()` twice with identical inputs returns identical results.
5. Write `tests/unit/test_simulator.py`:
   - Reproducibility: same seed → identical `SimulationResult`; different seed → may differ.
   - Confirm `SimulationResult` fields are realized values (not copies of `EvaluationResult`).
   - Confirm `elapsed_time_s` in `SimulationResult` differs from the expected delivery
     time computed by `PlanEvaluator` for the same plan (stochastic ≠ analytical).
   - Confirm no calls to `PlanEvaluator` occur inside the simulator.

**Status**: [ ] pending

---

### Phase 5 — FastAPI Layer + End-to-End Scenario Tests

**Intent**: Expose all deterministic pipeline stages via a REST API. Validate the complete
pipeline from scenario file to simulated state without the AI agent. Establish the benchmark
measurement point so baseline vs. AI-recommended plans can be compared.

**Expected Outcomes**
- All API routes exist and return correct Pydantic responses.
- `POST /plans/generate` replaces any would-be `GET /plans`; generation is an explicit action.
- No `POST /queue/reorder` endpoint exists.
- `POST /simulate/what-if` exists as a non-mutating simulation endpoint.
- End-to-end scenario test loads `nominal_pass.json`, generates candidates, evaluates all,
  simulates the baseline plan, and asserts final state.
- Evaluator is callable on both baseline and AI plans with identical interface (benchmark-ready).

**Todo List**
1. Implement `backend/app/main.py`: FastAPI app, CORS for `localhost:5173`, router registration,
   `/health` endpoint.
2. Implement `routes_state.py`:
   - `GET /state` → `{link_state, mission_state}` from `state.py`.
3. Implement `routes_queue.py`:
   - `GET /queue` → calls `BaselineScheduler.rank()` on active scenario; returns `CandidatePlan`.
4. Implement `routes_plans.py`:
   - `POST /plans/generate` → calls `CandidateGenerator.generate()`; returns `list[CandidatePlan]`.
   - `POST /plans/evaluate` → accepts `CandidatePlan`; returns `EvaluationResult`.
5. Implement `routes_simulate.py`:
   - `POST /simulate` → accepts `{plan_id, seed?}`; runs simulator; updates `state.py`; returns result.
   - `POST /simulate/what-if` → accepts `{plan, seed?}`; runs simulator without mutating state.
6. Implement `routes_approve.py`:
   - `POST /approve` → accepts `{plan_id, operator_notes}`; calls the same underlying
     simulation service function used by `routes_simulate.py` directly. Must NOT call the
     `POST /simulate` route handler internally.
7. Write `tests/integration/test_api_state.py`, `test_api_queue.py`, `test_api_plans.py`,
   `test_api_simulate.py`.
8. Write `tests/scenarios/test_scenario_e2e.py`:
   - Load `nominal_pass.json`.
   - `POST /plans/generate` → verify four strategies returned.
   - `POST /plans/evaluate` for each → verify `EvaluationResult` structure.
   - `POST /simulate` with seed=42 → verify final `LinkState` fields are deterministic.
   - Assert baseline `EvaluationResult` is comparable to alternative strategy results
     (benchmark measurement point confirmed working).

**Status**: [ ] pending

---

### Phase 6 — Granite AI Agent

**Intent**: Integrate IBM Granite as a structured reasoning agent. The agent reasons over
pre-evaluated deterministic facts. It never computes metrics and never invents values.

**Expected Outcomes**
- `GraniteAgent` produces a valid `AIRecommendation` with `evidence: list[EvidenceItem]`.
- Each `EvidenceItem` cites a field that exists in the provided `LinkState` or `MissionState`.
- Agent only invokes tools defined in `tools.py`.
- `simulate_what_if` tool is available to the agent (even though the UI screen is deferred).
- If Granite API is unavailable, agent raises a typed exception; it does not fabricate output.
- `POST /agent/recommend` endpoint works.

**Todo List**
1. Confirm IBM Granite API access method (REST + API key vs. client library); document in config.
2. Implement `agent/tools.py` with tool schemas for:
   `get_link_state`, `get_mission_state`, `get_transmission_queue`,
   `generate_candidate_plans`, `evaluate_plan`, `simulate_what_if`.
   `simulate_what_if` accepts an optional `seed` parameter.
3. Implement `GraniteAgent.recommend()`:
   - System prompt explicitly states: "You are a decision-support agent. You may only cite
     values from the structured data you have been given. You must not perform calculations."
   - Pass `LinkState`, `MissionState`, and all `EvaluationResult` objects as tool call results.
   - Parse JSON response; validate each `EvidenceItem.source` + `.field` against known model
     field names; raise `EvidenceHallucinationError` if a field is not recognized.
   - Parse `risk_score` and `risk_level` from AI response; reject if `risk_level` not in enum.
4. Implement `routes_agent.py`: `POST /agent/recommend`.
5. Mark agent tests with `pytest.mark.granite`; skip by default when key is absent.

**Status**: [ ] pending

---

### Phase 7 — React Frontend (Mission Control Screen)

**Intent**: Build the primary operator UI against the working API.

**Expected Outcomes**
- Mission Control screen renders all five panels from live API data.
- `RecommendationPanel` shows structured evidence items (source, field, value, interpretation).
- `ApprovalBar` Approve/Override posts to `/approve` and shows updated state.
- No mock data in production build; all data from API.

**Todo List**
1. Scaffold frontend: `npm create vite@latest frontend -- --template react-ts`.
2. Implement `types/domain.ts` as TypeScript interfaces mirroring Pydantic models, including
   `RiskLevel` enum, `EvidenceItem`, and `AIRecommendation`.
3. Implement `api/client.ts` with typed fetch functions for each route.
4. Implement `LinkHealthPanel` — SNR, BER, goodput, window remaining.
5. Implement `MissionStatePanel` — phase, event, `risk_score`, `risk_level` badge.
6. Implement `TransmissionQueuePanel` — ordered packet list with type, size, criticality, deadline.
7. Implement `RecommendationPanel` — recommended plan, reasoning, structured evidence table
   (source | field | value | interpretation), confidence, risk badge, alternative plan.
8. Implement `ApprovalBar` — Approve / Override buttons; posts to `/approve`; displays result.
9. Compose panels in `MissionControl.tsx`.
10. Configure Vite `server.proxy` to forward `/api` to `http://localhost:8000`.

**Status**: [ ] pending

---

### Phase 8 — Documentation, AGENTS.md Update, Cleanup

**Intent**: Leave the repository navigable by a new contributor or AI agent without prior knowledge.

**Expected Outcomes**
- `AGENTS.md` reflects actual commands, patterns, and gotchas.
- Mode-specific Bob rules files exist.
- `docs/architecture.md` and `docs/telecom_model.md` are complete.
- Deferred items are recorded.

**Todo List**
1. Update `AGENTS.md` (see recommendations section below).
2. Create `.bob/rules-agent/AGENTS.md`, `.bob/rules-ask/AGENTS.md`, `.bob/rules-plan/AGENTS.md`
   (see Bob mode-specific rules section below).
3. Write `docs/architecture.md` — pipeline diagram, module responsibilities, design rules.
4. Finalize `docs/telecom_model.md` — Eb/N0 formula with required inputs, BPSK BER, goodput
   vs. packet success probability separation, risk score thresholds; and an explicit section
   titled "Expected vs. Realized Metrics" documenting the `PlanEvaluator` / `TransmissionSimulator`
   distinction.
5. Write `scripts/run_dev.sh`.
6. Write `scripts/generate_scenario.py`.
7. Update `README.md` with setup instructions and a pointer to `docs/architecture.md`.

**Status**: [ ] pending

---

## Testing Strategy

| Test type | Location | What it covers |
|-----------|----------|----------------|
| Formula unit tests | `tests/unit/test_telecom_formulas.py` | Each formula in isolation; reference values from literature |
| Engine unit tests | `tests/unit/test_telecom_engine.py` | Full engine input → `LinkState` field correctness |
| Scheduler unit tests | `tests/unit/test_scheduler.py` | Deterministic ordering, weight sensitivity, edge cases |
| Generator unit tests | `tests/unit/test_candidate_generator.py` | Each strategy produces correct ordering; strategy names set |
| Evaluator unit tests | `tests/unit/test_evaluator.py` | Analytical metrics, deferred packets, risk thresholds; no RNG |
| Simulator unit tests | `tests/unit/test_simulator.py` | Realized outcomes, seed reproducibility, stochastic ≠ analytical |
| Schema validation | `tests/unit/test_models.py` | Pydantic construction, invalid inputs, risk_score bounds |
| API integration | `tests/integration/` | HTTP routes, request/response shapes |
| Scenario e2e | `tests/scenarios/test_scenario_e2e.py` | Full pipeline from JSON to simulated state; seed determinism |
| Benchmark test | `tests/scenarios/test_scenario_e2e.py` | Evaluator called on baseline + alternative plans; results comparable |
| AI agent tests | `tests/integration/test_agent.py` | Marked `granite`; skip when API key absent |

**Commands**
- All tests: `cd backend && pytest tests/`
- Single test: `pytest tests/unit/test_telecom_formulas.py::test_bpsk_ber_at_10db`
- Unit only: `pytest tests/unit/`
- Skip AI tests: `pytest tests/ -m "not granite"`
- Run with seed: seed is passed as a pytest fixture parameter in scenario tests

---

## Major Architectural Risks

1. **Granite tool-calling API shape**: IBM Granite's function-calling schema may differ from
   OpenAI conventions. All LLM interaction is isolated in `granite_agent.py`; changes are
   contained to one file.

2. **Evidence validation completeness**: Post-response field validation in `GraniteAgent`
   requires enumerating all valid `LinkState` and `MissionState` field names. Use Pydantic
   model introspection (`model_fields`) to build this set dynamically rather than hard-coding
   field names.

3. **Single-scenario state on concurrent requests**: If the FastAPI dev server receives
   concurrent requests while a simulation is writing to `state.py`, state may be transiently
   inconsistent. For MVP (single operator, dev server), this is acceptable. Document the
   constraint; do not add locking until it becomes a real problem.

4. **Packet success probability underflow**: `(1-BER)^N` underflows to 0.0 for large packets
   at low Eb/N0. The log-space formula `exp(N * log1p(-BER))` is required and must be tested
   explicitly with a large-packet, high-BER case.

5. **Frontend/backend type drift**: TypeScript interfaces in `types/domain.ts` must be kept
   in sync with Pydantic models manually. The risk is concrete for `EvidenceItem` and
   `RiskLevel`. Document this constraint in AGENTS.md.

6. **Telecom model communication**: The BPSK/AWGN model ignores fading, Doppler, and antenna
   gain. This must be visible in the UI (e.g., a "simulated data" badge) so operators cannot
   mistake it for real link analysis.

7. **`expected_transmission_cost` returning `math.inf`**: When `p_success <= 0`,
   `expected_transmission_cost` returns `math.inf` (not a floor value). Both `BaselineScheduler`
   and `CandidateGenerator` must treat `math.inf` cost as a valid sentinel meaning "sort last".
   Python's default float comparison handles `math.inf` correctly in sort keys, but this
   must be tested explicitly so the behaviour is confirmed, not assumed.

---

## Intentionally Deferred

The following are out of scope until Phase 8 baseline is stable and confirmed working:

- Orbital mechanics or ground station pass prediction.
- Orbit-type-specific modeling (LEO, GEO, HEO, deep space).
- DTN bundle protocol.
- Real spacecraft command uplink or telemetry downlink.
- External API calls (NASA HORIZONS, satellite TLE feeds, etc.).
- Persistent storage / database.
- User authentication or session management.
- Multi-mission / multi-spacecraft support.
- 3D or map visualization.
- What-if UI screen (backend `simulate_what_if` tool is implemented in Phase 4/6).
- Auto-generated TypeScript types from OpenAPI schema.
- Deployment / containerization.
- Custom deep-learning model training.
- RF fading, Doppler, antenna gain models.
- Streaming / real-time telemetry push (WebSocket).
- Noise figure and frequency band as first-class config (not consumed by current model).

---

## Recommendations for AGENTS.md

```markdown
## Commands
- Backend dev: `cd backend && uvicorn app.main:app --reload`
- Tests (all): `cd backend && pytest tests/`
- Tests (single): `pytest tests/unit/test_telecom_formulas.py::test_name`
- Tests (no AI): `pytest tests/ -m "not granite"`
- Frontend dev: `cd frontend && npm run dev`

## Critical Patterns
- LLM must NEVER call telecom formulas. All RF math is in `backend/app/telecom/`.
- `Packet` has no priority field. Priority is computed by `BaselineScheduler`, not stored.
- `link_goodput_bps = nominal_data_rate_bps * protocol_efficiency` (link-level; no packet size,
  no BER involvement). `packet_success_probability` is packet-level (BER + size_bits only).
  Do not conflate them. `transmission_time` uses `link_goodput_bps`.
- `expected_transmission_cost(tx_time, p_success)` returns `math.inf` when `p_success <= 0`.
  Do NOT use a `1e-9` floor. Scheduler and generator must handle `math.inf` explicitly (sort last).
- `snr_to_eb_n0` requires three explicit inputs: `snr_db`, `bandwidth_hz`, `bit_rate_bps`.
  Formula: `Eb/N0_dB = snr_db + 10*log10(bandwidth_hz / bit_rate_bps)`.
- Packet success probability MUST use log-space: `exp(size_bits * log1p(-ber))`.
  Never use `(1 - ber) ** size_bits` — it underflows for large packets at low Eb/N0.
- `risk_score` formula (in `PlanEvaluator`): weighted sum of `deadline_miss_rate`,
  `critical_deficit`, and `window_pressure`, clamped to [0,1]. Weights from config.
  Formula is documented in `docs/telecom_model.md`.
- All scenario JSON files must have `"simulated": true`. Loader rejects files without it.
- `EvaluationResult.deferred_packets` lists packet IDs that did not fit in the window.
  Packets are never silently dropped.
- `MissionState.risk_score` (float 0-1) and `.risk_level` (LOW/MEDIUM/HIGH/CRITICAL)
  are separate fields. Same separation applies to `EvaluationResult` and `AIRecommendation`.
- `AIRecommendation.evidence` is `list[EvidenceItem]` with `source`, `field`, `value`,
  `interpretation`. The agent must not invent field names; validated against model_fields.
- `BaselineScheduler` produces one plan (`strategy="baseline"`).
  `CandidateGenerator` produces four named strategies. They are separate modules.
- Scheduler and risk-score weights come from config. Never hard-code weight literals.
- `TransmissionSimulator.simulate()` accepts optional `seed: int | None`. Pass seed in tests.
- Config contains only: scheduler weights, risk weights, modulation scheme,
  `channel_bandwidth_hz`, `bit_rate_bps`, `protocol_efficiency`.
  Do not add noise figure or frequency band — not consumed by current model.
- No `POST /queue/reorder` endpoint. Queue ordering is produced by the scheduler.
- `POST /approve` calls the simulation service function directly, not `POST /simulate`
  internally. API route handlers must never call other route handlers.

## Architecture Rules
- No business logic in FastAPI route handlers.
- `GraniteAgent` is the only module that imports the LLM client.
- `TransmissionSimulator.simulate()` never mutates its input objects.
- `state.py` is the only module that writes to global in-process state.
- Single active scenario per server process; no session management.
- **`PlanEvaluator` is deterministic and non-stochastic.** It computes expected/analytical
  metrics. It must never call `numpy.random`, `random`, or any RNG.
- **`TransmissionSimulator` is stochastic.** It realizes outcomes via Bernoulli draws.
  It returns `SimulationResult`, not `EvaluationResult`.
- **`EvaluationResult` and `SimulationResult` must never be mixed.** Do not pass simulation
  results into the evaluator. Do not use evaluator outputs as simulation results.
  `PlanEvaluator` answers "what do we expect?" — `TransmissionSimulator` answers "what happened?"
```

---

## Bob Mode-Specific Rules

### `.bob/rules-plan/AGENTS.md`

```markdown
# GCSI Planning Rules

- The deterministic pipeline (Phases 1–5) must be complete and tested before any AI agent
  work begins. Do not plan Phase 6 work until Phase 5 e2e tests pass.
- `Packet` must never have a `priority` field. Priority is an output of the scheduler.
- Candidate generation and baseline scheduling are separate modules. `BaselineScheduler`
  produces one plan. `CandidateGenerator` produces named strategy variants. Do not merge them.
- Candidate strategies must have semantic names (baseline, deadline_first, mission_critical_first,
  value_per_cost). Do not use arbitrary weight perturbation as a strategy mechanism.
- `link_goodput_bps = nominal_rate * protocol_efficiency`. `packet_success_probability` is
  BER-derived and packet-level. They are independent. Any plan mixing them is wrong.
- `snr_to_eb_n0` requires `bandwidth_hz` and `bit_rate_bps` explicitly. Do not plan an
  ambiguous SNR→Eb/N0 shortcut.
- `MissionState.risk_score` and `.risk_level` are separate fields. Same for `EvaluationResult`.
- `AIRecommendation.evidence` is `list[EvidenceItem]` (structured), not `list[str]`.
- `risk_score` is a deterministic weighted formula (deadline miss rate + critical deficit +
  window pressure), clamped to [0,1], with configurable weights. Do not derive it from a single
  metric or invent ad hoc formulas.
- `expected_transmission_cost` returns `math.inf` for zero-probability packets. Do not plan
  a floor value — handle infinity explicitly in scheduling and generation logic.
- `POST /approve` must invoke the simulation service directly; it must not call `POST /simulate`
  internally. API routes must never call other API route handlers.
- Do not plan a database, session management, or multi-scenario support for the MVP.
- `simulate_what_if` is a backend capability available to the agent even though the
  dedicated What-if UI screen is deferred.
- Config includes only constants the current BPSK/AWGN model consumes: scheduler weights,
  risk weights, modulation, `channel_bandwidth_hz`, `bit_rate_bps`, `protocol_efficiency`.
  Do not plan noise figure or frequency band fields until a model that uses them is planned.
- No `POST /queue/reorder` API endpoint. Scheduling produces plans; the frontend does not
  reorder queues arbitrarily.
- The evaluator interface must be stable by Phase 4 so it can serve as a benchmark harness.
  Baseline and AI-recommended plans must be evaluated with the identical `PlanEvaluator`.
- `PlanEvaluator` is deterministic; `TransmissionSimulator` is stochastic. Plan them as
  separate modules with separate return types (`EvaluationResult` vs. `SimulationResult`).
  Do not plan any code path that feeds `SimulationResult` into `PlanEvaluator` or vice versa.
```

### `.bob/rules-agent/AGENTS.md`

```markdown
# GCSI Coding Rules

- `backend/app/telecom/formulas.py` is pure scalar functions only. No Pydantic models,
  no side effects, no logging. All inputs/outputs are Python floats or ints.
- `snr_to_eb_n0(snr_db, bandwidth_hz, bit_rate_bps)` — three required parameters.
  Formula: `snr_db + 10 * log10(bandwidth_hz / bit_rate_bps)`. No shortcuts.
- `link_goodput(nominal_rate_bps, protocol_efficiency)` — link-level, no `size_bits`, no BER.
  `protocol_efficiency` comes from config, not from packet loss rate or BER.
- `packet_success_probability(ber, size_bits)` — packet-level. MUST use log-space:
  `exp(size_bits * log1p(-ber))`. Never use `(1 - ber) ** size_bits`.
- `transmission_time(size_bits, goodput_bps)` — uses link goodput, not nominal_rate * p_success.
- `expected_transmission_cost(tx_time, p_success)` — returns `math.inf` when `p_success <= 0`.
  Do NOT use `max(p_success, 1e-9)` or any floor. Infinity is the correct semantic result.
  `BaselineScheduler` and `CandidateGenerator` must handle `math.inf` by sorting affected
  packets last (Python float sort handles inf correctly; test it explicitly).
- `BaselineScheduler.rank()` reads weights from the config object. No weight literals in
  scoring logic. Returns `CandidatePlan` with `strategy="baseline"`.
- `CandidateGenerator.generate()` produces four plans by named strategy. Each strategy has
  its own deterministic sort; no arbitrary weight perturbation.
- `CandidateGenerator` tests use purpose-built fixtures per strategy — NOT assertions that
  all strategies differ on any given scenario.
- `EvaluationResult.deferred_packets` must list the `packet_id` of every packet that did not
  fit in the window. Never omit or silently skip them.
- `risk_score` formula: `clamp(w_dm*deadline_miss_rate + w_cd*critical_deficit + w_wp*window_pressure, 0, 1)`.
  Weights from config. Implement in `PlanEvaluator`, document in `docs/telecom_model.md`.
- `risk_score` (float 0-1) and `risk_level` (RiskLevel enum) are separate fields on
  `MissionState`, `EvaluationResult`, and `AIRecommendation`. Do not store them as one field.
- `AIRecommendation.evidence` is `list[EvidenceItem]`. Each item has `source`, `field`,
  `value`, `interpretation`. Never use `list[str]` for evidence.
- `GraniteAgent` validates evidence items against `model_fields` of `LinkState` and
  `MissionState`. Use `Model.model_fields.keys()` to build the valid field set dynamically.
- `TransmissionSimulator.simulate()` signature: `(plan, link_state, mission_state, seed=None)`.
  Seed the numpy RNG at method entry if seed is not None. Return new objects; never mutate.
- `state.py` is the only place that writes to module-level state. All other modules are
  stateless and accept state as parameters.
- `GraniteAgent` is the only file that imports the LLM client.
- FastAPI route handlers contain no business logic; delegate to domain modules and return.
- `routes_approve.py` must call the shared simulation service function directly — never
  call `routes_simulate`'s handler internally.
- `PlanEvaluator.evaluate()` must contain zero RNG calls. If you see `numpy.random`,
  `random.random`, or similar inside `plan_evaluator.py` or its callees, that is a bug.
- `TransmissionSimulator.simulate()` returns `SimulationResult` — not a tuple, not an
  `EvaluationResult`. Do not copy `EvaluationResult` fields into `SimulationResult`.
- `retransmission_overhead` in `EvaluationResult` is analytical: `sum(1/p_success - 1)`.
  `retransmission_counts` in `SimulationResult` are realized integer counts from Bernoulli draws.
  They will differ numerically. This is correct and expected.
- Telecom formula tests must include BPSK BER at Eb/N0 = 10 dB ≈ 3.87×10⁻⁶.
- Config does not include noise figure or frequency band. Do not add them unless the model changes.
```

### `.bob/rules-ask/AGENTS.md`

```markdown
# GCSI Documentation Context

- `backend/app/telecom/formulas.py` is the authoritative source for all RF formulas.
  Docstrings there are the canonical reference. `docs/telecom_model.md` is the prose
  explanation of the same material.
- `link_goodput_bps = nominal_data_rate_bps * protocol_efficiency`. It is NOT derived from
  BER, packet loss rate, or packet size. `protocol_efficiency` is a configurable model
  assumption (default 0.9). This is intentionally separate from `packet_success_probability`.
- `packet_success_probability` is derived from BER and packet `size_bits` only. It is
  independent of `link_goodput_bps`. The scheduler uses `expected_transmission_cost` which
  combines transmission_time (from goodput) and p_success (from BER).
- `expected_transmission_cost` returns `math.inf` when `p_success <= 0`. This is intentional
  — infinity means "this packet cannot be delivered reliably". Do not treat it as an error.
- The `risk_score` formula in `PlanEvaluator` is:
  `clamp(w_dm*deadline_miss_rate + w_cd*critical_deficit + w_wp*window_pressure, 0, 1)`.
  The formula and default weights are in `docs/telecom_model.md`. It is deterministic and
  reproducible — not a heuristic or LLM output.
- The `Eb/N0` formula used here is `snr_db + 10*log10(B/Rb)`. Three inputs are required.
  It is not the same as `SNR - 10*log10(B/Rb)` — check the sign convention in the formula
  comment in `formulas.py` before citing it.
- `Packet` has no `priority` field by design. Priority is the output of `BaselineScheduler`,
  not an input attribute of the packet.
- `BaselineScheduler` produces the baseline plan. `CandidateGenerator` produces named
  strategy variants. They are in separate modules for a reason; do not conflate them.
- `CandidateGenerator` tests use purpose-built fixtures per strategy, not assertions that
  all strategies differ on all scenarios.
- `data/scenarios/` contains simulated data only. All files have `"simulated": true`.
  No real telemetry exists in this repository.
- The Granite agent reasons over pre-computed `EvaluationResult` objects. It does not
  perform calculations. Questions about "how does the AI compute X" are answered by
  the deterministic evaluator, not the agent.
- `AIRecommendation.evidence` is a list of `EvidenceItem` objects, not plain strings.
  Each item has `source`, `field`, `value`, and `interpretation`.
- `MissionState.risk_score` and `.risk_level` are separate fields. The same applies to
  `EvaluationResult` and `AIRecommendation`. The UI should display both.
- `PlanEvaluator` computes *expected* outcomes analytically — no randomness.
  `TransmissionSimulator` computes *realized* outcomes stochastically — via Bernoulli draws.
  `EvaluationResult` (planning layer) ≠ `SimulationResult` (execution layer). They answer
  different questions and must never be confused or used interchangeably.
- `docs/telecom_model.md` has a section "Expected vs. Realized Metrics" that explains
  this distinction. Read it before working on evaluator or simulator code.
- The `simulate_what_if` tool exists in `backend/app/agent/tools.py` and is usable by the
  agent. The What-if *UI screen* is deferred, but the backend capability is present.
- The system is a decision-support prototype. It does not control spacecraft and does not
  guarantee mission safety.
- Config contains: scheduler weights, risk weights, modulation, `channel_bandwidth_hz`,
  `bit_rate_bps`, `protocol_efficiency`. Noise figure and frequency band are intentionally
  absent — not modeled yet.
```
