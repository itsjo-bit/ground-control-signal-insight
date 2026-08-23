"""GCSI Backend API — routes for /plans."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state
from ..config import RiskWeights, SchedulerWeights
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


class WhatIfEvalRequest(BaseModel):
    """Request body for the what-if evaluation endpoint."""
    snr_db: float | None = None
    ber: float | None = None


class WhatIfEvalResponse(BaseModel):
    evaluations: list[EvaluationResult]
    risk_weights: dict


@router.post("/plans/what-if", response_model=WhatIfEvalResponse)
def what_if_evaluate(req: WhatIfEvalRequest) -> WhatIfEvalResponse:
    """Evaluate all 4 candidate plans under hypothetical link conditions.

    Accepts optional overrides for snr_db and/or ber.  Returns 4 EvaluationResult
    objects — one per strategy — computed against the overridden link state.
    Does NOT mutate the active scenario or link state (pure read-only preview).
    Raises 503 if no scenario has been loaded.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    from ..telecom.engine import TelecomEngine
    from ..config import GCSIConfig

    # Build a modified link_inputs dict to re-derive the link state.
    cfg = GCSIConfig()
    link_inputs = dict(state.active_scenario.link_inputs)
    if req.snr_db is not None:
        link_inputs["snr_db"] = req.snr_db
    if req.ber is not None:
        link_inputs["ber"] = req.ber

    # Re-derive the link state from the (potentially overridden) inputs.
    engine = TelecomEngine(cfg)
    try:
        hypothetical_link = engine.compute(link_inputs)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid link inputs: {exc}") from exc

    # Generate the 4 candidate plans against the ACTUAL scenario packets
    # (packet list doesn't change, only link quality changes).
    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        state.active_scenario.packets,
        hypothetical_link,
        state.active_scenario.mission_state,
        weights,
    )

    ev = PlanEvaluator()
    evals = [
        ev.evaluate(plan, hypothetical_link, state.active_scenario.mission_state)
        for plan in plans
    ]

    rw = RiskWeights()
    return WhatIfEvalResponse(
        evaluations=evals,
        risk_weights={
            "w_deadline_miss": rw.w_deadline_miss,
            "w_critical_deficit": rw.w_critical_deficit,
            "w_window_pressure": rw.w_window_pressure,
        },
    )
