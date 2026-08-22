"""GCSI Backend API — routes for /plans."""

from fastapi import APIRouter, HTTPException

from .. import state
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult

router = APIRouter()


@router.post("/plans/generate", response_model=list[CandidatePlan])
def generate_plans() -> list[CandidatePlan]:
    """Generate all candidate transmission plans from the active scenario.

    Returns four strategies: baseline, deadline-first, mission-critical-first,
    and value-per-cost.  Raises 503 if no scenario has been loaded.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    return gen.generate(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )


@router.post("/plans/evaluate", response_model=EvaluationResult)
def evaluate_plan(plan: CandidatePlan) -> EvaluationResult:
    """Evaluate a candidate plan analytically.

    Accepts any CandidatePlan (baseline or AI-recommended) and returns
    expected/analytical metrics via PlanEvaluator.
    Raises 503 if no scenario has been loaded.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    ev = PlanEvaluator()
    return ev.evaluate(
        plan,
        state.active_link_state,
        state.active_scenario.mission_state,
    )
