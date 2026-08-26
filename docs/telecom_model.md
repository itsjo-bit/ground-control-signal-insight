# GCSI Telecommunications Model

## Purpose

This document describes the deterministic BPSK/AWGN telecommunications model used by GCSI
to convert raw link measurements into actionable link state and packet-level reliability
metrics.

**This is a deterministic simulation model, not a real spacecraft link-budget tool.**
It is designed to support decision-support research and prototyping. All link quality
values are computed from simplified analytical models and must not be used for real
spacecraft operations.

---

## Model Assumptions

| Assumption | Value / Basis |
|---|---|
| Modulation | BPSK (Binary Phase-Shift Keying) |
| Channel model | AWGN (Additive White Gaussian Noise) |
| Fading | None — no multipath, Rayleigh, or Rician fading |
| Doppler | None — no frequency shift modeled |
| Antenna gain | None — not included in SNR→Eb/N0 conversion |
| Forward Error Correction (FEC) | None — no coding gain applied |
| Real channel impairments | None — interference, phase noise, I/Q imbalance not modeled |

---

## Formulas

### 1. SNR to Eb/N0

Converts signal-to-noise ratio to energy-per-bit to noise-power-spectral-density ratio.

```
Eb/N0_dB = SNR_dB + 10 * log10(B / Rb)
```

| Symbol | Meaning | Unit |
|---|---|---|
| `SNR_dB` | Signal-to-noise ratio | dB |
| `B` | Channel bandwidth | Hz |
| `Rb` | Signal bit rate | bits/s |
| `Eb/N0_dB` | Energy per bit / noise PSD | dB |

**Assumption:** noise power is measured over the full channel bandwidth `B`.

**Required inputs:** `snr_db`, `bandwidth_hz`, `bit_rate_bps` — all three are mandatory.
`bandwidth_hz` and `bit_rate_bps` are sourced from `TelecomConfig`, not from raw scenario inputs.

### 2. BPSK BER (AWGN)

Bit error rate for BPSK modulation over an AWGN channel.

```
BER = 0.5 * erfc( sqrt( 10^(Eb/N0_dB / 10) ) )
```

| Symbol | Meaning |
|---|---|
| `erfc(x)` | Complementary error function |
| `Eb/N0_dB` | Energy per bit / noise PSD in dB |

**Valid only for BPSK over AWGN.** Output is in the range [0, 0.5].

Reference value: at Eb/N0 = 10 dB, BER ≈ 3.87 × 10⁻⁶.

### 3. Packet Success Probability

Probability that a packet of `N` bits is received without any bit errors.

```
P_success = exp( N * log1p(-BER) )
```

**Implementation note:** The naive form `(1 - BER)^N` underflows to 0.0 in float64
for large packets at moderate-to-high BER. The log-space form above is numerically
stable across all valid inputs.

Boundary cases:
- `BER = 0` → `P_success = 1.0` (perfect channel)
- `BER = 1` → `P_success = 0.0` (total noise)

**This is a packet-level metric.** It depends on both `BER` and `size_bits`.
It is independent of `link_goodput_bps` (a link-level quantity — see below).

### 4. Link Goodput

Effective link throughput after protocol overhead.

```
link_goodput_bps = nominal_data_rate_bps * protocol_efficiency
```

| Symbol | Meaning | Default |
|---|---|---|
| `nominal_data_rate_bps` | Channel data rate | from scenario input |
| `protocol_efficiency` | Link-layer efficiency factor | 0.9 (configurable) |

`protocol_efficiency` accounts for link-layer headers, ACKs, and framing overhead.
It is a **configurable model assumption**, not derived from channel measurements or BER.

**This is a link-level quantity.** It does NOT depend on:
- Individual packet size
- BER
- Packet success probability

Goodput does NOT change when BER changes. This is intentional and tested.

### 5. Transmission Time

Expected time to transmit one packet at the link goodput rate.

```
tx_time_s = size_bits / link_goodput_bps
```

Uses `link_goodput_bps` (link-level), not `nominal_rate * p_success`.

### 6. Expected Transmission Cost

Expected total delivery time for a packet, accounting for the probability of retransmission.

```
expected_cost_s = tx_time_s / P_success
```

- When `P_success > 0`: represents the expected number of attempts times transmission time.
  Result is in **seconds**, not dimensionless attempt count.
- When `P_success = 0`: the packet cannot be delivered → cost is `math.inf`.

**Do not use an arbitrary floor such as `1e-9`.** Infinity is the correct semantic result
for an undeliverable packet. Callers (scheduler, candidate generator) sort infinite-cost
packets last.

