"""Mission-outcome evaluator — semantic ground truth layer.

``MissionOutcomeEvaluator.evaluate()`` measures what the delivered data
*means* for the mission.  It is the second deterministic evaluation layer,
complementing :class:`~backend.app.evaluator.plan_evaluator.PlanEvaluator`.

Architecture
------------
::

    CandidatePlan
    ├─ PlanEvaluator          ← physical / telecom feasibility
    └─ MissionOutcomeEvaluator ← mission-semantic outcome (this module)

Both evaluators are **AI-provenance-agnostic** — they accept any plan and
produce the same result for the same inputs regardless of whether the plan
came from an AI provider, a deterministic baseline, or a human override.

Responsibility split
--------------------
``PlanEvaluator`` determines **what can be delivered** — it runs the
telecom model and produces ``EvaluationResult.deferred_packets``.

``MissionOutcomeEvaluator`` determines **what that delivery means** — it
looks at the mission metadata (scientific_value, delivery_requirement,
anomaly linkage) of the packets that were *not* deferred.

This is the critical coupling point:

    delivered_product ≡
        product_id ∈ CandidatePlan.packets
        AND product_id ∉ EvaluationResult.deferred_packets

The semantic evaluator therefore depends on the physical evaluator's
verdict but does *not* reimplement window feasibility.

Zero-denominator policy
-----------------------
When a metric's denominator is zero (e.g. no required products in the
scenario) the *rate* field is ``None`` rather than a fictitious 1.0.
Raw counts remain 0.  This prevents false "perfect performance" signals.

Anomaly-weighted coverage formula
----------------------------------
For each active anomaly *i* that has at least one linked product::

    coverage_i = delivered_linked_i / total_linked_i

    anomaly_weighted_coverage =
        Σ(severity_i × coverage_i) / Σ(severity_i)

Only anomalies with ≥1 linked product participate in the weighted sum.

High-severity anomaly threshold
---------------------------------
Default: ``severity >= 0.75``.  Configurable via the ``high_severity_threshold``
constructor argument.  The threshold is documented in the ``MissionOutcomeResult``
so downstream tooling can verify which definition was used.
"""

from __future__ import annotations

import statistics
from typing import Sequence

from pydantic import BaseModel, Field

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.data_product import DataProduct
from ..models.evaluation_result import EvaluationResult

# ---------------------------------------------------------------------------
# High-severity anomaly threshold
# ---------------------------------------------------------------------------

#: Default severity threshold above which an anomaly is counted as "high severity".
DEFAULT_HIGH_SEVERITY_THRESHOLD: float = 0.75


# ---------------------------------------------------------------------------
# Typed result model
# ---------------------------------------------------------------------------


class AnomalyCoverageDetail(BaseModel):
    """Per-anomaly coverage breakdown.

    Fields
    ------
    anomaly_id
        Anomaly identifier, e.g. ``"ANOM-017"``.
    severity
        Authoritative severity from the scenario AnomalyEvent [0, 1].
    total_linked_products
        Number of data products whose ``anomaly_id`` matches this anomaly.
    delivered_linked_products
        Number of those products that were delivered (not deferred).
    coverage_rate
        Fraction of linked products delivered.  ``None`` when
        ``total_linked_products == 0`` (anomaly exists but has no linked products).
    """

    anomaly_id: str
    severity: float = Field(ge=0.0, le=1.0)
    total_linked_products: int = Field(ge=0)
    delivered_linked_products: int = Field(ge=0)
    coverage_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Fraction of linked products delivered; null when denominator is 0",
    )


