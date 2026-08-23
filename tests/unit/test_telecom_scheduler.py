"""Unit tests for the telecom packet scheduling decision engine.

Three link quality scenarios are covered:

good_link    BER = 1e-6,         goodput = 90 000 bps
current_link BER = 3.3627228e-5, goodput = 90 000 bps
bad_link     BER = 1e-4,         goodput = 90 000 bps

All packet objects are constructed from the canonical
``backend.app.models.packet.Packet`` Pydantic model.
"""

import pytest

from backend.app.models.packet import Packet
from backend.app.telecom.scheduler import evaluate_packet, PacketDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GOODPUT = 90_000.0  # bps — constant across all scenarios


def _make_packet(
    packet_id: str,
    packet_type: str,
    size_bits: int,
    criticality: float,
    mission_relevance: float,
) -> Packet:
    """Build a Packet with sensible defaults for non-scheduling fields."""
    return Packet(
        packet_id=packet_id,
        packet_type=packet_type,
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=mission_relevance,
        deadline_s=300.0,
        retry_cost=1.0,
        delivery_requirement="best-effort",
    )


def _decision(packet: Packet, ber: float) -> str:
    """Return just the decision string for brevity in assertions."""
    result: PacketDecision = evaluate_packet(packet, ber, GOODPUT)
    return result.decision


# ---------------------------------------------------------------------------
# Good link  (BER = 1e-6)
# ---------------------------------------------------------------------------

BER_GOOD = 1e-6


class TestGoodLink:
    """Both packets should be TRANSMIT on a near-perfect link."""

    def test_critical_telemetry_transmit(self):
        """High-criticality telemetry packet → TRANSMIT.

        criticality = 0.95 ≥ 0.9 and p_success ≈ 1.0 ≥ 0.5 → rule 1 fires.
        """
        pkt = _make_packet(
            packet_id="crit-telem-good",
            packet_type="telemetry",
            size_bits=1024,
            criticality=0.95,
            mission_relevance=0.8,
        )
        assert _decision(pkt, BER_GOOD) == "TRANSMIT"

    def test_large_science_image_transmit(self):
        """Science image on a good link → TRANSMIT.

        p_success ≈ 0.992 ≥ 0.9 and expected_cost ≈ 0.092 s ≤ 0.5 → rule 3 fires.
        """
        pkt = _make_packet(
            packet_id="sci-img-good",
            packet_type="science",
            size_bits=8192,
            criticality=0.6,
            mission_relevance=0.8,
        )
        assert _decision(pkt, BER_GOOD) == "TRANSMIT"


# ---------------------------------------------------------------------------
# Current link  (BER = 3.3627228e-5)
# ---------------------------------------------------------------------------

BER_CURRENT = 3.3627228e-5


class TestCurrentLink:
    """Mixed outcomes — critical packets transmit, large payload defers/splits."""

    def test_evt_critical_transmit(self):
        """Event-critical packet → TRANSMIT.

        criticality = 0.95 ≥ 0.9 and p_success ≈ 0.983 ≥ 0.5 → rule 1.
        """
        pkt = _make_packet(
            packet_id="evt-critical",
            packet_type="command",
            size_bits=512,
            criticality=0.95,
            mission_relevance=0.8,
        )
        assert _decision(pkt, BER_CURRENT) == "TRANSMIT"

    def test_nav_update_transmit(self):
        """Navigation update → TRANSMIT.

        p_success ≈ 0.934 ≥ 0.9 and expected_cost ≈ 0.024 s ≤ 0.5 → rule 3.
        """
        pkt = _make_packet(
            packet_id="nav-update",
            packet_type="telemetry",
            size_bits=2048,
            criticality=0.8,
            mission_relevance=0.9,
        )
        assert _decision(pkt, BER_CURRENT) == "TRANSMIT"

    def test_science_sample_transmit(self):
        """Science sample on current link → TRANSMIT.

        p_success ≈ 0.872, expected_cost ≈ 0.052 s.
        efficiency = (0.5 × 0.6) / 0.052 ≈ 5.75 ≥ 5.0 → rule 5 (good value/cost).
        """
        pkt = _make_packet(
            packet_id="science-sample",
            packet_type="science",
            size_bits=4096,
            criticality=0.5,
            mission_relevance=0.6,
        )
        assert _decision(pkt, BER_CURRENT) == "TRANSMIT"

    def test_science_image_defer(self):
        """Large science image on current link → SPLIT.

        size = 500 000 bits → p_success ≈ 5 × 10⁻⁸ < 0.2 → rule 2 (reliability too low).
        """
        pkt = _make_packet(
            packet_id="science-image",
            packet_type="science",
            size_bits=500_000,
            criticality=0.3,
            mission_relevance=0.4,
        )
        assert _decision(pkt, BER_CURRENT) == "SPLIT"


# ---------------------------------------------------------------------------
# Bad link  (BER = 1e-4)
# ---------------------------------------------------------------------------

BER_BAD = 1e-4


class TestBadLink:
    """Critical traffic still transmits; medium and large payloads defer or split."""

    def test_evt_critical_transmit(self):
        """Event-critical packet → TRANSMIT even on a bad link.

        criticality = 0.95 ≥ 0.9 and p_success ≈ 0.950 ≥ 0.5 → rule 1.
        """
        pkt = _make_packet(
            packet_id="evt-critical-bad",
            packet_type="command",
            size_bits=512,
            criticality=0.95,
            mission_relevance=0.8,
        )
        assert _decision(pkt, BER_BAD) == "TRANSMIT"

    def test_nav_update_transmit(self):
        """Navigation update → TRANSMIT on a bad link.

        p_success ≈ 0.815 (< 0.9), expected_cost ≈ 0.028 s ≤ 0.5.
        efficiency = (0.8 × 0.9) / 0.028 ≈ 25.8 ≥ 5.0 → rule 5.
        """
        pkt = _make_packet(
            packet_id="nav-update-bad",
            packet_type="telemetry",
            size_bits=2048,
            criticality=0.8,
            mission_relevance=0.9,
        )
        assert _decision(pkt, BER_BAD) == "TRANSMIT"

    def test_science_sample_defer(self):
        """Science sample on bad link → DEFER.

        p_success ≈ 0.664 (≥ 0.2), expected_cost ≈ 0.069 s (≤ 5 s),
        p_success < 0.9 → rule 3 does not fire.
        efficiency = (0.5 × 0.6) / 0.069 ≈ 4.37 < 5.0 → rule 4 (low value/cost).
        """
        pkt = _make_packet(
            packet_id="science-sample-bad",
            packet_type="science",
            size_bits=4096,
            criticality=0.5,
            mission_relevance=0.6,
        )
        assert _decision(pkt, BER_BAD) == "DEFER"

    def test_science_image_split(self):
        """Large science image on bad link → SPLIT.

        size = 500 000 bits → p_success ≈ 2 × 10⁻²² < 0.2 → rule 2.
        """
        pkt = _make_packet(
            packet_id="science-image-bad",
            packet_type="science",
            size_bits=500_000,
            criticality=0.3,
            mission_relevance=0.4,
        )
        assert _decision(pkt, BER_BAD) == "SPLIT"
