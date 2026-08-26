# GCSI Problem / Solution

---

## The Problem

Spacecraft in deep space generate mission data at rates that far exceed the capacity of individual
communication windows. During nominal operations this creates scheduling complexity. During anomaly
conditions it creates urgency: the data needed to understand the anomaly is somewhere in the queue,
but it competes with hundreds or thousands of other products for the same limited contact window.

**The operator must decide:**
- Which products carry the most mission-critical information right now?
- What risk does deferring each product create?
- What fits in the available contact window given link conditions?
- Which transmission plan best balances competing priorities?

### ASTERIA-7: A Concrete Illustration

1,284 products queued. 2.74 GB total. 85.7 MB of contact capacity. 4 minutes 32 seconds of contact.
Active thermal anomaly — unresolved. The diagnostic data is in the queue. The cause is unknown.

Without decision support, manually triaging 1,284 products under anomaly pressure at mission pace
is not a tractable problem.

---

## Why Simple Criticality Sorting Is Insufficient

Sorting by criticality alone ignores:

- **Anomaly context**: A moderate-criticality thermal diagnostic may be more mission-relevant than a
  high-criticality routine telemetry product during an active thermal anomaly.
- **Deadline urgency**: Products expiring before the next contact window should be elevated.
- **Scientific coherence**: Related products (diagnostic + calibration + event log) are more valuable
  together than individually.
- **Link feasibility**: A large high-criticality product that cannot fit in the window is a poor choice.
- **Multi-objective trade-offs**: Maximizing delivered critical packets vs. maximizing anomaly coverage
  vs. minimizing risk are competing objectives that a single sort key cannot resolve.

---

## The GCSI Approach

GCSI separates prioritization into three cooperating layers:

### 1. Deterministic Candidate Screening

`CandidatePrioritizer` screens the full product queue using a multi-factor deterministic algorithm:
anomaly-linked products, critical products, near-deadline products, high-relevance products, and
related-product completion. This produces a bounded 50-candidate set for AI reasoning.

**Why this matters**: The AI sees only the most operationally relevant candidates, not all 1,284
products. This is a deliberate design choice — it reduces noise, controls prompt size, and ensures
the most important products are always represented.

### 2. AI Semantic Prioritization (Advisory)

IBM Granite receives the 50 candidates with full semantic context: product descriptions, anomaly event
details, mission phase, link conditions. It produces an anomaly-aware semantic ranking.

This ranking drives the `ai-prioritized` plan. Four deterministic plans are generated independently
as a scientific control group.

**What AI adds that deterministic ranking cannot**: Natural-language semantic reasoning. Understanding
that "high-rate thermal telemetry from the last 11 minutes" is more valuable than "routine thermal
survey from 6 days ago" during an active thermal anomaly — even if both have similar criticality scores.

### 3. Why Deterministic Evaluation Is Required

All five plans are evaluated by the same `PlanEvaluator` (telecom physics) and
`MissionOutcomeEvaluator` (mission-semantic outcomes). No AI-specific scoring bonus.

The Stage-2 recommendation uses provenance-blind OPTION aliases — the AI cannot know which plan it
generated. This prevents automation bias from inflating the AI plan's ranking.

### 4. Why Human Approval Remains Required

- AI confidence is not calibrated — it is advisory, not probabilistic
- The telecom model is simplified; an operator has situational awareness the model lacks
- Transmission authorization has operational consequences that require human accountability
- The operator may have information not captured in the scenario data

---

## Technical Innovation Summary

| Innovation | Description |
|---|---|
| Bounded candidate screening | 1,284 products screened to 50 before AI reasoning |
| Five-plan objective comparison | AI plan competes against 4 deterministic baselines under identical evaluation |
| Stage-2 provenance blinding | OPTION-A…E aliases prevent AI self-preference bias |
| Authoritative fact reconstruction | Backend reconstructs packet facts on approval; client intent cannot tamper |
| Stochastic simulation | Bernoulli transmission trials with ground reception evidence visualization |
| Fail-closed recommendation | Invalid AI recommendations fall back to deterministic Local without silent substitution |
