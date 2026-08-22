"""GCSI Backend API — routes for /simulate and /simulate/what-if."""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..models.candidate_plan import CandidatePlan
from ..models.simulation_result import SimulationResult
from ..simulation.transmission_sim import TransmissionSimulator

router = APIRouter()


class SimulateRequest(BaseModel):
    plan_id: str
    seed: int | None = None


class WhatIfRequest(BaseModel):
    plan: CandidatePlan
    seed: int | None = None


def _run_simulation(
    plan: CandidatePlan,
    seed: int | None,
) -> SimulationResult:
    """Shared simulation logic — used by both /simulate and /approve routes.

    Never call POST /simulate internally; always call this function directly.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    sim = TransmissionSimulator()
    return sim.simulate(
        plan,
        state.active_link_state,
        state.active_scenario.mission_state,
        seed=seed,
    )


@router.post("/simulate", response_model=SimulationResult)
def simulate(req: SimulateRequest) -> SimulationResult:
    """Run a stochastic simulation of a named plan and update server state.

    Looks up the plan by plan_id from the candidate generator, runs the
    simulation, and mutates the active link/mission state with realized outcomes.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Find the requested plan among generated candidates.
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

    result = _run_simulation(plan, req.seed)

    # Mutate server state with realized outcomes.
    state.active_link_state = result.link_state
    state.active_scenario = state.active_scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    return result


@router.post("/simulate/what-if", response_model=SimulationResult)
def simulate_what_if(req: WhatIfRequest) -> SimulationResult:
    """Run a non-mutating what-if simulation for any plan.

    Does NOT update server state.  The plan is accepted directly in the
    request body so any custom or AI-recommended plan can be simulated.
    """
    return _run_simulation(req.plan, req.seed)
