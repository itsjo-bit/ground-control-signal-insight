"""Phase 8B.2 regression tests — subsystem-aware decision evidence.

Covers:
  TASK 18 — MissionOutcomeEvaluator subsystem metrics
  TASK 19 — Stage2PlanSummary subsystem fields
  TASK 20 — Stage-2 prompt / evidence parsing
  TASK 21 — Trade-off information contract

Tests deliberately do NOT:
  - require a live AI provider
  - assert which plan an AI should choose
  - impose subsystem diversity quotas
  - change any existing fixture semantics
"""

from __future__ import annotations

import json

import pytest

from backend.app.agent.stage2_blinding import (
    Stage2PlanSummary,
    assert_no_provenance_leak,
    build_blind_context_json,
    build_blind_mapping,
    build_stage2_summaries,
    get_stage2_citeable_fields,
    is_valid_source_field_strict,
    parse_stage2_response,
)
from backend.app.evaluator.mission_outcome_evaluator import (
    MissionOutcomeEvaluator,
    MissionOutcomeResult,
    UNKNOWN_SUBSYSTEM_KEY,
)
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.data_product import DataProduct
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dp(
    pid: str,
    *,
    subsystem: str = "payload",
    scientific_value: float = 0.5,
    delivery_requirement: str = "best_effort",
    anomaly_id: str | None = None,
) -> DataProduct:
    return DataProduct(
        product_id=pid,
        product_type="telemetry",
        subsystem=subsystem,
        size_bits=8_000,
        criticality=0.5,
        mission_relevance=0.5,
        scientific_value=scientific_value,
        deadline_s=3_000.0,
        age_s=100.0,
        delivery_requirement=delivery_requirement,
        retry_cost=0.1,
        anomaly_id=anomaly_id,
    )


def _pkt(pid: str) -> Packet:
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=8_000,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=3_000.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


def _plan(plan_id: str, pids: list[str]) -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy="test",
        packets=[_pkt(pid) for pid in pids],
        generated_by="test",
        metadata={},
    )


def _eval_result(plan_id: str, deferred: list[str] | None = None) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=10.0,
        critical_packets_delivered=3,
        total_critical_packets=5,
        deadline_misses=0,
        avg_packet_delay_s=5.0,
        bandwidth_utilization=0.7,
        retransmission_overhead=0.1,
        risk_score=0.25,
        risk_level=RiskLevel.LOW,
        deferred_packets=deferred or [],
        deadline_miss_rate=0.0,
        critical_deficit=0.0,
        window_pressure=0.5,
    )


_ev = MissionOutcomeEvaluator()


# ---------------------------------------------------------------------------
# TASK 18A — Mixed subsystem delivery
# ---------------------------------------------------------------------------

