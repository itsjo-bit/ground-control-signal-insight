"""GCSI Backend API — routes for /approve.

Phase 4 trust boundaries
------------------------
POST /approve
    Standard approval for a server-issued plan.

    The client supplies:
      - ``plan_id``     — must match a plan registered in the issued-plan registry
      - ``plan``        — optional CandidatePlan (used as an ID/order transport only)
      - ``operator_notes`` — operator-supplied text

    Behaviour:
    1. If ``plan`` is supplied, verify it matches the issued-plan registry entry
       (same plan_id and same packet order SHA-256).  Reject with HTTP 409 if the
       plan_id is not in the registry.  Reject with HTTP 422 if the order differs.
    2. If ``plan`` is NOT supplied (legacy form): regenerate from plan_id via
       CandidateGenerator and use plan_source=legacy_regenerated.
    3. All packet facts are reconstructed from the active scenario (authoritative).
    4. ApprovalTrace is produced and returned in ApproveResponse.
    5. issued-plan registry is invalidated after execution.

POST /approve/custom
    Operator-reordered plan (drag-to-reorder).

    Accepts a full CandidatePlan but treats it only as an ID/order transport.
    Does NOT require a registered issued plan (operator may freely reorder).
    All packet facts are reconstructed from the active scenario.
    ApprovalTrace with plan_source=operator_custom is returned.

Backward compatibility
----------------------
- Requests supplying only ``plan_id`` (legacy form) continue to work.
- New ``approval_trace`` and ``executed_plan`` fields are additive.
- ``status`` and ``simulation_result`` remain at their existing positions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..domain.plan_integrity import (
    IntegrityReason,
    PlanIntegrityError,
    PlanSource,
    compute_plan_fingerprint,
    reconstruct_authoritative_plan,
)
from ..models.approval_trace import ApprovalTrace
from ..models.candidate_plan import CandidatePlan
from ..models.simulation_result import SimulationResult
from .routes_plans import _effective_packets
from .routes_simulate import _run_simulation

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ApproveRequest(BaseModel):
    """Approve a named plan by ID, optionally supplying the full plan object.

    Phase 4 semantics:

    ``plan_id`` (required):
        Must match a plan in the issued-plan registry when ``plan`` is also
        supplied.  Used to look up the plan in the legacy path when ``plan``
        is ``None``.

    ``plan`` (optional):
        The CandidatePlan the operator approved.  When present the backend
        verifies it against the issued-plan registry before reconstructing
        packet facts.  Used as an ID/order transport only — client-supplied
        packet facts are replaced with authoritative scenario values.

    ``operator_notes`` (optional):
        Free-text operator annotation, stored in ApprovalTrace.
    """

    plan_id: str
    plan: CandidatePlan | None = None
    operator_notes: str = ""


class ApproveCustomRequest(BaseModel):
    """Approve an operator-reordered plan.

    ``plan`` is used as an ID/order transport only.  Packet facts are
    reconstructed from the active scenario.  No issued-plan registry check
    is performed — the operator is explicitly constructing a custom order.
    """

    plan: CandidatePlan
    operator_notes: str = ""


class ApproveResponse(BaseModel):
    """Response for POST /approve and POST /approve/custom.

    Phase 4 additions (backwards-compatible):

    ``approval_trace``
        Typed provenance record for this approval event.

    ``executed_plan``
        The authoritative CandidatePlan that was actually simulated.
        Packet facts are from the scenario; only the operator-supplied order
        is preserved.  Use this to confirm what was evaluated.
    """

    status: str
    simulation_result: SimulationResult
    approval_trace: ApprovalTrace
    executed_plan: CandidatePlan


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_approval_trace(
    *,
    plan_id: str,
    scenario_id: str,
    plan_source: PlanSource,
    operator_notes: str,
    authoritative_reconstruction: bool,
    issued_plan_verified: bool,
    packet_count: int,
    packet_order_sha256: str,
    canonical_plan_sha256: str,
) -> ApprovalTrace:
    """Construct an ApprovalTrace from discrete fields."""
    now_utc = datetime.now(timezone.utc)
    timestamp_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    approval_id = f"{plan_id}:{now_utc.strftime('%H%M%S')}"

    # Trim operator notes to 500 chars for safety.
    notes = (operator_notes or "").strip()[:500]

    return ApprovalTrace(
        approval_id=approval_id,
        timestamp_utc=timestamp_str,
        scenario_id=scenario_id,
        plan_id=plan_id,
        decision="approved",
        plan_source=plan_source.value,
        operator_notes=notes,
        authoritative_reconstruction=authoritative_reconstruction,
        issued_plan_verified=issued_plan_verified,
        packet_count=packet_count,
        packet_order_sha256=packet_order_sha256,
        canonical_plan_sha256=canonical_plan_sha256,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/approve", response_model=ApproveResponse)
def approve_plan(req: ApproveRequest) -> ApproveResponse:
    """Approve a server-issued plan and execute it via simulation.

    Phase 4 trust boundary:

    Standard path (``req.plan`` supplied):
        1. Verify plan_id is in the issued-plan registry (HTTP 409 if absent).
        2. Verify the submitted packet order matches the registered order
           (HTTP 422 if order differs — this catches operator tampering).
        3. Reconstruct packet facts from the active scenario.
        4. Simulate.
        5. Return ApprovalTrace + executed_plan + simulation_result.

    Legacy path (``req.plan`` is None):
        Regenerate plans from CandidateGenerator, look up by plan_id,
        reconstruct authoritatively.  plan_source = legacy_regenerated.

    Always invalidates the issued-plan registry after execution (state mutated).
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    scenario = state.active_scenario
    scenario_id = scenario.scenario_id
    issued_plan_verified = False

    if req.plan is not None:
        # ── Phase 4 standard path ─────────────────────────────────────────────
        # Validate plan_id consistency.
        if req.plan.plan_id != req.plan_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"plan_id mismatch: req.plan_id='{req.plan_id}' but "
                    f"req.plan.plan_id='{req.plan.plan_id}'.  They must agree."
                ),
            )

        # Look up the issued plan in the registry.
        record = state.issued_plans.get(req.plan_id)
        if record is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Plan '{req.plan_id}' is not in the issued-plan registry.  "
                    "Only server-generated plans may be approved via POST /approve.  "
                    "For operator-custom plans use POST /approve/custom."
                ),
            )

        # Verify the submitted packet order matches the registered order.
        from ..domain.plan_integrity import _compute_order_hash  # noqa: PLC0415
        submitted_order_sha = _compute_order_hash(
            [p.packet_id for p in req.plan.packets]
        )
        if submitted_order_sha != record.packet_order_sha256:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Packet order for plan '{req.plan_id}' does not match the "
                    "server-issued order.  The plan may have been tampered with.  "
                    "Re-generate plans or use POST /approve/custom for a custom order."
                ),
            )

        issued_plan_verified = True
        plan_source = PlanSource(record.plan_source)

        # Reconstruct packet facts from scenario (authoritative).
        try:
            trace = reconstruct_authoritative_plan(
                req.plan,
                scenario,
                plan_source=plan_source,
            )
        except PlanIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    else:
        # ── Legacy path: regenerate from plan_id ─────────────────────────────
        from ..config import SchedulerWeights  # noqa: PLC0415
        from ..candidate_generator.generator import CandidateGenerator  # noqa: PLC0415

        weights = SchedulerWeights()
        gen = CandidateGenerator()
        plans = gen.generate(
            _effective_packets(scenario),
            state.active_link_state,
            scenario.mission_state,
            weights,
        )

        candidate = next((p for p in plans if p.plan_id == req.plan_id), None)
        if candidate is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Plan '{req.plan_id}' not found. "
                    f"Available: {[p.plan_id for p in plans]}"
                ),
            )

        plan_source = PlanSource.legacy_regenerated
        try:
            trace = reconstruct_authoritative_plan(
                candidate,
                scenario,
                plan_source=plan_source,
            )
        except PlanIntegrityError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Build ApprovalTrace.
    approval_trace = _build_approval_trace(
        plan_id=trace.reconstructed_plan.plan_id,
        scenario_id=scenario_id,
        plan_source=plan_source,
        operator_notes=req.operator_notes,
        authoritative_reconstruction=trace.authoritative_reconstruction,
        issued_plan_verified=issued_plan_verified,
        packet_count=trace.packet_count,
        packet_order_sha256=trace.packet_order_sha256,
        canonical_plan_sha256=trace.canonical_plan_sha256,
    )

    # Persist trace.
    state.last_approval_trace = approval_trace

    # Simulate — uses the authoritative reconstructed plan.
    result = _run_simulation(trace.reconstructed_plan, seed=None)

    # Mutate server state with realized outcomes.
    state.active_link_state = result.link_state
    state.active_scenario = scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    # Invalidate issued plans — state has mutated.
    state.invalidate_issued_plans(
        reason=f"plan approved and executed: {trace.reconstructed_plan.plan_id}"
    )

    return ApproveResponse(
        status="approved",
        simulation_result=result,
        approval_trace=approval_trace,
        executed_plan=trace.reconstructed_plan,
    )


