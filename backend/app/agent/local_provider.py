"""Local rule-based AI provider — works with zero network dependencies.

``LocalRuleBasedProvider`` generates a valid, explainable ``AIRecommendation``
by reasoning over the pre-computed ``EvaluationResult`` objects produced by
``PlanEvaluator``.  It requires no API key, no running model, and no internet
connection.

Algorithm — recommend()
-----------------------
1. Select the plan with the lowest ``risk_score`` from the evaluations.
   Ties are broken by highest ``mission_value``, then by stable plan order.
2. Select the plan with the second-best risk/mission-value trade-off as the
   ``alternative_plan_id`` (if more than one plan is available).
3. Derive ``confidence`` from the gap between the best and second-best
   risk scores: a larger gap means higher confidence.
4. Build structured ``EvidenceItem`` objects that cite only real fields from
   the provided ``LinkState``, ``MissionState``, and ``EvaluationResult``.
5. Return a fully validated ``AIRecommendation``.

Supports an arbitrary number of plans (4, 5, or more).  No hard-coded
assumption about the count.

Algorithm — prioritize_candidates()
------------------------------------
Delegates to
:class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
so the deterministic semantic ordering logic lives in one canonical place.

This method is a **deterministic fallback** — it performs no semantic
reasoning and does not invoke any LLM.  Its purpose is to keep the
system functional when no AI provider is available, and to serve as a
stable baseline for testing.  It is NOT a substitute for genuine AI
semantic prioritization.

This provider is intentionally conservative: it never fabricates values and
never deviates from the facts already computed by the deterministic pipeline.

Why not a mock?
---------------
The ``LocalRuleBasedProvider`` is NOT a mock.  It performs real reasoning:
it compares all candidate plans across two objective metrics
(risk_score, mission_value), constructs structured evidence that cites actual
field values from the provided state, and produces a recommendation that would
change if the inputs changed.  The output is deterministic given the same
inputs, which makes it easy to test.
"""

from __future__ import annotations

from typing import Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from ..models.risk_level import RiskLevel
from .base_provider import AIProviderError, AIResponseError, BaseAIProvider
from .semantic_rule_prioritizer import SemanticRulePrioritizer


def _risk_level_from_score(score: float) -> RiskLevel:
    if score < 0.25:
        return RiskLevel.LOW
    if score < 0.50:
        return RiskLevel.MEDIUM
    if score < 0.75:
        return RiskLevel.HIGH
    return RiskLevel.CRITICAL


def _confidence_from_gap(best_risk: float, second_risk: float) -> float:
    """Derive confidence from the risk-score gap between best and runner-up.

    A gap of 0 (identical risk scores) → confidence 0.5 (no preference).
    A gap of 0.5+ → confidence 0.95 (strong preference).
    """
    gap = abs(second_risk - best_risk)
    # Linear ramp: 0.5 at gap=0, 0.95 at gap≥0.5
    return min(0.5 + gap * 0.9, 0.95)


