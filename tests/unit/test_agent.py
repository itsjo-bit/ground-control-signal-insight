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

    def _evals(self) -> list[EvaluationResult]:
        return [make_evaluation("baseline"), make_evaluation("deadline-first")]

    def test_valid_response_returns_ai_recommendation(self):
        agent = self._agent()
        result = agent._parse_response(_valid_response("baseline"), self._plans(), self._evals())
        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "baseline"

    def test_risk_level_comes_from_evaluation(self):
        """risk_level in the output must come from the matching EvaluationResult, not Granite."""
        agent = self._agent()
        result = agent._parse_response(_valid_response(), self._plans(), self._evals())
        # make_evaluation() produces risk_level=LOW — that is the authoritative value.
        assert result.risk_level == RiskLevel.LOW

    def test_evidence_items_present(self):
        agent = self._agent()
        result = agent._parse_response(_valid_response(), self._plans(), self._evals())
        assert len(result.evidence) == 2

    def test_packet_actions_built_from_recommended_plan(self):
        agent = self._agent()
        plans = [make_plan("baseline", pids=["p1", "p2", "p3"])]
        evals = [make_evaluation("baseline")]
        resp = json.dumps({
            "recommended_plan_id": "baseline",
            "reasoning": "test",
            "confidence": 0.9,
            "risk_score": 0.2,
            "risk_level": "LOW",
            "evidence": [],
            "alternative_plan_id": None,
        })
        result = agent._parse_response(resp, plans, evals)
        assert len(result.packet_actions) == 3
        assert result.packet_actions[0]["packet_id"] == "p1"
        assert result.packet_actions[0]["rank"] == 1

    def test_invalid_json_raises_response_error(self):
        agent = self._agent()
        with pytest.raises(GraniteResponseError):
            agent._parse_response("not json", self._plans(), self._evals())

    def test_missing_field_raises_response_error(self):
        agent = self._agent()
        data = {"recommended_plan_id": "baseline"}  # many fields missing
        with pytest.raises(GraniteResponseError):
            agent._parse_response(json.dumps(data), self._plans(), self._evals())

    def test_unknown_plan_id_raises_response_error(self):
        agent = self._agent()
        resp = _valid_response("nonexistent-plan-id")
        with pytest.raises(GraniteResponseError):
            agent._parse_response(resp, self._plans(), self._evals())

    def test_invalid_risk_level_in_granite_response_is_irrelevant(self):
        """Granite's risk_level field is ignored (eval is authoritative).
        An invalid enum string in Granite's response does NOT raise — it is discarded."""
        agent = self._agent()
        data = json.loads(_valid_response())
        data["risk_level"] = "UNKNOWN_LEVEL"
        # The field is present in the JSON so 'missing fields' check passes.
        # risk_level from Granite is never parsed; eval provides it.
        result = agent._parse_response(json.dumps(data), self._plans(), self._evals())
        assert result.risk_level == RiskLevel.LOW  # from make_evaluation()

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
            agent._parse_response(json.dumps(data), self._plans(), self._evals())

    def test_markdown_fenced_json_is_parsed(self):
        """Agent output may include ```json ``` fences — these should be stripped."""
        agent = self._agent()
        fenced = f"```json\n{_valid_response()}\n```"
        result = agent._parse_response(fenced, self._plans(), self._evals())
        assert isinstance(result, AIRecommendation)

    def test_trailing_analysis_tag_is_ignored(self):
        """Granite 4 sometimes appends </analysis> after the JSON object.
        The parser must accept this without raising JSONDecodeError."""
        agent = self._agent()
        raw = _valid_response("baseline") + "\n</analysis>"
        result = agent._parse_response(raw, self._plans(), self._evals())
        assert isinstance(result, AIRecommendation)
        assert result.recommended_plan_id == "baseline"

    def test_invalid_alternative_plan_id_raises_response_error(self):
        """alternative_plan_id must be None or a known plan_id."""
        agent = self._agent()
        data = json.loads(_valid_response("baseline"))
        data["alternative_plan_id"] = "invented-plan-xyz"
        with pytest.raises(GraniteResponseError, match="alternative_plan_id"):
            agent._parse_response(json.dumps(data), self._plans(), self._evals())

    def test_none_alternative_plan_id_is_accepted(self):
        """alternative_plan_id=None must pass validation."""
        agent = self._agent()
        data = json.loads(_valid_response("baseline"))
        data["alternative_plan_id"] = None
        result = agent._parse_response(json.dumps(data), self._plans(), self._evals())
        assert result.alternative_plan_id is None

    def test_valid_alternative_plan_id_is_accepted(self):
        """alternative_plan_id pointing to a real plan must pass."""
        agent = self._agent()
        plans = [make_plan("baseline"), make_plan("deadline-first")]
        evals = [make_evaluation("baseline"), make_evaluation("deadline-first")]
        data = json.loads(_valid_response("baseline"))
        data["alternative_plan_id"] = "deadline-first"
        result = agent._parse_response(json.dumps(data), plans, evals)
        assert result.alternative_plan_id == "deadline-first"

    def test_confidence_above_1_raises_response_error(self):
        """confidence > 1.0 from Granite must raise GraniteResponseError, not an unhandled 500."""
        agent = self._agent()
        data = json.loads(_valid_response())
        data["confidence"] = 1.5
        with pytest.raises(GraniteResponseError):
            agent._parse_response(json.dumps(data), self._plans(), self._evals())

    def test_confidence_below_0_raises_response_error(self):
        """confidence < 0.0 from Granite must raise GraniteResponseError."""
        agent = self._agent()
        data = json.loads(_valid_response())
        data["confidence"] = -0.1
        with pytest.raises(GraniteResponseError):
            agent._parse_response(json.dumps(data), self._plans(), self._evals())

    def test_out_of_range_granite_risk_score_is_ignored(self):
        """risk_score > 1.0 in Granite's response is silently ignored.
        The authoritative risk_score comes from EvaluationResult, which is always valid."""
        agent = self._agent()
        data = json.loads(_valid_response())
        data["risk_score"] = 1.1  # Granite hallucinated an out-of-range value
        result = agent._parse_response(json.dumps(data), self._plans(), self._evals())
        assert result.risk_score == pytest.approx(0.1)  # from make_evaluation()

    def test_negative_granite_risk_score_is_ignored(self):
        """risk_score < 0.0 in Granite's response is silently ignored.
        The authoritative risk_score comes from EvaluationResult."""
        agent = self._agent()
        data = json.loads(_valid_response())
        data["risk_score"] = -0.5  # Granite hallucinated a negative value
        result = agent._parse_response(json.dumps(data), self._plans(), self._evals())
        assert result.risk_score == pytest.approx(0.1)  # from make_evaluation()


