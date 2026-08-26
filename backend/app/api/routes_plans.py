"""GCSI Backend API — routes for /plans."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state
from ..config import RiskWeights, SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.packet import Packet
from ..telecom.what_if import WhatIfLinkContext, apply_link_what_if

router = APIRouter()


def _effective_packets(scenario) -> list[Packet]:
    """Return the packet list to use for scheduling.

    Priority:
    1. If ``scenario.packets`` is non-empty, use it directly (legacy path).
    2. Otherwise bridge ``scenario.data_products`` to Packet objects (v2 path).

    This means a v2 scenario that carries data_products but no legacy packets
    will automatically flow through the full scheduling and evaluation pipeline
    without any changes to the scheduler, evaluator, or candidate generator.
    """
    if scenario.packets:
        return scenario.packets
    return data_products_to_packets(scenario.data_products)


@router.post("/plans/generate", response_model=list[CandidatePlan])
def generate_plans() -> list[CandidatePlan]:
    """Generate all candidate transmission plans from the active scenario.

    Supports both legacy (``packets``) and v2 (``data_products``) scenarios.
    When ``packets`` is empty and ``data_products`` is non-empty, the data
    products are bridged to Packet objects transparently.

    Returns four strategies: baseline, deadline-first, mission-critical-first,
    and value-per-cost.  Raises 503 if no scenario has been loaded.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    return gen.generate(
        _effective_packets(state.active_scenario),
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
    """Request body for the what-if evaluation endpoint.

    BER validation (Phase 3):
        When ``ber`` is supplied it must be in [0, 0.5].  Values outside
        this range have no physical meaning for BPSK/AWGN and are rejected
        with HTTP 422.  NaN and ±infinity are also rejected.

    SNR validation (Phase 3):
        ``snr_db`` must be finite when supplied.
    """

    snr_db: float | None = None
    ber: float | None = None


class WhatIfEvalResponse(BaseModel):
    """Response body for ``POST /plans/what-if``.

    Phase 3 additions (backwards-compatible):

    ``what_if_context``
        A :class:`~backend.app.telecom.what_if.WhatIfLinkContext` record that
        explains exactly what the backend evaluated.  Operators and reviewers
        can inspect this to confirm which overrides were applied and what
        effective values were used.

    ``hypothetical_link_state``
        The :class:`~backend.app.models.link_state.LinkState` that was passed
        to ``PlanEvaluator``.  This is the authoritative evaluated link state
        for this what-if request.  The frontend must use this value rather than
        recalculating link metrics client-side.

    ``evaluations``
        One ``EvaluationResult`` per candidate strategy.

    ``risk_weights``
        The ``RiskWeights`` scalars used in ``PlanEvaluator.risk_score``.
    """

    what_if_context: WhatIfLinkContext
    hypothetical_link_state: LinkState
    evaluations: list[EvaluationResult]
    risk_weights: dict


@router.post("/plans/what-if", response_model=WhatIfEvalResponse)
def what_if_evaluate(req: WhatIfEvalRequest) -> WhatIfEvalResponse:
    """Evaluate all candidate plans under hypothetical link conditions.

    Accepts optional overrides for ``snr_db`` and/or ``ber``.  Returns
    EvaluationResults for all strategies computed against the overridden link
    state.

    **Override precedence (Phase 3):**

    1. No override → normal baseline LinkState.
    2. SNR only → TelecomEngine re-derives Eb/N0 and BER from new SNR.
    3. BER only → baseline SNR/Eb/N0 unchanged; only ``ber`` replaced.
    4. SNR + BER → SNR applied first (Eb/N0 re-derived); then ``ber``
       replaces the derived BER.  **Explicit BER has final precedence.**

    **Non-mutating:**
    ``state.active_link_state`` and ``state.active_scenario`` are never
    modified.  This endpoint is a pure read-only preview.

    Raises 503 if no scenario has been loaded.
    Raises 422 if supplied ``ber`` is outside [0, 0.5] or non-finite.
    Raises 422 if supplied ``snr_db`` is non-finite.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Build hypothetical link state via the dedicated what-if helper.
    # This is the ONLY valid path for what-if evaluation; do NOT call
    # TelecomEngine.compute() directly with a raw BER in the link_inputs dict.
    try:
        hypothetical_link, what_if_context = apply_link_what_if(
            dict(state.active_scenario.link_inputs),
            snr_db=req.snr_db,
            ber=req.ber,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Generate the 4 candidate plans against the ACTUAL scenario packets
    # (packet list doesn't change, only link quality changes).
    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        _effective_packets(state.active_scenario),
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
        what_if_context=what_if_context,
        hypothetical_link_state=hypothetical_link,
        evaluations=evals,
        risk_weights={
            "w_deadline_miss": rw.w_deadline_miss,
            "w_critical_deficit": rw.w_critical_deficit,
            "w_window_pressure": rw.w_window_pressure,
        },
    )
