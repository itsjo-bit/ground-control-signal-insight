from __future__ import annotations
from pydantic import BaseModel, Field


class SimulationModelMetadata(BaseModel):
    """Machine-readable description of the current simulator's retransmission model.

    Phase 3 — Part D: These fields make the simulator's model identity and
    limitations self-describing in every API response.

    Abstract independent-attempt retransmission model:
    ---------------------------------------------------
    * Each packet transmission is a Bernoulli trial with probability
      ``p = packet_success_probability(ber, size_bits)``.
    * On failure, another attempt is drawn immediately within the same
      communication window.  There is no explicit ACK round-trip wait.
    * Window exhaustion mid-retry causes the packet to be deferred.
    * Protocol overhead is represented only through ``protocol_efficiency``
      in the link goodput formula.

    What this model does NOT include:
    -----------------------------------
    * Explicit ACK/NACK round-trip timing (stop-and-wait ARQ).
    * CCSDS protocol stack behavior.
    * Propagation-aware retransmission scheduling.
    * Adaptive coding or modulation.

    ``elapsed_time_s`` semantics:
    ------------------------------
    ``SimulationResult.elapsed_time_s`` is the SUM of actual transmission-
    attempt durations consumed within the communication window.  It does NOT
    represent Earth receive wall-clock completion time including propagation.
    Do not interpret it as end-to-end delivery latency over deep space.

    ``expected_transmission_cost`` semantics:
    ------------------------------------------
    ``expected_transmission_cost = tx_time / p_success`` is the expected
    consumed transmission-window cost under the abstract independent attempt
    model.  It is NOT physical end-to-end packet delivery latency over deep
    space.
    """

    simulation_model: str = Field(
        default="abstract_packet_retransmission",
        description=(
            "Abstract independent-attempt retransmission model.  "
            "Each attempt is an independent Bernoulli trial at p_success.  "
            "Failed attempts may be retried immediately within the simulation window."
        ),
    )
    ack_timing_mode: str = Field(
        default="not_modeled",
        description=(
            "ACK/NACK round-trip timing is not modeled.  "
            "Retransmissions do not wait for a deep-space ACK before next attempt.  "
            "This is NOT stop-and-wait ARQ."
        ),
    )
    propagation_delay_included_in_elapsed_time: bool = Field(
        default=False,
        description=(
            "False — elapsed_time_s measures sum of transmission-attempt durations "
            "only.  One-way propagation delay (~180 s at 54 Mkm) is NOT added.  "
            "See GET /state for propagation_delay_s."
        ),
    )


class SimulationResult(BaseModel):
    """Realized outcomes from stochastic transmission simulation.

    Fields represent actual packet delivery outcomes drawn via Bernoulli trials
    against packet_success_probability. This is the execution layer output —
    it answers "what actually happened?"

    Must NOT be used as input to PlanEvaluator. EvaluationResult (expected/analytical)
    and SimulationResult (realized/stochastic) are separate layers.

    ``elapsed_time_s`` semantics (Phase 3):
        Sum of actual transmission-attempt durations consumed within the
        communication window.  Does NOT include one-way propagation delay.
        See ``simulation_model`` for the full model identity.

    ``retransmission_counts`` semantics:
        Integer counts of realized extra-attempt retransmissions per packet_id.
        The corresponding expected time cost can be computed analytically as
        ``(1 / p_success - 1) * tx_time`` — see ``EvaluationResult.retransmission_overhead``.
    """

    plan_id: str
    delivered_packets: list[str] = Field(
        default_factory=list,
        description="packet_id values of packets successfully delivered",
    )
    deferred_packets: list[str] = Field(
        default_factory=list,
        description="packet_id values of packets deferred because the window was exhausted",
    )
    failed_packets: list[str] = Field(
        default_factory=list,
        description="packet_id values of packets that failed all delivery attempts",
    )
    elapsed_time_s: float = Field(
        ge=0.0,
        description=(
            "Sum of actual transmission-attempt durations in seconds, "
            "consumed within the communication window.  "
            "Does NOT include free-space propagation delay.  "
            "This is NOT end-to-end delivery latency over deep space."
        ),
    )
    retransmission_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Realized number of retransmission attempts per packet_id",
    )
    # Updated state after simulation — imported inline to avoid circular imports
    link_state: "LinkState"  # type: ignore[name-defined]  # noqa: F821
    mission_state: "MissionState"  # type: ignore[name-defined]  # noqa: F821

    # Phase 3 — Part D: machine-readable simulation model metadata
    simulation_model: SimulationModelMetadata = Field(
        default_factory=SimulationModelMetadata,
        description=(
            "Machine-readable description of the retransmission model used.  "
            "See SimulationModelMetadata for field-by-field semantics."
        ),
    )


# Resolve forward references after LinkState and MissionState are defined
from .link_state import LinkState  # noqa: E402
from .mission_state import MissionState  # noqa: E402

SimulationResult.model_rebuild()