class MissionOutcomeResult(BaseModel):
    """Deterministic multi-dimensional mission-semantic evaluation result.

    Produced by :class:`MissionOutcomeEvaluator`.

    All rates are ``None`` when their denominator is zero to prevent false
    "perfect performance" signals.

    Fields
    ------
    plan_id
        Matches the ``CandidatePlan.plan_id`` that was evaluated.

    Product delivery
        total_products, delivered_products, delivery_rate

    Scientific value capture
        total_scientific_value, delivered_scientific_value, scientific_value_capture_rate

        ``scientific_value_capture_rate`` is null when total_scientific_value == 0.

    Required-product delivery
        required_products_total, required_products_delivered, required_delivery_rate

        ``required_delivery_rate`` is null when required_products_total == 0.

    Active-anomaly product delivery
        active_anomaly_products_total, active_anomaly_products_delivered,
        active_anomaly_delivery_rate

        ``active_anomaly_delivery_rate`` is null when no active-anomaly products exist.

    High-severity anomaly coverage
        high_severity_anomalies_total, high_severity_anomalies_covered,
        high_severity_anomaly_coverage_rate, high_severity_threshold

        ``high_severity_anomaly_coverage_rate`` is null when no high-severity anomalies exist.

    Anomaly-weighted coverage
        anomaly_weighted_coverage — Σ(severity_i × coverage_i) / Σ(severity_i).
        Null when no active anomaly has linked products.

    Data age
        average_delivered_age_s, median_delivered_age_s
        Both null when no products were delivered.

    Subsystem breakdown
        delivered_by_subsystem — dict mapping subsystem → delivered count.

    Per-anomaly detail
        anomaly_coverage_by_id — list of :class:`AnomalyCoverageDetail` objects.
    """

    plan_id: str

    # ── Product delivery ─────────────────────────────────────────────────────
    total_products: int = Field(ge=0)
    delivered_products: int = Field(ge=0)
    delivery_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    # ── Scientific value capture ─────────────────────────────────────────────
    total_scientific_value: float = Field(ge=0.0)
    delivered_scientific_value: float = Field(ge=0.0)
    scientific_value_capture_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    # ── Required-product delivery ─────────────────────────────────────────────
    required_products_total: int = Field(ge=0)
    required_products_delivered: int = Field(ge=0)
    required_delivery_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    # ── Active-anomaly product delivery ──────────────────────────────────────
    active_anomaly_products_total: int = Field(ge=0)
    active_anomaly_products_delivered: int = Field(ge=0)
    active_anomaly_delivery_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    # ── High-severity anomaly coverage ───────────────────────────────────────
    high_severity_threshold: float = Field(
        ge=0.0, le=1.0,
        description="Documented severity threshold used for high-severity classification",
    )
    high_severity_anomalies_total: int = Field(ge=0)
    high_severity_anomalies_covered: int = Field(ge=0)
    high_severity_anomaly_coverage_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    # ── Anomaly-weighted coverage ─────────────────────────────────────────────
    anomaly_weighted_coverage: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Σ(severity_i × coverage_i) / Σ(severity_i) for active anomalies with linked products. "
            "Null when no active anomaly has linked products."
        ),
    )

    # ── Data age ─────────────────────────────────────────────────────────────
    average_delivered_age_s: float | None = Field(
        default=None, ge=0.0,
        description="Mean age_s across delivered products; null when nothing delivered",
    )
    median_delivered_age_s: float | None = Field(
        default=None, ge=0.0,
        description="Median age_s across delivered products; null when nothing delivered",
    )

    # ── Subsystem breakdown ───────────────────────────────────────────────────
    delivered_by_subsystem: dict[str, int] = Field(
        default_factory=dict,
        description="Number of delivered products per spacecraft subsystem",
    )

    # ── Per-anomaly detail ────────────────────────────────────────────────────
    anomaly_coverage_by_id: list[AnomalyCoverageDetail] = Field(
        default_factory=list,
        description="Per-anomaly coverage breakdown for all active anomalies",
    )


# ---------------------------------------------------------------------------
# MissionOutcomeEvaluator
# ---------------------------------------------------------------------------


