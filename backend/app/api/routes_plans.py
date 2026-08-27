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

POST /plans/assess
    Non-mutating manual assessment.  Accepts a list of product_ids and
    an optional order.  Reconstructs authoritative packet facts from the
    scenario, runs PlanEvaluator and MissionOutcomeEvaluator, and returns
    the plan, evaluation, mission_outcome, and capacity_summary.
    Does NOT require an AI recommendation.  Does NOT mutate state.
    Does NOT invalidate the issued-plan registry.
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
from ..evaluator.mission_outcome_evaluator import MissionOutcomeEvaluator, MissionOutcomeResult
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


# ── POST /plans/assess — non-mutating manual assessment ──────────────────────


class AssessRequest(BaseModel):
    """Request body for POST /plans/assess — non-mutating manual plan assessment."""

    product_ids: list[str]
    order: list[str] | None = None  # if None, use product_ids order


class CapacitySummary(BaseModel):
    """Typed capacity summary for POST /plans/assess."""

    available_capacity_bits: int
    selected_bits: int
    selected_count: int
    exceeds_capacity: bool
    window_s: float


class AssessResponse(BaseModel):
    """Response for POST /plans/assess."""

    plan: CandidatePlan
    evaluation: EvaluationResult
    mission_outcome: MissionOutcomeResult | None = None
    capacity_summary: CapacitySummary


@router.post("/plans/assess", response_model=AssessResponse)
def assess_manual_plan(req: AssessRequest) -> AssessResponse:
    """Non-mutating manual plan assessment.

    Accepts a list of product_ids (with optional ordering), reconstructs
    authoritative packet facts from the active scenario, runs PlanEvaluator
    and MissionOutcomeEvaluator, and returns the plan + full evaluation.

    Does NOT require an AI recommendation.
    Does NOT mutate any state.
    Does NOT invalidate the issued-plan registry.

    Raises 503 if no scenario has been loaded.
    Raises 422 if product IDs are unknown, duplicated, or the scenario has
    duplicate authoritative IDs.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    ordered_ids = req.order if req.order is not None else req.product_ids

    # Build a minimal CandidatePlan from the ordered product IDs so
    # reconstruct_authoritative_plan can verify and re-bind facts.
    stub_packets = [
        Packet(
            packet_id=pid,
            packet_type="",
            size_bits=1,
            criticality=0.0,
            mission_relevance=0.0,
            deadline_s=0.0,
            retry_cost=0.0,
            delivery_requirement="optional",
        )
        for pid in ordered_ids
    ]
    stub_plan = CandidatePlan(
        plan_id="operator-manual-assess",
        strategy="manual",
        packets=stub_packets,
        generated_by="operator",
        metadata={"decision_mode": "manual", "selected_count": len(ordered_ids)},
    )

    # Reconstruct with authoritative packet facts.
    try:
        trace = reconstruct_authoritative_plan(
            stub_plan,
            state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
    except PlanIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    auth_plan = trace.reconstructed_plan

    # Run PlanEvaluator.
    ev = PlanEvaluator()
    evaluation = ev.evaluate(
        auth_plan,
        state.active_link_state,
        state.active_scenario.mission_state,
    )

    # Run MissionOutcomeEvaluator (requires data_products and anomalies).
    data_products = getattr(state.active_scenario, "data_products", []) or []
    anomalies = getattr(state.active_scenario, "anomalies", []) or []

    mission_outcome = None
    if data_products:
        moe = MissionOutcomeEvaluator()
        mission_outcome = moe.evaluate(
            auth_plan,
            evaluation,
            data_products,
            anomalies,
        )

    # Build capacity summary.
    link = state.active_link_state
    window_s = min(
        link.remaining_window_s,
        state.active_scenario.mission_state.comm_window_remaining_s,
    )
    available_capacity_bits = int(link.link_goodput_bps * window_s)
    selected_bits = sum(pkt.size_bits for pkt in auth_plan.packets)
    capacity = CapacitySummary(
        available_capacity_bits=available_capacity_bits,
        selected_bits=selected_bits,
        selected_count=len(auth_plan.packets),
        exceeds_capacity=selected_bits > available_capacity_bits,
        window_s=window_s,
    )

    return AssessResponse(
        plan=auth_plan,
        evaluation=evaluation,
        mission_outcome=mission_outcome,
        capacity_summary=capacity,
    )
