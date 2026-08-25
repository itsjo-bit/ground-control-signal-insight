"""Local rule-based AI provider — works with zero network dependencies.

``LocalRuleBasedProvider`` generates a valid, explainable ``AIRecommendation``
by reasoning over the pre-computed ``EvaluationResult`` objects produced by
``PlanEvaluator``.  It requires no API key, no running model, and no internet
connection.

Algorithm
---------
1. Select the plan with the lowest ``risk_score`` from the evaluations.
   Ties are broken by highest ``mission_value``, then by stable plan order.
2. Select the plan with the second-best risk/mission-value trade-off as the
   ``alternative_plan_id`` (if more than one plan is available).
3. Derive ``confidence`` from the gap between the best and second-best
   risk scores: a larger gap means higher confidence.
4. Build structured ``EvidenceItem`` objects that cite only real fields from
   the provided ``LinkState``, ``MissionState``, and ``EvaluationResult``.
5. Return a fully validated ``AIRecommendation``.

This provider is intentionally conservative: it never fabricates values and
never deviates from the facts already computed by the deterministic pipeline.

Why not a mock?
---------------
The ``LocalRuleBasedProvider`` is NOT a mock.  It performs real reasoning:
it compares all four candidate plans across two objective metrics
(risk_score, mission_value), constructs structured evidence that cites actual
field values from the provided state, and produces a recommendation that would
change if the inputs changed.  The output is deterministic given the same
inputs, which makes it easy to test.
"""

from __future__ import annotations

from typing import Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization, RankedProduct
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.evidence_item import EvidenceItem
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.recommendation import AIRecommendation
from ..models.risk_level import RiskLevel
from .base_provider import AIProviderError, AIResponseError, BaseAIProvider


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

        This is a **deterministic fallback** — it performs no semantic
        reasoning and does not invoke any LLM.  Its purpose is to keep the
        system functional when no AI provider is available, and to serve as a
        stable baseline for testing.

        Algorithm
        ---------
        1. Products with an ``anomaly_id`` linking them to a high-severity
           anomaly are ranked first (severity descending).
        2. Remaining products are ranked by a composite score:
           ``0.35 * criticality + 0.30 * mission_relevance
             + 0.20 * scientific_value + 0.15 * deadline_urgency``
           where ``deadline_urgency = max(0, 1 - deadline_s / 600)``.
        3. Ties are broken by ``product_id`` for determinism.

        The reasoning for each product explicitly notes that this is a
        deterministic ranking, not semantic AI reasoning.
        """
        if not candidates:
            return CandidatePrioritization(
                ranked_products=[],
                overall_reasoning=(
                    "No candidates supplied to LocalRuleBasedProvider. "
                    "This is a deterministic fallback — not AI reasoning."
                ),
                confidence=0.5,
                decision_factors=[],
                candidate_count=0,
            )

        # Build anomaly severity lookup
        severity_map: dict[str, float] = {}
        if anomalies:
            for ae in anomalies:
                severity_map[ae.anomaly_id] = ae.severity

        def _sort_key(cs: CandidateSummary) -> tuple:
            anomaly_severity = severity_map.get(cs.anomaly_id or "", 0.0)
            deadline_urgency = max(0.0, 1.0 - cs.deadline_s / 600.0)
            composite = (
                0.35 * cs.criticality
                + 0.30 * cs.mission_relevance
                + 0.20 * cs.scientific_value
                + 0.15 * deadline_urgency
            )
            # Negate so higher values sort first
            return (-anomaly_severity, -composite, cs.product_id)

        ranked = sorted(candidates, key=_sort_key)

        ranked_products: list[RankedProduct] = []
        for priority, cs in enumerate(ranked, start=1):
            anom_severity = severity_map.get(cs.anomaly_id or "", None)
            # Build structured decision factors (Phase 2D)
            factors: list[str] = []
            if cs.anomaly_id and anom_severity is not None:
                factors.append("active anomaly")
                if anom_severity >= 0.75:
                    factors.append("high severity anomaly")
            if cs.criticality >= 0.75:
                factors.append("high criticality")
            elif cs.criticality >= 0.5:
                factors.append("medium criticality")
            if cs.deadline_s <= 120.0:
                factors.append("deadline urgency")
            if cs.mission_relevance >= 0.75:
                factors.append("mission relevance")
            if cs.scientific_value >= 0.75:
                factors.append("scientific value")
            if cs.related_ids:
                factors.append("related products")
            if not factors:
                factors.append("routine housekeeping")

            if cs.anomaly_id and anom_severity is not None:
                reason = (
                    f"[Deterministic ranking] Anomaly-linked product ({cs.anomaly_id}, "
                    f"severity={anom_severity:.2f}); subsystem={cs.subsystem}; "
                    f"criticality={cs.criticality:.2f}."
                )
            else:
                reason = (
                    f"[Deterministic ranking] subsystem={cs.subsystem}; "
                    f"criticality={cs.criticality:.2f}; "
                    f"mission_relevance={cs.mission_relevance:.2f}; "
                    f"scientific_value={cs.scientific_value:.2f}; "
                    f"deadline_s={cs.deadline_s:.0f}."
                )
            ranked_products.append(
                RankedProduct(
                    product_id=cs.product_id,
                    priority=priority,
                    reason=reason,
                    # Phase 2E-D3: forward description from CandidateSummary
                    description=cs.description,
                    factors=factors,
                    anomaly_ids=[cs.anomaly_id] if cs.anomaly_id else [],
                    subsystem=cs.subsystem,
                    # Local provider does not report per-product confidence
                    confidence=None,
                )
            )

        n_anomaly = sum(1 for cs in candidates if cs.anomaly_id is not None)
        # Speed of light (m/s) — same constant as routes_state._SPEED_OF_LIGHT_M_S
        _C = 299_792_458.0
        geometry_sentence = ""
        if distance_km is not None:
            propagation_delay_s = (distance_km * 1_000.0) / _C
            geometry_sentence = (
                f"  Spacecraft is {distance_km / 1_000_000:.1f} million km from Earth; "
                f"one-way signal propagation ≈ {propagation_delay_s:.0f} s."
            )
        overall_reasoning = (
            f"Deterministic fallback ranking of {len(candidates)} candidate(s). "
            f"{n_anomaly} product(s) are linked to active anomalies and were ranked first "
            f"by anomaly severity. Remaining products ranked by composite urgency score "
            f"(criticality × mission_relevance × scientific_value × deadline urgency). "
            f"NOTE: This is NOT semantic AI reasoning. "
            f"Use a real AI provider (Granite, Gemini, Ollama) for genuine prioritization."
            f"{geometry_sentence}"
        )

        # Top-level decision factors for the overall prioritization
        top_factors: list[str] = []
        if n_anomaly > 0:
            top_factors.append("active anomaly")
        if any(cs.criticality >= 0.75 for cs in candidates):
            top_factors.append("high criticality")
        if any(cs.deadline_s <= 120.0 for cs in candidates):
            top_factors.append("deadline urgency")
        if any(cs.mission_relevance >= 0.75 for cs in candidates):
            top_factors.append("mission relevance")
        if not top_factors:
            top_factors.append("composite urgency score")

        return CandidatePrioritization(
            ranked_products=ranked_products,
            overall_reasoning=overall_reasoning,
            confidence=0.60,
            decision_factors=top_factors,
            candidate_count=len(candidates),
        )
