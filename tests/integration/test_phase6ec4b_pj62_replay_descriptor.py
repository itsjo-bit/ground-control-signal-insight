"""GCSI Phase 6E-C4B — PJ62 Replay Descriptor Offline Integration Test.

This test is COMPLETELY OFFLINE.

A network guard blocks all socket access.  No live requests are made to
NASA, JPL, PDS, or any other external service.

Scope
-----
1. Load the committed PJ62 replay descriptor.
2. Load the three verified snapshot artifacts (Horizons, IRDR, GRDR)
   using their production stores.
3. Verify that the descriptor references exactly those three artifacts.
4. Verify that Horizons epoch == IRDR stop == GRDR stop (temporal alignment).
5. Verify the authoritative range_km is correct.
6. Verify the descriptor does NOT store a redundant distance_km.
7. Verify modeled latency_s = 1.5 and that it is NOT the Horizons light time.
8. Feasibility: IRDR fits, GRDR fits, IRDR+GRDR together do NOT fit.
"""

from __future__ import annotations

import json
import math
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_ROOT))

_DESCRIPTOR_PATH = _ROOT / "data" / "replays" / "juno_pj62_mwr_v1.json"
_HORIZONS_SNAPSHOT_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "horizons"
    / "juno"
    / "juno_spk_-61_2024-06-14T035955.483000Z.json"
)
_IRDR_SNAPSHOT_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "pds_archive"
    / "juno_mwr"
    / "pj62"
    / "mwr62ri2024166030000_r04112_v04_3.0.json"
)
_GRDR_SNAPSHOT_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "pds_archive"
    / "juno_mwr"
    / "pj62"
    / "mwr62rg2024166030000_r04112_v04_3.0.json"
)

from backend.app.mission_sources.replay_descriptor import (
    load_historical_replay_descriptor,
    replay_risk_level_from_score,
)
from backend.app.mission_sources.snapshots.horizons_snapshot import HorizonsSnapshotStore
from backend.app.mission_sources.snapshots.pds_archive_snapshot import PdsArchiveSnapshotStore
from backend.app.telecom.formulas import (
    bpsk_ber,
    expected_transmission_cost,
    link_goodput,
    packet_success_probability,
    snr_to_eb_n0,
    transmission_time,
)

# ---------------------------------------------------------------------------
# Frozen expected values (from the authoritative snapshots / C4A capture)
# ---------------------------------------------------------------------------

_DECISION_EPOCH = datetime(2024, 6, 14, 3, 59, 55, 483000, tzinfo=timezone.utc)
_EXPECTED_RANGE_KM: float = 893345396.8038701
_EXPECTED_ONE_WAY_LIGHT_TIME_S: float = 2979.879489843171

# Modeled values (frozen in C4B)
_MODELED_LATENCY_S: float = 1.5
_MODELED_WINDOW_S: float = 900.0
_MODELED_SNR_DB: float = 3.0
_MODELED_RISK_SCORE: float = 0.35

# GCSI default telecom constants (from TelecomConfig defaults)
_CHANNEL_BANDWIDTH_HZ: float = 1_000_000.0
_BIT_RATE_BPS: float = 100_000.0
_PROTOCOL_EFFICIENCY: float = 0.9

# Authoritative product sizes (in bytes, from PDS XML labels)
_IRDR_SIZE_BYTES: int = 6_694_664
_GRDR_SIZE_BYTES: int = 5_093_997

# Derived sizes in bits
_IRDR_SIZE_BITS: int = _IRDR_SIZE_BYTES * 8
_GRDR_SIZE_BITS: int = _GRDR_SIZE_BYTES * 8


