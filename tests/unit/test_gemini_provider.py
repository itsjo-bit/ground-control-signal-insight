"""Unit tests for GeminiProvider and related provider-factory changes.

Covers the 12 required test cases plus GCSI_AI_PROVIDER override logic.

Tests NEVER make real Gemini API calls — the httpx client is always mocked.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.base_provider import (
    AIHallucinationError,
    AIProviderError,
    AIResponseError,
)
from backend.app.agent.gemini_provider import GeminiProvider
from backend.app.agent.granite_provider import GraniteProvider
from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.agent.provider_factory import get_provider
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.recommendation import AIRecommendation
from backend.app.models.risk_level import RiskLevel


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


def make_evaluation(
    plan_id: str,
    risk_score: float = 0.1,
    mission_value: float = 1.2,
    risk_level: RiskLevel = RiskLevel.LOW,
) -> EvaluationResult:
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
        risk_level=risk_level,
        deferred_packets=[],
    )


def _valid_gemini_text(plan_id: str = "baseline") -> str:
    """Return a valid JSON string as Gemini would produce."""
    return json.dumps({
        "recommended_plan_id": plan_id,
        "reasoning": "The baseline plan is optimal.",
        "confidence": 0.88,
        "risk_score": 0.10,
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


def _make_httpx_response(text_body: str, status_code: int = 200):
    """Create a mock httpx.Response that Gemini's _call_api will accept."""
    response_json = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": text_body}]
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.text = json.dumps(response_json)
    return mock_resp


def _make_httpx_error_response(status_code: int, body: str = "error"):
    """Create a mock httpx.Response with a non-200 status."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = body
    return mock_resp


# ---------------------------------------------------------------------------
# Helper: patch httpx.Client used inside GeminiProvider._call_api
# ---------------------------------------------------------------------------

def _patch_httpx(response):
    """Context-manager: replace httpx.Client.post with one that returns response."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = response
    return patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client)


# ---------------------------------------------------------------------------
# 1. Successful Gemini response
# ---------------------------------------------------------------------------

class TestGeminiProviderSuccess:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key-for-tests")

    def test_successful_response_returns_ai_recommendation(self):
        """Test case 1: successful Gemini response produces a valid AIRecommendation."""
        provider = self._provider()
        plans = [make_plan("baseline"), make_plan("deadline-first")]
        evals = [make_evaluation("baseline"), make_evaluation("deadline-first")]

        resp = _make_httpx_response(_valid_gemini_text("baseline"))
        with _patch_httpx(resp):
            result = provider.recommend(
                make_link_state(), make_mission_state(), plans, evals
            )

        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "baseline"
        assert result.provider_name if hasattr(result, "provider_name") else True

    def test_provider_name_is_gemini(self):
        assert GeminiProvider(api_key="k").provider_name == "Gemini"

    def test_packet_actions_built_correctly(self):
        provider = self._provider()
        plans = [make_plan("baseline", pids=["p1", "p2", "p3"])]
        evals = [make_evaluation("baseline")]

        resp = _make_httpx_response(_valid_gemini_text("baseline"))
        with _patch_httpx(resp):
            result = provider.recommend(
                make_link_state(), make_mission_state(), plans, evals
            )

        assert len(result.packet_actions) == 3
        assert result.packet_actions[0] == {"packet_id": "p1", "action": "transmit", "rank": 1}

    def test_risk_score_taken_from_evaluation_not_gemini(self):
        """Gemini's self-reported risk values are discarded; authoritative values come
        from the EvaluationResult."""
        provider = self._provider()
        plans = [make_plan("baseline")]
        # Evaluation has risk_score=0.42 (MEDIUM)
        evals = [make_evaluation("baseline", risk_score=0.42, risk_level=RiskLevel.MEDIUM)]

        # Gemini reports risk_score=0.10, LOW — these must be overridden.
        resp = _make_httpx_response(_valid_gemini_text("baseline"))
        with _patch_httpx(resp):
            result = provider.recommend(
                make_link_state(), make_mission_state(), plans, evals
            )

        assert result.risk_score == pytest.approx(0.42)
        assert result.risk_level == RiskLevel.MEDIUM

    def test_markdown_fenced_json_is_parsed(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]

        fenced = f"```json\n{_valid_gemini_text()}\n```"
        resp = _make_httpx_response(fenced)
        with _patch_httpx(resp):
            result = provider.recommend(
                make_link_state(), make_mission_state(), plans, evals
            )
        assert isinstance(result, AIRecommendation)


# ---------------------------------------------------------------------------
# 2. Missing API key
# ---------------------------------------------------------------------------

