"""Candidate plan generator — produces four named strategy variants.

Each strategy uses its own deterministic ordering principle.  No random
perturbation, no weight modification.

Plan IDs are stable and deterministic:
    "baseline", "deadline-first", "mission-critical-first", "value-per-cost"

Running generate() twice with identical inputs produces identical
CandidatePlan objects including plan_id.

Strategies
----------
baseline
    Delegates to BaselineScheduler.

deadline_first
    Earliest deadline ascending.  Ties: criticality descending, then packet_id.

mission_critical_first
    Highest criticality descending.  Ties: mission_relevance descending, then packet_id.

value_per_cost
    Highest (criticality × mission_relevance) / expected_transmission_cost first.
    Packets with infinite expected cost are placed last (sorted by packet_id among
    themselves for determinism).
"""

import math

from ..config import SchedulerWeights
from ..models.candidate_plan import CandidatePlan
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.packet import Packet
from ..scheduler.baseline import BaselineScheduler
from ..telecom.formulas import (
    expected_transmission_cost,
    packet_success_probability,
    transmission_time,
)
from ..telecom.scheduler import rank_packets


def _calc_expected_cost(packet: Packet, link_state: LinkState) -> float:
    """Return expected transmission cost for a packet; math.inf when p_success <= 0."""
    p_s = packet_success_probability(link_state.ber, packet.size_bits)
    tx = transmission_time(packet.size_bits, link_state.link_goodput_bps)
    return expected_transmission_cost(tx, p_s)


def _value_per_cost(packet: Packet, link_state: LinkState) -> float:
    """Return value/cost ratio; 0.0 when cost is infinite."""
    cost = _calc_expected_cost(packet, link_state)
    if math.isinf(cost):
        return 0.0  # infinite cost → ratio of 0 → sorted last
    value = packet.criticality * packet.mission_relevance
    if cost == 0.0:
        # Theoretically unreachable (cost=0 requires tx_time=0 which needs size_bits=0,
        # but size_bits is constrained > 0).  Guard defensively.
        return math.inf
    return value / cost


class CandidateGenerator:
    """Generates four named CandidatePlan variants from the same packet list."""

    @staticmethod
    def generate(
        packets: list[Packet],
        link_state: LinkState,
        mission_state: MissionState,
        weights: SchedulerWeights,
    ) -> list[CandidatePlan]:
        """Produce four candidate plans.

        Args:
            packets:       Packets to rank (not mutated).
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            weights:       Forwarded to BaselineScheduler for the baseline plan.

        Returns:
            A list of four :class:`CandidatePlan` objects in the order:
            [baseline, deadline_first, mission_critical_first, value_per_cost].

        Each plan's ``metadata`` includes a ``telecom_decisions`` key containing
        a list of per-packet telecom scheduling decisions (TRANSMIT / DEFER / SPLIT)
        ranked by mission efficiency for the current link state.
        """
        # Compute telecom decisions once — they depend only on link state, not
        # on ordering strategy.
        telecom_decisions = [
            {
                "packet_id": d.packet_id,
                "decision": d.decision,
                "reason": d.reason,
                "p_success": d.p_success,
                "efficiency": d.efficiency,
            }
            for d in rank_packets(packets, link_state.ber, link_state.link_goodput_bps)
        ]

        plans = [
            CandidateGenerator._baseline(packets, link_state, mission_state, weights),
            CandidateGenerator._deadline_first(packets),
            CandidateGenerator._mission_critical_first(packets),
            CandidateGenerator._value_per_cost(packets, link_state),
        ]

        # Attach the same telecom_decisions to every plan's metadata.
        return [
            plan.model_copy(update={"metadata": {**plan.metadata, "telecom_decisions": telecom_decisions}})
            for plan in plans
        ]

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _baseline(
        packets: list[Packet],
        link_state: LinkState,
        mission_state: MissionState,
        weights: SchedulerWeights,
    ) -> CandidatePlan:
        return BaselineScheduler.rank(packets, link_state, mission_state, weights)

    @staticmethod
    def _deadline_first(packets: list[Packet]) -> CandidatePlan:
        """Earliest deadline ascending; ties: criticality desc, then packet_id."""
        ranked = sorted(
            packets,
            key=lambda p: (p.deadline_s, -p.criticality, p.packet_id),
        )
        return CandidatePlan(
            plan_id="deadline-first",
            strategy="deadline_first",
            packets=ranked,
            generated_by="CandidateGenerator",
        )

    @staticmethod
    def _mission_critical_first(packets: list[Packet]) -> CandidatePlan:
        """Highest criticality descending; ties: mission_relevance desc, then packet_id."""
        ranked = sorted(
            packets,
            key=lambda p: (-p.criticality, -p.mission_relevance, p.packet_id),
        )
        return CandidatePlan(
            plan_id="mission-critical-first",
            strategy="mission_critical_first",
            packets=ranked,
            generated_by="CandidateGenerator",
        )

    @staticmethod
    def _value_per_cost(
        packets: list[Packet], link_state: LinkState
    ) -> CandidatePlan:
        """Highest value/cost ratio first; infinite-cost packets placed last."""
        finite: list[Packet] = []
        infinite_cost: list[Packet] = []

        for pkt in packets:
            cost = _calc_expected_cost(pkt, link_state)
            if math.isinf(cost):
                infinite_cost.append(pkt)
            else:
                finite.append(pkt)

        # Sort finite-cost packets by ratio descending, packet_id as tie-breaker
        finite_ranked = sorted(
            finite,
            key=lambda p: (-_value_per_cost(p, link_state), p.packet_id),
        )
        # Sort infinite-cost packets by packet_id for determinism
        infinite_ranked = sorted(infinite_cost, key=lambda p: p.packet_id)

        ranked = finite_ranked + infinite_ranked

        return CandidatePlan(
            plan_id="value-per-cost",
            strategy="value_per_cost",
            packets=ranked,
            generated_by="CandidateGenerator",
        )
