"""GCSI Backend API — routes for /agent/recommend.

The route is provider-agnostic: it delegates to whatever AI provider is
currently configured (IBM Granite, Gemini, Ollama, or local rule-based).

Provider selection is handled by :func:`~backend.app.agent.provider_factory.get_provider`.

Three stages
------------
1. **AI Stage 1 — Semantic Prioritization** (v2/v3 path only)
   ``CandidatePrioritizer.select()`` deterministically reduces the product list
   to at most ``GCSI_AI_MAX_CANDIDATES`` :class:`CandidateSummary` objects, then
   ``provider.prioritize_candidates()`` semantically ranks them.

2. **Plan Generation** (v2/v3 path)
   - Four deterministic baseline plans are generated from the **original**
     authoritative packet set, independent of AI ranking.
   - One AI-prioritized plan is built from the Stage-1 ranking via
     :func:`~backend.app.candidate_generator.ai_plan_builder.build_ai_prioritized_plan`.
   All five plans are evaluated identically by ``PlanEvaluator``.

3. **AI Stage 2 — Plan Recommendation**
   After evaluation, ``provider.recommend()`` receives all five plans and
   their evaluations and returns an advisory recommendation.

Architecture principle
----------------------
   **AI proposes. Deterministic math evaluates. Human decides.**

   The four deterministic baselines are generated from the original packet set
   and remain independent of Stage-1 AI output.  This provides a clean
   scientific control group: changing the AI ranking changes the AI plan but
   must NOT change any deterministic baseline.

Graceful fallback
-----------------
If the primary provider fails at **either** AI stage, the route falls back to
``LocalRuleBasedProvider`` rather than returning HTTP 502.  The response
includes ``requested_provider``, ``actual_provider``, and (if applicable)
``prioritization_fallback_reason`` / ``recommendation_fallback_reason`` so the
caller always knows which provider produced the result.

A 502 is only returned if both the primary provider and the Local fallback fail,
which cannot happen in normal operation (Local never raises on valid inputs).

Legacy scenarios (``scenario.packets`` only) use the original four-plan path
and skip AI candidate prioritization.  No fake ai-prioritized plan is created
for legacy scenarios.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from .. import state
from ..agent.base_provider import AIHallucinationError, AIPrioritizationError, AIProviderError, AIResponseError
from ..agent.candidate_prioritizer import CandidatePrioritizer
from ..agent.provider_factory import get_provider
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..candidate_generator.ai_plan_builder import build_ai_prioritized_plan
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.anomaly_event import AnomalyEvent
from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.packet import Packet
from ..models.recommendation import AIRecommendation

logger = logging.getLogger(__name__)

router = APIRouter()


class RecommendRequest(BaseModel):
    """Optional overrides for the recommend call (reserved for future use)."""
    plans: list | None = None
    evaluations: list | None = None


class RecommendResponse(BaseModel):
    """Wraps AIRecommendation with provider metadata.

    ``provider`` is kept for backwards compatibility and always equals
    ``actual_provider``.

    ``requested_provider`` — the provider name that was configured/selected
    before the request.  May differ from ``actual_provider`` when fallback
    occurs.

    ``actual_provider`` — the provider that produced the final recommendation.
    Always set; equals ``requested_provider`` when no fallback occurred.

    ``prioritization_fallback_reason`` — set when the primary provider failed
    during candidate prioritization and Local fallback was used.

    ``recommendation_fallback_reason`` — set when the primary provider failed
    during plan recommendation and Local fallback was used.

    The legacy ``prioritization_error`` field is retained and mirrors
    ``prioritization_fallback_reason`` for backwards compatibility.

    ``ai_plan`` — the AI-prioritized candidate plan (v2/v3 path only).
    ``None`` for legacy scenarios where Stage-1 prioritization is unavailable.

    ``ai_evaluation`` — deterministic evaluation of ``ai_plan`` (v2/v3 path only).
    ``None`` for legacy scenarios.
    """
    provider: str
    """Backwards-compatible: equals actual_provider."""
    requested_provider: str
    """The provider originally selected by configuration."""
    actual_provider: str
    """The provider that produced the final result (may be 'local' on fallback)."""
    recommendation: AIRecommendation
    prioritization: CandidatePrioritization | None = None
    candidate_count: int | None = None
    # Fallback transparency fields
    prioritization_fallback_reason: str | None = None
    recommendation_fallback_reason: str | None = None
    # Backwards-compatible alias: mirrors prioritization_fallback_reason
    prioritization_error: str | None = None
    # AI plan surface — v2/v3 path only; null for legacy scenarios
    ai_plan: CandidatePlan | None = None
    """The AI-prioritized transmission plan (v2/v3 path). Null for legacy scenarios."""
    ai_evaluation: EvaluationResult | None = None
    """Deterministic evaluation of ai_plan (v2/v3 path). Null for legacy scenarios."""


def _effective_packets(scenario) -> list[Packet]:
    """Return the effective packet list for the given scenario.

    Uses legacy ``packets`` when present; otherwise bridges ``data_products``.
    Mirrors the same helper in ``routes_plans`` to keep behaviour consistent.
    """
    if scenario.packets:
        return scenario.packets
    return data_products_to_packets(scenario.data_products)


def _reorder_packets_by_ai(
    packets: list[Packet],
    prioritization: CandidatePrioritization,
) -> list[Packet]:
    """Reorder bridged packets using the AI priority ranking.

    Products included in the AI ranking are placed first in ascending priority
    order.  Products not mentioned by the AI (because they were outside the
    candidate set, or the AI chose not to rank them) are appended at the end
    in their original order.

    Args:
        packets:         Full bridged packet list.
        prioritization:  AI prioritization result.

    Returns:
        A new list of the same Packet objects in AI-driven order.
    """
    # Build a lookup: packet_id → Packet
    pkt_map: dict[str, Packet] = {p.packet_id: p for p in packets}

    # Sort ranked products by priority ascending (1 = most important)
    ranked_sorted = sorted(
        prioritization.ranked_products, key=lambda rp: rp.priority
    )

    ordered: list[Packet] = []
    seen: set[str] = set()

    for rp in ranked_sorted:
        if rp.product_id in pkt_map:
            ordered.append(pkt_map[rp.product_id])
            seen.add(rp.product_id)

    # Append unranked packets in original order
    for pkt in packets:
        if pkt.packet_id not in seen:
            ordered.append(pkt)

    return ordered


@router.post("/agent/recommend", response_model=RecommendResponse)
def recommend(req: RecommendRequest | None = None) -> RecommendResponse:  # noqa: ARG001
    """Request an AI recommendation for the active scenario.

    **Legacy path** (``scenario.packets`` non-empty):
    Generates four deterministic candidate plans, evaluates them, then
    asks the AI provider to recommend one.  No AI-prioritized plan is
    created for legacy scenarios (Stage-1 prioritization is unavailable).

    **v2/v3 path** (``scenario.data_products`` non-empty):

    1. AI Stage 1 — Deterministically selects a bounded candidate set
       (≤ GCSI_AI_MAX_CANDIDATES), then asks the AI to semantically rank them.
    2. Four deterministic baseline plans are generated from the **original**
       authoritative packet set, independent of AI ranking.
    3. One AI-prioritized plan is built via
       :func:`~backend.app.candidate_generator.ai_plan_builder.build_ai_prioritized_plan`:
       AI-ranked products appear first (priority 1 first); unranked products
       are appended in BaselineScheduler order.
    4. All **five** plans are evaluated identically by ``PlanEvaluator``.
    5. AI Stage 2 — AI reviews all five evaluated plans and returns an
       advisory recommendation.  It may recommend any plan including the
       AI-prioritized one or any deterministic baseline.

    **Causal path**:
    Stage-1 AI ranking directly determines the ``ai-prioritized`` plan order,
    which in turn determines its ``PlanEvaluator`` metrics.  Changing the AI
    ranking changes the AI plan outcome.  The four deterministic baselines
    are unaffected by AI ranking.

    **Graceful fallback (both paths)**:
    If the primary provider is unavailable or returns an invalid response at
    either AI stage, the route automatically falls back to LocalRuleBasedProvider.

    Raises:
        503: No active scenario loaded.
        502: Both primary and Local fallback failed (should not occur).
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    scenario = state.active_scenario
    link_state = state.active_link_state
    anomalies: list[AnomalyEvent] = scenario.anomalies
    provider = get_provider()

    # Import fallback provider once — used at both AI stages if needed.
    from ..agent.local_provider import LocalRuleBasedProvider
    _fallback = LocalRuleBasedProvider()

    requested_provider_name: str = provider.provider_name

    # Track which provider actually produces each result.
    # Starts as the requested provider; updated to 'local' on fallback.
    actual_recommendation_provider: str = requested_provider_name

    # ── Spacecraft geometry context ───────────────────────────────────────────
    distance_km: float | None = scenario.distance_km

    # ── Determine which path to take ─────────────────────────────────────────
    use_v2_path = bool(scenario.data_products) and not scenario.packets

    prioritization: CandidatePrioritization | None = None
    candidate_count: int | None = None
    prioritization_fallback_reason: str | None = None
    ai_plan: CandidatePlan | None = None
    ai_evaluation: EvaluationResult | None = None

    weights = SchedulerWeights()
    gen = CandidateGenerator()

    if use_v2_path:
        # ── AI Stage 1: candidate prioritization ─────────────────────────
        # Step 1: deterministic candidate selection (token-safe)
        prioritizer = CandidatePrioritizer()
        candidates: list[CandidateSummary] = prioritizer.select(
            scenario.data_products,
            anomalies=anomalies,
            remaining_window_s=link_state.remaining_window_s,
        )
        candidate_count = len(candidates)
        logger.info(
            "v2 prioritization path: %d/%d data products selected as candidates",
            candidate_count, len(scenario.data_products),
        )

        # Step 2: AI semantic prioritization with graceful fallback.
        # Any provider failure falls back to LocalRuleBasedProvider so the
        # mission workflow continues; fallback reason is surfaced in the response.
        actual_stage1_provider: str = requested_provider_name
        try:
            prioritization = provider.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
        except NotImplementedError:
            logger.warning(
                "Provider '%s' does not implement prioritize_candidates(); "
                "using LocalRuleBasedProvider fallback.",
                provider.provider_name,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            actual_stage1_provider = _fallback.provider_name
            prioritization_fallback_reason = (
                f"Provider '{provider.provider_name}' does not implement AI candidate "
                "prioritization. Using deterministic fallback ordering."
            )
        except AIProviderError as exc:
            logger.error(
                "AI provider '%s' unavailable for prioritization: %s. "
                "Falling back to LocalRuleBasedProvider.",
                provider.provider_name, exc,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            actual_stage1_provider = _fallback.provider_name
            prioritization_fallback_reason = (
                f"AI provider '{provider.provider_name}' unavailable. "
                "Deterministic candidate ordering is in use."
            )
        except AIPrioritizationError as exc:
            logger.error(
                "Invalid AI prioritization from '%s': %s. "
                "Falling back to LocalRuleBasedProvider.",
                provider.provider_name, exc,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            actual_stage1_provider = _fallback.provider_name
            prioritization_fallback_reason = (
                f"Invalid AI prioritization from '{provider.provider_name}'. "
                "Deterministic candidate ordering is in use."
            )

        # Step 3: Build ALL five plans.
        #
        # CRITICAL ARCHITECTURE PRINCIPLE:
        # The four deterministic baselines are generated from the ORIGINAL
        # authoritative packet set, completely independent of AI ranking.
        # This is the scientific control group.  Changing the AI ranking
        # must NOT change any deterministic baseline plan.
        all_packets = data_products_to_packets(scenario.data_products)

        # Four deterministic baselines — from original packets, AI-agnostic.
        plans = gen.generate(all_packets, link_state, scenario.mission_state, weights)

        # Fifth plan: AI-prioritized — built from Stage-1 semantic ranking.
        # AI-ranked products appear in priority order; unranked products
        # are appended in BaselineScheduler order.
        ai_plan = build_ai_prioritized_plan(
            all_packets,
            prioritization,
            link_state,
            scenario.mission_state,
            weights,
            stage1_provider=actual_stage1_provider,
            fallback_used=(prioritization_fallback_reason is not None),
        )

    else:
        # ── Legacy path: four deterministic plans only ─────────────────────
        # No AI plan for legacy scenarios — Stage-1 prioritization is
        # unavailable when only legacy packets are present.
        plans = gen.generate(
            _effective_packets(scenario),
            link_state,
            scenario.mission_state,
            weights,
        )

    # ── Deterministic evaluation (both paths) ────────────────────────────────
    # All plans are evaluated with the SAME PlanEvaluator instance under
    # the SAME link/mission/risk parameters.  No AI bonus.  No special
    # treatment for the AI plan.
    ev = PlanEvaluator()
    evaluations = [
        ev.evaluate(plan, link_state, scenario.mission_state)
        for plan in plans
    ]

    if use_v2_path and ai_plan is not None:
        # Evaluate the AI plan with the same evaluator — no special treatment.
        ai_evaluation = ev.evaluate(ai_plan, link_state, scenario.mission_state)

        # Include the AI plan and its evaluation in Stage 2.
        all_plans_for_stage2 = plans + [ai_plan]
        all_evals_for_stage2 = evaluations + [ai_evaluation]
    else:
        all_plans_for_stage2 = plans
        all_evals_for_stage2 = evaluations

    # ── AI Stage 2: plan recommendation with graceful fallback ───────────────
    # Stage 2 receives ALL five evaluated plans (four deterministic + ai plan).
    # It may recommend any plan — including the AI-prioritized plan or any
    # deterministic baseline.  The recommendation is based solely on
    # deterministic evaluation evidence.
    recommendation_fallback_reason: str | None = None
    try:
        recommendation = provider.recommend(
            link_state,
            scenario.mission_state,
            all_plans_for_stage2,
            all_evals_for_stage2,
            anomalies=anomalies,
        )
    except (AIProviderError, AIResponseError, AIHallucinationError) as exc:
        logger.error(
            "AI provider '%s' failed plan recommendation: %s. "
            "Falling back to LocalRuleBasedProvider.",
            provider.provider_name, exc,
        )
        try:
            recommendation = _fallback.recommend(
                link_state,
                scenario.mission_state,
                all_plans_for_stage2,
                all_evals_for_stage2,
                anomalies=anomalies,
            )
            actual_recommendation_provider = _fallback.provider_name
            recommendation_fallback_reason = (
                f"AI provider '{provider.provider_name}' unavailable for plan "
                "recommendation. Local rule-based recommendation is in use."
            )
        except Exception as fallback_exc:  # noqa: BLE001
            # Local fallback should never fail on valid inputs.
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Both primary provider '{provider.provider_name}' and Local "
                    f"fallback failed: {fallback_exc}"
                ),
            ) from fallback_exc

    # ── Determine the reported actual_provider ────────────────────────────────
    actual_provider_name = actual_recommendation_provider

    return RecommendResponse(
        provider=actual_provider_name,           # backwards-compatible
        requested_provider=requested_provider_name,
        actual_provider=actual_provider_name,
        recommendation=recommendation,
        prioritization=prioritization,
        candidate_count=candidate_count,
        prioritization_fallback_reason=prioritization_fallback_reason,
        recommendation_fallback_reason=recommendation_fallback_reason,
        # Backwards-compatible alias
        prioritization_error=prioritization_fallback_reason,
        # AI plan surface — v2/v3 path only
        ai_plan=ai_plan,
        ai_evaluation=ai_evaluation,
    )
