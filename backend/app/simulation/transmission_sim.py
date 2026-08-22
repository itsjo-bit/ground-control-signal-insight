"""Stochastic transmission simulator.

``TransmissionSimulator.simulate()`` realizes packet delivery outcomes by
drawing Bernoulli trials against each packet's ``packet_success_probability``.
It is the execution-layer counterpart to the analytical ``PlanEvaluator``.

**Key contracts**:

- Accepts ``seed: int | None``; if provided, seeds the numpy RNG at method
  entry so results are fully reproducible.
- Never calls ``PlanEvaluator`` or any evaluator method.
- Never mutates inputs (``CandidatePlan``, ``LinkState``, ``MissionState``).
- Returns ``SimulationResult`` — never an ``EvaluationResult``.
- ``EvaluationResult`` and ``SimulationResult`` are separate layers and must
  never be mixed.

Retransmission model:
    Each packet attempt is a Bernoulli draw with
    ``p = packet_success_probability(ber, size_bits)``.
    On failure, another attempt is drawn until either success or the
    communication window is exhausted.  Window exhaustion mid-packet
    causes the packet to be added to ``deferred_packets``.

    If ``p_success == 0`` on the first draw the packet is immediately
    deferred (cannot succeed regardless of remaining window).
"""

import math

import numpy as np

from ..models.candidate_plan import CandidatePlan
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.risk_level import RiskLevel
from ..models.simulation_result import SimulationResult
from ..telecom.formulas import packet_success_probability, transmission_time

#: Maximum retransmission attempts per packet before giving up.
#: Prevents infinite loops when p_success is tiny but non-zero.
MAX_ATTEMPTS: int = 100


def _derive_risk_level(
    delivered: list[str],
    deferred: list[str],
    failed: list[str],
) -> tuple[float, RiskLevel]:
    """Derive a realized risk_score and RiskLevel from simulation outcomes.

    Simple heuristic based on fraction of packets not delivered::

        non_delivered_rate = (deferred + failed) / total
        risk_score = non_delivered_rate  (clamped to [0, 1])

    Thresholds (matching PlanEvaluator):
        < 0.25 → LOW, < 0.50 → MEDIUM, < 0.75 → HIGH, ≥ 0.75 → CRITICAL
    """
    total = len(delivered) + len(deferred) + len(failed)
    if total == 0:
        return 0.0, RiskLevel.LOW
    risk_score = max(0.0, min((len(deferred) + len(failed)) / total, 1.0))
    if risk_score < 0.25:
        level = RiskLevel.LOW
    elif risk_score < 0.50:
        level = RiskLevel.MEDIUM
    elif risk_score < 0.75:
        level = RiskLevel.HIGH
    else:
        level = RiskLevel.CRITICAL
    return risk_score, level


class TransmissionSimulator:
    """Realize stochastic transmission outcomes for a CandidatePlan.

    Usage::

        sim = TransmissionSimulator()
        result = sim.simulate(plan, link_state, mission_state, seed=42)
    """

    def simulate(
        self,
        plan: CandidatePlan,
        link_state: LinkState,
        mission_state: MissionState,
        seed: int | None = None,
    ) -> SimulationResult:
        """Run one stochastic realization of the plan.

        Args:
            plan:          Ordered transmission plan.
            link_state:    Link snapshot at simulation start.
            mission_state: Mission snapshot at simulation start.
            seed:          Optional RNG seed for reproducibility.
                           ``None`` (default) → non-deterministic.

        Returns:
            :class:`SimulationResult` with realized delivery outcomes and
            updated ``LinkState``/``MissionState`` post-simulation.
            Never returns ``EvaluationResult``.
        """
        rng = np.random.default_rng(seed)

        window_s: float = min(
            link_state.remaining_window_s,
            mission_state.comm_window_remaining_s,
        )

        elapsed_s: float = 0.0
        delivered: list[str] = []
        deferred: list[str] = []
        failed: list[str] = []
        retransmission_counts: dict[str, int] = {}

        for pkt in plan.packets:
            if elapsed_s >= window_s:
                # Window already exhausted — defer remaining packets immediately.
                deferred.append(pkt.packet_id)
                retransmission_counts[pkt.packet_id] = 0
                continue

            p_s = packet_success_probability(link_state.ber, pkt.size_bits)
            tx = transmission_time(pkt.size_bits, link_state.link_goodput_bps)

            if p_s <= 0.0:
                # Zero probability of success — defer immediately.
                deferred.append(pkt.packet_id)
                retransmission_counts[pkt.packet_id] = 0
                continue

            # Attempt delivery, retrying on failure until window or attempts exhausted.
            attempts = 0
            packet_outcome = "failed"

            for attempt_num in range(1, MAX_ATTEMPTS + 1):
                attempts = attempt_num
                # Each attempt consumes tx seconds of window.
                elapsed_s += tx

                success: bool = bool(rng.random() < p_s)
                if success:
                    packet_outcome = "delivered"
                    break

                # Failed attempt — check if window still has budget for a retry.
                if elapsed_s >= window_s:
                    packet_outcome = "deferred"
                    break

            retransmission_counts[pkt.packet_id] = max(0, attempts - 1)

            if packet_outcome == "delivered":
                delivered.append(pkt.packet_id)
            elif packet_outcome == "deferred":
                deferred.append(pkt.packet_id)
            else:
                # MAX_ATTEMPTS exhausted without success.
                failed.append(pkt.packet_id)

        # Derive updated state from realized outcomes.
        remaining_window = max(0.0, window_s - elapsed_s)
        risk_score, risk_level = _derive_risk_level(delivered, deferred, failed)

        updated_link_state = link_state.model_copy(
            update={"remaining_window_s": remaining_window}
        )
        updated_mission_state = mission_state.model_copy(
            update={
                "comm_window_remaining_s": remaining_window,
                "risk_score": risk_score,
                "risk_level": risk_level,
            }
        )

        return SimulationResult(
            plan_id=plan.plan_id,
            delivered_packets=delivered,
            deferred_packets=deferred,
            failed_packets=failed,
            elapsed_time_s=elapsed_s,
            retransmission_counts=retransmission_counts,
            link_state=updated_link_state,
            mission_state=updated_mission_state,
        )
