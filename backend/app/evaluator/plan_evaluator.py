"""Deterministic plan evaluator — expected/analytical metrics only.

``PlanEvaluator.evaluate()`` never calls any RNG.  Every metric is a
closed-form calculation derived from telecom formulas and the plan's
ordered packet list.  This makes the evaluator suitable as a stable
benchmark harness: the same function measures both the baseline plan
and any AI-recommended plan, producing directly comparable results.

Risk score formula::

    deadline_miss_rate  = deadline_misses / max(total_packets, 1)
    critical_deficit    = 1 - (critical_packets_delivered / max(total_critical_packets, 1))
    window_pressure     = min(cumulative_time_s / window_s, 1.0)   # fraction of budget consumed

    risk_score = clamp(
        w_deadline_miss    * deadline_miss_rate
      + w_critical_deficit * critical_deficit
      + w_window_pressure  * window_pressure,
      0.0, 1.0
    )

Risk level thresholds::

    risk_score <  0.25  → LOW
    risk_score <  0.50  → MEDIUM
    risk_score <  0.75  → HIGH
    risk_score >= 0.75  → CRITICAL

``criticality_threshold`` for counting "critical" packets is 0.7 (configurable
via constructor; not a SchedulerWeights concern).
"""

from ..config import RiskWeights
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.risk_level import RiskLevel
from ..telecom.formulas import (
    expected_transmission_cost,
    packet_success_probability,
    transmission_time,
)

#: Default threshold above which a packet is counted as "critical".
DEFAULT_CRITICALITY_THRESHOLD: float = 0.7


def _risk_level_from_score(risk_score: float) -> RiskLevel:
    """Map a risk_score in [0, 1] to a categorical RiskLevel."""
    if risk_score < 0.25:
        return RiskLevel.LOW
    if risk_score < 0.50:
        return RiskLevel.MEDIUM
    if risk_score < 0.75:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


class PlanEvaluator:
    """Compute expected/analytical performance metrics for a CandidatePlan.

    No random number generation anywhere in this class.  Results are
    fully reproducible given identical inputs.

    Args:
        risk_weights:            Weights for the three-term risk formula.
                                 Defaults to ``RiskWeights()`` (env-configurable).
        criticality_threshold:   Minimum criticality score to count a packet as
                                 "critical" for delivery counting purposes.
    """

    def __init__(
        self,
        risk_weights: RiskWeights | None = None,
        criticality_threshold: float = DEFAULT_CRITICALITY_THRESHOLD,
    ) -> None:
        self._risk_weights = risk_weights or RiskWeights()
        self._criticality_threshold = criticality_threshold

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        plan: CandidatePlan,
        link_state: LinkState,
        mission_state: MissionState,
    ) -> EvaluationResult:
        """Evaluate *plan* analytically against current link and mission state.

        Args:
            plan:          Ordered transmission plan to evaluate.
            link_state:    Current link snapshot (provides goodput, BER, window).
            mission_state: Current mission snapshot (provides window, context).

        Returns:
            :class:`EvaluationResult` with expected/analytical metrics.
            No fields are drawn stochastically.
        """
        # Use the smaller of the two window values as the effective budget.
        window_s: float = min(
            link_state.remaining_window_s,
            mission_state.comm_window_remaining_s,
        )

        # --- Walk packets in plan order, accumulating expected elapsed time ---
        cumulative_time_s: float = 0.0

        delivered_ids: list[str] = []
        deferred_ids: list[str] = []

        mission_value: float = 0.0
        total_critical: int = 0
        critical_delivered: int = 0
        deadline_misses: int = 0
        delivery_timestamps: list[float] = []
        retransmission_overhead: float = 0.0

        for pkt in plan.packets:
            tx = transmission_time(pkt.size_bits, link_state.link_goodput_bps)
            p_s = packet_success_probability(link_state.ber, pkt.size_bits)

            # Fix 2: a zero-probability packet can never be delivered regardless
            # of remaining window.  Defer it immediately without consuming budget.
            if p_s <= 0.0:
                deferred_ids.append(pkt.packet_id)
                continue

            cost = expected_transmission_cost(tx, p_s)

            # Fix 3: a packet is analytically delivered only if its expected
            # completion time fits entirely within the remaining window budget.
            # "Expected completion" = cumulative budget consumed so far + this
            # packet's expected cost (including retransmission factor).
            expected_completion = cumulative_time_s + cost

            if expected_completion > window_s:
                # Packet would not complete within the window — defer it.
                deferred_ids.append(pkt.packet_id)
                continue

            # Packet fits — count it as analytically delivered.
            delivered_ids.append(pkt.packet_id)
            cumulative_time_s = expected_completion   # advance by full expected cost

            # Accumulate analytical metrics for delivered packets.
            mission_value += pkt.criticality * pkt.mission_relevance
            delivery_timestamps.append(expected_completion)

            # Retransmission overhead: expected extra transmission time beyond
            # the single-attempt baseline.
            # extra = (1/p_success - 1) * tx_time  (always >= 0 here since p_s > 0)
            retransmission_overhead += (1.0 / p_s - 1.0) * tx

            if pkt.deadline_s < expected_completion:
                deadline_misses += 1

            if pkt.criticality >= self._criticality_threshold:
                critical_delivered += 1

        # Count ALL critical packets across the full plan (delivered + deferred).
        for pkt in plan.packets:
            if pkt.criticality >= self._criticality_threshold:
                total_critical += 1

        # --- Scalar metrics ---
        avg_delay = (
            sum(delivery_timestamps) / len(delivery_timestamps)
            if delivery_timestamps
            else 0.0
        )

        total_delivered_bits = sum(
            pkt.size_bits
            for pkt in plan.packets
            if pkt.packet_id in delivered_ids
        )
        bandwidth_utilization = min(
            total_delivered_bits / (link_state.link_goodput_bps * window_s)
            if window_s > 0.0
            else 0.0,
            1.0,
        )

        # --- Risk score ---
        rw = self._risk_weights
        total_packets = len(plan.packets)

        deadline_miss_rate = deadline_misses / max(total_packets, 1)
        # When there are no critical packets there is no deficit.
        critical_deficit = (
            0.0
            if total_critical == 0
            else 1.0 - (critical_delivered / total_critical)
        )
        # Fix 1: window_pressure = fraction of the initial window budget consumed
        # by the plan's expected transmission cost.  When window_s = 0 there is no
        # budget, so pressure is maximum (1.0).
        window_pressure = (
            min(cumulative_time_s / window_s, 1.0) if window_s > 0.0 else 1.0
        )

        risk_score_raw = (
            rw.w_deadline_miss * deadline_miss_rate
            + rw.w_critical_deficit * critical_deficit
            + rw.w_window_pressure * window_pressure
        )
        risk_score = max(0.0, min(risk_score_raw, 1.0))
        risk_level = _risk_level_from_score(risk_score)

        return EvaluationResult(
            plan_id=plan.plan_id,
            mission_value=mission_value,
            critical_packets_delivered=critical_delivered,
            total_critical_packets=total_critical,
            deadline_misses=deadline_misses,
            avg_packet_delay_s=avg_delay,
            bandwidth_utilization=bandwidth_utilization,
            retransmission_overhead=retransmission_overhead,
            risk_score=risk_score,
            risk_level=risk_level,
            deferred_packets=deferred_ids,
        )
