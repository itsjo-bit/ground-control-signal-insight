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
        product_id ∈ authoritative DataProduct inventory
        AND product_id ∈ CandidatePlan.packets
        AND product_id ∉ EvaluationResult.deferred_packets

Authoritative denominator policy
---------------------------------
All rates are computed against the **full authoritative DataProduct
inventory**, not merely the products present in the candidate plan.

This is the ground-truth principle:

    "A plan cannot improve its score simply by omitting products."

Concretely:

* ``total_products``          = len(authoritative data_products)
* ``total_scientific_value``  = Σ scientific_value across ALL authoritative products
* ``required_products_total`` = all authoritative products where delivery_requirement == "required"
* ``active_anomaly_products_total`` = all authoritative products whose anomaly_id
                                       references an applicable active anomaly
* per-anomaly ``total_linked_products`` = all authoritative products linked to that anomaly

An omitted authoritative product therefore counts as not delivered,
and this is reflected in every rate.

Applicable anomaly semantics
-----------------------------
An anomaly is **applicable** (counted for coverage purposes) if its
``status`` is ``"active"`` or ``"monitoring"``.  Anomalies with
``status == "resolved"`` are excluded from anomaly coverage metrics.

Use :func:`is_applicable_anomaly` everywhere status filtering is needed.
Do not duplicate the status rule across evaluator, prioritizer, or benchmark.

Zero-denominator policy
-----------------------
When a metric's denominator is zero (e.g. no required products in the
scenario) the *rate* field is ``None`` rather than a fictitious 1.0.
Raw counts remain 0.  This prevents false "perfect performance" signals.

High-severity anomaly coverage
---------------------------------
The denominator includes only high-severity anomalies that have at least
one authoritative linked product.  Anomalies with severity >= threshold
but zero authoritative linked products are excluded from the denominator
(not from the anomaly list itself).  This prevents "no evidence exists"
from being confused with "evidence was not delivered".

Anomaly-weighted coverage formula
----------------------------------
For each applicable anomaly *i* that has at least one linked product::

    coverage_i = delivered_linked_i / total_linked_i  (authoritative denominator)

    anomaly_weighted_coverage =
        Σ(severity_i × coverage_i) / Σ(severity_i)

Only anomalies with ≥1 authoritative linked product participate in the
weighted sum.

Strict validation
-----------------
``MissionOutcomeEvaluationError`` is raised for:

* ``plan.plan_id != evaluation_result.plan_id`` — mismatched pair
* duplicate ``product_id`` in authoritative ``DataProduct[]``
* ``CandidatePlan`` references a ``packet_id`` not in authoritative inventory
* ``EvaluationResult.deferred_packets`` contains IDs not in the plan

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
# Applicable anomaly policy — re-exported from shared domain helper
# ---------------------------------------------------------------------------
# The canonical definitions live in backend.app.domain.anomaly_policy.
# They are re-exported here so existing tests and imports that reference
# is_applicable_anomaly / APPLICABLE_ANOMALY_STATUSES from this module
# continue to work without modification.

from ..domain.anomaly_policy import (  # noqa: E402  (import after module-level constants)
    APPLICABLE_ANOMALY_STATUSES,
    is_applicable_anomaly,
)

#: Fallback key used when a DataProduct.subsystem is empty/whitespace-only.
UNKNOWN_SUBSYSTEM_KEY: str = "__unknown__"

__all__ = [
    "DEFAULT_HIGH_SEVERITY_THRESHOLD",
    "APPLICABLE_ANOMALY_STATUSES",
    "is_applicable_anomaly",
    "UNKNOWN_SUBSYSTEM_KEY",
    "MissionOutcomeEvaluationError",
    "AnomalyCoverageDetail",
    "MissionOutcomeResult",
    "MissionOutcomeEvaluator",
]


# ---------------------------------------------------------------------------
# Typed exception
# ---------------------------------------------------------------------------