class LocalRuleBasedProvider(BaseAIProvider):
    """Deterministic rule-based recommendation provider requiring no API key.

    Reasons over pre-evaluated plans and produces a valid ``AIRecommendation``
    using only the deterministic facts supplied by the pipeline.

    This is the default provider when ``GCSI_GRANITE_API_KEY`` is not set.
    """

    @property
    def provider_name(self) -> str:
        return "Local"

    def recommend(
        self,
        link_state: LinkState,
        mission_state: MissionState,
        plans: list[CandidatePlan],
        evaluations: list[EvaluationResult],
        *,
        anomalies: list[AnomalyEvent] | None = None,
    ) -> AIRecommendation:
        """Generate a recommendation from pre-evaluated plan metrics.

        Args:
            link_state:    Current link snapshot.
            mission_state: Current mission snapshot.
            plans:         All candidate plans.
            evaluations:   Deterministic evaluation results for each plan.
            anomalies:     Optional list of active anomaly events (Phase 2A).

        Returns:
            A validated :class:`AIRecommendation`.

        Raises:
            AIProviderError:  If no evaluations are provided.
            AIResponseError:  If a recommendation cannot be derived.
        """
        if not evaluations:
            raise AIProviderError("LocalRuleBasedProvider: no evaluations provided")
        if not plans:
            raise AIProviderError("LocalRuleBasedProvider: no plans provided")

        valid_plan_ids = {p.plan_id for p in plans}
        # Guard: drop evaluations that have no matching plan
        valid_evals = [e for e in evaluations if e.plan_id in valid_plan_ids]
        if not valid_evals:
            raise AIResponseError(
                "LocalRuleBasedProvider: evaluations do not match any provided plan"
            )

        # ── Step 1: Rank plans by (risk_score ASC, mission_value DESC) ──────
        ranked = sorted(
            valid_evals,
            key=lambda e: (e.risk_score, -e.mission_value, e.plan_id),
        )
        best_eval = ranked[0]
        runner_up_eval = ranked[1] if len(ranked) > 1 else None

        recommended_plan_id = best_eval.plan_id
        alternative_plan_id: str | None = (
            runner_up_eval.plan_id if runner_up_eval is not None else None
        )

        # ── Step 2: Derive confidence ────────────────────────────────────────
        second_risk = runner_up_eval.risk_score if runner_up_eval else best_eval.risk_score
        confidence = _confidence_from_gap(best_eval.risk_score, second_risk)

        # ── Step 3: Build reasoning text ─────────────────────────────────────
        n_plans = len(ranked)
        reasoning_parts = [
            f"Evaluated {n_plans} candidate plan(s) against current link and mission state.",
            f"Plan '{recommended_plan_id}' was selected because it has the lowest risk score "
            f"({best_eval.risk_score:.3f}) among all candidates.",
        ]
        if best_eval.mission_value > 0:
            reasoning_parts.append(
                f"It delivers a mission value of {best_eval.mission_value:.3f}, "
                f"covering {best_eval.critical_packets_delivered}/"
                f"{best_eval.total_critical_packets} critical packets."
            )
        if best_eval.deadline_misses > 0:
            reasoning_parts.append(
                f"Note: {best_eval.deadline_misses} packet(s) are at risk of missing "
                f"their deadline under this plan."
            )
        if alternative_plan_id:
            reasoning_parts.append(
                f"Alternative plan '{alternative_plan_id}' (risk: "
                f"{runner_up_eval.risk_score:.3f}) is recommended if this plan "
                f"cannot be approved."
            )
        reasoning_parts.append(
            f"Link BER is {link_state.ber:.2e} with {link_state.remaining_window_s:.0f}s "
            f"remaining in the communication window."
        )
        # Include anomaly context when v2 data is present.
        active_anomalies = anomalies or []
        if active_anomalies:
            severe = sorted(active_anomalies, key=lambda a: -a.severity)
            top = severe[0]
            reasoning_parts.append(
                f"Active anomaly context: {len(active_anomalies)} anomaly event(s) on the "
                f"spacecraft.  Highest severity: {top.anomaly_id} ({top.subsystem}, "
                f"severity={top.severity:.2f}, status={top.status}).  Data products "
                f"linked to active anomalies should be prioritised."
            )
        reasoning = "  ".join(reasoning_parts)

        # ── Step 4: Build evidence items (all fields must be real) ───────────
        evidence: list[EvidenceItem] = [
            EvidenceItem(
                source="evaluation_result",
                field="risk_score",
                value=best_eval.risk_score,
                interpretation=(
                    f"Lowest risk score among {n_plans} candidate plans; "
                    f"risk level is {best_eval.risk_level.value}."
                ),
            ),
            EvidenceItem(
                source="evaluation_result",
                field="mission_value",
                value=best_eval.mission_value,
                interpretation=(
                    f"Sum of criticality × mission_relevance for delivered packets; "
                    f"higher is better."
                ),
            ),
            EvidenceItem(
                source="link_state",
                field="ber",
                value=link_state.ber,
                interpretation=(
                    "Bit error rate at the time of evaluation; "
                    "lower BER improves delivery probability."
                ),
            ),
            EvidenceItem(
                source="link_state",
                field="remaining_window_s",
                value=link_state.remaining_window_s,
                interpretation=(
                    "Remaining communication window in seconds; "
                    "all packets must be delivered within this budget."
                ),
            ),
            EvidenceItem(
                source="mission_state",
                field="comm_window_remaining_s",
                value=mission_state.comm_window_remaining_s,
                interpretation=(
                    "Mission-level communication window budget; "
                    "used as upper bound on transmission scheduling."
                ),
            ),
            EvidenceItem(
                source="evaluation_result",
                field="bandwidth_utilization",
                value=best_eval.bandwidth_utilization,
                interpretation=(
                    f"Fraction of available bandwidth consumed by this plan "
                    f"({best_eval.bandwidth_utilization:.1%})."
                ),
            ),
        ]

        # ── Step 5: Build packet_actions from recommended plan ───────────────
        recommended_plan = next(
            (p for p in plans if p.plan_id == recommended_plan_id), None
        )
        if recommended_plan is None:
            raise AIResponseError(
                f"LocalRuleBasedProvider: recommended plan '{recommended_plan_id}' "
                f"not found in provided plans"
            )
        packet_actions = [
            {"packet_id": pkt.packet_id, "action": "transmit", "rank": rank}
            for rank, pkt in enumerate(recommended_plan.packets, start=1)
        ]

        # ── Step 6: Derive risk level from best plan's risk score ────────────
        risk_level = _risk_level_from_score(best_eval.risk_score)

        return AIRecommendation(
            recommended_plan_id=recommended_plan_id,
            packet_actions=packet_actions,
            reasoning=reasoning,
            confidence=confidence,
            # Phase 4: LocalRuleBasedProvider uses a deterministic risk-gap
            # heuristic — not an LLM self-report.
            confidence_semantics="heuristic",
            risk_score=best_eval.risk_score,
            risk_level=risk_level,
            evidence=evidence,
            alternative_plan_id=alternative_plan_id,
        )

    def prioritize_candidates(
        self,
        candidates: Sequence[CandidateSummary],
        link_state: LinkState,
        mission_state: MissionState,
        anomalies: Sequence[AnomalyEvent] | None = None,
        *,
        distance_km: float | None = None,
    ) -> CandidatePrioritization:
        """Rank candidates deterministically by composite urgency score.

        Delegates to :class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
        so the deterministic semantic ordering logic lives in one canonical place.

        This is a **deterministic fallback** — it performs no semantic
        reasoning and does not invoke any LLM.  Its purpose is to keep the
        system functional when no AI provider is available, and to serve as a
        stable baseline for testing.  It is NOT a substitute for genuine AI
        semantic prioritization.

        The ``distance_km`` parameter is used to append a spacecraft geometry
        sentence to the overall_reasoning when provided.
        """
        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize(candidates, anomalies=list(anomalies) if anomalies else [])

        # Append geometry sentence to overall_reasoning when distance is known.
        # This matches the original LocalRuleBasedProvider behaviour for backwards
        # compatibility with existing tests and UI display.
        if distance_km is not None:
            _C = 299_792_458.0
            propagation_delay_s = (distance_km * 1_000.0) / _C
            geometry_sentence = (
                f"  Spacecraft is {distance_km / 1_000_000:.1f} million km from Earth; "
                f"one-way signal propagation ≈ {propagation_delay_s:.0f} s."
            )
            # Rebuild result with appended reasoning (CandidatePrioritization is immutable
            # via Pydantic, so we recreate it)
            from ..models.candidate_prioritization import CandidatePrioritization as _CP
            result = _CP(
                ranked_products=result.ranked_products,
                overall_reasoning=result.overall_reasoning + geometry_sentence,
                confidence=result.confidence,
                decision_factors=result.decision_factors,
                candidate_count=result.candidate_count,
            )
        return result
