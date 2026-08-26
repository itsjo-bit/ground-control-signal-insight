# ASTERIA-7 Mission — GCSI Demo Guide

## Overview

ASTERIA-7 is the canonical simulated demonstration mission for GCSI (Ground Control Signal Insight).  
It illustrates the core product positioning:

> **When a spacecraft can't send everything, GCSI helps mission operators decide what Earth needs to hear first.**

This is a **fictional simulated mission**. It is not affiliated with any real space agency or actual spacecraft.

---

## Mission Situation

| Parameter | Value |
|---|---|
| Scenario ID | `asteria7_thermal_priority_contact_v1` |
| Mission ID | `GCSI-ASTERIA-7` |
| Display name | ASTERIA-7 — THERMAL PRIORITY CONTACT |
| Ground station | GCSI-GS-03 (Deep Space Ground Station, fictional) |
| Spacecraft distance | 182,273,814.464 km |
| One-way signal propagation | 608.000 s (10m 08s) |
| Round-trip time | 1,216.000 s (20m 16s) |
| Next high-rate contact in | 800 s (13m 20s) |
| Plan uplink margin | 192 s (03m 12s) |
| Contact duration | 272 s (04m 32s) |
| Mission phase | `pre_contact_anomaly_triage` |
| Risk level | HIGH (0.72) |

### Why it's urgent

The spacecraft has an **active thermal anomaly** (`ANOM-THERM-017`) that requires immediate diagnostic context from ground. The recent short-term temperature trend is +2.8 °C/min (this is the recent rate, not a continuous 11-minute rate). The cause is unresolved.

Simultaneously, the **communication link is degraded** (SNR 2.8 dB ↓, stability 68%).

There is only **04m 32s** of high-rate contact time available before the orbital geometry changes.

---

## Data Scarcity

| Metric | Value |
|---|---|
| Total queued data products | 1,284 |
| Total queued volume | 2.74 GB |
| Contact duration | 272 s |
| Link goodput | 2.52 Mbps (2,800,000 bps × 0.90 efficiency) |
| Raw contact capacity | 85.68 MB |
| Queue-to-contact ratio | 31.98× |
| Fraction that fits | ~3.13% |

Only about 3% of the queued data can fit in one contact window. The operator must decide what matters most.

---

## Product Family Distribution (1,284 products)

| Family | Products | Bytes |
|---|---:|---:|
| Science imagery | 60 | 1,200,000,000 |
| Experiment results | 90 | 720,000,000 |
| Engineering snapshots | 40 | 246,400,000 |
| Routine telemetry | 420 | 210,000,000 |
| Subsystem diagnostics | 180 | 216,000,000 |
| High-rate thermal telemetry | 90 | 54,000,000 |
| Power telemetry | 100 | 40,000,000 |
| Navigation records | 160 | 40,000,000 |
| Fault/event logs | 64 | 9,600,000 |
| Command acknowledgement bundles | 80 | 4,000,000 |
| **TOTAL** | **1,284** | **2,740,000,000** |

---

## Active Anomaly: ANOM-THERM-017

| Field | Value |
|---|---|
| Subsystem | thermal |
| Severity | 0.94 |
| Status | active |
| Detected ~11 minutes ago | (664 s before planning snapshot) |

**Key semantic context for AI reasoning:**
- Power telemetry remains nominal — reducing likelihood of spacecraft-wide power transient
- Cooling-loop flow is available but insufficient at current temperature rise rate
- The cause is **unresolved** — diagnostic data is critical
- Rolling buffer thermal history cannot be reliably reconstructed after overwrite

---

## The 8 Canonical Anchor Products

These are the products that the ASTERIA scenario is tuned to prioritize. Under the current CandidatePrioritizer configuration, all 8 should appear in the 50 selected candidates.