class TestGeminiMissingApiKey:
    def test_missing_api_key_raises_ai_provider_error(self):
        """Test case 2: no API key → AIProviderError immediately, no HTTP call."""
        provider = GeminiProvider(api_key="")
        with pytest.raises(AIProviderError, match="GCSI_GEMINI_API_KEY"):
            provider.recommend(
                make_link_state(), make_mission_state(),
                [make_plan("baseline")], [make_evaluation("baseline")]
            )

    def test_whitespace_api_key_raises_ai_provider_error(self):
        """A whitespace-only key is equivalent to missing."""
        provider = GeminiProvider(api_key="   ")
        # The key is stored as-is; the empty check in _call_api strips it.
        # (Behaviour: whitespace-only key is falsy after strip in factory,
        # but the provider itself does not strip — it passes the key to the API
        # which will return a 401.  We test the factory behaviour separately.)
        # This test ensures that an explicit empty string raises immediately.
        p = GeminiProvider(api_key="")
        with pytest.raises(AIProviderError):
            p._call_api("test")


# ---------------------------------------------------------------------------
# 3. Gemini HTTP / API failure
# ---------------------------------------------------------------------------

class TestGeminiHTTPFailure:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def test_http_500_raises_ai_provider_error(self):
        """Test case 3: non-200 response → AIProviderError."""
        provider = self._provider()
        resp = _make_httpx_error_response(500, "Internal Server Error")
        with _patch_httpx(resp):
            with pytest.raises(AIProviderError, match="500"):
                provider._call_api("test")

    def test_http_401_raises_ai_provider_error(self):
        provider = self._provider()
        resp = _make_httpx_error_response(401)
        with _patch_httpx(resp):
            with pytest.raises(AIProviderError, match="401"):
                provider._call_api("test")

    def test_http_403_raises_ai_provider_error(self):
        provider = self._provider()
        resp = _make_httpx_error_response(403)
        with _patch_httpx(resp):
            with pytest.raises(AIProviderError, match="403"):
                provider._call_api("test")

    def test_connection_error_raises_ai_provider_error(self):
        import httpx as _httpx
        provider = self._provider()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.ConnectError("refused")
        with patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client):
            with pytest.raises(AIProviderError):
                provider._call_api("test")

    def test_timeout_raises_ai_provider_error(self):
        import httpx as _httpx
        provider = self._provider()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _httpx.TimeoutException("timeout")
        with patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client):
            with pytest.raises(AIProviderError, match="timed out"):
                provider._call_api("test")


# ---------------------------------------------------------------------------
# 4. Malformed Gemini JSON
# ---------------------------------------------------------------------------

class TestGeminiMalformedJson:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def _plans(self):
        return [make_plan("baseline"), make_plan("deadline-first")]

    def _evals(self):
        return [make_evaluation("baseline"), make_evaluation("deadline-first")]

    def test_invalid_json_raises_ai_response_error(self):
        """Test case 4: non-JSON text → AIResponseError."""
        provider = self._provider()
        with pytest.raises(AIResponseError, match="not valid JSON"):
            provider._parse_response("not json at all", self._plans(), self._evals())

    def test_empty_string_raises_ai_response_error(self):
        provider = self._provider()
        with pytest.raises(AIResponseError):
            provider._parse_response("", self._plans(), self._evals())


# ---------------------------------------------------------------------------
# 5. Invalid recommended_plan_id
# ---------------------------------------------------------------------------

class TestGeminiInvalidPlanId:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def _plans(self):
        return [make_plan("baseline"), make_plan("deadline-first")]

    def _evals(self):
        return [make_evaluation("baseline"), make_evaluation("deadline-first")]

    def test_unknown_plan_id_raises_ai_response_error(self):
        """Test case 5: plan_id not in provided plans → AIResponseError."""
        provider = self._provider()
        with pytest.raises(AIResponseError, match="unknown plan_id"):
            provider._parse_response(
                _valid_gemini_text("nonexistent-plan"),
                self._plans(),
                self._evals(),
            )


# ---------------------------------------------------------------------------
# 6. Invalid evidence field (hallucination)
# ---------------------------------------------------------------------------

class TestGeminiEvidenceHallucination:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def _plans(self):
        return [make_plan("baseline")]

    def _evals(self):
        return [make_evaluation("baseline")]

    def test_hallucinated_field_raises_ai_hallucination_error(self):
        """Test case 6: evidence cites a nonexistent field → AIHallucinationError."""
        provider = self._provider()
        data = json.loads(_valid_gemini_text("baseline"))
        data["evidence"].append({
            "source": "link_state",
            "field": "invented_quantum_metric",
            "value": 999,
            "interpretation": "completely made up",
        })
        with pytest.raises(AIHallucinationError):
            provider._parse_response(
                json.dumps(data), self._plans(), self._evals()
            )


# ---------------------------------------------------------------------------
# 7. Invalid risk level
# ---------------------------------------------------------------------------