@router.post("/approve/custom", response_model=ApproveResponse)
def approve_custom_plan(req: ApproveCustomRequest) -> ApproveResponse:
    """Approve and simulate an operator-supplied custom plan.

    Accepts a full CandidatePlan as an ID/order transport.  Used when the
    operator has manually reordered packets (drag-to-reorder) or is
    submitting an ad-hoc selection.

    Does NOT require a registered issued plan — operator custom orders are
    always accepted (within the authoritative packet inventory).

    All packet facts are reconstructed from the active scenario; client-
    supplied size_bits, criticality, etc. are replaced.

    Unknown or duplicate packet IDs are rejected (HTTP 422).
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    scenario = state.active_scenario
    scenario_id = scenario.scenario_id

    # Reconstruct packet facts authoritatively.
    try:
        trace = reconstruct_authoritative_plan(
            req.plan,
            scenario,
            plan_source=PlanSource.operator_custom,
        )
    except PlanIntegrityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    approval_trace = _build_approval_trace(
        plan_id=trace.reconstructed_plan.plan_id,
        scenario_id=scenario_id,
        plan_source=PlanSource.operator_custom,
        operator_notes=req.operator_notes,
        authoritative_reconstruction=trace.authoritative_reconstruction,
        issued_plan_verified=False,  # operator custom — no issued plan to verify against
        packet_count=trace.packet_count,
        packet_order_sha256=trace.packet_order_sha256,
        canonical_plan_sha256=trace.canonical_plan_sha256,
    )

    # Persist trace.
    state.last_approval_trace = approval_trace

    result = _run_simulation(trace.reconstructed_plan, seed=None)

    state.active_link_state = result.link_state
    state.active_scenario = scenario.model_copy(
        update={"mission_state": result.mission_state}
    )

    state.invalidate_issued_plans(
        reason=f"custom plan approved and executed: {trace.reconstructed_plan.plan_id}"
    )

    return ApproveResponse(
        status="approved",
        simulation_result=result,
        approval_trace=approval_trace,
        executed_plan=trace.reconstructed_plan,
    )