| ID | Subsystem | Size | Deadline | Criticality | Anomaly Link |
|---|---|---:|---:|---:|---|
| TEL-THERM-HR-042 | thermal | 22.00 MB | 90 s | 0.99 | ANOM-THERM-017 |
| DIAG-THERM-EVT-017 | thermal | 11.50 MB | 128 s | 0.98 | ANOM-THERM-017 |
| TEL-PWR-CORR-031 | power | 9.50 MB | 160 s | 0.94 | ANOM-THERM-017 |
| DIAG-COM-LINK-088 | communications | 12.00 MB | 205 s | 0.90 | none |
| NAV-ATT-214 | navigation | 8.00 MB | 230 s | 0.88 | none |
| FDIR-THERM-017 | flight_computer | 3.20 MB | 240 s | 0.97 | ANOM-THERM-017 |
| CMD-THERM-571 | thermal | 2.30 MB | 252 s | 0.96 | ANOM-THERM-017 |
| CAL-THERM-006 | thermal | 14.08 MB | 272 s | 0.92 | ANOM-THERM-017 |
| **Total** | | **82.58 MB** | | | |

**Expected anchor set transmission cost (under current telecom model):** ~271.95 s  
This is intentionally tight — the canonical mission is designed to consume nearly the full contact window.

---

## Candidate Screening

GCSI's `CandidatePrioritizer` deterministically selects a bounded representative set to pass to the AI for semantic reasoning.

| Stage | Count |
|---|---:|
| Total queued products | 1,284 |
| Semantic candidates (passed to AI) | 50 |
| Urgent / operationally relevant (display predicate) | 23 |
| Expected to fit in contact window | ~8 (varies by AI ranking) |

The **urgent/relevant display predicate** (presentation-layer only, not used in AI scoring):
- Product is linked to an applicable active anomaly, **OR**
- `delivery_requirement == "required"`, **OR**
- `deadline_s <= 272.0` (within the contact window)

This predicate is **not** used by the CandidatePrioritizer, PlanEvaluator, or any AI ranking algorithm.

---

## Link Conditions

| Parameter | Value |
|---|---|
| SNR | 2.8 dB (degraded) |
| RSSI | -103.6 dBm |
| Eb/N0 | ~12.8 dB |
| BER | ~3.3447e-10 |
| Nominal data rate | 2,800,000 bps |
| Protocol efficiency | 0.90 |
| Link goodput | 2,520,000 bps |
| Link stability | 0.68 |
| Latency (protocol) | 1.4 s |
| Remaining window | 272.0 s |

**Important**: `latency_s` is link-layer protocol overhead, NOT the free-space propagation delay.  
The one-way propagation delay (608 s) is derived from `distance_km` using the exact speed-of-light formula.

---

## Ground Information Objectives

These are **presentation-layer only** — they are never consumed by the evaluator or AI ranking.  
They drive the "ground information state" visualization after reception.

| Objective | Product IDs |
|---|---|
| Fresh thermal history | TEL-THERM-HR-042 |
| Anomaly event timeline | DIAG-THERM-EVT-017 |
| Power correlation | TEL-PWR-CORR-031 |
| Fault/control context | FDIR-THERM-017, CMD-THERM-571 |
| Sensor interpretation | CAL-THERM-006 |
| Communication context | DIAG-COM-LINK-088 |
| Pointing context | NAV-ATT-214 |

**Evidence coverage thresholds (display-only):**
- `HIGH`: ≥ 80% of required products received
- `MEDIUM`: ≥ 40% and < 80%
- `LOW`: < 40%

---

## Scenario Generator

The ASTERIA-7 scenario is generated deterministically from:

```
tools/generate_asteria7_demo.py
```

Fixed seed: `20240923`

To regenerate:
```bash
python tools/generate_asteria7_demo.py
```

This produces `data/scenarios/asteria7_thermal_priority_contact_v1.json` (committed).

A clean regeneration produces byte-identical output.

---

## Scientific Boundaries

### What GCSI models