---

## Latency / Timing Concepts

GCSI uses three distinct timing / delay quantities. They are **independent** and must
not be conflated:

| Field | Location | Meaning |
|---|---|---|
| `latency_s` | `LinkState` | Link-layer / protocol-stack latency in seconds. Communication-protocol overhead (headers, ACKs, framing). For deep-space scenarios this is a small value (~1–2 s), **not** the free-space signal travel time. |
| `propagation_delay_s` | `GET /state` response | One-way free-space signal travel time. Formula: `distance_km × 1000 / c`. For a spacecraft 54 million km from Earth this is approximately 180 s. Never stored in `LinkState`. |
| `round_trip_time_s` | `GET /state` response | Propagation RTT only: `2 × propagation_delay_s`. Not an ACK/delivery guarantee. |

**Do NOT** derive `propagation_delay_s` from `latency_s`. They are independent.

**Do NOT** set `latency_s = propagation_delay_s`. For a 54 Mkm spacecraft, `latency_s`
is approximately 1.4 s while `propagation_delay_s` is approximately 180 s.

### Speed-of-light authority

The exact SI speed of light (`299,792,458 m/s`) is defined once in
`backend/app/telecom/geometry.py` as `SPEED_OF_LIGHT_M_S` and imported
everywhere it is needed. Do not define independent copies.

---

## Expected vs. Realized Metrics

GCSI separates two distinct layers of metrics:

### Expected / Analytical (PlanEvaluator)

`PlanEvaluator` computes **expected** performance of a transmission plan using the formulas
above. All computations are deterministic — no random number generation occurs.

- `EvaluationResult.retransmission_overhead` = `Σ (1/p_success - 1) × tx_time_s` per delivered
  packet. Units are **SECONDS**. This is expected extra transmission time, not a
  dimensionless attempt count.
- `EvaluationResult.deadline_misses` = packets where expected delivery time > `deadline_s`
- `EvaluationResult.bandwidth_utilization` = total bits / (goodput × window)

This layer answers: **"What do we expect to happen if we execute this plan?"**

### Realized / Stochastic (TransmissionSimulator)

`TransmissionSimulator` draws **realized** outcomes via Bernoulli trials against
`packet_success_probability` for each packet. Outcomes differ from expected values
due to randomness.

- `SimulationResult.retransmission_counts` = integer counts of realized retransmission attempts
- `SimulationResult.elapsed_time_s` = actual elapsed transmission-attempt time, not expected

These two layers use the same telecom formulas as inputs but are never mixed.
`SimulationResult` must never be passed into `PlanEvaluator`.

This layer answers: **"What actually happened during this simulated transmission?"**

---

## Retransmission Model (Abstract Independent-Attempt)

`TransmissionSimulator` implements an **abstract independent-attempt retransmission model**.

Each packet transmission attempt is a Bernoulli trial with
`p = packet_success_probability(ber, size_bits)`. Failed attempts may be retried
immediately in simulated transmission time within the communication window.

**What the model includes:**
- Bernoulli packet delivery abstraction
- Window-bounded retransmission (packet deferred if window exhausted)
- Protocol overhead through `protocol_efficiency`

**What the model does NOT include:**
- Propagation ACK delay (not modeled)
- Stop-and-wait ARQ (retries are not gated on a deep-space ACK round trip)
- CCSDS protocol stack emulation
- Adaptive coding or modulation based on link quality

**IMPORTANT:** `elapsed_time_s` measures the sum of actual transmission-attempt durations
consumed within the communication window. It does **NOT** include the ~180 s one-way
propagation delay for a 54 Mkm spacecraft. It is **not** end-to-end delivery latency
over deep space.

This is a deliberate model simplification for decision-support research.
See `SimulationResult.simulation_model` for machine-readable metadata.

---

## Risk Score Formula

`PlanEvaluator` computes `risk_score` as a deterministic weighted combination:

```
deadline_miss_rate  = deadline_misses / max(total_packets, 1)

critical_deficit    = 1 - (critical_delivered / max(total_critical, 1))
                    = 0  when there are no critical packets in the plan

window_pressure     = min(expected_consumed_window_s / effective_window_s, 1.0)
                    = 1.0  when effective_window_s == 0

risk_score = clamp(
    w_deadline_miss    * deadline_miss_rate
  + w_critical_deficit * critical_deficit
  + w_window_pressure  * window_pressure,
  0.0, 1.0
)
```

Where:

