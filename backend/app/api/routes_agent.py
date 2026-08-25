"""GCSI Backend API — routes for /agent/recommend.

The route is provider-agnostic: it delegates to whatever AI provider is
currently configured (IBM Granite, Ollama, or local rule-based).

Provider selection is handled by :func:`~backend.app.agent.provider_factory.get_provider`.
The response includes a ``provider`` field indicating which provider was used.

Phase 2C (v2 path)
------------------
When the active scenario carries ``data_products``, the route activates the
AI candidate prioritization path:

1. ``CandidatePrioritizer.select()`` deterministically reduces the product
   list to at most ``GCSI_AI_MAX_CANDIDATES`` :class:`CandidateSummary` objects.
2. ``provider.prioritize_candidates()`` asks the AI to rank those candidates.
3. The AI ranking is used to reorder the bridged Packet list passed to
   ``CandidateGenerator`` and ``PlanEvaluator``.
4. The result includes the full ``CandidatePrioritization`` so the frontend
   can later show AI reasoning per product.

Legacy scenarios (``scenario.packets`` only) use the original four-plan path.
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

    Phase 2C adds ``prioritization`` (non-None only for v2 scenarios) so the
    frontend can display per-product AI reasoning without a separate API call.

    Phase 2D adds ``prioritization_error`` to surface AI failures transparently
    while keeping the deterministic recommendation intact.
    """
    provider: str
    recommendation: AIRecommendation
    prioritization: CandidatePrioritization | None = None
    candidate_count: int | None = None
    # Phase 2D: surface AI failures without breaking the deterministic path
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

    **v2 path** (``scenario.data_products`` non-empty):
    1. Deterministically selects a bounded candidate set (≤ GCSI_AI_MAX_CANDIDATES).
    2. Asks the AI to semantically rank those candidates.
    3. Uses the AI ranking to reorder packets before plan generation.
    4. Evaluates the reordered plan deterministically.
    5. Returns the recommendation together with the full ``CandidatePrioritization``
       so the frontend can display per-product AI reasoning.

    The provider is selected automatically:
    - IBM Granite if ``GCSI_GRANITE_API_KEY`` is set.
    - Google Gemini if ``GCSI_GEMINI_API_KEY`` is set.
    - Ollama if ``GCSI_OLLAMA_ENABLED=true`` and the server is reachable.
    - Local rule-based provider otherwise (default, no credentials required).

    Raises:
        503: No active scenario loaded.
        502: AI provider is unavailable (Granite API down, etc.).
        422: AI response is invalid or evidence is hallucinated.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    scenario = state.active_scenario
    link_state = state.active_link_state
    anomalies: list[AnomalyEvent] = scenario.anomalies
    provider = get_provider()

    # Phase 2E-C3-E: extract spacecraft distance for geometry context
    distance_km: float | None = scenario.distance_km

    # ── Determine which path to take ─────────────────────────────────────────
    use_v2_path = bool(scenario.data_products) and not scenario.packets

    prioritization: CandidatePrioritization | None = None
    candidate_count: int | None = None
    prioritization_error: str | None = None

    if use_v2_path:
        # ── Phase 2C: AI candidate prioritization path ────────────────────
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

        # Step 2: AI semantic prioritization
        # Phase 2D: graceful fallback — AI failure must NOT collapse the mission.
        # If the primary provider fails, fall back to LocalRuleBasedProvider and
        # surface the error transparently in the response.
        from ..agent.local_provider import LocalRuleBasedProvider
        _fallback = LocalRuleBasedProvider()
        try:
            prioritization = provider.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
        except NotImplementedError:
            # Provider doesn't implement Phase 2C — fall back to local deterministic
            logger.warning(
                "Provider '%s' does not implement prioritize_candidates(); "
                "using LocalRuleBasedProvider fallback.",
                provider.provider_name,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            prioritization_error = (
                f"Provider '{provider.provider_name}' does not implement AI candidate "
                "prioritization. Using deterministic fallback ordering."
            )
        except AIProviderError as exc:
            # Phase 2D: surface the failure gracefully — deterministic fallback keeps mission running
            logger.error(
                "AI provider '%s' unavailable for prioritization: %s. Falling back to deterministic.",
                provider.provider_name, exc,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            prioritization_error = (
                f"AI provider '{provider.provider_name}' unavailable: {exc}. "
                "Deterministic candidate ordering is in use."
            )
        except AIPrioritizationError as exc:
            # Phase 2D: invalid AI response — fall back to deterministic
            logger.error(
                "Invalid AI prioritization from '%s': %s. Falling back to deterministic.",
                provider.provider_name, exc,
            )
            prioritization = _fallback.prioritize_candidates(
                candidates, link_state, scenario.mission_state, anomalies,
                distance_km=distance_km,
            )
            prioritization_error = (
                f"Invalid AI prioritization from '{provider.provider_name}': {exc}. "
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

    # ── AI recommendation: choose the best plan from evaluated candidates ────
    try:
        recommendation = provider.recommend(
            link_state,
            scenario.mission_state,
            plans,
            evaluations,
            anomalies=anomalies,
        )
    except AIProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"AI provider '{provider.provider_name}' unavailable: {exc}",
        ) from exc
    except (AIResponseError, AIHallucinationError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid AI response from '{provider.provider_name}': {exc}",
        ) from exc

    return RecommendResponse(
        provider=provider.provider_name,
        recommendation=recommendation,
        prioritization=prioritization,
        candidate_count=candidate_count,
        prioritization_error=prioritization_error,
    )
