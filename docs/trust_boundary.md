# GCSI Phase 4.1a — Trust, Safety & Authoritative Execution Boundaries

## Overview

Phase 4.1 closes the remaining gaps between the documented trust principle
and the actual implementation, after Phase 4 established the core framework.

The central principle:

> **CLIENTS SUBMIT INTENT.**  
> **THE BACKEND RECONSTRUCTS FACTS.**  
> **DETERMINISTIC EVALUATORS AUTHORIZE PHYSICAL CLAIMS.**  
> **THE HUMAN OPERATOR AUTHORIZES EXECUTION.**

### Operator Trust Chain

```
Client plan request
      ↓
IDs/order treated as intent
      ↓
Authoritative scenario facts
      ↓
Trusted backend provenance
      ↓
Canonical plan
      ↓
Deterministic evaluation
      ↓
AI advisory reasoning
      ↓
Authoritative recommendation finalization
      ↓
Human approval
      ↓
Canonical authoritative execution
```

---

## 1. What Clients Control

Clients may control only:

| Field | Path |
|-------|------|
| Ordered packet IDs (which packets, in which order) | `CandidatePlan.packets[].packet_id` |
| Operator notes (free text, trimmed to 500 chars) | `ApproveRequest.operator_notes` |
| Plan ID reference (string key) | `ApproveRequest.plan_id` |

### What Clients Do NOT Control

The following fields are **not authoritative** when submitted by a client:

- `CandidatePlan.strategy`
- `CandidatePlan.generated_by`
- `CandidatePlan.metadata` (all keys)
- `Packet.size_bits`, `criticality`, `mission_relevance`, `deadline_s`,
  `retry_cost`, `delivery_requirement`, `packet_type`
- Any claim of `plan_source`, AI provenance, benchmark provenance, or
  evaluator identity

These fields are either **ignored** (standard issued-plan path) or
**replaced** with authoritative backend values (all reconstruction paths).

---

## 2. What the Backend Reconstructs

The backend is the sole authority for:

| Field | Source |
|-------|--------|
| `size_bits` | Authoritative scenario inventory |
| `criticality` | Authoritative scenario inventory |
| `mission_relevance` | Authoritative scenario inventory |
| `deadline_s` | Authoritative scenario inventory |
| `retry_cost` | Authoritative scenario inventory |
| `delivery_requirement` | Authoritative scenario inventory |
| `packet_type` | Authoritative scenario inventory |
| `strategy` | Backend-assigned (`backend:<plan_source>`) |
| `generated_by` | Backend-assigned (`backend:<plan_source>`) |
| `metadata.plan_source` | Backend trust classification |
| `metadata.authoritative_reconstruction` | Always `true` |
| `risk_score` | Deterministic `PlanEvaluator` — never from AI |
| `risk_level` | Deterministic `PlanEvaluator` — never from AI |
| `packet_actions` | Rebuilt from authoritative plan ordering |
| `confidence_semantics` | Backend-assigned provider category |

---

## 3. Trusted Plan Provenance

### Standard Issued-Plan Path (`POST /approve` with plan)

The **trusted source** for plan provenance is the canonical issued plan
stored in the backend registry. The client-submitted `CandidatePlan` is
only an ID/order transport:

- `plan_id`, `strategy`, `generated_by`, trusted metadata, `plan_source` —
  all come from the **server-issued canonical plan** in the registry.
- Only packet IDs and order are compared against the submitted plan.
- Client-submitted `strategy`, `generated_by`, and `metadata` are **ignored**.

### Operator Custom Plans (`POST /approve/custom`)

The operator may choose packet IDs and ordering. However, the backend
assigns provenance:

- `plan_source = operator_custom` (backend-controlled)
- `strategy = "backend:operator_custom"` (backend-controlled)
- `generated_by = "backend:operator_custom"` (backend-controlled)
- Client-submitted strategy and metadata do not survive as authoritative provenance.

### Generic Client-Intent Paths (`POST /plans/evaluate`, `POST /simulate/what-if`)

