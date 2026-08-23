"""GraniteProvider — adapts the existing GraniteAgent to the BaseAIProvider interface.

This thin wrapper re-raises GraniteAgent's typed exceptions as the canonical
``AIProviderError`` / ``AIResponseError`` / ``AIHallucinationError`` hierarchy,
so the route layer is provider-agnostic.

The existing ``GraniteAgent`` class is preserved unchanged; this adapter is the
only coupling point between the agent implementation and the provider interface.
"""

from __future__ import annotations

from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from .base_provider import AIHallucinationError, AIProviderError, AIResponseError, BaseAIProvider
from .granite_agent import (
    EvidenceHallucinationError,
    GraniteAPIError,
    GraniteAgent,
    GraniteResponseError,
)


class GraniteProvider(BaseAIProvider):
    """IBM Granite recommendation provider.

    Wraps :class:`GraniteAgent` and maps its exception types to the canonical
    provider hierarchy so the route layer is provider-agnostic.

    Args:
        agent: Optional pre-configured :class:`GraniteAgent` instance.
               Defaults to ``GraniteAgent()`` (reads env vars).
    """

    def __init__(self, agent: GraniteAgent | None = None) -> None:
        self._agent = agent or GraniteAgent()

    @property
    def provider_name(self) -> str:
        return "Granite"

    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
    ) -> AIRecommendation:
        """Delegate to :class:`GraniteAgent` and map exceptions.

        Raises:
            AIProviderError:      If the Granite API is unavailable.
            AIResponseError:      If the Granite response is malformed/invalid.
            AIHallucinationError: If evidence cites a non-existent field.
        """
        try:
            return self._agent.recommend(link_state, mission_state, plans, evaluations)
        except GraniteAPIError as exc:
            raise AIProviderError(str(exc)) from exc
        except GraniteResponseError as exc:
            raise AIResponseError(str(exc)) from exc
        except EvidenceHallucinationError as exc:
            raise AIHallucinationError(str(exc)) from exc
