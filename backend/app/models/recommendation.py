from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field
from .risk_level import RiskLevel
from .evidence_item import EvidenceItem


class ConfidenceSemantics(str, Enum):
    """Typed classification of how the confidence value was produced.

    Trust rules
    -----------
    - The backend assigns this; provider-returned JSON must NOT override it.
    - The safest default is ``unspecified_uncalibrated``, NOT ``heuristic``.

    Values
    ------
    heuristic
        Deterministic heuristic, e.g. ``LocalRuleBasedProvider`` risk-gap formula.
        The value is reproducible given the same inputs.

    uncalibrated_llm
        LLM self-reported confidence; NOT a calibrated probability.
        External providers (Granite, Gemini, Ollama, etc.) receive this label.

    unspecified_uncalibrated
        Fail-safe default when provenance cannot be confidently classified.
        Used for unknown provider categories and as the model default.
        Prevents an external LLM path that forgets to set semantics from
        appearing as a deterministic heuristic.
    """

    heuristic = "heuristic"
    """Deterministic risk-gap heuristic (LocalRuleBasedProvider)."""

    uncalibrated_llm = "uncalibrated_llm"
    """LLM self-report — not a calibrated probability."""

    unspecified_uncalibrated = "unspecified_uncalibrated"
    """Fail-safe default — uncalibrated, provenance unknown."""


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
    provider-specific logic in the frontend.  The backend assigns this; providers
    must NOT be able to override it.

    Phase 4.1 trust notes
    ---------------------
    ``confidence_semantics`` is now a typed enum (``ConfidenceSemantics``).
    The fail-safe default is ``unspecified_uncalibrated``, NOT ``heuristic``.
    This prevents an external LLM path that forgets to set semantics from
    incorrectly appearing as a deterministic heuristic.
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
    confidence_semantics: ConfidenceSemantics = Field(
        default=ConfidenceSemantics.unspecified_uncalibrated,
        description=(
            "How the confidence value was produced.  "
            "'heuristic' — deterministic risk-gap (LocalRuleBasedProvider).  "
            "'uncalibrated_llm' — LLM self-report, not a calibrated probability.  "
            "'unspecified_uncalibrated' — fail-safe default.  "
            "Assigned by the backend; providers must not override this.  "
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