- The submitted `CandidatePlan` is a convenient transport object.
- Packet facts are replaced with authoritative scenario values.
- `plan_source = client_intent` (backend-assigned).
- Client provenance is **not preserved**.

### Backend-Generated Plans (`POST /plans/generate`, `POST /agent/recommend`)

Backend-generated plans carry trusted provenance because they originate
from the backend:
- `plan_source = deterministic_generated` or `ai_generated`
- `strategy` and `generated_by` are set by the generator/AI pipeline.

---

## 4. Canonical Issued-Plan Fingerprint Lifecycle

The invariant that must hold for every issued plan:

```
stored canonical_plan_sha256
    == SHA-256 of the exact canonical plan stored in the registry
    == SHA-256 of the exact canonical plan issued to the operator
```

### Required Order of Operations (`canonicalize_issued_plan`)

```
1. Assign trusted plan_source to metadata        ← BEFORE hashing
2. Assign authoritative_reconstruction = True    ← BEFORE hashing
3. Compute packet_order_sha256                   ← over finalized plan
4. Compute canonical_plan_sha256                 ← over finalized plan
5. Deep-copy into registry snapshot              ← immutable
6. Surface canonical issued plan to operator
```

**Important:** If `plan_source` is set AFTER hashing, the stored hash
will have been computed with `plan_source = "unknown"` while the stored
plan contains the actual value. This ordering bug is corrected in Phase 4.1.

### What the Canonical Fingerprint Covers

The canonical fingerprint (SHA-256) is computed over the **stable execution
identity** of a plan, not over every serialized field of the `CandidatePlan`.

```json
{
  "scenario_id": "<active scenario ID>",
  "plan_id":     "<plan_id>",
  "plan_source": "<backend-assigned plan_source>",
  "packets":     [
    { "packet_id", "packet_type", "size_bits", "criticality",
      "mission_relevance", "deadline_s", "retry_cost", "delivery_requirement" }
  ]
}
```

The fingerprint covers:
- `scenario_id` — binds the plan to the active session
- `plan_id` — unique plan identifier
- `plan_source` — backend-assigned trust classification
- Ordered authoritative packet fields (`packet_id`, `packet_type`, `size_bits`,
  `criticality`, `mission_relevance`, `deadline_s`, `retry_cost`,
  `delivery_requirement`)

The fingerprint does **not** cover `strategy`, `generated_by`, or arbitrary
metadata keys other than `plan_source`.  It is a **canonical execution
fingerprint** over the stable authoritative execution identity — not a hash
of every field in the full `CandidatePlan` serialization.

---

## 5. Issued-Plan Registry Snapshot Semantics

Phase 4.1 makes the registry store a **deep canonical snapshot**, not a
mutable caller reference.

- `state.register_issued_plan()` calls `plan.model_copy(deep=True)` before
  storing the plan in the registry.
- Modifying the original plan object after registration does **not** affect
  the registry canonical plan.
- Fingerprints stored in the registry always match the registry snapshot.
- The registry remains in-memory (no persistence, no database).

---

## 6. Approval Verification (`POST /approve`)

Phase 4.1 adds three explicit fail-closed checks before any execution:

### 6.1 Scenario Binding

```python
assert record.scenario_id == active_scenario.scenario_id
```

A stale plan issued under a different scenario must not execute.
→ **HTTP 409** with reason `STALE_PLAN` if mismatch.

### 6.2 Registry Canonical Fingerprint Integrity

Recompute the canonical SHA from `record.canonical_plan` and verify:

```python
_compute_canonical_hash(record.canonical_plan, record.scenario_id)
    == record.canonical_plan_sha256
```

An inconsistency indicates an internal state corruption.
→ **HTTP 500** with reason `FINGERPRINT_MISMATCH` if mismatch.
→ No simulation runs.

### 6.3 Submitted Order Verification

```python
_compute_order_hash([p.packet_id for p in req.plan.packets])
    == record.packet_order_sha256
```

The submitted packet IDs/order must exactly match the registered order.
→ **HTTP 422** if mismatch (order, subset, or identity tampering).

