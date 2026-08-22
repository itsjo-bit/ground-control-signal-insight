from pydantic import BaseModel, Field
from .risk_level import RiskLevel
from .evidence_item import EvidenceItem


class AIRecommendation(BaseModel):
    """Structured output produced by the Granite AI agent.

    The agent reasons over pre-evaluated EvaluationResult objects and
    pre-computed LinkState / MissionState. It does not perform calculations.
    Evidence must cite only fields present in the provided state.
    """

    recommended_plan_id: str = Field(description="plan_id of the recommended CandidatePlan")
    packet_actions: list[dict] = Field(
        description="Per-packet action decisions, e.g. [{'packet_id': 'p1', 'action': 'transmit', 'rank': 1}]"
    )
    risk_score: float = Field(ge=0.0, le=1.0, description="Agent-assessed risk score [0, 1]")
    risk_level: RiskLevel = Field(description="Categorical risk level derived from risk_score")
    confidence: float = Field(ge=0.0, le=1.0, description="Agent confidence in recommendation [0, 1]")
    reasoning: str = Field(description="Human-readable explanation of the recommendation")
    evidence: list[EvidenceItem] = Field(
        description="Structured evidence items supporting the recommendation"
    )
    alternative_plan_id: str | None = Field(
        default=None,
        description="plan_id of an alternative CandidatePlan if the recommended plan cannot be approved",
    )
