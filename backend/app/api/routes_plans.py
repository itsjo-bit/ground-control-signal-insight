"""GCSI Backend API — routes for /plans.

Phase 4 trust boundaries
------------------------
POST /plans/generate
    Generates authoritative plans server-side and registers them in the
    issued-plan registry.  Plans are only generated from scenario inventory;
    the client cannot inject packet facts.

POST /plans/evaluate
    Accepts a CandidatePlan but treats it only as an ID/order transport.
    All packet facts are reconstructed from the active scenario before
    evaluation.  Client-supplied size/criticality/deadline are ignored.
    Unknown or duplicate IDs are rejected (HTTP 422).

POST /plans/what-if
    Accepts optional snr_db/ber overrides.  Plans are evaluated from the
    scenario inventory; client packet facts are not used.  Non-mutating.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state
from ..config import RiskWeights, SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..domain.plan_integrity import (
    IntegrityReason,
    PlanIntegrityError,
    PlanSource,
    canonicalize_issued_plan,
    compute_plan_fingerprint,
    get_authoritative_packets,
    reconstruct_authoritative_plan,
)
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.packet import Packet
from ..telecom.what_if import WhatIfLinkContext, apply_link_what_if

router = APIRouter()


def _effective_packets(scenario) -> list[Packet]:
    """Return the authoritative packet list for the given scenario.

    This function is the single implementation used by all routes.
    The implementation in ``routes_agent._effective_packets`` mirrors this.

    Priority:
    1. If ``scenario.packets`` is non-empty, use it directly (legacy path).
    2. Otherwise bridge ``scenario.data_products`` to Packet objects (v2 path).
    """
    return get_authoritative_packets(scenario)


@router.post("/plans/generate", response_model=list[CandidatePlan])
def generate_plans() -> list[CandidatePlan]:
    """Generate all candidate transmission plans from the active scenario.

    Phase 4: Generated plans are registered in the issued-plan registry so
    that ``POST /approve`` can later verify exact plan identity.

    Supports both legacy (``packets``) and v2 (``data_products``) scenarios.
    Returns four strategies: baseline, deadline-first, mission-critical-first,
    and value-per-cost.  Raises 503 if no scenario has been loaded.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        _effective_packets(state.active_scenario),
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )

    # Register each plan in the issued-plan registry.
    # canonicalize_issued_plan() sets plan_source BEFORE hashing (invariant),
    # then returns a deep-copy snapshot so the registry is immutable.
    scenario_id = state.active_scenario.scenario_id
    for plan in plans:
        snapshot, order_sha, canonical_sha = canonicalize_issued_plan(
            plan,
            scenario_id,
            PlanSource.deterministic_generated,
        )
        state.register_issued_plan(
            snapshot,
            scenario_id=scenario_id,
            packet_order_sha256=order_sha,
            canonical_plan_sha256=canonical_sha,
            plan_source_value=PlanSource.deterministic_generated.value,
        )

    return plans


@router.post("/plans/evaluate", response_model=EvaluationResult)
def evaluate_plan(plan: CandidatePlan) -> EvaluationResult:
    """Evaluate a candidate plan analytically.

    Phase 4: Packet facts in the request body are treated only as an
    ID/order transport format.  All packet facts (size_bits, criticality,
    deadline_s, etc.) are reconstructed from the active scenario before
    evaluation.  Client-supplied values for those fields are ignored.

    This endpoint is non-mutating and does NOT invalidate the issued-plan
    registry.

    Raises 503 if no scenario has been loaded.
    Raises 422 if packet IDs are unknown, duplicated, or the scenario has
    duplicate authoritative IDs.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    # Reconstruct packet facts authoritatively.
    try:
        trace = reconstruct_authoritative_plan(
            plan,
            state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
    except PlanIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ev = PlanEvaluator()
    return ev.evaluate(
        trace.reconstructed_plan,
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
        explains exactly what the backend evaluated.

    ``hypothetical_link_state``
        The :class:`~backend.app.models.link_state.LinkState` that was passed
        to ``PlanEvaluator``.

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

    Phase 4: Plans are generated from the authoritative scenario inventory.
    This endpoint is non-mutating and does NOT invalidate the issued-plan
    registry.

    Raises 503 if no scenario has been loaded.
    Raises 422 if supplied ``ber`` is outside [0, 0.5] or non-finite.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    try:
        hypothetical_link, what_if_context = apply_link_what_if(
            dict(state.active_scenario.link_inputs),
            snr_db=req.snr_db,
            ber=req.ber,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
