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
from typing import Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation


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
        """Generate a plan recommendation (legacy / four-plan path).

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
