"""Phase 3 tests — BaselineScheduler unit tests.

Covers:
- empty queue
- plan metadata (strategy, generated_by, plan_id)
- determinism (same inputs → same output)
- input packets not mutated
- ordering: criticality, deadline urgency
- tie-breaking by packet_id
- configurable weights affect ordering
- five-factor score behaviour
- cost_efficiency normalization (dimensionless, window-based)
- math.inf expected cost handled gracefully
- packet priority field never created
"""

import math
from datetime import datetime, timezone

import pytest

from backend.app.config import GCSIConfig, SchedulerWeights
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel
from backend.app.scheduler.baseline import (
    BaselineScheduler,
    _cost_efficiency,
    _deadline_urgency,
    _delivery_reliability,
)

NOW = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_link_state(**overrides) -> LinkState:
    base = dict(
        timestamp=NOW,
        snr_db=10.0,
        eb_n0_db=20.0,
        ber=3.87e-6,
        rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0,
        link_goodput_bps=90_000.0,
        latency_s=0.25,
        link_stability=0.95,
        remaining_window_s=300.0,
    )
    base.update(overrides)
    return LinkState(**base)


def make_mission_state(**overrides) -> MissionState:
    base = dict(
        mission_id="m-001",
        mission_phase="science",
        current_event="downlink",
        event_time_remaining_s=300.0,
        comm_window_remaining_s=300.0,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )
    base.update(overrides)
    return MissionState(**base)


def make_packet(**overrides) -> Packet:
    base = dict(
        packet_id="pkt-001",
        packet_type="telemetry",
        size_bits=8192,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=200.0,
        retry_cost=0.5,
        delivery_requirement="required",
    )
    base.update(overrides)
    return Packet(**base)


DEFAULT_WEIGHTS = SchedulerWeights()


# ---------------------------------------------------------------------------
# Empty queue
# ---------------------------------------------------------------------------

