"""Unit tests for PlanEvaluator — deterministic expected/analytical metrics.

All tests use hand-crafted fixtures with known analytical answers.
No RNG is involved anywhere in these tests or the evaluator itself.
"""

import math
from datetime import datetime, timezone

import pytest

from backend.app.config import RiskWeights
from backend.app.evaluator.plan_evaluator import PlanEvaluator, _risk_level_from_score
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_link_state(
    *,
    ber: float = 0.0,
    link_goodput_bps: float = 100_000.0,
    remaining_window_s: float = 300.0,
) -> LinkState:
    return LinkState(
        timestamp=_TS,
        snr_db=12.0,
        eb_n0_db=20.0,
        ber=ber,
        rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0,
        link_goodput_bps=link_goodput_bps,
        latency_s=0.0,
        link_stability=1.0,
        remaining_window_s=remaining_window_s,
    )


def make_mission_state(
    *,
    comm_window_remaining_s: float = 300.0,
    risk_score: float = 0.1,
) -> MissionState:
    return MissionState(
        mission_id="test-mission",
        mission_phase="test",
        current_event="test_event",
        event_time_remaining_s=300.0,
        comm_window_remaining_s=comm_window_remaining_s,
        risk_score=risk_score,
        risk_level=RiskLevel.LOW,
    )


def make_packet(
    packet_id: str = "pkt-001",
    *,
    size_bits: int = 8_000,      # 8 kbits → tx_time = 0.08 s at 100 kbps
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    deadline_s: float = 300.0,
) -> Packet:
    return Packet(
        packet_id=packet_id,
        packet_type="telemetry",
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=mission_relevance,
        deadline_s=deadline_s,
        retry_cost=0.1,
        delivery_requirement="best-effort",
    )


def make_plan(packets: list[Packet], plan_id: str = "baseline") -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy=plan_id,
        packets=packets,
        generated_by="test",
    )


# ---------------------------------------------------------------------------
# Risk level threshold tests
# ---------------------------------------------------------------------------

class TestRiskLevelThresholds:
    def test_below_025_is_low(self):
        assert _risk_level_from_score(0.0) == RiskLevel.LOW
        assert _risk_level_from_score(0.24) == RiskLevel.LOW

    def test_025_to_049_is_medium(self):
        assert _risk_level_from_score(0.25) == RiskLevel.MEDIUM
        assert _risk_level_from_score(0.49) == RiskLevel.MEDIUM

    def test_050_to_074_is_high(self):
        assert _risk_level_from_score(0.50) == RiskLevel.HIGH
        assert _risk_level_from_score(0.74) == RiskLevel.HIGH

    def test_075_and_above_is_critical(self):
        assert _risk_level_from_score(0.75) == RiskLevel.CRITICAL
        assert _risk_level_from_score(1.0) == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Zero-packet plan
# ---------------------------------------------------------------------------

class TestEmptyPlan:
    def test_zero_packets_returns_zero_metrics(self):
        ev = PlanEvaluator()
        result = ev.evaluate(
            make_plan([]),
            make_link_state(),
            make_mission_state(),
        )
        assert isinstance(result, EvaluationResult)
        assert result.mission_value == 0.0
        assert result.critical_packets_delivered == 0
        assert result.total_critical_packets == 0
        assert result.deadline_misses == 0
        assert result.avg_packet_delay_s == 0.0
        assert result.bandwidth_utilization == 0.0
        assert result.retransmission_overhead == 0.0
        assert result.deferred_packets == []

    def test_zero_packets_risk_score_is_zero(self):
        ev = PlanEvaluator()
        result = ev.evaluate(make_plan([]), make_link_state(), make_mission_state())
        # 0 deadline misses / max(0,1)=0; 0 critical deficit; window=full → risk ≈ 0
        assert result.risk_score == pytest.approx(0.0, abs=0.01)
        assert result.risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# Single-packet plan — sanity checks
# ---------------------------------------------------------------------------