# ---------------------------------------------------------------------------
# Zero-network guard
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError(
        "GCSI offline test guard: network access is forbidden in this test."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Fixtures: load once per test session (cached within module scope)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def descriptor():
    return load_historical_replay_descriptor(_DESCRIPTOR_PATH)


@pytest.fixture(scope="module")
def horizons_result():
    return HorizonsSnapshotStore.load(_HORIZONS_SNAPSHOT_PATH)


@pytest.fixture(scope="module")
def irdr_result():
    product, provenance = PdsArchiveSnapshotStore.load(_IRDR_SNAPSHOT_PATH)
    return product, provenance


@pytest.fixture(scope="module")
def grdr_result():
    product, provenance = PdsArchiveSnapshotStore.load(_GRDR_SNAPSHOT_PATH)
    return product, provenance


# ---------------------------------------------------------------------------
# Part 1: Descriptor references exactly the three frozen artifacts
# ---------------------------------------------------------------------------


class TestDescriptorSnapshotReferences:
    """Descriptor paths reference exactly the three verified artifacts."""

    def test_horizons_path_matches_artifact(self, descriptor):
        expected = (
            "data/verified_snapshots/horizons/juno/"
            "juno_spk_-61_2024-06-14T035955.483000Z.json"
        )
        assert descriptor.horizons_snapshot_path == expected

    def test_irdr_path_matches_artifact(self, descriptor):
        expected = (
            "data/verified_snapshots/pds_archive/juno_mwr/pj62/"
            "mwr62ri2024166030000_r04112_v04_3.0.json"
        )
        assert descriptor.irdr_snapshot_path == expected

    def test_grdr_path_matches_artifact(self, descriptor):
        expected = (
            "data/verified_snapshots/pds_archive/juno_mwr/pj62/"
            "mwr62rg2024166030000_r04112_v04_3.0.json"
        )
        assert descriptor.grdr_snapshot_path == expected

    def test_descriptor_references_existing_horizons_file(self, descriptor):
        p = _ROOT / descriptor.horizons_snapshot_path
        assert p.exists()

    def test_descriptor_references_existing_irdr_file(self, descriptor):
        p = _ROOT / descriptor.irdr_snapshot_path
        assert p.exists()

    def test_descriptor_references_existing_grdr_file(self, descriptor):
        p = _ROOT / descriptor.grdr_snapshot_path
        assert p.exists()


# ---------------------------------------------------------------------------
# Part 2: Temporal alignment
# ---------------------------------------------------------------------------


class TestTemporalAlignment:
    """Horizons epoch == IRDR stop == GRDR stop == decision epoch."""

    def test_horizons_epoch_equals_decision_epoch(self, horizons_result):
        assert horizons_result.geometry.epoch_utc == _DECISION_EPOCH

    def test_irdr_stop_equals_decision_epoch(self, irdr_result):
        product, _ = irdr_result
        stop_str = product.observation_stop_utc
        # Handle both "Z" suffix and "+00:00"
        if isinstance(stop_str, str):
            irdr_stop = datetime.fromisoformat(stop_str.replace("Z", "+00:00"))
        else:
            irdr_stop = stop_str
        assert irdr_stop == _DECISION_EPOCH

    def test_grdr_stop_equals_decision_epoch(self, grdr_result):
        product, _ = grdr_result
        stop_str = product.observation_stop_utc
        if isinstance(stop_str, str):
            grdr_stop = datetime.fromisoformat(stop_str.replace("Z", "+00:00"))
        else:
            grdr_stop = stop_str
        assert grdr_stop == _DECISION_EPOCH

    def test_all_three_aligned(self, horizons_result, irdr_result, grdr_result):
        """All three authoritative sources share exactly the decision epoch."""
        horizons_epoch = horizons_result.geometry.epoch_utc

        irdr_product, _ = irdr_result
        irdr_stop_str = irdr_product.observation_stop_utc
        if isinstance(irdr_stop_str, str):
            irdr_stop = datetime.fromisoformat(irdr_stop_str.replace("Z", "+00:00"))
        else:
            irdr_stop = irdr_stop_str

        grdr_product, _ = grdr_result
        grdr_stop_str = grdr_product.observation_stop_utc
        if isinstance(grdr_stop_str, str):
            grdr_stop = datetime.fromisoformat(grdr_stop_str.replace("Z", "+00:00"))
        else:
            grdr_stop = grdr_stop_str

        assert horizons_epoch == irdr_stop == grdr_stop == _DECISION_EPOCH, (
            f"TEMPORAL_ALIGNMENT_MISMATCH: "
            f"Horizons={horizons_epoch.isoformat()}, "
            f"IRDR_stop={irdr_stop.isoformat()}, "
            f"GRDR_stop={grdr_stop.isoformat()}, "
            f"expected={_DECISION_EPOCH.isoformat()}"
        )


# ---------------------------------------------------------------------------
# Part 3: Authoritative geometry
# ---------------------------------------------------------------------------


class TestAuthoritativeGeometry:
    """Exact Horizons range_km and descriptor geometry policy."""

    def test_range_km_exact(self, horizons_result):
        """Authoritative range_km must be exactly the frozen value."""
        assert horizons_result.geometry.range_km == _EXPECTED_RANGE_KM

    def test_one_way_light_time_exact(self, horizons_result):
        """Authoritative one_way_light_time_s must be exactly the frozen value."""
        assert horizons_result.geometry.one_way_light_time_s == _EXPECTED_ONE_WAY_LIGHT_TIME_S

    def test_descriptor_does_not_store_distance_km(self, descriptor):
        """Descriptor must NOT store a redundant distance_km field.

        Scenario.distance_km is DERIVED from Horizons range_km at assembly time.
        Storing it in the descriptor would violate the 'no redundant geometry'
        invariant frozen in C4B.
        """
        # Confirm the attribute does not exist on the descriptor model.
        assert not hasattr(descriptor, "distance_km"), (
            "Descriptor must NOT have a 'distance_km' field — "
            "it must be derived from Horizons range_km by the assembler."
        )
        # Also confirm the raw JSON does not contain it.
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        assert "distance_km" not in raw, (
            "Descriptor JSON must NOT contain 'distance_km'."
        )


# ---------------------------------------------------------------------------
# Part 4: Latency semantics (C4 correction lock)
# ---------------------------------------------------------------------------


class TestLatencySemantics:
    """latency_s is modeled protocol overhead — NOT Horizons one_way_light_time_s."""

    def test_modeled_latency_is_1_5(self, descriptor):
        """Frozen modeled latency_s must be 1.5."""
        assert descriptor.link_policy.latency_s == _MODELED_LATENCY_S

    def test_latency_is_not_light_time(self, descriptor, horizons_result):
        """latency_s must NOT equal the authoritative one_way_light_time_s.

        This test locks the C4 correction: propagation delay must not be
        mapped into link-layer protocol latency.
        """
        assert descriptor.link_policy.latency_s != horizons_result.geometry.one_way_light_time_s, (
            f"CRITICAL: latency_s ({descriptor.link_policy.latency_s}) must not equal "
            f"one_way_light_time_s ({horizons_result.geometry.one_way_light_time_s}). "
            "Protocol latency and propagation delay are semantically distinct."
        )

    def test_light_time_is_authoritative_not_protocol_latency(self, horizons_result):
        """One-way light time is an EXTERNAL_AUTHORITATIVE geometry fact, not latency."""
        assert horizons_result.geometry.one_way_light_time_s == _EXPECTED_ONE_WAY_LIGHT_TIME_S
        # It is not 1.5 s (the modeled protocol latency).
        assert horizons_result.geometry.one_way_light_time_s != _MODELED_LATENCY_S


# ---------------------------------------------------------------------------
# Part 5: Risk-level derivation
# ---------------------------------------------------------------------------


class TestRiskLevelDerivation:
    """Risk-score 0.35 → MEDIUM via gcsi_risk_thresholds_v1."""

    def test_pj62_risk_score_derives_medium(self, descriptor):
        risk_level = replay_risk_level_from_score(descriptor.mission_policy.risk_score)
        assert risk_level == "MEDIUM"

    def test_risk_level_policy_identifier(self, descriptor):
        assert descriptor.risk_level_policy == "gcsi_risk_thresholds_v1"


# ---------------------------------------------------------------------------
# Part 6: Feasibility diagnostic (GCSI simulation, NOT NASA evidence)
# ---------------------------------------------------------------------------


class TestFeasibilityDiagnostic:
    """Offline deterministic feasibility: IRDR fits, GRDR fits, both do NOT fit.

    All numbers here are GCSI SIMULATION BEHAVIOR, not historical NASA facts.

    Uses production deterministic telecom formulas and the authoritative
    product sizes from the verified PDS snapshots.
    """

    @pytest.fixture()
    def telecom_params(self, irdr_result, grdr_result):
        irdr_product, _ = irdr_result
        grdr_product, _ = grdr_result

        # Compute from production formulas with frozen modeled params.
        eb_n0 = snr_to_eb_n0(_MODELED_SNR_DB, _CHANNEL_BANDWIDTH_HZ, _BIT_RATE_BPS)
        ber = bpsk_ber(eb_n0)
        goodput = link_goodput(_BIT_RATE_BPS, _PROTOCOL_EFFICIENCY)

        # IRDR
        irdr_p = packet_success_probability(ber, _IRDR_SIZE_BITS)
        irdr_tx = transmission_time(_IRDR_SIZE_BITS, goodput)
        irdr_cost = expected_transmission_cost(irdr_tx, irdr_p)

        # GRDR
        grdr_p = packet_success_probability(ber, _GRDR_SIZE_BITS)
        grdr_tx = transmission_time(_GRDR_SIZE_BITS, goodput)
        grdr_cost = expected_transmission_cost(grdr_tx, grdr_p)

        return {
            "eb_n0": eb_n0,
            "ber": ber,
            "goodput": goodput,
            "irdr_p_success": irdr_p,
            "irdr_tx_time": irdr_tx,
            "irdr_expected_cost": irdr_cost,
            "grdr_p_success": grdr_p,
            "grdr_tx_time": grdr_tx,
            "grdr_expected_cost": grdr_cost,
        }

    def test_eb_n0_approx_13_db(self, telecom_params):
        """Eb/N0 ≈ 13 dB for SNR=3 dB, BW=1 MHz, rate=100 kbps."""
        assert abs(telecom_params["eb_n0"] - 13.0) < 0.001

    def test_goodput_is_90000_bps(self, telecom_params):
        """Link goodput at 100000 bps × 0.9 efficiency = 90000 bps."""
        assert telecom_params["goodput"] == pytest.approx(90000.0)

    def test_ber_approx(self, telecom_params):
        """BER ≈ 1.33e-10 (BPSK/AWGN at Eb/N0≈13 dB)."""
        assert telecom_params["ber"] == pytest.approx(1.33293101753005e-10, rel=1e-3)

    def test_irdr_size_bits(self):
        """IRDR size = 6694664 bytes × 8 = 53557312 bits."""
        assert _IRDR_SIZE_BITS == 53_557_312

    def test_grdr_size_bits(self):
        """GRDR size = 5093997 bytes × 8 = 40751976 bits."""
        assert _GRDR_SIZE_BITS == 40_751_976

    def test_irdr_p_success_approx(self, telecom_params):
        """IRDR p_success ≈ 0.9928866."""
        assert telecom_params["irdr_p_success"] == pytest.approx(0.9928866006, rel=1e-4)

    def test_grdr_p_success_approx(self, telecom_params):
        """GRDR p_success ≈ 0.9945828."""
        assert telecom_params["grdr_p_success"] == pytest.approx(0.9945827691, rel=1e-4)

    def test_irdr_expected_cost_approx(self, telecom_params):
        """IRDR expected cost ≈ 599.34 s."""
        assert telecom_params["irdr_expected_cost"] == pytest.approx(599.344622, rel=1e-3)

    def test_grdr_expected_cost_approx(self, telecom_params):
        """GRDR expected cost ≈ 455.27 s."""
        assert telecom_params["grdr_expected_cost"] == pytest.approx(455.266014, rel=1e-3)

    def test_irdr_fits_individually(self, telecom_params):
        """IRDR expected cost must be < modeled window (900 s)."""
        assert telecom_params["irdr_expected_cost"] < _MODELED_WINDOW_S, (
            f"IRDR expected cost {telecom_params['irdr_expected_cost']:.3f} s "
            f"must be < {_MODELED_WINDOW_S} s"
        )

    def test_grdr_fits_individually(self, telecom_params):
        """GRDR expected cost must be < modeled window (900 s)."""
        assert telecom_params["grdr_expected_cost"] < _MODELED_WINDOW_S, (
            f"GRDR expected cost {telecom_params['grdr_expected_cost']:.3f} s "
            f"must be < {_MODELED_WINDOW_S} s"
        )

    def test_both_do_not_fit_sequentially(self, telecom_params):
        """IRDR + GRDR sequential expected cost must EXCEED the modeled window.

        This is the intentional replay-policy design: the first replay must
        produce a genuine prioritization/feasibility decision rather than a
        trivial 'send everything' result.

        This is GCSI simulation behavior — NOT historical NASA evidence.
        """
        combined = (
            telecom_params["irdr_expected_cost"]
            + telecom_params["grdr_expected_cost"]
        )
        assert combined > _MODELED_WINDOW_S, (
            f"Combined sequential cost {combined:.3f} s must exceed "
            f"modeled window {_MODELED_WINDOW_S} s. "
            "Replay must require a genuine prioritization decision."
        )

    def test_combined_cost_approx(self, telecom_params):
        """Combined sequential cost ≈ 1054.61 s."""
        combined = (
            telecom_params["irdr_expected_cost"]
            + telecom_params["grdr_expected_cost"]
        )
        assert combined == pytest.approx(1054.610636, rel=1e-3)
