# AGENTS.md

This file provides guidance to agents working with code in this repository.

## AI Provider Layer — architecture (Phase 6 + provider abstraction)

### Provider selection

The `/agent/recommend` endpoint is provider-agnostic.  The provider is selected
at request time by `backend/app/agent/provider_factory.py`:

```
if GCSI_GRANITE_API_KEY is set  →  GraniteProvider  (wraps GraniteAgent)
elif GCSI_OLLAMA_ENABLED=true and Ollama reachable  →  OllamaProvider
else  →  LocalRuleBasedProvider  (default, always available)
```

**No API key is required for the default demo path.**

### Common interface — BaseAIProvider

All providers implement `backend/app/agent/base_provider.py::BaseAIProvider`:

```python
class BaseAIProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    def recommend(link_state, mission_state, plans, evaluations) -> AIRecommendation: ...
```

All providers raise the **same canonical exceptions**:
- `AIProviderError` — provider unavailable / network error
- `AIResponseError` — malformed or invalid response
- `AIHallucinationError` — evidence cites a non-existent field

`routes_agent.py` catches only these canonical types; it never imports
provider-specific exceptions.

### LocalRuleBasedProvider (default)

`backend/app/agent/local_provider.py` — works offline, no dependencies beyond
the existing `EvaluationResult` models.

Algorithm:
1. Rank all candidate plans by `(risk_score ASC, -mission_value ASC, plan_id)`.
2. Recommend the best; set runner-up as `alternative_plan_id`.
3. Derive `confidence` from the risk-score gap between best and runner-up.
4. Build `EvidenceItem` objects citing only real fields from `LinkState`,
   `MissionState`, and `EvaluationResult`.
5. Return a fully validated `AIRecommendation`.

This is **not a mock**.  The recommendation changes when inputs change.
It is deterministic given identical inputs (testable and reproducible).

### OllamaProvider (opt-in)

`backend/app/agent/ollama_provider.py` — calls a locally-running Ollama server
via its HTTP REST API (`POST /api/generate`).  Uses the same system prompt and
response validation as `GraniteAgent`.  Enabled with `GCSI_OLLAMA_ENABLED=true`.
Falls back to `LocalRuleBasedProvider` if the server is not reachable.

Configuration:
- `GCSI_OLLAMA_URL` (default: `http://localhost:11434`)
- `GCSI_OLLAMA_MODEL` (default: `llama3.2`)
- `GCSI_OLLAMA_TIMEOUT` (default: `60.0` seconds)

### GraniteProvider / GraniteAgent (optional, IBM Granite)

`backend/app/agent/granite_provider.py` wraps `GraniteAgent` and maps its
exception types to the canonical `AIProviderError` / `AIResponseError` /
`AIHallucinationError` hierarchy.

`GraniteAgent.recommend()` operates as a **context-injection** (prompt-based) agent:

1. The deterministic pipeline runs first:
   - `TelecomEngine` computes `LinkState` from raw scenario inputs.
   - `CandidateGenerator` produces four `CandidatePlan` variants.
   - `PlanEvaluator` evaluates all four plans analytically (no RNG).
2. All four objects are serialized to JSON and passed to Granite as a structured
   user message.
3. Granite reasons over these pre-computed facts and returns a structured
   `AIRecommendation` JSON object.
4. The response is parsed and validated server-side before being returned.

### What the AI layer does and does not do

| Responsibility | Python (deterministic) | AI Layer |
|----------------|----------------------|----------|
| RF calculations (Eb/N0, BER, goodput) | ✓ | ✗ never |
| Packet scheduling / scoring | ✓ | ✗ never |
| Plan evaluation metrics | ✓ | ✗ never |
| Stochastic simulation | ✓ | ✗ never |
| Plan recommendation reasoning | — | ✓ |
| Evidence citation | — | ✓ (validated server-side) |
| Human-readable justification | — | ✓ |

### TOOL_SCHEMAS — deferred / future capability

`backend/app/agent/tools.py` defines `TOOL_SCHEMAS`: a list of
OpenAI-compatible function-calling schemas for six domain tools.

**These schemas are NOT currently passed to any API.**
The current implementation uses prompt-based context injection, not
function-calling.  `TOOL_SCHEMAS` exists as a draft for a future
iterative tool-calling loop.

### Validation invariants (enforced in code — all providers)

- `recommended_plan_id` **must** be one of the provided `plan_id` values.
- `alternative_plan_id` **must** be `None` or one of the provided `plan_id` values.
- `EvidenceItem.field` **must** be a real field name in `LinkState`,
  `MissionState`, or `EvaluationResult` → `AIHallucinationError`.
- `risk_level` **must** be a valid `RiskLevel` enum value.
- `confidence` and `risk_score` **must** be in `[0.0, 1.0]`.
- Invalid output always fails loudly with a typed exception; fabricated
  recommendations are never silently returned.

### Response contract — `RecommendResponse`

`POST /agent/recommend` now returns a `RecommendResponse` wrapper:

```json
{
  "provider": "Local",
  "recommendation": { ...AIRecommendation fields... }
}
```

The `provider` field is displayed in the frontend header badge so the operator
always knows which AI backend produced the recommendation.

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

### `asyncio_mode = "auto"` is set in `pyproject.toml` but `@pytest.mark.asyncio` is still required

The installed `pytest-asyncio` version (1.4.0) does not honour `asyncio_mode = "auto"` at the
ini level.  All async test functions **must** carry `@pytest.mark.asyncio`.
This applies to both module-level and class-method async tests.

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