class TestSinglePacket:
    def test_plan_id_propagated(self):
        ev = PlanEvaluator()
        pkt = make_packet("pkt-x", size_bits=8_000, criticality=0.8, mission_relevance=0.9)
        result = ev.evaluate(make_plan([pkt], "my-plan"), make_link_state(), make_mission_state())
        assert result.plan_id == "my-plan"

    def test_mission_value_is_criticality_times_relevance(self):
        ev = PlanEvaluator()
        pkt = make_packet("pkt-x", criticality=0.8, mission_relevance=0.9)
        result = ev.evaluate(make_plan([pkt]), make_link_state(), make_mission_state())
        assert result.mission_value == pytest.approx(0.8 * 0.9)

    def test_no_deadline_miss_when_delivered_before_deadline(self):
        """A small packet at high goodput finishes well before its deadline."""
        ev = PlanEvaluator()
        # tx_time = 8000 / 100000 = 0.08 s; deadline = 100 s → no miss
        pkt = make_packet("pkt-x", size_bits=8_000, deadline_s=100.0)
        result = ev.evaluate(make_plan([pkt]), make_link_state(ber=0.0, link_goodput_bps=100_000.0), make_mission_state())
        assert result.deadline_misses == 0

    def test_deadline_miss_when_delivery_exceeds_deadline(self):
        """Packet takes longer than its deadline."""
        ev = PlanEvaluator()
        # tx_time = 1_000_000 / 100_000 = 10 s; deadline = 5 s → miss
        pkt = make_packet("pkt-x", size_bits=1_000_000, deadline_s=5.0)
        result = ev.evaluate(make_plan([pkt]), make_link_state(ber=0.0, link_goodput_bps=100_000.0), make_mission_state())
        assert result.deadline_misses == 1

    def test_zero_retransmission_overhead_on_perfect_channel(self):
        """BER=0 → p_success=1 → zero retransmission overhead."""
        ev = PlanEvaluator()
        pkt = make_packet("pkt-x", size_bits=8_000)
        result = ev.evaluate(make_plan([pkt]), make_link_state(ber=0.0), make_mission_state())
        assert result.retransmission_overhead == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Deferred packets — window enforcement
# ---------------------------------------------------------------------------

class TestDeferredPackets:
    def test_packet_deferred_when_window_exhausted(self):
        """With a 1-second window and two slow packets, only the first fits."""
        ev = PlanEvaluator()
        # Each packet: tx_time = 100_000 / 100_000 = 1 s exactly
        pkt_a = make_packet("pkt-a", size_bits=100_000)
        pkt_b = make_packet("pkt-b", size_bits=100_000)
        # Window = 1 s → pkt_a uses all of it; pkt_b is deferred
        result = ev.evaluate(
            make_plan([pkt_a, pkt_b]),
            make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=1.0),
            make_mission_state(comm_window_remaining_s=1.0),
        )
        assert "pkt-a" not in result.deferred_packets
        assert "pkt-b" in result.deferred_packets

    def test_all_packets_deferred_when_zero_window(self):
        ev = PlanEvaluator()
        pkts = [make_packet(f"pkt-{i}") for i in range(3)]
        result = ev.evaluate(
            make_plan(pkts),
            make_link_state(remaining_window_s=0.0),
            make_mission_state(comm_window_remaining_s=0.0),
        )
        assert set(result.deferred_packets) == {"pkt-0", "pkt-1", "pkt-2"}

    def test_deferred_packets_not_counted_in_mission_value(self):
        ev = PlanEvaluator()
        pkt_a = make_packet("pkt-a", size_bits=100_000, criticality=0.9, mission_relevance=0.9)
        pkt_b = make_packet("pkt-b", size_bits=1_000, criticality=0.8, mission_relevance=0.8)
        # Window 1 s → pkt_a (1 s tx) fits; pkt_b is deferred
        ls = make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=1.0)
        ms = make_mission_state(comm_window_remaining_s=1.0)
        result = ev.evaluate(make_plan([pkt_a, pkt_b]), ls, ms)
        assert result.mission_value == pytest.approx(0.9 * 0.9)


# ---------------------------------------------------------------------------
# Critical packet counting
# ---------------------------------------------------------------------------