# ---------------------------------------------------------------------------
# Deterministic risk binding — EvaluationResult is authoritative
# ---------------------------------------------------------------------------

def make_evaluation_with_risk(plan_id: str, risk_score: float, risk_level: RiskLevel) -> EvaluationResult:
    """Build an EvaluationResult with explicit risk values for binding tests."""
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=1.0,
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


class TestRiskBindingToEvaluation:
    """EvaluationResult is the sole authority for risk_score and risk_level.

    Whatever Granite returns in those fields is discarded.  The output must
    always reflect the deterministic evaluation of the recommended plan.
    """

    def _agent(self) -> GraniteAgent:
        return GraniteAgent(api_key="dummy")

    def _plans(self) -> list[CandidatePlan]:
        return [make_plan("baseline"), make_plan("deadline-first")]

    def test_granite_risk_score_overridden_by_eval(self):
        """Granite returns risk_score=0.99 but eval says 0.12 → output must be 0.12."""
        evals = [
            make_evaluation_with_risk("baseline", risk_score=0.12, risk_level=RiskLevel.LOW),
            make_evaluation_with_risk("deadline-first", risk_score=0.5, risk_level=RiskLevel.MEDIUM),
        ]
        data = json.loads(_valid_response("baseline"))
        data["risk_score"] = 0.99   # Granite's hallucinated value
        data["risk_level"] = "CRITICAL"
        result = self._agent()._parse_response(json.dumps(data), self._plans(), evals)
        assert result.risk_score == pytest.approx(0.12)

    def test_granite_risk_level_overridden_by_eval(self):
        """Granite returns CRITICAL but eval says LOW → output must be LOW."""
        evals = [
            make_evaluation_with_risk("baseline", risk_score=0.12, risk_level=RiskLevel.LOW),
            make_evaluation_with_risk("deadline-first", risk_score=0.5, risk_level=RiskLevel.MEDIUM),
        ]
        data = json.loads(_valid_response("baseline"))
        data["risk_score"] = 0.99
        data["risk_level"] = "CRITICAL"
        result = self._agent()._parse_response(json.dumps(data), self._plans(), evals)
        assert result.risk_level == RiskLevel.LOW

    def test_granite_risk_values_entirely_ignored(self):
        """Any combination of risk_score / risk_level from Granite is irrelevant."""
        evals = [
            make_evaluation_with_risk("baseline", risk_score=0.55, risk_level=RiskLevel.HIGH),
            make_evaluation_with_risk("deadline-first", risk_score=0.1, risk_level=RiskLevel.LOW),
        ]
        data = json.loads(_valid_response("baseline"))
        data["risk_score"] = 0.01   # would be LOW if trusted
        data["risk_level"] = "LOW"
        result = self._agent()._parse_response(json.dumps(data), self._plans(), evals)
        assert result.risk_score == pytest.approx(0.55)
        assert result.risk_level == RiskLevel.HIGH

    def test_no_matching_evaluation_raises_response_error(self):
        """If no EvaluationResult exists for recommended_plan_id, raise GraniteResponseError."""
        # Evaluations exist but none match "baseline"
        evals = [make_evaluation_with_risk("deadline-first", 0.1, RiskLevel.LOW)]
        with pytest.raises(GraniteResponseError, match="No EvaluationResult"):
            self._agent()._parse_response(
                _valid_response("baseline"), self._plans(), evals
            )

    def test_empty_evaluations_raises_response_error(self):
        """An empty evaluations list must raise GraniteResponseError."""
        with pytest.raises(GraniteResponseError, match="No EvaluationResult"):
            self._agent()._parse_response(
                _valid_response("baseline"), self._plans(), []
            )

    def test_mission_risk_cannot_bleed_into_recommendation(self):
        """mission_state.risk_score=0.8 (CRITICAL) must not appear in the recommendation.

        The recommendation risk comes solely from the recommended plan's eval.
        """
        # Eval for "baseline" has low risk — simulating a good plan in a bad mission context
        evals = [
            make_evaluation_with_risk("baseline", risk_score=0.20, risk_level=RiskLevel.LOW),
            make_evaluation_with_risk("deadline-first", risk_score=0.5, risk_level=RiskLevel.MEDIUM),
        ]
        # Simulate Granite copying mission_state.risk_score=0.8 into its response
        data = json.loads(_valid_response("baseline"))
        data["risk_score"] = 0.8    # mission risk, not plan risk
        data["risk_level"] = "CRITICAL"
        result = self._agent()._parse_response(json.dumps(data), self._plans(), evals)
        assert result.risk_score == pytest.approx(0.20)
        assert result.risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# API unavailability — missing credentials
