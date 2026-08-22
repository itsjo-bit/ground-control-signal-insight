"""Phase 2 tests — TelecomEngine integration tests.

Tests verify that TelecomEngine.compute() correctly:
- Accepts the required raw_inputs dict.
- Derives Eb/N0, BER, and link_goodput_bps from config constants.
- Returns a fully populated LinkState.
- Rejects incomplete or invalid inputs.
"""

import math
from datetime import datetime, timezone

import pytest

from backend.app.config import GCSIConfig, TelecomConfig
from backend.app.models.link_state import LinkState
from backend.app.telecom.engine import TelecomEngine
from backend.app.telecom.formulas import snr_to_eb_n0, bpsk_ber, link_goodput


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)

# A representative raw input fixture matching a moderate-quality downlink pass
NOMINAL_RAW_INPUTS = {
    "timestamp": NOW,
    "snr_db": 10.0,
    "rssi_dbm": -85.0,
    "nominal_data_rate_bps": 100_000.0,
    "latency_s": 0.25,
    "link_stability": 0.9,
    "remaining_window_s": 300.0,
}

# Config matching the fixture: B=1 MHz, Rb=100 kbps, efficiency=0.9 → known reference values
REFERENCE_CONFIG = GCSIConfig()  # defaults: B=1e6, Rb=1e5, η=0.9


# ---------------------------------------------------------------------------
# Engine construction
# ---------------------------------------------------------------------------


class TestTelecomEngineConstruction:
    def test_uses_default_config_when_none_provided(self):
        engine = TelecomEngine()
        assert engine._config is not None

    def test_accepts_explicit_config(self):
        cfg = GCSIConfig()
        engine = TelecomEngine(config=cfg)
        assert engine._config is cfg


# ---------------------------------------------------------------------------
# compute() — correct derived fields
# ---------------------------------------------------------------------------