class TestSubsystemMetricsMixed:
    """Authoritative: JIRAM, MWR, JADE, WAVES. Delivered: JIRAM, MWR."""

    def _build(self):
        products = [
            _dp("J1", subsystem="jiram"),
            _dp("J2", subsystem="jiram"),
            _dp("M1", subsystem="mwr"),
            _dp("D1", subsystem="jade"),
            _dp("W1", subsystem="waves"),
        ]
        # Deliver J1, J2, M1; defer D1, W1
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=["D1", "W1"])
        return products, plan, er

    def test_total_subsystems(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 4

    def test_delivered_subsystems(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.delivered_subsystems == 2  # jiram + mwr

    def test_subsystem_coverage_rate(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.subsystem_coverage_rate == pytest.approx(0.5)

    def test_delivered_by_subsystem_counts(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.delivered_by_subsystem["jiram"] == 2
        assert r.delivered_by_subsystem["mwr"] == 1
        assert "jade" not in r.delivered_by_subsystem
        assert "waves" not in r.delivered_by_subsystem

    def test_sum_consistency(self):
        """sum(delivered_by_subsystem.values()) == delivered_products."""
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert sum(r.delivered_by_subsystem.values()) == r.delivered_products


# ---------------------------------------------------------------------------
# TASK 18B — One-subsystem concentration
# ---------------------------------------------------------------------------

class TestSubsystemMetricsConcentrated:
    """Delivered: JIRAM x3; authoritative includes multiple subsystems."""

    def _build(self):
        products = [
            _dp("J1", subsystem="jiram"),
            _dp("J2", subsystem="jiram"),
            _dp("J3", subsystem="jiram"),
            _dp("M1", subsystem="mwr"),
            _dp("D1", subsystem="jade"),
        ]
        # Deliver only JIRAM products; defer MWR + JADE
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=["M1", "D1"])
        return products, plan, er

    def test_delivered_subsystems_is_one(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.delivered_subsystems == 1

    def test_total_subsystems_uses_authoritative_denominator(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 3  # jiram, mwr, jade

    def test_coverage_rate_uses_authoritative_denominator(self):
        products, plan, er = self._build()
        r = _ev.evaluate(plan, er, products, [])
        assert r.subsystem_coverage_rate == pytest.approx(1 / 3)


# ---------------------------------------------------------------------------
# TASK 18C — All subsystems delivered
# ---------------------------------------------------------------------------

class TestSubsystemMetricsAllDelivered:
    def test_coverage_is_1_when_all_delivered(self):
        products = [
            _dp("A", subsystem="jiram"),
            _dp("B", subsystem="mwr"),
            _dp("C", subsystem="jade"),
        ]
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 3
        assert r.delivered_subsystems == 3
        assert r.subsystem_coverage_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# TASK 18D — Zero denominator (no non-empty subsystems)
# ---------------------------------------------------------------------------

class TestSubsystemMetricsZeroDenominator:
    def test_null_rate_when_no_non_empty_subsystems(self):
        """All products have empty/whitespace subsystem → rate is None, not 1.0."""
        products = [
            _dp("X", subsystem=""),
            _dp("Y", subsystem="   "),
        ]
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 0
        assert r.subsystem_coverage_rate is None

    def test_unknown_key_used_for_empty_subsystem(self):
        products = [_dp("X", subsystem="")]
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert UNKNOWN_SUBSYSTEM_KEY in r.delivered_by_subsystem

    def test_unknown_key_excluded_from_total_subsystems(self):
        """__unknown__ does not count toward total_subsystems denominator."""
        products = [_dp("X", subsystem=""), _dp("Y", subsystem="jiram")]
        plan = _plan("p", ["X", "Y"])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 1  # only jiram
        assert r.delivered_subsystems == 1

    def test_no_products_empty_result(self):
        """Empty authoritative inventory → zero counts, None rate."""
        plan = _plan("p", [])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, [], [])
        assert r.total_subsystems == 0
        assert r.delivered_subsystems == 0
        assert r.subsystem_coverage_rate is None


# ---------------------------------------------------------------------------
# TASK 18 additional — name normalisation
# ---------------------------------------------------------------------------

class TestSubsystemNormalisation:
    def test_mixed_case_normalised(self):
        """'JIRAM', 'Jiram', 'jiram' all map to the same key."""
        products = [
            _dp("A", subsystem="JIRAM"),
            _dp("B", subsystem="Jiram"),
            _dp("C", subsystem="jiram"),
        ]
        plan = _plan("p", [dp.product_id for dp in products])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert r.total_subsystems == 1
        assert r.delivered_subsystems == 1
        assert r.delivered_by_subsystem.get("jiram", 0) == 3

    def test_whitespace_stripped(self):
        products = [_dp("X", subsystem="  mwr  ")]
        plan = _plan("p", ["X"])
        er = _eval_result("p", deferred=[])
        r = _ev.evaluate(plan, er, products, [])
        assert "mwr" in r.delivered_by_subsystem


# ---------------------------------------------------------------------------
# TASK 19 — Stage2PlanSummary subsystem fields
# ---------------------------------------------------------------------------

class TestStage2SummarySubsystemFields:
    def _mo(self, plan_id: str) -> MissionOutcomeResult:
        return MissionOutcomeResult(
            plan_id=plan_id,
            total_products=5,
            delivered_products=2,
            delivery_rate=0.4,
            total_scientific_value=2.5,
            delivered_scientific_value=1.0,
            scientific_value_capture_rate=0.4,
            required_products_total=0,
            required_products_delivered=0,
            required_delivery_rate=None,
            active_anomaly_products_total=0,
            active_anomaly_products_delivered=0,
            active_anomaly_delivery_rate=None,
            high_severity_threshold=0.75,
            high_severity_anomalies_total=0,
            high_severity_anomalies_covered=0,
            high_severity_anomaly_coverage_rate=None,
            # Subsystem fields
            total_subsystems=4,
            delivered_subsystems=2,
            subsystem_coverage_rate=0.5,
            delivered_by_subsystem={"jiram": 1, "mwr": 1},
        )

    def test_subsystem_fields_in_summary(self):
        plan = _plan("plan-x", ["A", "B"])
        er = _eval_result("plan-x", deferred=["B"])
        mo = self._mo("plan-x")
        alias_map = build_blind_mapping([plan], scenario_id="test")
        summaries = build_stage2_summaries(alias_map, [plan], [er], [mo])
        s = summaries[0]
        assert s.total_subsystems == 4
        assert s.delivered_subsystems == 2
        assert s.subsystem_coverage_rate == pytest.approx(0.5)
        assert s.delivered_by_subsystem == {"jiram": 1, "mwr": 1}

    def test_subsystem_fields_in_context_json(self):
        plan = _plan("plan-x", ["A", "B"])
        er = _eval_result("plan-x", deferred=["B"])
        mo = self._mo("plan-x")
        alias_map = build_blind_mapping([plan], scenario_id="test")
        summaries = build_stage2_summaries(alias_map, [plan], [er], [mo])
        ctx = build_blind_context_json(summaries)
        data = json.loads(ctx)
        option_data = list(data.values())[0]
        assert "subsystem_coverage_rate" in option_data
        assert "delivered_subsystems" in option_data
        assert "total_subsystems" in option_data
        assert "delivered_by_subsystem" in option_data

    def test_no_subsystem_fields_when_no_mission_outcomes(self):
        """Without mission_outcomes, subsystem fields are absent (None → omitted)."""
        plan = _plan("plan-x", ["A", "B"])
        er = _eval_result("plan-x", deferred=["B"])
        alias_map = build_blind_mapping([plan], scenario_id="test")
        summaries = build_stage2_summaries(alias_map, [plan], [er])
        ctx = build_blind_context_json(summaries)
        data = json.loads(ctx)
        option_data = list(data.values())[0]
        assert "subsystem_coverage_rate" not in option_data
        assert "delivered_by_subsystem" not in option_data

    def test_no_provenance_leak_with_subsystem_data(self):
        """Subsystem names are mission content — they must NOT trigger provenance leak."""
        plan = _plan("plan-x", ["A"])
        er = _eval_result("plan-x")
        mo = self._mo("plan-x")
        alias_map = build_blind_mapping([plan], scenario_id="test")
        summaries = build_stage2_summaries(alias_map, [plan], [er], [mo])
        ctx = build_blind_context_json(summaries)
        # Must not raise — jiram/mwr are mission content, not plan provenance
        assert_no_provenance_leak(ctx)

    def test_subsystem_coverage_rate_is_citeable(self):
        """subsystem_coverage_rate must be in the citeable fields set."""
        citeable = get_stage2_citeable_fields()
        assert "subsystem_coverage_rate" in citeable

    def test_delivered_by_subsystem_is_citeable(self):
        assert "delivered_by_subsystem" in get_stage2_citeable_fields()

    def test_delivered_subsystems_is_citeable(self):
        assert "delivered_subsystems" in get_stage2_citeable_fields()

    def test_total_subsystems_is_citeable(self):
        assert "total_subsystems" in get_stage2_citeable_fields()

    def test_scientific_value_capture_rate_is_citeable(self):
        assert "scientific_value_capture_rate" in get_stage2_citeable_fields()

    def test_source_field_strict_accepts_subsystem_coverage_rate(self):
        assert is_valid_source_field_strict("candidate_option", "subsystem_coverage_rate")

    def test_source_field_strict_accepts_delivered_by_subsystem(self):
        assert is_valid_source_field_strict("candidate_option", "delivered_by_subsystem")

    def test_source_field_strict_rejects_nonexistent_field(self):
        assert not is_valid_source_field_strict("candidate_option", "nonexistent_subsystem_xyz")

    def test_source_field_strict_rejects_strategy(self):
        assert not is_valid_source_field_strict("candidate_option", "strategy")


# ---------------------------------------------------------------------------
# TASK 20 — Stage-2 response parsing: evidence can cite subsystem fields
# ---------------------------------------------------------------------------

class TestStage2EvidenceParsing:
    def _alias_map(self) -> dict[str, str]:
        return {"OPTION-A": "plan-a", "OPTION-B": "plan-b"}

    def _summary(self, option_id: str, coverage: float | None) -> Stage2PlanSummary:
        return Stage2PlanSummary(
            option_id=option_id,
            total_packets=10,
            deferred_count=8,
            risk_score=0.3,
            risk_level="LOW",
            mission_value=5.0,
            critical_packets_delivered=2,
            total_critical_packets=3,
            deadline_misses=0,
            deadline_miss_rate=0.0,
            bandwidth_utilization=0.6,
            retransmission_overhead=0.1,
            window_pressure=0.5,
            # subsystem
            subsystem_coverage_rate=coverage,
            delivered_subsystems=1 if coverage is not None else None,
            total_subsystems=9 if coverage is not None else None,
            delivered_by_subsystem={"jiram": 2} if coverage is not None else None,
            scientific_value_capture_rate=0.3,
        )

    def _valid_response(self, option_id: str, field: str) -> str:
        return json.dumps({
            "recommended_option_id": option_id,
            "reasoning": "Test reasoning",
            "confidence": 0.8,
            "evidence": [{
                "option_id": option_id,
                "source": "candidate_option",
                "field": field,
                "interpretation": "This field supports the recommendation",
            }],
            "alternative_option_id": None,
        })

    def test_subsystem_coverage_rate_evidence_accepted(self):
        alias_map = self._alias_map()
        summaries = [
            self._summary("OPTION-A", 0.11),
            self._summary("OPTION-B", 0.56),
        ]
        raw = self._valid_response("OPTION-A", "subsystem_coverage_rate")
        _, _, _, evidence, _ = parse_stage2_response(raw, alias_map, summaries)
        assert any(e["field"] == "subsystem_coverage_rate" for e in evidence)

    def test_scientific_value_capture_rate_evidence_accepted(self):
        alias_map = self._alias_map()
        summaries = [
            self._summary("OPTION-A", 0.11),
            self._summary("OPTION-B", 0.56),
        ]
        raw = self._valid_response("OPTION-A", "scientific_value_capture_rate")
        _, _, _, evidence, _ = parse_stage2_response(raw, alias_map, summaries)
        assert any(e["field"] == "scientific_value_capture_rate" for e in evidence)

    def test_nonexistent_subsystem_field_rejected(self):
        alias_map = self._alias_map()
        summaries = [
            self._summary("OPTION-A", 0.11),
            self._summary("OPTION-B", 0.56),
        ]
        raw = self._valid_response("OPTION-A", "nonexistent_magic_subsystem_field")
        _, _, _, evidence, _ = parse_stage2_response(raw, alias_map, summaries)
        assert not any(e["field"] == "nonexistent_magic_subsystem_field" for e in evidence)

    def test_subsystem_coverage_rate_dropped_when_none_for_option(self):
        """Gate 0.4: evidence citing subsystem_coverage_rate is dropped when field is None."""
        alias_map = self._alias_map()
        summaries = [
            self._summary("OPTION-A", None),   # subsystem_coverage_rate is None
            self._summary("OPTION-B", 0.56),
        ]
        raw = self._valid_response("OPTION-A", "subsystem_coverage_rate")
        _, _, _, evidence, _ = parse_stage2_response(raw, alias_map, summaries)
        # Should be dropped because the field is None for OPTION-A
        assert not any(e["field"] == "subsystem_coverage_rate" for e in evidence)


# ---------------------------------------------------------------------------
# TASK 21 — Trade-off information contract
# ---------------------------------------------------------------------------

class TestTradeOffInformationContract:
    """Verify Stage-2 receives both subsystem composition AND outcome metrics.

    OPTION-A: higher mission_value, concentrated (1/9 subsystems)
    OPTION-B: lower mission_value, broader (5/9 subsystems)

    The AI receives both fact sets and is free to choose either.
    We only verify the information is present.
    """

    def _build_concentrated_mo(self, plan_id: str) -> MissionOutcomeResult:
        return MissionOutcomeResult(
            plan_id=plan_id,
            total_products=30,
            delivered_products=29,
            delivery_rate=29 / 30,
            total_scientific_value=5.0,
            delivered_scientific_value=2.0,
            scientific_value_capture_rate=0.4,
            required_products_total=0,
            required_products_delivered=0,
            required_delivery_rate=None,
            active_anomaly_products_total=0,
            active_anomaly_products_delivered=0,
            active_anomaly_delivery_rate=None,
            high_severity_threshold=0.75,
            high_severity_anomalies_total=0,
            high_severity_anomalies_covered=0,
            high_severity_anomaly_coverage_rate=None,
            total_subsystems=9,
            delivered_subsystems=1,
            subsystem_coverage_rate=1 / 9,
            delivered_by_subsystem={"jiram": 29},
        )

    def _build_diverse_mo(self, plan_id: str) -> MissionOutcomeResult:
        return MissionOutcomeResult(
            plan_id=plan_id,
            total_products=30,
            delivered_products=27,
            delivery_rate=27 / 30,
            total_scientific_value=5.0,
            delivered_scientific_value=2.5,
            scientific_value_capture_rate=0.5,
            required_products_total=0,
            required_products_delivered=0,
            required_delivery_rate=None,
            active_anomaly_products_total=0,
            active_anomaly_products_delivered=0,
            active_anomaly_delivery_rate=None,
            high_severity_threshold=0.75,
            high_severity_anomalies_total=0,
            high_severity_anomalies_covered=0,
            high_severity_anomaly_coverage_rate=None,
            total_subsystems=9,
            delivered_subsystems=5,
            subsystem_coverage_rate=5 / 9,
            delivered_by_subsystem={"jiram": 8, "mwr": 5, "jade": 4, "jedi": 4, "waves": 6},
        )

    def _make_plan_and_eval(self, plan_id: str, ev_value: float, n_packets: int, n_deferred: int):
        pkts = [_pkt(f"{plan_id}-P{i}") for i in range(n_packets)]
        plan = CandidatePlan(
            plan_id=plan_id, strategy="test",
            packets=pkts, generated_by="test", metadata={},
        )
        er = EvaluationResult(
            plan_id=plan_id,
            mission_value=ev_value,
            critical_packets_delivered=0,
            total_critical_packets=0,
            deadline_misses=0,
            avg_packet_delay_s=0.0,
            bandwidth_utilization=0.6,
            retransmission_overhead=0.1,
            risk_score=0.2,
            risk_level=RiskLevel.LOW,
            deferred_packets=[f"{plan_id}-P{i}" for i in range(n_deferred)],
            deadline_miss_rate=0.0,
            critical_deficit=0.0,
            window_pressure=0.5,
        )
        return plan, er

    def test_both_options_carry_subsystem_facts(self):
        plan_a, ev_a = self._make_plan_and_eval("plan-a", ev_value=15.0, n_packets=30, n_deferred=1)
        plan_b, ev_b = self._make_plan_and_eval("plan-b", ev_value=12.0, n_packets=30, n_deferred=3)
        mo_a = self._build_concentrated_mo("plan-a")
        mo_b = self._build_diverse_mo("plan-b")

        alias_map = build_blind_mapping([plan_a, plan_b], scenario_id="trade-off-test")
        summaries = build_stage2_summaries(
            alias_map, [plan_a, plan_b], [ev_a, ev_b], [mo_a, mo_b]
        )

        # Both options must carry subsystem evidence
        for s in summaries:
            assert s.total_subsystems == 9, f"{s.option_id} total_subsystems mismatch"
            assert s.subsystem_coverage_rate is not None
            assert s.delivered_by_subsystem is not None

    def test_concentrated_option_has_low_coverage(self):
        plan_a, ev_a = self._make_plan_and_eval("plan-a", ev_value=15.0, n_packets=30, n_deferred=1)
        mo_a = self._build_concentrated_mo("plan-a")
        alias_map = build_blind_mapping([plan_a], scenario_id="conc-test")
        summaries = build_stage2_summaries(alias_map, [plan_a], [ev_a], [mo_a])
        s = summaries[0]
        assert s.delivered_subsystems == 1
        assert s.subsystem_coverage_rate == pytest.approx(1 / 9)
        assert s.delivered_by_subsystem == {"jiram": 29}

    def test_diverse_option_has_higher_coverage(self):
        plan_b, ev_b = self._make_plan_and_eval("plan-b", ev_value=12.0, n_packets=30, n_deferred=3)
        mo_b = self._build_diverse_mo("plan-b")
        alias_map = build_blind_mapping([plan_b], scenario_id="div-test")
        summaries = build_stage2_summaries(alias_map, [plan_b], [ev_b], [mo_b])
        s = summaries[0]
        assert s.delivered_subsystems == 5
        assert s.subsystem_coverage_rate == pytest.approx(5 / 9)

    def test_context_json_contains_subsystem_facts_for_both(self):
        plan_a, ev_a = self._make_plan_and_eval("plan-a", ev_value=15.0, n_packets=30, n_deferred=1)
        plan_b, ev_b = self._make_plan_and_eval("plan-b", ev_value=12.0, n_packets=30, n_deferred=3)
        mo_a = self._build_concentrated_mo("plan-a")
        mo_b = self._build_diverse_mo("plan-b")

        alias_map = build_blind_mapping([plan_a, plan_b], scenario_id="ctx-test")
        summaries = build_stage2_summaries(
            alias_map, [plan_a, plan_b], [ev_a, ev_b], [mo_a, mo_b]
        )
        ctx = build_blind_context_json(summaries)
        data = json.loads(ctx)

        for option_data in data.values():
            assert "subsystem_coverage_rate" in option_data
            assert "delivered_by_subsystem" in option_data
            assert "total_subsystems" in option_data

    def test_no_provenance_in_trade_off_context(self):
        plan_a, ev_a = self._make_plan_and_eval("plan-a", ev_value=15.0, n_packets=30, n_deferred=1)
        plan_b, ev_b = self._make_plan_and_eval("plan-b", ev_value=12.0, n_packets=30, n_deferred=3)
        mo_a = self._build_concentrated_mo("plan-a")
        mo_b = self._build_diverse_mo("plan-b")

        alias_map = build_blind_mapping([plan_a, plan_b], scenario_id="ctx-test")
        summaries = build_stage2_summaries(
            alias_map, [plan_a, plan_b], [ev_a, ev_b], [mo_a, mo_b]
        )
        ctx = build_blind_context_json(summaries)
        # Must not leak plan identity despite subsystem names being present
        assert_no_provenance_leak(ctx)
