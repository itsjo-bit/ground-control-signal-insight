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

### 5. Transmission Time

Expected time to transmit one packet at the link goodput rate.

```
tx_time_s = size_bits / link_goodput_bps
```

Uses `link_goodput_bps` (link-level), not `nominal_rate * p_success`.

### 6. Expected Transmission Cost

Expected total delivery time for a packet, accounting for the probability of retransmission.

```
expected_cost = tx_time / P_success
```

- When `P_success > 0`: represents the expected number of attempts times transmission time.
- When `P_success = 0`: the packet cannot be delivered → cost is `math.inf`.

**Do not use an arbitrary floor such as `1e-9`.** Infinity is the correct semantic result
for an undeliverable packet. Callers (scheduler, candidate generator) sort infinite-cost
packets last.

---

## Expected vs. Realized Metrics

GCSI separates two distinct layers of metrics:

### Expected / Analytical (PlanEvaluator)

`PlanEvaluator` computes **expected** performance of a transmission plan using the formulas
above. All computations are deterministic — no random number generation occurs.

- `EvaluationResult.retransmission_overhead` = `sum(1/p_success - 1)` per packet (analytical)
- `EvaluationResult.deadline_misses` = packets where expected delivery time > `deadline_s`
- `EvaluationResult.bandwidth_utilization` = total bits / (goodput × window)

This layer answers: **"What do we expect to happen if we execute this plan?"**

### Realized / Stochastic (TransmissionSimulator)

`TransmissionSimulator` draws **realized** outcomes via Bernoulli trials against
`packet_success_probability` for each packet. Outcomes differ from expected values
due to randomness.

- `SimulationResult.retransmission_counts` = integer counts of realized retransmission attempts
- `SimulationResult.elapsed_time_s` = actual elapsed time, not expected

These two layers use the same telecom formulas as inputs but are never mixed.
`SimulationResult` must never be passed into `PlanEvaluator`.

This layer answers: **"What actually happened during this simulated transmission?"**

---

## Risk Score Formula

`PlanEvaluator` computes `risk_score` as a deterministic weighted combination:

```
deadline_miss_rate  = deadline_misses / max(total_packets, 1)
critical_deficit    = 1 - (critical_delivered / max(total_critical, 1))
window_pressure     = 1 - min(comm_window_remaining_s / initial_window_s, 1.0)

risk_score = clamp(
    w_deadline_miss    * deadline_miss_rate
  + w_critical_deficit * critical_deficit
  + w_window_pressure  * window_pressure,
  0.0, 1.0
)
```

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
