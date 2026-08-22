"""Deterministic weighted-priority baseline scheduler.

The baseline scheduler assigns each packet a scalar score from five
independent, dimensionless factors.  The formula is::

    score =
        w_criticality        * criticality
      + w_deadline_urgency   * deadline_urgency
      + w_mission_relevance  * mission_relevance
      + w_delivery_reliability * packet_success_probability
      + w_cost_efficiency    * cost_efficiency

All five factors are normalised to [0, 1] so weights are directly
comparable.  The factors are:

criticality
    Intrinsic packet importance (from Packet model).

deadline_urgency
    Linear urgency derived from how close the deadline is relative to the
    remaining communication window.

mission_relevance
    Intrinsic mission context score (from Packet model).

delivery_reliability  (replaces delivery_risk)
    = packet_success_probability(ber, size_bits)
    Direct probability of successful delivery; higher is better.
    This is the inverse of the old ``delivery_risk`` factor.

cost_efficiency  (replaces transmission_efficiency + cost_score)
    = 1.0 - cost_pressure
    where cost_pressure = min(expected_cost / comm_window, 1.0)
    Dimensionless: expected_cost and comm_window are both in seconds.
    Higher efficiency means the packet consumes less of the available window.
    When expected_cost is math.inf, cost_efficiency = 0.0.

Design notes
------------
- delivery_reliability and cost_efficiency are NOT redundant: reliability
  captures per-bit BER-based success probability; cost_efficiency captures
  the *time* pressure a packet places on the window budget.
- Neither redundant efficiency+cost pair nor a risk-penalisation term is
  retained — those three old factors were strongly correlated.
- All weight literals live in SchedulerWeights; none are in this module.
- Packet.priority is NEVER set.  Priority is expressed solely as position
  within the returned CandidatePlan.
"""

import math
from dataclasses import dataclass

from ..config import SchedulerWeights
from ..models.candidate_plan import CandidatePlan
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.packet import Packet
from ..telecom.formulas import (
    expected_transmission_cost,
    packet_success_probability,
    transmission_time,
)


# ---------------------------------------------------------------------------
# Normalised factor helpers (all return values in [0, 1])
# ---------------------------------------------------------------------------


def _deadline_urgency(packet: Packet, mission_state: MissionState) -> float:
    """Urgency in [0, 1]: 1 when deadline has passed, 0 when beyond the window.

    Increases linearly as the deadline approaches, relative to the remaining
    communication window.
    """
    window = mission_state.comm_window_remaining_s
    if window <= 0.0:
        return 1.0
    urgency = 1.0 - min(max(packet.deadline_s, 0.0), window) / window
    return urgency


def _delivery_reliability(packet: Packet, link_state: LinkState) -> float:
    """Packet-level delivery reliability = packet_success_probability in [0, 1].

    Higher is better — this rewards packets likely to arrive intact.
    Replaces the old ``_delivery_risk`` factor (which was its complement).
    """
    return packet_success_probability(link_state.ber, packet.size_bits)


def _cost_efficiency(
    packet: Packet, link_state: LinkState, mission_state: MissionState
) -> float:
    """Cost efficiency in [0, 1].

    Formula::
        cost_pressure = min(expected_cost / comm_window, 1.0)
        cost_efficiency = 1.0 - cost_pressure

    Both expected_cost (seconds) and comm_window (seconds) share the same
    unit, so the ratio is dimensionless.

    When expected_cost is math.inf → cost_efficiency = 0.0.
    When comm_window <= 0 → cost_efficiency = 0.0 (no budget left).
    """
    window = mission_state.comm_window_remaining_s
    if window <= 0.0:
        return 0.0

    p_s = packet_success_probability(link_state.ber, packet.size_bits)
    tx = transmission_time(packet.size_bits, link_state.link_goodput_bps)
    cost = expected_transmission_cost(tx, p_s)

    if math.isinf(cost):
        return 0.0

    cost_pressure = min(cost / window, 1.0)
    return 1.0 - cost_pressure


# ---------------------------------------------------------------------------
# BaselineScheduler
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PacketScore:
    """Internal value object used for sorting only — never attached to Packet."""

    score: float
    packet_id: str  # deterministic tie-breaker


class BaselineScheduler:
    """Produces a single deterministic weighted-priority CandidatePlan.

    Scoring formula (all factors in [0, 1])::

        score =
            w.w_criticality         * packet.criticality
          + w.w_deadline_urgency    * deadline_urgency(packet, mission_state)
          + w.w_mission_relevance   * packet.mission_relevance
          + w.w_delivery_reliability * delivery_reliability(packet, link_state)
          + w.w_cost_efficiency     * cost_efficiency(packet, link_state, mission_state)

    Higher score → earlier in the plan.
    Ties broken lexicographically by packet_id.
    """

    @staticmethod
    def rank(
        packets: list[Packet],
        link_state: LinkState,
        mission_state: MissionState,
        weights: SchedulerWeights,
    ) -> CandidatePlan:
        """Rank packets and return a CandidatePlan.

        Args:
            packets:       Packets to rank (not mutated).
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            weights:       Configurable scoring weights.

        Returns:
            A :class:`CandidatePlan` with ``strategy="baseline"``,
            ``plan_id="baseline"``, and ``generated_by="BaselineScheduler"``.
        """
        if not packets:
            return CandidatePlan(
                plan_id="baseline",
                strategy="baseline",
                packets=[],
                generated_by="BaselineScheduler",
            )

        scored: list[_PacketScore] = []
        for pkt in packets:
            score = (
                weights.w_criticality * pkt.criticality
                + weights.w_deadline_urgency * _deadline_urgency(pkt, mission_state)
                + weights.w_mission_relevance * pkt.mission_relevance
                + weights.w_delivery_reliability
                * _delivery_reliability(pkt, link_state)
                + weights.w_cost_efficiency
                * _cost_efficiency(pkt, link_state, mission_state)
            )
            scored.append(_PacketScore(score=score, packet_id=pkt.packet_id))

        # Sort: highest score first; packet_id is the deterministic tie-breaker
        order = sorted(scored, key=lambda s: (-s.score, s.packet_id))

        id_to_packet = {pkt.packet_id: pkt for pkt in packets}
        ranked = [id_to_packet[s.packet_id] for s in order]

        return CandidatePlan(
            plan_id="baseline",
            strategy="baseline",
            packets=ranked,
            generated_by="BaselineScheduler",
        )
