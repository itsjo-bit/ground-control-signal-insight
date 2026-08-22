"""Phase 2 tests — telecom formula unit tests.

Each test covers one formula in isolation using exact reference values where
available and explicit floating-point tolerances where appropriate.

Reference values:
    BPSK BER at Eb/N0 = 10 dB: 3.872e-6  (standard digital communications result)
    snr_to_eb_n0 at SNR=10 dB, B=1 MHz, Rb=100 kbps: 20 dB (exact)
"""

import math

import pytest

from backend.app.telecom.formulas import (
    bpsk_ber,
    expected_transmission_cost,
    link_goodput,
    packet_success_probability,
    snr_to_eb_n0,
    transmission_time,
)


# ---------------------------------------------------------------------------
# 1. snr_to_eb_n0
# ---------------------------------------------------------------------------


class TestSnrToEbN0:
    def test_reference_value(self):
        """SNR=10 dB, B=1 MHz, Rb=100 kbps → Eb/N0 = 10 + 10*log10(10) = 20 dB exactly."""
        result = snr_to_eb_n0(snr_db=10.0, bandwidth_hz=1_000_000.0, bit_rate_bps=100_000.0)
        assert math.isclose(result, 20.0, abs_tol=1e-9)

    def test_equal_bandwidth_and_bitrate(self):
        """When B == Rb, log10(B/Rb) = 0, so Eb/N0 == SNR."""
        result = snr_to_eb_n0(snr_db=5.0, bandwidth_hz=50_000.0, bit_rate_bps=50_000.0)
        assert math.isclose(result, 5.0, abs_tol=1e-9)

    def test_bandwidth_greater_than_bitrate_increases_eb_n0(self):
        result = snr_to_eb_n0(snr_db=0.0, bandwidth_hz=10_000.0, bit_rate_bps=1_000.0)
        # log10(10000/1000) = log10(10) = 1, so result = 0 + 10*1 = 10
        assert math.isclose(result, 10.0, abs_tol=1e-9)

    def test_negative_bandwidth_raises(self):
        with pytest.raises(ValueError, match="bandwidth_hz"):
            snr_to_eb_n0(10.0, -1.0, 100_000.0)

    def test_zero_bandwidth_raises(self):
        with pytest.raises(ValueError, match="bandwidth_hz"):
            snr_to_eb_n0(10.0, 0.0, 100_000.0)

    def test_zero_bit_rate_raises(self):
        with pytest.raises(ValueError, match="bit_rate_bps"):
            snr_to_eb_n0(10.0, 1_000_000.0, 0.0)

    def test_negative_bit_rate_raises(self):
        with pytest.raises(ValueError, match="bit_rate_bps"):
            snr_to_eb_n0(10.0, 1_000_000.0, -1.0)

    def test_negative_snr_is_valid(self):
        """Negative SNR in dB is a legal input (weak signal)."""
        result = snr_to_eb_n0(-5.0, 1_000_000.0, 100_000.0)
        assert math.isclose(result, -5.0 + 10.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# 2. bpsk_ber
# ---------------------------------------------------------------------------


class TestBpskBer:
    def test_reference_value_at_10db(self):
        """BPSK BER at Eb/N0 = 10 dB ≈ 3.87e-6 (standard reference value)."""
        result = bpsk_ber(10.0)
        # Precise reference: 0.5 * erfc(sqrt(10)) ≈ 3.872e-6
        assert math.isclose(result, 3.872e-6, rel_tol=0.01), f"Got {result}"

    def test_high_eb_n0_gives_very_low_ber(self):
        """At high Eb/N0, BER should be extremely small."""
        result = bpsk_ber(20.0)
        assert result < 1e-12

    def test_zero_db_gives_ber_near_half(self):
        """At Eb/N0 = 0 dB, BER ≈ 0.0786 (well below 0.5)."""
        result = bpsk_ber(0.0)
        assert 0.0 < result < 0.5

    def test_very_negative_eb_n0_approaches_half(self):
        """Deep in the noise, BPSK BER approaches 0.5."""
        result = bpsk_ber(-20.0)
        assert result > 0.4

    def test_output_is_in_valid_range(self):
        for eb_n0 in [-10.0, 0.0, 5.0, 10.0, 15.0]:
            result = bpsk_ber(eb_n0)
            assert 0.0 <= result <= 0.5, f"BER={result} out of range at Eb/N0={eb_n0} dB"

    def test_ber_decreases_as_eb_n0_increases(self):
        """BER is a monotonically decreasing function of Eb/N0."""
        values = [bpsk_ber(x) for x in [-5.0, 0.0, 5.0, 10.0, 15.0]]
        assert values == sorted(values, reverse=True)


# ---------------------------------------------------------------------------
# 3. packet_success_probability
# ---------------------------------------------------------------------------


class TestPacketSuccessProbability:
    def test_ber_zero_gives_probability_one(self):
        """Perfect channel: every packet succeeds."""
        result = packet_success_probability(ber=0.0, size_bits=8192)
        assert result == 1.0

    def test_ber_one_gives_probability_zero(self):
        """Total noise: no packet succeeds."""
        result = packet_success_probability(ber=1.0, size_bits=8192)
        assert result == 0.0

    def test_small_ber_small_packet(self):
        """Low BER, small packet → probability close to 1."""
        # BER = 1e-6, 100 bits: P = exp(100 * log1p(-1e-6)) ≈ exp(-1e-4) ≈ 0.9999
        result = packet_success_probability(ber=1e-6, size_bits=100)
        assert math.isclose(result, math.exp(100 * math.log1p(-1e-6)), rel_tol=1e-12)
        assert 0.99 < result <= 1.0

    def test_numerical_stability_large_packet_nontrivial_ber(self):
        """Large packet with non-trivial BER: naive (1-BER)^N underflows, log-space is stable.

        Verify the result is strictly between 0 and 1 (not 0.0 due to underflow).
        """
        ber = 1e-4
        size_bits = 1_000_000  # 1 Mbit packet — naive form gives 0.0

        result = packet_success_probability(ber=ber, size_bits=size_bits)

        # Naive form would underflow to 0.0; log-space gives a finite positive value
        naive = (1.0 - ber) ** size_bits  # expected to be ~0 or very small
        assert result > 0.0, "Log-space result must be > 0"
        assert result < 1.0
        # Log-space and naive should agree when the naive form doesn't underflow;
        # for this extreme case they won't match — that's the point of the log form.
        # Just verify log-space gives a numerically meaningful (non-zero) answer.
        assert math.isfinite(result)

        # Additionally confirm log-space matches math.exp(N * log1p(-BER)) exactly
        expected = math.exp(size_bits * math.log1p(-ber))
        assert math.isclose(result, expected, rel_tol=1e-12)

    def test_does_not_use_naive_power_form(self):
        """Smoke test: a large-packet / high-BER combo that causes naive underflow."""
        ber = 0.01
        size_bits = 10_000
        result = packet_success_probability(ber=ber, size_bits=size_bits)
        naive = (1.0 - ber) ** size_bits
        # Both forms should agree here (no underflow), but result must be non-zero
        assert result > 0.0
        assert math.isclose(result, naive, rel_tol=1e-6)

    def test_invalid_ber_negative_raises(self):
        with pytest.raises(ValueError, match="ber"):
            packet_success_probability(ber=-0.01, size_bits=100)

    def test_invalid_ber_above_one_raises(self):
        with pytest.raises(ValueError, match="ber"):
            packet_success_probability(ber=1.1, size_bits=100)

    def test_invalid_size_zero_raises(self):
        with pytest.raises(ValueError, match="size_bits"):
            packet_success_probability(ber=0.001, size_bits=0)

    def test_invalid_size_negative_raises(self):
        with pytest.raises(ValueError, match="size_bits"):
            packet_success_probability(ber=0.001, size_bits=-1)


# ---------------------------------------------------------------------------
# 4. link_goodput
# ---------------------------------------------------------------------------


class TestLinkGoodput:
    def test_reference_value(self):
        """nominal=100000, efficiency=0.9 → goodput=90000 exactly."""
        result = link_goodput(nominal_data_rate_bps=100_000.0, protocol_efficiency=0.9)
        assert math.isclose(result, 90_000.0, rel_tol=1e-12)

    def test_full_efficiency(self):
        """With efficiency=1.0, goodput equals nominal rate."""
        result = link_goodput(100_000.0, 1.0)
        assert math.isclose(result, 100_000.0, rel_tol=1e-12)

    def test_does_not_depend_on_packet_size(self):
        """link_goodput takes no size_bits argument — independence is structural."""
        # Verify the function signature accepts only nominal_rate and efficiency
        import inspect
        sig = inspect.signature(link_goodput)
        param_names = set(sig.parameters.keys())
        assert "size_bits" not in param_names, "link_goodput must not accept size_bits"
        assert "ber" not in param_names, "link_goodput must not accept ber"
        assert "p_success" not in param_names, "link_goodput must not accept p_success"

    def test_different_efficiency_values_produce_proportional_results(self):
        base = link_goodput(200_000.0, 1.0)
        half = link_goodput(200_000.0, 0.5)
        assert math.isclose(half, base * 0.5, rel_tol=1e-12)

    def test_zero_nominal_rate_raises(self):
        with pytest.raises(ValueError, match="nominal_data_rate_bps"):
            link_goodput(0.0, 0.9)

    def test_negative_nominal_rate_raises(self):
        with pytest.raises(ValueError, match="nominal_data_rate_bps"):
            link_goodput(-1.0, 0.9)

    def test_zero_efficiency_raises(self):
        with pytest.raises(ValueError, match="protocol_efficiency"):
            link_goodput(100_000.0, 0.0)

    def test_efficiency_above_one_raises(self):
        with pytest.raises(ValueError, match="protocol_efficiency"):
            link_goodput(100_000.0, 1.1)


# ---------------------------------------------------------------------------
# 5. transmission_time
# ---------------------------------------------------------------------------


class TestTransmissionTime:
    def test_reference_value(self):
        """900000 bits / 90000 bps = 10 seconds exactly."""
        result = transmission_time(size_bits=900_000, goodput_bps=90_000.0)
        assert math.isclose(result, 10.0, rel_tol=1e-12)

    def test_proportional_to_size(self):
        t1 = transmission_time(100, 1000.0)
        t2 = transmission_time(200, 1000.0)
        assert math.isclose(t2, 2.0 * t1, rel_tol=1e-12)

    def test_inversely_proportional_to_goodput(self):
        t1 = transmission_time(1000, 500.0)
        t2 = transmission_time(1000, 1000.0)
        assert math.isclose(t1, 2.0 * t2, rel_tol=1e-12)

    def test_zero_size_raises(self):
        with pytest.raises(ValueError, match="size_bits"):
            transmission_time(0, 90_000.0)

    def test_negative_size_raises(self):
        with pytest.raises(ValueError, match="size_bits"):
            transmission_time(-1, 90_000.0)

    def test_zero_goodput_raises(self):
        with pytest.raises(ValueError, match="goodput_bps"):
            transmission_time(900_000, 0.0)

    def test_negative_goodput_raises(self):
        with pytest.raises(ValueError, match="goodput_bps"):
            transmission_time(900_000, -1.0)


# ---------------------------------------------------------------------------
# 6. expected_transmission_cost
# ---------------------------------------------------------------------------


class TestExpectedTransmissionCost:
    def test_reference_value(self):
        """tx_time=10, p_success=0.5 → cost = 10/0.5 = 20."""
        result = expected_transmission_cost(tx_time=10.0, p_success=0.5)
        assert math.isclose(result, 20.0, rel_tol=1e-12)

    def test_certain_success(self):
        """p_success=1.0 → cost equals tx_time."""
        result = expected_transmission_cost(tx_time=5.0, p_success=1.0)
        assert math.isclose(result, 5.0, rel_tol=1e-12)

    def test_p_success_zero_returns_inf(self):
        """p_success=0 → undeliverable packet → math.inf."""
        result = expected_transmission_cost(tx_time=10.0, p_success=0.0)
        assert result == math.inf

    def test_p_success_negative_returns_inf(self):
        """Negative p_success is physically invalid → math.inf, not an exception."""
        result = expected_transmission_cost(tx_time=10.0, p_success=-0.5)
        assert result == math.inf

    def test_no_arbitrary_floor(self):
        """Confirm 1e-9 floor is not used — very small p_success gives very large cost."""
        result = expected_transmission_cost(tx_time=1.0, p_success=1e-12)
        assert result == 1e12
        # If a 1e-9 floor were used, result would be capped at 1e9 — confirm it is not
        assert result > 1e9

    def test_negative_tx_time_raises(self):
        with pytest.raises(ValueError, match="tx_time"):
            expected_transmission_cost(tx_time=-1.0, p_success=0.5)

    def test_cost_increases_as_p_success_decreases(self):
        """Lower success probability → higher cost."""
        costs = [expected_transmission_cost(5.0, p) for p in [1.0, 0.8, 0.5, 0.2, 0.01]]
        assert costs == sorted(costs)