class MissionOutcomeEvaluationError(Exception):
    """Raised when the evaluator detects a fatal input inconsistency.

    Callers must fix the root cause rather than catching and ignoring
    this exception — it always indicates corrupted or mismatched inputs.

    Conditions that trigger this error:

    * ``plan.plan_id != evaluation_result.plan_id``
      The plan and its evaluation result do not correspond to the same object.

    * Duplicate ``product_id`` in the authoritative ``DataProduct[]`` list.
      Dictionary construction would silently overwrite products, producing
      incorrect denominators.

    * A ``CandidatePlan`` packet references a ``product_id`` that does not
      exist in the authoritative ``DataProduct[]`` inventory.

    * ``EvaluationResult.deferred_packets`` contains an ID that is not
      present in the plan's packet list.  This indicates a mismatch
      between the physical evaluator result and the plan.
    """


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
        Number of authoritative data products whose ``anomaly_id`` matches
        this anomaly (denominator uses full inventory, not just plan products).
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

    Denominator policy
    ------------------
    All denominators use the **full authoritative DataProduct inventory**,
    not merely the products present in the candidate plan.  Omitting a
    product therefore counts as non-delivery rather than removing it from
    the denominator.

    Fields
    ------
    plan_id
        Matches the ``CandidatePlan.plan_id`` that was evaluated.

    Product delivery
        total_products, delivered_products, delivery_rate

        ``total_products`` = len(authoritative DataProduct[]).

    Scientific value capture
        total_scientific_value, delivered_scientific_value, scientific_value_capture_rate

        ``total_scientific_value`` = Σ scientific_value across ALL authoritative products.
        ``scientific_value_capture_rate`` is null when total_scientific_value == 0.

    Required-product delivery
        required_products_total, required_products_delivered, required_delivery_rate

        ``required_products_total`` = all authoritative products where
        delivery_requirement == "required".
        ``required_delivery_rate`` is null when required_products_total == 0.

    Active-anomaly product delivery
        active_anomaly_products_total, active_anomaly_products_delivered,
        active_anomaly_delivery_rate

        ``active_anomaly_products_total`` = all authoritative products whose
        anomaly_id references an applicable active anomaly.
        ``active_anomaly_delivery_rate`` is null when no active-anomaly products exist.

    High-severity anomaly coverage
        high_severity_anomalies_total, high_severity_anomalies_covered,
        high_severity_anomaly_coverage_rate, high_severity_threshold

        Denominator includes only high-severity anomalies with ≥1 authoritative
        linked product.  See module docstring for rationale.
        ``high_severity_anomaly_coverage_rate`` is null when denominator is zero.

    Anomaly-weighted coverage
        anomaly_weighted_coverage — Σ(severity_i × coverage_i) / Σ(severity_i).
        Coverage denominator uses authoritative total linked products.
        Null when no applicable anomaly has linked products.

    Data age
        average_delivered_age_s, median_delivered_age_s
        Both null when no products were delivered.

    Subsystem breakdown
        delivered_by_subsystem — dict mapping normalised subsystem name → delivered count.
        Products with empty/whitespace subsystem are keyed under ``"__unknown__"``.

    Subsystem coverage (authoritative denominator)
        total_subsystems — distinct non-empty subsystem names in full inventory.
        delivered_subsystems — distinct subsystems with ≥1 projected delivered product.
        subsystem_coverage_rate — delivered_subsystems / total_subsystems (None when 0).

        These are DESCRIPTIVE metrics only:
        * A single-subsystem plan is not automatically invalid.
        * A higher coverage rate is not automatically better.
        * The denominator uses the full authoritative inventory, not the plan alone.

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
            "Σ(severity_i × coverage_i) / Σ(severity_i) for applicable anomalies with linked "
            "products. Coverage denominator uses authoritative inventory. "
            "Null when no applicable anomaly has linked products."
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
        description=(
            "Number of projected non-deferred products per subsystem. "
            "Keys are normalised subsystem names (stripped, lower-cased). "
            "Products with empty subsystem are grouped under '__unknown__'. "
            "Semantics: sum(values()) == delivered_products when every authoritative "
            "product has a non-empty subsystem."
        ),
    )

    # ── Subsystem coverage ────────────────────────────────────────────────────
    total_subsystems: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of distinct non-empty normalised subsystem names across the "
            "FULL authoritative DataProduct inventory. This is the authoritative "
            "denominator — a plan cannot improve its coverage by omitting products."
        ),
    )
    delivered_subsystems: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of distinct subsystem names with at least one projected "
            "non-deferred product. Always <= total_subsystems."
        ),
    )
    subsystem_coverage_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "delivered_subsystems / total_subsystems. "
            "None when total_subsystems == 0 (no non-empty subsystem data in inventory). "
            "DESCRIPTIVE ONLY — higher is not automatically better."
        ),
    )

    # ── Per-anomaly detail ────────────────────────────────────────────────────
    anomaly_coverage_by_id: list[AnomalyCoverageDetail] = Field(
        default_factory=list,
        description="Per-anomaly coverage breakdown for all applicable anomalies",
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
    * ``DataProduct[]`` — authoritative product metadata (full inventory)
    * ``AnomalyEvent[]`` — authoritative anomaly metadata

    All rate denominators use the **full authoritative DataProduct inventory**
    rather than only the products present in the plan.  A plan cannot improve
    its score by omitting difficult products.

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
            data_products:     Authoritative list of ALL mission data products.
                               Denominators are computed from this full inventory.
            anomalies:         Authoritative list of anomaly events.

        Returns:
            :class:`MissionOutcomeResult` with multi-dimensional semantic metrics.

        Raises:
            MissionOutcomeEvaluationError:
                * ``plan.plan_id != evaluation_result.plan_id``
                * Duplicate ``product_id`` in authoritative ``data_products``
                * A plan packet references an unknown authoritative product
                * A deferred packet ID is not present in the plan

        Notes:
            Applicable anomaly statuses: ``"active"`` and ``"monitoring"``.
            Anomalies with ``status == "resolved"`` are excluded from
            coverage metrics (see :func:`is_applicable_anomaly`).
        """
        # ── Strict validation ─────────────────────────────────────────────────

        # 1. plan / evaluation_result must match
        if plan.plan_id != evaluation_result.plan_id:
            raise MissionOutcomeEvaluationError(
                f"plan.plan_id '{plan.plan_id}' does not match "
                f"evaluation_result.plan_id '{evaluation_result.plan_id}'. "
                "Computing metrics from mismatched objects produces meaningless results."
            )

        # 2. Reject duplicate authoritative product IDs
        seen_product_ids: set[str] = set()
        duplicates: list[str] = []
        for dp in data_products:
            if dp.product_id in seen_product_ids:
                duplicates.append(dp.product_id)
            seen_product_ids.add(dp.product_id)
        if duplicates:
            raise MissionOutcomeEvaluationError(
                f"Authoritative data_products contains duplicate product_ids: {sorted(set(duplicates))}. "
                "Dictionary construction would silently overwrite products and corrupt denominators."
            )

        # 2b. Reject duplicate authoritative anomaly IDs
        seen_anomaly_ids: set[str] = set()
        duplicate_anomaly_ids: list[str] = []
        for ae in anomalies:
            if ae.anomaly_id in seen_anomaly_ids:
                duplicate_anomaly_ids.append(ae.anomaly_id)
            seen_anomaly_ids.add(ae.anomaly_id)
        if duplicate_anomaly_ids:
            raise MissionOutcomeEvaluationError(
                f"Authoritative anomalies contains duplicate anomaly_ids: "
                f"{sorted(set(duplicate_anomaly_ids))}. "
                "Duplicate anomaly IDs would silently overwrite severity/status values "
                "and corrupt coverage metrics."
            )

        # ── Build authoritative lookup ────────────────────────────────────────
        # product_id → DataProduct (authoritative metadata for all scenario products)
        product_map: dict[str, DataProduct] = {dp.product_id: dp for dp in data_products}

        # 3. Reject duplicate packet IDs inside the CandidatePlan
        plan_packet_ids_raw: list[str] = [pkt.packet_id for pkt in plan.packets]
        seen_plan_pkt_ids: set[str] = set()
        duplicate_plan_pkt_ids: list[str] = []
        for pid in plan_packet_ids_raw:
            if pid in seen_plan_pkt_ids:
                duplicate_plan_pkt_ids.append(pid)
            seen_plan_pkt_ids.add(pid)
        if duplicate_plan_pkt_ids:
            raise MissionOutcomeEvaluationError(
                f"CandidatePlan '{plan.plan_id}' contains duplicate packet_ids: "
                f"{sorted(set(duplicate_plan_pkt_ids))}. "
                "Duplicate packet IDs would produce inconsistent physical and semantic "
                "evaluation results."
            )

        # 4. Reject plan packets that reference unknown authoritative products
        plan_packet_ids: list[str] = plan_packet_ids_raw
        unknown_plan_ids: list[str] = [
            pid for pid in plan_packet_ids if pid not in product_map
        ]
        if unknown_plan_ids:
            raise MissionOutcomeEvaluationError(
                f"CandidatePlan '{plan.plan_id}' references packet_id(s) not in the "
                f"authoritative DataProduct inventory: {sorted(unknown_plan_ids)}. "
                "A plan being evaluated must reference authoritative mission data only."
            )

        # 5. Reject deferred IDs that are not in the plan
        plan_packet_id_set: set[str] = set(plan_packet_ids)
        unknown_deferred: list[str] = [
            pid for pid in evaluation_result.deferred_packets
            if pid not in plan_packet_id_set
        ]
        if unknown_deferred:
            raise MissionOutcomeEvaluationError(
                f"EvaluationResult for '{evaluation_result.plan_id}' contains deferred_packet IDs "
                f"not present in the plan's packet list: {sorted(unknown_deferred)}. "
                "This indicates a mismatch between the physical evaluator result and the plan."
            )

        # ── Set up working sets ───────────────────────────────────────────────

        # Set of deferred product IDs (from PlanEvaluator — physical ground truth)
        deferred_set: set[str] = set(evaluation_result.deferred_packets)

        # Applicable anomalies (active + monitoring, not resolved)
        applicable_anomalies = [ae for ae in anomalies if is_applicable_anomaly(ae)]
        applicable_anomaly_ids: set[str] = {ae.anomaly_id for ae in applicable_anomalies}
        anomaly_severity: dict[str, float] = {ae.anomaly_id: ae.severity for ae in applicable_anomalies}

        # Products delivered by this plan (in plan AND not deferred)
        delivered_ids: set[str] = {
            pid for pid in plan_packet_ids
            if pid not in deferred_set
        }

        # ── AUTHORITATIVE denominators: use FULL inventory ────────────────────
        #
        # "A plan cannot improve its score by omitting products."
        #
        # total_products = len(ALL authoritative products)
        all_auth_products: list[DataProduct] = list(data_products)
        total_products = len(all_auth_products)

        # ── Product delivery (authoritative denominator) ──────────────────────
        # Delivered = in plan AND not deferred AND in authoritative inventory
        # Omitted authoritative product = not delivered
        delivered_count = sum(
            1 for dp in all_auth_products
            if dp.product_id in delivered_ids
        )
        delivery_rate: float | None = (
            delivered_count / total_products if total_products > 0 else None
        )

        # ── Scientific value capture (authoritative denominator) ──────────────
        # total = Σ scientific_value across ALL authoritative products
        total_sci_val = sum(dp.scientific_value for dp in all_auth_products)
        delivered_sci_val = sum(
            dp.scientific_value for dp in all_auth_products
            if dp.product_id in delivered_ids
        )
        sci_capture_rate: float | None = (
            delivered_sci_val / total_sci_val if total_sci_val > 0.0 else None
        )

        # ── Required-product delivery (authoritative denominator) ─────────────
        # required_total = all authoritative products where delivery_requirement == "required"
        auth_required = [dp for dp in all_auth_products if dp.delivery_requirement == "required"]
        req_total = len(auth_required)
        req_delivered = sum(1 for dp in auth_required if dp.product_id in delivered_ids)
        req_rate: float | None = req_delivered / req_total if req_total > 0 else None

        # ── Active-anomaly product delivery (authoritative denominator) ───────
        # total = all authoritative products whose anomaly_id references an applicable anomaly
        auth_anomaly_products = [
            dp for dp in all_auth_products
            if dp.anomaly_id is not None and dp.anomaly_id in applicable_anomaly_ids
        ]
        anom_total = len(auth_anomaly_products)
        anom_delivered = sum(
            1 for dp in auth_anomaly_products if dp.product_id in delivered_ids
        )
        anom_rate: float | None = anom_delivered / anom_total if anom_total > 0 else None

        # ── Per-anomaly coverage detail (authoritative denominator) ───────────
        # For each applicable anomaly: total = all authoritative products linked to it
        auth_anomaly_product_map: dict[str, list[DataProduct]] = {}
        for dp in all_auth_products:
            aid = dp.anomaly_id
            if aid and aid in applicable_anomaly_ids:
                auth_anomaly_product_map.setdefault(aid, []).append(dp)

        coverage_details: list[AnomalyCoverageDetail] = []
        for ae in applicable_anomalies:
            linked = auth_anomaly_product_map.get(ae.anomaly_id, [])
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

        # ── High-severity anomaly coverage (authoritative denominator) ────────
        # "Covered" = at least one linked authoritative product was delivered.
        # Denominator: high-severity applicable anomalies WITH ≥1 authoritative linked product.
        # This prevents "no evidence exists" from polluting the denominator.
        high_sev_anomalies_with_products = [
            ae for ae in applicable_anomalies
            if (
                ae.severity >= self._high_severity_threshold
                and len(auth_anomaly_product_map.get(ae.anomaly_id, [])) > 0
            )
        ]
        hs_total = len(high_sev_anomalies_with_products)
        hs_covered = 0
        for ae in high_sev_anomalies_with_products:
            linked = auth_anomaly_product_map.get(ae.anomaly_id, [])
            if any(dp.product_id in delivered_ids for dp in linked):
                hs_covered += 1
        hs_rate: float | None = hs_covered / hs_total if hs_total > 0 else None

        # ── Anomaly-weighted coverage (authoritative denominator) ─────────────
        # Formula: Σ(severity_i × coverage_i) / Σ(severity_i)
        # Only applicable anomalies with ≥1 authoritative linked product participate.
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

        # ── Data age metrics (delivered products only) ────────────────────────
        delivered_ages: list[float] = [
            dp.age_s for dp in all_auth_products
            if dp.product_id in delivered_ids
        ]
        avg_age: float | None = (
            sum(delivered_ages) / len(delivered_ages) if delivered_ages else None
        )
        med_age: float | None = statistics.median(delivered_ages) if delivered_ages else None

        # ── Subsystem delivery breakdown & coverage ───────────────────────────
        #
        # Normalise subsystem names: strip whitespace, lower-case.
        # Empty/whitespace-only subsystem values are grouped under UNKNOWN_SUBSYSTEM_KEY.
        #
        # Authoritative denominator policy (mirrors existing rate denominators):
        #   total_subsystems = distinct NON-EMPTY normalised names across ALL auth products
        #   delivered_subsystems = distinct names with >=1 projected non-deferred product
        #   subsystem_coverage_rate = delivered_subsystems / total_subsystems
        #                              (None when total_subsystems == 0)
        #
        # The denominator excludes __unknown__ so that products with missing subsystem
        # metadata do not inflate total_subsystems.

        def _norm_subsystem(s: str) -> str:
            """Return normalised subsystem key; empty/whitespace → UNKNOWN_SUBSYSTEM_KEY."""
            stripped = s.strip().lower()
            return stripped if stripped else UNKNOWN_SUBSYSTEM_KEY

        # Build delivered_by_subsystem (includes __unknown__ if applicable)
        subsystem_counts: dict[str, int] = {}
        for dp in all_auth_products:
            if dp.product_id in delivered_ids:
                key = _norm_subsystem(dp.subsystem)
                subsystem_counts[key] = subsystem_counts.get(key, 0) + 1

        # Compute authoritative total_subsystems from FULL inventory (non-empty only)
        auth_subsystem_names: set[str] = set()
        for dp in all_auth_products:
            norm = _norm_subsystem(dp.subsystem)
            if norm != UNKNOWN_SUBSYSTEM_KEY:
                auth_subsystem_names.add(norm)
        total_subs = len(auth_subsystem_names)

        # delivered_subsystems: distinct NON-EMPTY subsystems with >=1 delivered product
        delivered_subs_set: set[str] = {
            k for k in subsystem_counts
            if k != UNKNOWN_SUBSYSTEM_KEY and subsystem_counts[k] > 0
        }
        delivered_subs = len(delivered_subs_set)

        # subsystem_coverage_rate: None when total_subs == 0 (no non-empty subsystem data)
        sub_coverage_rate: float | None = (
            delivered_subs / total_subs if total_subs > 0 else None
        )

        return MissionOutcomeResult(
            plan_id=plan.plan_id,
            # Product delivery (authoritative denominator)
            total_products=total_products,
            delivered_products=delivered_count,
            delivery_rate=delivery_rate,
            # Scientific value (authoritative denominator)
            total_scientific_value=total_sci_val,
            delivered_scientific_value=delivered_sci_val,
            scientific_value_capture_rate=sci_capture_rate,
            # Required products (authoritative denominator)
            required_products_total=req_total,
            required_products_delivered=req_delivered,
            required_delivery_rate=req_rate,
            # Active-anomaly products (authoritative denominator)
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
            # Subsystem breakdown + coverage
            delivered_by_subsystem=subsystem_counts,
            total_subsystems=total_subs,
            delivered_subsystems=delivered_subs,
            subsystem_coverage_rate=sub_coverage_rate,
            # Per-anomaly detail
            anomaly_coverage_by_id=coverage_details,
        )
