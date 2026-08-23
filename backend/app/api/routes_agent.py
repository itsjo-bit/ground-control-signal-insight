"""GCSI Backend API — routes for /agent/recommend.

The route is provider-agnostic: it delegates to whatever AI provider is
currently configured (IBM Granite, Ollama, or local rule-based).

Provider selection is handled by :func:`~backend.app.agent.provider_factory.get_provider`.
The response includes a ``provider`` field indicating which provider was used.
"""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..agent.base_provider import AIHallucinationError, AIProviderError, AIResponseError
from ..agent.provider_factory import get_provider
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.recommendation import AIRecommendation

router = APIRouter()


class RecommendRequest(BaseModel):
    """Optional overrides for the recommend call (reserved for future use)."""
    plans: list | None = None
    evaluations: list | None = None


class RecommendResponse(BaseModel):
    """Wraps AIRecommendation with provider metadata."""
    provider: str
    recommendation: AIRecommendation


@router.post("/agent/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest | None = None) -> RecommendResponse:  # noqa: ARG001
    """Request an AI recommendation for the active scenario.

    Generates candidate plans, evaluates them deterministically, then asks
    the configured AI provider to reason over the results and recommend a plan.

    The provider is selected automatically:
    - IBM Granite if ``GCSI_GRANITE_API_KEY`` is set.
    - Ollama if ``GCSI_OLLAMA_ENABLED=true`` and the server is reachable.
    - Local rule-based provider otherwise (default, no credentials required).

    Raises:
        503: No active scenario loaded.
        502: AI provider is unavailable (Granite API down, etc.).
        422: AI response is invalid or evidence is hallucinated.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Always regenerate fresh plans and evaluations from current state.
    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )

    ev = PlanEvaluator()
    evaluations = [
        ev.evaluate(plan, state.active_link_state, state.active_scenario.mission_state)
        for plan in plans
    ]

    provider = get_provider()
    try:
        recommendation = provider.recommend(
            state.active_link_state,
            state.active_scenario.mission_state,
            plans,
            evaluations,
        )
    except AIProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI provider '{provider.provider_name}' unavailable: {exc}",
        ) from exc
    except (AIResponseError, AIHallucinationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid AI response from '{provider.provider_name}': {exc}",
        ) from exc

    return RecommendResponse(provider=provider.provider_name, recommendation=recommendation)
