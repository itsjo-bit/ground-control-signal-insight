"""Abstract provider interface for AI recommendation agents.

All AI providers — IBM Granite, Ollama, or local rule-based — must implement
this protocol so the API route is provider-agnostic.

Design constraints
------------------
- Providers receive only pre-computed deterministic facts (LinkState,
  MissionState, EvaluationResult, CandidatePlan).  They never perform
  telecom calculations.
- Providers must raise typed exceptions on failure; they must never silently
  fabricate recommendations.
- The route layer selects the provider once per request based on
  configuration; the provider implementation is fully encapsulated.

Two recommendation paths
------------------------
recommend()
    Legacy / local path.  Receives full CandidatePlan objects and
    EvaluationResult objects.  Used by LocalRuleBasedProvider and legacy
    scenarios.

recommend_from_summaries()
    External Stage-2 path (v2/v3 with Granite/Gemini/Ollama).  Receives
    compact, provenance-blind Stage2PlanSummary objects.  The external LLM
    never sees real plan identifiers, strategy names, or packet lists.
    Base implementation raises NotImplementedError.

Typed exceptions
----------------
AIProviderError        — raised when the underlying provider (API or model) is
                         unavailable, times out, or returns a non-200 response.
AIResponseError        — raised when the provider returns output that cannot be
                         parsed or validated into an AIRecommendation.
AIHallucinationError   — raised when the provider cites a field that does not
                         exist in the provided state models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation

if TYPE_CHECKING:
    from ..agent.stage2_blinding import Stage2PlanSummary


# ---------------------------------------------------------------------------
# Shared typed exceptions  (all providers use the same hierarchy)
# ---------------------------------------------------------------------------


class AIProviderError(Exception):
    """Provider unavailable, timed out, or returned a non-200 response."""


class AIResponseError(Exception):
    """Provider returned output that is malformed, incomplete, or invalid."""


class AIHallucinationError(Exception):
    """Provider cited a field that does not exist in the provided state models."""


class AIPrioritizationError(Exception):
    """Raised when AI candidate prioritization fails validation.

    Distinct from ``AIResponseError`` so callers can distinguish between a
    malformed recommendation (existing flow) and a malformed prioritization
    (Phase 2C flow).
    """


class RecommendationFinalizationError(Exception):
    """Raised when a provider recommendation cannot be authoritatively finalized.

    This is a fail-closed error.  It is raised by ``finalize_recommendation()``
    when the recommended plan ID cannot be bound to an authoritative
    ``CandidatePlan`` and ``EvaluationResult``.

    The route layer must NOT return the unfinalized recommendation.  It must
    trigger the ``LocalRuleBasedProvider`` fallback or, if that also fails,
    return HTTP 502.

    Reason codes (``reason`` attribute)
    ------------------------------------
    UNKNOWN_RECOMMENDED_PLAN
        ``recommended_plan_id`` does not exist in the authoritative plan set.

    MISSING_EVALUATION
        Plan exists but no ``EvaluationResult`` for that plan was found.

    INVALID_ALTERNATIVE_PLAN
        Internal use — alternative plan policy is soft-drop; not raised.

    UNFINALIZABLE_RECOMMENDATION
        Generic catch-all for any other condition that prevents finalization.
    """

    UNKNOWN_RECOMMENDED_PLAN = "UNKNOWN_RECOMMENDED_PLAN"
    MISSING_EVALUATION = "MISSING_EVALUATION"
    INVALID_ALTERNATIVE_PLAN = "INVALID_ALTERNATIVE_PLAN"
    UNFINALIZABLE_RECOMMENDATION = "UNFINALIZABLE_RECOMMENDATION"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def __repr__(self) -> str:
        return f"RecommendationFinalizationError(reason={self.reason!r}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseAIProvider(ABC):
    """Common interface for all AI recommendation providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short human-readable identifier, e.g. 'Granite', 'Ollama', 'Local'."""

    @abstractmethod
    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
        *,
        anomalies: list[AnomalyEvent] | None = None,
    ) -> AIRecommendation:
        """Generate a plan recommendation (legacy / local path).

        Receives full CandidatePlan objects and EvaluationResult objects.
        Used by LocalRuleBasedProvider and legacy scenarios.

        Args:
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            plans:         All candidate plans (baseline + alternatives).
            evaluations:   Deterministic evaluation results for each plan.
            anomalies:     Optional list of active spacecraft anomaly events
                           (Phase 2A).  Empty / None for legacy scenarios.

        Returns:
            A validated :class:`AIRecommendation`.

        Raises:
            AIProviderError:      If the underlying provider is unavailable.
            AIResponseError:      If the provider response is malformed/invalid.
            AIHallucinationError: If evidence cites a non-existent field.
        """

    def recommend_from_summaries(
        self,
        summaries: list[Stage2PlanSummary],
        link_state: LinkState,
        mission_state: MissionState,
        anomalies: list[AnomalyEvent] | None = None,
    ) -> AIRecommendation:
        """Generate a recommendation from compact provenance-blind summaries.

        This is the external Stage-2 path used by Granite, Gemini, and Ollama.
        The provider receives only compact metric summaries (no real plan IDs,
        no strategy names, no packet lists) and returns an opaque option alias.

        The recommended_plan_id in the returned AIRecommendation is an OPTION
        alias (e.g. ``"OPTION-C"``), not a real plan ID.  The caller (routes_agent)
        is responsible for mapping the alias back to the real plan identity and
        rebinding authoritative risk/packet data.

        Args:
            summaries:     Compact provenance-blind plan summaries.  Each
                           summary contains only objective metrics and an opaque
                           option alias (``OPTION-A``, ``OPTION-B``, ...).
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            anomalies:     Active anomaly events.

        Returns:
            An :class:`AIRecommendation` where ``recommended_plan_id`` is an
            opaque option alias.  ``risk_score``, ``risk_level``, and
            ``packet_actions`` will be rebound by the trusted backend after
            alias resolution.

        Raises:
            NotImplementedError:  If the provider does not implement this path.
            AIProviderError:      If the underlying provider is unavailable.
            AIResponseError:      If the provider response is malformed/invalid.
            AIHallucinationError: If evidence cites an unknown field.
        """
        raise NotImplementedError(
            f"Provider '{self.provider_name}' does not implement "
            "recommend_from_summaries(). Override this method to support "
            "the compact Stage-2 provenance-blind recommendation path."
        )

    def prioritize_candidates(
        self,
        candidates: Sequence[CandidateSummary],
        link_state: LinkState,
        mission_state: MissionState,
        anomalies: Sequence[AnomalyEvent] | None = None,
        *,
        distance_km: float | None = None,
    ) -> CandidatePrioritization:
        """Rank a bounded set of data-product candidates by mission importance.

        Phase 2C entry point.  Providers that implement genuine AI reasoning
        override this method.  The base implementation raises
        ``NotImplementedError`` — callers must handle this and fall back to
        :meth:`recommend` or a deterministic ranking if needed.

        Args:
            candidates:    Pre-filtered list of :class:`CandidateSummary` objects.
                           The count is bounded by ``GCSI_AI_MAX_CANDIDATES``.
            link_state:    Current link snapshot (window, BER, goodput).
            mission_state: Current mission snapshot (phase, event, risk).
            anomalies:     Active anomaly events for contextual reasoning.
            distance_km:   Spacecraft distance from Earth in km (Phase 2E-C3-E).
                           Providers may use this to add geometry context.

        Returns:
            A validated :class:`CandidatePrioritization` with ranked products.

        Raises:
            NotImplementedError:     If the provider does not support Phase 2C.
            AIProviderError:         If the underlying provider is unavailable.
            AIPrioritizationError:   If the response fails validation.
        """
        raise NotImplementedError(
            f"Provider '{self.provider_name}' does not implement prioritize_candidates(). "
            "Override this method or use the recommend() path instead."
        )
