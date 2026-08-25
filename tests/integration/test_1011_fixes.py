"""Targeted regression tests for GCSI 1.0.1 fixes.

Covers:
- AI fallback: prioritization failure → Local fallback + recommendation succeeds
- AI fallback: recommendation failure → Local fallback
- AI fallback: both stages fail → still succeeds via Local
- AI fallback: accurate provider metadata (requested vs actual)
- AI fallback: prioritization_fallback_reason and recommendation_fallback_reason set
- GET /scenarios: metadata isolation — unreadable file does not inherit from previous
- POST /scenarios/switch: basename-only contract enforced
  - subdir/test.json → 400
  - ../test.json → 400
  - absolute path → 400
  - non-json → 400
  - valid filenames still work
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Sequence
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.agent.base_provider import (
    AIHallucinationError,
    AIPrioritizationError,
    AIProviderError,
    AIResponseError,
    BaseAIProvider,
)
from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.api.routes_data_products import _SCENARIOS_DIR_PATH
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_prioritization import CandidatePrioritization
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.recommendation import AIRecommendation
from backend.app.models.anomaly_event import AnomalyEvent

# ── Scenario file paths ───────────────────────────────────────────────────────
_SCENARIOS_DIR_ABS = Path(__file__).parents[2] / "data" / "scenarios"
_V3_SCENARIO = str(_SCENARIOS_DIR_ABS / "mission_data_v3.json")
_LEGACY_SCENARIO = str(_SCENARIOS_DIR_ABS / "nominal_pass.json")


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    """Isolate every test."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


@pytest.fixture
def loaded_v3():
    app_state.load_scenario(_V3_SCENARIO)


@pytest.fixture
def loaded_legacy():
    app_state.load_scenario(_LEGACY_SCENARIO)


# ── Failing provider helpers ──────────────────────────────────────────────────

class _PrioritizationFailProvider(BaseAIProvider):
    """Provider that always raises AIProviderError during prioritize_candidates,
    but succeeds at recommend (delegates to LocalRuleBasedProvider)."""

    _local = LocalRuleBasedProvider()

    @property
    def provider_name(self) -> str:
        return "failing_prioritization"

    def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
        return self._local.recommend(link_state, mission_state, plans, evaluations, anomalies=anomalies)

    def prioritize_candidates(self, candidates, link_state, mission_state, anomalies=None, *, distance_km=None):
        raise AIProviderError("Simulated prioritization outage")


class _RecommendFailProvider(BaseAIProvider):
    """Provider that succeeds at prioritize_candidates (delegates to Local)
    but always raises AIProviderError during recommend."""

    _local = LocalRuleBasedProvider()

    @property
    def provider_name(self) -> str:
        return "failing_recommendation"

    def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
        raise AIProviderError("Simulated recommendation outage")

    def prioritize_candidates(self, candidates, link_state, mission_state, anomalies=None, *, distance_km=None):
        return self._local.prioritize_candidates(
            candidates, link_state, mission_state, anomalies, distance_km=distance_km
        )


class _BothFailProvider(BaseAIProvider):
    """Provider that fails at both AI stages."""

    @property
    def provider_name(self) -> str:
        return "fully_failing"

    def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
        raise AIProviderError("Both stages fail")

    def prioritize_candidates(self, candidates, link_state, mission_state, anomalies=None, *, distance_km=None):
        raise AIProviderError("Both stages fail")


class _InvalidResponseProvider(BaseAIProvider):
    """Provider that raises AIResponseError during recommend."""

    _local = LocalRuleBasedProvider()

    @property
    def provider_name(self) -> str:
        return "invalid_response"

    def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
        raise AIResponseError("Malformed JSON from provider")

    def prioritize_candidates(self, candidates, link_state, mission_state, anomalies=None, *, distance_km=None):
        return self._local.prioritize_candidates(
            candidates, link_state, mission_state, anomalies, distance_km=distance_km
        )


