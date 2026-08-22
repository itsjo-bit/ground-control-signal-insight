# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Non-obvious coding rules

### `RiskWeights` / `SchedulerWeights` weight fields are `gt=0.0` — never pass `0.0`
Use `1e-9` in tests that want to zero out a weight. `0.0` raises `ValidationError`.

### pytest runs from repo root, config lives in `backend/pyproject.toml`
`pythonpath = ["."]` means imports resolve from `ground-control-signal-insight/`, so
`from backend.app.evaluator.plan_evaluator import PlanEvaluator` works in tests.
`asyncio_mode = "auto"` — no `@pytest.mark.asyncio` needed.

### `expected_transmission_cost` returns `math.inf` — callers must handle it
The function deliberately never substitutes a probability floor. Any scheduler or
generator that calls it must guard against `math.inf` explicitly before arithmetic.

### `packet_success_probability` must use log-space form
`exp(N * log1p(-BER))` — never `(1 - BER) ** N`. Float underflow silently produces
`0.0` for large packets, breaking all downstream logic.

### `Packet` has no priority field — ordering is the only priority signal
Do not add a `priority` field. Modify `CandidatePlan.packets` order instead.

### `TransmissionSimulator` must never import `PlanEvaluator`
A test (`test_no_evaluator_import_used_in_simulator_module`) enforces this. Mixing the
two layers breaks the deterministic/stochastic separation contract.

### `SimulationResult` has three outcome lists: `delivered`, `deferred`, `failed`
`failed` ≠ `deferred`. Always populate all three and check all three downstream.

### Config is env-driven via `pydantic-settings`
`RiskWeights`, `SchedulerWeights`, `TelecomConfig` all read from env vars at
construction time (prefixes `GCSI_RISK_`, `GCSI_SCHED_`, `GCSI_TELECOM_`). In tests,
pass explicit constructor args to avoid ambient env interference.
