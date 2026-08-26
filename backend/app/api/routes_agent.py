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
   All five plans are evaluated identically by ``PlanEvaluator`` (telecom /
   feasibility) and ``MissionOutcomeEvaluator`` (semantic mission outcome).

3. **AI Stage 2 — Plan Recommendation**
   For **external** providers (Granite, Gemini, Ollama), plans are anonymised
   as opaque options (OPTION-A … OPTION-E) before sending to the LLM so the
   model cannot identify which plan was AI-generated.  The model's choice is
   mapped back to the real plan_id by the trusted backend.

   ``LocalRuleBasedProvider`` is deterministic and is not provenance-blind
   (it does not reason in a way that would be biased by plan names).

Architecture principle
----------------------
   **AI proposes. Deterministic math evaluates. Human decides.**

   The four deterministic baselines are generated from the original packet set
   and remain independent of Stage-1 AI output.  This provides a clean
   scientific control group: changing the AI ranking changes the AI plan but
   must NOT change any deterministic baseline.

Two evaluation layers
---------------------
Both layers are AI-provenance-agnostic::

    CandidatePlan
    ├─ PlanEvaluator           ← physical / telecom feasibility
    └─ MissionOutcomeEvaluator ← semantic mission outcome

``PlanEvaluator`` determines WHAT CAN BE DELIVERED (telecom physics).
``MissionOutcomeEvaluator`` determines WHAT THAT DELIVERY MEANS (mission value).

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
from ..agent.local_provider import LocalRuleBasedProvider
from ..agent.provider_factory import get_provider
from ..agent.stage2_blinding import (
    InvalidStage2AliasError,
    Stage2PlanSummary,
    Stage2SummaryBuildError,
    build_blind_mapping,
    build_stage2_summaries,
    map_alias_to_plan_id,
)
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..candidate_generator.ai_plan_builder import build_ai_prioritized_plan
from ..domain.anomaly_policy import is_applicable_anomaly
from ..evaluator.plan_evaluator import PlanEvaluator
from ..evaluator.mission_outcome_evaluator import (
    MissionOutcomeEvaluator,
    MissionOutcomeResult,
)
from ..domain.plan_integrity import PlanSource, compute_plan_fingerprint
from ..models.anomaly_event import AnomalyEvent
from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
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

    ``ai_mission_outcome`` — deterministic mission-outcome evaluation of ``ai_plan``
    (v2/v3 path only).  ``None`` for legacy scenarios.
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
    """Deterministic PlanEvaluator evaluation of ai_plan (v2/v3 path). Null for legacy scenarios."""
    ai_mission_outcome: MissionOutcomeResult | None = None
    """Deterministic MissionOutcomeEvaluator result for ai_plan (v2/v3 path). Null for legacy scenarios."""


def _effective_packets(scenario) -> list[Packet]:
    """Return the effective packet list for the given scenario.

    Uses legacy ``packets`` when present; otherwise bridges ``data_products``.
    Mirrors the same helper in ``routes_plans`` to keep behaviour consistent.
    """
    if scenario.packets:
        return scenario.packets
    return data_products_to_packets(scenario.data_products)


def _is_local_provider(provider) -> bool:
    """Return True when *provider* is the deterministic LocalRuleBasedProvider.

    The local provider does not exhibit self-preference and does not need
    provenance blinding for Stage-2 recommendation.
    """
    return isinstance(provider, LocalRuleBasedProvider)


