# GCSI Responsible AI

This document describes GCSI's responsible AI design choices.

---

## Core Principle

> The AI understands what the data means.
> The deterministic system determines what can fit.
> The human decides what gets sent.

---

## 1. AI as Advisory, Not Authoritative

GCSI's AI components are explicitly advisory at every layer:

- **Stage 1** produces a semantic ranking. It does not determine physical feasibility.
- **Stage 2** recommends one of five plans. It does not authorize transmission.
- **Neither stage** can override deterministic `PlanEvaluator` or `MissionOutcomeEvaluator` output.
- **Neither stage** can alter packet facts, risk scores, or anomaly linkage.

The word "advisory" is displayed in the UI wherever AI output is shown.

---

## 2. Uncalibrated Confidence

AI confidence values are explicitly typed as `uncalibrated_llm` or `heuristic`.

- LLM confidence is not a calibrated probability
- It is displayed as an indicator, not a reliability guarantee
- The `ConfidenceSemantics` enum prevents the system from treating LLM confidence as a measured quantity
- The UI labels confidence as "advisory" and does not derive critical thresholds from it

---

## 3. Bounded Evidence — AI Cannot Invent Facts

When Stage-2 provides evidence items (citations for its recommendation):
- The AI supplies field labels only (which metrics it is citing)
- The backend replaces AI-supplied values with authoritative deterministic values
- The AI cannot fabricate better metrics to justify its recommendation

Result: all numerical evidence displayed to the operator comes from deterministic backend evaluators.

---

## 4. No Packet Fact Authority

The AI provider receives:
- Product descriptions (text)
- Normalized scores (criticality, mission relevance, scientific value)
- Active anomaly context (severity, subsystem)
- Link conditions (window size, BER, goodput)

The AI provider does NOT receive:
- Raw packet bit sizes
- Actual transmission cost calculations
- BER formulas or Eb/N0 values (for modification)

All packet-level facts are controlled exclusively by the backend.

---

## 5. Human Approval Required

No transmission occurs without explicit operator action:

1. Operator must explicitly click "Analyze" to start AI triage (AI does not run automatically)
2. Operator reviews the AI recommendation with full plan comparison evidence
3. Operator must explicitly click "Approve Transmission" to proceed
4. Operator may modify the plan, reject the AI recommendation, or choose a different plan
5. Any modification creates a custom plan independently verified by the backend

This is not UX friction — it is a deliberate human-in-the-loop trust design.

---

## 6. Provenance-Blind Stage-2 Prevents Automation Bias

The Stage-2 AI provider receives only OPTION-A through OPTION-E aliases — never plan IDs,
strategy names, or `generated_by` fields. This prevents:

- **Self-preference bias**: The AI cannot favor the plan it generated
- **Automation bias**: "AI" branding on a plan cannot inflate its recommendation probability
- **Provenance leakage**: Any strategic identity information is stripped before the AI call

---

## 7. Provider Fallback — Transparent, Not Silent

When an external AI provider (Granite, Gemini, Ollama) is unavailable or returns an invalid
recommendation:

- The system falls back to `LocalRuleBasedProvider` (deterministic rule-based reasoning)
- The response accurately reports `actual_provider: Local`, `requested_provider: [original]`,
  and `recommendation_fallback_reason: [reason]`
- The UI displays a clearly labeled fallback warning
- The operator is never shown a Local result masquerading as Granite

---

## 8. Invalid Recommendation Fail-Closed

If the AI recommendation references a plan ID that does not exist in the current session:
- The recommendation is rejected
- Finalization falls back to `LocalRuleBasedProvider`
- The response is labeled as a fallback with the failure reason

An invalid AI recommendation never silently reaches the operator as valid.

---

## 9. Deterministic Baseline Is Always Available

The four deterministic baseline plans (baseline, deadline-first, mission-critical-first,
value-per-cost) are always generated and always evaluated — independent of AI availability.

If all AI components fail, the operator can still:
- Review the four deterministic plans
- Select the best deterministic option
- Approve and simulate transmission

The system degrades gracefully. No AI requirement for core mission functionality.

---

## 10. Benchmark Scientific Integrity

The benchmark methodology enforces:
- AI plans evaluated by the same deterministic evaluators as classical plans — no AI scoring bonus
- Failed Granite trials recorded as failures — not counted as successes
- No Local fallback results counted as Granite results
- No cherry-picking of favorable results
- Pre-registered methodology frozen before execution

Current status: The official Granite efficacy experiment has not yet been executed.
GCSI does not claim "AI beats baseline" — this claim is not yet supported by evidence.

---

*GCSI Responsible AI — Phase 5*
