"""Unit tests for the AI provider layer.

Covers:
- Provider selection (factory) without/with Granite credentials
- LocalRuleBasedProvider: successful recommendation
- LocalRuleBasedProvider: invalid inputs raise appropriate errors
- LocalRuleBasedProvider: confidence derived from risk gap
- GraniteProvider: maps GraniteAgent exceptions to canonical hierarchy
- OllamaProvider: response parsing and validation
- OllamaProvider: server unreachable raises AIProviderError
- Integration: POST /agent/recommend returns RecommendResponse with provider field
- Integration: POST /agent/recommend works without GCSI_GRANITE_API_KEY
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.agent.base_provider import (
    AIHallucinationError,
    AIProviderError,
    AIResponseError,
)
from backend.app.agent.granite_provider import GraniteProvider
from backend.app.agent.local_provider import LocalRuleBasedProvider, _confidence_from_gap
from backend.app.agent.ollama_provider import OllamaProvider
from backend.app.agent.provider_factory import get_provider
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.recommendation import AIRecommendation
from backend.app.models.risk_level import RiskLevel
from backend.app.main import app
from backend.app import state as app_state


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def make_plan(plan_id: str, pids: list[str] | None = None) -> CandidatePlan:
    pkts = [make_packet(p) for p in (pids or ["p1", "p2"])]
    return CandidatePlan(
        plan_id=plan_id, strategy=plan_id, packets=pkts, generated_by="test"
    )


def make_evaluation(plan_id: str, risk_score: float = 0.1, mission_value: float = 1.2) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=mission_value,
        critical_packets_delivered=2,
        total_critical_packets=2,
        deadline_misses=0,
        avg_packet_delay_s=5.0,
        bandwidth_utilization=0.3,
        retransmission_overhead=0.0,
        risk_score=risk_score,
        risk_level=RiskLevel.LOW,
        deferred_packets=[],
    )


def _valid_ollama_response(plan_id: str = "baseline") -> str:
    return json.dumps({
        "recommended_plan_id": plan_id,
        "reasoning": "The baseline plan is optimal.",
        "confidence": 0.88,
        "risk_score": 0.15,
        "risk_level": "LOW",
        "evidence": [
            {
                "source": "link_state",
                "field": "ber",
                "value": 1e-6,
                "interpretation": "Low bit error rate.",
            }
        ],
        "alternative_plan_id": None,
    })


@pytest.fixture(autouse=True)
def reset_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None


# ---------------------------------------------------------------------------
# Provider factory — selection logic
# ---------------------------------------------------------------------------

class TestProviderFactory:
    def test_returns_local_provider_when_no_granite_key(self):
        """Without GCSI_GRANITE_API_KEY, factory must return LocalRuleBasedProvider."""
        with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
            provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)
        assert provider.provider_name == "Local"

    def test_returns_granite_provider_when_api_key_set(self):
        """With GCSI_GRANITE_API_KEY set, factory must return GraniteProvider."""
        with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "fake-key-for-test"}):
            provider = get_provider()
        assert isinstance(provider, GraniteProvider)
        assert provider.provider_name == "Granite"

    def test_returns_local_when_ollama_enabled_but_not_reachable(self):
        """GCSI_OLLAMA_ENABLED=true but server not reachable → LocalRuleBasedProvider."""
        from backend.app.agent import provider_factory
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_OLLAMA_ENABLED": "true",
        }):
            with patch.object(provider_factory, "_ollama_reachable", return_value=False):
                provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)
        assert provider.provider_name == "Local"

    def test_returns_ollama_when_enabled_and_reachable(self):
        """GCSI_OLLAMA_ENABLED=true and Ollama reachable → OllamaProvider."""
        from backend.app.agent import provider_factory
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_OLLAMA_ENABLED": "true",
        }):
            with patch.object(provider_factory, "_ollama_reachable", return_value=True):
                provider = get_provider()
        assert isinstance(provider, OllamaProvider)

    def test_granite_key_takes_priority_over_ollama(self):
        """Granite key takes priority even when GCSI_OLLAMA_ENABLED=true."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "fake-key",
            "GCSI_OLLAMA_ENABLED": "true",
        }):
            provider = get_provider()
        assert isinstance(provider, GraniteProvider)

    def test_factory_returns_granite_not_local_when_only_key_set(self):
        """Regression: factory must NOT fall through to Local when the key is set.

        This catches the bug where load_dotenv() was missing and the key was
        invisible to the process — the factory would always return Local.
        """
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "any-non-empty-key",
            "GCSI_OLLAMA_ENABLED": "false",
        }):
            provider = get_provider()
        assert not isinstance(provider, LocalRuleBasedProvider), (
            "factory returned Local despite GCSI_GRANITE_API_KEY being set — "
            "check that load_dotenv() is called before get_provider()"
        )
        assert isinstance(provider, GraniteProvider)

    def test_factory_does_not_raise_when_granite_project_id_missing(self):
        """Factory must return GraniteProvider even when GCSI_GRANITE_PROJECT_ID is
        missing.  The error only surfaces at recommend() time, not at selection time.
        """
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "test-key",
            "GCSI_GRANITE_PROJECT_ID": "",
            "GCSI_OLLAMA_ENABLED": "false",
        }):
            provider = get_provider()  # must not raise
        assert isinstance(provider, GraniteProvider)

    def test_granite_api_key_whitespace_only_treated_as_missing(self):
        """A key that is only whitespace must be treated as absent (falls back to Local)."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "   ",
            "GCSI_OLLAMA_ENABLED": "false",
        }):
            provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)


# ---------------------------------------------------------------------------
# LocalRuleBasedProvider
# ---------------------------------------------------------------------------

class TestLocalRuleBasedProvider:
    def _provider(self) -> LocalRuleBasedProvider:
        return LocalRuleBasedProvider()

    def _full_set(self) -> tuple:
        plans = [
            make_plan("baseline"),
            make_plan("deadline-first"),
            make_plan("mission-critical-first"),
            make_plan("value-per-cost"),
        ]
        evals = [
            make_evaluation("baseline", risk_score=0.10, mission_value=1.5),
            make_evaluation("deadline-first", risk_score=0.25, mission_value=1.2),
            make_evaluation("mission-critical-first", risk_score=0.20, mission_value=1.3),
            make_evaluation("value-per-cost", risk_score=0.15, mission_value=1.4),
        ]
        return plans, evals

    def test_returns_ai_recommendation(self):
        provider = self._provider()
        plans, evals = self._full_set()
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert isinstance(result, AIRecommendation)

    def test_recommends_lowest_risk_plan(self):
        """Provider must pick the plan with the lowest risk_score."""
        provider = self._provider()
        plans, evals = self._full_set()
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert result.recommended_plan_id == "baseline"  # risk=0.10, lowest

    def test_alternative_is_second_best(self):
        """Alternative plan is the runner-up by risk_score."""
        provider = self._provider()
        plans, evals = self._full_set()
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        # Second best: value-per-cost (risk=0.15)
        assert result.alternative_plan_id == "value-per-cost"

    def test_confidence_is_in_range(self):
        provider = self._provider()
        plans, evals = self._full_set()
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert 0.0 <= result.confidence <= 1.0

    def test_risk_score_matches_best_plan(self):
        provider = self._provider()
        plans, evals = self._full_set()
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert result.risk_score == pytest.approx(0.10)

    def test_packet_actions_built_correctly(self):
        provider = self._provider()
        plans = [make_plan("baseline", pids=["p1", "p2", "p3"])]
        evals = [make_evaluation("baseline")]
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert len(result.packet_actions) == 3
        assert result.packet_actions[0]["packet_id"] == "p1"
        assert result.packet_actions[0]["rank"] == 1
        assert result.packet_actions[0]["action"] == "transmit"

    def test_evidence_cites_only_real_fields(self):
        """All evidence fields must be in the known citeable set."""
        from backend.app.agent.granite_agent import _ALL_CITEABLE_FIELDS
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        for item in result.evidence:
            assert item.field in _ALL_CITEABLE_FIELDS, (
                f"Evidence cites unknown field '{item.field}'"
            )

    def test_raises_ai_provider_error_when_no_evaluations(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        with pytest.raises(AIProviderError):
            provider.recommend(make_link_state(), make_mission_state(), plans, [])

    def test_raises_ai_provider_error_when_no_plans(self):
        provider = self._provider()
        evals = [make_evaluation("baseline")]
        with pytest.raises(AIProviderError):
            provider.recommend(make_link_state(), make_mission_state(), [], evals)

    def test_raises_ai_response_error_when_evals_dont_match_plans(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        # Evaluation references a plan_id that doesn't exist in plans
        evals = [make_evaluation("nonexistent-plan")]
        with pytest.raises(AIResponseError):
            provider.recommend(make_link_state(), make_mission_state(), plans, evals)

    def test_single_plan_has_no_alternative(self):
        """With only one plan, alternative_plan_id must be None."""
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert result.alternative_plan_id is None

    def test_tie_broken_by_mission_value(self):
        """When two plans have equal risk_score, pick higher mission_value."""
        provider = self._provider()
        plans = [make_plan("plan-a"), make_plan("plan-b")]
        evals = [
            make_evaluation("plan-a", risk_score=0.2, mission_value=1.0),
            make_evaluation("plan-b", risk_score=0.2, mission_value=2.0),
        ]
        result = provider.recommend(make_link_state(), make_mission_state(), plans, evals)
        assert result.recommended_plan_id == "plan-b"

    def test_deterministic_output_same_inputs(self):
        """Same inputs must produce identical recommendations."""
        provider = self._provider()
        plans, evals = self._full_set()
        link = make_link_state()
        mission = make_mission_state()
        r1 = provider.recommend(link, mission, plans, evals)
        r2 = provider.recommend(link, mission, plans, evals)
        assert r1.recommended_plan_id == r2.recommended_plan_id
        assert r1.confidence == pytest.approx(r2.confidence)
        assert r1.risk_score == pytest.approx(r2.risk_score)


# ---------------------------------------------------------------------------
# _confidence_from_gap helper
# ---------------------------------------------------------------------------

class TestConfidenceFromGap:
    def test_zero_gap_gives_half_confidence(self):
        assert _confidence_from_gap(0.2, 0.2) == pytest.approx(0.5)

    def test_large_gap_gives_high_confidence(self):
        c = _confidence_from_gap(0.1, 0.9)
        assert c >= 0.90

    def test_confidence_capped_at_0_95(self):
        c = _confidence_from_gap(0.0, 1.0)
        assert c <= 0.95

    def test_confidence_always_in_range(self):
        for a, b in [(0.0, 0.0), (0.0, 1.0), (0.5, 0.5), (0.3, 0.7)]:
            c = _confidence_from_gap(a, b)
            assert 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# GraniteProvider — exception mapping
# ---------------------------------------------------------------------------

class TestGraniteProvider:
    def test_maps_granite_api_error_to_ai_provider_error(self):
        from backend.app.agent.granite_agent import GraniteAPIError
        mock_agent = MagicMock()
        mock_agent.recommend.side_effect = GraniteAPIError("no key")
        provider = GraniteProvider(agent=mock_agent)
        with pytest.raises(AIProviderError):
            provider.recommend(
                make_link_state(), make_mission_state(),
                [make_plan("baseline")], [make_evaluation("baseline")]
            )

    def test_maps_granite_response_error_to_ai_response_error(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        mock_agent = MagicMock()
        mock_agent.recommend.side_effect = GraniteResponseError("bad json")
        provider = GraniteProvider(agent=mock_agent)
        with pytest.raises(AIResponseError):
            provider.recommend(
                make_link_state(), make_mission_state(),
                [make_plan("baseline")], [make_evaluation("baseline")]
            )

    def test_maps_hallucination_error_to_ai_hallucination_error(self):
        from backend.app.agent.granite_agent import EvidenceHallucinationError
        mock_agent = MagicMock()
        mock_agent.recommend.side_effect = EvidenceHallucinationError("fake field")
        provider = GraniteProvider(agent=mock_agent)
        with pytest.raises(AIHallucinationError):
            provider.recommend(
                make_link_state(), make_mission_state(),
                [make_plan("baseline")], [make_evaluation("baseline")]
            )

    def test_passes_through_recommendation_on_success(self):
        from backend.app.models.evidence_item import EvidenceItem
        expected = AIRecommendation(
            recommended_plan_id="baseline",
            packet_actions=[],
            reasoning="test",
            confidence=0.9,
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            evidence=[EvidenceItem(source="link_state", field="ber", value=1e-6, interpretation="ok")],
            alternative_plan_id=None,
        )
        mock_agent = MagicMock()
        mock_agent.recommend.return_value = expected
        provider = GraniteProvider(agent=mock_agent)
        result = provider.recommend(
            make_link_state(), make_mission_state(),
            [make_plan("baseline")], [make_evaluation("baseline")]
        )
        assert result is expected

    def test_provider_name_is_granite(self):
        assert GraniteProvider().provider_name == "Granite"


# ---------------------------------------------------------------------------
# OllamaProvider — response parsing
# ---------------------------------------------------------------------------

class TestOllamaProviderParsing:
    def _provider(self) -> OllamaProvider:
        return OllamaProvider(base_url="http://localhost:11434", model="llama3.2")

    def _plans(self) -> list[CandidatePlan]:
        return [make_plan("baseline"), make_plan("deadline-first")]

    def test_valid_response_returns_ai_recommendation(self):
        provider = self._provider()
        result = provider._parse_response(_valid_ollama_response("baseline"), self._plans())
        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "baseline"

    def test_unknown_plan_id_raises_ai_response_error(self):
        provider = self._provider()
        with pytest.raises(AIResponseError):
            provider._parse_response(
                _valid_ollama_response("nonexistent-plan"), self._plans()
            )

    def test_invalid_json_raises_ai_response_error(self):
        provider = self._provider()
        with pytest.raises(AIResponseError):
            provider._parse_response("not json", self._plans())

    def test_missing_fields_raises_ai_response_error(self):
        provider = self._provider()
        with pytest.raises(AIResponseError):
            provider._parse_response(
                json.dumps({"recommended_plan_id": "baseline"}), self._plans()
            )

    def test_invalid_risk_level_raises_ai_response_error(self):
        provider = self._provider()
        data = json.loads(_valid_ollama_response())
        data["risk_level"] = "SUPER_LOW"
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), self._plans())

    def test_hallucinated_evidence_field_raises_ai_hallucination_error(self):
        provider = self._provider()
        data = json.loads(_valid_ollama_response())
        data["evidence"].append({
            "source": "link_state", "field": "invented_rf_metric",
            "value": 42, "interpretation": "made up",
        })
        with pytest.raises(AIHallucinationError):
            provider._parse_response(json.dumps(data), self._plans())

    def test_invalid_alternative_plan_id_raises_ai_response_error(self):
        provider = self._provider()
        data = json.loads(_valid_ollama_response("baseline"))
        data["alternative_plan_id"] = "does-not-exist"
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), self._plans())

    def test_confidence_above_1_raises_ai_response_error(self):
        provider = self._provider()
        data = json.loads(_valid_ollama_response())
        data["confidence"] = 1.5
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), self._plans())

    def test_risk_score_below_0_raises_ai_response_error(self):
        provider = self._provider()
        data = json.loads(_valid_ollama_response())
        data["risk_score"] = -0.1
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), self._plans())

    def test_markdown_fenced_json_parsed(self):
        provider = self._provider()
        fenced = f"```json\n{_valid_ollama_response()}\n```"
        result = provider._parse_response(fenced, self._plans())
        assert isinstance(result, AIRecommendation)

    def test_server_unreachable_raises_ai_provider_error(self):
        """If Ollama server is not running, _call_api raises AIProviderError."""
        provider = OllamaProvider(
            base_url="http://localhost:19999",  # port nobody is listening on
            model="test",
            timeout_s=2.0,
        )
        with pytest.raises(AIProviderError):
            provider._call_api("test prompt")

    def test_provider_name_includes_model(self):
        provider = OllamaProvider(model="llama3.2")
        assert "llama3.2" in provider.provider_name


# ---------------------------------------------------------------------------
# Integration: POST /agent/recommend  (module-level async — asyncio_mode=auto)
#
# These are module-level async functions, NOT methods inside a class.
# asyncio_mode=auto in pyproject.toml picks them up without @pytest.mark.asyncio.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommend_returns_200_without_granite_key():
    """POST /agent/recommend must succeed without GCSI_GRANITE_API_KEY."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/agent/recommend")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recommend_response_has_provider_field():
    """Response must include the 'provider' field."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/agent/recommend")
    body = resp.json()
    assert "provider" in body
    assert isinstance(body["provider"], str)
    assert len(body["provider"]) > 0


@pytest.mark.asyncio
async def test_recommend_response_has_recommendation_field():
    """Response must include the 'recommendation' field with full AIRecommendation."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/agent/recommend")
    rec = resp.json()["recommendation"]
    assert "recommended_plan_id" in rec
    assert "reasoning" in rec
    assert "confidence" in rec
    assert "risk_score" in rec
    assert "risk_level" in rec
    assert "evidence" in rec
    assert 0.0 <= rec["confidence"] <= 1.0
    assert 0.0 <= rec["risk_score"] <= 1.0