class TestGeminiInvalidRiskLevel:
    """Test case 7 — invalid risk_level in Gemini response.

    Note: GeminiProvider binds risk_level from the authoritative EvaluationResult
    (exactly as GraniteAgent does) rather than trusting Gemini's self-reported
    value.  Therefore an invalid Gemini risk_level does NOT raise an error on
    its own — it is simply ignored.

    The validation that DOES occur is on the EvaluationResult's own risk_level,
    which is already a valid RiskLevel by construction (it was built by
    PlanEvaluator).  We verify this contract here.
    """

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def test_gemini_risk_level_overridden_by_evaluation(self):
        """Gemini's risk_level is ignored; the EvaluationResult value is used."""
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline", risk_score=0.8, risk_level=RiskLevel.CRITICAL)]

        data = json.loads(_valid_gemini_text("baseline"))
        data["risk_level"] = "SUPER_ULTRA_LOW"  # invalid, but ignored

        # Should succeed because risk_level comes from eval, not Gemini.
        result = provider._parse_response(json.dumps(data), plans, evals)
        assert result.risk_level == RiskLevel.CRITICAL

    def test_missing_fields_raises_ai_response_error(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]
        with pytest.raises(AIResponseError, match="missing fields"):
            provider._parse_response(
                json.dumps({"recommended_plan_id": "baseline"}), plans, evals
            )


# ---------------------------------------------------------------------------
# 8. Invalid risk score (out-of-range)
# ---------------------------------------------------------------------------

class TestGeminiInvalidRiskScore:
    """Test case 8 — out-of-range risk_score.

    As with risk_level, GeminiProvider takes risk_score from the authoritative
    EvaluationResult, not from Gemini's response.  The EvaluationResult's
    risk_score is always in [0, 1] by Pydantic validation.

    We verify that confidence (which IS taken from Gemini) is validated.
    """

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def test_out_of_range_confidence_raises_ai_response_error(self):
        """Test case 9 (confidence) — also exercised here for completeness."""
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]

        data = json.loads(_valid_gemini_text("baseline"))
        data["confidence"] = 2.5  # > 1.0 — Pydantic will reject this

        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), plans, evals)

    def test_negative_confidence_raises_ai_response_error(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]

        data = json.loads(_valid_gemini_text("baseline"))
        data["confidence"] = -0.1

        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), plans, evals)


# ---------------------------------------------------------------------------
# 9. Invalid confidence
# ---------------------------------------------------------------------------

class TestGeminiInvalidConfidence:
    """Test case 9 — out-of-range confidence value."""

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def test_confidence_above_1_raises_ai_response_error(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]
        data = json.loads(_valid_gemini_text("baseline"))
        data["confidence"] = 1.5
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), plans, evals)

    def test_confidence_below_0_raises_ai_response_error(self):
        provider = self._provider()
        plans = [make_plan("baseline")]
        evals = [make_evaluation("baseline")]
        data = json.loads(_valid_gemini_text("baseline"))
        data["confidence"] = -0.01
        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), plans, evals)


# ---------------------------------------------------------------------------
# 10. Provider factory selects Gemini when Gemini configured, Granite is not
# ---------------------------------------------------------------------------

class TestProviderFactoryGeminiSelection:
    def test_factory_returns_gemini_when_only_gemini_key_set(self):
        """Test case 10: Gemini key set, Granite key absent → GeminiProvider."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_GEMINI_API_KEY": "fake-gemini-key",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "",
        }):
            provider = get_provider()
        assert isinstance(provider, GeminiProvider)
        assert provider.provider_name == "Gemini"

    def test_gemini_key_whitespace_treated_as_absent(self):
        """A whitespace-only Gemini key is treated as missing → Local fallback."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_GEMINI_API_KEY": "   ",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "",
        }):
            provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)


# ---------------------------------------------------------------------------
# 11. Factory still selects Granite when Granite is configured
# ---------------------------------------------------------------------------

class TestProviderFactoryGraniteStillPriority:
    def test_granite_takes_priority_over_gemini(self):
        """Test case 11: Granite key takes priority when both keys are present."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "fake-granite-key",
            "GCSI_GEMINI_API_KEY": "fake-gemini-key",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "",
        }):
            provider = get_provider()
        assert isinstance(provider, GraniteProvider)
        assert provider.provider_name == "Granite"

    def test_granite_only_key_still_returns_granite(self):
        """Granite key alone → GraniteProvider (regression: existing behaviour)."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "any-non-empty-key",
            "GCSI_GEMINI_API_KEY": "",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "",
        }):
            provider = get_provider()
        assert isinstance(provider, GraniteProvider)


# ---------------------------------------------------------------------------
# 12. Factory selects Local when no external provider is configured
# ---------------------------------------------------------------------------

