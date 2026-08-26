"""Deterministic semantic rule prioritizer — shared comparison infrastructure.

``SemanticRulePrioritizer`` applies the same structured semantic heuristic
used by :class:`~backend.app.agent.local_provider.LocalRuleBasedProvider`
as a standalone, reusable class.

Purpose
-------
This class exists to support fair scientific benchmarking.  It gives the
deterministic control group the same *structured metadata* access as the
LLM-based plan — anomaly severity, criticality, mission relevance,
scientific value, deadline urgency — and ranks candidates accordingly.

The ranking is NOT AI reasoning.  It is a transparent, fully documented
composite heuristic that can be inspected, audited, and reproduced.

When to use
-----------
* Building a ``semantic-rule-based`` comparator plan (benchmark/ablation use).
* Providing the fallback ordering inside ``LocalRuleBasedProvider``.
* Any context where deterministic semantic ordering is needed without an LLM.

Algorithm (documented)
----------------------
1. Anomaly-linked products ranked first by anomaly severity (descending).
2. Remaining products ranked by composite urgency score::

       0.35 × criticality
     + 0.30 × mission_relevance
     + 0.20 × scientific_value
     + 0.15 × deadline_urgency

   where ``deadline_urgency = max(0, 1 - deadline_s / 600)``.
3. Ties broken by ``product_id`` (lexicographic) for strict determinism.

This is identical to the algorithm in ``LocalRuleBasedProvider.prioritize_candidates()``.
``LocalRuleBasedProvider`` now delegates to this class so the logic lives in
one place only.

Design constraints
------------------
* No LLM calls, no randomness, no network I/O.
* Same input → exact same output (deterministic).
* No knowledge of plan provenance (AI vs non-AI).
"""

from __future__ import annotations

from typing import Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_prioritization import CandidatePrioritization, RankedProduct
from ..models.candidate_summary import CandidateSummary


class SemanticRulePrioritizer:
    """Deterministic structured-metadata semantic prioritizer.

    Ranks :class:`~backend.app.models.candidate_summary.CandidateSummary`
    objects using anomaly severity and composite urgency score.

    This is the deterministic *comparator* used for scientific benchmarking.
    It receives the same bounded candidate set, the same anomaly context, and
    the same structured metadata as the LLM — but produces its ranking via
    explicit documented rules rather than generative inference.

    Args:
        high_severity_threshold: Severity threshold above which an anomaly is
            labelled "high severity" in the output factors.  Default ``0.75``.
    """

    def __init__(self, high_severity_threshold: float = 0.75) -> None:
        self._hs_threshold = high_severity_threshold

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def prioritize(
        self,
        candidates: Sequence[CandidateSummary],
        anomalies: Sequence[AnomalyEvent] | None = None,
    ) -> CandidatePrioritization:
        """Rank *candidates* deterministically using the semantic rule heuristic.

        Args:
            candidates: Pre-filtered :class:`CandidateSummary` list.
            anomalies:  Active anomaly events (used to sort anomaly-linked
                        products by anomaly severity).

        Returns:
            A :class:`CandidatePrioritization` with ranked products.
            All candidates are ranked (unlike an LLM which may omit some).
        """
        if not candidates:
            return CandidatePrioritization(
                ranked_products=[],
                overall_reasoning=(
                    "No candidates supplied to SemanticRulePrioritizer. "
                    "This is deterministic semantic-rule prioritization — not AI reasoning."
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

        ranked_candidates = sorted(candidates, key=_sort_key)

        ranked_products: list[RankedProduct] = []
        for priority, cs in enumerate(ranked_candidates, start=1):
            anom_severity = severity_map.get(cs.anomaly_id or "", None)

            # Build structured decision factors
            factors: list[str] = []
            if cs.anomaly_id and anom_severity is not None:
                factors.append("active anomaly")
                if anom_severity >= self._hs_threshold:
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
                    f"[Semantic-rule ranking] Anomaly-linked product ({cs.anomaly_id}, "
                    f"severity={anom_severity:.2f}); subsystem={cs.subsystem}; "
                    f"criticality={cs.criticality:.2f}."
                )
            else:
                reason = (
                    f"[Semantic-rule ranking] subsystem={cs.subsystem}; "
                    f"criticality={cs.criticality:.2f}; "
                    f"mission_relevance={cs.mission_relevance:.2f}; "
                    f"scientific_value={cs.scientific_value:.2f}; "
                    f"deadline_s={cs.deadline_s:.0f}."
                )

            ranked_products.append(RankedProduct(
                product_id=cs.product_id,
                priority=priority,
                reason=reason,
                description=cs.description,
                factors=factors,
                anomaly_ids=[cs.anomaly_id] if cs.anomaly_id else [],
                subsystem=cs.subsystem,
                confidence=None,  # deterministic ranking does not report per-item confidence
            ))

        n_anomaly = sum(1 for cs in candidates if cs.anomaly_id is not None)
        n_candidates = len(candidates)

        overall_reasoning = (
            f"Deterministic semantic-rule ranking of {n_candidates} candidate(s). "
            f"{n_anomaly} product(s) are linked to active anomalies and were ranked first "
            f"by anomaly severity.  Remaining products ranked by composite urgency score "
            f"(0.35×criticality + 0.30×mission_relevance + 0.20×scientific_value + "
            f"0.15×deadline_urgency).  Ties broken by product_id.  "
            f"NOTE: This is deterministic semantic-rule prioritization — NOT AI/LLM reasoning.  "
            f"Use this for scientific comparison with LLM-based prioritization."
        )

        # Top-level decision factors
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
            candidate_count=n_candidates,
        )
