"""Unit tests for backend.app.models.bridge.

Verifies that data_product_to_packet and data_products_to_packets produce
correctly-mapped Packet objects from DataProduct inputs, and that the bridge
is used transparently in the scheduling pipeline for v2 scenarios.
"""

from __future__ import annotations

import pytest

from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.bridge import data_product_to_packet, data_products_to_packets
from backend.app.models.data_product import DataProduct
from backend.app.models.packet import Packet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_data_product(**overrides) -> DataProduct:
    base = dict(
        product_id="DIAG-PROP-001",
        product_type="diagnostic",
        subsystem="propulsion",
        size_bits=8192,
        criticality=0.97,
        mission_relevance=1.0,
        scientific_value=0.0,
        deadline_s=90.0,
        age_s=180.0,
        anomaly_id="ANOM-017",
        experiment_id=None,
        related_ids=["DIAG-PROP-002"],
        delivery_requirement="required",
        retry_cost=0.95,
    )
    base.update(overrides)
    return DataProduct(**base)


# ===========================================================================
# data_product_to_packet — field mapping
# ===========================================================================


class TestDataProductToPacket:
    def test_returns_packet_instance(self):
        dp = _make_data_product()
        result = data_product_to_packet(dp)
        assert isinstance(result, Packet)

    def test_product_id_maps_to_packet_id(self):
        dp = _make_data_product(product_id="TEL-PROP-001")
        pkt = data_product_to_packet(dp)
        assert pkt.packet_id == "TEL-PROP-001"

    def test_product_type_maps_to_packet_type(self):
        dp = _make_data_product(product_type="telemetry")
        pkt = data_product_to_packet(dp)
        assert pkt.packet_type == "telemetry"

    def test_size_bits_preserved(self):
        dp = _make_data_product(size_bits=16384)
        pkt = data_product_to_packet(dp)
        assert pkt.size_bits == 16384

    def test_criticality_preserved(self):
        dp = _make_data_product(criticality=0.75)
        pkt = data_product_to_packet(dp)
        assert pkt.criticality == pytest.approx(0.75)

    def test_mission_relevance_preserved(self):
        dp = _make_data_product(mission_relevance=0.6)
        pkt = data_product_to_packet(dp)
        assert pkt.mission_relevance == pytest.approx(0.6)

    def test_deadline_s_preserved(self):
        dp = _make_data_product(deadline_s=200.0)
        pkt = data_product_to_packet(dp)
        assert pkt.deadline_s == pytest.approx(200.0)

    def test_retry_cost_preserved(self):
        dp = _make_data_product(retry_cost=0.42)
        pkt = data_product_to_packet(dp)
        assert pkt.retry_cost == pytest.approx(0.42)

    def test_delivery_requirement_preserved(self):
        dp = _make_data_product(delivery_requirement="best_effort")
        pkt = data_product_to_packet(dp)
        assert pkt.delivery_requirement == "best_effort"

    def test_dp_only_fields_not_on_packet(self):
        """Fields exclusive to DataProduct must not appear on the Packet output."""
        dp = _make_data_product()
        pkt = data_product_to_packet(dp)
        assert not hasattr(pkt, "scientific_value")
        assert not hasattr(pkt, "age_s")
        assert not hasattr(pkt, "anomaly_id")
        assert not hasattr(pkt, "experiment_id")
        assert not hasattr(pkt, "related_ids")
        assert not hasattr(pkt, "subsystem")
        assert not hasattr(pkt, "product_id")
        assert not hasattr(pkt, "product_type")

    def test_packet_has_no_priority_field(self):
        """The resulting Packet must not carry a priority field."""
        dp = _make_data_product()
        pkt = data_product_to_packet(dp)
        assert "priority" not in Packet.model_fields
        assert not hasattr(pkt, "priority")

    def test_bridge_is_pure_does_not_mutate_input(self):
        """The bridge must not mutate the DataProduct."""
        dp = _make_data_product(criticality=0.8)
        _ = data_product_to_packet(dp)
        assert dp.criticality == pytest.approx(0.8)

    def test_boundary_criticality_zero(self):
        dp = _make_data_product(criticality=0.0)
        pkt = data_product_to_packet(dp)
        assert pkt.criticality == pytest.approx(0.0)

    def test_boundary_criticality_one(self):
        dp = _make_data_product(criticality=1.0)
        pkt = data_product_to_packet(dp)
        assert pkt.criticality == pytest.approx(1.0)

    def test_boundary_deadline_zero(self):
        dp = _make_data_product(deadline_s=0.0)
        pkt = data_product_to_packet(dp)
        assert pkt.deadline_s == pytest.approx(0.0)

    def test_dp_with_no_anomaly_id_still_bridges(self):
        dp = _make_data_product(anomaly_id=None)
        pkt = data_product_to_packet(dp)
        assert pkt.packet_id == dp.product_id

    def test_dp_with_experiment_id_still_bridges(self):
        dp = _make_data_product(experiment_id="EXP-MARS-001")
        pkt = data_product_to_packet(dp)
        assert isinstance(pkt, Packet)