```
expected_consumed_window_s =
    cumulative expected cost of analytically delivered packets
    = Σ (tx_time / p_success) for delivered packets

effective_window_s =
    min(link_state.remaining_window_s, mission_state.comm_window_remaining_s)
```

Both `PlanEvaluator` and `TransmissionSimulator` use the **same `effective_window_s`
formula** — the smaller of the two window values controls feasibility.

Default weights (all configurable via `RiskWeights`):

| Weight | Default |
|---|---|
| `w_deadline_miss` | 0.40 |
| `w_critical_deficit` | 0.40 |
| `w_window_pressure` | 0.20 |

Risk level thresholds:

| risk_score | risk_level |
|---|---|
| < 0.25 | `LOW` |
| < 0.50 | `MEDIUM` |
| < 0.75 | `HIGH` |
| ≥ 0.75 | `CRITICAL` |

### Retransmission overhead in risk scoring

The `retransmission_overhead` field in `EvaluationResult` is:

```
retransmission_overhead_s = Σ (1/p_success - 1) × tx_time_s
                              over all analytically delivered packets
```

This is the expected **extra transmission time in seconds** compared to a perfect-channel
baseline. It is NOT an attempt count.

---

## What-If Sensitivity Analysis

`POST /plans/what-if` accepts optional `snr_db` and `ber` overrides. Overrides are
handled by `backend/app/telecom/what_if.py` with the following precedence:

| Overrides supplied | Behavior |
|---|---|
| None | Use normal TelecomEngine-derived `LinkState` |
| `snr_db` only | Rebuild with new SNR; Eb/N0 and BER re-derived normally |
| `ber` only | Baseline SNR/Eb/N0 unchanged; only `ber` replaced in `LinkState` |
| `snr_db` + `ber` | SNR applied first (Eb/N0 re-derived); then explicit `ber` replaces derived BER. **Explicit BER has final precedence.** |

**BER validation for what-if input:** Explicit BER must be in [0, 0.5]. Values outside
this range have no physical meaning for the BPSK/AWGN model and are rejected (HTTP 422).

The `WhatIfEvalResponse` includes:
- `what_if_context`: typed provenance record (what was actually applied)
- `hypothetical_link_state`: the exact `LinkState` evaluated
- `evaluations`: plan evaluation results

What-if requests are **non-mutating** — `active_link_state` and `active_scenario` are
never modified.

---

## Distance and SNR Independence

**`distance_km` does NOT determine SNR in GCSI.**

GCSI does not compute:
```
distance → free-space path loss → received power → SNR
```

`SNR` remains a scenario input, not derived from distance. The `distance_km` field
in a scenario is **geometry context only** — used for displaying propagation delay
and RTT in `GET /state` and for AI context. It does not enter any RF calculation.

This is intentional. GCSI isolates:
- Link quality state (SNR, BER, goodput)
- Packet reliability (P_success)
- Communication capacity (window × goodput)

…to study mission-data prioritization decisions in isolation from full RF engineering.
This is a defensible, precisely-defined research abstraction.

---

## Configuration

All model constants are in `GCSIConfig.telecom` (`TelecomConfig`).
Only constants consumed by the current model are present:

| Field | Default | Description |
|---|---|---|
| `modulation` | `"BPSK"` | Modulation scheme (only BPSK supported) |
| `channel_bandwidth_hz` | 1 000 000 Hz | Channel bandwidth B |
| `bit_rate_bps` | 100 000 bps | Signal bit rate Rb |
| `protocol_efficiency` | 0.9 | Link-layer efficiency factor |

Noise figure and frequency band are intentionally absent — not consumed by the current model.

---

## What the Model Does Not Include

The following capabilities are **not implemented** in GCSI's current telecom model.
This is an explicit boundary statement, not an apology:

- Real RF link-budget calculation
- Transmit power / EIRP / path-loss derivation from distance
- Antenna gain (transmit or receive)
- Free-space path loss computed from `distance_km`
- Doppler frequency shift
- Multipath fading (Rayleigh, Rician, or other)
- Coding / Forward Error Correction (FEC) gain
- Adaptive coding and modulation (ACM)
- CCSDS protocol stack
- Explicit ACK/NACK timing
- Propagation-aware ARQ (stop-and-wait or selective-repeat)
- Orbital dynamics
- Real DSN scheduling constraints
- Atmospheric or ionospheric delay
- Relativistic corrections

**GCSI is an intentionally simplified decision-support research abstraction.**
Its purpose is to study mission-data prioritization under communication constraints,
not to emulate a flight-qualified link-budget tool.