### 6.4 Executed Plan Canonical Identity

For standard issued-plan approval, the plan that enters `TransmissionSimulator`
is the **canonical issued plan from the registry** — not a reconstruction of the
client submission. This guarantees:

```
executed canonical SHA == issued canonical SHA
```

### 6.5 Client Factual Tampering Policy

For the standard issued-plan path, client-submitted `size_bits`, `criticality`,
`strategy`, `generated_by`, and `metadata` are **safely ignored** because the
authoritative canonical issued plan is used directly from the registry.

The policy is: **Ignore untrusted factual/provenance fields. Verify IDs/order.
Execute canonical server-issued facts/provenance.**

---

## 7. Recommendation Finalization

Phase 4.1a strengthens the `finalize_recommendation()` layer to be
**fail-closed**.  It runs after **every** operator-facing recommendation path,
including:

- External compact Stage-2 (Granite, Gemini, Ollama)
- Local rule-based provider path
- Legacy external/direct recommendation path
- Fallback paths

> **Every operator-facing recommendation must successfully bind to an
> authoritative candidate plan and EvaluationResult before it is returned.**
>
> **Recommendations that cannot be authoritatively finalized are rejected and
> routed through the deterministic Local fallback.**

### 7.1 Finalizer Responsibilities

Given a provider-produced recommendation plus authoritative plans/evaluations:

| Step | What happens |
|------|-------------|
| 1 | Validate `recommended_plan_id` refers to a real candidate plan — **RAISE** `RecommendationFinalizationError` if not found |
| 2 | Locate authoritative `CandidatePlan` |
| 3 | Locate authoritative `EvaluationResult` — **RAISE** `RecommendationFinalizationError` if not found |
| 4 | **REPLACE** `risk_score` and `risk_level` with authoritative values |
| 5 | **REBUILD** `packet_actions` from the authoritative plan ordering |
| 6 | Validate `alternative_plan_id`; silently set to `null` if not a known plan (soft drop — optional field) |
| 7 | **ASSIGN** `confidence_semantics` from the ACTUAL provider category |
| 8 | Preserve `reasoning` as advisory text |
| 9 | Preserve `evidence` (already bound by Stage-2 evidence validation) |

### 7.2 Authoritative vs Advisory Fields

| Field | Status |
|-------|--------|
| `risk_score` | **Authoritative** — deterministic `PlanEvaluator` |
| `risk_level` | **Authoritative** — deterministic `PlanEvaluator` |
| `packet_actions` | **Authoritative** — rebuilt from canonical plan |
| `recommended_plan_id` | **Authoritative** — validated against known plans |
| `confidence_semantics` | **Authoritative** — backend-assigned |
| `reasoning` | Advisory — provider text |
| `confidence` | Advisory — provider self-report |
| `evidence` | Advisory — provider evidence (source-whitelist validated) |

### 7.3 Typed Finalization Error

`RecommendationFinalizationError` is raised with a typed `reason` code:

| Reason code | Condition |
|-------------|-----------|
| `UNKNOWN_RECOMMENDED_PLAN` | `recommended_plan_id` not in authoritative plan set |
| `MISSING_EVALUATION` | Plan exists but no `EvaluationResult` for that plan |
| `UNFINALIZABLE_RECOMMENDATION` | Generic — any other unfinalizable condition |

The route layer catches this error and triggers the Local deterministic fallback.
If Local fallback itself fails or fails finalization, HTTP 502 is returned.

### 7.4 Fail-Closed Route Flow

```
External provider
    → recommendation
    → finalize_recommendation()
    → if RecommendationFinalizationError:
        → LocalRuleBasedProvider.recommend()
        → finalize_recommendation() [Local result]
        → if also fails: HTTP 502
        → else: return Local result + recommendation_fallback_reason
    → else: return finalized result
```

The operator-facing response always reflects the actual provider via
`actual_provider`, `recommendation_provider`, and `recommendation_fallback_reason`.

### 7.5 Stage-2 Blinding Preserved

