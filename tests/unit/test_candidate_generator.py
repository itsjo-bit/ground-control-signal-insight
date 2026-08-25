"""Phase 3 tests — CandidateGenerator and ScenarioLoader unit tests.

Generator tests use purpose-built fixtures that isolate exactly one
ordering variable at a time, rather than asserting all strategies differ
on the same scenario (which is not guaranteed and not required).
"""

import json
import math
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.candidate_generator.generator import CandidateGenerator
from backend.app.config import GCSIConfig, SchedulerWeights
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel
from backend.app.scheduler.baseline import BaselineScheduler
from backend.app.simulation.scenario_loader import ScenarioLoader

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
        size_bits=1024,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=200.0,
        retry_cost=0.5,
        delivery_requirement="required",
    )
    base.update(overrides)
    return Packet(**base)


DEFAULT_WEIGHTS = SchedulerWeights()
DEFAULT_LS = make_link_state()
DEFAULT_MS = make_mission_state()


# ===========================================================================
# ScenarioLoader tests
# ===========================================================================


class TestScenarioLoader:
    def _write_scenario(self, data: dict) -> str:
        """Write data to a temp file; return its path."""
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        )
        json.dump(data, tmp)
        tmp.close()
        return tmp.name

    def _minimal_scenario(self, simulated: bool = True) -> dict:
        return {
            "scenario_id": "test-001",
            "simulated": simulated,
            "link_inputs": {"snr_db": 10.0},
            "mission_state": {
                "mission_id": "m-001",
                "mission_phase": "science",
                "current_event": "pass",
                "event_time_remaining_s": 300.0,
                "comm_window_remaining_s": 300.0,
                "risk_score": 0.1,
                "risk_level": "LOW",
            },
            "packets": [
                {
                    "packet_id": "p1",
                    "packet_type": "telemetry",
                    "size_bits": 1024,
                    "criticality": 0.8,
                    "mission_relevance": 0.7,
                    "deadline_s": 100.0,
                    "retry_cost": 0.3,
                    "delivery_requirement": "required",
                }
            ],
        }

    def test_valid_simulated_scenario_loads(self):
        path = self._write_scenario(self._minimal_scenario(simulated=True))
        scenario = ScenarioLoader.load(path)
        assert scenario.scenario_id == "test-001"
        assert scenario.simulated is True
        assert len(scenario.packets) == 1

    def test_simulated_false_raises_value_error(self):
        path = self._write_scenario(self._minimal_scenario(simulated=False))
        with pytest.raises(ValueError, match="simulated=False"):
            ScenarioLoader.load(path)

    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            ScenarioLoader.load("/nonexistent/path/scenario.json")

    def test_invalid_json_raises_value_error(self):
        tmp = tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        )
        tmp.write("{ not valid json }")
        tmp.close()
        with pytest.raises(ValueError, match="invalid JSON"):
            ScenarioLoader.load(tmp.name)

    def test_schema_validation_failure_raises_value_error(self):
        """A JSON file with missing required fields must raise ValueError."""
        bad = {"scenario_id": "bad", "simulated": True}  # missing required fields
        path = self._write_scenario(bad)
        with pytest.raises(ValueError, match="schema validation"):
            ScenarioLoader.load(path)

    def test_packet_order_preserved(self):
        """Packets must appear in the same order as in the file."""
        data = self._minimal_scenario()
        extra_pkt = {
            "packet_id": "p2",
            "packet_type": "science",
            "size_bits": 2048,
            "criticality": 0.3,
            "mission_relevance": 0.4,
            "deadline_s": 200.0,
            "retry_cost": 0.1,
            "delivery_requirement": "best-effort",
        }
        data["packets"].insert(0, extra_pkt)  # p2 first in file
        path = self._write_scenario(data)
        scenario = ScenarioLoader.load(path)
        assert scenario.packets[0].packet_id == "p2"
        assert scenario.packets[1].packet_id == "p1"

    def test_reference_scenario_nominal_pass_loads(self):
        """The nominal_pass.json reference scenario must load cleanly."""
        path = Path(__file__).parents[2] / "data" / "scenarios" / "nominal_pass.json"
        scenario = ScenarioLoader.load(str(path))
        assert scenario.simulated is True
        assert len(scenario.packets) >= 1

    def test_reference_scenario_degraded_link_loads(self):
        """The degraded_link.json reference scenario must load cleanly."""
        path = Path(__file__).parents[2] / "data" / "scenarios" / "degraded_link.json"
        scenario = ScenarioLoader.load(str(path))
        assert scenario.simulated is True
        assert len(scenario.packets) >= 1