# ---------------------------------------------------------------------------

class TestAPIUnavailable:
    def test_raises_api_error_when_no_key(self, monkeypatch):
        # Blank env so the constructor cannot fall back to the real key.
        monkeypatch.setenv("GCSI_GRANITE_API_KEY", "")
        agent = GraniteAgent(api_key="", project_id="proj-abc")
        with pytest.raises(GraniteAPIError, match="GCSI_GRANITE_API_KEY"):
            agent._call_api("hello")

    def test_raises_api_error_when_no_project_id(self, monkeypatch):
        """Missing project_id must raise GraniteAPIError before any HTTP call."""
        monkeypatch.setenv("GCSI_GRANITE_PROJECT_ID", "")
        agent = GraniteAgent(api_key="dummy-key", project_id="")
        with pytest.raises(GraniteAPIError, match="GCSI_GRANITE_PROJECT_ID"):
            agent._call_api("hello")

    def test_recommend_raises_api_error_when_no_key(self, monkeypatch):
        monkeypatch.setenv("GCSI_GRANITE_API_KEY", "")
        agent = GraniteAgent(api_key="", project_id="proj-abc")
        with pytest.raises(GraniteAPIError):
            agent.recommend(
                make_link_state(),
                make_mission_state(),
                [make_plan("baseline")],
                [make_evaluation("baseline")],
            )

    def test_recommend_raises_api_error_when_no_project_id(self, monkeypatch):
        """Calling recommend without project_id must raise GraniteAPIError."""
        monkeypatch.setenv("GCSI_GRANITE_PROJECT_ID", "")
        agent = GraniteAgent(api_key="dummy-key", project_id="")
        with pytest.raises(GraniteAPIError, match="GCSI_GRANITE_PROJECT_ID"):
            agent.recommend(
                make_link_state(),
                make_mission_state(),
                [make_plan("baseline")],
                [make_evaluation("baseline")],
            )


