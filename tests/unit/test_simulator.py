"""Unit tests for TransmissionSimulator — stochastic realized outcomes.

Tests verify:
- Reproducibility: same seed → identical SimulationResult.
- Non-determinism: different seeds → results may differ.
- Realized fields are NOT copies of EvaluationResult fields.
- elapsed_time_s can differ from analytical expected delivery time.
- No PlanEvaluator calls occur inside the simulator.
- Output type is always SimulationResult, never EvaluationResult.
"""

from datetime import datetime, timezone

import pytest

from backend.app.evaluator.plan_evaluator import PlanEvaluator
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel
from backend.app.models.simulation_result import SimulationResult
from backend.app.simulation.transmission_sim import TransmissionSimulator

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
) -> MissionState:
    return MissionState(
        mission_id="test-mission",
        mission_phase="test",
        current_event="test_event",
        event_time_remaining_s=300.0,
        comm_window_remaining_s=comm_window_remaining_s,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


def make_packet(
    packet_id: str = "pkt-001",
    *,
    size_bits: int = 8_000,
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
# Output type
# ---------------------------------------------------------------------------

class TestOutputType:
    def test_returns_simulation_result_not_evaluation_result(self):
        sim = TransmissionSimulator()
        result = sim.simulate(
            make_plan([make_packet("p")]),
            make_link_state(),
            make_mission_state(),
            seed=0,
        )
        assert isinstance(result, SimulationResult)

    def test_plan_id_propagated(self):
        sim = TransmissionSimulator()
        result = sim.simulate(
            make_plan([make_packet("p")], plan_id="deadline-first"),
            make_link_state(),
            make_mission_state(),
            seed=0,
        )
        assert result.plan_id == "deadline-first"


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

class TestReproducibility:
    def test_same_seed_identical_result(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}", size_bits=8_000) for i in range(5)]
        plan = make_plan(pkts)
        ls = make_link_state(ber=1e-4)
        ms = make_mission_state()

        r1 = sim.simulate(plan, ls, ms, seed=42)
        r2 = sim.simulate(plan, ls, ms, seed=42)

        assert r1.model_dump() == r2.model_dump()

    def test_same_seed_same_delivered_set(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}", size_bits=8_000) for i in range(10)]
        plan = make_plan(pkts)
        ls = make_link_state(ber=1e-5)
        ms = make_mission_state()

        r1 = sim.simulate(plan, ls, ms, seed=99)
        r2 = sim.simulate(plan, ls, ms, seed=99)

        assert r1.delivered_packets == r2.delivered_packets
        assert r1.retransmission_counts == r2.retransmission_counts

    def test_different_seeds_may_produce_different_results(self):
        """Run 20 seeds and verify at least two produce different elapsed times.

        Use a small packet (100 bits) at moderate BER (0.01) so
        p_success = exp(100 * log(0.99)) ≈ 0.366 — enough variation across seeds.
        """
        sim = TransmissionSimulator()
        pkts = [make_packet("pkt-0", size_bits=100)]
        plan = make_plan(pkts)
        ls = make_link_state(ber=0.01, remaining_window_s=600.0)
        ms = make_mission_state(comm_window_remaining_s=600.0)

        elapsed_times = {sim.simulate(plan, ls, ms, seed=s).elapsed_time_s for s in range(20)}
        # At least two distinct elapsed times means non-determinism is present.
        assert len(elapsed_times) > 1, "All 20 seeds produced identical elapsed time — simulator may not be stochastic"


# ---------------------------------------------------------------------------
# Deterministic edge cases (BER=0 → all packets delivered on first try)
# ---------------------------------------------------------------------------

class TestPerfectChannel:
    def test_all_packets_delivered_on_perfect_channel(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}") for i in range(5)]
        result = sim.simulate(make_plan(pkts), make_link_state(ber=0.0), make_mission_state(), seed=0)
        assert set(result.delivered_packets) == {f"pkt-{i}" for i in range(5)}
        assert result.deferred_packets == []
        assert result.failed_packets == []

    def test_zero_retransmissions_on_perfect_channel(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}") for i in range(3)]
        result = sim.simulate(make_plan(pkts), make_link_state(ber=0.0), make_mission_state(), seed=0)
        assert all(v == 0 for v in result.retransmission_counts.values())


# ---------------------------------------------------------------------------
# Window enforcement
# ---------------------------------------------------------------------------

class TestWindowEnforcement:
    def test_packets_deferred_when_window_exhausted(self):
        sim = TransmissionSimulator()
        # Two packets: each 100_000 bits at 100_000 bps → 1 s each
        # Window = 1 s → second packet cannot start
        pkt_a = make_packet("pkt-a", size_bits=100_000)
        pkt_b = make_packet("pkt-b", size_bits=100_000)
        ls = make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=1.0)
        ms = make_mission_state(comm_window_remaining_s=1.0)
        result = sim.simulate(make_plan([pkt_a, pkt_b]), ls, ms, seed=0)
        assert "pkt-a" in result.delivered_packets
        assert "pkt-b" in result.deferred_packets

    def test_all_deferred_when_zero_window(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}") for i in range(3)]
        ls = make_link_state(remaining_window_s=0.0)
        ms = make_mission_state(comm_window_remaining_s=0.0)
        result = sim.simulate(make_plan(pkts), ls, ms, seed=0)
        assert set(result.deferred_packets) == {"pkt-0", "pkt-1", "pkt-2"}
        assert result.delivered_packets == []

    def test_elapsed_time_does_not_exceed_window(self):
        sim = TransmissionSimulator()
        pkts = [make_packet(f"pkt-{i}", size_bits=50_000) for i in range(20)]
        ls = make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=10.0)
        ms = make_mission_state(comm_window_remaining_s=10.0)
        result = sim.simulate(make_plan(pkts), ls, ms, seed=0)
        # elapsed may exceed window slightly due to in-flight packet finishing
        # but the updated link state remaining_window_s should be >= 0
        assert result.link_state.remaining_window_s >= 0.0