def _build_blind_recommend(
    provider,
    fallback_provider: LocalRuleBasedProvider,
    all_plans: list[CandidatePlan],
    all_evals: list[EvaluationResult],
    all_outcomes: list[MissionOutcomeResult],
    scenario_id: str,
    *,
    link_state,
    mission_state,
    anomalies: list[AnomalyEvent],
) -> tuple[AIRecommendation, str | None]:
    """Run Stage-2 recommendation with provenance blinding for external providers.

    Uses compact Stage2PlanSummary objects — no CandidatePlan packet lists,
    no dummy plans, no provenance fields.  The provider's choice is an opaque
    option alias which is mapped back to the real plan_id by the trusted backend.

    Production flow:
        plans/evaluations/outcomes
        → build_blind_mapping()
        → build_stage2_summaries()
        → provider.recommend_from_summaries()  ← actual LLM call with compact context
        → map alias → real plan_id
        → rebind risk_score, risk_level, packet_actions from authoritative data

    Returns:
        (recommendation, fallback_reason) — fallback_reason is None when the
        primary provider succeeded.
    """
    # Build the alias map: OPTION-A … OPTION-E → real plan_id
    alias_map = build_blind_mapping(all_plans, scenario_id=scenario_id)

    # Build compact summaries keyed by alias (no provenance)
    summaries = build_stage2_summaries(alias_map, all_plans, all_evals, all_outcomes)

    # Pre-filter to applicable anomalies before passing to the provider.
    # build_stage2_user_message also applies this filter internally, but we
    # filter here too so providers receive clean input regardless of
    # whether they forward it directly or use it for other purposes.
    applicable_anomalies = [ae for ae in anomalies if is_applicable_anomaly(ae)]

    fallback_reason: str | None = None

    # Call external provider with compact summaries (no dummy CandidatePlan objects)
    try:
        aliased_rec = provider.recommend_from_summaries(
            summaries,
            link_state,
            mission_state,
            anomalies=applicable_anomalies,
        )
    except NotImplementedError:
        # Provider doesn't implement compact Stage-2; fall back to local
        logger.warning(
            "Provider '%s' does not implement recommend_from_summaries(). "
            "Falling back to LocalRuleBasedProvider.",
            provider.provider_name,
        )
        try:
            local_rec = fallback_provider.recommend(
                link_state, mission_state, all_plans, all_evals, anomalies=anomalies
            )
            fallback_reason = (
                f"AI provider '{provider.provider_name}' does not implement compact "
                "Stage-2 recommendation. Local rule-based recommendation is in use."
            )
            return local_rec, fallback_reason
        except Exception as fallback_exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Fallback failed after NotImplementedError: {fallback_exc}",
            ) from fallback_exc
    except (AIProviderError, AIResponseError, AIHallucinationError) as exc:
        logger.error(
            "AI provider '%s' failed blind Stage-2 recommendation: %s. "
            "Falling back to LocalRuleBasedProvider.",
            provider.provider_name, exc,
        )
        # Fallback: local provider uses real plans/evals
        try:
            local_rec = fallback_provider.recommend(
                link_state, mission_state, all_plans, all_evals, anomalies=anomalies
            )
            fallback_reason = (
                f"AI provider '{provider.provider_name}' unavailable for plan "
                "recommendation. Local rule-based recommendation is in use."
            )
            return local_rec, fallback_reason
        except Exception as fallback_exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Both primary provider '{provider.provider_name}' and Local "
                    f"fallback failed: {fallback_exc}"
                ),
            ) from fallback_exc

    # Map the recommended alias back to the real plan_id
    recommended_alias = aliased_rec.recommended_plan_id
    try:
        real_plan_id = map_alias_to_plan_id(recommended_alias, alias_map)
    except InvalidStage2AliasError as exc:
        logger.error(
            "Stage-2 provider '%s' returned invalid alias '%s': %s. "
            "Falling back to LocalRuleBasedProvider.",
            provider.provider_name, recommended_alias, exc,
        )
        # Fall back — local provider with real plans
        try:
            local_rec = fallback_provider.recommend(
                link_state, mission_state, all_plans, all_evals, anomalies=anomalies
            )
            if fallback_reason is None:
                fallback_reason = (
                    f"AI provider '{provider.provider_name}' returned invalid option alias "
                    f"'{recommended_alias}'. Local deterministic recommendation is in use."
                )
            return local_rec, fallback_reason
        except Exception as fallback_exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502,
                detail=f"Fallback failed after invalid alias: {fallback_exc}",
            ) from fallback_exc

    # Translate recommendation back to real plan identity
    real_plan = next(p for p in all_plans if p.plan_id == real_plan_id)
    real_eval = next(e for e in all_evals if e.plan_id == real_plan_id)

    # Alternative plan: map alias to real plan_id if present
    real_alt_plan_id: str | None = None
    if aliased_rec.alternative_plan_id:
        try:
            real_alt_plan_id = map_alias_to_plan_id(aliased_rec.alternative_plan_id, alias_map)
        except InvalidStage2AliasError:
            real_alt_plan_id = None  # silently drop invalid alternative alias

    # Rebuild packet_actions from the real plan (authoritative)
    packet_actions = [
        {"packet_id": pkt.packet_id, "action": "transmit", "rank": rank}
        for rank, pkt in enumerate(real_plan.packets, start=1)
    ]

    # Build alias → summary lookup for fast per-option evidence binding.
    summary_by_alias: dict[str, "Stage2PlanSummary"] = {s.option_id: s for s in summaries}  # type: ignore[name-defined]

    # Rebind evidence values from authoritative data.
    # Each evidence item carries its own option_id (which OPTION alias it cited).
    # For candidate_option evidence we bind the value from THAT option's summary,
    # NOT unconditionally from the recommended option.
    # After binding, the OPTION alias is translated to the real plan_id.
    bound_evidence = []
    for item in aliased_rec.evidence:
        bound_val = item.value

        if item.source == "candidate_option":
            # Bind from the specific option this evidence item refers to.
            ev_alias = item.option_id  # OPTION-X alias preserved by parser
            ev_summary = summary_by_alias.get(ev_alias) if ev_alias else None
            if ev_summary is not None:
                auth_val = getattr(ev_summary, item.field, None)
                if auth_val is not None:
                    bound_val = auth_val
            # Translate the OPTION alias to the real plan_id for operator output.
            real_ev_option_id: str | None = None
            if ev_alias is not None:
                try:
                    real_ev_option_id = map_alias_to_plan_id(ev_alias, alias_map)
                except InvalidStage2AliasError:
                    real_ev_option_id = None  # alias was invalid; drop identity
        elif item.source == "evaluation_result":
            auth_val = getattr(real_eval, item.field, None)
            if auth_val is not None:
                bound_val = auth_val
            real_ev_option_id = None
        elif item.source == "link_state":
            auth_val = getattr(link_state, item.field, None)
            if auth_val is not None:
                bound_val = auth_val
            real_ev_option_id = None
        elif item.source == "mission_state":
            auth_val = getattr(mission_state, item.field, None)
            if auth_val is not None:
                bound_val = auth_val
            real_ev_option_id = None
        else:
            real_ev_option_id = None

        bound_evidence.append(EvidenceItem(
            option_id=real_ev_option_id,  # real plan identity (not OPTION alias)
            source=item.source,
            field=item.field,
            value=bound_val,
            interpretation=item.interpretation,
        ))

    recommendation = AIRecommendation(
        recommended_plan_id=real_plan_id,
        packet_actions=packet_actions,
        reasoning=aliased_rec.reasoning,
        confidence=aliased_rec.confidence,
        # Phase 4: confidence from an external LLM is an uncalibrated self-report.
        confidence_semantics="uncalibrated_llm",
        # risk_score and risk_level are ALWAYS from authoritative EvaluationResult.
        risk_score=real_eval.risk_score,
        risk_level=real_eval.risk_level,
        evidence=bound_evidence,
        alternative_plan_id=real_alt_plan_id,
    )
    return recommendation, fallback_reason


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
    4. All **five** plans are evaluated identically by ``PlanEvaluator``
       (telecom/feasibility) and ``MissionOutcomeEvaluator`` (mission outcome).
    5. AI Stage 2 — External providers receive provenance-blind option aliases
       (OPTION-A…OPTION-E) rather than real plan identities.  The model's choice
       is mapped back to the real plan_id by the trusted backend.
       LocalRuleBasedProvider operates on real plan data (deterministic, no bias).

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

    # Import fallback provider — used at both AI stages if needed.
    _fallback = LocalRuleBasedProvider()

    requested_provider_name: str = provider.provider_name

    # Track which provider actually produces each result.
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
    ai_mission_outcome: MissionOutcomeResult | None = None

    weights = SchedulerWeights()
    gen = CandidateGenerator()

    if use_v2_path:
        # ── AI Stage 1: candidate prioritization ─────────────────────────
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

        # ── Step 3: Build ALL five plans ──────────────────────────────────
        # CRITICAL: The four deterministic baselines use the ORIGINAL
        # authoritative packet set, completely independent of AI ranking.
        all_packets = data_products_to_packets(scenario.data_products)

        plans = gen.generate(all_packets, link_state, scenario.mission_state, weights)

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
        plans = gen.generate(
            _effective_packets(scenario),
            link_state,
            scenario.mission_state,
            weights,
        )

    # ── Deterministic evaluation: PlanEvaluator (both paths) ─────────────────
    # Same PlanEvaluator for ALL plans.  No AI bonus.  No provenance check.
    ev = PlanEvaluator()
    evaluations = [
        ev.evaluate(plan, link_state, scenario.mission_state)
        for plan in plans
    ]

    if use_v2_path and ai_plan is not None:
        ai_evaluation = ev.evaluate(ai_plan, link_state, scenario.mission_state)
        all_plans_for_stage2 = plans + [ai_plan]
        all_evals_for_stage2 = evaluations + [ai_evaluation]
    else:
        all_plans_for_stage2 = plans
        all_evals_for_stage2 = evaluations

    # ── Deterministic evaluation: MissionOutcomeEvaluator (v2/v3 path) ───────
    # Second evaluation layer — semantic mission outcomes.
    # Only available on v2/v3 path (data_products provides the authoritative metadata).
    mission_outcomes: list[MissionOutcomeResult] = []
    if use_v2_path:
        outcome_ev = MissionOutcomeEvaluator()
        mission_outcomes = [
            outcome_ev.evaluate(
                plan,
                eval_result,
                scenario.data_products,
                anomalies,
            )
            for plan, eval_result in zip(all_plans_for_stage2, all_evals_for_stage2)
        ]
        # Store the AI plan's outcome separately for the response
        if ai_plan is not None and ai_evaluation is not None:
            ai_mission_outcome = next(
                (mo for mo in mission_outcomes if mo.plan_id == ai_plan.plan_id), None
            )

    # ── Phase 4: register all issued plans ───────────────────────────────────
    # All plans surfaced to the operator (deterministic + optional AI plan) are
    # registered in the issued-plan registry so POST /approve can verify them.
    _scenario_id = scenario.scenario_id
    for _plan in all_plans_for_stage2:
        _plan_source = (
            PlanSource.ai_generated
            if _plan is ai_plan
            else PlanSource.deterministic_generated
        )
        _order_sha, _canonical_sha = compute_plan_fingerprint(_plan, _scenario_id)
        _plan.metadata["plan_source"] = _plan_source.value
        state.register_issued_plan(
            _plan,
            scenario_id=_scenario_id,
            packet_order_sha256=_order_sha,
            canonical_plan_sha256=_canonical_sha,
            plan_source_value=_plan_source.value,
        )

    # ── AI Stage 2: plan recommendation ──────────────────────────────────────
    recommendation_fallback_reason: str | None = None

    if use_v2_path and not _is_local_provider(provider):
        # External LLM: use provenance-blind Stage-2 recommendation.
        scenario_id = scenario.scenario_id
        recommendation, recommendation_fallback_reason = _build_blind_recommend(
            provider,
            _fallback,
            all_plans_for_stage2,
            all_evals_for_stage2,
            mission_outcomes,
            scenario_id,
            link_state=link_state,
            mission_state=scenario.mission_state,
            anomalies=anomalies,
        )
        if recommendation_fallback_reason:
            actual_recommendation_provider = _fallback.provider_name
    else:
        # Local provider or legacy path: use direct recommendation (no blinding needed)
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
        provider=actual_provider_name,
        requested_provider=requested_provider_name,
        actual_provider=actual_provider_name,
        recommendation=recommendation,
        prioritization=prioritization,
        candidate_count=candidate_count,
        prioritization_fallback_reason=prioritization_fallback_reason,
        recommendation_fallback_reason=recommendation_fallback_reason,
        prioritization_error=prioritization_fallback_reason,
        ai_plan=ai_plan,
        ai_evaluation=ai_evaluation,
        ai_mission_outcome=ai_mission_outcome,
    )