The existing compact Stage-2 architecture is preserved and unaffected:
- Opaque OPTION aliases (OPTION-A … OPTION-E)
- Source whitelist evidence validation
- Option-specific evidence binding
- Backend authoritative value binding
- Alias → real plan mapping

`finalize_recommendation()` runs **after** Stage-2 alias/evidence resolution
and is idempotent for the risk fields already rebound in the blinded path.

**The finalizer is a second safety net.**  For the blinded path, alias mapping
already validates plan identity.  The finalizer still runs.  If it cannot bind
the mapped plan and evaluation, it fails closed — prior validation does not
make finalizer failure impossible.

---

## 8. Deterministic Risk Rebinding

`AIRecommendation.risk_score` and `AIRecommendation.risk_level` are
**always** sourced from the deterministic `PlanEvaluator`, not from AI
self-reporting.

This is enforced at two levels:

1. **Per-path rebinding**: The compact Stage-2 path already rebinds risk
   inside `_build_blind_recommend()` before returning.

2. **Universal finalization**: `finalize_recommendation()` applies to every
   path and overwrites provider-supplied values with authoritative
   `EvaluationResult` values.

Frontend implementations must display risk values as deterministic backend
outputs, not as AI estimates.

---

## 9. Confidence Semantics

`AIRecommendation.confidence` is the provider's self-reported estimate.
It is advisory and must never be described as a calibrated probability.

### Typed Enum (`ConfidenceSemantics`)

| Value | Meaning |
|-------|---------|
| `heuristic` | Deterministic risk-gap (`LocalRuleBasedProvider`) |
| `uncalibrated_llm` | LLM self-report — not a calibrated probability |
| `unspecified_uncalibrated` | Fail-safe default — provenance unknown |

### Assignment Policy

The backend assigns `confidence_semantics` based on **explicit `isinstance`
checks against known provider classes** — never by string matching.
Provider-returned JSON cannot override this:

| Provider | Assigned semantics |
|----------|--------------------|
| `LocalRuleBasedProvider` | `heuristic` |
| `GraniteProvider` | `uncalibrated_llm` |
| `GeminiProvider` | `uncalibrated_llm` |
| `OllamaProvider` | `uncalibrated_llm` |
| Unknown class / `None` | `unspecified_uncalibrated` |

**The fail-safe default for unknown providers is `unspecified_uncalibrated`,
NOT `uncalibrated_llm`.**  This prevents future deterministic optimizers,
rules engines, or other non-LLM providers from being incorrectly labelled
as uncalibrated LLMs.  An unknown provider class does not prove LLM identity.

### Frontend Advisory Wording

| `confidence_semantics` | Advisory label |
|------------------------|----------------|
| `uncalibrated_llm` | "advisory — uncalibrated LLM estimate" |
| `heuristic` | "advisory — deterministic heuristic estimate" |
| `unspecified_uncalibrated` | "advisory — uncalibrated estimate" |

---

## 10. Stage-1 vs Stage-2 Provider Identity

The recommendation pipeline has two separate AI stages with independently
tracked providers.

### Response Fields

| Field | Meaning | Backwards compat |
|-------|---------|-----------------|
| `provider` | Equals `actual_provider` | ✓ unchanged |
| `requested_provider` | Originally configured provider | ✓ unchanged |
| `actual_provider` | Final recommendation provider | ✓ unchanged |
| `prioritization_provider` | Actual Stage-1 provider; `null` for legacy | New in 4.1 |
| `recommendation_provider` | Actual Stage-2 provider; equals `actual_provider` | New in 4.1 |

### Fallback Examples

| Case | `prioritization_provider` | `recommendation_provider` |
|------|--------------------------|--------------------------|
| Both stages Granite | `Granite` | `Granite` |
| Stage-1 fallback to Local, Stage-2 Granite | `Local` | `Granite` |
| Stage-1 Granite, Stage-2 fallback to Local | `Granite` | `Local` |
| Both stages Local | `Local` | `Local` |
| Legacy scenario (no Stage-1) | `null` | actual Stage-2 provider |

