# GCSI Demo Capture Checklist

Use this checklist to capture screenshots and video for the competition submission.

**Recommended setup**: 1920×1080, browser zoom 100%, no DevTools open, clean console,
no API keys visible in any panel, no personal file paths visible in URL bar or terminals.

---

## Pre-Capture Setup

- [ ] Fresh browser tab — no cached state from previous sessions
- [ ] Backend started with ASTERIA-7 (default — no `GCSI_SCENARIO_PATH` override needed)
- [ ] `GCSI_AI_PROVIDER=local` set if recording without live Granite/Gemini
  - If recording with Granite: verify provider label shows "Granite" in UI
- [ ] Frontend at `http://localhost:5173`
- [ ] Console clear of uncaught exceptions
- [ ] Workspace mode: Normal (default)

---

## Scene 1 — ASTERIA-7 Mission Overview (T+0s)

**Panel**: Mission State or opening experience

Capture:
- Mission ID: `GCSI-ASTERIA-7`
- Event: `THERMAL PRIORITY CONTACT`
- Risk level: HIGH (0.72)
- Contact countdown: T−13:20 (or similar)
- Uplink margin visible

**Key message**: There's an active thermal anomaly and a short contact window.

---

## Scene 2 — Data Scarcity (T+5s)

**Panel**: Data queue / transmission queue

Capture:
- Total queued: **1,284 products**
- Total volume: **2.74 GB**
- Contact capacity: **~85.7 MB**
- Queue-to-contact ratio: **31.98×**

**Key message**: Only 3.1% of the queued data fits. Something has to be chosen.

---

## Scene 3 — Active Thermal Anomaly (T+10s)

**Panel**: Mission State / Anomaly panel

Capture:
- Anomaly: `ANOM-THERM-017`
- Subsystem: thermal
- Severity: 0.94
- Status: ACTIVE
- Temperature trend visible (if shown)

**Key message**: The anomaly is unresolved. Diagnostic data is somewhere in those 1,284 products.

---

## Scene 4 — Link Health / SNR Declining (T+15s)

**Panel**: Link Health

Capture:
- SNR: 2.8 dB (degraded)
- Link stability: 68%
- BER value
- Goodput: 2.52 Mbps
- Remaining window countdown

**Key message**: The link is degraded. Transmission reliability matters.

---

## Scene 5 — 3D Spacecraft Visualization (T+20s)

**Panel**: Mission Viewport (3D scene)

Capture:
- Earth visible
- Spacecraft visible at distance
- Communication link beam visible
- Distance annotation (182M km) if shown
- Propagation delay annotation (10m 08s) if shown

**Key message**: The spacecraft is 182 million km away. Every bit of the contact window matters.

---

## Scene 6 — Data Products Queue (T+25s)

**Panel**: Data Products / Transmission Queue

Capture:
- Full list of products scrolling
- Anomaly-linked products highlighted
- Criticality scores visible
- Size diversity visible

**Key message**: 1,284 products with different priorities, deadlines, and anomaly relevance.

---

## Scene 7 — AI Candidate Funnel (T+35s)

**Panel**: AI Triage / Decision panel

Capture:
- Total products: 1,284
- Semantic candidates (screened): 50
- AI ranking in progress (or completed)
- Candidate count visible

**Key message**: GCSI screens 1,284 products to 50 relevant candidates before AI reasoning.
The LLM does NOT analyze all 1,284 directly.

---

## Scene 8 — AI Recommendation / Why This Matters (T+45s)

**Panel**: AI Recommendation / Why This Matters

Capture:
- AI recommendation panel (which plan recommended)
- Evidence items bound to authoritative data
- Anomaly-aware reasoning visible
- "Advisory" label visible
- Provider label (Local / Granite / Gemini)

**Key message**: AI recommends a plan based on semantic context. Evidence is bound from
authoritative backend data — not invented.

---

## Scene 9 — Human Decision / Approval (T+55s)

**Panel**: Approval Bar

Capture:
- APPROVE TRANSMISSION button prominent
- Modify Plan and Reject options visible
- Risk level indicator
- Plan comparison available

**Key message**: No transmission happens without explicit human approval.

---

## Scene 10 — Plan Uplink / Contact Acquisition (T+65s)

**Panel**: Transmission / Simulation

Capture:
- Plan uplink animation (command propagation)
- Contact acquisition sequence
- Window countdown

---

## Scene 11 — Packet Transmission Events (T+72s)

**Panel**: Transmission Sequence / Simulation Panel

Capture:
- Packet events streaming (success / failure / retry)
- Elapsed window consumption
- Products delivered counter incrementing

**Key message**: Stochastic simulation with real packet-level outcomes.

---

## Scene 12 — Signal in Transit (T+78s)

**Panel**: Mission Viewport / Signal transit visualization

Capture:
- Signal beam animation towards Earth
- TIME-COMPRESSED VISUALIZATION label visible
- Propagation delay annotation if shown

**Key message**: The signal is traveling 10m 08s to Earth. Reception is not instantaneous.

---

## Scene 13 — Ground Reception (T+83s)

**Panel**: Ground Reception / Evidence Update

Capture:
- Before: information objectives "unknown"
- After: ground objectives now available (Fresh thermal history, Anomaly event timeline, etc.)
- Evidence coverage percentage / HIGH / MEDIUM / LOW

**Key message**: What Earth knows has changed. The right data was transmitted.

---

## Scene 14 — Before/After Evidence State (T+87s)

**Panel**: Ground Reception comparison

Capture:
- Side-by-side or before/after state for key objectives:
  - Fresh thermal history: ✓ received
  - Anomaly event timeline: ✓ received
  - Power correlation: ✓ received

**Key message**: This is why the mission communication mattered.

---

## Video Recording Notes

- Target total runtime: 90 seconds for demo, 3 minutes for extended
- Use 60 fps if possible for smooth animation capture
- Do not record with DevTools or browser extensions showing
- If using Local provider: confirm provider label is visible in UI
- If recording with Granite: provider label must show "Granite"
- Narrate the key moments live or in post-production

---

## Screenshot Filenames (suggested)

```
gcsi-01-mission-overview.png
gcsi-02-data-scarcity.png
gcsi-03-thermal-anomaly.png
gcsi-04-link-health.png
gcsi-05-3d-spacecraft.png
gcsi-06-data-products.png
gcsi-07-ai-funnel.png
gcsi-08-ai-recommendation.png
gcsi-09-human-approval.png
gcsi-10-plan-uplink.png
gcsi-11-packet-events.png
gcsi-12-signal-transit.png
gcsi-13-ground-reception.png
gcsi-14-evidence-before-after.png
```

---

*GCSI Demo Capture Checklist — Phase 5*
