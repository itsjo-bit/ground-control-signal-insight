# GCSI Technical Innovation

This document describes the technical innovations in GCSI's architecture.

---

## 1. Bounded Candidate Screening Before AI Reasoning

**What**: `CandidatePrioritizer` deterministically screens the full product queue to a bounded
≤50 candidate set before any AI call.

**Why it's innovative**: Most LLM-assisted systems either send all data to the LLM (expensive,
noisy, token-limited) or apply a trivial filter. GCSI uses a multi-factor semantic pre-screening
that ensures anomaly-linked products, critical products, near-deadline products, and high-relevance
products are always represented in the candidate set — independent of AI reasoning.

This is not a limitation. It is a deliberate trust architecture: the AI only receives a
representative bounded set where its semantic reasoning is most valuable.

---

## 2. Five-Plan Objective Comparison with Identical Evaluators

**What**: Every planning session generates five independent transmission plans:
- `ai-prioritized` — AI-ranked prefix plan (causal AI plan)
- `baseline` — mission-value weighted sort (classical)
- `deadline-first` — earliest deadline ascending
- `mission-critical-first` — highest criticality descending
- `value-per-cost` — (criticality × mission_relevance) / expected_transmission_cost

All five are evaluated by the **same** `PlanEvaluator` and `MissionOutcomeEvaluator` instances
with identical inputs. No AI-specific scoring bonus.

**Why it's innovative**: The AI plan is not automatically preferred. If `deadline-first` produces
better mission outcomes, Stage-2 recommends `deadline-first`. The system is honestly comparative.

---

## 3. Stage-2 Provenance-Blind Plan Recommendation

**What**: Before Stage-2, `build_blind_mapping()` assigns OPTION-A through OPTION-E aliases to
the five plans. The external AI provider receives only OPTION aliases — never plan IDs, strategy
names, `generated_by` fields, or `plan_type` labels.

**Why it's innovative**: This prevents:
- **Self-preference bias**: The AI cannot favor the plan it may have generated
- **Automation bias**: "AI-prioritized" branding cannot inflate the AI plan's ranking
- **Provenance leakage**: Strategic labels are completely absent from the recommendation prompt

The alias→plan mapping is SHA-256 based, deterministic, and never exposed to the provider.

---

## 4. Authoritative Plan Reconstruction on Approval

**What**: When the operator approves a plan, the backend reconstructs authoritative packet facts
from its own registry. Client-submitted data is used for intent verification only.

The `approve` endpoint:
1. Verifies the canonical fingerprint of the submitted plan matches a registered issued plan
2. Reconstructs the authoritative packet list from the backend registry
3. Rejects any factual tampering (wrong size_bits, wrong anomaly_id, wrong subsystem)

**Why it's innovative**: A malicious or buggy client cannot change what actually gets transmitted
by modifying the plan data it submits. The backend is the single source of truth for packet facts.

---

## 5. Two-Stage AI with Independent Evaluation

**Stage 1 — Semantic Prioritization**:
The AI orders candidates by mission importance. It receives rich semantic context: product
descriptions, anomaly metadata, mission phase, link conditions.

**Stage 2 — Plan Recommendation**:
The AI evaluates compact metric summaries for all five plans and recommends one. It never sees
the full packet lists, candidate scores, or plan provenance.

**Independence property**: Stage-2 evaluation metrics come from deterministic `PlanEvaluator`
and `MissionOutcomeEvaluator` — not from the AI. The AI cannot invent superior metrics for
itself.

---

## 6. Evidence Binding from Authoritative Data

**What**: When the AI provides `evidence` items (citations for its recommendation), the backend
**replaces** the AI-supplied values with authoritative values from the deterministic evaluators.
The AI provides field labels (what it's citing); the backend provides the actual numbers.

**Why it matters**: The AI cannot fabricate favorable metrics to support its recommendation.
Evidence displayed to the operator is always deterministic backend output.

---

## 7. Transmission Simulation with Ground Reception Evidence

**What**: `TransmissionSimulator` runs Bernoulli trials against packet success probability for
each transmission attempt. After simulation, the ground reception panel shows the before/after
information state: which mission objectives are now resolvable given the received products.

**Why it matters**: The complete mission story — from triage to transmission to ground reception
— is demonstrated end-to-end. The operator sees what Earth now knows.

---

## 8. Fail-Closed Recommendation Finalization

**What**: If an AI recommendation references an invalid plan ID, the finalization process falls
back to the `LocalRuleBasedProvider` deterministically. The response accurately reports:
- `requested_provider`: the originally configured provider
- `actual_provider`: `Local`
- `recommendation_fallback_reason`: the reason for fallback

**Why it matters**: An invalid AI recommendation never silently reaches the operator as if it
were valid. The fallback is transparent, labeled, and always produces a deterministic result.

---

## 9. Benchmark with Fairness Controls

The scientific benchmark (`gcsi_benchmark_v1`) enforces:
- Same candidate set for AI and semantic-rule plans
- Same `build_ranked_prefix_plan()` for plan construction
- Same `PlanEvaluator` and `MissionOutcomeEvaluator` — no AI scoring bonus
- Failed Granite trials recorded as failures, not silently substituted with Local
- No cherry-picking: failed trials retained in raw data; retry for poor performance prohibited
- No composite AI score: multi-dimensional Pareto comparison used

The methodology is pre-registered and frozen before benchmark execution.

---

*GCSI Technical Innovation — Phase 5*