- Abstract BPSK/AWGN link model (simplified)
- Packet-level Bernoulli success probability
- Deterministic plan evaluation (expected cost/risk)
- Stochastic transmission simulation
- AI semantic candidate prioritization (Stage 1) + plan recommendation (Stage 2)
- Human-in-the-loop approval with authoritative packet reconstruction

### What GCSI does NOT model

- Real orbital mechanics
- Real RF path-loss or antenna models
- Doppler shift, atmospheric delay, relativistic corrections
- Real ACK/NACK timing (retransmissions are instantaneous in model)
- Real command-uplink simulation
- Persistent ground station database
- Calibrated AI confidence values
- Real propagation in simulator elapsed_time_s (propagation is separate from tx time)

---

## Architecture: Scientific State vs Presentation Metadata

```
AUTHORITATIVE MISSION/SCENARIO FACTS
  └── Scenario JSON (scenario_id, distance_km, link_inputs, mission_state, anomalies, data_products)
      └── Produces: LinkState via TelecomEngine (BER, goodput)
          └── Never changes with presentation choreography

DETERMINISTIC EVALUATOR OUTPUTS
  └── PlanEvaluator → EvaluationResult (risk_score, bandwidth_utilization, etc.)
  └── MissionOutcomeEvaluator → MissionOutcomeResult (anomaly coverage, delivery rates)
      └── Same inputs always produce same outputs

STOCHASTIC SIMULATOR OUTPUTS
  └── TransmissionSimulator → SimulationResult (delivered, failed, deferred)
  └── Seed-deterministic; same seed = same result
  └── attempt_events (additive, Phase 4.2B) — observational only

AI ADVISORY REASONING
  └── Stage 1: CandidatePrioritizer selects ≤50 candidates
  └── Stage 2: Provider ranks candidates semantically
  └── Risk/confidence are bound from authoritative evaluators, not AI
  └── Labelled "advisory" — not verified facts

PRESENTATION CHOREOGRAPHY
  └── data/demo/asteria7_experience.json (sidecar)
  └── Ingest replay animation, SNR/thermal history visualizations
  └── Contact countdown (T−13:20), uplink margin (03:12)
  └── Subsystem status panels, ground information objectives
  └── GET /experience endpoint — read-only, hardcoded sidecar registry
  └── NEVER consumed by PlanEvaluator, CandidatePrioritizer, or benchmark
```

---

## Time-Compression Policy

The GCSI frontend uses **time-compressed visualization** for:

- Recent data ingest replay (5–7 seconds represents minutes of accumulation)
- Plan uplink animation (compressed command propagation visualization)
- Contact acquisition sequence
- Signal-in-transit to Earth (one-way 608 s displayed as compressed animation)

These are labeled clearly in the UI as "TIME-COMPRESSED VISUALIZATION" and "NOT TO SCALE".

The backend `TransmissionSimulator.elapsed_time_s` is the actual simulated transmission window consumption — not compressed.

---

## Simulator Abstraction

`TransmissionSimulator.elapsed_time_s`:
- Sum of actual transmission-attempt durations consumed within the communication window
- **Does NOT include** one-way signal propagation delay (608 s)
- **Does NOT model** real ACK/NACK round-trip timing

`TransmissionAttemptEvent` (Phase 4.2B additive):
- Per-attempt event log for visualization
- `status: "success" | "failure"`
- Purely observational — adding events does not change stochastic behavior
- Same seed → identical delivered/failed/deferred/retransmission_counts/elapsed_time_s

---

## Default Scenario Configuration

As of Phase 4.2, ASTERIA-7 is the **default demo scenario**:

```python
# backend/app/main.py
_DEFAULT_SCENARIO_PATH = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")
```

To use a different scenario:
```bash
GCSI_SCENARIO_PATH=data/scenarios/mission_data_v3.json uvicorn backend.app.main:app
```

The benchmark continues to explicitly reference `mission_data_v3.json` via `gcsi_benchmark_v1.json`.
