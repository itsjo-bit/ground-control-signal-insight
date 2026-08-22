"""GCSI Backend API — routes for /approve."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..models.simulation_result import SimulationResult
from .routes_simulate import _run_simulation

router = APIRouter()


class ApproveRequest(BaseModel):
    plan_id: str
    operator_notes: str = ""


class ApproveResponse(BaseModel):
    status: str
    simulation_result: SimulationResult


@router.post("/approve", response_model=ApproveResponse)
def approve_plan(req: ApproveRequest) -> ApproveResponse:
    """Approve a plan and execute it via simulation.

    Calls _run_simulation() directly — does NOT call POST /simulate internally.
    Updates server state with realized outcomes.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Resolve plan_id to a CandidatePlan.
    from ..config import SchedulerWeights
    from ..candidate_generator.generator import CandidateGenerator

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )

    plan = next((p for p in plans if p.plan_id == req.plan_id), None)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan '{req.plan_id}' not found. Available: {[p.plan_id for p in plans]}",
        )

    # Call the shared simulation function directly — never call POST /simulate.
    result = _run_simulation(plan, seed=None)

    # Mutate server state with realized outcomes.
    state.active_link_state = result.link_state
    state.active_scenario = state.active_scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    return ApproveResponse(status="approved", simulation_result=result)