class TestCriticalPackets:
    def test_critical_threshold_default_is_0_7(self):
        """Packets with criticality >= 0.7 should count as critical."""
        ev = PlanEvaluator()
        pkt_hi = make_packet("pkt-hi", criticality=0.8)
        pkt_lo = make_packet("pkt-lo", criticality=0.5)
        result = ev.evaluate(make_plan([pkt_hi, pkt_lo]), make_link_state(), make_mission_state())
        assert result.total_critical_packets == 1
        assert result.critical_packets_delivered == 1

    def test_custom_criticality_threshold(self):
        ev = PlanEvaluator(criticality_threshold=0.5)
        pkt_a = make_packet("pkt-a", criticality=0.6)
        pkt_b = make_packet("pkt-b", criticality=0.4)
        result = ev.evaluate(make_plan([pkt_a, pkt_b]), make_link_state(), make_mission_state())
        assert result.total_critical_packets == 1
        assert result.critical_packets_delivered == 1

    def test_deferred_critical_packet_not_counted_as_delivered(self):
        ev = PlanEvaluator()
        pkt_a = make_packet("pkt-a", size_bits=100_000, criticality=0.3)
        pkt_b = make_packet("pkt-b", size_bits=1_000, criticality=0.9)
        # Window = 1 s → pkt_a fills it; pkt_b (critical) deferred
        ls = make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=1.0)
        ms = make_mission_state(comm_window_remaining_s=1.0)
        result = ev.evaluate(make_plan([pkt_a, pkt_b]), ls, ms)
        assert result.total_critical_packets == 1
        assert result.critical_packets_delivered == 0


# ---------------------------------------------------------------------------
# Bandwidth utilization
# ---------------------------------------------------------------------------

class TestBandwidthUtilization:
    def test_single_packet_utilization(self):
        """Utilization = total_delivered_bits / (goodput * window)."""
        ev = PlanEvaluator()
        # 10_000 bits at 100_000 bps over 300 s window
        pkt = make_packet("pkt-x", size_bits=10_000)
        result = ev.evaluate(
            make_plan([pkt]),
            make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=300.0),
            make_mission_state(comm_window_remaining_s=300.0),
        )
        expected = 10_000 / (100_000.0 * 300.0)
        assert result.bandwidth_utilization == pytest.approx(expected, rel=1e-5)

    def test_utilization_capped_at_one(self):
        """Utilization never exceeds 1.0."""
        ev = PlanEvaluator()
        pkts = [make_packet(f"pkt-{i}", size_bits=1_000_000) for i in range(100)]
        result = ev.evaluate(
            make_plan(pkts),
            make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=300.0),
            make_mission_state(comm_window_remaining_s=300.0),
        )
        assert result.bandwidth_utilization <= 1.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_identical_inputs_produce_identical_results(self):
        ev = PlanEvaluator()
        pkts = [
            make_packet("pkt-a", size_bits=8_000, criticality=0.9),
            make_packet("pkt-b", size_bits=16_000, criticality=0.5),
        ]
        plan = make_plan(pkts)
        ls = make_link_state(ber=1e-6)
        ms = make_mission_state()

        r1 = ev.evaluate(plan, ls, ms)
        r2 = ev.evaluate(plan, ls, ms)

        assert r1.model_dump() == r2.model_dump()

    def test_returns_evaluation_result_type(self):
        ev = PlanEvaluator()
        result = ev.evaluate(make_plan([make_packet("p")]), make_link_state(), make_mission_state())
        assert isinstance(result, EvaluationResult)


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------

class TestRiskScore:
    def test_risk_score_is_in_01_range(self):
        ev = PlanEvaluator()
        pkts = [make_packet(f"p{i}", deadline_s=0.001) for i in range(5)]
        result = ev.evaluate(make_plan(pkts), make_link_state(), make_mission_state())
        assert 0.0 <= result.risk_score <= 1.0

    def test_risk_level_matches_risk_score(self):
        ev = PlanEvaluator()
        pkts = [make_packet(f"p{i}") for i in range(3)]
        result = ev.evaluate(make_plan(pkts), make_link_state(), make_mission_state())
        expected_level = _risk_level_from_score(result.risk_score)
        assert result.risk_level == expected_level

    def test_custom_risk_weights_accepted(self):
        rw = RiskWeights(w_deadline_miss=0.5, w_critical_deficit=0.3, w_window_pressure=0.2)
        ev = PlanEvaluator(risk_weights=rw)
        result = ev.evaluate(make_plan([make_packet("p")]), make_link_state(), make_mission_state())
        assert isinstance(result, EvaluationResult)
