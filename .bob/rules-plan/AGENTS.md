# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Non-obvious architectural constraints

### Evaluator and Simulator are intentionally separate layers — must remain decoupled
`PlanEvaluator` is deterministic (no RNG). `TransmissionSimulator` is stochastic.
They must never call each other. A test enforces this: the simulator module may not
import from the evaluator. New features must not break this boundary.

### `window_s` is always `min(link_state.remaining_window_s, mission_state.comm_window_remaining_s)`
Both evaluator and simulator use the smaller of the two windows. Any new component
that consumes a comm window must follow the same convention.

### Packet ordering within `CandidatePlan` IS the priority — there is no priority field
The `BaselineScheduler` produces a sorted `CandidatePlan`. Any AI-generated plan
must produce the same structure. Adding a priority attribute to `Packet` would break
the scheduler contract.

### `expected_transmission_cost` returns `math.inf` for zero-probability packets
This is the canonical sentinel. Do not replace it with a large finite number or a
probability floor — the evaluator's `p_s <= 0.0` guard relies on the correct behavior.

### Config is fully env-driven with typed Pydantic models
All weights (`RiskWeights`, `SchedulerWeights`) and telecom constants (`TelecomConfig`)
are constructed from env vars. Extending config means adding a field with a `gt` /
`ge` / `le` constraint, not writing any parsing code.

### Only BPSK over AWGN is modelled — `TelecomConfig.modulation` is validated at construction
The validator hard-rejects anything other than `"BPSK"`. Adding a new modulation
scheme requires changing the validator and the `bpsk_ber` formula together.

### `failed_packets` in `SimulationResult` is a distinct third outcome
Architecture assumes three lists: `delivered`, `deferred`, `failed`. Any aggregation
layer (API response, dashboard metric) must surface all three, not collapse `failed`
into `deferred`.
