# GCSI Competition Pitch Outline

---

## Closing Line

> "GCSI is not trying to make the spacecraft autonomous.
> It is trying to make every minute of communication more informative."

Alternative:

> "When every second of contact counts, GCSI helps the operator send what matters —
> and the operator always makes the call."

---

## 1. Hook (10 seconds)

> "A spacecraft is 182 million kilometers from Earth. It has a thermal anomaly.
> In 13 minutes, there's a 4-minute window. 1,284 products. 3% can fit.
> What do you send?"

---

## 2. The Problem (30 seconds)

Mission operators face an information triage problem that grows with spacecraft data volume.
Simple criticality sorting cannot account for anomaly context, semantic relationships between
products, or multi-objective trade-offs. Manual selection at mission pace is impractical.

---

## 3. The ASTERIA-7 Scenario (45 seconds)

GCSI's canonical demo: ASTERIA-7, thermal anomaly ANOM-THERM-017 (severity 0.94), active and
unresolved. 1,284 products, 2.74 GB queued. Contact window: 4 min 32 s, 85.7 MB capacity.
Queue-to-contact ratio: 31.98×.

The diagnostic data for the thermal anomaly is in the queue. So are 1,276 other products.

---

## 4. The GCSI Solution (60 seconds)

Three cooperating layers:

**Deterministic layer** — screens candidates, evaluates plans, computes risk. Authoritative.

**AI advisory layer** — semantically prioritizes the 50 most relevant candidates, recommends
the best of five competing plans. Advisory only. Provenance-blind Stage-2 prevents self-preference bias.

**Human control layer** — operator approves, modifies, or rejects. No transmission without
explicit human authorization.

---

## 5. Why AI (30 seconds)

Sorting 1,284 products by criticality score cannot tell you that:
- Fresh thermal telemetry from 11 minutes ago is more valuable than a 6-day-old routine scan
- The fault detection and response record should go with the thermal diagnostic, not separately
- The power correlation data resolves an ambiguity in the thermal anomaly attribution

Natural language semantic reasoning — with anomaly context — is what AI adds here.

---

## 6. Why Deterministic Telecom (30 seconds)

The AI does not calculate BER, goodput, or transmission feasibility. These are deterministic
physics. The `PlanEvaluator` is authoritative for what can actually be transmitted. Separating
semantic reasoning (AI) from physical evaluation (deterministic) prevents AI overconfidence.

---

## 7. Human-in-the-Loop (20 seconds)

The operator has situational awareness the model lacks. The AI recommendation is evidence for
a decision — not the decision itself. No transmission happens without explicit approval.

---

## 8. Technical Architecture (30 seconds)

1,284 products → deterministic screening → 50 candidates → AI Stage-1 → ai-prioritized plan
→ 5-plan evaluation under identical metrics → Stage-2 provenance-blind recommendation
→ human approval → authoritative packet reconstruction → transmission simulation → ground reception

---

## 9. Evidence Status (15 seconds)

The benchmark framework is implemented and the methodology is frozen. The official Granite
efficacy evaluation is pending valid provider access. We do not claim empirical superiority —
that experiment has not been run yet.

---

## 10. Close (10 seconds)

> "GCSI is not trying to make the spacecraft autonomous.
> It is trying to make every minute of communication more informative."

---

*GCSI Pitch Outline — Phase 5*