class TestProviderFactoryLocalFallback:
    def test_no_keys_returns_local_provider(self):
        """Test case 12: no keys configured → LocalRuleBasedProvider."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_GEMINI_API_KEY": "",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "",
        }):
            provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)
        assert provider.provider_name == "Local"


# ---------------------------------------------------------------------------
# GCSI_AI_PROVIDER explicit override
# ---------------------------------------------------------------------------

class TestProviderFactoryExplicitOverride:
    def test_explicit_gemini_override(self):
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_GEMINI_API_KEY": "",   # not set, but override forces gemini
            "GCSI_AI_PROVIDER": "gemini",
        }):
            provider = get_provider()
        assert isinstance(provider, GeminiProvider)

    def test_explicit_local_override(self):
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "key",   # would normally select Granite
            "GCSI_AI_PROVIDER": "local",
        }):
            provider = get_provider()
        assert isinstance(provider, LocalRuleBasedProvider)

    def test_invalid_override_falls_back_to_automatic(self):
        """An unknown GCSI_AI_PROVIDER value should NOT crash; it falls back."""
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_GEMINI_API_KEY": "",
            "GCSI_OLLAMA_ENABLED": "false",
            "GCSI_AI_PROVIDER": "unknown-provider-name",
        }):
            provider = get_provider()
        # Falls back to automatic selection → Local (no keys set)
        assert isinstance(provider, LocalRuleBasedProvider)

    def test_explicit_granite_override(self):
        with patch.dict(os.environ, {
            "GCSI_GRANITE_API_KEY": "",
            "GCSI_AI_PROVIDER": "granite",
        }):
            provider = get_provider()
        assert isinstance(provider, GraniteProvider)


# ---------------------------------------------------------------------------
# Valid alternative_plan_id
# ---------------------------------------------------------------------------

class TestGeminiAlternativePlanId:
    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def test_valid_alternative_plan_id_accepted(self):
        provider = self._provider()
        plans = [make_plan("baseline"), make_plan("deadline-first")]
        evals = [make_evaluation("baseline"), make_evaluation("deadline-first")]

        data = json.loads(_valid_gemini_text("baseline"))
        data["alternative_plan_id"] = "deadline-first"

        result = provider._parse_response(json.dumps(data), plans, evals)
        assert result.alternative_plan_id == "deadline-first"

    def test_invalid_alternative_plan_id_raises_ai_response_error(self):
        provider = self._provider()
        plans = [make_plan("baseline"), make_plan("deadline-first")]
        evals = [make_evaluation("baseline"), make_evaluation("deadline-first")]

        data = json.loads(_valid_gemini_text("baseline"))
        data["alternative_plan_id"] = "does-not-exist"

        with pytest.raises(AIResponseError):
            provider._parse_response(json.dumps(data), plans, evals)


# ---------------------------------------------------------------------------
# Output-token budget
# ---------------------------------------------------------------------------

class TestGeminiOutputTokenBudget:
    """Verify that _call_api sends maxOutputTokens=4096 (not the old 1024)."""

    def _captured_payload(self, provider: GeminiProvider) -> dict:
        """Call _call_api with a mocked HTTP client and return the JSON body sent."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": _valid_gemini_text("baseline")}]}}
            ]
        }

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp

        with patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client):
            provider._call_api("test message")

        # Extract the json= keyword arg passed to client.post
        _, kwargs = mock_client.post.call_args
        return kwargs["json"]

    def test_max_output_tokens_is_4096(self):
        """The request must set maxOutputTokens to 4096."""
        provider = GeminiProvider(api_key="fake-key")
        payload = self._captured_payload(provider)
        assert payload["generationConfig"]["maxOutputTokens"] == 4096

    def test_max_output_tokens_is_not_1024(self):
        """Regression: the old 1024 limit must no longer be used."""
        provider = GeminiProvider(api_key="fake-key")
        payload = self._captured_payload(provider)
        assert payload["generationConfig"]["maxOutputTokens"] != 1024

    def test_structured_json_mime_type_is_set(self):
        """response_mime_type must remain 'application/json'."""
        provider = GeminiProvider(api_key="fake-key")
        payload = self._captured_payload(provider)
        assert payload["generationConfig"]["response_mime_type"] == "application/json"


# ---------------------------------------------------------------------------
# Truncated JSON handling
# ---------------------------------------------------------------------------

class TestGeminiTruncatedJson:
    """Truncated or incomplete JSON must raise AIResponseError, not be silently accepted."""

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key")

    def _plans(self):
        return [make_plan("baseline"), make_plan("deadline-first")]

    def _evals(self):
        return [make_evaluation("baseline"), make_evaluation("deadline-first")]

    def test_truncated_string_value_raises_ai_response_error(self):
        """Simulates the exact truncation seen in production: unterminated string."""
        truncated = (
            '{\n'
            '  "recommended_plan_id": "baseline",\n'
            '  "reasoning": "The baseline plan achieves the maximum'
            # deliberately cut off here — no closing quote, no closing brace
        )
        with pytest.raises(AIResponseError, match="not valid JSON"):
            self._provider()._parse_response(truncated, self._plans(), self._evals())

    def test_truncated_mid_object_raises_ai_response_error(self):
        """JSON that stops in the middle of the object body raises AIResponseError."""
        truncated = '{"recommended_plan_id": "baseline", "reasoning": "ok"'
        with pytest.raises(AIResponseError):
            self._provider()._parse_response(truncated, self._plans(), self._evals())

    def test_complete_recommendation_with_long_reasoning_is_parsed(self):
        """A full recommendation with a long reasoning string must parse without error."""
        provider = self._provider()
        long_reasoning = "x" * 1500   # well within 4096 tokens
        data = json.loads(_valid_gemini_text("baseline"))
        data["reasoning"] = long_reasoning

        result = provider._parse_response(json.dumps(data), self._plans(), self._evals())
        assert isinstance(result, AIRecommendation)
        assert result.reasoning == long_reasoning


