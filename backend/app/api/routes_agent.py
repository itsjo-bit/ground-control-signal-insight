"""GCSI Backend API — routes for /agent/recommend.

The route is provider-agnostic: it delegates to whatever AI provider is
currently configured (IBM Granite, Gemini, Ollama, or local rule-based).

Provider selection is handled by :func:`~backend.app.agent.provider_factory.get_provider`.

Two AI advisory stages
----------------------
1. **AI Semantic Prioritization** (v2/v3 path only)
   ``CandidatePrioritizer.select()`` deterministically reduces the product list
   to at most ``GCSI_AI_MAX_CANDIDATES`` :class:`CandidateSummary` objects, then
   ``provider.prioritize_candidates()`` semantically ranks them.  The ranking
   reorders the bridged Packet list before plan generation.

2. **AI Plan Recommendation**
   After deterministic plan generation and evaluation, ``provider.recommend()``
   reviews the evaluated plans and returns an advisory recommendation with
   evidence citations.  Deterministic metrics remain authoritative.

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
and skip AI candidate prioritization.
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
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.anomaly_event import AnomalyEvent
from ..models.bridge import data_products_to_packets
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
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
    Generates four candidate plans, evaluates them deterministically, then
    asks the AI provider to recommend one of the four plans.

    **v2/v3 path** (``scenario.data_products`` non-empty):
    1. AI Stage 1 — Deterministically selects a bounded candidate set
       (≤ GCSI_AI_MAX_CANDIDATES), then asks the AI to semantically rank them.
    2. Uses the AI ranking to reorder packets before plan generation.
    3. Deterministic plan generation and evaluation (authoritative).
    4. AI Stage 2 — AI reviews evaluated plans and returns an advisory
       recommendation with evidence citations.

    **Graceful fallback (both paths)**:
    If the primary provider is unavailable or returns an invalid response at
    either AI stage, the route automatically falls back to LocalRuleBasedProvider.
    The response transparently reports which provider actually produced the result
    via ``requested_provider`` and ``actual_provider``.  HTTP 502 is only raised
    if both providers fail, which cannot occur in normal operation.

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
            prioritization_fallback_reason = (
                f"Invalid AI prioritization from '{provider.provider_name}'. "
                "Deterministic candidate ordering is in use."
            )

        # Step 3: Reorder bridged packets using AI ranking
        all_packets = data_products_to_packets(scenario.data_products)
        ai_ordered_packets = _reorder_packets_by_ai(all_packets, prioritization)

        # Step 4: Generate plans from AI-ordered packets
        weights = SchedulerWeights()
        gen = CandidateGenerator()
        plans = gen.generate(
            ai_ordered_packets,
            link_state,
            scenario.mission_state,
            weights,
        )

    else:
        # ── Legacy path: four-plan selection ─────────────────────────────
        weights = SchedulerWeights()
        gen = CandidateGenerator()
        plans = gen.generate(
            _effective_packets(scenario),
            link_state,
            scenario.mission_state,
            weights,
        )

    # ── Deterministic evaluation (both paths) ────────────────────────────────
    ev = PlanEvaluator()
    evaluations = [
        ev.evaluate(plan, link_state, scenario.mission_state)
        for plan in plans
    ]

    # ── AI Stage 2: plan recommendation with graceful fallback ───────────────
    # If the primary provider failed during prioritization, attempt its
    # recommend() call anyway — it may still be capable of plan recommendation
    # even when prioritization was unavailable.  If recommend() also fails,
    # fall back to LocalRuleBasedProvider rather than returning HTTP 502.
    recommendation_fallback_reason: str | None = None
    try:
        recommendation = provider.recommend(
            link_state,
            scenario.mission_state,
            plans,
            evaluations,
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
                plans,
                evaluations,
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
    # The recommendation provider takes precedence since it is the final
    # advisory result visible to the operator.
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
    )