class TestTelecomEngineCompute:
    def test_returns_link_state_instance(self):
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        result = engine.compute(NOMINAL_RAW_INPUTS)
        assert isinstance(result, LinkState)

    def test_raw_fields_passed_through_unchanged(self):
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        ls = engine.compute(NOMINAL_RAW_INPUTS)
        assert ls.timestamp == NOW
        assert ls.snr_db == 10.0
        assert ls.rssi_dbm == -85.0
        assert ls.nominal_data_rate_bps == 100_000.0
        assert ls.latency_s == 0.25
        assert ls.link_stability == 0.9
        assert ls.remaining_window_s == 300.0

    def test_eb_n0_is_correctly_derived(self):
        """With SNR=10 dB, B=1 MHz, Rb=100 kbps → Eb/N0 = 20 dB."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        ls = engine.compute(NOMINAL_RAW_INPUTS)
        expected = snr_to_eb_n0(10.0, 1_000_000.0, 100_000.0)  # = 20.0 dB
        assert math.isclose(ls.eb_n0_db, expected, abs_tol=1e-9)
        assert math.isclose(ls.eb_n0_db, 20.0, abs_tol=1e-9)

    def test_ber_is_correctly_derived(self):
        """BER is derived from eb_n0_db = 20 dB → very small value."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        ls = engine.compute(NOMINAL_RAW_INPUTS)
        expected = bpsk_ber(20.0)
        assert math.isclose(ls.ber, expected, rel_tol=1e-9)
        assert ls.ber < 1e-10  # at 20 dB Eb/N0, BER is negligible

    def test_link_goodput_equals_nominal_times_efficiency(self):
        """link_goodput_bps = nominal_data_rate_bps * protocol_efficiency = 90000."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        ls = engine.compute(NOMINAL_RAW_INPUTS)
        expected = link_goodput(100_000.0, 0.9)  # = 90000
        assert math.isclose(ls.link_goodput_bps, expected, rel_tol=1e-12)
        assert math.isclose(ls.link_goodput_bps, 90_000.0, rel_tol=1e-12)

    def test_link_goodput_uses_config_not_ber(self):
        """Changing SNR (and thus BER) must not affect link_goodput_bps."""
        cfg = REFERENCE_CONFIG
        engine = TelecomEngine(config=cfg)
        inputs_good = {**NOMINAL_RAW_INPUTS, "snr_db": 20.0}   # low BER
        inputs_poor = {**NOMINAL_RAW_INPUTS, "snr_db": -5.0}   # high BER
        ls_good = engine.compute(inputs_good)
        ls_poor = engine.compute(inputs_poor)
        # Goodput depends only on nominal_rate and protocol_efficiency; BER must not affect it
        assert math.isclose(ls_good.link_goodput_bps, ls_poor.link_goodput_bps, rel_tol=1e-12)

    def test_different_protocol_efficiency_changes_goodput(self):
        """Changing protocol_efficiency in config changes link_goodput_bps."""
        from pydantic_settings import BaseSettings
        cfg_90 = GCSIConfig()   # default efficiency = 0.9
        cfg_80 = GCSIConfig()
        # Override via direct attribute (TelecomConfig is a BaseSettings — rebuild for test)
        cfg_80.telecom = TelecomConfig(protocol_efficiency=0.8)
        engine_90 = TelecomEngine(config=cfg_90)
        engine_80 = TelecomEngine(config=cfg_80)
        ls_90 = engine_90.compute(NOMINAL_RAW_INPUTS)
        ls_80 = engine_80.compute(NOMINAL_RAW_INPUTS)
        assert math.isclose(ls_90.link_goodput_bps, 90_000.0, rel_tol=1e-12)
        assert math.isclose(ls_80.link_goodput_bps, 80_000.0, rel_tol=1e-12)

    def test_all_link_state_fields_populated(self):
        """Every field of LinkState must be set (no None or zero-default surprises)."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        ls = engine.compute(NOMINAL_RAW_INPUTS)
        assert ls.timestamp is not None
        assert math.isfinite(ls.snr_db)
        assert math.isfinite(ls.eb_n0_db)
        assert 0.0 <= ls.ber <= 1.0
        assert math.isfinite(ls.rssi_dbm)
        assert ls.nominal_data_rate_bps > 0
        assert ls.link_goodput_bps > 0
        assert ls.latency_s >= 0
        assert 0.0 <= ls.link_stability <= 1.0
        assert ls.remaining_window_s >= 0


# ---------------------------------------------------------------------------
# compute() — error handling
# ---------------------------------------------------------------------------


class TestTelecomEngineErrors:
    def test_missing_required_key_raises_key_error(self):
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        incomplete = {k: v for k, v in NOMINAL_RAW_INPUTS.items() if k != "snr_db"}
        with pytest.raises(KeyError, match="snr_db"):
            engine.compute(incomplete)

    def test_all_required_keys_are_checked(self):
        """Each required key, when removed, must raise KeyError."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        for key in TelecomEngine.REQUIRED_INPUTS:
            incomplete = {k: v for k, v in NOMINAL_RAW_INPUTS.items() if k != key}
            with pytest.raises(KeyError):
                engine.compute(incomplete)

    def test_extra_keys_in_raw_inputs_are_ignored(self):
        """Engine ignores unknown keys — forward-compatible with richer scenario files."""
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        inputs_with_extra = {**NOMINAL_RAW_INPUTS, "orbit_altitude_km": 400.0}
        result = engine.compute(inputs_with_extra)
        assert isinstance(result, LinkState)

    def test_invalid_link_stability_propagates_validation_error(self):
        """link_stability > 1.0 is rejected by LinkState Pydantic validation."""
        from pydantic import ValidationError
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        bad_inputs = {**NOMINAL_RAW_INPUTS, "link_stability": 1.5}
        with pytest.raises(ValidationError):
            engine.compute(bad_inputs)

    def test_negative_remaining_window_propagates_validation_error(self):
        from pydantic import ValidationError
        engine = TelecomEngine(config=REFERENCE_CONFIG)
        bad_inputs = {**NOMINAL_RAW_INPUTS, "remaining_window_s": -10.0}
        with pytest.raises(ValidationError):
            engine.compute(bad_inputs)
