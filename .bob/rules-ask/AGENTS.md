# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Non-obvious documentation context

### `backend/pyproject.toml` is the pytest config, but tests live outside `backend/`
`testpaths = ["tests"]` resolves relative to the repo root, not to `backend/`.
Always run `pytest` from `ground-control-signal-insight/`.

### Two separate risk concepts with different formulas — commonly confused
- `PlanEvaluator.risk_score`: weighted combination of `deadline_miss_rate`,
  `critical_deficit`, and `window_pressure`; purely analytical, no RNG.
- `TransmissionSimulator._derive_risk_level`: `(deferred + failed) / total`;
  realized after stochastic simulation. These are intentionally different; do
  not conflate them in answers.

### `docs/telecom_model.md` is the authoritative formula reference
If asked about how BER, Eb/N0, goodput, or packet success probability work,
start there before reading source.

### `gcsi-mvp-plan.md` is the authoritative architecture plan
Contains phase-by-phase implementation decisions. Consult before describing how
the system is intended to be extended.

### `data/` directory contains scenario JSON fixtures
Used by `tests/scenarios/test_scenario_e2e.py` and `simulation/scenario_loader.py`.

### `granite` mark = live IBM Granite API key required
Tests with `@pytest.mark.granite` are skipped in the standard suite (`-m "not granite"`).
