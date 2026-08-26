from pydantic import BaseModel, Field
from .risk_level import RiskLevel
from .evidence_item import EvidenceItem


class AIRecommendation(BaseModel):
    """Structured output produced by the Granite AI agent.

    The agent reasons over pre-evaluated EvaluationResult objects and
    pre-computed LinkState / MissionState. It does not perform calculations.
    Evidence must cite only fields present in the provided state.

    Phase 4 trust notes
    -------------------
    ``risk_score`` and ``risk_level`` are ALWAYS rebound from the deterministic
    ``EvaluationResult`` for the recommended plan.  They are NEVER derived from
    AI self-reporting.  These fields are authoritative outputs of ``PlanEvaluator``.

    ``confidence`` is the provider's self-reported estimate.  For LLM providers it
    is an uncalibrated heuristic and should be presented to the operator with an
    advisory label.  For ``LocalRuleBasedProvider`` it is a deterministic
    heuristic derived from the risk-score gap between the best and second-best
    plan.  Neither value should be treated as a calibrated probability.

    ``confidence_semantics`` indicates how the confidence value was produced.
    Use this field to drive advisory labels in the UI rather than hardcoding
    provider-specific logic in the frontend.
    """

    recommended_plan_id: str = Field(description="plan_id of the recommended CandidatePlan")
    packet_actions: list[dict] = Field(
        description="Per-packet action decisions, e.g. [{'packet_id': 'p1', 'action': 'transmit', 'rank': 1}]"
    )
    risk_score: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Deterministic risk score from PlanEvaluator.  "
            "ALWAYS sourced from EvaluationResult, never from AI self-reporting."
        ),
    )
    risk_level: RiskLevel = Field(
        description=(
            "Categorical risk level from PlanEvaluator.  "
            "ALWAYS sourced from EvaluationResult, never from AI self-reporting."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Provider self-reported confidence [0, 1].  "
            "Advisory only — see confidence_semantics for interpretation guidance."
        ),
    )
    confidence_semantics: str = Field(
        default="heuristic",
        description=(
            "How the confidence value was produced.  "
            "'uncalibrated_llm' — LLM self-report, not a calibrated probability.  "
            "'heuristic' — deterministic heuristic (LocalRuleBasedProvider risk-gap).  "
            "Operators and UIs should present confidence with an advisory label, "
            "not as a precise probability."
        ),
    )
    reasoning: str = Field(description="Human-readable explanation of the recommendation")
    evidence: list[EvidenceItem] = Field(
        description="Structured evidence items supporting the recommendation"
    )
    alternative_plan_id: str | None = Field(
        default=None,
        description="plan_id of an alternative CandidatePlan if the recommended plan cannot be approved",
    )