class _HallucinationProvider(BaseAIProvider):
    """Provider that raises AIHallucinationError during recommend."""

    _local = LocalRuleBasedProvider()

    @property
    def provider_name(self) -> str:
        return "hallucinating"

    def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
        raise AIHallucinationError("Provider cited non-existent field")

    def prioritize_candidates(self, candidates, link_state, mission_state, anomalies=None, *, distance_km=None):
        return self._local.prioritize_candidates(
            candidates, link_state, mission_state, anomalies, distance_km=distance_km
        )


# ── AI fallback tests (v3 / high-volume path) ─────────────────────────────────

class TestAIFallbackV3:
    """Test graceful AI fallback on the v2/v3 high-volume data products path."""

    @pytest.mark.asyncio
    async def test_prioritization_failure_does_not_return_502(self, loaded_v3):
        """Primary prioritization failure must NOT cause HTTP 502."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_PrioritizationFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_prioritization_failure_sets_fallback_reason(self, loaded_v3):
        """Prioritization failure must populate prioritization_fallback_reason."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_PrioritizationFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["prioritization_fallback_reason"] is not None
        assert "failing_prioritization" in body["prioritization_fallback_reason"]

    @pytest.mark.asyncio
    async def test_prioritization_failure_reports_correct_requested_provider(self, loaded_v3):
        """requested_provider must still reflect the originally configured provider."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_PrioritizationFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["requested_provider"] == "failing_prioritization"

    @pytest.mark.asyncio
    async def test_prioritization_failure_backwards_compat_prioritization_error(self, loaded_v3):
        """prioritization_error (legacy field) must mirror prioritization_fallback_reason."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_PrioritizationFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["prioritization_error"] == body["prioritization_fallback_reason"]

    @pytest.mark.asyncio
    async def test_recommendation_failure_does_not_return_502(self, loaded_v3):
        """Primary recommendation failure must NOT cause HTTP 502."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_RecommendFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_recommendation_failure_sets_fallback_reason(self, loaded_v3):
        """Recommendation failure must populate recommendation_fallback_reason."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_RecommendFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["recommendation_fallback_reason"] is not None
        assert "failing_recommendation" in body["recommendation_fallback_reason"]

    @pytest.mark.asyncio
    async def test_recommendation_failure_actual_provider_is_local(self, loaded_v3):
        """When recommendation falls back, actual_provider must be the Local provider name."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_RecommendFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        # LocalRuleBasedProvider.provider_name == "Local" (capital L)
        assert body["actual_provider"].lower() == "local"
        assert body["provider"].lower() == "local"  # backwards-compat field

    @pytest.mark.asyncio
    async def test_recommendation_failure_requested_provider_preserved(self, loaded_v3):
        """requested_provider must still name the originally selected provider."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_RecommendFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["requested_provider"] == "failing_recommendation"

    @pytest.mark.asyncio
    async def test_both_stages_fail_still_succeeds(self, loaded_v3):
        """Both AI stages failing must still produce a valid response (Local fallback)."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_BothFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prioritization_fallback_reason"] is not None
        assert body["recommendation_fallback_reason"] is not None
        assert body["actual_provider"].lower() == "local"

    @pytest.mark.asyncio
    async def test_invalid_response_falls_back(self, loaded_v3):
        """AIResponseError during recommend must trigger Local fallback."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_InvalidResponseProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendation_fallback_reason"] is not None
        assert body["actual_provider"].lower() == "local"

    @pytest.mark.asyncio
    async def test_hallucination_falls_back(self, loaded_v3):
        """AIHallucinationError during recommend must trigger Local fallback."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_HallucinationProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendation_fallback_reason"] is not None
        assert body["actual_provider"].lower() == "local"

    @pytest.mark.asyncio
    async def test_no_fallback_when_provider_succeeds(self, loaded_v3):
        """When Local provider succeeds at both stages, no fallback fields are set."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=LocalRuleBasedProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prioritization_fallback_reason"] is None
        assert body["recommendation_fallback_reason"] is None
        assert body["requested_provider"].lower() == "local"
        assert body["actual_provider"].lower() == "local"


