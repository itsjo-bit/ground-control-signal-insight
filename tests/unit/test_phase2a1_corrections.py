"""Phase 2A.1 tests — GCSI correction pass.

Covers all acceptance criteria for Phase 2A.1:

1.  Stage-2 actual provider message uses compact summaries (no packet lists,
    no provenance strings, no dummy CandidatePlan objects).
2.  MissionOutcomeEvaluator authoritative denominator policy (all rates).
3.  Strict validation (plan/eval mismatch, duplicate IDs, unknown IDs,
    unknown deferred IDs).
4.  Applicable anomaly status semantics (active/monitoring vs resolved).
5.  Shared ranked-prefix builder equivalence test.
6.  Evidence value binding (backend-authoritative, not LLM-supplied).
7.  Stage-2 context size using actual provider message.
8.  Semantic metric differences reflected in Stage-2 context.
9.  Provider message provenance-blind assertions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.stage2_blinding import (
    STAGE2_SYSTEM_PROMPT,
    InvalidStage2AliasError,
    Stage2PlanSummary,
    assert_no_provenance_leak,
    build_blind_context_json,
    build_blind_mapping,
    build_stage2_summaries,
    build_stage2_user_message,
    get_stage2_citeable_fields,
    parse_stage2_response,
)
from backend.app.candidate_generator.ai_plan_builder import (
    AIPlanBuildError,
    build_ai_prioritized_plan,
)
from backend.app.candidate_generator.semantic_rule_plan_builder import (
    build_semantic_rule_plan,
)
from backend.app.candidate_generator.ranked_prefix_builder import (
    SharedPlanBuildError,
    build_ranked_prefix_plan,
)
from backend.app.evaluator.mission_outcome_evaluator import (
    DEFAULT_HIGH_SEVERITY_THRESHOLD,
    APPLICABLE_ANOMALY_STATUSES,
    MissionOutcomeEvaluationError,
    MissionOutcomeEvaluator,
    MissionOutcomeResult,
    is_applicable_anomaly,
)
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_prioritization import CandidatePrioritization, RankedProduct
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.data_product import DataProduct
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.evidence_item import EvidenceItem
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Shared helpers
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
        mission_value=10.0,
        critical_packets_delivered=3,
        total_critical_packets=5,
        deadline_misses=1,
        avg_packet_delay_s=12.5,
        bandwidth_utilization=0.72,
        retransmission_overhead=0.15,
        risk_score=0.35,
        risk_level=RiskLevel.MEDIUM,
        deferred_packets=deferred or [],
        deadline_miss_rate=0.2,
        critical_deficit=0.4,
        window_pressure=0.7,
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


def _prioritization(ranked: list[tuple[str, int]]) -> CandidatePrioritization:
    return CandidatePrioritization(
        ranked_products=[
            RankedProduct(product_id=pid, priority=pri, reason=f"rank {pri}")
            for pid, pri in ranked
        ],
        overall_reasoning="test",
        confidence=0.8,
        candidate_count=len(ranked),
    )


_LS = _link()
_MS = _mission()


# ---------------------------------------------------------------------------
# Section 1: Authoritative denominator policy — delivery rate
# ---------------------------------------------------------------------------


class TestAuthoritativeDeliveryRate:
    """Req 13/14: denominators use full authoritative inventory."""

    def test_omitted_product_counts_as_not_delivered(self):
        """4 authoritative products; plan contains only 2; both delivered.

        delivery_rate = 2/4 = 0.5, NOT 2/2 = 1.0.
        """
        all_products = [_dp(f"P{i}") for i in range(4)]
        plan = _plan("p", ["P0", "P1"])  # only 2 of 4
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.total_products == 4
        assert result.delivered_products == 2
        assert result.delivery_rate == pytest.approx(0.5)

    def test_plan_delivers_zero_of_four(self):
        """Plan contains no products; all 4 count as not delivered."""
        all_products = [_dp(f"P{i}") for i in range(4)]
        plan = _plan("p", [])  # empty plan
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.total_products == 4
        assert result.delivered_products == 0
        assert result.delivery_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Section 2: Authoritative scientific value denominator
# ---------------------------------------------------------------------------


class TestAuthoritativeScientificValue:
    """Req 15/42: scientific value denominator uses full authoritative inventory."""

    def test_omitted_product_reduces_capture_rate(self):
        """authoritative: A sci=0.9, B sci=0.1.  Plan contains only A; A delivered.

        total_scientific_value = 1.0 (full inventory)
        delivered = 0.9
        capture = 0.9 (NOT 1.0)
        """
        all_products = [
            _dp("A", scientific_value=0.9),
            _dp("B", scientific_value=0.1),
        ]
        plan = _plan("p", ["A"])  # B omitted
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.total_scientific_value == pytest.approx(1.0)
        assert result.delivered_scientific_value == pytest.approx(0.9)
        assert result.scientific_value_capture_rate == pytest.approx(0.9)

    def test_full_delivery_of_subset_is_not_1_0(self):
        """Delivering all planned products does not equal 1.0 when others exist."""
        all_products = [_dp(f"P{i}", scientific_value=0.25) for i in range(4)]
        plan = _plan("p", ["P0", "P1"])  # deliver 2 of 4
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.total_scientific_value == pytest.approx(1.0)
        assert result.delivered_scientific_value == pytest.approx(0.5)
        assert result.scientific_value_capture_rate == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Section 3: Authoritative required-product denominator
# ---------------------------------------------------------------------------


class TestAuthoritativeRequiredProduct:
    """Req 16/43: required product denominator uses full authoritative inventory."""

    def test_omitted_required_product_counts_as_not_delivered(self):
        """Authoritative: R1 required, R2 required.
        Plan contains only R1; R1 delivered.
        required_delivery_rate = 1/2 = 0.5 (NOT 1/1 = 1.0).
        """
        all_products = [
            _dp("R1", delivery_requirement="required"),
            _dp("R2", delivery_requirement="required"),
        ]
        plan = _plan("p", ["R1"])  # R2 omitted
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.required_products_total == 2
        assert result.required_products_delivered == 1
        assert result.required_delivery_rate == pytest.approx(0.5)

    def test_plan_with_no_required_products_still_correct_total(self):
        """Plan has no required products but authoritative has 2."""
        all_products = [
            _dp("R1", delivery_requirement="required"),
            _dp("R2", delivery_requirement="required"),
            _dp("B1", delivery_requirement="best_effort"),
        ]
        plan = _plan("p", ["B1"])  # no required products in plan
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [])

        assert result.required_products_total == 2
        assert result.required_products_delivered == 0
        assert result.required_delivery_rate == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Section 4: Authoritative anomaly product denominator
# ---------------------------------------------------------------------------


class TestAuthoritativeAnomalyProduct:
    """Req 17/44: anomaly product denominator uses full authoritative inventory."""

    def test_omitted_anomaly_product_reduces_rate(self):
        """Authoritative: ANOM-017 linked to A, B, C, D.
        Plan contains A and B; only A delivered.
        coverage = 1/4 = 0.25 (NOT 1/2 = 0.5).
        """
        anomaly = _anomaly("ANOM-017", severity=0.9)
        all_products = [
            _dp("A", anomaly_id="ANOM-017"),
            _dp("B", anomaly_id="ANOM-017"),
            _dp("C", anomaly_id="ANOM-017"),
            _dp("D", anomaly_id="ANOM-017"),
        ]
        plan = _plan("p", ["A", "B"])  # C and D omitted
        er = _eval_result("p", deferred=["B"])  # B deferred

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [anomaly])

        # active_anomaly_products_total should be 4 (full inventory)
        assert result.active_anomaly_products_total == 4
        assert result.active_anomaly_products_delivered == 1
        assert result.active_anomaly_delivery_rate == pytest.approx(0.25)

    def test_per_anomaly_denominator_uses_full_inventory(self):
        """Per-anomaly coverage denominator uses all authoritative products linked."""
        anomaly = _anomaly("ANOM-017", severity=0.9)
        all_products = [
            _dp("A", anomaly_id="ANOM-017"),
            _dp("B", anomaly_id="ANOM-017"),
            _dp("C", anomaly_id="ANOM-017"),
            _dp("D", anomaly_id="ANOM-017"),
        ]
        plan = _plan("p", ["A", "B"])  # C and D omitted
        er = _eval_result("p", deferred=["B"])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [anomaly])

        detail = next(d for d in result.anomaly_coverage_by_id if d.anomaly_id == "ANOM-017")
        assert detail.total_linked_products == 4  # full inventory
        assert detail.delivered_linked_products == 1
        assert detail.coverage_rate == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# Section 5: Applicable anomaly status semantics
# ---------------------------------------------------------------------------


class TestApplicableAnomalyStatus:
    """Req 20/45: applicable anomaly statuses are active and monitoring only."""

    def test_active_anomaly_is_applicable(self):
        ae = _anomaly("A1", severity=0.8, status="active")
        assert is_applicable_anomaly(ae) is True

    def test_monitoring_anomaly_is_applicable(self):
        ae = _anomaly("A2", severity=0.8, status="monitoring")
        assert is_applicable_anomaly(ae) is True

    def test_resolved_anomaly_is_not_applicable(self):
        ae = _anomaly("A3", severity=0.8, status="resolved")
        assert is_applicable_anomaly(ae) is False

    def test_unknown_status_is_not_applicable(self):
        ae = _anomaly("A4", severity=0.8, status="unknown_status")
        assert is_applicable_anomaly(ae) is False

    def test_resolved_anomaly_excluded_from_coverage_metrics(self):
        """Resolved anomaly's products are NOT counted in active_anomaly metrics."""
        active_anomaly = _anomaly("ANOM-ACT", severity=0.9, status="active")
        resolved_anomaly = _anomaly("ANOM-RES", severity=0.9, status="resolved")
        all_products = [
            _dp("P1", anomaly_id="ANOM-ACT"),
            _dp("P2", anomaly_id="ANOM-RES"),  # linked to resolved anomaly
        ]
        plan = _plan("p", ["P1", "P2"])
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [active_anomaly, resolved_anomaly])

        # Only ANOM-ACT is applicable
        assert result.active_anomaly_products_total == 1
        assert result.active_anomaly_products_delivered == 1
        # Only ANOM-ACT appears in per-anomaly details
        assert len(result.anomaly_coverage_by_id) == 1
        assert result.anomaly_coverage_by_id[0].anomaly_id == "ANOM-ACT"

    def test_monitoring_anomaly_included_in_coverage_metrics(self):
        """Monitoring anomaly IS counted in active_anomaly metrics."""
        monitoring_anomaly = _anomaly("ANOM-MON", severity=0.8, status="monitoring")
        all_products = [_dp("P1", anomaly_id="ANOM-MON")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(plan, er, all_products, [monitoring_anomaly])

        assert result.active_anomaly_products_total == 1
        assert result.active_anomaly_products_delivered == 1

    def test_applicable_anomaly_statuses_constant(self):
        """The documented applicable statuses set is exactly active and monitoring."""
        assert APPLICABLE_ANOMALY_STATUSES == frozenset({"active", "monitoring"})


# ---------------------------------------------------------------------------
# Section 6: Strict validation — plan/evaluation mismatch
# ---------------------------------------------------------------------------


class TestPlanEvalMismatch:
    """Req 21/46: plan/evaluation mismatch raises MissionOutcomeEvaluationError."""

    def test_mismatched_plan_eval_raises(self):
        """plan.plan_id != evaluation_result.plan_id must raise."""
        plan = _plan("plan-A", ["P1"])
        er = _eval_result("plan-B")  # different plan_id
        products = [_dp("P1")]

        ev = MissionOutcomeEvaluator()
        with pytest.raises(MissionOutcomeEvaluationError, match="plan-A"):
            ev.evaluate(plan, er, products, [])

    def test_matching_ids_do_not_raise(self):
        plan = _plan("plan-X", ["P1"])
        er = _eval_result("plan-X")
        products = [_dp("P1")]

        ev = MissionOutcomeEvaluator()
        result = ev.evaluate(plan, er, products, [])
        assert result.plan_id == "plan-X"


# ---------------------------------------------------------------------------
# Section 7: Strict validation — duplicate authoritative product IDs
# ---------------------------------------------------------------------------


class TestDuplicateProductIds:
    """Req 22/47: duplicate authoritative product IDs raise MissionOutcomeEvaluationError."""

    def test_duplicate_ids_raise(self):
        """Authoritative input with DP-001 twice must raise."""
        plan = _plan("p", ["DP-001"])
        er = _eval_result("p")
        products = [_dp("DP-001"), _dp("DP-001")]  # duplicate

        ev = MissionOutcomeEvaluator()
        with pytest.raises(MissionOutcomeEvaluationError, match="DP-001"):
            ev.evaluate(plan, er, products, [])

    def test_unique_ids_do_not_raise(self):
        plan = _plan("p", ["A", "B"])
        er = _eval_result("p")
        products = [_dp("A"), _dp("B")]

        ev = MissionOutcomeEvaluator()
        result = ev.evaluate(plan, er, products, [])
        assert result.total_products == 2


# ---------------------------------------------------------------------------
# Section 8: Strict validation — unknown plan packet IDs
# ---------------------------------------------------------------------------


class TestUnknownPlanPacketIds:
    """Req 23/48: CandidatePlan referencing unknown authoritative ID must raise."""

    def test_unknown_packet_id_raises(self):
        """Plan references FAKE-999 not in authoritative inventory."""
        plan = _plan("p", ["FAKE-999"])
        er = _eval_result("p")
        products = [_dp("P1"), _dp("P2")]

        ev = MissionOutcomeEvaluator()
        with pytest.raises(MissionOutcomeEvaluationError, match="FAKE-999"):
            ev.evaluate(plan, er, products, [])

    def test_authoritative_ids_only_do_not_raise(self):
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        products = [_dp("P1"), _dp("P2")]

        ev = MissionOutcomeEvaluator()
        result = ev.evaluate(plan, er, products, [])
        assert result.plan_id == "p"


# ---------------------------------------------------------------------------
# Section 9: Strict validation — unknown deferred packet IDs
# ---------------------------------------------------------------------------


class TestUnknownDeferredIds:
    """Req 24: deferred packet IDs not in plan must raise MissionOutcomeEvaluationError."""

    def test_deferred_id_not_in_plan_raises(self):
        """EvaluationResult.deferred_packets contains ID not in the plan."""
        plan = _plan("p", ["P1", "P2"])
        er = _eval_result("p", deferred=["P3"])  # P3 not in plan
        products = [_dp("P1"), _dp("P2"), _dp("P3")]

        ev = MissionOutcomeEvaluator()
        with pytest.raises(MissionOutcomeEvaluationError, match="P3"):
            ev.evaluate(plan, er, products, [])

    def test_valid_deferred_id_does_not_raise(self):
        plan = _plan("p", ["P1", "P2"])
        er = _eval_result("p", deferred=["P2"])  # P2 is in the plan
        products = [_dp("P1"), _dp("P2")]

        ev = MissionOutcomeEvaluator()
        result = ev.evaluate(plan, er, products, [])
        assert result.delivered_products == 1  # P1 delivered, P2 deferred


# ---------------------------------------------------------------------------
# Section 10: Shared ranked-prefix builder equivalence
# ---------------------------------------------------------------------------


class TestSharedRankedPrefixBuilder:
    """Req 25-28/49: AI and semantic-rule plans use the same construction mechanics."""

    def _packets(self) -> list[Packet]:
        return [_pkt(f"P{i}") for i in range(6)]

    def _candidates(self, packets: list[Packet]) -> list[CandidateSummary]:
        return [
            CandidateSummary(
                product_id=p.packet_id,
                product_type="telemetry",
                subsystem="payload",
                size_bits=8_000,
                criticality=0.5,
                mission_relevance=0.5,
                scientific_value=0.5,
                deadline_s=3000.0,
                age_s=100.0,
            )
            for p in packets
        ]

    def test_identical_ranking_produces_identical_packet_orders(self):
        """AI and semantic-rule plans with identical ordering must have the same
        packet sequence (aside from plan_id, strategy, generated_by, metadata)."""
        packets = self._packets()
        # Build a prioritization that ranks all packets in the same order
        ranked = [(f"P{i}", i + 1) for i in range(6)]
        prio = _prioritization(ranked)

        ai_plan = build_ai_prioritized_plan(
            packets, prio, _LS, _MS,
        )

        # Build semantic-rule plan with identical external ranking
        # We'll directly use build_ranked_prefix_plan with the same prioritization
        from backend.app.candidate_generator.ranked_prefix_builder import build_ranked_prefix_plan
        sem_plan = build_ranked_prefix_plan(
            all_packets=packets,
            prioritization=prio,
            link_state=_LS,
            mission_state=_MS,
            plan_id="semantic-rule-based",
            strategy="semantic_rule_based",
            generated_by="test",
            metadata={},
        )

        ai_ids = [p.packet_id for p in ai_plan.packets]
        sem_ids = [p.packet_id for p in sem_plan.packets]

        assert ai_ids == sem_ids, (
            "AI and semantic-rule plans with identical ranking must have identical packet orders. "
            f"AI: {ai_ids}, Semantic: {sem_ids}"
        )

    def test_both_builders_reject_duplicate_input_ids(self):
        """Both builders must raise on duplicate packet IDs."""
        packets = [_pkt("DUP"), _pkt("DUP"), _pkt("P1")]
        prio = _prioritization([])

        with pytest.raises(AIPlanBuildError):
            build_ai_prioritized_plan(packets, prio, _LS, _MS)

        with pytest.raises(SharedPlanBuildError):
            build_ranked_prefix_plan(
                all_packets=packets,
                prioritization=prio,
                link_state=_LS,
                mission_state=_MS,
                plan_id="test",
                strategy="test",
                generated_by="test",
                metadata={},
            )

    def test_both_builders_complete_set_invariant(self):
        """Both builders must include every input packet exactly once."""
        packets = [_pkt(f"P{i}") for i in range(5)]
        ranked = [("P0", 1), ("P2", 2)]
        prio = _prioritization(ranked)

        ai_plan = build_ai_prioritized_plan(packets, prio, _LS, _MS)
        sem_plan = build_ranked_prefix_plan(
            all_packets=packets,
            prioritization=prio,
            link_state=_LS,
            mission_state=_MS,
            plan_id="sem",
            strategy="sem",
            generated_by="test",
            metadata={},
        )

        assert len(ai_plan.packets) == len(packets)
        assert len(sem_plan.packets) == len(packets)
        assert {p.packet_id for p in ai_plan.packets} == {p.packet_id for p in packets}
        assert {p.packet_id for p in sem_plan.packets} == {p.packet_id for p in packets}


# ---------------------------------------------------------------------------
# Section 11: Actual Stage-2 provider message (integration)
# ---------------------------------------------------------------------------


class TestActualProviderMessage:
    """Req 38/39/40/50: actual provider message tests."""

    def _make_summaries(self, option_ids: list[str]) -> list[Stage2PlanSummary]:
        return [
            Stage2PlanSummary(
                option_id=oid,
                total_packets=20,
                deferred_count=2,
                risk_score=0.30,
                risk_level="MEDIUM",
                mission_value=15.0,
                critical_packets_delivered=8,
                total_critical_packets=10,
                deadline_misses=2,
                deadline_miss_rate=0.04,
                bandwidth_utilization=0.72,
                retransmission_overhead=0.15,
                window_pressure=0.69,
                scientific_value_capture_rate=0.81,
                required_delivery_rate=1.0,
                active_anomaly_delivery_rate=0.88,
                high_severity_anomaly_coverage_rate=1.0,
                anomaly_weighted_coverage=0.91,
                average_delivered_age_s=71.4,
            )
            for oid in option_ids
        ]

    def test_stage2_user_message_contains_option_aliases(self):
        """The actual Stage-2 user message must contain OPTION-X keys."""
        summaries = self._make_summaries(["OPTION-A", "OPTION-B", "OPTION-C"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        data = json.loads(msg)

        assert "candidate_options" in data
        assert "OPTION-A" in data["candidate_options"]
        assert "OPTION-B" in data["candidate_options"]
        assert "OPTION-C" in data["candidate_options"]

    def test_stage2_user_message_contains_semantic_metrics(self):
        """The actual Stage-2 user message must contain semantic mission outcome metrics."""
        summaries = self._make_summaries(["OPTION-A"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        data = json.loads(msg)

        option_data = data["candidate_options"]["OPTION-A"]
        assert "scientific_value_capture_rate" in option_data
        assert "active_anomaly_delivery_rate" in option_data
        assert "anomaly_weighted_coverage" in option_data
        assert "required_delivery_rate" in option_data

    def test_stage2_user_message_contains_telecom_metrics(self):
        """The actual Stage-2 user message must contain telecom/feasibility metrics."""
        summaries = self._make_summaries(["OPTION-A"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        data = json.loads(msg)

        option_data = data["candidate_options"]["OPTION-A"]
        assert "risk_score" in option_data
        assert "mission_value" in option_data
        assert "bandwidth_utilization" in option_data
        assert "deadline_misses" in option_data

    def test_stage2_user_message_no_provenance(self):
        """The actual Stage-2 user message must contain no provenance strings."""
        summaries = self._make_summaries(["OPTION-A", "OPTION-B"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        assert_no_provenance_leak(msg)

    def test_stage2_user_message_no_real_plan_ids(self):
        """Plan IDs like 'ai-prioritized', 'baseline' must not appear in message."""
        summaries = self._make_summaries(["OPTION-A"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])

        for forbidden in ["ai-prioritized", "baseline", "deadline-first",
                          "mission-critical-first", "value-per-cost",
                          "ai_prioritized", "deadline_first"]:
            assert forbidden not in msg, (
                f"Provenance string '{forbidden}' found in Stage-2 user message"
            )

    def test_stage2_user_message_no_packet_arrays(self):
        """The actual Stage-2 user message must not contain individual packet_id strings
        or full packet list arrays. 'total_packets' as a metric field is acceptable."""
        summaries = self._make_summaries(["OPTION-A"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        # Individual packet_id field must not appear (no per-packet records)
        assert '"packet_id"' not in msg
        # Array of packets (like "candidate_plans" or "packets": [...]) must not appear
        assert '"packet_actions"' not in msg
        # But total_packets as a metric IS allowed (it's a count, not an array)

    def test_stage2_user_message_contains_anomaly_context(self):
        """Anomaly context appears in actual Stage-2 user message."""
        summaries = self._make_summaries(["OPTION-A"])
        anomaly = _anomaly("ANOM-017", severity=0.91)
        msg = build_stage2_user_message(summaries, _LS, _MS, [anomaly])
        data = json.loads(msg)

        assert "active_anomalies" in data
        assert len(data["active_anomalies"]) == 1
        assert data["active_anomalies"][0]["anomaly_id"] == "ANOM-017"
        assert data["active_anomalies"][0]["severity"] == pytest.approx(0.91)

    def test_stage2_user_message_contains_mission_and_link_context(self):
        """Mission and link context appear in actual Stage-2 user message."""
        summaries = self._make_summaries(["OPTION-A"])
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        data = json.loads(msg)

        assert "mission_context" in data
        assert "link_context" in data
        assert "mission_phase" in data["mission_context"]
        assert "remaining_window_s" in data["link_context"]
        assert "ber" in data["link_context"]

    def test_stage2_user_message_compact_size(self):
        """For 5 plans with 150 packets each, compact context must be much smaller
        than full packet serialization."""
        # Build 5 summaries (equivalent to 5 plans × 150 packets)
        option_ids = [f"OPTION-{chr(65 + i)}" for i in range(5)]
        summaries = self._make_summaries(option_ids)
        msg = build_stage2_user_message(summaries, _LS, _MS, [])

        compact_size = len(msg)
        # Full packet serialization: 5 plans × 150 packets × ~50 chars each
        full_packet_size = 5 * 150 * 50
        assert compact_size < full_packet_size, (
            f"Compact context ({compact_size} chars) is not smaller than "
            f"full packet serialization ({full_packet_size} chars)"
        )
        # Sanity: message must still have reasonable content
        assert compact_size > 100

    def test_granite_provider_message_uses_summaries(self):
        """Granite provider's recommend_from_summaries must use build_stage2_user_message."""
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent(api_key="test", project_id="test")
        summaries = self._make_summaries(["OPTION-A", "OPTION-B"])
        anomaly = _anomaly("ANOM-017", severity=0.9)

        # Capture the actual message built by the agent
        captured_messages = []

        def fake_call_stage2_api(user_message: str) -> str:
            captured_messages.append(user_message)
            return json.dumps({
                "recommended_option_id": "OPTION-A",
                "reasoning": "OPTION-A has best trade-offs",
                "confidence": 0.85,
                "evidence": [],
                "alternative_option_id": None,
            })

        agent._call_stage2_api = fake_call_stage2_api
        agent.recommend_from_summaries(summaries, _LS, _MS, [anomaly])

        assert len(captured_messages) == 1
        msg = captured_messages[0]
        data = json.loads(msg)

        # Must contain options
        assert "OPTION-A" in data["candidate_options"]
        assert "OPTION-B" in data["candidate_options"]
        # Must contain semantic metrics
        assert "scientific_value_capture_rate" in data["candidate_options"]["OPTION-A"]
        assert "active_anomaly_delivery_rate" in data["candidate_options"]["OPTION-A"]
        # Must not contain provenance
        assert_no_provenance_leak(msg)
        # Must not contain packet arrays
        assert "packet_id" not in msg

    def test_gemini_provider_message_uses_summaries(self):
        """Gemini provider's recommend_from_summaries must use build_stage2_user_message."""
        from backend.app.agent.gemini_provider import GeminiProvider

        provider = GeminiProvider(api_key="test-gemini-key")
        summaries = self._make_summaries(["OPTION-A", "OPTION-B"])
        captured_payloads = []

        def fake_post(url, params=None, json=None, **kwargs):
            captured_payloads.append(json)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "candidates": [{
                    "content": {"parts": [{"text": '{"recommended_option_id": "OPTION-A", "reasoning": "test", "confidence": 0.8, "evidence": [], "alternative_option_id": null}'}]}
                }]
            }
            return mock_resp

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = fake_post
            mock_client_class.return_value = mock_client
            provider.recommend_from_summaries(summaries, _LS, _MS, [])

        assert len(captured_payloads) == 1
        # Extract user message from payload
        user_text = captured_payloads[0]["contents"][0]["parts"][0]["text"]
        data = json.loads(user_text)

        assert "OPTION-A" in data["candidate_options"]
        assert "scientific_value_capture_rate" in data["candidate_options"]["OPTION-A"]
        assert_no_provenance_leak(user_text)

    def test_ollama_provider_message_uses_summaries(self):
        """Ollama provider's recommend_from_summaries must use build_stage2_user_message."""
        from backend.app.agent.ollama_provider import OllamaProvider

        provider = OllamaProvider(base_url="http://localhost:11434")
        summaries = self._make_summaries(["OPTION-A", "OPTION-B"])
        captured_prompts = []

        def fake_call_api(prompt: str) -> str:
            captured_prompts.append(prompt)
            return json.dumps({
                "recommended_option_id": "OPTION-A",
                "reasoning": "test",
                "confidence": 0.8,
                "evidence": [],
                "alternative_option_id": None,
            })

        provider._call_api = fake_call_api
        provider.recommend_from_summaries(summaries, _LS, _MS, [])

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        # Extract the user message section from the prompt
        # Format: "<|system|>\n...\n<|user|>\n{user_message}\n<|assistant|>\n"
        user_start = prompt.index("<|user|>\n") + len("<|user|>\n")
        user_end = prompt.index("\n<|assistant|>")
        user_message = prompt[user_start:user_end]
        data = json.loads(user_message)

        assert "OPTION-A" in data["candidate_options"]
        assert "scientific_value_capture_rate" in data["candidate_options"]["OPTION-A"]
        assert_no_provenance_leak(user_message)


# ---------------------------------------------------------------------------
# Section 12: Semantic metric differences reflected in Stage-2 context
# ---------------------------------------------------------------------------


class TestSemanticMetricDifferences:
    """Req 39: different semantic outcomes are visible in Stage-2 context."""

    def test_different_anomaly_coverage_visible_in_context(self):
        """Two options with same risk_score but different anomaly_weighted_coverage
        must produce distinguishable contexts."""
        summary_a = Stage2PlanSummary(
            option_id="OPTION-A",
            total_packets=20,
            deferred_count=2,
            risk_score=0.30,
            risk_level="MEDIUM",
            mission_value=15.0,
            critical_packets_delivered=8,
            total_critical_packets=10,
            deadline_misses=2,
            deadline_miss_rate=0.04,
            bandwidth_utilization=0.72,
            retransmission_overhead=0.10,
            window_pressure=0.69,
            anomaly_weighted_coverage=0.90,
        )
        summary_b = Stage2PlanSummary(
            option_id="OPTION-B",
            total_packets=20,
            deferred_count=2,
            risk_score=0.30,
            risk_level="MEDIUM",
            mission_value=15.0,
            critical_packets_delivered=8,
            total_critical_packets=10,
            deadline_misses=2,
            deadline_miss_rate=0.04,
            bandwidth_utilization=0.72,
            retransmission_overhead=0.10,
            window_pressure=0.69,
            anomaly_weighted_coverage=0.40,
        )
        summaries = [summary_a, summary_b]
        msg = build_stage2_user_message(summaries, _LS, _MS, [])
        data = json.loads(msg)

        cov_a = data["candidate_options"]["OPTION-A"]["anomaly_weighted_coverage"]
        cov_b = data["candidate_options"]["OPTION-B"]["anomaly_weighted_coverage"]
        assert cov_a != cov_b, (
            "Different anomaly_weighted_coverage values must be distinguishable in Stage-2 context"
        )
        assert abs(cov_a - 0.90) < 1e-9
        assert abs(cov_b - 0.40) < 1e-9

    def test_different_scientific_capture_visible_in_context(self):
        """Different scientific_value_capture_rate values are visible in context."""
        summary_a = Stage2PlanSummary(
            option_id="OPTION-A",
            total_packets=10, deferred_count=0,
            risk_score=0.2, risk_level="LOW",
            mission_value=10.0, critical_packets_delivered=5, total_critical_packets=5,
            deadline_misses=0, deadline_miss_rate=0.0,
            bandwidth_utilization=0.5, retransmission_overhead=0.1, window_pressure=0.5,
            scientific_value_capture_rate=0.90,
        )
        summary_b = Stage2PlanSummary(
            option_id="OPTION-B",
            total_packets=10, deferred_count=0,
            risk_score=0.2, risk_level="LOW",
            mission_value=10.0, critical_packets_delivered=5, total_critical_packets=5,
            deadline_misses=0, deadline_miss_rate=0.0,
            bandwidth_utilization=0.5, retransmission_overhead=0.1, window_pressure=0.5,
            scientific_value_capture_rate=0.45,
        )
        msg = build_stage2_user_message([summary_a, summary_b], _LS, _MS, [])
        data = json.loads(msg)

        sci_a = data["candidate_options"]["OPTION-A"]["scientific_value_capture_rate"]
        sci_b = data["candidate_options"]["OPTION-B"]["scientific_value_capture_rate"]
        assert sci_a == pytest.approx(0.90)
        assert sci_b == pytest.approx(0.45)


# ---------------------------------------------------------------------------
# Section 13: Stage-2 response parsing — option ID validation
# ---------------------------------------------------------------------------


class TestStage2ResponseParsing:
    """Req 33/34: option ID validation in Stage-2 response parsing."""

    def _alias_map(self) -> dict[str, str]:
        return {"OPTION-A": "baseline", "OPTION-B": "ai-prioritized", "OPTION-C": "deadline-first"}

    def test_valid_option_id_accepted(self):
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test reason",
            "confidence": 0.85,
            "evidence": [],
            "alternative_option_id": None,
        })
        rec_alias, reasoning, confidence, evidence, alt_alias = parse_stage2_response(
            raw, self._alias_map()
        )
        assert rec_alias == "OPTION-A"
        assert reasoning == "test reason"
        assert confidence == pytest.approx(0.85)
        assert alt_alias is None

    def test_real_plan_id_as_recommended_is_rejected(self):
        """Provider returning a real plan ID must trigger InvalidStage2AliasError."""
        raw = json.dumps({
            "recommended_option_id": "ai-prioritized",  # real plan ID, not alias
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [],
            "alternative_option_id": None,
        })
        with pytest.raises(InvalidStage2AliasError):
            parse_stage2_response(raw, self._alias_map())

    def test_unknown_alias_is_rejected(self):
        raw = json.dumps({
            "recommended_option_id": "OPTION-Z",  # not in alias map
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [],
        })
        with pytest.raises(InvalidStage2AliasError):
            parse_stage2_response(raw, self._alias_map())

    def test_invalid_alternative_is_silently_dropped(self):
        """Invalid alternative alias is dropped, not rejected."""
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [],
            "alternative_option_id": "INVALID-XYZ",
        })
        rec_alias, _, _, _, alt_alias = parse_stage2_response(raw, self._alias_map())
        assert rec_alias == "OPTION-A"
        assert alt_alias is None  # silently dropped

    def test_valid_alternative_is_preserved(self):
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [],
            "alternative_option_id": "OPTION-B",
        })
        rec_alias, _, _, _, alt_alias = parse_stage2_response(raw, self._alias_map())
        assert rec_alias == "OPTION-A"
        assert alt_alias == "OPTION-B"

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_stage2_response("not json at all", self._alias_map())

    def test_missing_required_fields_raises_value_error(self):
        raw = json.dumps({"recommended_option_id": "OPTION-A"})  # missing reasoning, confidence
        with pytest.raises(ValueError, match="missing"):
            parse_stage2_response(raw, self._alias_map())


# ---------------------------------------------------------------------------
# Section 14: Evidence field registry and binding
# ---------------------------------------------------------------------------


class TestEvidenceFieldRegistry:
    """Req 29/30/31/32: evidence fields are validated and values backend-bound."""

    def test_stage2_citeable_fields_include_mission_outcome(self):
        """Stage-2 citeable fields must include MissionOutcomeResult fields."""
        citeable = get_stage2_citeable_fields()
        # MissionOutcomeResult fields
        assert "scientific_value_capture_rate" in citeable
        assert "active_anomaly_delivery_rate" in citeable
        assert "anomaly_weighted_coverage" in citeable
        assert "required_delivery_rate" in citeable
        # Physical fields from Stage2PlanSummary
        assert "risk_score" in citeable
        assert "mission_value" in citeable
        assert "bandwidth_utilization" in citeable
        # Link/mission state fields
        assert "remaining_window_s" in citeable
        assert "ber" in citeable

    def test_unknown_evidence_field_silently_dropped(self):
        """Evidence citing an unknown field is silently dropped in parse_stage2_response."""
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [
                {
                    "option_id": "OPTION-A",
                    "source": "candidate_option",
                    "field": "HALLUCINATED_NONEXISTENT_FIELD",
                    "interpretation": "this field does not exist",
                }
            ],
            "alternative_option_id": None,
        })
        alias_map = {"OPTION-A": "baseline"}
        _, _, _, evidence_dicts, _ = parse_stage2_response(raw, alias_map)
        # Unknown field must be silently dropped
        assert len(evidence_dicts) == 0

    def test_valid_evidence_field_is_preserved(self):
        """Evidence citing a valid field is preserved."""
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [
                {
                    "option_id": "OPTION-A",
                    "source": "candidate_option",
                    "field": "active_anomaly_delivery_rate",
                    "interpretation": "high coverage of anomaly data",
                }
            ],
            "alternative_option_id": None,
        })
        alias_map = {"OPTION-A": "baseline"}
        _, _, _, evidence_dicts, _ = parse_stage2_response(raw, alias_map)
        assert len(evidence_dicts) == 1
        assert evidence_dicts[0]["field"] == "active_anomaly_delivery_rate"


