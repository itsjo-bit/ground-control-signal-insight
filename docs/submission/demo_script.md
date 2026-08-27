# GCSI Demo Script

---

## 90-Second Reliable Offline Demo — Local Provider

> **Narration note**: This script is designed for reliable offline demonstration
> using GCSI's Local deterministic provider. No external API key is required.
> Granite-specific sentence variants are given at the end of this document.

---

### 0–15 seconds: Problem and ASTERIA Context

> "ASTERIA-7 is deep in space — 182 million kilometers from Earth.
> It has just detected an active thermal anomaly. The cause is unknown.
> In 13 minutes, there's a communication window: 4 minutes 32 seconds of contact.
> The question is: what does Earth need to hear first?"

*[Show: Mission State panel — mission ID, risk level HIGH, thermal anomaly ACTIVE, contact countdown]*

---

### 15–25 seconds: The Scale of the Problem

> "ASTERIA-7 has 1,284 products queued — 2.74 gigabytes.
> The contact window can carry about 85 megabytes. That's 3.1% of the queue.
> Someone has to decide what goes."

*[Show: Data queue with product count, volume, contact capacity]*

---

### 25–40 seconds: Deterministic Screening + Prioritization

> "GCSI screens all 1,284 products deterministically — anomaly-linked first,
> then critical, deadline-urgent, high-relevance — down to 50 mission-relevant candidates.
> For this offline demonstration, GCSI's Local deterministic provider ranks those
> 50 candidates using the same mission-context pipeline.
> The same bounded candidate interface supports IBM Granite when valid external
> access is configured."

*[Show: Decision panel — 1,284 → 50 → ranked candidates, anomaly products highlighted]*

---

### 40–55 seconds: Why This Matters

> "The recommended plan competes against four deterministic alternatives under
> identical evaluation metrics. For external LLM providers, plan provenance is
> blinded behind OPTION aliases so the model cannot identify which option it may
> have generated. That's an honest comparison."

*[Show: Plan comparison panel — 5 plans, metrics, recommendation with evidence]*

---

### 55–65 seconds: Human Decision

> "The operator sees the evidence. They can accept the recommendation, modify it,
> or reject it entirely. No transmission happens automatically."

*[Show: Approval bar — APPROVE TRANSMISSION button, plan summary, risk level]*

*[Click: Approve]*

---

### 65–80 seconds: Transmission

> "The plan is sent. 272 seconds of contact. Products streaming to Earth —
> successful, deferred, some retransmitted. The signal travels at the speed of light —
> 10 minutes 8 seconds to reach the ground station."

*[Show: Transmission sequence — packet events, elapsed window, signal in transit animation]*

---

### 80–90 seconds: Ground Reception

> "Earth receives the data. The thermal anomaly event timeline is now available
> on the ground. Ground operators now have the diagnostic data they needed.
> GCSI didn't fix the spacecraft — but it made sure the right data got through first."

*[Show: Ground Reception panel — evidence objectives updated, BEFORE → AFTER]*

---

## 90-Second Granite Demo — External AI Provider

> **Narration note**: Use this variant when demonstrating with IBM Granite configured.
> Replace the paragraph in section 25–40 seconds and section 40–55 seconds as shown.
> All other sections remain identical.

### 25–40 seconds (Granite variant):

> "GCSI screens all 1,284 products deterministically — anomaly-linked first,
> then critical, deadline-urgent, high-relevance. 50 candidates go to IBM Granite
> for semantic reasoning. The model understands that fresh thermal history from
> the last 11 minutes is more valuable than a routine scan from 6 days ago."

*[Show: AI Triage panel — 1,284 → 50 → ranked candidates, anomaly products highlighted]*

### 40–55 seconds (Granite variant):

> "The AI recommends a plan. That plan competes against four deterministic
> alternatives under identical evaluation metrics. Plan provenance is blinded
> behind OPTION aliases — the model cannot see which option it generated.
> That's an honest comparison."

---

## 3-Minute Demo

### 0–20 seconds: Problem and Mission Setup (same as 90-second version)

---

### 20–40 seconds: Manual Mode First

> "Let's start with manual mode. The operator can browse all 1,284 products,
> filter by subsystem or anomaly link, and manually select what to transmit."

*[Show: Data Products panel — filter by THERMAL, show anomaly-linked products]*

*[Select: TEL-THERM-HR-042 (22 MB thermal telemetry)]*

> "The deterministic evaluator immediately shows: feasibility check, risk score,
> window usage. No AI needed for the basics."

*[Show: Manual assessment result]*

---

### 40–80 seconds: AI-Assisted Mode (Local or Granite depending on configuration)

> "Now let's see what the prioritization pipeline adds. Click Analyze."

*[Click: Analyze]*

> "The backend screens 1,284 products to 50 candidates before the provider call.
> The provider receives product descriptions, the active thermal anomaly context,
> and link conditions."

*[Show: Lifecycle — STANDBY → ANALYZING → READY]*

> "Five plans are generated. The prioritized plan is evaluated by the same
> deterministic evaluators as the four classical baselines. No provider bonus."

*[Show: Plan comparison — mission_value, anomaly coverage, risk_score for all 5 plans]*

---

### 80–110 seconds: Trust Architecture

> "Stage 2 uses provenance-blind comparison. For external LLM providers, the model
> sees OPTION-A through OPTION-E — it cannot tell which option it generated.
> This prevents the model from simply recommending its own output."

*[Show: Recommendation panel — recommendation with evidence, advisory label]*

> "The evidence values are bound from the deterministic evaluator — not from the AI.
> The provider cites which metrics matter; the backend supplies the actual numbers."

---

### 110–150 seconds: Approval and Transmission (same as 90-second version)

---

### 150–180 seconds: Ground Reception + Architecture

> "After transmission, Earth receives the diagnostic data. The anomaly event timeline,
> thermal history, and power correlation are now available to the ground team."

*[Show: Ground Reception — before/after evidence state]*

> "GCSI is not trying to make the spacecraft autonomous.
> It is trying to make every minute of communication more informative."

*[Optional: show Architecture diagram or project structure briefly]*

---

## Provider Notes for Recording

- **Recording with Local provider**: UI shows "Deterministic Mission Triage" and
  "LOCAL" badge in the decision chain. Use Local narration above. This is honest.
- **Recording with Granite**: UI shows "AI Prioritization" and "AI" badge.
  Use Granite narration variant above. Provider label must show "Granite".
- **If Granite is rate-limited during recording**: Use Local provider and use
  Local narration. Do not use Local and claim the result was generated by Granite.
- **Never imply benchmark success**: Do not say "Granite is better" or
  "AI improves outcome" unless the official benchmark has been run and supports the claim.

---

*GCSI Demo Script — Phase 5.1*