class TestBaselineSchedulerEmpty:
    def test_empty_packets_returns_empty_plan(self):
        plan = BaselineScheduler.rank([], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert isinstance(plan, CandidatePlan)
        assert plan.packets == []
        assert plan.strategy == "baseline"
        assert plan.generated_by == "BaselineScheduler"

    def test_empty_plan_id_is_baseline(self):
        plan = BaselineScheduler.rank([], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.plan_id == "baseline"


# ---------------------------------------------------------------------------
# Plan metadata
# ---------------------------------------------------------------------------

class TestBaselineSchedulerMetadata:
    def test_strategy_field_is_baseline(self):
        plan = BaselineScheduler.rank([make_packet()], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.strategy == "baseline"

    def test_plan_id_is_deterministic_and_stable(self):
        """plan_id must be the constant string 'baseline', not a UUID."""
        plan = BaselineScheduler.rank([make_packet()], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.plan_id == "baseline"

    def test_generated_by_field(self):
        plan = BaselineScheduler.rank([make_packet()], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.generated_by == "BaselineScheduler"

    def test_packet_priority_field_never_created(self):
        """Packet must not have a priority attribute after ranking."""
        packets = [make_packet(packet_id="p1"), make_packet(packet_id="p2")]
        plan = BaselineScheduler.rank(packets, make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        for pkt in plan.packets:
            assert not hasattr(pkt, "priority"), "Packet must not have a priority field"
        assert "priority" not in Packet.model_fields


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestBaselineSchedulerDeterminism:
    def test_same_inputs_same_output(self):
        packets = [
            make_packet(packet_id="a", criticality=0.9),
            make_packet(packet_id="b", criticality=0.6),
            make_packet(packet_id="c", criticality=0.3),
        ]
        ls, ms = make_link_state(), make_mission_state()
        plan1 = BaselineScheduler.rank(packets, ls, ms, DEFAULT_WEIGHTS)
        plan2 = BaselineScheduler.rank(packets, ls, ms, DEFAULT_WEIGHTS)
        assert [p.packet_id for p in plan1.packets] == [p.packet_id for p in plan2.packets]

    def test_plan_id_stable_across_calls(self):
        packets = [make_packet(packet_id="x")]
        ls, ms = make_link_state(), make_mission_state()
        p1 = BaselineScheduler.rank(packets, ls, ms, DEFAULT_WEIGHTS)
        p2 = BaselineScheduler.rank(packets, ls, ms, DEFAULT_WEIGHTS)
        assert p1.plan_id == p2.plan_id == "baseline"

    def test_input_packets_not_mutated(self):
        packets = [make_packet(packet_id="z"), make_packet(packet_id="a", criticality=0.9)]
        original_order = [p.packet_id for p in packets]
        BaselineScheduler.rank(packets, make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert [p.packet_id for p in packets] == original_order


# ---------------------------------------------------------------------------
# Factor: deadline urgency
# ---------------------------------------------------------------------------

class TestDeadlineUrgencyFactor:
    def test_urgency_zero_when_deadline_equals_window(self):
        ms = make_mission_state(comm_window_remaining_s=300.0)
        pkt = make_packet(deadline_s=300.0)
        assert math.isclose(_deadline_urgency(pkt, ms), 0.0, abs_tol=1e-9)

    def test_urgency_one_when_deadline_is_zero(self):
        ms = make_mission_state(comm_window_remaining_s=300.0)
        pkt = make_packet(deadline_s=0.0)
        assert math.isclose(_deadline_urgency(pkt, ms), 1.0, abs_tol=1e-9)

    def test_urgency_half_when_deadline_is_half_window(self):
        ms = make_mission_state(comm_window_remaining_s=200.0)
        pkt = make_packet(deadline_s=100.0)
        assert math.isclose(_deadline_urgency(pkt, ms), 0.5, abs_tol=1e-9)

    def test_urgency_one_when_window_is_zero(self):
        ms = make_mission_state(comm_window_remaining_s=0.0)
        pkt = make_packet(deadline_s=100.0)
        assert _deadline_urgency(pkt, ms) == 1.0


# ---------------------------------------------------------------------------
# Factor: delivery reliability
# ---------------------------------------------------------------------------

class TestDeliveryReliabilityFactor:
    def test_near_one_on_low_ber_small_packet(self):
        ls = make_link_state(ber=3.87e-6)
        pkt = make_packet(size_bits=1024)
        r = _delivery_reliability(pkt, ls)
        assert 0.99 < r <= 1.0

    def test_zero_on_ber_one(self):
        ls = make_link_state(ber=1.0)
        pkt = make_packet(size_bits=1024)
        assert _delivery_reliability(pkt, ls) == 0.0

    def test_is_packet_success_probability(self):
        """delivery_reliability must equal packet_success_probability exactly."""
        from backend.app.telecom.formulas import packet_success_probability
        ls = make_link_state(ber=0.001)
        pkt = make_packet(size_bits=4096)
        assert math.isclose(
            _delivery_reliability(pkt, ls),
            packet_success_probability(ls.ber, pkt.size_bits),
            rel_tol=1e-12,
        )


# ---------------------------------------------------------------------------
# Factor: cost efficiency
# ---------------------------------------------------------------------------

class TestCostEfficiencyFactor:
    def test_small_packet_high_efficiency(self):
        """A tiny packet relative to the window should have high cost efficiency."""
        ls = make_link_state(link_goodput_bps=90_000.0)
        ms = make_mission_state(comm_window_remaining_s=300.0)
        # 900 bits / 90000 bps = 0.01 s; p_success ≈ 1; cost ≈ 0.01 s
        # cost_pressure = 0.01 / 300 ≈ 0.000033; efficiency ≈ 0.9999
        pkt = make_packet(size_bits=900, packet_id="small")
        eff = _cost_efficiency(pkt, ls, ms)
        assert eff > 0.99

    def test_zero_when_cost_is_inf(self):
        """Infinite cost (undeliverable packet) → efficiency = 0."""
        ls = make_link_state(ber=1.0)   # BER=1 → p_success=0 → cost=inf
        ms = make_mission_state(comm_window_remaining_s=300.0)
        pkt = make_packet(size_bits=8192)
        assert _cost_efficiency(pkt, ls, ms) == 0.0

    def test_zero_when_window_is_zero(self):
        ls = make_link_state()
        ms = make_mission_state(comm_window_remaining_s=0.0)
        pkt = make_packet(size_bits=8192)
        assert _cost_efficiency(pkt, ls, ms) == 0.0

    def test_dimensionless_normalization(self):
        """cost_efficiency = 1 - min(expected_cost / comm_window, 1.0).

        Verify with a packet whose expected_cost equals exactly half the window.
        expected_cost = tx_time / p_success
        We want: expected_cost ≈ 150 s (half of 300 s window)
        Use a small packet so p_success ≈ 1.0 and tx_time ≈ expected_cost.
        With ber=1e-9 and size_bits=10_000, p_success ≈ exp(-1e-5) ≈ 0.99999.
        goodput = size_bits / tx_time = 10_000 / 150 ≈ 66.67 bps
        """
        ls = make_link_state(ber=1e-9, link_goodput_bps=10_000 / 150.0)
        ms = make_mission_state(comm_window_remaining_s=300.0)
        pkt = make_packet(size_bits=10_000)
        eff = _cost_efficiency(pkt, ls, ms)
        # cost_pressure ≈ 0.5 → efficiency ≈ 0.5
        assert 0.45 < eff < 0.55, f"Expected ~0.5, got {eff}"

    def test_clamped_at_zero_when_cost_exceeds_window(self):
        """When expected_cost > comm_window, cost_pressure=1.0, efficiency=0."""
        ls = make_link_state(link_goodput_bps=90_000.0)
        ms = make_mission_state(comm_window_remaining_s=1.0)
        # Large packet: tx_time = 1_000_000 / 90000 ≈ 11 s >> 1 s window
        pkt = make_packet(size_bits=1_000_000)
        eff = _cost_efficiency(pkt, ls, ms)
        assert eff == 0.0


# ---------------------------------------------------------------------------
# Ordering: factor dominance tests
# ---------------------------------------------------------------------------

class TestBaselineSchedulerOrdering:
    def test_higher_criticality_ranks_first_when_other_factors_equal(self):
        low = make_packet(packet_id="low", criticality=0.2, mission_relevance=0.5,
                          deadline_s=200.0, size_bits=1024)
        high = make_packet(packet_id="high", criticality=0.9, mission_relevance=0.5,
                           deadline_s=200.0, size_bits=1024)
        plan = BaselineScheduler.rank([low, high], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.packets[0].packet_id == "high"

    def test_earlier_deadline_increases_rank(self):
        """Same criticality and size: nearer deadline → higher urgency → higher score."""
        near = make_packet(packet_id="near", criticality=0.5, deadline_s=10.0, size_bits=1024)
        far = make_packet(packet_id="far", criticality=0.5, deadline_s=290.0, size_bits=1024)
        plan = BaselineScheduler.rank([far, near], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert plan.packets[0].packet_id == "near"

    def test_all_packets_present_in_output(self):
        packets = [make_packet(packet_id=f"p{i}") for i in range(5)]
        plan = BaselineScheduler.rank(packets, make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert len(plan.packets) == 5
        assert {p.packet_id for p in plan.packets} == {p.packet_id for p in packets}

    def test_high_reliability_ranks_higher_than_low(self):
        """With high w_delivery_reliability, a more reliable packet wins over identical others."""
        weights = SchedulerWeights(
            w_criticality=0.01,
            w_deadline_urgency=0.01,
            w_mission_relevance=0.01,
            w_delivery_reliability=0.95,
            w_cost_efficiency=0.01,
        )
        # low_ber: near-certain delivery; high_ber: near-certain failure
        low_ber_link = make_link_state(ber=1e-9)
        high_ber_link = make_link_state(ber=0.9)

        pkt_reliable = make_packet(packet_id="reliable", size_bits=1024, criticality=0.5)
        pkt_unreliable = make_packet(packet_id="unreliable", size_bits=1024, criticality=0.5)

        # We can't change BER per-packet, but we CAN use size to differentiate p_success
        # on the same link: small packet → high p_success; large packet → low p_success
        low_ber_link = make_link_state(ber=0.001)
        pkt_small = make_packet(packet_id="small-reliable", size_bits=8, criticality=0.5)
        pkt_large = make_packet(packet_id="large-unreliable", size_bits=500_000, criticality=0.5)

        plan = BaselineScheduler.rank([pkt_large, pkt_small], low_ber_link, make_mission_state(), weights)
        assert plan.packets[0].packet_id == "small-reliable"


# ---------------------------------------------------------------------------
# Tie-breaking
# ---------------------------------------------------------------------------

class TestBaselineSchedulerTieBreaking:
    def test_identical_scores_break_by_packet_id_lexicographic(self):
        pkts = [
            make_packet(packet_id="pkt-c", criticality=0.5, mission_relevance=0.5,
                        deadline_s=150.0, size_bits=4096),
            make_packet(packet_id="pkt-a", criticality=0.5, mission_relevance=0.5,
                        deadline_s=150.0, size_bits=4096),
            make_packet(packet_id="pkt-b", criticality=0.5, mission_relevance=0.5,
                        deadline_s=150.0, size_bits=4096),
        ]
        plan = BaselineScheduler.rank(pkts, make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        ids = [p.packet_id for p in plan.packets]
        assert ids == sorted(ids), f"Expected lexicographic order, got {ids}"


# ---------------------------------------------------------------------------
# Configurable weights
# ---------------------------------------------------------------------------

class TestBaselineSchedulerWeights:
    def test_high_criticality_weight_promotes_critical_packet(self):
        low_crit = make_packet(packet_id="low", criticality=0.1, deadline_s=5.0, size_bits=1024)
        high_crit = make_packet(packet_id="high", criticality=0.99, deadline_s=290.0, size_bits=1024)
        plan = BaselineScheduler.rank(
            [low_crit, high_crit], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS
        )
        assert plan.packets[0].packet_id == "high"

    def test_weight_fields_exist_on_scheduler_weights(self):
        """SchedulerWeights must expose exactly the five new factor weight fields."""
        w = SchedulerWeights()
        assert hasattr(w, "w_criticality")
        assert hasattr(w, "w_deadline_urgency")
        assert hasattr(w, "w_mission_relevance")
        assert hasattr(w, "w_delivery_reliability")
        assert hasattr(w, "w_cost_efficiency")
        # Old fields must not exist
        assert not hasattr(w, "w_efficiency")
        assert not hasattr(w, "w_cost")
        assert not hasattr(w, "w_risk")

    def test_all_weight_defaults_are_positive(self):
        w = SchedulerWeights()
        assert w.w_criticality > 0
        assert w.w_deadline_urgency > 0
        assert w.w_mission_relevance > 0
        assert w.w_delivery_reliability > 0
        assert w.w_cost_efficiency > 0

    def test_custom_weights_accepted(self):
        w = SchedulerWeights(
            w_criticality=0.40,
            w_deadline_urgency=0.30,
            w_mission_relevance=0.15,
            w_delivery_reliability=0.10,
            w_cost_efficiency=0.05,
        )
        pkt = make_packet()
        plan = BaselineScheduler.rank([pkt], make_link_state(), make_mission_state(), w)
        assert len(plan.packets) == 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestBaselineSchedulerEdgeCases:
    def test_expired_deadline_packet_does_not_crash(self):
        expired = make_packet(packet_id="expired", deadline_s=0.0)
        fresh = make_packet(packet_id="fresh", deadline_s=200.0)
        plan = BaselineScheduler.rank(
            [expired, fresh], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS
        )
        assert len(plan.packets) == 2

    def test_infinite_expected_cost_does_not_crash(self):
        """A packet with p_success=0 (cost=inf) → cost_efficiency=0, scheduler still runs."""
        bad_link = make_link_state(ber=1.0)   # BER=1 → all p_success=0 → all cost=inf
        large_pkt = make_packet(packet_id="inf-cost", size_bits=1_000_000)
        normal_pkt = make_packet(packet_id="normal", size_bits=100)
        plan = BaselineScheduler.rank(
            [large_pkt, normal_pkt], bad_link, make_mission_state(), DEFAULT_WEIGHTS
        )
        assert len(plan.packets) == 2

    def test_infinite_cost_produces_zero_cost_efficiency(self):
        """Packets with infinite cost must get cost_efficiency=0, not an exception."""
        ls = make_link_state(ber=1.0)
        ms = make_mission_state(comm_window_remaining_s=300.0)
        pkt = make_packet(size_bits=8192)
        assert _cost_efficiency(pkt, ls, ms) == 0.0

    def test_single_packet_returns_single_item_plan(self):
        plan = BaselineScheduler.rank([make_packet()], make_link_state(), make_mission_state(), DEFAULT_WEIGHTS)
        assert len(plan.packets) == 1
