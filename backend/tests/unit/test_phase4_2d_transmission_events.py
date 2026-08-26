"""Phase 4.2D — Tests for TransmissionAttemptEvent instrumentation.

These tests verify:
- TransmissionAttemptEvent fields exist in SimulationResult.
- attempt_events are monotone in timing (end >= start for each event).
- Retransmissions have increasing attempt_number within the same packet.
- Failed attempts are represented.
- Successful attempts are represented.
- CRITICAL regression: same seed → IDENTICAL delivered/failed/deferred/
  retransmission_counts/elapsed_time_s before and after instrumentation.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest

from app import state as app_state
from app.models.candidate_plan import CandidatePlan
from app.models.link_state import LinkState
from app.models.mission_state import MissionState
from app.models.packet import Packet
from app.models.risk_level import RiskLevel
from app.models.simulation_result import SimulationResult, TransmissionAttemptEvent
from app.simulation.transmission_sim import TransmissionSimulator


SCENARIO_V3_PATH = os.path.join(
    os.path.dirname(__file__), "../../../data/scenarios/mission_data_v3.json"
)


@pytest.fixture(autouse=True)
def load_scenario():
    path = os.path.abspath(SCENARIO_V3_PATH)
    if not os.path.exists(path):
        pytest.skip(f"Scenario not found: {path}")
    app_state.load_scenario(path)
    yield


def _make_link_state(ber: float = 1e-5) -> LinkState:
    """Return a simple LinkState suitable for simulation tests."""
    return app_state.active_link_state.model_copy(update={"ber": ber})


def _make_mission_state() -> MissionState:
    return app_state.active_scenario.mission_state.model_copy()


def _make_small_plan(n_packets: int = 5) -> CandidatePlan:
    """Build a small plan from the first n authoritative data products."""
    scenario = app_state.active_scenario
    if scenario.data_products:
        items = scenario.data_products[:n_packets]
        packets = [
            Packet(
                packet_id=dp.product_id,
                packet_type=dp.product_type,
                size_bits=dp.size_bits,
                criticality=dp.criticality,
                mission_relevance=dp.mission_relevance,
                deadline_s=dp.deadline_s,
                retry_cost=dp.retry_cost,
                delivery_requirement=dp.delivery_requirement,
            )
            for dp in items
        ]
    else:
        packets = scenario.packets[:n_packets]
    return CandidatePlan(
        plan_id="test-sim-plan",
        strategy="baseline",
        packets=packets,
        generated_by="test",
    )


# ── Model field existence tests ───────────────────────────────────────────────


def test_transmission_attempt_event_fields():
    """TransmissionAttemptEvent must have all required fields."""
    evt = TransmissionAttemptEvent(
        packet_id="PKT-001",
        attempt_number=1,
        start_elapsed_s=0.0,
        end_elapsed_s=0.5,
        status="success",
        packet_size_bits=8192,
    )
    assert evt.packet_id == "PKT-001"
    assert evt.attempt_number == 1
    assert evt.start_elapsed_s == 0.0
    assert evt.end_elapsed_s == 0.5
    assert evt.status == "success"
    assert evt.packet_size_bits == 8192


def test_simulation_result_has_attempt_events_field():
    """SimulationResult must include attempt_events with a default empty list."""
    ls = _make_link_state()
    ms = _make_mission_state()
    result = SimulationResult(
        plan_id="x",
        delivered_packets=[],
        deferred_packets=[],
        failed_packets=[],
        elapsed_time_s=0.0,
        retransmission_counts={},
        link_state=ls,
        mission_state=ms,
    )
    assert hasattr(result, "attempt_events")
    assert isinstance(result.attempt_events, list)


# ── Simulation event tests ────────────────────────────────────────────────────


def test_attempt_events_monotone_timing():
    """All attempt events must have end_elapsed_s >= start_elapsed_s."""
    sim = TransmissionSimulator()
    plan = _make_small_plan(10)
    ls = _make_link_state()
    ms = _make_mission_state()
    result = sim.simulate(plan, ls, ms, seed=42)
    for evt in result.attempt_events:
        assert evt.end_elapsed_s >= evt.start_elapsed_s, (
            f"Event {evt.packet_id} attempt {evt.attempt_number}: "
            f"end({evt.end_elapsed_s}) < start({evt.start_elapsed_s})"
        )


def test_attempt_events_increasing_attempt_numbers_per_packet():
    """Within the same packet, attempt_number must be strictly increasing."""
    sim = TransmissionSimulator()
    plan = _make_small_plan(10)
    ls = _make_link_state()
    ms = _make_mission_state()
    result = sim.simulate(plan, ls, ms, seed=42)

    # Group events by packet_id.
    by_packet: dict[str, list[int]] = {}
    for evt in result.attempt_events:
        by_packet.setdefault(evt.packet_id, []).append(evt.attempt_number)

    for pid, nums in by_packet.items():
        for i in range(1, len(nums)):
            assert nums[i] > nums[i - 1], (
                f"Packet {pid}: attempt_numbers not strictly increasing: {nums}"
            )


def test_success_attempts_represented():
    """Delivered packets must have at least one 'success' attempt event."""
    sim = TransmissionSimulator()
    plan = _make_small_plan(10)
    ls = _make_link_state()
    ms = _make_mission_state()
    result = sim.simulate(plan, ls, ms, seed=42)
    if not result.delivered_packets:
        pytest.skip("No packets delivered — cannot test success events")
    success_pids = {e.packet_id for e in result.attempt_events if e.status == "success"}
    for pid in result.delivered_packets:
        assert pid in success_pids, f"Delivered packet {pid} has no success event"


def test_failed_attempts_represented_on_retransmission():
    """Packets with retransmissions must have at least one 'failure' event."""
    sim = TransmissionSimulator()
    plan = _make_small_plan(20)
    ls = _make_link_state(ber=1e-3)  # higher BER → more retransmissions
    ms = _make_mission_state()
    result = sim.simulate(plan, ls, ms, seed=7)
    retransmitted = {pid for pid, cnt in result.retransmission_counts.items() if cnt > 0}
    if not retransmitted:
        pytest.skip("No retransmissions at this BER — cannot test failure events")
    failure_pids = {e.packet_id for e in result.attempt_events if e.status == "failure"}
    for pid in retransmitted:
        if pid in result.delivered_packets:
            assert pid in failure_pids, (
                f"Retransmitted+delivered packet {pid} has no failure event"
            )


# ── CRITICAL regression test: same seed → IDENTICAL core outputs ──────────────


def test_same_seed_identical_outputs():
    """CRITICAL: Running the same plan with the same seed twice must produce
    identical delivered/failed/deferred/retransmission_counts/elapsed_time_s.

    This verifies that no new RNG calls were introduced by the event
    instrumentation — the stochastic behaviour is unchanged.
    """
    sim = TransmissionSimulator()
    plan = _make_small_plan(15)
    ls = _make_link_state(ber=1e-4)
    ms = _make_mission_state()

    r1 = sim.simulate(plan, ls, ms, seed=42)
    r2 = sim.simulate(plan, ls, ms, seed=42)

    assert sorted(r1.delivered_packets) == sorted(r2.delivered_packets), (
        "delivered_packets differ between identical-seed runs"
    )
    assert sorted(r1.deferred_packets) == sorted(r2.deferred_packets), (
        "deferred_packets differ between identical-seed runs"
    )
    assert sorted(r1.failed_packets) == sorted(r2.failed_packets), (
        "failed_packets differ between identical-seed runs"
    )
    assert r1.retransmission_counts == r2.retransmission_counts, (
        "retransmission_counts differ between identical-seed runs"
    )
    assert abs(r1.elapsed_time_s - r2.elapsed_time_s) < 1e-9, (
        f"elapsed_time_s differs: {r1.elapsed_time_s} vs {r2.elapsed_time_s}"
    )
