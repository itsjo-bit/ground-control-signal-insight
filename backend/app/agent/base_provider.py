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

from ..models.candidate_plan import CandidatePlan
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
    ) -> AIRecommendation:
        """Generate a plan recommendation.

        Args:
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            plans:         All candidate plans (baseline + alternatives).
            evaluations:   Deterministic evaluation results for each plan.

        Returns:
            A validated :class:`AIRecommendation`.

        Raises:
            AIProviderError:      If the underlying provider is unavailable.
            AIResponseError:      If the provider response is malformed/invalid.
            AIHallucinationError: If evidence cites a non-existent field.
        """
