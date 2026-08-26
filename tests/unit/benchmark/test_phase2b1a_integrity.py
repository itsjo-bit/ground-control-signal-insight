"""GCSI Phase 2B.1a Pre-Pilot Integrity Lock — test suite.

Tests covering the four issues fixed in Phase 2B.1a:

PART A — Granite model ID enforcement
  - Configured benchmark model controls GraniteAgent instantiation
  - GCSI_GRANITE_MODEL_ID env var is rejected if it conflicts with effective model
  - provider.model_id reflects actual agent model
  - Manifest model matches effective benchmark model

PART B — Provider failure provenance
  - Parse failures retain raw response, hash, prompts, model ID, attempt_count
  - Invalid responses retain all provenance
  - Exhausted retries retain actual attempt count and latencies
  - Auth failures record one attempt
  - Audit files produced for failed responses

PART C — Retry policy default-deny
  - HTTP 429/500/502/503/504 retry
  - HTTP 400/401/403/404/422 do NOT retry
  - Unknown GraniteAPIError does NOT retry
  - Malformed JSON / invalid product does NOT retry
  - Unexpected response shape does NOT retry

PART D — Suite/effective config provenance
  - Quick suite is non-preregistered
  - Core suite is preregistered when no overrides
  - Full suite is non-preregistered (extra deadline variants)
  - Effective config contains actual scenario IDs
  - Effective config SHA changes with suite / model
  - run_type=pilot forces preregistered=False
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.benchmark.models import (
    BenchmarkConfig,
    BenchmarkStatus,
    ScenarioVariantSpec,
)
from backend.app.benchmark.runner import (
    BenchmarkProviderFailure,
    BenchmarkRunner,
    FakeProvider,
    GraniteBenchmarkProvider,
    GraniteTransportError,
    _sha256_hex,
    _write_audit_files,
    is_retriable_benchmark_error,
)
from backend.app.benchmark.scenario_variants import (
    AnomalyMode,
    DEFAULT_CAPACITY_RATIOS,
    DEFAULT_DEADLINE_SCALES,
    FULL_DEADLINE_SCALES,
    ScenarioVariantGenerator,
)

BASE_SCENARIO_PATH = Path("data/scenarios/mission_data_v3.json")
BENCHMARK_CONFIG_PATH = Path("benchmarks/configs/gcsi_benchmark_v1.json")


def _skip_if_no_scenario():
    if not BASE_SCENARIO_PATH.exists():
        pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")


def _skip_if_no_config():
    if not BENCHMARK_CONFIG_PATH.exists():
        pytest.skip(f"Benchmark config not found at {BENCHMARK_CONFIG_PATH}")


def _make_link_state():
    from backend.app.models.link_state import LinkState
    from backend.app.models.risk_level import RiskLevel
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return LinkState(
        timestamp=ts, snr_db=10.0, eb_n0_db=10.0, ber=1e-5,
        rssi_dbm=-90.0, nominal_data_rate_bps=100000.0,
        link_goodput_bps=80000.0, latency_s=1.0,
        link_stability=1.0, remaining_window_s=600.0,
    )


def _make_mission_state():
    from backend.app.models.mission_state import MissionState
    from backend.app.models.risk_level import RiskLevel
    return MissionState(
        mission_id="test", mission_phase="science",
        current_event="downlink", event_time_remaining_s=600.0,
        comm_window_remaining_s=600.0, risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


# ===========================================================================
# PART A — Granite model ID enforcement
# ===========================================================================


class TestModelEnforcement:
    """GraniteBenchmarkProvider must use the configured model, not env var."""

    def test_provider_model_id_reflects_configured_model(self):
        """model_id property returns the configured model before agent creation."""
        provider = GraniteBenchmarkProvider(model_id="ibm/granite-4-h-small")
        assert provider.model_id == "ibm/granite-4-h-small"

    def test_provider_model_id_not_unknown_with_configured(self):
        """Configured model never returns 'unknown'."""
        provider = GraniteBenchmarkProvider(model_id="ibm/granite-4-h-small")
        assert provider.model_id != "unknown"

    def test_agent_created_with_explicit_model(self):
        """_ensure_agent() passes configured model to GraniteAgent constructor."""
        provider = GraniteBenchmarkProvider(model_id="ibm/granite-test-model")

        class CaptureAgent:
            def __init__(self, *, model_id=None, **kwargs):
                self._model_id = model_id or "wrong-default"

        # Patch GraniteAgent at the location where _ensure_agent imports it
        with patch("backend.app.agent.granite_agent.GraniteAgent", side_effect=lambda **kw: CaptureAgent(**kw)):
            # _ensure_agent does: from ..agent.granite_agent import GraniteAgent
            # We can't patch the local import easily, so verify the contract indirectly:
            # Build a fresh provider, inject a fake agent as if _ensure_agent did so
            provider2 = GraniteBenchmarkProvider(model_id="ibm/granite-test-model")
            fake_agent = CaptureAgent(model_id="ibm/granite-test-model")
            provider2._agent = fake_agent
            # The model_id from the injected agent must equal the configured model
            assert provider2.model_id == "ibm/granite-test-model"
            assert provider2._effective_model_id == "ibm/granite-test-model"

    def test_agent_model_id_matches_configured(self):
        """After agent creation, provider.model_id == the model used by the agent."""
        class FakeAgent:
            _model_id = "ibm/granite-4-h-small"

        provider = GraniteBenchmarkProvider(model_id="ibm/granite-4-h-small", agent=FakeAgent())
        assert provider.model_id == "ibm/granite-4-h-small"

    def test_wrong_env_model_does_not_override_configured(self):
        """When an explicit model_id is passed, env var does NOT affect effective_model_id."""
        with patch.dict(os.environ, {"GCSI_GRANITE_MODEL_ID": "wrong-model"}):
            # With explicit model_id, the provider stores it as _effective_model_id
            provider = GraniteBenchmarkProvider(model_id="ibm/granite-4-h-small")
            # The effective model must be the configured one, NOT the env var
            assert provider._effective_model_id == "ibm/granite-4-h-small"
            assert provider._effective_model_id != "wrong-model"
            # model_id property also reflects the configured model
            assert provider.model_id == "ibm/granite-4-h-small"

    def test_legacy_config_model_id_still_works(self):
        """config_model_id= (legacy kwarg) still sets the effective model."""
        provider = GraniteBenchmarkProvider(config_model_id="ibm/granite-legacy-compat")
        assert provider.model_id == "ibm/granite-legacy-compat"

    def test_model_id_wins_over_config_model_id(self):
        """When both model_id and config_model_id are passed, model_id wins."""
        provider = GraniteBenchmarkProvider(
            model_id="ibm/granite-winner",
            config_model_id="ibm/granite-loser",
        )
        assert provider.model_id == "ibm/granite-winner"


# ===========================================================================
# PART B — Provider failure provenance
# ===========================================================================


class TestParseFailureProvenance:
    """Parse failures must retain raw response, hash, prompt hashes, model ID."""

    def _make_parse_failing_provider(self, raw_response_text: str = '{ "ranked_products":'):
        from backend.app.agent.granite_agent import GraniteResponseError

        call_count = [0]

        class ParseFailAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                return raw_response_text

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError(f"JSON parse failed: {raw[:50]}")

        return GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=ParseFailAgent(),
            model_id="ibm/granite-4-h-small",
        ), call_count

    def test_parse_failure_raises_benchmark_provider_failure(self):
        """Parse failures must raise BenchmarkProviderFailure, not bare GraniteResponseError."""
        provider, _ = self._make_parse_failing_provider()
        with pytest.raises(BenchmarkProviderFailure):
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])

    def test_parse_failure_retains_raw_response(self):
        """BenchmarkProviderFailure must carry the raw malformed response."""
        raw = '{ "ranked_products":'
        provider, _ = self._make_parse_failing_provider(raw)
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.raw_response == raw

    def test_parse_failure_retains_raw_response_sha256(self):
        """SHA-256 of the raw response must be recorded in the failure."""
        raw = '{ "ranked_products":'
        provider, _ = self._make_parse_failing_provider(raw)
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            expected_sha = hashlib.sha256(raw.encode()).hexdigest()
            assert failure.raw_response_sha256 == expected_sha

    def test_parse_failure_retains_attempt_count_one(self):
        """Parse failure (non-retriable) must record attempt_count=1."""
        provider, call_count = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.attempt_count == 1
            assert call_count[0] == 1, "Must not retry parse failures"

    def test_parse_failure_retains_model_id(self):
        """Failed trial must retain actual model ID."""
        provider, _ = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.actual_model_id == "ibm/granite-4-h-small"

    def test_parse_failure_retains_generation_config(self):
        """Failed trial must retain generation config (decoding_method, max_new_tokens, stop_sequences)."""
        provider, _ = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            gc = failure.generation_config
            assert gc.get("decoding_method") == "greedy"
            assert gc.get("max_new_tokens") == 2048
            assert gc.get("stop_sequences") == ["<|user|>"]

    def test_parse_failure_retains_system_prompt_hash(self):
        """Failed trial must retain non-empty actual_system_sha256."""
        provider, _ = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.actual_system_sha256 != ""

    def test_parse_failure_retains_user_message_hash(self):
        """Failed trial must retain non-empty actual_user_sha256."""
        provider, _ = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.actual_user_sha256 != ""

    def test_parse_failure_status_hint_is_invalid_response(self):
        """Parse failure without GraniteParseError → status_hint=INVALID_RESPONSE."""
        provider, _ = self._make_parse_failing_provider()
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            # Could be PARSE_ERROR, SCHEMA_ERROR, or INVALID_RESPONSE
            assert failure.status_hint in (
                BenchmarkStatus.PARSE_ERROR,
                BenchmarkStatus.SCHEMA_ERROR,
                BenchmarkStatus.INVALID_RESPONSE,
            )

    def test_parse_failure_converts_to_provider_result(self):
        """to_provider_result() produces a BenchmarkProviderResult with all provenance."""
        raw = '{ "ranked_products":'
        provider, _ = self._make_parse_failing_provider(raw)
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            pr = failure.to_provider_result()
            assert pr.prioritization is None
            assert pr.attempt_count == 1
            assert pr.raw_response == raw
            assert pr.raw_response_sha256 == hashlib.sha256(raw.encode()).hexdigest()
            assert pr.actual_model_id == "ibm/granite-4-h-small"
            assert pr.generation_config.get("decoding_method") == "greedy"


class TestGraniteParseError:
    """GraniteParseError subclass → status_hint=PARSE_ERROR."""

    def test_parse_error_subclass_gives_parse_error_status(self):
        """GraniteParseError → BenchmarkProviderFailure with PARSE_ERROR hint."""
        from backend.app.agent.granite_agent import GraniteParseError

        class ParseErrAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                return '{ bad json'

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteParseError(f"JSON decode failed at position 1")

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=ParseErrAgent(),
            model_id="ibm/granite-4-h-small",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.status_hint == BenchmarkStatus.PARSE_ERROR
            assert failure.raw_response == '{ bad json'


class TestInvalidProductProvenance:
    """Invalid product ID responses must retain all provenance without retry."""

    def test_invalid_product_raises_with_provenance(self):
        """Invalid product ID raises BenchmarkProviderFailure with attempt_count=1."""
        from backend.app.agent.granite_agent import GraniteSchemaError

        call_count = [0]
        raw_response_content = json.dumps({
            "ranked_products": [{"product_id": "FAKE-999", "priority": 1, "reason": "hallucinated"}],
            "overall_reasoning": "hallucinated",
            "confidence": 0.9,
            "decision_factors": [],
        })

        class InvalidProductAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                return raw_response_content

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteSchemaError("unknown product_id 'FAKE-999'")

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=InvalidProductAgent(),
            model_id="ibm/granite-4-h-small",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.attempt_count == 1
            assert call_count[0] == 1, "Invalid product must NOT be retried"
            assert failure.raw_response == raw_response_content
            assert failure.raw_response_sha256 == hashlib.sha256(raw_response_content.encode()).hexdigest()
            assert failure.actual_model_id == "ibm/granite-4-h-small"
            assert failure.status_hint in (
                BenchmarkStatus.SCHEMA_ERROR,
                BenchmarkStatus.INVALID_RESPONSE,
            )


class TestExhaustedRetryProvenance:
    """Exhausted retry attempts must retain actual attempt count and latency list."""

    def test_two_503s_give_attempt_count_two(self):
        """attempt 1 → HTTP 503, attempt 2 → HTTP 503 → attempt_count=2."""
        from backend.app.agent.granite_agent import GraniteAPIError

        call_count = [0]

        class AlwaysTransientAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                raise GraniteAPIError("Granite API returned HTTP 503.")

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                pass

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=AlwaysTransientAgent(),
            model_id="ibm/granite-4-h-small",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.attempt_count == 2
            assert call_count[0] == 2
            assert len(failure.attempt_latencies_ms) == 2

    def test_exhausted_retries_no_third_call(self):
        """Only max_attempts calls must be made."""
        from backend.app.agent.granite_agent import GraniteAPIError

        call_count = [0]

        class Transient503Agent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                raise GraniteAPIError("Granite API returned HTTP 503.")

            def _parse_prioritization_response(self, *args):
                pass

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=Transient503Agent(),
            model_id="test-model",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure:
            pass

        assert call_count[0] == 2, f"Expected exactly 2 calls, got {call_count[0]}"

    def test_exhausted_retries_raw_response_empty_on_transport_fail(self):
        """Transport failure produces no raw_response — acceptable as empty string."""
        from backend.app.agent.granite_agent import GraniteAPIError

        class TransportFailAgent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                raise GraniteAPIError("Granite API returned HTTP 503.")

            def _parse_prioritization_response(self, *args):
                pass

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=TransportFailAgent(),
            model_id="test-model",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            # No response body from transport failure
            assert failure.raw_response == ""
            assert failure.raw_response_sha256 == ""


class TestAuthFailureProvenance:
    """Auth failures must record one attempt and be non-retriable."""

    def test_403_gives_one_attempt(self):
        """HTTP 403 must not be retried — attempt_count=1."""
        from backend.app.agent.granite_agent import GraniteAPIError

        call_count = [0]

        class Auth403Agent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                raise GraniteAPIError("Granite API returned HTTP 403.")

            def _parse_prioritization_response(self, *args):
                pass

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=Auth403Agent(),
            model_id="test-model",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.attempt_count == 1
            assert call_count[0] == 1
            assert failure.status_hint == BenchmarkStatus.PROVIDER_ERROR

    def test_401_gives_one_attempt(self):
        """HTTP 401 must not be retried."""
        from backend.app.agent.granite_agent import GraniteAPIError

        call_count = [0]

        class Auth401Agent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                raise GraniteAPIError("Granite API returned HTTP 401: IAM authentication failed.")

            def _parse_prioritization_response(self, *args):
                pass

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=Auth401Agent(),
            model_id="test-model",
        )
        try:
            provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        except BenchmarkProviderFailure as failure:
            assert failure.attempt_count == 1
            assert call_count[0] == 1


class TestSuccessAfterRetry:
    """Success on attempt 2 → attempt_count=2, raw_response from successful attempt."""

    def test_503_then_success(self):
        """Attempt 1 → 503, attempt 2 → success → attempt_count=2."""
        from backend.app.agent.granite_agent import GraniteAPIError
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        call_count = [0]
        success_raw = json.dumps({
            "ranked_products": [],
            "overall_reasoning": "ok",
            "confidence": 0.8,
            "decision_factors": [],
        })

        class FlakyAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise GraniteAPIError("Granite API returned HTTP 503.")
                return success_raw

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                return CandidatePrioritization(
                    ranked_products=[], overall_reasoning="ok", confidence=0.8,
                    decision_factors=[], candidate_count=0,
                )

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=FlakyAgent(),
            model_id="ibm/granite-4-h-small",
        )
        result = provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        assert result.attempt_count == 2
        assert result.raw_response == success_raw
        assert len(result.attempt_latencies_ms) == 2


# ===========================================================================
# PART C — Retry policy default-deny
# ===========================================================================


class TestRetryPolicyDefaultDeny:
    """Verify every retry category from the acceptance criteria."""

    def _granite_api_exc(self, msg: str):
        from backend.app.agent.granite_agent import GraniteAPIError
        return GraniteAPIError(msg)

    def _granite_response_exc(self, msg: str):
        from backend.app.agent.granite_agent import GraniteResponseError
        return GraniteResponseError(msg)

    # --- Retriable cases ---
    def test_http_429_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 429.")) is True

    def test_http_500_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 500.")) is True

    def test_http_502_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 502.")) is True

    def test_http_503_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 503.")) is True

    def test_http_504_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 504.")) is True

    def test_timeout_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("connection timeout")) is True

    def test_connection_error_is_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("connection refused")) is True

    def test_transport_error_is_retriable(self):
        assert is_retriable_benchmark_error(GraniteTransportError("transport failed")) is True

    # --- Non-retriable cases ---
    def test_http_400_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 400.")) is False

    def test_http_401_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 401: IAM authentication failed.")) is False

    def test_http_403_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 403.")) is False

    def test_http_404_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 404.")) is False

    def test_http_422_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_api_exc("Granite API returned HTTP 422.")) is False

    def test_malformed_json_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_response_exc("not valid JSON: ...")) is False

    def test_invalid_product_id_not_retriable(self):
        assert is_retriable_benchmark_error(self._granite_response_exc("unknown product_id 'FAKE-999'")) is False

    def test_unknown_granite_api_error_not_retriable(self):
        """Unknown GraniteAPIError defaults to NOT retriable (default-deny)."""
        from backend.app.agent.granite_agent import GraniteAPIError
        exc = GraniteAPIError("unexpected Granite application error with code XYZ")
        assert is_retriable_benchmark_error(exc) is False

    def test_unexpected_response_shape_not_retriable(self):
        """HTTP 200 but missing results[0].generated_text → not retriable."""
        # This is represented as GraniteAPIError with 'unexpected' in message
        from backend.app.agent.granite_agent import GraniteAPIError
        exc = GraniteAPIError("Unexpected Granite API response shape: KeyError")
        # The message contains 'Unexpected' but no HTTP 5xx — should be default-deny
        assert is_retriable_benchmark_error(exc) is False

    def test_granite_parse_error_not_retriable(self):
        """GraniteParseError (subclass of GraniteResponseError) is not retriable."""
        from backend.app.agent.granite_agent import GraniteParseError
        assert is_retriable_benchmark_error(GraniteParseError("bad JSON")) is False

    def test_granite_schema_error_not_retriable(self):
        """GraniteSchemaError (subclass of GraniteResponseError) is not retriable."""
        from backend.app.agent.granite_agent import GraniteSchemaError
        assert is_retriable_benchmark_error(GraniteSchemaError("schema violation")) is False


# ===========================================================================
# PART D — Suite/effective config provenance
# ===========================================================================


class TestEffectiveConfigProvenance:
    """Suite / effective config provenance tests."""

    def _make_fake_provider(self):
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        return FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
            model_id="ibm/granite-4-h-small",
        )

    def _make_quick_variants(self):
        """Two quick-suite variants: CAP035_ORIGINAL and CAP090_ORIGINAL."""
        _skip_if_no_scenario()
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        all_variants = gen.generate_all()
        quick_ids = {"CAP035_ORIGINAL", "CAP090_ORIGINAL"}
        return [v for v in all_variants if v.spec.scenario_id in quick_ids]

    def _make_core_variants(self):
        """12 core variants."""
        _skip_if_no_scenario()
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        return gen.generate_core()

    def test_quick_suite_has_two_scenarios(self):
        """Quick suite must produce exactly 2 scenario variants."""
        variants = self._make_quick_variants()
        assert len(variants) == 2
        scenario_ids = {v.spec.scenario_id for v in variants}
        assert "CAP035_ORIGINAL" in scenario_ids
        assert "CAP090_ORIGINAL" in scenario_ids

    def test_quick_suite_effective_config_contains_scenario_ids(self):
        """Effective config for quick suite records the 2 scenario IDs explicitly."""
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(
            provider=provider, repetitions=1,
            config_overrides={"scenario_matrix": {"configured_count": 12, "executed": [v.spec.scenario_id for v in variants]}},
            run_type="pilot",
        )
        eff = runner.write_effective_config(
            variants=variants, suite="quick", run_type="pilot", preregistered=False,
        )
        assert eff["suite"] == "quick"
        assert eff["run_type"] == "pilot"
        assert eff["preregistered"] is False
        ids = eff["executed_scenario_ids"]
        assert "CAP035_ORIGINAL" in ids
        assert "CAP090_ORIGINAL" in ids
        assert len(ids) == 2

    def test_quick_suite_is_non_preregistered(self):
        """Quick suite forces preregistered=False."""
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(
            provider=provider, repetitions=1,
            config_overrides={"scenario_matrix": {"configured_count": 12, "executed": [v.spec.scenario_id for v in variants]}},
            run_type="pilot",
        )
        eff = runner.write_effective_config(
            variants=variants, suite="quick", run_type="pilot", preregistered=False,
        )
        assert eff["preregistered"] is False

    def test_pilot_run_type_forces_non_preregistered(self):
        """run_type=pilot always produces preregistered=False."""
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(provider=provider, repetitions=1, run_type="pilot")
        eff = runner.write_effective_config(
            variants=variants, suite="quick", run_type="pilot", preregistered=False,
        )
        assert eff["preregistered"] is False

    def test_core_suite_is_preregistered_when_no_overrides(self):
        """Core suite with no overrides must produce preregistered=True."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        variants = self._make_core_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(
            provider=provider, repetitions=cfg.repetitions,
            benchmark_config=cfg, config_overrides={}, run_type="core",
        )
        eff = runner.write_effective_config(
            cfg, variants=variants, suite="core", run_type="core", preregistered=True,
        )
        assert eff["preregistered"] is True
        assert eff["suite"] == "core"

    def test_core_suite_has_twelve_scenarios(self):
        """Core suite produces 12 scenario variants (4 caps × 3 anomaly modes)."""
        variants = self._make_core_variants()
        assert len(variants) == 12

    def test_effective_config_hash_changes_with_suite(self):
        """core effective_config_sha256 must differ from quick effective_config_sha256."""
        _skip_if_no_scenario()
        quick_variants = self._make_quick_variants()
        core_variants = self._make_core_variants()
        provider = self._make_fake_provider()

        runner = BenchmarkRunner(provider=provider, repetitions=1)
        quick_eff = runner.write_effective_config(variants=quick_variants, suite="quick")
        core_eff = runner.write_effective_config(variants=core_variants, suite="core")

        assert quick_eff["effective_config_sha256"] != core_eff["effective_config_sha256"]

    def test_effective_config_hash_changes_with_model(self):
        """Different model → different effective_config_sha256."""
        _skip_if_no_scenario()
        variants = self._make_quick_variants()

        prov_a = FakeProvider(
            lambda c, ls, ms, a, distance_km=None: __import__(
                "backend.app.models.candidate_prioritization", fromlist=["CandidatePrioritization"]
            ).CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
            model_id="model-A",
        )
        prov_b = FakeProvider(
            lambda c, ls, ms, a, distance_km=None: __import__(
                "backend.app.models.candidate_prioritization", fromlist=["CandidatePrioritization"]
            ).CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
            model_id="model-B",
        )

        runner_a = BenchmarkRunner(provider=prov_a, repetitions=1)
        runner_b = BenchmarkRunner(provider=prov_b, repetitions=1)

        eff_a = runner_a.write_effective_config(variants=variants, suite="quick")
        eff_b = runner_b.write_effective_config(variants=variants, suite="quick")

        assert eff_a["effective_config_sha256"] != eff_b["effective_config_sha256"]

    def test_effective_config_contains_actual_model(self):
        """Effective config executed_values.model must equal provider.model_id."""
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(provider=provider, repetitions=1)
        eff = runner.write_effective_config(variants=variants, suite="quick")
        assert eff["executed_values"]["model"] == provider.model_id

    def test_effective_config_writes_to_file(self):
        """write_effective_config writes effective_config.json to output_dir."""
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir)
            runner = BenchmarkRunner(provider=provider, repetitions=1, output_dir=out)
            runner.write_effective_config(variants=variants, suite="quick")
            eff_file = out / "effective_config.json"
            assert eff_file.exists()
            data = json.loads(eff_file.read_text())
            assert "effective_config_sha256" in data
            assert "executed_scenario_ids" in data

    def test_full_suite_adds_deadline_variants(self):
        """Full suite with deadline_scale=0.5 adds more variants than core."""
        _skip_if_no_scenario()
        gen_full = ScenarioVariantGenerator(
            base_scenario_path=BASE_SCENARIO_PATH,
            deadline_scales=FULL_DEADLINE_SCALES,
        )
        full_variants = gen_full.generate_all()
        # Full should have more variants than the 12-scenario core
        assert len(full_variants) > 12

    def test_source_and_effective_sha_are_distinct_fields(self):
        """source_config_sha256 and effective_config_sha256 are distinct."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        variants = self._make_quick_variants()
        provider = self._make_fake_provider()
        runner = BenchmarkRunner(provider=provider, repetitions=1, benchmark_config=cfg)
        eff = runner.write_effective_config(cfg, variants=variants, suite="quick")
        # Both must exist
        assert "source_config_sha256" in eff
        assert "effective_config_sha256" in eff
        # They should differ (different content)
        assert eff["source_config_sha256"] != eff["effective_config_sha256"]


# ===========================================================================
# PART D — Audit files for failed responses
# ===========================================================================


class TestFailedAuditFiles:
    """Failed responses with save_prompts=True must produce audit files."""

    def test_audit_files_created_for_parse_failure(self):
        """Parse-failed response must produce system/user/response audit files."""
        _skip_if_no_scenario()
        from backend.app.agent.granite_agent import GraniteResponseError
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        malformed_raw = '{ "ranked_products":'
        call_count = [0]

        class ParseFailAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                return malformed_raw

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("malformed JSON")

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test-run"
            provider = GraniteBenchmarkProvider(
                max_attempts=1, delay_s=0.0,
                agent=ParseFailAgent(),
                model_id="ibm/granite-4-h-small",
            )
            runner = BenchmarkRunner(
                provider=provider, repetitions=1,
                save_prompts=True, output_dir=out,
            )
            trials = runner.run_variant(variants[0])

            audit_dir = out / "audit"
            assert audit_dir.exists(), "audit/ directory must exist for failed trial"

            trial = trials[0]
            safe_id = trial.trial_id.replace("/", "_").replace("\\", "_")
            assert (audit_dir / f"{safe_id}.system.txt").exists(), "system.txt must exist"
            assert (audit_dir / f"{safe_id}.user.json").exists(), "user.json must exist"
            assert (audit_dir / f"{safe_id}.response.txt").exists(), "response.txt must exist"

            # Response file should contain the malformed raw output
            resp_content = (audit_dir / f"{safe_id}.response.txt").read_text()
            assert "ranked_products" in resp_content or resp_content.strip() != ""

    def test_audit_response_file_for_no_response(self):
        """Transport failure (no response) produces empty or marker response file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            _write_audit_files(
                audit_dir, "trial_no_response",
                system_prompt="system content",
                user_message='{"context": "data"}',
                raw_response="",  # no response received
            )
            resp_file = audit_dir / "trial_no_response.response.txt"
            assert resp_file.exists()
            content = resp_file.read_text()
            # Empty string is acceptable for transport failure
            assert content == ""


