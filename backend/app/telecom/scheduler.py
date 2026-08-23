"""GCSI Packet Scheduling Decision Engine.

Deterministic decision layer built on top of the pure telecommunications
formulas. This module ranks packets by mission value per expected delivery
cost and recommends TRANSMIT, DEFER, or SPLIT.
"""

import math
from dataclasses import dataclass
from typing import Literal

from ..models.packet import Packet
from .formulas import (
    packet_success_probability,
    transmission_time,
    expected_transmission_cost,
)


Decision = Literal["TRANSMIT", "DEFER", "SPLIT"]


@dataclass(frozen=True)
class PacketDecision:
    packet_id: str
    packet_size_bits: int
    p_success: float
    tx_time_s: float
    expected_cost_s: float
    mission_value: float
    efficiency: float
    decision: Decision
    reason: str


def evaluate_packet(
    packet: Packet,
    ber: float,
    goodput_bps: float,
    split_threshold_s: float = 0.5,
) -> PacketDecision:
    """Evaluate one packet under current link conditions."""

    p_success = packet_success_probability(
        ber,
        packet.size_bits,
    )

    tx_time = transmission_time(
        packet.size_bits,
        goodput_bps,
    )

    expected_cost = expected_transmission_cost(
        tx_time,
        p_success,
    )

    mission_value = packet.criticality * packet.mission_relevance

    if math.isinf(expected_cost):
        efficiency = 0.0
    else:
        efficiency = mission_value / expected_cost

    # Critical packets should be transmitted whenever delivery is reasonably
    # reliable.
    if packet.criticality >= 0.9 and p_success >= 0.5:
        decision = "TRANSMIT"
        reason = "High-criticality packet with acceptable delivery probability."

    # Very poor packet reliability makes large packets inefficient to deliver.
    elif p_success < 0.2 or expected_cost > split_threshold_s * 10:
        decision = "SPLIT"
        reason = (
            "Packet reliability is too low or expected delivery cost is extreme."
        )

    # If the packet can be delivered reliably at reasonable cost, transmit it.
    elif p_success >= 0.9 and expected_cost <= 0.5:
        decision = "TRANSMIT"
        reason = "Link conditions support reliable delivery at reasonable cost."

    # Low mission value can wait when it competes with more valuable traffic.
    elif efficiency < 5.0:
        decision = "DEFER"
        reason = "Low mission value relative to expected transmission cost."

    else:
        decision = "TRANSMIT"
        reason = "Good mission value relative to expected transmission cost."

    return PacketDecision(
        packet_id=packet.packet_id,
        packet_size_bits=packet.size_bits,
        p_success=p_success,
        tx_time_s=tx_time,
        expected_cost_s=expected_cost,
        mission_value=mission_value,
        efficiency=efficiency,
        decision=decision,
        reason=reason,
    )


def rank_packets(
    packets: list[Packet],
    ber: float,
    goodput_bps: float,
) -> list[PacketDecision]:
    """Evaluate and rank packets by mission efficiency."""

    decisions = [
        evaluate_packet(
            packet,
            ber,
            goodput_bps,
        )
        for packet in packets
    ]

    return sorted(
        decisions,
        key=lambda item: item.efficiency,
        reverse=True,
    )