# ---------------------------------------------------------------------------
# Section 15: High-severity anomaly coverage — zero-product policy
# ---------------------------------------------------------------------------


class TestHighSeverityZeroProducts:
    """Req 19: high-severity anomaly with no authoritative products excluded from denominator."""

    def test_high_severity_no_products_excluded_from_denominator(self):
        """A high-severity anomaly with no linked authoritative products must
        NOT distort the high-severity coverage rate denominator."""
        # High-severity anomaly with no linked products
        anom_no_products = _anomaly("ANOM-NOPRODUCT", severity=0.90, status="active")
        # High-severity anomaly with linked product, product delivered
        anom_with_product = _anomaly("ANOM-PRODUCT", severity=0.90, status="active")

        all_products = [_dp("P1", anomaly_id="ANOM-PRODUCT")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p", deferred=[])

        result = MissionOutcomeEvaluator().evaluate(
            plan, er, all_products, [anom_no_products, anom_with_product]
        )

        # ANOM-NOPRODUCT excluded from denominator (no linked products)
        # ANOM-PRODUCT included (has linked products) and covered (P1 delivered)
        assert result.high_severity_anomalies_total == 1  # only ANOM-PRODUCT
        assert result.high_severity_anomalies_covered == 1
        assert result.high_severity_anomaly_coverage_rate == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Section 16: Stage-2 system prompt properties
# ---------------------------------------------------------------------------


class TestStage2SystemPrompt:
    """Req 11/12: Stage-2 system prompt uses option_id terminology, is neutral."""

    def test_system_prompt_uses_option_id_not_plan_id(self):
        """Stage-2 system prompt must reference option_id, not plan_id."""
        assert "recommended_option_id" in STAGE2_SYSTEM_PROMPT
        # Must NOT instruct model to return plan_id
        assert "recommended_plan_id" not in STAGE2_SYSTEM_PROMPT

    def test_system_prompt_references_semantic_metrics(self):
        """System prompt must explicitly reference semantic mission outcome metrics."""
        assert "scientific_value_capture_rate" in STAGE2_SYSTEM_PROMPT
        assert "active_anomaly_delivery_rate" in STAGE2_SYSTEM_PROMPT
        assert "anomaly_weighted_coverage" in STAGE2_SYSTEM_PROMPT

    def test_system_prompt_references_telecom_metrics(self):
        """System prompt must also reference telecom/feasibility metrics."""
        assert "risk_score" in STAGE2_SYSTEM_PROMPT
        assert "bandwidth_utilization" in STAGE2_SYSTEM_PROMPT
        assert "mission_value" in STAGE2_SYSTEM_PROMPT

    def test_system_prompt_is_neutral_no_ai_bias(self):
        """System prompt must not instruct the LLM to favor AI-generated options.
        It may mention 'AI-generated' only in the context of telling the model NOT
        to assume an option is AI-generated (i.e., neutral/unbiased language)."""
        # These biased phrases must NOT appear
        lower = STAGE2_SYSTEM_PROMPT.lower()
        assert "prefer ai" not in lower
        assert "ai plan is better" not in lower
        assert "favor the ai" not in lower
        # The neutral disclaimer "do not assume any option is AI-generated" is correct
        assert "do not assume" in lower

    def test_system_prompt_no_risk_echoing_instruction(self):
        """System prompt must instruct model NOT to echo risk_score."""
        # The prompt tells the LLM not to include risk_score/risk_level
        assert "Do NOT include risk_score" in STAGE2_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Section 17: MissionOutcomeEvaluationError is a distinct typed exception
# ---------------------------------------------------------------------------


class TestMissionOutcomeEvaluationErrorType:
    def test_error_is_not_assertion_error(self):
        assert not issubclass(MissionOutcomeEvaluationError, AssertionError)
        assert issubclass(MissionOutcomeEvaluationError, Exception)

    def test_error_can_be_caught(self):
        plan = _plan("A", ["P1"])
        er = _eval_result("B")  # mismatch
        products = [_dp("P1")]

        caught = False
        try:
            MissionOutcomeEvaluator().evaluate(plan, er, products, [])
        except MissionOutcomeEvaluationError:
            caught = True
        assert caught