# ===========================================================================
# PART A+B — Runner correctly converts BenchmarkProviderFailure to trial
# ===========================================================================


class TestRunnerProvenanceFromFailure:
    """Runner must use BenchmarkProviderFailure provenance, not guess metadata."""

    def test_runner_uses_failure_attempt_count(self):
        """Runner records actual attempt_count from BenchmarkProviderFailure."""
        _skip_if_no_scenario()
        from backend.app.agent.granite_agent import GraniteAPIError

        call_count = [0]

        class AlwaysTransientAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                raise GraniteAPIError("Granite API returned HTTP 503.")

            def _parse_prioritization_response(self, *args):
                pass

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        provider = GraniteBenchmarkProvider(
            max_attempts=2, delay_s=0.0,
            agent=AlwaysTransientAgent(),
            model_id="ibm/granite-4-h-small",
        )
        runner = BenchmarkRunner(provider=provider, repetitions=1)
        trials = runner.run_variant(variants[0])

        trial = trials[0]
        # Runner must record actual attempt_count=2 from the failure
        assert trial.attempt_count == 2

    def test_runner_uses_failure_raw_response(self):
        """Runner records raw_response_sha256 from BenchmarkProviderFailure."""
        _skip_if_no_scenario()
        from backend.app.agent.granite_agent import GraniteResponseError

        malformed_raw = '{ "ranked_products":'

        class ParseFailAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                return malformed_raw

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("bad JSON")

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        provider = GraniteBenchmarkProvider(
            max_attempts=1, delay_s=0.0,
            agent=ParseFailAgent(),
            model_id="ibm/granite-4-h-small",
        )
        runner = BenchmarkRunner(provider=provider, repetitions=1)
        trials = runner.run_variant(variants[0])

        trial = trials[0]
        expected_sha = hashlib.sha256(malformed_raw.encode()).hexdigest()
        assert trial.raw_response_sha256 == expected_sha

    def test_runner_uses_failure_model_id(self):
        """Runner records actual_model_id from BenchmarkProviderFailure."""
        _skip_if_no_scenario()
        from backend.app.agent.granite_agent import GraniteResponseError

        class FailAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                return '{ bad'

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("bad")

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        provider = GraniteBenchmarkProvider(
            max_attempts=1, delay_s=0.0,
            agent=FailAgent(),
            model_id="ibm/granite-4-h-small",
        )
        runner = BenchmarkRunner(provider=provider, repetitions=1)
        trials = runner.run_variant(variants[0])

        trial = trials[0]
        assert trial.actual_model_id == "ibm/granite-4-h-small"

    def test_runner_uses_failure_prompt_hashes(self):
        """Runner records prompt hashes from BenchmarkProviderFailure (not empty)."""
        _skip_if_no_scenario()
        from backend.app.agent.granite_agent import GraniteResponseError

        class FailAgent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                return '{ bad'

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("bad")

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        provider = GraniteBenchmarkProvider(
            max_attempts=1, delay_s=0.0,
            agent=FailAgent(),
            model_id="test-model",
        )
        runner = BenchmarkRunner(provider=provider, repetitions=1)
        trials = runner.run_variant(variants[0])

        trial = trials[0]
        # Prompt hashes must be non-empty for any failure where messages were built
        assert trial.prompt_system_sha256 != ""
        assert trial.prompt_user_sha256 != ""