@pytest.mark.asyncio
async def test_recommend_provider_is_local_without_key():
    """Without an API key, the provider must be 'Local'."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/agent/recommend")
    assert resp.json()["provider"] == "Local"


@pytest.mark.asyncio
async def test_recommend_recommended_plan_id_is_valid():
    """recommended_plan_id must be one of the four generated plan IDs."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    valid_ids = {"baseline", "deadline-first", "mission-critical-first", "value-per-cost"}
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/agent/recommend")
    assert resp.json()["recommendation"]["recommended_plan_id"] in valid_ids


@pytest.mark.asyncio
async def test_recommend_returns_503_before_scenario_load():
    """Must return 503 when no scenario is loaded."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/agent/recommend")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_approve_simulation_flow_without_granite():
    """Full flow: recommend → approve → simulation works without GCSI_GRANITE_API_KEY."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")
    with patch.dict(os.environ, {"GCSI_GRANITE_API_KEY": "", "GCSI_OLLAMA_ENABLED": "false"}):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            rec_resp = await client.post("/agent/recommend")
            assert rec_resp.status_code == 200
            plan_id = rec_resp.json()["recommendation"]["recommended_plan_id"]

            approve_resp = await client.post("/approve", json={
                "plan_id": plan_id,
                "operator_notes": "Approved in test",
            })
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] == "approved"
    assert "elapsed_time_s" in body["simulation_result"]