---

## 11. Fingerprints Are Integrity Fingerprints, Not Authentication Signatures

The SHA-256 fingerprints used throughout GCSI are **integrity fingerprints**
for session-level traceability only. They are NOT:

- Cryptographic authentication tokens
- Digital signatures
- Proof of authorship
- Tamper-evident in the cryptographic sense (no private key)

They verify that the plan evaluated is the same plan executed **within a
single server session**. They do not provide security guarantees against an
adversary with direct access to the in-memory state.

Use them for:
- Detecting accidental plan substitution
- Confirming the `executed_plan` in `ApproveResponse` matches the registry
- Audit traceability within a session

---

## 12. Plan Source Classification

| `plan_source` value | Produced by | `issued_plan_verified` |
|---------------------|-------------|----------------------|
| `deterministic_generated` | `POST /plans/generate` | `true` |
| `ai_generated` | AI-prioritized plan in `POST /agent/recommend` | `true` |
| `operator_custom` | `POST /approve/custom` | `false` |
| `legacy_regenerated` | Legacy `/approve` (only `plan_id`, no `plan`) | `false` |
| `client_intent` | Generic client submission | N/A |

---

## 13. Summary: Trust Boundary Enforcement Points

| Endpoint | Registry check | Provenance assigned by | Packet facts | State mutates | Invalidates registry |
|----------|---------------|----------------------|-------------|--------------|---------------------|
| `POST /plans/generate` | — | Backend | Scenario | No | No |
| `POST /plans/evaluate` | No | `client_intent` | Scenario | No | No |
| `POST /plans/what-if` | No | Backend (scenario) | Scenario | No | No |
| `POST /simulate` | No | Backend (regenerated) | Scenario | Yes | Yes |
| `POST /simulate/what-if` | No | `client_intent` | Scenario | No | No |
| `POST /approve` (standard) | **Yes — 409/500 if fail** | **Registry canonical plan** | Registry | Yes | Yes |
| `POST /approve` (legacy) | No | `legacy_regenerated` | Scenario | Yes | Yes |
| `POST /approve/custom` | No | `operator_custom` | Scenario | Yes | Yes |
| `POST /agent/recommend` | — | Backend | Scenario | No | No (registers) |

---

## 14. Phase 4.1a Change Summary

Phase 4.1a (fail-closed finalization) adds the following changes on top of Phase 4.1:

| Area | Change |
|------|--------|
| `finalize_recommendation()` | Now raises `RecommendationFinalizationError` on unknown plan or missing eval (was: return unchanged) |
| `RecommendationFinalizationError` | New typed error with `reason` attribute and typed reason codes |
| Route fallback | `_finalize_or_fallback()` helper: finalization failure triggers Local fallback; Local failure returns 502 |
| Confidence semantics | Explicit `isinstance` checks for `GraniteProvider`, `GeminiProvider`, `OllamaProvider`; unknown provider → `unspecified_uncalibrated` (was: `uncalibrated_llm`) |
| Tests | New `tests/unit/test_phase4_1a.py` with targeted regression tests |

---

## 15. Remaining Limitations

The following limitations are explicitly acknowledged:

- **In-memory state only.** The issued-plan registry, approval trace, and
  all server state are in-process memory. A server restart clears all state.

- **No persistent approval audit database.** `ApprovalTrace` is returned in
  the response and available as `state.last_approval_trace`. It is not
  persisted across restarts.

- **SHA-256 fingerprints are not digital signatures.** They do not provide
  cryptographic authentication against an adversary with state access.

- **Confidence is not calibrated.** Neither `heuristic` nor `uncalibrated_llm`
  confidence values are statistically calibrated probabilities.

- **Stochastic simulator is an abstraction.** `TransmissionSimulator` is
  an abstract retransmission model. It does not model specific telecom
  protocols, ARQ schemes, or real link dynamics.

- **Single-process only.** The trust model is designed for a single server
  process. Multi-process deployments would require shared registry state.
