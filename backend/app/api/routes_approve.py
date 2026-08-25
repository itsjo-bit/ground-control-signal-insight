"""GCSI Backend API — routes for /approve.

Phase 2E-D3 (P0-1): /approve now accepts an optional full ``CandidatePlan`` in
the request body.  When ``plan`` is supplied it is used *directly* — the backend
never regenerates the plan from ``_effective_packets()``.  This guarantees that
the exact AI-ordered plan the operator saw and approved is the plan that enters
``TransmissionSimulator``.

Backward compatibility is fully preserved:
- Requests that only supply ``plan_id`` (legacy form) continue to work as before.
- When both ``plan`` and ``plan_id`` are supplied, ``plan`` is authoritative.
- ``/approve/custom`` behaviour is unchanged.
"""

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..models.candidate_plan import CandidatePlan
from ..models.simulation_result import SimulationResult
from .routes_plans import _effective_packets
from .routes_simulate import _run_simulation

router = APIRouter()


class ApproveRequest(BaseModel):
    """Approve a named plan by ID, optionally supplying the full plan object.

    Phase 2E-D3 (P0-1) extension:

    ``plan`` (optional): The exact ``CandidatePlan`` the operator approved,
    including its AI-ordered packet list.  When present, this plan is passed
    directly to ``_run_simulation()`` — no regeneration occurs.

    ``plan_id`` (required for legacy compat): Must still be present so existing
    callers (e.g. legacy frontend builds) continue to work.  It is used to look
    up the plan *only* when ``plan`` is ``None``.

    Priority rule: if both ``plan`` and ``plan_id`` are supplied, ``plan`` wins.
    """
    plan_id: str
    plan: CandidatePlan | None = None
    operator_notes: str = ""


class ApproveCustomRequest(BaseModel):
    plan: CandidatePlan
    operator_notes: str = ""


class ApproveResponse(BaseModel):
    status: str
    simulation_result: SimulationResult


@router.post("/approve", response_model=ApproveResponse)
def approve_plan(req: ApproveRequest) -> ApproveResponse:
    """Approve a plan and execute it via simulation.

    Phase 2E-D3 (P0-1):
    When ``req.plan`` is supplied it is used directly — the backend does NOT
    regenerate the plan from ``_effective_packets()``.  This preserves the
    AI-ordered packet sequence that was shown to the operator.

    Legacy path (``req.plan is None``): regenerates plans from
    ``_effective_packets()`` and looks up by ``plan_id``, matching the
    original pre-D3 behaviour exactly.

    Calls _run_simulation() directly — does NOT call POST /simulate internally.
    Updates server state with realized outcomes.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    if req.plan is not None:
        # ── Phase 2E-D3 path: use the supplied plan directly ─────────────────
        # The frontend sends the exact CandidatePlan it built from the AI
        # recommendation — no regeneration, no packet-order loss.
        plan = req.plan
    else:
        # ── Legacy path: regenerate and look up by plan_id ───────────────────
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

    # Call the shared simulation function directly — never call POST /simulate.
    result = _run_simulation(plan, seed=None)

    # Mutate server state with realized outcomes.
    state.active_link_state = result.link_state
    state.active_scenario = state.active_scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    return ApproveResponse(status="approved", simulation_result=result)


@router.post("/approve/custom", response_model=ApproveResponse)
def approve_custom_plan(req: ApproveCustomRequest) -> ApproveResponse:
    """Approve and simulate an operator-supplied custom plan.

    Accepts a full CandidatePlan in the request body — used when the operator
    has manually reordered packets (drag-to-reorder).  Does NOT look up by
    plan_id; runs the plan as-is.  Updates server state with realized outcomes.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    result = _run_simulation(req.plan, seed=None)

    state.active_link_state = result.link_state
    state.active_scenario = state.active_scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    return ApproveResponse(status="approved", simulation_result=result)
