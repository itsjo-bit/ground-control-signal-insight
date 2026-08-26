"""Unit tests for MissionOutcomeEvaluator.

Tests cover:
- Scientific value capture
- Required product delivery
- Active anomaly product delivery
- Per-anomaly coverage
- High-severity anomaly coverage
- Anomaly-weighted coverage (exact formula verification)
- Zero-denominator → null rate (not fake 1.0)
- Data age metrics
- Subsystem breakdown
- Determinism (same input → same output)
- AI-provenance agnosticism (same metrics regardless of plan strategy)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from backend.app.evaluator.mission_outcome_evaluator import (
    MissionOutcomeEvaluator,
    MissionOutcomeResult,
    DEFAULT_HIGH_SEVERITY_THRESHOLD,
)
from backend.app.evaluator.plan_evaluator import PlanEvaluator
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.data_product import DataProduct
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _link(*, ber: float = 0.0, goodput: float = 1_000_000.0, window: float = 3600.0) -> LinkState:
    return LinkState(
        timestamp=_TS,
        snr_db=20.0,
        eb_n0_db=20.0,
        ber=ber,
        rssi_dbm=-70.0,
        nominal_data_rate_bps=goodput,
        link_goodput_bps=goodput,
        latency_s=0.0,
        link_stability=1.0,
        remaining_window_s=window,
    )


def _mission(*, window: float = 3600.0) -> MissionState:
    return MissionState(
        mission_id="test",
        mission_phase="science",
        current_event="downlink",
        event_time_remaining_s=window,
        comm_window_remaining_s=window,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


def _dp(
    pid: str,
    *,
    subsystem: str = "payload",
    scientific_value: float = 0.5,
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    delivery_requirement: str = "best_effort",
    anomaly_id: str | None = None,
    age_s: float = 100.0,
    size_bits: int = 8_000,
) -> DataProduct:
    return DataProduct(
        product_id=pid,
        product_type="telemetry",
        subsystem=subsystem,
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=mission_relevance,
        scientific_value=scientific_value,
        deadline_s=3000.0,
        age_s=age_s,
        delivery_requirement=delivery_requirement,
        retry_cost=0.1,
        anomaly_id=anomaly_id,
    )


def _pkt(pid: str, *, size_bits: int = 8_000) -> Packet:
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=size_bits,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=3000.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


def _plan(plan_id: str, pids: list[str], strategy: str = "test") -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy=strategy,
        packets=[_pkt(pid) for pid in pids],
        generated_by="test",
        metadata={},
    )


def _eval_result(plan_id: str, deferred: list[str] | None = None) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=1.0,
        critical_packets_delivered=0,
        total_critical_packets=0,
        deadline_misses=0,
        avg_packet_delay_s=0.0,
        bandwidth_utilization=0.5,
        retransmission_overhead=0.0,
        risk_score=0.2,
        risk_level=RiskLevel.LOW,
        deferred_packets=deferred or [],
    )


def _anomaly(aid: str, severity: float, status: str = "active") -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=aid,
        subsystem="propulsion",
        severity=severity,
        detected_at_s=0.0,
        description=f"Test anomaly {aid}",
        status=status,
    )


# Shorthand evaluator
_ev = MissionOutcomeEvaluator()


# ---------------------------------------------------------------------------
# Section 1: Scientific value capture
# ---------------------------------------------------------------------------

class TestScientificValueCapture:
    def test_higher_sci_value_products_yield_higher_capture(self):
        """Plan delivering higher scientific-value products → higher captured value."""
        dp_high = _dp("A", scientific_value=0.9)
        dp_low = _dp("B", scientific_value=0.1)

        # Plan A: high sci-value product delivered (B deferred)
        plan_a = _plan("plan-a", ["A", "B"])
        eval_a = _eval_result("plan-a", deferred=["B"])

        # Plan B: low sci-value product delivered (A deferred)
        plan_b = _plan("plan-b", ["A", "B"])
        eval_b = _eval_result("plan-b", deferred=["A"])

        result_a = _ev.evaluate(plan_a, eval_a, [dp_high, dp_low], [])
        result_b = _ev.evaluate(plan_b, eval_b, [dp_high, dp_low], [])

        assert result_a.delivered_scientific_value > result_b.delivered_scientific_value
        assert result_a.scientific_value_capture_rate > result_b.scientific_value_capture_rate  # type: ignore[operator]

    def test_all_products_delivered_gives_rate_1(self):
        dp = _dp("X", scientific_value=0.8)
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.scientific_value_capture_rate == pytest.approx(1.0)

    def test_no_products_delivered_gives_rate_0(self):
        dp = _dp("X", scientific_value=0.8)
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=["X"])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.scientific_value_capture_rate == pytest.approx(0.0)

    def test_zero_total_sci_value_gives_null_rate(self):
        """When total scientific value is 0, rate must be null, not 1.0."""
        dp = _dp("Z", scientific_value=0.0)
        plan = _plan("p", ["Z"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.scientific_value_capture_rate is None
        assert result.total_scientific_value == pytest.approx(0.0)

    def test_partial_delivery_computes_correct_rate(self):
        products = [_dp(f"P{i}", scientific_value=0.5) for i in range(4)]
        plan = _plan("p", [dp.product_id for dp in products])
        # Defer P2 and P3
        er = _eval_result("p", deferred=["P2", "P3"])
        result = _ev.evaluate(plan, er, products, [])
        # 2 of 4 delivered, each with scientific_value 0.5
        # total = 2.0, delivered = 1.0, rate = 0.5
        assert result.total_scientific_value == pytest.approx(2.0)
        assert result.delivered_scientific_value == pytest.approx(1.0)
        assert result.scientific_value_capture_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Section 2: Required product delivery
# ---------------------------------------------------------------------------

class TestRequiredProductDelivery:
    def test_required_product_delivered_increases_rate(self):
        dp_req = _dp("R", delivery_requirement="required")
        dp_be = _dp("B", delivery_requirement="best_effort")

        plan = _plan("p", ["R", "B"])
        er_delivered = _eval_result("p", deferred=[])
        er_deferred = _eval_result("p", deferred=["R"])

        result_del = _ev.evaluate(plan, er_delivered, [dp_req, dp_be], [])
        result_def = _ev.evaluate(plan, er_deferred, [dp_req, dp_be], [])

        assert result_del.required_delivery_rate == pytest.approx(1.0)
        assert result_def.required_delivery_rate == pytest.approx(0.0)

    def test_no_required_products_gives_null_rate(self):
        """Zero required products → required_delivery_rate must be null."""
        dp = _dp("X", delivery_requirement="best_effort")
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.required_products_total == 0
        assert result.required_products_delivered == 0
        assert result.required_delivery_rate is None

    def test_partial_required_delivery(self):
        products = [_dp(f"R{i}", delivery_requirement="required") for i in range(3)]
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=["R2"])
        result = _ev.evaluate(plan, er, products, [])
        assert result.required_products_total == 3
        assert result.required_products_delivered == 2
        assert result.required_delivery_rate == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Section 3: Active anomaly product delivery
# ---------------------------------------------------------------------------

class TestActiveAnomalyDelivery:
    def test_anomaly_linked_products_counted(self):
        """Products linked to active anomalies have correct coverage counts."""
        anomaly = _anomaly("ANOM-017", severity=0.9)
        dp_a = _dp("ANOM-A", anomaly_id="ANOM-017")
        dp_b = _dp("ANOM-B", anomaly_id="ANOM-017")
        dp_c = _dp("NORMAL-C")  # not anomaly-linked

        plan = _plan("p", ["ANOM-A", "ANOM-B", "NORMAL-C"])
        er = _eval_result("p", deferred=["ANOM-B"])
        result = _ev.evaluate(plan, er, [dp_a, dp_b, dp_c], [anomaly])

        assert result.active_anomaly_products_total == 2
        assert result.active_anomaly_products_delivered == 1
        assert result.active_anomaly_delivery_rate == pytest.approx(0.5)

    def test_no_active_anomaly_products_gives_null_rate(self):
        """No active anomaly products → active_anomaly_delivery_rate must be null."""
        dp = _dp("X")  # no anomaly_id
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.active_anomaly_products_total == 0
        assert result.active_anomaly_delivery_rate is None

    def test_inactive_anomaly_not_counted(self):
        """Products linked to anomaly IDs not in the active list are not counted."""
        anomaly = _anomaly("ANOM-ACTIVE", severity=0.8)
        dp_active = _dp("A", anomaly_id="ANOM-ACTIVE")
        dp_other = _dp("B", anomaly_id="ANOM-NOT-ACTIVE")  # not in anomaly list
        plan = _plan("p", ["A", "B"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp_active, dp_other], [anomaly])
        # Only ANOM-ACTIVE is in the scenario anomalies
        assert result.active_anomaly_products_total == 1
        assert result.active_anomaly_products_delivered == 1


# ---------------------------------------------------------------------------
# Section 4: Per-anomaly coverage
# ---------------------------------------------------------------------------

class TestPerAnomalyCoverage:
    def test_per_anomaly_coverage_computed(self):
        """Per-anomaly coverage detail is correct."""
        anomaly = _anomaly("ANOM-017", severity=0.91)
        products = [_dp(f"P{i}", anomaly_id="ANOM-017") for i in range(8)]

        plan = _plan("p", [dp.product_id for dp in products])
        # Deliver 7 of 8
        er = _eval_result("p", deferred=["P7"])
        result = _ev.evaluate(plan, er, products, [anomaly])

        # Find the ANOM-017 detail
        detail = next(d for d in result.anomaly_coverage_by_id if d.anomaly_id == "ANOM-017")
        assert detail.total_linked_products == 8
        assert detail.delivered_linked_products == 7
        assert detail.coverage_rate == pytest.approx(7 / 8)
        assert detail.severity == pytest.approx(0.91)

    def test_anomaly_with_no_linked_products_in_plan(self):
        """Anomaly with no products in the plan → null coverage_rate, counts of 0."""
        anomaly = _anomaly("ANOM-X", severity=0.5)
        dp = _dp("P1")  # no anomaly_id
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [anomaly])
        assert len(result.anomaly_coverage_by_id) == 1
        detail = result.anomaly_coverage_by_id[0]
        assert detail.anomaly_id == "ANOM-X"
        assert detail.total_linked_products == 0
        assert detail.delivered_linked_products == 0
        assert detail.coverage_rate is None


# ---------------------------------------------------------------------------
# Section 5: High-severity anomaly coverage
# ---------------------------------------------------------------------------

class TestHighSeverityAnomalyCoverage:
    def test_two_anomalies_one_covered(self):
        """One of two high-severity anomalies covered → rate = 0.5."""
        anom1 = _anomaly("ANOM-017", severity=0.91)
        anom2 = _anomaly("ANOM-021", severity=0.80)
        dp1 = _dp("P1", anomaly_id="ANOM-017")
        dp2 = _dp("P2", anomaly_id="ANOM-021")

        plan = _plan("p", ["P1", "P2"])
        # Only P1 delivered; P2 deferred → ANOM-021 not covered
        er = _eval_result("p", deferred=["P2"])
        result = _ev.evaluate(plan, er, [dp1, dp2], [anom1, anom2])

        assert result.high_severity_anomalies_total == 2
        assert result.high_severity_anomalies_covered == 1
        assert result.high_severity_anomaly_coverage_rate == pytest.approx(0.5)

    def test_no_high_severity_anomalies_gives_null_rate(self):
        """No high-severity anomalies → rate must be null."""
        anom = _anomaly("ANOM-LOW", severity=0.3)  # below threshold
        dp = _dp("P1", anomaly_id="ANOM-LOW")
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [anom])

        assert result.high_severity_anomalies_total == 0
        assert result.high_severity_anomaly_coverage_rate is None

    def test_custom_threshold(self):
        """Custom threshold applied correctly."""
        ev = MissionOutcomeEvaluator(high_severity_threshold=0.5)
        anom = _anomaly("ANOM-M", severity=0.6)  # above 0.5 threshold
        dp = _dp("P1", anomaly_id="ANOM-M")
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])
        result = ev.evaluate(plan, er, [dp], [anom])
        assert result.high_severity_anomalies_total == 1
        assert result.high_severity_threshold == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Section 6: Anomaly-weighted coverage
# ---------------------------------------------------------------------------

class TestAnomalyWeightedCoverage:
    def test_weighted_coverage_formula(self):
        """Verify exact formula: Σ(severity_i × coverage_i) / Σ(severity_i)."""
        # ANOM-A: severity=0.9, 2 products, 2 delivered → coverage=1.0
        # ANOM-B: severity=0.6, 4 products, 2 delivered → coverage=0.5
        anom_a = _anomaly("ANOM-A", severity=0.9)
        anom_b = _anomaly("ANOM-B", severity=0.6)
        products_a = [_dp(f"A{i}", anomaly_id="ANOM-A") for i in range(2)]
        products_b = [_dp(f"B{i}", anomaly_id="ANOM-B") for i in range(4)]

        all_products = products_a + products_b
        plan = _plan("p", [dp.product_id for dp in all_products])
        # Defer B2 and B3
        er = _eval_result("p", deferred=["B2", "B3"])
        result = _ev.evaluate(plan, er, all_products, [anom_a, anom_b])

        # Expected: (0.9*1.0 + 0.6*0.5) / (0.9 + 0.6) = (0.9 + 0.3) / 1.5 = 0.8
        expected = (0.9 * 1.0 + 0.6 * 0.5) / (0.9 + 0.6)
        assert result.anomaly_weighted_coverage == pytest.approx(expected, rel=1e-6)

    def test_no_anomaly_products_gives_null_weighted_coverage(self):
        """No anomaly-linked products → anomaly_weighted_coverage must be null."""
        anom = _anomaly("ANOM-X", severity=0.8)
        dp = _dp("P1")  # no anomaly link
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp], [anom])
        assert result.anomaly_weighted_coverage is None

    def test_single_anomaly_full_coverage(self):
        """Single anomaly, all delivered → weighted coverage = 1.0."""
        anom = _anomaly("ANOM-ONLY", severity=0.85)
        products = [_dp(f"X{i}", anomaly_id="ANOM-ONLY") for i in range(3)]
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, products, [anom])
        assert result.anomaly_weighted_coverage == pytest.approx(1.0)

    def test_weighted_coverage_not_influenced_by_zero_severity(self):
        """Zero-severity anomaly contributes to denominator test — excluded properly."""
        # If severity=0, it still participates (it has products) but with weight 0
        # The formula handles this: 0*coverage/0 — but we guard Σseverity>0
        anom_zero = _anomaly("ANOM-ZERO", severity=0.0)
        anom_high = _anomaly("ANOM-HIGH", severity=1.0)
        products_zero = [_dp("Z1", anomaly_id="ANOM-ZERO")]
        products_high = [_dp("H1", anomaly_id="ANOM-HIGH")]
        plan = _plan("p", ["Z1", "H1"])
        er = _eval_result("p", deferred=["Z1"])
        result = _ev.evaluate(plan, er, products_zero + products_high, [anom_zero, anom_high])
        # Σseverity for participating = 0 + 1 = 1
        # weighted = (0*0 + 1*1) / 1 = 1.0
        assert result.anomaly_weighted_coverage == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Section 7: Data age metrics
# ---------------------------------------------------------------------------

class TestDataAge:
    def test_average_delivered_age(self):
        """average_delivered_age_s computed correctly."""
        dp_a = _dp("A", age_s=100.0)
        dp_b = _dp("B", age_s=300.0)
        dp_c = _dp("C", age_s=200.0)

        plan = _plan("p", ["A", "B", "C"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp_a, dp_b, dp_c], [])

        assert result.average_delivered_age_s == pytest.approx((100.0 + 300.0 + 200.0) / 3)

    def test_median_delivered_age(self):
        """median_delivered_age_s computed correctly for odd count."""
        dp_a = _dp("A", age_s=100.0)
        dp_b = _dp("B", age_s=300.0)
        dp_c = _dp("C", age_s=200.0)

        plan = _plan("p", ["A", "B", "C"])
        er = _eval_result("p", deferred=[])
        result = _ev.evaluate(plan, er, [dp_a, dp_b, dp_c], [])

        assert result.median_delivered_age_s == pytest.approx(200.0)

    def test_no_delivered_products_age_is_null(self):
        """When nothing is delivered, both age metrics are null."""
        dp = _dp("X", age_s=50.0)
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=["X"])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.average_delivered_age_s is None
        assert result.median_delivered_age_s is None


# ---------------------------------------------------------------------------
# Section 8: Subsystem breakdown
# ---------------------------------------------------------------------------

class TestSubsystemBreakdown:
    def test_subsystem_counts_correct(self):
        """Delivered products counted correctly per subsystem."""
        products = [
            _dp("P1", subsystem="propulsion"),
            _dp("P2", subsystem="thermal"),
            _dp("P3", subsystem="propulsion"),
            _dp("P4", subsystem="power"),
        ]
        plan = _plan("p", ["P1", "P2", "P3", "P4"])
        er = _eval_result("p", deferred=["P4"])
        result = _ev.evaluate(plan, er, products, [])

        assert result.delivered_by_subsystem["propulsion"] == 2
        assert result.delivered_by_subsystem["thermal"] == 1
        assert "power" not in result.delivered_by_subsystem  # P4 deferred

    def test_empty_subsystem_dict_when_nothing_delivered(self):
        dp = _dp("X", subsystem="thermal")
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=["X"])
        result = _ev.evaluate(plan, er, [dp], [])
        assert result.delivered_by_subsystem == {}


# ---------------------------------------------------------------------------
# Section 9: Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_input_same_output(self):
        """Same inputs must produce bitwise-identical outputs."""
        anom = _anomaly("ANOM-DET", severity=0.85)
        products = [
            _dp("A", scientific_value=0.8, anomaly_id="ANOM-DET"),
            _dp("B", scientific_value=0.6, delivery_requirement="required"),
            _dp("C", scientific_value=0.3),
        ]
        plan = _plan("p", ["A", "B", "C"])
        er = _eval_result("p", deferred=["C"])

        result1 = _ev.evaluate(plan, er, products, [anom])
        result2 = _ev.evaluate(plan, er, products, [anom])

        assert result1.model_dump() == result2.model_dump()


# ---------------------------------------------------------------------------
# Section 10: Fairness — no AI provenance affects metrics
# ---------------------------------------------------------------------------

class TestFairnessNoBias:
    """Prove the evaluator is AI-provenance-agnostic."""

    def _products(self):
        return [
            _dp("P1", scientific_value=0.9, delivery_requirement="required"),
            _dp("P2", scientific_value=0.4),
            _dp("P3", scientific_value=0.7, delivery_requirement="required"),
        ]

    def test_same_packet_order_different_strategy_gives_identical_metrics(self):
        """Same packets, same deferred set — different plan strategy must give same metrics."""
        products = self._products()

        # "baseline-copy" plan
        plan_baseline = CandidatePlan(
            plan_id="baseline-copy",
            strategy="baseline",
            packets=[_pkt(dp.product_id) for dp in products],
            generated_by="test",
            metadata={},
        )

        # "ai-copy" plan — same packets, different strategy/plan_id
        plan_ai = CandidatePlan(
            plan_id="ai-copy",
            strategy="ai_prioritized",
            packets=[_pkt(dp.product_id) for dp in products],
            generated_by="build_ai_prioritized_plan",
            metadata={"plan_type": "ai_semantic"},
        )

        # Same deferred set
        er_baseline = _eval_result("baseline-copy", deferred=["P2"])
        er_ai = _eval_result("ai-copy", deferred=["P2"])

        result_b = _ev.evaluate(plan_baseline, er_baseline, products, [])
        result_a = _ev.evaluate(plan_ai, er_ai, products, [])

        # All metrics must be identical (except plan_id)
        assert result_b.delivered_products == result_a.delivered_products
        assert result_b.scientific_value_capture_rate == result_a.scientific_value_capture_rate
        assert result_b.required_delivery_rate == result_a.required_delivery_rate
        assert result_b.total_scientific_value == pytest.approx(result_a.total_scientific_value)
        assert result_b.delivered_scientific_value == pytest.approx(result_a.delivered_scientific_value)
        assert result_b.delivered_by_subsystem == result_a.delivered_by_subsystem

    def test_plan_id_differs_but_all_other_metrics_same(self):
        """plan_id uniquely differs; every other metric is the same."""
        products = self._products()
        plan_b = _plan("baseline-copy", [dp.product_id for dp in products], strategy="baseline")
        plan_a = _plan("ai-copy", [dp.product_id for dp in products], strategy="ai_prioritized")
        er_b = _eval_result("baseline-copy")
        er_a = _eval_result("ai-copy")

        r_b = _ev.evaluate(plan_b, er_b, products, [])
        r_a = _ev.evaluate(plan_a, er_a, products, [])

        dump_b = r_b.model_dump()
        dump_a = r_a.model_dump()

        # Remove plan_id before comparison
        del dump_b["plan_id"]
        del dump_a["plan_id"]
        assert dump_b == dump_a


# ---------------------------------------------------------------------------
# Section 11: Integration with PlanEvaluator output
# ---------------------------------------------------------------------------

class TestIntegrationWithPlanEvaluator:
    """Verify that MissionOutcomeEvaluator correctly uses PlanEvaluator's deferred_packets."""

    def test_deferred_packets_used_as_physical_ground_truth(self):
        """MissionOutcomeEvaluator uses PlanEvaluator's deferred_packets to determine delivery."""
        ls = _link(goodput=1_000.0, window=0.1)  # very tight window
        ms = _mission(window=0.1)

        products = [
            _dp("HIGH", scientific_value=0.9, size_bits=8_000),
            _dp("LOW", scientific_value=0.1, size_bits=8_000),
        ]
        pkts = [_pkt(dp.product_id, size_bits=dp.size_bits) for dp in products]

        plan = CandidatePlan(
            plan_id="tight-plan",
            strategy="test",
            packets=pkts,
            generated_by="test",
            metadata={},
        )

        # Use real PlanEvaluator
        pe = PlanEvaluator()
        plan_eval = pe.evaluate(plan, ls, ms)

        # Use MissionOutcomeEvaluator — it uses plan_eval.deferred_packets
        outcome_ev = MissionOutcomeEvaluator()
        outcome = outcome_ev.evaluate(plan, plan_eval, products, [])

        # Whatever PlanEvaluator decided is delivered, MissionOutcomeEvaluator uses that
        delivered_by_pe = set(
            pkt.packet_id for pkt in plan.packets
            if pkt.packet_id not in plan_eval.deferred_packets
        )
        delivered_by_mo = set(
            dp.product_id for dp in products
            if dp.product_id in delivered_by_pe
        )
        # Counts must agree
        assert outcome.delivered_products == len(delivered_by_mo)