# ── AI fallback tests (legacy path) ──────────────────────────────────────────

class TestAIFallbackLegacy:
    """Fallback tests on the legacy packet path (no prioritization stage)."""

    @pytest.mark.asyncio
    async def test_recommendation_failure_falls_back_on_legacy(self, loaded_legacy):
        """Legacy path: recommend failure must fall back to Local."""
        with patch("backend.app.api.routes_agent.get_provider", return_value=_RecommendFailProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendation_fallback_reason"] is not None
        assert body["actual_provider"].lower() == "local"
        # No prioritization stage on legacy path
        assert body["prioritization_fallback_reason"] is None


# ── GET /scenarios — metadata isolation ──────────────────────────────────────

class TestScenarioListingIsolation:
    """Verify that an unreadable scenario does not inherit metadata from a
    previous iteration in the GET /scenarios loop."""

    @pytest.mark.asyncio
    async def test_unreadable_file_has_only_filename_metadata(self, loaded_v3):
        """A scenario file that cannot be parsed must have zeroed-out metadata."""
        base = _SCENARIOS_DIR_PATH.resolve()
        tmp_path = base / "_test_unreadable.json"
        try:
            # Write syntactically invalid JSON so the parser raises
            tmp_path.write_text("{ this is not valid json }", encoding="utf-8")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/scenarios")
            assert resp.status_code == 200

            scenarios = resp.json()["scenarios"]
            bad = next((s for s in scenarios if s["filename"] == tmp_path.name), None)
            assert bad is not None, "Unreadable scenario not in listing"

            # Must have zeroed-out metadata — must NOT inherit from mission_data_v3.json
            assert bad["data_products_count"] == 0
            assert bad["anomalies_count"] == 0
            assert bad["has_data_products"] is False
            assert bad["has_anomalies"] is False
            assert bad["scenario_id"] is None
            # Label should be derived from filename, not from v3 data
            assert "150" not in bad["label"]
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    @pytest.mark.asyncio
    async def test_unreadable_file_after_valid_does_not_inherit(self, loaded_v3):
        """A file that fails mid-parse after a valid file must get clean metadata."""
        base = _SCENARIOS_DIR_PATH.resolve()
        # Name chosen to sort after mission_data_v3.json alphabetically
        tmp_path = base / "zzz_test_corrupted.json"
        try:
            tmp_path.write_text("{invalid", encoding="utf-8")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.get("/scenarios")

            scenarios = resp.json()["scenarios"]
            bad = next((s for s in scenarios if s["filename"] == tmp_path.name), None)
            assert bad is not None
            assert bad["data_products_count"] == 0
            assert bad["scenario_id"] is None
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


# ── POST /scenarios/switch — basename contract ────────────────────────────────

class TestSwitchScenarioBasenameContract:
    """Verify that POST /scenarios/switch only accepts plain filenames."""

    @pytest.mark.asyncio
    async def test_subdir_slash_rejected(self, loaded_v3):
        """subdir/test.json must be rejected (path separator present)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "subdir/mission_data_v3.json"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_dotdot_slash_rejected(self, loaded_v3):
        """../test.json must be rejected (path traversal attempt)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "../mission_data_v3.json"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_absolute_unix_path_rejected(self, loaded_v3):
        """/absolute/path.json must be rejected."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "/etc/mission.json"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_windows_style_path_rejected(self, loaded_v3):
        r"""C:\something\file.json must be rejected (backslash or drive letter)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "C:\\scenarios\\mission_data_v3.json"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_non_json_extension_rejected(self, loaded_v3):
        """A plain filename without .json extension must be rejected."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_filename_still_works(self, loaded_legacy):
        """A valid plain filename must still succeed."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
        assert resp.status_code == 200
        assert resp.json()["data_products_count"] == 150

    @pytest.mark.asyncio
    async def test_basename_with_spaces_in_traversal_rejected(self, loaded_v3):
        """Embedded .. within a longer path must still be rejected."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "a/../mission_data_v3.json"})
        assert resp.status_code == 400
