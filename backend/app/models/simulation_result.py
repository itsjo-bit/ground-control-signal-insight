from __future__ import annotations
from pydantic import BaseModel, Field


class SimulationResult(BaseModel):
    """Realized outcomes from stochastic transmission simulation.

    Fields represent actual packet delivery outcomes drawn via Bernoulli trials
    against packet_success_probability. This is the execution layer output —
    it answers "what actually happened?"

    Must NOT be used as input to PlanEvaluator. EvaluationResult (expected/analytical)
    and SimulationResult (realized/stochastic) are separate layers.
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
        description="Actual elapsed transmission time including realized retransmission delays",
    )
    retransmission_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Realized number of retransmission attempts per packet_id",
    )
    # Updated state after simulation — imported inline to avoid circular imports
    link_state: "LinkState"  # type: ignore[name-defined]  # noqa: F821
    mission_state: "MissionState"  # type: ignore[name-defined]  # noqa: F821


# Resolve forward references after LinkState and MissionState are defined
from .link_state import LinkState  # noqa: E402
from .mission_state import MissionState  # noqa: E402

SimulationResult.model_rebuild()
