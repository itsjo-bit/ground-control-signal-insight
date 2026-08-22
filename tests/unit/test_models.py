"""Phase 1 tests — domain models and configuration.

Tests cover:
- valid model construction
- required field enforcement
- constraint validation (risk_score, confidence, criticality, mission_relevance,
  protocol_efficiency, ber, link_stability, bandwidth_utilization)
- Packet has no priority field
- EvidenceItem field contract
- AIRecommendation field contract
- Scenario simulated flag
- Config defaults and protocol_efficiency bounds
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.app.config import GCSIConfig, SchedulerWeights, RiskWeights, TelecomConfig
from backend.app.models import (
    AIRecommendation,
    CandidatePlan,
    EvaluationResult,
    EvidenceItem,
    LinkState,
    MissionState,
    Packet,
    RiskLevel,
    Scenario,
    SimulationResult,
)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def make_link_state(**overrides) -> dict:
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
    return base


def make_mission_state(**overrides) -> dict:
    base = dict(
        mission_id="GCSI-001",
        mission_phase="science",
        current_event="downlink_pass",
        event_time_remaining_s=600.0,
        comm_window_remaining_s=300.0,
        risk_score=0.2,
        risk_level=RiskLevel.LOW,
    )
    base.update(overrides)
    return base


def make_packet(**overrides) -> dict:
    base = dict(
        packet_id="pkt-001",
        packet_type="telemetry",
        size_bits=8192,
        criticality=0.9,
        mission_relevance=0.8,
        deadline_s=120.0,
        retry_cost=0.5,
        delivery_requirement="required",
    )
    base.update(overrides)
    return base


def make_evaluation_result(**overrides) -> dict:
    base = dict(
        plan_id="plan-001",
        mission_value=0.72,
        critical_packets_delivered=3,
        total_critical_packets=4,
        deadline_misses=1,
        avg_packet_delay_s=45.0,
        bandwidth_utilization=0.85,
        retransmission_overhead=0.04,
        risk_score=0.3,
        risk_level=RiskLevel.MEDIUM,
        deferred_packets=[],
    )
    base.update(overrides)
    return base


def make_evidence_item(**overrides) -> dict:
    base = dict(
        source="LinkState",
        field="snr_db",
        value=10.0,
        interpretation="SNR is acceptable for reliable BPSK transmission.",
    )
    base.update(overrides)
    return base


def make_ai_recommendation(**overrides) -> dict:
    base = dict(
        recommended_plan_id="plan-001",
        packet_actions=[{"packet_id": "pkt-001", "action": "transmit", "rank": 1}],
        risk_score=0.3,
        risk_level=RiskLevel.MEDIUM,
        confidence=0.85,
        reasoning="Baseline plan covers all critical packets within window.",
        evidence=[EvidenceItem(**make_evidence_item())],
        alternative_plan_id=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# RiskLevel
# ---------------------------------------------------------------------------


class TestRiskLevel:
    def test_all_values_exist(self):
        assert RiskLevel.LOW == "LOW"
        assert RiskLevel.MEDIUM == "MEDIUM"
        assert RiskLevel.HIGH == "HIGH"
        assert RiskLevel.CRITICAL == "CRITICAL"

    def test_is_string_enum(self):
        assert isinstance(RiskLevel.LOW, str)


# ---------------------------------------------------------------------------
# LinkState
# ---------------------------------------------------------------------------


class TestLinkState:
    def test_valid_construction(self):
        ls = LinkState(**make_link_state())
        assert ls.snr_db == 10.0
        assert ls.remaining_window_s == 300.0

    def test_missing_required_field_raises(self):
        data = make_link_state()
        del data["snr_db"]
        with pytest.raises(ValidationError):
            LinkState(**data)

    def test_ber_below_zero_raises(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(ber=-0.01))

    def test_ber_above_one_raises(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(ber=1.1))

    def test_link_stability_bounds(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(link_stability=-0.1))
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(link_stability=1.1))

    def test_nominal_data_rate_must_be_positive(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(nominal_data_rate_bps=0.0))

    def test_link_goodput_must_be_positive(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(link_goodput_bps=0.0))

    def test_remaining_window_non_negative(self):
        with pytest.raises(ValidationError):
            LinkState(**make_link_state(remaining_window_s=-1.0))


# ---------------------------------------------------------------------------
# MissionState
# ---------------------------------------------------------------------------


class TestMissionState:
    def test_valid_construction(self):
        ms = MissionState(**make_mission_state())
        assert ms.mission_id == "GCSI-001"
        assert ms.risk_level == RiskLevel.LOW

    def test_risk_score_above_one_raises(self):
        with pytest.raises(ValidationError):
            MissionState(**make_mission_state(risk_score=1.1))

    def test_risk_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            MissionState(**make_mission_state(risk_score=-0.1))

    def test_risk_score_and_risk_level_are_separate_fields(self):
        ms = MissionState(**make_mission_state(risk_score=0.8, risk_level=RiskLevel.HIGH))
        assert ms.risk_score == 0.8
        assert ms.risk_level == RiskLevel.HIGH

    def test_missing_mission_id_raises(self):
        data = make_mission_state()
        del data["mission_id"]
        with pytest.raises(ValidationError):
            MissionState(**data)


# ---------------------------------------------------------------------------
# Packet
# ---------------------------------------------------------------------------


class TestPacket:
    def test_valid_construction(self):
        pkt = Packet(**make_packet())
        assert pkt.packet_id == "pkt-001"
        assert pkt.size_bits == 8192

    def test_no_priority_field(self):
        pkt = Packet(**make_packet())
        assert not hasattr(pkt, "priority"), "Packet must not expose a 'priority' field"
        assert "priority" not in Packet.model_fields

    def test_criticality_above_one_raises(self):
        with pytest.raises(ValidationError):
            Packet(**make_packet(criticality=1.1))

    def test_criticality_below_zero_raises(self):
        with pytest.raises(ValidationError):
            Packet(**make_packet(criticality=-0.1))

    def test_mission_relevance_above_one_raises(self):
        with pytest.raises(ValidationError):
            Packet(**make_packet(mission_relevance=1.5))

    def test_mission_relevance_below_zero_raises(self):
        with pytest.raises(ValidationError):
            Packet(**make_packet(mission_relevance=-0.5))

    def test_size_bits_must_be_positive(self):
        with pytest.raises(ValidationError):
            Packet(**make_packet(size_bits=0))

    def test_missing_packet_id_raises(self):
        data = make_packet()
        del data["packet_id"]
        with pytest.raises(ValidationError):
            Packet(**data)


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


class TestScenario:
    def _valid_scenario(self) -> dict:
        return dict(
            scenario_id="sc-001",
            simulated=True,
            link_inputs={"snr_db": 10.0, "rssi_dbm": -80.0},
            mission_state=MissionState(**make_mission_state()),
            packets=[Packet(**make_packet())],
        )

    def test_valid_construction(self):
        sc = Scenario(**self._valid_scenario())
        assert sc.simulated is True
        assert len(sc.packets) == 1

    def test_simulated_false_is_accepted_by_model(self):
        # Model accepts False; the ScenarioLoader (not the model) enforces simulated=True.
        data = self._valid_scenario()
        data["simulated"] = False
        sc = Scenario(**data)
        assert sc.simulated is False

    def test_missing_scenario_id_raises(self):
        data = self._valid_scenario()
        del data["scenario_id"]
        with pytest.raises(ValidationError):
            Scenario(**data)

    def test_empty_packets_allowed(self):
        data = self._valid_scenario()
        data["packets"] = []
        sc = Scenario(**data)
        assert sc.packets == []


# ---------------------------------------------------------------------------
# CandidatePlan
# ---------------------------------------------------------------------------


class TestCandidatePlan:
    def test_valid_construction(self):
        plan = CandidatePlan(
            plan_id="plan-001",
            strategy="baseline",
            packets=[Packet(**make_packet())],
            generated_by="BaselineScheduler",
            metadata={},
        )
        assert plan.strategy == "baseline"

    def test_missing_plan_id_raises(self):
        with pytest.raises(ValidationError):
            CandidatePlan(
                strategy="baseline",
                packets=[],
                generated_by="BaselineScheduler",
                metadata={},
            )

    def test_metadata_defaults_to_empty_dict(self):
        plan = CandidatePlan(
            plan_id="plan-002",
            strategy="deadline_first",
            packets=[],
            generated_by="CandidateGenerator",
        )
        assert plan.metadata == {}


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------


class TestEvaluationResult:
    def test_valid_construction(self):
        er = EvaluationResult(**make_evaluation_result())
        assert er.plan_id == "plan-001"
        assert er.risk_level == RiskLevel.MEDIUM

    def test_risk_score_above_one_raises(self):
        with pytest.raises(ValidationError):
            EvaluationResult(**make_evaluation_result(risk_score=1.01))

    def test_risk_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            EvaluationResult(**make_evaluation_result(risk_score=-0.01))

    def test_bandwidth_utilization_above_one_raises(self):
        with pytest.raises(ValidationError):
            EvaluationResult(**make_evaluation_result(bandwidth_utilization=1.01))

    def test_deferred_packets_defaults_to_empty(self):
        data = make_evaluation_result()
        del data["deferred_packets"]
        er = EvaluationResult(**data)
        assert er.deferred_packets == []

    def test_deferred_packets_contains_strings(self):
        er = EvaluationResult(**make_evaluation_result(deferred_packets=["pkt-003", "pkt-007"]))
        assert er.deferred_packets == ["pkt-003", "pkt-007"]

    def test_risk_score_and_risk_level_are_separate(self):
        er = EvaluationResult(**make_evaluation_result(risk_score=0.76, risk_level=RiskLevel.CRITICAL))
        assert er.risk_score == 0.76
        assert er.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------


class TestSimulationResult:
    def _valid_sim_result(self) -> dict:
        return dict(
            plan_id="plan-001",
            delivered_packets=["pkt-001"],
            deferred_packets=[],
            failed_packets=[],
            elapsed_time_s=92.4,
            retransmission_counts={"pkt-001": 0},
            link_state=LinkState(**make_link_state(remaining_window_s=207.6)),
            mission_state=MissionState(**make_mission_state()),
        )

    def test_valid_construction(self):
        sr = SimulationResult(**self._valid_sim_result())
        assert sr.elapsed_time_s == 92.4
        assert sr.delivered_packets == ["pkt-001"]

    def test_missing_link_state_raises(self):
        data = self._valid_sim_result()
        del data["link_state"]
        with pytest.raises(ValidationError):
            SimulationResult(**data)

    def test_empty_lists_are_defaults(self):
        data = self._valid_sim_result()
        del data["delivered_packets"]
        del data["deferred_packets"]
        del data["failed_packets"]
        sr = SimulationResult(**data)
        assert sr.delivered_packets == []


# ---------------------------------------------------------------------------
# EvidenceItem
# ---------------------------------------------------------------------------


class TestEvidenceItem:
    def test_valid_construction(self):
        ei = EvidenceItem(**make_evidence_item())
        assert ei.source == "LinkState"
        assert ei.field == "snr_db"
        assert ei.value == 10.0
        assert "SNR" in ei.interpretation

    def test_missing_source_raises(self):
        data = make_evidence_item()
        del data["source"]
        with pytest.raises(ValidationError):
            EvidenceItem(**data)

    def test_missing_field_raises(self):
        data = make_evidence_item()
        del data["field"]
        with pytest.raises(ValidationError):
            EvidenceItem(**data)

    def test_missing_interpretation_raises(self):
        data = make_evidence_item()
        del data["interpretation"]
        with pytest.raises(ValidationError):
            EvidenceItem(**data)

    def test_value_accepts_any_type(self):
        ei = EvidenceItem(**make_evidence_item(value={"nested": True}))
        assert ei.value == {"nested": True}
        ei2 = EvidenceItem(**make_evidence_item(value=None))
        assert ei2.value is None


# ---------------------------------------------------------------------------
# AIRecommendation
# ---------------------------------------------------------------------------


class TestAIRecommendation:
    def test_valid_construction(self):
        rec = AIRecommendation(**make_ai_recommendation())
        assert rec.recommended_plan_id == "plan-001"
        assert rec.confidence == 0.85
        assert rec.alternative_plan_id is None

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            AIRecommendation(**make_ai_recommendation(confidence=1.1))

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            AIRecommendation(**make_ai_recommendation(confidence=-0.1))

    def test_risk_score_above_one_raises(self):
        with pytest.raises(ValidationError):
            AIRecommendation(**make_ai_recommendation(risk_score=1.5))

    def test_risk_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            AIRecommendation(**make_ai_recommendation(risk_score=-0.5))

    def test_evidence_is_list_of_evidence_items(self):
        rec = AIRecommendation(**make_ai_recommendation())
        assert isinstance(rec.evidence[0], EvidenceItem)

    def test_alternative_plan_id_optional(self):
        rec = AIRecommendation(**make_ai_recommendation(alternative_plan_id="plan-002"))
        assert rec.alternative_plan_id == "plan-002"

    def test_missing_recommended_plan_id_raises(self):
        data = make_ai_recommendation()
        del data["recommended_plan_id"]
        with pytest.raises(ValidationError):
            AIRecommendation(**data)

    def test_risk_score_and_risk_level_are_separate(self):
        rec = AIRecommendation(**make_ai_recommendation(risk_score=0.9, risk_level=RiskLevel.CRITICAL))
        assert rec.risk_score == 0.9
        assert rec.risk_level == RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class TestGCSIConfig:
    def test_default_construction(self):
        cfg = GCSIConfig()
        assert cfg.telecom.modulation == "BPSK"
        assert cfg.telecom.protocol_efficiency == 0.9
        assert cfg.telecom.channel_bandwidth_hz == 1_000_000.0
        assert cfg.telecom.bit_rate_bps == 100_000.0

    def test_scheduler_weights_defaults_are_positive(self):
        cfg = GCSIConfig()
        sw = cfg.scheduler
        assert sw.w_criticality > 0
        assert sw.w_deadline_urgency > 0
        assert sw.w_mission_relevance > 0
        assert sw.w_delivery_reliability > 0
        assert sw.w_cost_efficiency > 0

    def test_risk_weights_defaults_are_positive(self):
        cfg = GCSIConfig()
        rw = cfg.risk
        assert rw.w_deadline_miss > 0
        assert rw.w_critical_deficit > 0
        assert rw.w_window_pressure > 0

    def test_protocol_efficiency_zero_raises(self):
        with pytest.raises(ValidationError):
            TelecomConfig(protocol_efficiency=0.0)

    def test_protocol_efficiency_above_one_raises(self):
        with pytest.raises(ValidationError):
            TelecomConfig(protocol_efficiency=1.1)

    def test_protocol_efficiency_exactly_one_is_valid(self):
        tc = TelecomConfig(protocol_efficiency=1.0)
        assert tc.protocol_efficiency == 1.0

    def test_invalid_modulation_raises(self):
        with pytest.raises(ValidationError):
            TelecomConfig(modulation="QPSK")

    def test_channel_bandwidth_must_be_positive(self):
        with pytest.raises(ValidationError):
            TelecomConfig(channel_bandwidth_hz=0.0)

    def test_bit_rate_must_be_positive(self):
        with pytest.raises(ValidationError):
            TelecomConfig(bit_rate_bps=0.0)
