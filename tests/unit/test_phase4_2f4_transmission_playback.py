"""
Phase 4.2F4 — Transmission Playback Tests

Verifies:
- Simulation result has attempt_events (Phase 4.2B feature consumed by frontend)
- All delivered packets have at least one success attempt event
- Deferred packets have no attempt events
- Retransmissions create multiple attempt events
- Same-seed invariant preserved
- propagation_delay_s is separate from elapsed_time_s
"""
from __future__ import annotations

import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[2]
_ASTERIA_SCENARIO = str(_PROJECT_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")


@pytest.fixture(autouse=True)
def reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


def _run_simulation_for_ids(product_ids: list[str]):
    """Run simulation for given product IDs. Returns SimulationResult."""
    from backend.app import state as app_state
    from backend.app.api.routes_approve import approve_custom_plan, ApproveCustomRequest
    from backend.app.models.candidate_plan import CandidatePlan
    from backend.app.models.packet import Packet

    scenario = app_state.active_scenario
    valid_ids = {dp.product_id for dp in scenario.data_products}
    packets = [
        Packet(
            packet_id=pid,
            packet_type="telemetry",
            size_bits=next(
                (dp.size_bits for dp in scenario.data_products if dp.product_id == pid),
                1_000_000
            ),
            criticality=0.5,
            mission_relevance=0.5,
            deadline_s=3600.0,
            retry_cost=0.1,
            delivery_requirement="optional",
        )
        for pid in product_ids
        if pid in valid_ids
    ]
    if not packets:
        return None
    plan = CandidatePlan(
        plan_id="f4-test",
        strategy="operator-manual",
        packets=packets,
        generated_by="test",
        metadata={},
    )
    req = ApproveCustomRequest(plan=plan, operator_notes="f4 test")
    result = approve_custom_plan(req)
    return result.simulation_result


class TestAttemptEvents:

    def test_simulation_result_has_attempt_events(self):
        """Simulation result must include attempt_events (Phase 4.2B feature)."""
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:5]]
        sim = _run_simulation_for_ids(product_ids)
        assert sim is not None
        assert sim.attempt_events is not None
        assert isinstance(sim.attempt_events, list)

    def test_attempt_events_match_delivered_packets(self):
        """All delivered_packets must have at least one success attempt event."""
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:8]]
        sim = _run_simulation_for_ids(product_ids)
        assert sim is not None

        delivered_set = set(sim.delivered_packets)
        success_ids = {e.packet_id for e in sim.attempt_events if e.status == "success"}
        for pid in delivered_set:
            assert pid in success_ids, f"Delivered packet {pid} has no success attempt event"

    def test_deferred_packets_have_no_attempt_events(self):
        """Deferred packets must not appear in attempt_events."""
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        # Use many products to force deferrals
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:20]]
        sim = _run_simulation_for_ids(product_ids)
        assert sim is not None
        if not sim.deferred_packets:
            pytest.skip("No deferrals in this test run")

        deferred_set = set(sim.deferred_packets)
        attempt_ids = {e.packet_id for e in sim.attempt_events}
        for pid in deferred_set:
            assert pid not in attempt_ids, (
                f"Deferred packet {pid} appeared in attempt_events"
            )

    def test_retransmission_creates_multiple_events(self):
        """If retransmission_counts > 0 for a packet, it must have > 1 attempt events."""
        from backend.app import state as app_state
        app_state.load_scenario(_ASTERIA_SCENARIO)
        product_ids = [dp.product_id for dp in app_state.active_scenario.data_products[:15]]
        sim = _run_simulation_for_ids(product_ids)
        assert sim is not None

        retransmitted = {pid for pid, count in sim.retransmission_counts.items() if count > 0}
        if not retransmitted:
            pytest.skip("No retransmissions in this test run (deterministic seed may have no failures)")
        for pid in retransmitted:
            events_for_packet = [e for e in sim.attempt_events if e.packet_id == pid]
            assert len(events_for_packet) > 1, (
                f"Packet {pid} has retransmissions but only {len(events_for_packet)} attempt events"
            )


class TestSimulatorSeedInvariant:

    def test_same_seed_produces_identical_results(self):
        """Same RNG seed must produce byte-identical simulation results."""
        from backend.app import state as app_state
        from backend.app.simulation.transmission_sim import TransmissionSimulator
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario = app_state.active_scenario
        link_state = app_state.active_link_state
        mission_state = scenario.mission_state

        products = scenario.data_products[:5]
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
            for dp in products
        ]
        plan = CandidatePlan(
            plan_id="seed-test",
            strategy="test",
            packets=packets,
            generated_by="test",
            metadata={},
        )
        sim = TransmissionSimulator()
        result_a = sim.simulate(plan, link_state, mission_state, seed=42)
        result_b = sim.simulate(plan, link_state, mission_state, seed=42)

        assert result_a.delivered_packets == result_b.delivered_packets
        assert result_a.failed_packets == result_b.failed_packets
        assert result_a.deferred_packets == result_b.deferred_packets
        assert result_a.elapsed_time_s == result_b.elapsed_time_s

        if result_a.attempt_events and result_b.attempt_events:
            assert len(result_a.attempt_events) == len(result_b.attempt_events)
            for ea, eb in zip(result_a.attempt_events, result_b.attempt_events):
                assert ea.packet_id == eb.packet_id
                assert ea.status == eb.status
                assert ea.attempt_number == eb.attempt_number


class TestPropagationDelayIsSeparate:

    def test_propagation_delay_not_added_to_elapsed_time(self):
        """
        propagation_delay_s (608s for ASTERIA) must NOT be added to elapsed_time_s.
        Transmitting 5 products takes seconds, not 600+ seconds.
        """
        from backend.app import state as app_state
        from backend.app.telecom.geometry import compute_propagation_delay

        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario = app_state.active_scenario
        prop_delay = compute_propagation_delay(scenario.distance_km) if scenario.distance_km else None
        if prop_delay is None:
            pytest.skip("No distance_km in scenario")

        product_ids = [dp.product_id for dp in scenario.data_products[:5]]
        sim = _run_simulation_for_ids(product_ids)
        assert sim is not None

        assert sim.elapsed_time_s < prop_delay, (
            f"elapsed_time_s={sim.elapsed_time_s:.1f}s should be less than "
            f"propagation_delay_s={prop_delay:.1f}s"
        )