# ---------------------------------------------------------------------------
# GraniteAgent configuration — project_id, URL versioning, payload shape
# ---------------------------------------------------------------------------

class TestGraniteAgentConfig:
    def test_project_id_read_from_env(self, monkeypatch):
        """project_id is read from GCSI_GRANITE_PROJECT_ID when not passed directly."""
        monkeypatch.setenv("GCSI_GRANITE_PROJECT_ID", "env-project-abc")
        monkeypatch.setenv("GCSI_GRANITE_API_KEY", "dummy")
        agent = GraniteAgent()
        assert agent._project_id == "env-project-abc"

    def test_project_id_passed_directly_takes_priority(self, monkeypatch):
        """Explicit project_id arg overrides GCSI_GRANITE_PROJECT_ID env var."""
        monkeypatch.setenv("GCSI_GRANITE_PROJECT_ID", "env-project-ignored")
        agent = GraniteAgent(api_key="dummy", project_id="direct-project-xyz")
        assert agent._project_id == "direct-project-xyz"

    def test_default_url_contains_version_param(self):
        """Default API URL must contain ?version= after construction."""
        agent = GraniteAgent(api_key="dummy", project_id="p")
        assert "version=" in agent._api_url

    def test_url_with_existing_version_is_not_duplicated(self):
        """If the caller supplies a URL that already has ?version=, it must not be added twice."""
        url_with_version = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation?version=2023-05-29"
        agent = GraniteAgent(api_url=url_with_version, api_key="dummy", project_id="p")
        assert agent._api_url.count("version=") == 1

    def test_url_without_version_gets_version_appended(self):
        """A URL without ?version= must have it added by _ensure_version_param."""
        url_no_version = "https://us-south.ml.cloud.ibm.com/ml/v1/text/generation"
        agent = GraniteAgent(api_url=url_no_version, api_key="dummy", project_id="p")
        assert "version=" in agent._api_url
        assert agent._api_url.count("version=") == 1

    def test_ensure_version_param_adds_version(self):
        """_ensure_version_param adds version to a bare URL."""
        from backend.app.agent.granite_agent import _ensure_version_param
        result = _ensure_version_param("https://example.com/ml/v1/text/generation")
        assert "version=2023-05-29" in result

    def test_ensure_version_param_does_not_duplicate(self):
        """_ensure_version_param leaves an existing version untouched."""
        from backend.app.agent.granite_agent import _ensure_version_param
        url = "https://example.com/ml/v1/text/generation?version=2024-01-01"
        result = _ensure_version_param(url)
        assert result.count("version=") == 1
        assert "version=2024-01-01" in result

    def test_payload_contains_project_id(self, monkeypatch):
        """The JSON payload sent to Granite must include the project_id field."""
        import httpx

        captured_payload: dict = {}
        _IAM_URL = "https://iam.cloud.ibm.com/identity/token"

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):  # noqa: ARG001
            if url == _IAM_URL:
                return httpx.Response(
                    200,
                    json={"access_token": "test-iam-token", "expires_in": 3600},
                )
            captured_payload.update(json or {})
            return httpx.Response(
                200,
                json={"results": [{"generated_text": "{}"}]},
            )

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = GraniteAgent(api_key="dummy-key", project_id="test-project-999")
        # _call_api will build and "send" the payload; it will then fail to parse
        # the empty "{}" response — that's fine, we only care about the payload.
        try:
            agent._call_api("test message")
        except Exception:  # noqa: BLE001
            pass
        assert captured_payload.get("project_id") == "test-project-999"

    def test_payload_contains_model_id(self, monkeypatch):
        """The JSON payload must include model_id."""
        import httpx

        captured_payload: dict = {}
        _IAM_URL = "https://iam.cloud.ibm.com/identity/token"

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):  # noqa: ARG001
            if url == _IAM_URL:
                return httpx.Response(
                    200,
                    json={"access_token": "test-iam-token", "expires_in": 3600},
                )
            captured_payload.update(json or {})
            return httpx.Response(200, json={"results": [{"generated_text": "{}"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = GraniteAgent(api_key="dummy-key", project_id="p", model_id="ibm/granite-test")
        try:
            agent._call_api("test")
        except Exception:  # noqa: BLE001
            pass
        assert captured_payload.get("model_id") == "ibm/granite-test"

    def test_project_id_not_exposed_in_api_error_message(self):
        """GraniteAPIError must not echo the project_id value in its message."""
        secret_project_id = "super-secret-project-id-do-not-log"
        agent = GraniteAgent(api_key="", project_id=secret_project_id)
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._call_api("hello")
        # The error should NOT contain the project_id value
        assert secret_project_id not in str(exc_info.value)

    def test_api_key_not_exposed_in_api_error_message(self):
        """GraniteAPIError for missing project_id must not echo the API key."""
        secret_key = "super-secret-api-key-do-not-log"
        agent = GraniteAgent(api_key=secret_key, project_id="")
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._call_api("hello")
        assert secret_key not in str(exc_info.value)


# ---------------------------------------------------------------------------
# IAM token exchange and caching
# ---------------------------------------------------------------------------


class TestIAMTokenExchange:
    """Tests for _get_iam_token and the _IAMTokenCache.

    All tests mock the IAM HTTP endpoint so no real credentials are needed.
    """

    _IAM_URL = "https://iam.cloud.ibm.com/identity/token"
    _FAKE_TOKEN = "fake-iam-access-token-xyz"

    def _agent(self, iam_url: str | None = None) -> GraniteAgent:
        return GraniteAgent(
            api_key="test-api-key",
            project_id="test-project",
            iam_url=iam_url or self._IAM_URL,
        )

    def _make_iam_resp(self, token: str = None, expires_in: int = 3600):
        import httpx
        return httpx.Response(
            200,
            json={"access_token": token or self._FAKE_TOKEN, "expires_in": expires_in},
        )

    # --- token is exchanged correctly ------------------------------------------

    def test_api_key_is_exchanged_for_iam_token(self, monkeypatch):
        """_get_iam_token must POST to the IAM URL and return the access_token."""
        import httpx
        call_count = {"n": 0}

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            call_count["n"] += 1
            assert url == self._IAM_URL
            return self._make_iam_resp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        token = agent._get_iam_token()
        assert token == self._FAKE_TOKEN
        assert call_count["n"] == 1

    def test_iam_request_uses_form_encoding(self, monkeypatch):
        """IAM request must use Content-Type: application/x-www-form-urlencoded."""
        import httpx
        captured_headers: dict = {}

        def fake_post(self_client, url, *, data=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                captured_headers.update(headers or {})
                return self._make_iam_resp()
            return httpx.Response(200, json={"results": [{"generated_text": "{}"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        agent._get_iam_token()
        assert captured_headers.get("Content-Type") == "application/x-www-form-urlencoded"

    def test_iam_request_body_contains_grant_type_and_apikey(self, monkeypatch):
        """IAM request body must contain grant_type and apikey fields."""
        import httpx
        captured_data: list[dict] = []

        def fake_post(self_client, url, *, data=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                if data:
                    captured_data.append(dict(data))
                return self._make_iam_resp()
            return httpx.Response(200, json={"results": [{"generated_text": "{}"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        agent._get_iam_token()
        assert captured_data, "IAM request data was empty"
        body = captured_data[0]
        assert "grant_type" in body
        assert "apikey" in body
        assert body["apikey"] == "test-api-key"
        assert "apikey" in body["grant_type"]

    # --- watsonx request uses IAM token, not raw API key -----------------------

    def test_watsonx_request_uses_iam_access_token_not_api_key(self, monkeypatch):
        """Authorization header to watsonx must be the IAM access_token, not the API key."""
        import httpx
        captured_auth: list[str] = []

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                return self._make_iam_resp()
            if headers:
                captured_auth.append(headers.get("Authorization", ""))
            return httpx.Response(200, json={"results": [{"generated_text": "{}"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        try:
            agent._call_api("test")
        except Exception:  # noqa: BLE001
            pass
        assert captured_auth, "No Authorization header captured from watsonx call"
        assert captured_auth[0] == f"Bearer {self._FAKE_TOKEN}"
        # Must NOT use the raw API key
        assert "test-api-key" not in captured_auth[0]

    # --- caching ---------------------------------------------------------------

    def test_iam_token_is_cached_between_requests(self, monkeypatch):
        """Second call to _get_iam_token must not issue a new HTTP request."""
        import httpx
        call_count = {"n": 0}

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            call_count["n"] += 1
            return self._make_iam_resp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        t1 = agent._get_iam_token()
        t2 = agent._get_iam_token()
        assert t1 == t2 == self._FAKE_TOKEN
        assert call_count["n"] == 1  # only one IAM call

    def test_expired_token_triggers_refresh(self, monkeypatch):
        """A cached token that is past expiry must be re-fetched."""
        import httpx
        call_count = {"n": 0}

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            call_count["n"] += 1
            return self._make_iam_resp(token=f"token-{call_count['n']}")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        # Store a token that expires immediately (expires_in=0 → already past buffer).
        agent._iam_cache.store("old-token", expires_in=0)
        # Cache should be expired now.
        assert agent._iam_cache.get() is None
        # Next call must fetch a fresh token.
        new_token = agent._get_iam_token()
        assert new_token == "token-1"
        assert call_count["n"] == 1

    def test_near_expiry_token_triggers_refresh(self, monkeypatch):
        """Token within the refresh buffer window must be treated as expired."""
        import httpx
        from backend.app.agent.granite_agent import _IAM_REFRESH_BUFFER_S
        call_count = {"n": 0}

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            call_count["n"] += 1
            return self._make_iam_resp(token=f"refreshed-{call_count['n']}")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        # Store a token that expires in exactly _IAM_REFRESH_BUFFER_S seconds
        # (i.e. right at the boundary — the cache subtracts the buffer, so it
        # stores expires_at = now + 0, which means get() returns None).
        agent._iam_cache.store("near-expiry-token", expires_in=int(_IAM_REFRESH_BUFFER_S))
        assert agent._iam_cache.get() is None
        new_token = agent._get_iam_token()
        assert new_token == "refreshed-1"

    def test_invalidate_forces_refresh_on_next_call(self, monkeypatch):
        """After invalidate(), the next _get_iam_token must re-fetch."""
        import httpx
        call_count = {"n": 0}

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            call_count["n"] += 1
            return self._make_iam_resp(token=f"fresh-{call_count['n']}")

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        t1 = agent._get_iam_token()
        assert call_count["n"] == 1
        agent._iam_cache.invalidate()
        t2 = agent._get_iam_token()
        assert call_count["n"] == 2
        assert t1 != t2

    # --- IAM failure mapping ---------------------------------------------------

    def test_iam_failure_raises_granite_api_error(self, monkeypatch):
        """A non-200 from the IAM endpoint must raise GraniteAPIError."""
        import httpx

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            return httpx.Response(400, json={"errorCode": "BXNIM0415E"})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        with pytest.raises(GraniteAPIError):
            agent._get_iam_token()

    def test_iam_failure_message_does_not_contain_api_key(self, monkeypatch):
        """GraniteAPIError from IAM failure must not include the API key."""
        import httpx

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            return httpx.Response(401, json={"errorMessage": "Bad API key"})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._get_iam_token()
        assert "test-api-key" not in str(exc_info.value)

    def test_watsonx_401_invalidates_cache_and_raises(self, monkeypatch):
        """A 401 from watsonx.ai must invalidate the IAM token cache and raise GraniteAPIError."""
        import httpx

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                return self._make_iam_resp()
            return httpx.Response(401, json={"errors": [{"message": "token not valid"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._call_api("test")
        assert "401" in str(exc_info.value)
        # Cache must be invalidated after a 401.
        assert agent._iam_cache.get() is None

    def test_watsonx_401_error_does_not_expose_credentials(self, monkeypatch):
        """The GraniteAPIError from watsonx 401 must not contain API key or IAM token."""
        import httpx

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                return self._make_iam_resp(token="secret-iam-token-do-not-log")
            return httpx.Response(401, json={"errors": [{"message": "auth failed"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._call_api("test")
        error_msg = str(exc_info.value)
        assert "test-api-key" not in error_msg
        assert "secret-iam-token-do-not-log" not in error_msg

    def test_watsonx_403_raises_with_permission_hint(self, monkeypatch):
        """A 403 from watsonx.ai must raise GraniteAPIError mentioning project permissions."""
        import httpx

        def fake_post(self_client, url, *, json=None, content=None, headers=None, **kw):
            if url == self._IAM_URL:
                return self._make_iam_resp()
            return httpx.Response(403, json={"errors": [{"message": "forbidden"}]})

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = self._agent()
        with pytest.raises(GraniteAPIError) as exc_info:
            agent._call_api("test")
        assert "403" in str(exc_info.value)

    # --- GCSI_GRANITE_IAM_URL config -------------------------------------------

    def test_iam_url_can_be_overridden(self, monkeypatch):
        """Custom GCSI_GRANITE_IAM_URL must be used instead of the default."""
        import httpx
        custom_iam_url = "https://custom-iam.example.com/token"
        called_urls: list[str] = []

        def fake_post(self_client, url, *, content=None, headers=None, **kw):
            called_urls.append(url)
            return self._make_iam_resp()

        monkeypatch.setattr(httpx.Client, "post", fake_post)
        agent = GraniteAgent(
            api_key="test-key",
            project_id="test-project",
            iam_url=custom_iam_url,
        )
        agent._get_iam_token()
        assert custom_iam_url in called_urls

    def test_iam_url_read_from_env(self, monkeypatch):
        """GCSI_GRANITE_IAM_URL env var must be picked up when no explicit iam_url is passed."""
        monkeypatch.setenv("GCSI_GRANITE_IAM_URL", "https://env-iam.example.com/token")
        agent = GraniteAgent(api_key="k", project_id="p")
        assert agent._iam_url == "https://env-iam.example.com/token"


# ---------------------------------------------------------------------------
# Live Granite tests — skipped unless both credentials are set
# ---------------------------------------------------------------------------

_has_api_key = bool(os.getenv("GCSI_GRANITE_API_KEY"))
_has_project_id = bool(os.getenv("GCSI_GRANITE_PROJECT_ID"))
_can_run_live = _has_api_key and _has_project_id


@pytest.mark.granite
@pytest.mark.skipif(
    not _can_run_live,
    reason="GCSI_GRANITE_API_KEY and/or GCSI_GRANITE_PROJECT_ID not set",
)
def test_live_granite_returns_recommendation():
    """Integration test: calls real Granite API. Only runs with both credentials set."""
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

    # GraniteAgent() reads GCSI_GRANITE_API_KEY, GCSI_GRANITE_PROJECT_ID,
    # GCSI_GRANITE_API_URL, and GCSI_GRANITE_MODEL_ID from the environment.
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
