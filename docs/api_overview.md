# GCSI API Overview

> **Mutation labeling**: endpoints are marked READ-ONLY or STATE-MUTATING.
> Read-only endpoints are safe to call without side effects.
> State-mutating endpoints change backend scenario/approval state.

---

## Core Endpoints

### State & Scenario

| Method | Path | Mutation | Description |
|---|---|---|---|
| `GET` | `/state` | READ-ONLY | Full mission state: link conditions, scenario info, propagation delay, approval status. Includes `source` sub-object with `mode`, `is_historical_replay`, `provenance_scope`, and `provenance_kind_counts` when in historical replay mode. |
| `GET` | `/health` | READ-ONLY | Liveness probe: version, scenario loaded, product count. In historical replay mode also reports `source_mode`, `historical_replay_active`, and `source_provenance_available`. |
| `POST` | `/state/reset` | **STATE-MUTATING** | Reset to initial state. In historical replay mode: deterministic, `randomized: false`, `scenario_path: null`. In synthetic mode: randomized jitter applied. |

### Data Products & Queue

| Method | Path | Mutation | Description |
|---|---|---|---|
| `GET` | `/data-products` | READ-ONLY | All data products in the active scenario (with pagination/filter support) |
| `GET` | `/queue` | READ-ONLY | Packet queue summary for the active scenario |

### Plan Generation & Evaluation

| Method | Path | Mutation | Description |
|---|---|---|---|
| `POST` | `/plans/generate` | READ-ONLY | Generate the five-plan set (baseline + 4 deterministic) for the current state |
| `POST` | `/plans/evaluate` | READ-ONLY | Evaluate a submitted candidate plan (non-mutating what-if) |
| `POST` | `/plans/assess` | READ-ONLY | Assess a manually-selected product list (non-mutating feasibility check) |
| `POST` | `/plans/what-if` | READ-ONLY | Evaluate plans under hypothetical SNR/BER overrides (never mutates link state) |

### AI Agent

| Method | Path | Mutation | Description |
|---|---|---|---|
| `POST` | `/agent/recommend` | READ-ONLY* | Run Stage-1 + Stage-2 AI pipeline; returns advisory recommendation |

*`/agent/recommend` does not mutate persistent state. The issued-plan registry is only updated by `/approve`.

### Approval

| Method | Path | Mutation | Description |
|---|---|---|---|
| `POST` | `/approve` | **STATE-MUTATING** | Approve a previously-issued plan. Backend reconstructs authoritative packet list; registers approved plan. |
| `POST` | `/approve/custom` | **STATE-MUTATING** | Approve a custom product selection. Backend constructs and registers authoritative plan. |

Both approval endpoints verify canonical fingerprint integrity before registering a plan.

### Simulation

| Method | Path | Mutation | Description |
|---|---|---|---|
| `POST` | `/simulate` | **STATE-MUTATING** | Run transmission simulation against the approved plan; updates simulation state. |
| `POST` | `/simulate/what-if` | READ-ONLY | Simulate a plan without modifying state (speculative only). |

### Experience / Mission Narrative

| Method | Path | Mutation | Description |
|---|---|---|---|
| `GET` | `/experience` | READ-ONLY | Loads the scenario experience manifest (ingest timeline, ground objectives, subsystem status). ASTERIA-7 only. |

### Scenario Management

| Method | Path | Mutation | Description |
|---|---|---|---|
| `GET` | `/scenarios` | READ-ONLY | List available scenario files |
| `POST` | `/scenarios/load` | **STATE-MUTATING** | Load a different scenario; resets all state |
| `POST` | `/scenarios/switch` | **STATE-MUTATING** | Switch to a different scenario by filename (basename only; path traversal rejected). Switches from historical to synthetic mode. |
| `POST` | `/scenarios/reset` | **STATE-MUTATING** | Alias for `/state/reset`. Reset active scenario to initial state. |

---

## Trust Semantics

```
READ-ONLY endpoints     → safe to call repeatedly; no side effects
STATE-MUTATING endpoints → change active scenario, approval, or simulation state
```

Approval endpoints reconstruct authoritative packet facts from the backend registry.
Client-submitted data is used for intent only — the backend rejects tampered packet facts.

See [`docs/trust_boundary.md`](trust_boundary.md) for full trust architecture.

---

## Interactive API Documentation

When the backend is running, the full OpenAPI schema is available at:

- `http://localhost:8000/docs` — Swagger UI
- `http://localhost:8000/redoc` — ReDoc
- `http://localhost:8000/openapi.json` — Raw schema

---

---

## Historical Replay Source Metadata

When `GCSI_SOURCE_MODE=historical_replay` is active, `/health` and `/state` expose
additional fields documenting the mission source context.

### GET /health (historical fields)

```json
{
  "source_mode": "historical_replay",
  "historical_replay_active": true,
  "source_provenance_available": true
}
```

### GET /state (source sub-object)

```json
{
  "source": {
    "mode": "historical_replay",
    "is_historical_replay": true,
    "provenance_scope": "source_baseline",
    "provenance_kind_counts": {
      "external_authoritative": 3,
      "derived": 13,
      "modeled": 1
    }
  }
}
```

**Provenance categories:**

| Kind | Meaning |
|---|---|
| `external_authoritative` | Value comes directly from a verified external NASA/JPL source |
| `derived` | Computed deterministically from authoritative inputs by GCSI |
| `modeled` | GCSI communication policy assumption — not from NASA |

---

*GCSI API Overview — Phase 6E-C8*