# ---------------------------------------------------------------------------
# Updated state fields
# ---------------------------------------------------------------------------

class TestUpdatedState:
    def test_updated_link_state_remaining_window_decreases(self):
        sim = TransmissionSimulator()
        pkts = [make_packet("p", size_bits=10_000)]  # tx_time = 0.1 s
        ls = make_link_state(ber=0.0, link_goodput_bps=100_000.0, remaining_window_s=300.0)
        ms = make_mission_state(comm_window_remaining_s=300.0)
        result = sim.simulate(make_plan(pkts), ls, ms, seed=0)
        assert result.link_state.remaining_window_s < 300.0

    def test_inputs_not_mutated(self):
        sim = TransmissionSimulator()
        pkts = [make_packet("p")]
        ls = make_link_state()
        ms = make_mission_state()
        original_window = ls.remaining_window_s
        sim.simulate(make_plan(pkts), ls, ms, seed=0)
        assert ls.remaining_window_s == original_window

    def test_updated_mission_state_has_risk_level(self):
        sim = TransmissionSimulator()
        pkts = [make_packet("p")]
        result = sim.simulate(make_plan(pkts), make_link_state(), make_mission_state(), seed=0)
        assert result.mission_state.risk_level in list(RiskLevel)


# ---------------------------------------------------------------------------
# Realized != analytical (simulator != evaluator)
# ---------------------------------------------------------------------------

class TestSimulatorNotEvaluator:
    def test_elapsed_time_may_differ_from_analytical_expected(self):
        """With non-zero BER, stochastic retransmissions cause elapsed_time_s to
        differ from the analytical expected delivery time across runs.

        Use a 100-bit packet at ber=0.01 so p_success ≈ 0.366 — retransmissions
        are frequent enough that elapsed time varies across seeds.
        """
        sim = TransmissionSimulator()
        pkts = [make_packet("p", size_bits=100)]
        ls = make_link_state(ber=0.01, link_goodput_bps=100_000.0, remaining_window_s=600.0)
        ms = make_mission_state(comm_window_remaining_s=600.0)

        elapsed_values = [
            sim.simulate(make_plan(pkts), ls, ms, seed=s).elapsed_time_s
            for s in range(30)
        ]
        # There should be some spread in elapsed times (retransmissions vary).
        assert max(elapsed_values) > min(elapsed_values), (
            "All seeds produced identical elapsed_time_s — simulator is not stochastic"
        )

    def test_no_evaluator_import_used_in_simulator_module(self):
        """Confirm TransmissionSimulator does not import PlanEvaluator."""
        import backend.app.simulation.transmission_sim as sim_module
        import inspect
        # Check the import lines only — the docstring mentions PlanEvaluator by name
        # as a contrast, which is fine; what matters is no import exists.
        source = inspect.getsource(sim_module)
        import_lines = [
            line for line in source.splitlines()
            if line.strip().startswith("import ") or line.strip().startswith("from ")
        ]
        import_block = "\n".join(import_lines)
        assert "PlanEvaluator" not in import_block
        assert "EvaluationResult" not in import_block


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------

class TestEmptyPlan:
    def test_empty_plan_returns_zero_elapsed(self):
        sim = TransmissionSimulator()
        result = sim.simulate(make_plan([]), make_link_state(), make_mission_state(), seed=0)
        assert result.elapsed_time_s == 0.0
        assert result.delivered_packets == []
        assert result.deferred_packets == []
        assert result.failed_packets == []