# ===========================================================================
# ScenarioLoaderV2 tests — mission_data_v2.json with DataProduct + AnomalyEvent
# ===========================================================================


class TestScenarioLoaderV2:
    """Verify that ScenarioLoader correctly loads the v2 reference scenario."""

    _SCENARIO_PATH = Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json"

    def test_v2_scenario_loads_without_error(self):
        """mission_data_v2.json must load via ScenarioLoader without raising."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert scenario is not None

    def test_v2_scenario_id(self):
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert scenario.scenario_id == "mission_data_v2_anomaly_pass"

    def test_v2_is_simulated(self):
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert scenario.simulated is True

    def test_v2_packets_field_empty(self):
        """The v2 fixture intentionally carries zero legacy Packet objects."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert scenario.packets == []

    def test_v2_data_products_count(self):
        """The v2 fixture carries exactly 50 DataProduct entries."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert len(scenario.data_products) == 50

    def test_v2_anomalies_count(self):
        """The v2 fixture carries exactly 3 AnomalyEvent entries."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert len(scenario.anomalies) == 3

    def test_v2_data_products_are_typed(self):
        """Every element of data_products must be a DataProduct instance."""
        from backend.app.models.data_product import DataProduct

        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        for dp in scenario.data_products:
            assert isinstance(dp, DataProduct), (
                f"Expected DataProduct, got {type(dp)} for id={dp.product_id!r}"
            )

    def test_v2_anomalies_are_typed(self):
        """Every element of anomalies must be an AnomalyEvent instance."""
        from backend.app.models.anomaly_event import AnomalyEvent

        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        for ae in scenario.anomalies:
            assert isinstance(ae, AnomalyEvent), (
                f"Expected AnomalyEvent, got {type(ae)} for id={ae.anomaly_id!r}"
            )

    def test_v2_all_product_ids_unique(self):
        """Every DataProduct must have a distinct product_id."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        ids = [dp.product_id for dp in scenario.data_products]
        assert len(ids) == len(set(ids)), "Duplicate product_id detected"

    def test_v2_all_anomaly_ids_unique(self):
        """Every AnomalyEvent must have a distinct anomaly_id."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        ids = [ae.anomaly_id for ae in scenario.anomalies]
        assert len(ids) == len(set(ids)), "Duplicate anomaly_id detected"

    def test_v2_known_anomaly_ids(self):
        """The three anomalies must have the expected IDs."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        ids = {ae.anomaly_id for ae in scenario.anomalies}
        assert ids == {"ANOM-017", "ANOM-023", "ANOM-031"}

    def test_v2_severities_in_range(self):
        """All anomaly severities must be within [0.0, 1.0]."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        for ae in scenario.anomalies:
            assert 0.0 <= ae.severity <= 1.0, (
                f"Severity out of range for {ae.anomaly_id}: {ae.severity}"
            )

    def test_v2_product_criticality_in_range(self):
        """All DataProduct criticality values must be within [0.0, 1.0]."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        for dp in scenario.data_products:
            assert 0.0 <= dp.criticality <= 1.0, (
                f"Criticality out of range for {dp.product_id}: {dp.criticality}"
            )

    def test_v2_anomaly_linked_products_reference_valid_ids(self):
        """Every product_id referenced in anomaly.related_product_ids must exist."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        product_id_set = {dp.product_id for dp in scenario.data_products}
        for ae in scenario.anomalies:
            for ref_id in ae.related_product_ids:
                assert ref_id in product_id_set, (
                    f"Anomaly {ae.anomaly_id} references unknown product_id {ref_id!r}"
                )

    def test_v2_product_anomaly_ids_reference_valid_anomalies(self):
        """Every non-None anomaly_id on a DataProduct must exist as an AnomalyEvent."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        anomaly_id_set = {ae.anomaly_id for ae in scenario.anomalies}
        for dp in scenario.data_products:
            if dp.anomaly_id is not None:
                assert dp.anomaly_id in anomaly_id_set, (
                    f"DataProduct {dp.product_id} references unknown anomaly_id {dp.anomaly_id!r}"
                )

    def test_v2_all_product_sizes_positive(self):
        """All DataProduct size_bits values must be strictly positive."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        for dp in scenario.data_products:
            assert dp.size_bits > 0, f"{dp.product_id} has non-positive size_bits={dp.size_bits}"

    def test_v2_high_severity_anomaly_has_related_products(self):
        """ANOM-017 (severity 0.85) must have at least one related product."""
        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        anom = next(ae for ae in scenario.anomalies if ae.anomaly_id == "ANOM-017")
        assert len(anom.related_product_ids) >= 1

    def test_v2_mission_state_risk_level(self):
        """The v2 scenario mission state must reflect the HIGH risk_level."""
        from backend.app.models.risk_level import RiskLevel

        scenario = ScenarioLoader.load(str(self._SCENARIO_PATH))
        assert scenario.mission_state.risk_level == RiskLevel.HIGH




