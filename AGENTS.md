# AGENTS.md

This file provides guidance to agents working with code in this repository.

## Granite AI Agent — architecture (Phase 6)

### How the agent works (current implementation)

`GraniteAgent.recommend()` operates as a **context-injection** (prompt-based) agent:

1. The deterministic pipeline runs first:
   - `TelecomEngine` computes `LinkState` from raw scenario inputs.
   - `CandidateGenerator` produces four `CandidatePlan` variants.
   - `PlanEvaluator` evaluates all four plans analytically (no RNG).
2. All four objects — `LinkState`, `MissionState`, `list[CandidatePlan]`,
   `list[EvaluationResult]` — are serialized to JSON and passed to Granite
   as a structured user message.
3. Granite reasons over these pre-computed facts and returns a structured
   `AIRecommendation` JSON object.
4. The response is parsed and validated server-side before being returned.

### What Granite does and does not do

| Responsibility | Python (deterministic) | Granite (LLM) |
|----------------|----------------------|---------------|
| RF calculations (Eb/N0, BER, goodput) | ✓ | ✗ never |
| Packet scheduling / scoring | ✓ | ✗ never |
| Plan evaluation metrics | ✓ | ✗ never |
| Stochastic simulation | ✓ | ✗ never |
| Plan recommendation reasoning | — | ✓ |
| Evidence citation | — | ✓ (validated server-side) |
| Human-readable justification | — | ✓ |

### TOOL_SCHEMAS — deferred / future capability

`backend/app/agent/tools.py` defines `TOOL_SCHEMAS`: a list of
OpenAI-compatible function-calling schemas for six domain tools
(`get_link_state`, `get_mission_state`, `get_transmission_queue`,
`generate_candidate_plans`, `evaluate_plan`, `simulate_what_if`).

**These schemas are NOT currently passed to the Granite API.**
The current implementation does not use Granite's function-calling
interface. `TOOL_SCHEMAS` exists as a clearly-labelled draft for a
future iterative tool-calling loop.

Do not describe the current implementation as "tool-calling" or
"function-calling" — it is prompt-based context injection.

### Validation invariants (enforced in code)

- `recommended_plan_id` **must** be one of the provided `plan_id` values →
  `GraniteResponseError` if violated.
- `alternative_plan_id` **must** be `None` or one of the provided `plan_id`
  values → `GraniteResponseError` if violated.
- `EvidenceItem.field` **must** be a real field name in `LinkState`,
  `MissionState`, or `EvaluationResult` → `EvidenceHallucinationError`.
- `risk_level` **must** be a valid `RiskLevel` enum value →
  `GraniteResponseError`.
- `confidence` and `risk_score` **must** be in `[0.0, 1.0]`; Pydantic
  `ValidationError` is caught and re-raised as `GraniteResponseError` so
  the route returns HTTP 422, not an unhandled 500.

### Granite unavailability

When `GCSI_GRANITE_API_KEY` is empty or the API call fails, `GraniteAPIError`
is raised. `routes_agent.py` maps this to HTTP 502. No fabricated fallback
recommendation is ever returned. The frontend gracefully hides the
`RecommendationPanel` when the endpoint returns an error.

---

## Stack

- **Backend**: Python 3.11+, FastAPI, Pydantic v2, NumPy/SciPy — in `backend/`
- **Frontend**: React 18 + TypeScript + Vite — in `frontend/`
- **Tests**: pytest, run from repo root (`ground-control-signal-insight/`), **not** from `backend/`

## Commands

```bash
# Run all tests (from ground-control-signal-insight/)
<python> -m pytest

# Run a single test file
<python> -m pytest tests/unit/test_evaluator.py -v

# Run a single test class or function
<python> -m pytest tests/unit/test_evaluator.py::TestWindowPressureNonZero -v

# Skip tests requiring a live IBM Granite API key
<python> -m pytest -m "not granite"

# Frontend dev
cd frontend && npm run dev
```

> **Windows note**: Python is not on PATH. Use the full path, e.g.
> `& "C:\Program Files\Langflow\resources\python\python.exe"`

## Critical gotchas

### `RiskWeights` and `SchedulerWeights` — all weight fields use `gt=0.0` (strictly positive)
Pydantic will reject `0.0`. In tests that want to isolate a single weight, use `1e-9` for the others, **not** `0.0`.

```python
# WRONG — ValidationError at runtime
RiskWeights(w_deadline_miss=0.0, w_critical_deficit=0.0, w_window_pressure=1.0)

# CORRECT
RiskWeights(w_deadline_miss=1e-9, w_critical_deficit=1e-9, w_window_pressure=1.0)
```

### `pytest.ini_options` lives in `backend/pyproject.toml` but `testpaths = ["tests"]` resolves relative to the repo root
Always run pytest from `ground-control-signal-insight/`, not from `backend/`.

### `asyncio_mode = "auto"` is set in `pyproject.toml`
All async tests automatically get an event loop — no `@pytest.mark.asyncio` decorator needed.

### `packet_success_probability` uses log-space to avoid float underflow
Never implement as `(1 - BER) ** N`. Large packets at moderate BER will silently underflow to 0.0 with the naive form.

### `expected_transmission_cost` returns `math.inf` when `p_success <= 0`
Callers **must** handle `math.inf` explicitly. A probability floor must **not** be substituted.

### Two distinct risk layers — must not be conflated
- `PlanEvaluator.risk_score` — analytical/expected (no RNG, deterministic, pre-transmission)
- `TransmissionSimulator._derive_risk_level` — realized/stochastic (post-simulation); uses a different formula (`non_delivered_rate = (deferred + failed) / total`)
- These answer different questions and must never be compared as equivalent.

### Simulator outcome categories — three distinct lists
`delivered`, `deferred`, `failed` are all separate. `failed` means MAX_ATTEMPTS (100) exhausted with window still available — it is **not** the same as `deferred`. Downstream consumers must check all three.

### `window_pressure` formula
`window_pressure = min(cumulative_time_s / window_s, 1.0)` — fraction of window budget consumed by the plan. When `window_s == 0` it is forced to `1.0`.

### `Packet` has no priority field
Priority is expressed solely by ordering within `CandidatePlan.packets`. The scheduler produces the order; it is never stored on the packet itself.