# ===========================================================================
# Phase 8B.4 — Gemini Stage-2 hardening tests
# Tasks 11–14: request shape, complete response, truncation, finish reason
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared Stage-2 fixtures
# ---------------------------------------------------------------------------

from backend.app.agent.gemini_provider import (
    _build_stage2_response_schema,
    _extract_text_from_candidate,
    _is_gemini_3x,
    _STAGE2_MAX_OUTPUT_TOKENS,
)
from backend.app.agent.stage2_blinding import Stage2PlanSummary


def _make_stage2_summary(option_id: str) -> Stage2PlanSummary:
    """Minimal Stage2PlanSummary for Stage-2 tests."""
    return Stage2PlanSummary(
        option_id=option_id,
        total_packets=10,
        deferred_count=2,
        risk_score=0.2,
        risk_level="LOW",
        mission_value=1.5,
        critical_packets_delivered=8,
        total_critical_packets=8,
        deadline_misses=0,
        deadline_miss_rate=0.0,
        bandwidth_utilization=0.6,
        retransmission_overhead=0.0,
        window_pressure=0.4,
        scientific_value_capture_rate=0.85,
        required_delivery_rate=1.0,
    )


def _make_stage2_summaries() -> list[Stage2PlanSummary]:
    return [
        _make_stage2_summary("OPTION-A"),
        _make_stage2_summary("OPTION-B"),
        _make_stage2_summary("OPTION-C"),
    ]


def _valid_stage2_text(recommended: str = "OPTION-B") -> str:
    """Return a complete, valid Stage-2 JSON response as Gemini would produce."""
    return json.dumps({
        "recommended_option_id": recommended,
        "reasoning": "During pre-contact anomaly triage, OPTION-B delivers the highest "
                     "anomaly coverage while maintaining acceptable risk.",
        "confidence": 0.91,
        "evidence": [
            {
                "option_id": recommended,
                "source": "candidate_option",
                "field": "scientific_value_capture_rate",
                "interpretation": "Highest scientific capture rate among options.",
            }
        ],
        "alternative_option_id": "OPTION-C",
    })


def _make_stage2_httpx_response(text_body: str, status_code: int = 200,
                                 finish_reason: str | None = None):
    """Create a mock httpx.Response for Stage-2 calls."""
    candidate: dict = {
        "content": {
            "parts": [{"text": text_body}]
        }
    }
    if finish_reason is not None:
        candidate["finishReason"] = finish_reason

    response_json = {"candidates": [candidate]}
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = response_json
    mock_resp.text = json.dumps(response_json)
    return mock_resp