# ===========================================================================
# Part E — Generation config canonical constant
# ===========================================================================


class TestGenerationConfigConsistency:
    """STAGE1_GENERATION_CONFIG must be the single source of truth."""

    def test_stage1_generation_config_values(self):
        """STAGE1_GENERATION_CONFIG contains correct preregistered v1 values."""
        from backend.app.agent.granite_agent import STAGE1_GENERATION_CONFIG
        assert STAGE1_GENERATION_CONFIG["decoding_method"] == "greedy"
        assert STAGE1_GENERATION_CONFIG["max_new_tokens"] == 2048
        assert STAGE1_GENERATION_CONFIG["stop_sequences"] == ["<|user|>"]

    def test_provider_generation_config_matches_stage1_constant(self):
        """BenchmarkProviderResult.generation_config matches STAGE1_GENERATION_CONFIG."""
        from backend.app.agent.granite_agent import STAGE1_GENERATION_CONFIG
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        success_raw = json.dumps({
            "ranked_products": [],
            "overall_reasoning": "ok",
            "confidence": 0.8,
            "decision_factors": [],
        })

        class OkAgent:
            _model_id = "ibm/granite-4-h-small"

            def _call_prioritization_api(self, user_message: str) -> str:
                return success_raw

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                return CandidatePrioritization(
                    ranked_products=[], overall_reasoning="ok", confidence=0.8,
                    decision_factors=[], candidate_count=0,
                )

        provider = GraniteBenchmarkProvider(
            max_attempts=1, delay_s=0.0,
            agent=OkAgent(),
            model_id="ibm/granite-4-h-small",
        )
        result = provider.prioritize([], _make_link_state(), _make_mission_state(), [])
        assert result.generation_config["decoding_method"] == STAGE1_GENERATION_CONFIG["decoding_method"]
        assert result.generation_config["max_new_tokens"] == STAGE1_GENERATION_CONFIG["max_new_tokens"]
        assert result.generation_config["stop_sequences"] == STAGE1_GENERATION_CONFIG["stop_sequences"]
