"""GCSI Backend API — routes for /simulate and /simulate/what-if.

Phase 4 trust boundaries
-------------------------
POST /simulate
    Looks up a plan by plan_id (server-generated).  Reconstructs authoritatively.
    State-mutating: invalidates issued-plan registry.

POST /simulate/what-if
    Accepts a client CandidatePlan as an ID/order transport.
    All packet facts are reconstructed from the active scenario.
    Non-mutating: does NOT invalidate issued-plan registry.
"""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..domain.plan_integrity import PlanIntegrityError, PlanSource, reconstruct_authoritative_plan
from ..models.candidate_plan import CandidatePlan
from ..models.simulation_result import SimulationResult
from ..simulation.transmission_sim import TransmissionSimulator
from .routes_plans import _effective_packets

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

    Phase 4: After simulation, issued plans are invalidated (state has mutated).
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    from ..config import SchedulerWeights
    from ..candidate_generator.generator import CandidateGenerator

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        _effective_packets(state.active_scenario),
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

    # Invalidate issued plans — state has mutated, old plans may be stale.
    state.invalidate_issued_plans(reason=f"state-mutating simulation: plan={req.plan_id}")

    return result


@router.post("/simulate/what-if", response_model=SimulationResult)
def simulate_what_if(req: WhatIfRequest) -> SimulationResult:
    """Run a non-mutating what-if simulation for any plan.

    Phase 4: The client plan is treated only as an ID/order transport.
    All packet facts are reconstructed from the active scenario before
    simulation.  Unknown or duplicate IDs are rejected (HTTP 422).

    Does NOT update server state.
    Does NOT invalidate the issued-plan registry.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Reconstruct packet facts authoritatively.
    try:
        trace = reconstruct_authoritative_plan(
            req.plan,
            state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
    except PlanIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _run_simulation(trace.reconstructed_plan, req.seed)
