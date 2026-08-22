"""Unit tests for GraniteAgent — response parsing, validation, and error handling.

Most tests exercise the response parsing/validation logic in isolation using
mock Granite responses, so they run without any API key.

Tests that require a live Granite API are marked with @pytest.mark.granite and
are skipped by default when GCSI_GRANITE_API_KEY is not set.
"""

import json
import os

import pytest

from backend.app.agent.granite_agent import (
    EvidenceHallucinationError,
    GraniteAgent,
    GraniteAPIError,
    GraniteResponseError,
)
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.recommendation import AIRecommendation
from backend.app.models.risk_level import RiskLevel
from datetime import datetime, timezone
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_link_state() -> LinkState:
    return LinkState(
        timestamp=_TS, snr_db=12.0, eb_n0_db=20.0, ber=1e-6,
        rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
        link_goodput_bps=90_000.0, latency_s=0.0, link_stability=0.9,
        remaining_window_s=300.0,
    )


def make_mission_state() -> MissionState:
    return MissionState(
        mission_id="test", mission_phase="downlink", current_event="pass",
        event_time_remaining_s=300.0, comm_window_remaining_s=300.0,
        risk_score=0.1, risk_level=RiskLevel.LOW,
    )


def make_packet(pid: str) -> Packet:
    return Packet(
        packet_id=pid, packet_type="telemetry", size_bits=8_000,
        criticality=0.8, mission_relevance=0.7, deadline_s=200.0,
        retry_cost=0.2, delivery_requirement="required",
    )


def make_plan(plan_id: str, pids: list[str] = None) -> CandidatePlan:
    pkts = [make_packet(p) for p in (pids or ["p1", "p2"])]
    return CandidatePlan(
        plan_id=plan_id, strategy=plan_id, packets=pkts, generated_by="test"
    )


def make_evaluation(plan_id: str) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=1.2,
        critical_packets_delivered=2,
        total_critical_packets=2,
        deadline_misses=0,
        avg_packet_delay_s=5.0,
        bandwidth_utilization=0.3,
        retransmission_overhead=0.0,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
        deferred_packets=[],
    )


def _valid_response(plan_id: str = "baseline") -> str:
    return json.dumps({
        "recommended_plan_id": plan_id,
        "reasoning": "The baseline plan delivers all critical packets before deadline.",
        "confidence": 0.85,
        "risk_score": 0.1,
        "risk_level": "LOW",
        "evidence": [
            {
                "source": "link_state",
                "field": "ber",
                "value": 1e-6,
                "interpretation": "Very low bit error rate; reliable channel.",
            },
            {
                "source": "mission_state",
                "field": "risk_score",
                "value": 0.1,
                "interpretation": "Mission risk is currently low.",
            },
        ],
        "alternative_plan_id": None,
    })


# ---------------------------------------------------------------------------
# _parse_response tests (no API call needed)
# ---------------------------------------------------------------------------