# ===========================================================================
# CandidateGenerator — strategy-specific fixtures
# ===========================================================================


class TestCandidateGeneratorDeadlineFirst:
    """deadline_first fixture: same criticality, clearly different deadlines."""

    def _packets(self) -> list[Packet]:
        return [
            make_packet(packet_id="late", criticality=0.5, deadline_s=250.0),
            make_packet(packet_id="mid", criticality=0.5, deadline_s=100.0),
            make_packet(packet_id="early", criticality=0.5, deadline_s=20.0),
        ]

    def test_earliest_deadline_first(self):
        plans = CandidateGenerator.generate(
            self._packets(), DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS
        )
        df_plan = next(p for p in plans if p.strategy == "deadline_first")
        assert df_plan.packets[0].packet_id == "early"
        assert df_plan.packets[-1].packet_id == "late"

    def test_deadline_first_tie_broken_by_criticality_desc(self):
        pkts = [
            make_packet(packet_id="high-crit", criticality=0.9, deadline_s=50.0),
            make_packet(packet_id="low-crit", criticality=0.2, deadline_s=50.0),
        ]
        plans = CandidateGenerator.generate(pkts, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        df_plan = next(p for p in plans if p.strategy == "deadline_first")
        assert df_plan.packets[0].packet_id == "high-crit"

    def test_strategy_name_is_correct(self):
        plans = CandidateGenerator.generate(
            self._packets(), DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS
        )
        df_plan = next(p for p in plans if p.strategy == "deadline_first")
        assert df_plan.strategy == "deadline_first"
        assert df_plan.generated_by == "CandidateGenerator"


class TestCandidateGeneratorMissionCriticalFirst:
    """mission_critical_first fixture: same deadline, clearly different criticality."""

    def _packets(self) -> list[Packet]:
        return [
            make_packet(packet_id="mid", criticality=0.5, mission_relevance=0.5, deadline_s=200.0),
            make_packet(packet_id="low", criticality=0.1, mission_relevance=0.8, deadline_s=200.0),
            make_packet(packet_id="high", criticality=0.9, mission_relevance=0.4, deadline_s=200.0),
        ]

    def test_highest_criticality_first(self):
        plans = CandidateGenerator.generate(
            self._packets(), DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS
        )
        mc_plan = next(p for p in plans if p.strategy == "mission_critical_first")
        assert mc_plan.packets[0].packet_id == "high"
        assert mc_plan.packets[-1].packet_id == "low"

    def test_tie_broken_by_mission_relevance_desc(self):
        pkts = [
            make_packet(packet_id="low-rel", criticality=0.8, mission_relevance=0.2, deadline_s=100.0),
            make_packet(packet_id="high-rel", criticality=0.8, mission_relevance=0.9, deadline_s=100.0),
        ]
        plans = CandidateGenerator.generate(pkts, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        mc_plan = next(p for p in plans if p.strategy == "mission_critical_first")
        assert mc_plan.packets[0].packet_id == "high-rel"

    def test_strategy_name_is_correct(self):
        plans = CandidateGenerator.generate(
            self._packets(), DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS
        )
        mc_plan = next(p for p in plans if p.strategy == "mission_critical_first")
        assert mc_plan.strategy == "mission_critical_first"
        assert mc_plan.generated_by == "CandidateGenerator"


class TestCandidateGeneratorValuePerCost:
    """value_per_cost fixture: packets with clearly different value/cost ratios."""

    def _high_value_low_cost(self) -> Packet:
        """High criticality * relevance, tiny packet → high ratio."""
        return make_packet(
            packet_id="best-ratio",
            criticality=1.0,
            mission_relevance=1.0,
            size_bits=512,   # tiny → low tx_time → low cost
        )

    def _low_value_high_cost(self) -> Packet:
        """Low value, huge packet → low ratio."""
        return make_packet(
            packet_id="worst-ratio",
            criticality=0.1,
            mission_relevance=0.1,
            size_bits=500_000,  # large → high cost
        )

    def test_highest_ratio_first(self):
        pkts = [self._low_value_high_cost(), self._high_value_low_cost()]
        plans = CandidateGenerator.generate(pkts, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        vp_plan = next(p for p in plans if p.strategy == "value_per_cost")
        assert vp_plan.packets[0].packet_id == "best-ratio"
        assert vp_plan.packets[-1].packet_id == "worst-ratio"

    def test_infinite_cost_packet_placed_last(self):
        """Packets with p_success=0 (infinite cost) must appear after finite-cost packets.

        Strategy: use a moderate-BER link where a very large packet underflows
        packet_success_probability to exactly 0.0 (infinite cost) while a
        tiny packet retains a finite p_success.
        """
        # ber=0.5 → p_success for 1 Mbit packet = exp(1e6 * log1p(-0.5))
        # = exp(1e6 * -0.693) ≈ exp(-693000) → exactly 0.0 in float64 → infinite cost.
        # Tiny packet (8 bits): p_success = exp(8 * log1p(-0.5)) ≈ 0.004 → finite cost.
        moderate_ber_link = make_link_state(ber=0.5)
        infinite_pkt = make_packet(packet_id="infinite", size_bits=1_000_000,
                                   criticality=0.5, mission_relevance=0.5)
        finite_pkt = make_packet(packet_id="finite", size_bits=8,
                                 criticality=0.5, mission_relevance=0.5)
        plans = CandidateGenerator.generate(
            [infinite_pkt, finite_pkt], moderate_ber_link, DEFAULT_MS, DEFAULT_WEIGHTS
        )
        vp_plan = next(p for p in plans if p.strategy == "value_per_cost")
        # finite_pkt has a finite cost → comes first; infinite_pkt → last
        assert vp_plan.packets[0].packet_id == "finite"
        assert vp_plan.packets[-1].packet_id == "infinite"

    def test_strategy_name_is_correct(self):
        pkts = [self._high_value_low_cost(), self._low_value_high_cost()]
        plans = CandidateGenerator.generate(pkts, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        vp_plan = next(p for p in plans if p.strategy == "value_per_cost")
        assert vp_plan.strategy == "value_per_cost"
        assert vp_plan.generated_by == "CandidateGenerator"


class TestCandidateGeneratorPlanIds:
    """Plan IDs must be deterministic stable strings, never UUIDs."""

    def test_baseline_plan_id(self):
        plans = CandidateGenerator.generate([make_packet()], DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        bl = next(p for p in plans if p.strategy == "baseline")
        assert bl.plan_id == "baseline"

    def test_deadline_first_plan_id(self):
        plans = CandidateGenerator.generate([make_packet()], DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        df = next(p for p in plans if p.strategy == "deadline_first")
        assert df.plan_id == "deadline-first"

    def test_mission_critical_first_plan_id(self):
        plans = CandidateGenerator.generate([make_packet()], DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        mc = next(p for p in plans if p.strategy == "mission_critical_first")
        assert mc.plan_id == "mission-critical-first"

    def test_value_per_cost_plan_id(self):
        plans = CandidateGenerator.generate([make_packet()], DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        vp = next(p for p in plans if p.strategy == "value_per_cost")
        assert vp.plan_id == "value-per-cost"

    def test_plan_ids_stable_across_calls(self):
        """Two calls with the same inputs must produce identical plan_id values."""
        packets = [make_packet(packet_id="p1"), make_packet(packet_id="p2")]
        plans1 = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        plans2 = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        for p1, p2 in zip(plans1, plans2):
            assert p1.plan_id == p2.plan_id, (
                f"strategy={p1.strategy}: got {p1.plan_id} vs {p2.plan_id}"
            )


class TestCandidateGeneratorBaseline:
    def test_first_plan_is_baseline(self):
        packets = [
            make_packet(packet_id="a", criticality=0.7),
            make_packet(packet_id="b", criticality=0.4),
        ]
        plans = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        assert plans[0].strategy == "baseline"

    def test_baseline_plan_matches_scheduler_output(self):
        """The baseline candidate must be identical to BaselineScheduler.rank()."""
        packets = [
            make_packet(packet_id="x", criticality=0.9),
            make_packet(packet_id="y", criticality=0.2),
        ]
        plans = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        baseline_candidate = next(p for p in plans if p.strategy == "baseline")

        direct = BaselineScheduler.rank(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        assert [p.packet_id for p in baseline_candidate.packets] == [
            p.packet_id for p in direct.packets
        ]


class TestCandidateGeneratorGeneral:
    def test_exactly_four_plans_returned(self):
        packets = [make_packet(packet_id=f"p{i}") for i in range(3)]
        plans = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        assert len(plans) == 4

    def test_all_strategy_names_present(self):
        packets = [make_packet(packet_id=f"p{i}") for i in range(3)]
        plans = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        names = {p.strategy for p in plans}
        assert names == {"baseline", "deadline_first", "mission_critical_first", "value_per_cost"}

    def test_all_plans_contain_all_packets(self):
        """Every strategy must include every packet — none are dropped."""
        packets = [make_packet(packet_id=f"p{i}") for i in range(5)]
        plans = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        expected_ids = {p.packet_id for p in packets}
        for plan in plans:
            assert {p.packet_id for p in plan.packets} == expected_ids

    def test_empty_packet_list_returns_four_plans(self):
        plans = CandidateGenerator.generate([], DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        assert len(plans) == 4
        for plan in plans:
            assert plan.packets == []

    def test_generate_is_deterministic(self):
        """Same inputs produce same ordering on every call."""
        packets = [
            make_packet(packet_id="a", criticality=0.7, deadline_s=50.0),
            make_packet(packet_id="b", criticality=0.3, deadline_s=200.0),
            make_packet(packet_id="c", criticality=0.9, deadline_s=100.0),
        ]
        plans1 = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        plans2 = CandidateGenerator.generate(packets, DEFAULT_LS, DEFAULT_MS, DEFAULT_WEIGHTS)
        for p1, p2 in zip(plans1, plans2):
            assert [x.packet_id for x in p1.packets] == [x.packet_id for x in p2.packets]