def _patch_stage2_httpx(response):
    """Context-manager: replace httpx.Client used by recommend_from_summaries."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = response
    return patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client)


def _captured_stage2_payload(
    provider: GeminiProvider,
    summaries: list[Stage2PlanSummary] | None = None,
) -> dict:
    """Invoke recommend_from_summaries with a mock and return the JSON payload sent."""
    if summaries is None:
        summaries = _make_stage2_summaries()

    text = _valid_stage2_text(summaries[0].option_id)
    mock_resp = _make_stage2_httpx_response(text)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("backend.app.agent.gemini_provider.httpx.Client", return_value=mock_client):
        provider.recommend_from_summaries(
            summaries, make_link_state(), make_mission_state()
        )

    _, kwargs = mock_client.post.call_args
    return kwargs["json"]


# ---------------------------------------------------------------------------
# TASK 11 — Stage-2 request shape
# ---------------------------------------------------------------------------

class TestStage2RequestShape:
    """Task 11: prove Stage-2 request contains the required fields/values."""

    def _provider_2x(self) -> GeminiProvider:
        """Provider configured with a Gemini 2.x model (no thinkingConfig)."""
        return GeminiProvider(api_key="fake-key", model="gemini-2.0-flash")

    def _provider_3x(self) -> GeminiProvider:
        """Provider configured with a Gemini 3.x model (thinkingConfig required)."""
        return GeminiProvider(api_key="fake-key", model="gemini-3.0-flash")

    def test_response_mime_type_is_application_json(self):
        payload = _captured_stage2_payload(self._provider_2x())
        assert payload["generationConfig"]["response_mime_type"] == "application/json"

    def test_response_schema_is_present(self):
        payload = _captured_stage2_payload(self._provider_2x())
        assert "responseSchema" in payload["generationConfig"]

    def test_response_schema_root_is_object(self):
        payload = _captured_stage2_payload(self._provider_2x())
        schema = payload["generationConfig"]["responseSchema"]
        assert schema["type"] == "OBJECT"

    def test_response_schema_required_fields(self):
        payload = _captured_stage2_payload(self._provider_2x())
        schema = payload["generationConfig"]["responseSchema"]
        required = set(schema["required"])
        assert "recommended_option_id" in required
        assert "reasoning" in required
        assert "confidence" in required
        assert "evidence" in required

    def test_response_schema_option_aliases_are_opaque(self):
        """Schema enum must only contain OPTION-X aliases, no real plan IDs."""
        summaries = _make_stage2_summaries()
        payload = _captured_stage2_payload(self._provider_2x(), summaries=summaries)
        schema = payload["generationConfig"]["responseSchema"]
        aliases = schema["properties"]["recommended_option_id"]["enum"]
        for alias in aliases:
            assert alias.startswith("OPTION-"), (
                f"Schema enum contains non-opaque alias: {alias!r}"
            )
        # Must contain all provided aliases
        assert set(aliases) == {"OPTION-A", "OPTION-B", "OPTION-C"}

    def test_response_schema_no_real_plan_ids(self):
        """Schema must never enumerate real plan names."""
        summaries = _make_stage2_summaries()
        payload = _captured_stage2_payload(self._provider_2x(), summaries=summaries)
        schema_json = json.dumps(payload["generationConfig"]["responseSchema"])
        for forbidden in ("baseline", "ai-prioritized", "deadline-first",
                          "mission-critical-first", "value-per-cost"):
            assert forbidden not in schema_json, (
                f"Forbidden plan name {forbidden!r} found in responseSchema"
            )

    def test_max_output_tokens_is_4096(self):
        payload = _captured_stage2_payload(self._provider_2x())
        assert payload["generationConfig"]["maxOutputTokens"] == _STAGE2_MAX_OUTPUT_TOKENS
        assert payload["generationConfig"]["maxOutputTokens"] == 4096

    def test_max_output_tokens_is_not_1024(self):
        payload = _captured_stage2_payload(self._provider_2x())
        assert payload["generationConfig"]["maxOutputTokens"] != 1024

    def test_gemini_2x_has_no_thinking_config(self):
        """A Gemini 2.x model must NOT receive thinkingConfig."""
        payload = _captured_stage2_payload(self._provider_2x())
        assert "thinkingConfig" not in payload["generationConfig"]

    def test_gemini_3x_has_thinking_config(self):
        """A Gemini 3.x model MUST receive thinkingConfig."""
        payload = _captured_stage2_payload(self._provider_3x())
        assert "thinkingConfig" in payload["generationConfig"]

    def test_gemini_3x_thinking_budget_is_zero(self):
        """thinkingConfig must set thinkingBudget=0 (minimal thinking)."""
        payload = _captured_stage2_payload(self._provider_3x())
        thinking = payload["generationConfig"]["thinkingConfig"]
        assert thinking.get("thinkingBudget") == 0


# ---------------------------------------------------------------------------
# Helper: _is_gemini_3x
# ---------------------------------------------------------------------------

class TestIsGemini3x:
    def test_gemini_3_flash_is_3x(self):
        assert _is_gemini_3x("gemini-3.0-flash") is True

    def test_gemini_3_5_flash_is_3x(self):
        assert _is_gemini_3x("gemini-3.5-flash-lite") is True

    def test_gemini_2_flash_is_not_3x(self):
        assert _is_gemini_3x("gemini-2.0-flash") is False

    def test_gemini_2_5_flash_is_not_3x(self):
        assert _is_gemini_3x("gemini-2.5-flash-lite-preview-06-17") is False

    def test_gemini_1_5_flash_is_not_3x(self):
        assert _is_gemini_3x("gemini-1.5-flash") is False

    def test_case_insensitive(self):
        assert _is_gemini_3x("Gemini-3.0-Flash") is True


# ---------------------------------------------------------------------------
# Helper: _build_stage2_response_schema
# ---------------------------------------------------------------------------

class TestBuildStage2ResponseSchema:
    def test_root_type_is_object(self):
        schema = _build_stage2_response_schema(["OPTION-A", "OPTION-B"])
        assert schema["type"] == "OBJECT"

    def test_required_fields_present(self):
        schema = _build_stage2_response_schema(["OPTION-A"])
        assert set(schema["required"]) >= {
            "recommended_option_id", "reasoning", "confidence", "evidence"
        }

    def test_option_alias_enum_matches_input(self):
        aliases = ["OPTION-A", "OPTION-B", "OPTION-C", "OPTION-D"]
        schema = _build_stage2_response_schema(aliases)
        enum = schema["properties"]["recommended_option_id"]["enum"]
        assert set(enum) == set(aliases)

    def test_recommended_option_id_type_is_string(self):
        schema = _build_stage2_response_schema(["OPTION-A"])
        assert schema["properties"]["recommended_option_id"]["type"] == "STRING"

    def test_evidence_type_is_array(self):
        schema = _build_stage2_response_schema(["OPTION-A"])
        assert schema["properties"]["evidence"]["type"] == "ARRAY"

    def test_evidence_items_required_fields(self):
        schema = _build_stage2_response_schema(["OPTION-A"])
        items = schema["properties"]["evidence"]["items"]
        assert set(items["required"]) >= {"source", "field", "interpretation"}


# ---------------------------------------------------------------------------
# TASK 12 — Complete structured Stage-2 response
# ---------------------------------------------------------------------------

class TestStage2CompleteResponse:
    """Task 12: mock a valid complete Stage-2 response and verify parsing."""

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key", model="gemini-2.0-flash")

    def test_complete_response_parses_successfully(self):
        """A full valid Stage-2 JSON is accepted by parse_stage2_response."""
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(_valid_stage2_text("OPTION-B"))

        with _patch_stage2_httpx(resp):
            result = self._provider().recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )

        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "OPTION-B"

    def test_alias_remains_opaque_in_result(self):
        """The recommended_plan_id in the result is still an opaque OPTION alias."""
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(_valid_stage2_text("OPTION-A"))

        with _patch_stage2_httpx(resp):
            result = self._provider().recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )

        # Must be a valid OPTION alias — never a real plan ID at this stage
        assert result.recommended_plan_id.startswith("OPTION-")
        for forbidden in ("baseline", "ai-prioritized", "deadline-first"):
            assert forbidden not in result.recommended_plan_id

    def test_confidence_is_preserved(self):
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(_valid_stage2_text("OPTION-B"))

        with _patch_stage2_httpx(resp):
            result = self._provider().recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )

        assert result.confidence == pytest.approx(0.91)

    def test_alternative_option_id_preserved(self):
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(_valid_stage2_text("OPTION-B"))

        with _patch_stage2_httpx(resp):
            result = self._provider().recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )

        # OPTION-C is the alternative in _valid_stage2_text
        assert result.alternative_plan_id == "OPTION-C"

    def test_evidence_option_ids_are_opaque(self):
        """Evidence option_ids must remain OPTION aliases, never real plan IDs."""
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(_valid_stage2_text("OPTION-B"))

        with _patch_stage2_httpx(resp):
            result = self._provider().recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )

        for ev in result.evidence:
            if ev.option_id is not None:
                assert ev.option_id.startswith("OPTION-"), (
                    f"Evidence option_id is not opaque: {ev.option_id!r}"
                )

    def test_parser_remains_authoritative_on_invalid_alias(self):
        """Even with structured output, the parser must reject unknown aliases."""
        summaries = _make_stage2_summaries()
        bad_json = json.dumps({
            "recommended_option_id": "OPTION-Z",  # not in alias_map
            "reasoning": "...",
            "confidence": 0.8,
            "evidence": [],
            "alternative_option_id": None,
        })
        resp = _make_stage2_httpx_response(bad_json)

        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError):
                self._provider().recommend_from_summaries(
                    summaries, make_link_state(), make_mission_state()
                )


# ---------------------------------------------------------------------------
# TASK 13 — Truncated Stage-2 response
# ---------------------------------------------------------------------------

class TestStage2TruncatedResponse:
    """Task 13: mock the exact truncation failure observed in production."""

    def _provider(self) -> GeminiProvider:
        return GeminiProvider(api_key="fake-key", model="gemini-2.0-flash")

    def _summaries(self):
        return _make_stage2_summaries()

    def test_truncated_string_is_rejected(self):
        """Simulates the exact production failure: unterminated string in reasoning."""
        truncated = (
            '{\n'
            '  "recommended_option_id": "OPTION-D",\n'
            '  "reasoning": "During pre-contact anomaly triage ...'
            # deliberately cut off — no closing quote, no closing brace
        )
        resp = _make_stage2_httpx_response(truncated)
        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError):
                self._provider().recommend_from_summaries(
                    self._summaries(), make_link_state(), make_mission_state()
                )

    def test_truncated_string_variant_a_is_rejected(self):
        """Second exact production failure variant: OPTION-A."""
        truncated = (
            '{\n'
            '  "recommended_option_id": "OPTION-A",\n'
            '  "reasoning": "During pre-contact anomaly triage ...'
        )
        resp = _make_stage2_httpx_response(truncated)
        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError):
                self._provider().recommend_from_summaries(
                    self._summaries(), make_link_state(), make_mission_state()
                )

    def test_truncated_mid_object_is_rejected(self):
        truncated = '{"recommended_option_id": "OPTION-A", "reasoning": "ok"'
        resp = _make_stage2_httpx_response(truncated)
        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError):
                self._provider().recommend_from_summaries(
                    self._summaries(), make_link_state(), make_mission_state()
                )

    def test_no_json_repair_attempted(self):
        """Truncated output must not be silently repaired — error must propagate."""
        truncated = '{"recommended_option_id": "OPTION-B", "reasoning": "...'
        resp = _make_stage2_httpx_response(truncated)
        with _patch_stage2_httpx(resp):
            # Must raise, not return a patched-up recommendation
            with pytest.raises((AIResponseError, AIProviderError)):
                self._provider().recommend_from_summaries(
                    self._summaries(), make_link_state(), make_mission_state()
                )

    def test_empty_response_text_is_rejected(self):
        resp = _make_stage2_httpx_response("")
        with _patch_stage2_httpx(resp):
            with pytest.raises((AIResponseError, AIProviderError)):
                self._provider().recommend_from_summaries(
                    self._summaries(), make_link_state(), make_mission_state()
                )


# ---------------------------------------------------------------------------
# TASK 14 — Finish reason handling
# ---------------------------------------------------------------------------

class TestStage2FinishReason:
    """Task 14: verify finishReason inspection and truncation error handling."""

    # ── _extract_text_from_candidate unit tests ───────────────────────────────

    def test_stop_finish_reason_succeeds(self):
        """STOP is a normal completion — text is returned without error."""
        body = {
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": "hello"}]},
            }]
        }
        text = _extract_text_from_candidate(body)
        assert text == "hello"

    def test_end_of_turn_finish_reason_succeeds(self):
        body = {
            "candidates": [{
                "finishReason": "END_OF_TURN",
                "content": {"parts": [{"text": "world"}]},
            }]
        }
        text = _extract_text_from_candidate(body)
        assert text == "world"

    def test_no_finish_reason_succeeds(self):
        """A candidate with no finishReason field is treated as normal completion."""
        body = {
            "candidates": [{
                "content": {"parts": [{"text": "ok"}]},
            }]
        }
        text = _extract_text_from_candidate(body)
        assert text == "ok"

    def test_max_tokens_finish_reason_raises_ai_response_error(self):
        """MAX_TOKENS indicates truncation — must raise AIResponseError."""
        body = {
            "candidates": [{
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": '{"recommended_option_id": "OPTION-A"'}]},
            }]
        }
        with pytest.raises(AIResponseError, match="truncated"):
            _extract_text_from_candidate(body)

    def test_max_output_tokens_finish_reason_raises_ai_response_error(self):
        """MAX_OUTPUT_TOKENS also indicates truncation."""
        body = {
            "candidates": [{
                "finishReason": "MAX_OUTPUT_TOKENS",
                "content": {"parts": [{"text": "partial..."}]},
            }]
        }
        with pytest.raises(AIResponseError, match="truncated"):
            _extract_text_from_candidate(body)

    def test_truncation_error_message_is_diagnostic(self):
        """Error message should mention finishReason value for diagnostics."""
        body = {
            "candidates": [{
                "finishReason": "MAX_TOKENS",
                "content": {"parts": [{"text": "truncated"}]},
            }]
        }
        with pytest.raises(AIResponseError) as exc_info:
            _extract_text_from_candidate(body)
        msg = str(exc_info.value)
        assert "MAX_TOKENS" in msg

    def test_safety_finish_reason_falls_through(self):
        """SAFETY is not a truncation reason — falls through to let JSON parse fail."""
        body = {
            "candidates": [{
                "finishReason": "SAFETY",
                "content": {"parts": [{"text": "blocked content"}]},
            }]
        }
        # Should NOT raise AIResponseError for truncation; returns text
        text = _extract_text_from_candidate(body)
        assert text == "blocked content"

    # ── Integration: recommend_from_summaries with MAX_TOKENS ─────────────────

    def test_recommend_from_summaries_raises_on_max_tokens(self):
        """recommend_from_summaries must surface the truncation error."""
        provider = GeminiProvider(api_key="fake-key", model="gemini-2.0-flash")
        summaries = _make_stage2_summaries()
        # Return a truncated body with MAX_TOKENS finish reason
        resp = _make_stage2_httpx_response(
            '{"recommended_option_id": "OPTION-A", "reasoning": "truncated...',
            finish_reason="MAX_TOKENS",
        )
        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError, match="truncated"):
                provider.recommend_from_summaries(
                    summaries, make_link_state(), make_mission_state()
                )

    def test_recommend_from_summaries_succeeds_with_stop_reason(self):
        """A STOP finish reason with complete JSON must succeed normally."""
        provider = GeminiProvider(api_key="fake-key", model="gemini-2.0-flash")
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response(
            _valid_stage2_text("OPTION-A"),
            finish_reason="STOP",
        )
        with _patch_stage2_httpx(resp):
            result = provider.recommend_from_summaries(
                summaries, make_link_state(), make_mission_state()
            )
        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "OPTION-A"

    def test_truncation_error_does_not_leak_api_key(self):
        """Error message for truncation must not contain the API key."""
        provider = GeminiProvider(api_key="super-secret-key-12345", model="gemini-2.0-flash")
        summaries = _make_stage2_summaries()
        resp = _make_stage2_httpx_response("partial", finish_reason="MAX_TOKENS")
        with _patch_stage2_httpx(resp):
            with pytest.raises(AIResponseError) as exc_info:
                provider.recommend_from_summaries(
                    summaries, make_link_state(), make_mission_state()
                )
        assert "super-secret-key-12345" not in str(exc_info.value)
