"""GCSI Telecommunications Formulas — BPSK/AWGN Model.

All functions in this module are pure scalar functions.
- No Pydantic models.
- No global mutable state.
- No side effects.
- Inputs and outputs are plain Python floats or ints.

This is a deterministic simulation model.
It is NOT a real spacecraft link-budget tool.
See docs/telecom_model.md for full assumptions and limitations.
"""

import math


# ---------------------------------------------------------------------------
# 1. SNR → Eb/N0
# ---------------------------------------------------------------------------

def snr_to_eb_n0(snr_db: float, bandwidth_hz: float, bit_rate_bps: float) -> float:
    """Convert SNR (dB) to Eb/N0 (dB).

    Formula:
        Eb/N0_dB = SNR_dB + 10 * log10(B / Rb)

    Assumptions:
        - Noise power is measured over the full channel bandwidth B.
        - The signal occupies bit rate Rb within that bandwidth.

    Args:
        snr_db:        Signal-to-noise ratio in dB.
        bandwidth_hz:  Channel bandwidth B in Hz. Must be > 0.
        bit_rate_bps:  Signal bit rate Rb in bits/s. Must be > 0.

    Returns:
        Eb/N0 in dB.

    Raises:
        ValueError: if bandwidth_hz or bit_rate_bps are not strictly positive.
    """
    if bandwidth_hz <= 0.0:
        raise ValueError(f"bandwidth_hz must be > 0; got {bandwidth_hz}")
    if bit_rate_bps <= 0.0:
        raise ValueError(f"bit_rate_bps must be > 0; got {bit_rate_bps}")
    return snr_db + 10.0 * math.log10(bandwidth_hz / bit_rate_bps)


# ---------------------------------------------------------------------------
# 2. BPSK BER over AWGN
# ---------------------------------------------------------------------------

def bpsk_ber(eb_n0_db: float) -> float:
    """Compute bit error rate for BPSK modulation over an AWGN channel.

    Formula:
        BER = 0.5 * erfc(sqrt(10^(Eb/N0_dB / 10)))

    Valid only for BPSK over AWGN.  No fading, no Doppler, no antenna gain.

    Args:
        eb_n0_db: Energy-per-bit to noise-power-spectral-density ratio in dB.

    Returns:
        Bit error rate in [0, 0.5].
    """
    eb_n0_linear = 10.0 ** (eb_n0_db / 10.0)
    return 0.5 * math.erfc(math.sqrt(eb_n0_linear))


# ---------------------------------------------------------------------------
# 3. Packet success probability
# ---------------------------------------------------------------------------

def packet_success_probability(ber: float, size_bits: int) -> float:
    """Probability that an entire packet of size_bits is received without error.

    Formula (log-space to avoid float underflow):
        P_success = exp(size_bits * log1p(-BER))

    This is a PACKET-LEVEL metric. It depends on both BER and packet size.
    It is independent of link goodput (a link-level quantity).

    Boundary cases handled explicitly:
        BER = 0  →  1.0  (perfect channel, every packet succeeds)
        BER = 1  →  0.0  (total noise, no packet succeeds)

    Args:
        ber:       Bit error rate in [0, 1].
        size_bits: Packet size in bits. Must be > 0.

    Returns:
        Packet success probability in [0, 1].

    Raises:
        ValueError: if ber is outside [0, 1] or size_bits is not positive.

    Note:
        Do NOT implement as ``(1 - BER) ** N``.  That expression underflows
        to 0.0 in float64 for large packets at moderate-to-high BER values.
        The log-space form ``exp(N * log1p(-BER))`` is numerically stable.
    """
    if not (0.0 <= ber <= 1.0):
        raise ValueError(f"ber must be in [0, 1]; got {ber}")
    if size_bits <= 0:
        raise ValueError(f"size_bits must be > 0; got {size_bits}")

    # Explicit boundary cases
    if ber == 0.0:
        return 1.0
    if ber == 1.0:
        return 0.0

    # Log-space computation avoids float underflow for large packets
    return math.exp(size_bits * math.log1p(-ber))


# ---------------------------------------------------------------------------
# 4. Link goodput
# ---------------------------------------------------------------------------

def link_goodput(nominal_data_rate_bps: float, protocol_efficiency: float) -> float:
    """Compute effective link throughput after protocol overhead.

    Formula:
        link_goodput_bps = nominal_data_rate_bps * protocol_efficiency

    This is a LINK-LEVEL quantity.
    - It does NOT depend on individual packet size.
    - It does NOT depend on BER.
    - It does NOT depend on packet_success_probability.

    protocol_efficiency accounts for link-layer headers, ACKs, and framing.
    It is a configurable model assumption, not derived from channel measurements.

    Args:
        nominal_data_rate_bps: Channel data rate in bits/s. Must be > 0.
        protocol_efficiency:   Link-layer efficiency in (0, 1]. Must be in (0, 1].

    Returns:
        Effective link goodput in bits/s.

    Raises:
        ValueError: if inputs are out of range.
    """
    if nominal_data_rate_bps <= 0.0:
        raise ValueError(f"nominal_data_rate_bps must be > 0; got {nominal_data_rate_bps}")
    if not (0.0 < protocol_efficiency <= 1.0):
        raise ValueError(f"protocol_efficiency must be in (0, 1]; got {protocol_efficiency}")
    return nominal_data_rate_bps * protocol_efficiency


# ---------------------------------------------------------------------------
# 5. Transmission time
# ---------------------------------------------------------------------------

def transmission_time(size_bits: int, goodput_bps: float) -> float:
    """Compute the expected time to transmit a packet at the link goodput.

    Formula:
        tx_time_s = size_bits / goodput_bps

    Uses link-level goodput, NOT ``nominal_rate * p_success``.

    Args:
        size_bits:   Packet size in bits. Must be > 0.
        goodput_bps: Link goodput in bits/s. Must be > 0.

    Returns:
        Transmission time in seconds.

    Raises:
        ValueError: if inputs are not strictly positive.
    """
    if size_bits <= 0:
        raise ValueError(f"size_bits must be > 0; got {size_bits}")
    if goodput_bps <= 0.0:
        raise ValueError(f"goodput_bps must be > 0; got {goodput_bps}")
    return size_bits / goodput_bps


# ---------------------------------------------------------------------------
# 6. Expected transmission cost
# ---------------------------------------------------------------------------

def expected_transmission_cost(tx_time: float, p_success: float) -> float:
    """Expected total time cost of delivering a packet, accounting for retransmissions.

    Formula:
        expected_cost = tx_time / p_success

    When p_success <= 0 the packet cannot be delivered; cost is infinite.
    Returns math.inf in that case.  Do NOT use an arbitrary probability floor.

    Args:
        tx_time:   Transmission time for one attempt in seconds.
        p_success: Probability that a single attempt succeeds, in [0, 1].

    Returns:
        Expected delivery cost in seconds, or math.inf if p_success <= 0.

    Note:
        Callers (BaselineScheduler, CandidateGenerator) must handle math.inf
        explicitly — packets with zero delivery probability sort last.
    """
    if tx_time < 0.0:
        raise ValueError(f"tx_time must be >= 0; got {tx_time}")
    if p_success <= 0.0:
        return math.inf
    return tx_time / p_success
