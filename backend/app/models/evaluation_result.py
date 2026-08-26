from pydantic import BaseModel, Field
from .risk_level import RiskLevel


class EvaluationResult(BaseModel):
    """Expected/analytical metrics for a CandidatePlan.

    All fields are derived deterministically from link-level and packet-level
    telecom quantities. No stochastic draws are made to produce these values.
    This is the planning layer output — it answers "what do we expect to happen?"

    deferred_packets contains packet_id strings for packets that could not fit
    within the communication window. Packets are never silently dropped.
    """

    plan_id: str
    mission_value: float = Field(ge=0.0, description="Weighted sum of criticality * mission_relevance for non-deferred packets")
    critical_packets_delivered: int = Field(ge=0, description="Count of non-deferred packets meeting the criticality threshold")
    total_critical_packets: int = Field(ge=0, description="Total count of critical packets in the plan")
    deadline_misses: int = Field(ge=0, description="Count of packets whose expected delivery time exceeds their deadline")
    avg_packet_delay_s: float = Field(ge=0.0, description="Mean expected delivery timestamp across non-deferred packets")
    bandwidth_utilization: float = Field(ge=0.0, le=1.0, description="Fraction of available window capacity used [0, 1]")
    retransmission_overhead: float = Field(
        ge=0.0,
        description=(
            "Expected total retransmission overhead in SECONDS, analytically derived.  "
            "Formula: sum over delivered packets of (1/p_success - 1) × tx_time_s.  "
            "This is the expected extra transmission time beyond single-attempt baselines.  "
            "It is NOT a dimensionless attempt-count ratio."
        ),
    )
    risk_score: float = Field(ge=0.0, le=1.0, description="Plan risk scalar [0, 1]")
    risk_level: RiskLevel
    deferred_packets: list[str] = Field(
        default_factory=list,
        description="packet_id values of packets deferred due to window exhaustion",
    )
    # Risk score breakdown components (Feature 2)
    deadline_miss_rate: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Fraction of packets that missed their deadline [0, 1]",
    )
    critical_deficit: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Fraction of critical packets not delivered [0, 1]",
    )
    window_pressure: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Fraction of communication window budget consumed [0, 1]",
    )
