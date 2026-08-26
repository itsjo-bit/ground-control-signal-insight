# GCSI Submission Summary

---

## 50-Word Version

GCSI helps mission operators decide which spacecraft data to transmit first during limited contact windows.
When ASTERIA-7 queues 1,284 products and only 3% fit, AI semantic triage identifies what matters most —
but deterministic telecom analysis and human approval remain authoritative. The operator always decides.

---

## 150-Word Version

Spacecraft generate far more data than a single contact window can carry.
GCSI — Ground Control Signal Insight — helps mission operators triage that data intelligently.

In the canonical ASTERIA-7 scenario: 1,284 queued products, 2.74 GB, only ~85.7 MB of contact capacity,
and an active unresolved thermal anomaly. The operator has 4 minutes 32 seconds.

GCSI screens all 1,284 products to a bounded 50-candidate set, applies AI semantic prioritization
aware of the anomaly context, evaluates five competing transmission plans deterministically, and presents
an advisory recommendation. A provenance-blind Stage-2 comparison prevents AI self-preference bias.
The human operator retains final approval authority — no transmission happens automatically.

IBM Granite is the primary AI provider. The Local deterministic fallback requires no API key.
The benchmark infrastructure is implemented; the official Granite efficacy evaluation is pending.

---

## 300-Word Version

### The Problem

Spacecraft generate a continuous stream of mission data. During anomaly conditions, some of that data is
immediately critical — but communication windows are finite, link quality may be degraded, and the operator
cannot manually triage hundreds or thousands of products at mission pace.

### The ASTERIA-7 Mission

GCSI's canonical demonstration: ASTERIA-7 has 1,284 queued products totaling 2.74 GB. The contact
window carries ~85.7 MB — 3.1% of the queue. An active thermal anomaly (severity 0.94) is unresolved.
The spacecraft is 182 million km from Earth; the one-way signal takes 10 minutes 8 seconds.

### The GCSI Approach

GCSI combines three distinct layers:

**Deterministic layer** (authoritative): A BPSK/AWGN analytical link model computes BER, goodput, and
feasibility. The `PlanEvaluator` and `MissionOutcomeEvaluator` score every plan identically — no AI bonus.

**AI advisory layer**: IBM Granite semantically prioritizes a bounded 50-candidate set, identifying
anomaly-relevant products the deterministic scheduler might deprioritize. A provenance-blind Stage-2
comparison prevents the AI from preferring its own plan. AI recommendations are advisory only.

**Human control layer**: No transmission occurs without explicit operator approval. The operator sees
all evidence, can modify the plan, and retains final authority throughout.

### Technical Differentiation

- 1,284-product candidate screening before AI reasoning (not brute-force LLM over all data)
- Five-plan objective comparison with identical deterministic evaluators
- Stage-2 provenance blinding (OPTION aliases) preventing AI self-preference bias
- Authoritative packet reconstruction on approval — client intent cannot tamper with facts
- Stochastic transmission simulation with ground reception evidence visualization

### Current Evidence Status

The benchmark framework is implemented and methodology is frozen. The official Granite efficacy
evaluation is pending valid IBM Cloud IAM access. AI claims are architectural, not empirical.