class TestParseResponse:
    def _agent(self) -> GraniteAgent:
        return GraniteAgent(api_key="dummy")

    def _plans(self) -> list[CandidatePlan]:
        return [make_plan("baseline"), make_plan("deadline-first")]

    def test_valid_response_returns_ai_recommendation(self):
        agent = self._agent()
        result = agent._parse_response(_valid_response("baseline"), self._plans())
        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "baseline"

    def test_risk_level_parsed_correctly(self):
        agent = self._agent()
        result = agent._parse_response(_valid_response(), self._plans())
        assert result.risk_level == RiskLevel.LOW

    def test_evidence_items_present(self):
        agent = self._agent()
        result = agent._parse_response(_valid_response(), self._plans())
        assert len(result.evidence) == 2

    def test_packet_actions_built_from_recommended_plan(self):
        agent = self._agent()
        plans = [make_plan("baseline", pids=["p1", "p2", "p3"])]
        resp = json.dumps({
            "recommended_plan_id": "baseline",
            "reasoning": "test",
            "confidence": 0.9,
            "risk_score": 0.2,
            "risk_level": "LOW",
            "evidence": [],
            "alternative_plan_id": None,
        })
        result = agent._parse_response(resp, plans)
        assert len(result.packet_actions) == 3
        assert result.packet_actions[0]["packet_id"] == "p1"
        assert result.packet_actions[0]["rank"] == 1

    def test_invalid_json_raises_response_error(self):
        agent = self._agent()
        with pytest.raises(GraniteResponseError):
            agent._parse_response("not json", self._plans())

    def test_missing_field_raises_response_error(self):
        agent = self._agent()
        data = {"recommended_plan_id": "baseline"}  # many fields missing
        with pytest.raises(GraniteResponseError):
            agent._parse_response(json.dumps(data), self._plans())

    def test_unknown_plan_id_raises_response_error(self):
        agent = self._agent()
        resp = _valid_response("nonexistent-plan-id")
        with pytest.raises(GraniteResponseError):
            agent._parse_response(resp, self._plans())

    def test_invalid_risk_level_raises_response_error(self):
        agent = self._agent()
        data = json.loads(_valid_response())
        data["risk_level"] = "UNKNOWN_LEVEL"
        with pytest.raises(GraniteResponseError):
            agent._parse_response(json.dumps(data), self._plans())

    def test_hallucinated_evidence_field_raises_hallucination_error(self):
        agent = self._agent()
        data = json.loads(_valid_response())
        data["evidence"].append({
            "source": "link_state",
            "field": "invented_rf_metric",  # does not exist in any model
            "value": 42,
            "interpretation": "made up",
        })
        with pytest.raises(EvidenceHallucinationError):
            agent._parse_response(json.dumps(data), self._plans())

    def test_markdown_fenced_json_is_parsed(self):
        """Agent output may include ```json ``` fences — these should be stripped."""
        agent = self._agent()
        fenced = f"```json\n{_valid_response()}\n```"
        result = agent._parse_response(fenced, self._plans())
        assert isinstance(result, AIRecommendation)


# ---------------------------------------------------------------------------
# API unavailability — no API key
# ---------------------------------------------------------------------------

class TestAPIUnavailable:
    def test_raises_api_error_when_no_key(self):
        agent = GraniteAgent(api_key="")
        with pytest.raises(GraniteAPIError, match="GCSI_GRANITE_API_KEY"):
            agent._call_api("hello")

    def test_recommend_raises_api_error_when_no_key(self):
        agent = GraniteAgent(api_key="")
        with pytest.raises(GraniteAPIError):
            agent.recommend(
                make_link_state(),
                make_mission_state(),
                [make_plan("baseline")],
                [make_evaluation("baseline")],
            )


# ---------------------------------------------------------------------------
# Live Granite tests — skipped unless GCSI_GRANITE_API_KEY is set
# ---------------------------------------------------------------------------

_has_api_key = bool(os.getenv("GCSI_GRANITE_API_KEY"))


@pytest.mark.granite
@pytest.mark.skipif(not _has_api_key, reason="GCSI_GRANITE_API_KEY not set")
def test_live_granite_returns_recommendation():
    """Integration test: calls real Granite API. Only runs with API key set."""
    from backend.app import state as app_state
    app_state.load_scenario("data/scenarios/nominal_pass.json")

    from backend.app.config import SchedulerWeights
    from backend.app.candidate_generator.generator import CandidateGenerator
    from backend.app.evaluator.plan_evaluator import PlanEvaluator

    weights = SchedulerWeights()
    gen = CandidateGenerator()
    plans = gen.generate(
        app_state.active_scenario.packets,
        app_state.active_link_state,
        app_state.active_scenario.mission_state,
        weights,
    )
    ev = PlanEvaluator()
    evaluations = [
        ev.evaluate(p, app_state.active_link_state, app_state.active_scenario.mission_state)
        for p in plans
    ]

    agent = GraniteAgent()
    result = agent.recommend(
        app_state.active_link_state,
        app_state.active_scenario.mission_state,
        plans,
        evaluations,
    )
    assert isinstance(result, AIRecommendation)
    assert result.recommended_plan_id in {p.plan_id for p in plans}
    assert 0.0 <= result.confidence <= 1.0
    assert 0.0 <= result.risk_score <= 1.0
    assert result.risk_level in list(RiskLevel)