class MissionOutcomeEvaluator:
    """Evaluate mission-semantic outcomes of a transmission plan.

    This evaluator is **fully deterministic** and **AI-provenance-agnostic**.
    It does not:

    * call any LLM or external service
    * read the plan's ``strategy``, ``generated_by``, or ``metadata``
    * apply bonuses for AI-generated plans
    * introduce any randomness

    It uses:

    * ``CandidatePlan.packets`` — to know which products are in the plan
    * ``EvaluationResult.deferred_packets`` — to know which were deferred
    * ``DataProduct[]`` — authoritative product metadata
    * ``AnomalyEvent[]`` — authoritative anomaly metadata

    Args:
        high_severity_threshold: Severity threshold for classifying an anomaly
            as "high severity" (default ``0.75``).  Must be in [0.0, 1.0].
    """

    def __init__(self, high_severity_threshold: float = DEFAULT_HIGH_SEVERITY_THRESHOLD) -> None:
        if not (0.0 <= high_severity_threshold <= 1.0):
            raise ValueError(
                f"high_severity_threshold must be in [0, 1]; got {high_severity_threshold}"
            )
        self._high_severity_threshold = high_severity_threshold

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def evaluate(
        self,
        plan: CandidatePlan,
        evaluation_result: EvaluationResult,
        data_products: Sequence[DataProduct],
        anomalies: Sequence[AnomalyEvent],
    ) -> MissionOutcomeResult:
        """Evaluate mission-semantic outcomes of *plan*.

        Args:
            plan:              The transmission plan being evaluated.
            evaluation_result: Deterministic ``PlanEvaluator`` result for *plan*.
                               ``deferred_packets`` is used to derive physical
                               delivery status.
            data_products:     Authoritative list of all mission data products.
            anomalies:         Authoritative list of active anomaly events.

        Returns:
            :class:`MissionOutcomeResult` with multi-dimensional semantic metrics.

        Notes:
            The ``plan`` and ``evaluation_result`` must correspond to the same
            plan (same ``plan_id``).  No exception is raised if they differ, but
            the metrics will be meaningless.
        """
        # ── Build authoritative lookups ───────────────────────────────────────
        # product_id → DataProduct (authoritative metadata)
        product_map: dict[str, DataProduct] = {dp.product_id: dp for dp in data_products}

        # Set of deferred product IDs (from PlanEvaluator — physical ground truth)
        deferred_set: set[str] = set(evaluation_result.deferred_packets)

        # Active anomaly ID set and severity lookup
        active_anomaly_ids: set[str] = {ae.anomaly_id for ae in anomalies}
        anomaly_severity: dict[str, float] = {ae.anomaly_id: ae.severity for ae in anomalies}

        # ── Classify plan products as delivered vs deferred ───────────────────
        # A product is "delivered" iff it is in the plan AND not deferred.
        delivered_ids: set[str] = set()
        for pkt in plan.packets:
            if pkt.packet_id not in deferred_set:
                delivered_ids.add(pkt.packet_id)

        # ── Only consider products that appear in the plan ────────────────────
        # We measure outcomes for products in the plan, using authoritative metadata.
        plan_product_ids = [pkt.packet_id for pkt in plan.packets]
        plan_products: list[DataProduct] = [
            product_map[pid] for pid in plan_product_ids if pid in product_map
        ]
        total_products = len(plan_products)

        # ── Product delivery counts ───────────────────────────────────────────
        delivered_products_list: list[DataProduct] = [
            dp for dp in plan_products if dp.product_id in delivered_ids
        ]
        delivered_count = len(delivered_products_list)
        delivery_rate: float | None = (
            delivered_count / total_products if total_products > 0 else None
        )

        # ── Scientific value capture ──────────────────────────────────────────
        total_sci_val = sum(dp.scientific_value for dp in plan_products)
        delivered_sci_val = sum(
            dp.scientific_value for dp in plan_products if dp.product_id in delivered_ids
        )
        sci_capture_rate: float | None = (
            delivered_sci_val / total_sci_val if total_sci_val > 0.0 else None
        )

        # ── Required-product delivery ─────────────────────────────────────────
        required_products = [
            dp for dp in plan_products if dp.delivery_requirement == "required"
        ]
        req_total = len(required_products)
        req_delivered = sum(1 for dp in required_products if dp.product_id in delivered_ids)
        req_rate: float | None = req_delivered / req_total if req_total > 0 else None

        # ── Active-anomaly product delivery ───────────────────────────────────
        # Products linked to an anomaly that appears in the active anomaly list.
        anomaly_products = [
            dp for dp in plan_products
            if dp.anomaly_id is not None and dp.anomaly_id in active_anomaly_ids
        ]
        anom_total = len(anomaly_products)
        anom_delivered = sum(1 for dp in anomaly_products if dp.product_id in delivered_ids)
        anom_rate: float | None = anom_delivered / anom_total if anom_total > 0 else None

        # ── Per-anomaly coverage detail ───────────────────────────────────────
        # Group plan products by anomaly_id (only active anomalies count).
        anomaly_product_map: dict[str, list[DataProduct]] = {}
        for dp in plan_products:
            aid = dp.anomaly_id
            if aid and aid in active_anomaly_ids:
                anomaly_product_map.setdefault(aid, []).append(dp)

        coverage_details: list[AnomalyCoverageDetail] = []
        for ae in anomalies:
            linked = anomaly_product_map.get(ae.anomaly_id, [])
            linked_total = len(linked)
            linked_delivered = sum(1 for dp in linked if dp.product_id in delivered_ids)
            cov_rate: float | None = (
                linked_delivered / linked_total if linked_total > 0 else None
            )
            coverage_details.append(AnomalyCoverageDetail(
                anomaly_id=ae.anomaly_id,
                severity=ae.severity,
                total_linked_products=linked_total,
                delivered_linked_products=linked_delivered,
                coverage_rate=cov_rate,
            ))

        # ── High-severity anomaly coverage ────────────────────────────────────
        # "Covered" = at least one linked product was delivered.
        high_sev_anomalies = [
            ae for ae in anomalies
            if ae.severity >= self._high_severity_threshold
        ]
        hs_total = len(high_sev_anomalies)
        hs_covered = 0
        for ae in high_sev_anomalies:
            linked = anomaly_product_map.get(ae.anomaly_id, [])
            if any(dp.product_id in delivered_ids for dp in linked):
                hs_covered += 1
        hs_rate: float | None = hs_covered / hs_total if hs_total > 0 else None

        # ── Anomaly-weighted coverage ─────────────────────────────────────────
        # Formula: Σ(severity_i × coverage_i) / Σ(severity_i)
        # Only anomalies with ≥1 linked product participate.
        weighted_cov: float | None = None
        participating: list[tuple[float, float]] = []  # (severity, coverage_i)
        for detail in coverage_details:
            if detail.total_linked_products > 0 and detail.coverage_rate is not None:
                participating.append((detail.severity, detail.coverage_rate))

        if participating:
            weight_sum = sum(s for s, _ in participating)
            if weight_sum > 0.0:
                weighted_cov = sum(s * c for s, c in participating) / weight_sum
            else:
                weighted_cov = None

        # ── Data age metrics ──────────────────────────────────────────────────
        delivered_ages: list[float] = []
        for dp in delivered_products_list:
            delivered_ages.append(dp.age_s)

        avg_age: float | None = sum(delivered_ages) / len(delivered_ages) if delivered_ages else None
        med_age: float | None = statistics.median(delivered_ages) if delivered_ages else None

        # ── Subsystem delivery breakdown ──────────────────────────────────────
        subsystem_counts: dict[str, int] = {}
        for dp in delivered_products_list:
            subsystem_counts[dp.subsystem] = subsystem_counts.get(dp.subsystem, 0) + 1

        return MissionOutcomeResult(
            plan_id=plan.plan_id,
            # Product delivery
            total_products=total_products,
            delivered_products=delivered_count,
            delivery_rate=delivery_rate,
            # Scientific value
            total_scientific_value=total_sci_val,
            delivered_scientific_value=delivered_sci_val,
            scientific_value_capture_rate=sci_capture_rate,
            # Required products
            required_products_total=req_total,
            required_products_delivered=req_delivered,
            required_delivery_rate=req_rate,
            # Active-anomaly products
            active_anomaly_products_total=anom_total,
            active_anomaly_products_delivered=anom_delivered,
            active_anomaly_delivery_rate=anom_rate,
            # High-severity anomaly coverage
            high_severity_threshold=self._high_severity_threshold,
            high_severity_anomalies_total=hs_total,
            high_severity_anomalies_covered=hs_covered,
            high_severity_anomaly_coverage_rate=hs_rate,
            # Anomaly-weighted coverage
            anomaly_weighted_coverage=weighted_cov,
            # Data age
            average_delivered_age_s=avg_age,
            median_delivered_age_s=med_age,
            # Subsystem breakdown
            delivered_by_subsystem=subsystem_counts,
            # Per-anomaly detail
            anomaly_coverage_by_id=coverage_details,
        )