# ===========================================================================
# data_products_to_packets — list conversion
# ===========================================================================


class TestDataProductsToPackets:
    def test_empty_list_returns_empty(self):
        result = data_products_to_packets([])
        assert result == []

    def test_single_product_returns_single_packet(self):
        dp = _make_data_product()
        result = data_products_to_packets([dp])
        assert len(result) == 1
        assert isinstance(result[0], Packet)

    def test_order_preserved(self):
        products = [
            _make_data_product(product_id=f"PROD-{i:03d}", size_bits=1024 * (i + 1))
            for i in range(5)
        ]
        packets = data_products_to_packets(products)
        assert len(packets) == 5
        for i, pkt in enumerate(packets):
            assert pkt.packet_id == f"PROD-{i:03d}"

    def test_all_items_are_packet_instances(self):
        products = [_make_data_product(product_id=f"P-{i}") for i in range(3)]
        packets = data_products_to_packets(products)
        for pkt in packets:
            assert isinstance(pkt, Packet)

    def test_does_not_mutate_input_list(self):
        dp = _make_data_product()
        original = [dp]
        _ = data_products_to_packets(original)
        assert len(original) == 1
        assert original[0] is dp

    def test_independent_packets(self):
        """Each bridged Packet is a distinct object."""
        dp = _make_data_product()
        packets = data_products_to_packets([dp, dp])
        assert packets[0] is not packets[1]


# ===========================================================================
# Bridge integration — DataProduct through scheduling pipeline
# ===========================================================================


class TestBridgeWithSchedulingPipeline:
    """Verify that bridged Packets flow correctly through the evaluator."""

    def _make_link_state(self):
        from datetime import datetime, timezone
        from backend.app.models.link_state import LinkState
        return LinkState(
            timestamp=datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
            snr_db=10.0,
            eb_n0_db=20.0,
            ber=3.87e-6,
            rssi_dbm=-80.0,
            nominal_data_rate_bps=100_000.0,
            link_goodput_bps=90_000.0,
            latency_s=0.25,
            link_stability=0.95,
            remaining_window_s=600.0,
        )

    def _make_mission_state(self):
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        return MissionState(
            mission_id="m-001",
            mission_phase="science",
            current_event="downlink",
            event_time_remaining_s=600.0,
            comm_window_remaining_s=600.0,
            risk_score=0.3,
            risk_level=RiskLevel.LOW,
        )

    def test_bridged_packet_evaluates_without_error(self):
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.models.bridge import data_products_to_packets
        from backend.app.models.candidate_plan import CandidatePlan

        dp = _make_data_product(
            product_id="TEL-001",
            size_bits=4096,
            criticality=0.9,
            mission_relevance=0.85,
            deadline_s=300.0,
        )
        packets = data_products_to_packets([dp])
        plan = CandidatePlan(
            plan_id="test-plan",
            strategy="baseline",
            packets=packets,
            generated_by="test",
        )

        ev = PlanEvaluator()
        result = ev.evaluate(plan, self._make_link_state(), self._make_mission_state())

        assert result.plan_id == "test-plan"
        assert result.mission_value >= 0.0
        assert 0.0 <= result.risk_score <= 1.0

    def test_bridged_packets_schedule_by_criticality(self):
        from backend.app.config import SchedulerWeights
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.models.bridge import data_products_to_packets

        # High criticality product should appear first in mission_critical_first
        dp_high = _make_data_product(
            product_id="HIGH-001",
            criticality=0.95,
            mission_relevance=0.9,
            deadline_s=400.0,
        )
        dp_low = _make_data_product(
            product_id="LOW-001",
            criticality=0.1,
            mission_relevance=0.2,
            deadline_s=400.0,
        )

        packets = data_products_to_packets([dp_low, dp_high])  # deliberately reversed
        plans = CandidateGenerator.generate(
            packets,
            self._make_link_state(),
            self._make_mission_state(),
            SchedulerWeights(),
        )

        mc_plan = next(p for p in plans if p.strategy == "mission_critical_first")
        assert mc_plan.packets[0].packet_id == "HIGH-001"
        assert mc_plan.packets[-1].packet_id == "LOW-001"

    def test_bridged_deadline_ordering(self):
        from backend.app.config import SchedulerWeights
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.models.bridge import data_products_to_packets

        dp_urgent = _make_data_product(
            product_id="URGENT-001",
            criticality=0.5,
            deadline_s=30.0,
        )
        dp_late = _make_data_product(
            product_id="LATE-001",
            criticality=0.5,
            deadline_s=500.0,
        )

        packets = data_products_to_packets([dp_late, dp_urgent])
        plans = CandidateGenerator.generate(
            packets,
            self._make_link_state(),
            self._make_mission_state(),
            SchedulerWeights(),
        )

        df_plan = next(p for p in plans if p.strategy == "deadline_first")
        assert df_plan.packets[0].packet_id == "URGENT-001"
        assert df_plan.packets[-1].packet_id == "LATE-001"
