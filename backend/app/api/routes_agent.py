"""GCSI Backend API — routes for /agent/recommend."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..agent.granite_agent import (
    GraniteAgent,
    GraniteAPIError,
    GraniteResponseError,
    EvidenceHallucinationError,
)
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.recommendation import AIRecommendation

router = APIRouter()


class RecommendRequest(BaseModel):
    """Optional overrides for the recommend call.

    If omitted, the agent fetches the active scenario's plans and evaluations.
    """
    plans: list | None = None
    evaluations: list | None = None


@router.post("/agent/recommend", response_model=AIRecommendation)
def recommend(req: RecommendRequest | None = None) -> AIRecommendation:
    """Request an AI recommendation for the active scenario.

    Generates candidate plans, evaluates them deterministically, then asks
    Granite to reason over the results and recommend a plan.

    Raises 503 if no scenario has been loaded.
    Raises 502 if the Granite API is unavailable.
    Raises 422 if the AI response is invalid or evidence is hallucinated.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Always regenerate fresh plans and evaluations from the current state.
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

    agent = GraniteAgent()
    try:
        return agent.recommend(
            state.active_link_state,
            state.active_scenario.mission_state,
            plans,
            evaluations,
        )
    except GraniteAPIError as exc:
        raise HTTPException(status_code=502, detail=f"Granite API unavailable: {exc}") from exc
    except (GraniteResponseError, EvidenceHallucinationError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid AI response: {exc}") from exc
