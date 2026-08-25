"""Unit tests for Phase 2D: AI Decision Transparency & Explainability.

Covers:
- RankedProduct Phase 2D field validation (factors, anomaly_ids, subsystem, confidence)
- CandidatePrioritization Phase 2D field validation (decision_factors, candidate_count)
- LocalRuleBasedProvider: structured factors, anomaly_ids, subsystem populated deterministically
- GraniteAgent._parse_prioritization_response: Phase 2D fields parsed / ignored gracefully
- API response model: prioritization_error field
- Graceful fallback: AI failure surfaces error without collapsing deterministic recommendation
- Backwards compatibility: old responses without Phase 2D fields still parse
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_prioritization import (
    CandidatePrioritization,
    RankedProduct,
)
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_link_state(**kw) -> LinkState:
    base = dict(
        timestamp=_TS, snr_db=10.0, eb_n0_db=20.0, ber=3.87e-6, rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0, link_goodput_bps=90_000.0,
        latency_s=0.25, link_stability=0.95, remaining_window_s=600.0,
    )
    base.update(kw)
    return LinkState(**base)


def make_mission_state(**kw) -> MissionState:
    base = dict(
        mission_id="m-001", mission_phase="science", current_event="downlink",
        event_time_remaining_s=600.0, comm_window_remaining_s=600.0,
        risk_score=0.3, risk_level=RiskLevel.LOW,
    )
    base.update(kw)
    return MissionState(**base)


def make_candidate_summary(**kw) -> CandidateSummary:
    base = dict(
        product_id="CS-001", product_type="telemetry", subsystem="power",
        size_bits=4096, criticality=0.6, mission_relevance=0.6,
        scientific_value=0.4, deadline_s=300.0, age_s=120.0,
    )
    base.update(kw)
    return CandidateSummary(**base)


def make_anomaly(
    anomaly_id: str = "ANOM-001",
    subsystem: str = "propulsion",
    severity: float = 0.85,
) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=anomaly_id,
        subsystem=subsystem,
        severity=severity,
        detected_at_s=100.0,
        description="Test anomaly.",
        status="active",
    )


# ===========================================================================
# RankedProduct — Phase 2D model fields
# ===========================================================================


class TestRankedProductPhase2D:
    def test_valid_with_all_phase2d_fields(self):
        rp = RankedProduct(
            product_id="P1",
            priority=1,
            reason="Critical diagnostic data.",
            factors=["active anomaly", "high criticality"],
            anomaly_ids=["ANOM-017"],
            subsystem="propulsion",
            confidence=0.94,
        )
        assert rp.factors == ["active anomaly", "high criticality"]
        assert rp.anomaly_ids == ["ANOM-017"]
        assert rp.subsystem == "propulsion"
        assert rp.confidence == pytest.approx(0.94)

    def test_phase2d_fields_default_empty(self):
        rp = RankedProduct(product_id="P1", priority=1, reason="First.")
        assert rp.factors == []
        assert rp.anomaly_ids == []
        assert rp.subsystem == ""
        assert rp.confidence is None

    def test_confidence_none_is_valid(self):
        rp = RankedProduct(product_id="P1", priority=1, reason="Test.", confidence=None)
        assert rp.confidence is None

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(Exception):
            RankedProduct(product_id="P1", priority=1, reason="Test.", confidence=1.5)

    def test_confidence_negative_rejected(self):
        with pytest.raises(Exception):
            RankedProduct(product_id="P1", priority=1, reason="Test.", confidence=-0.1)

    def test_multiple_anomaly_ids(self):
        rp = RankedProduct(
            product_id="P1", priority=1, reason="R.",
            anomaly_ids=["ANOM-001", "ANOM-002"],
        )
        assert len(rp.anomaly_ids) == 2

    def test_legacy_response_without_phase2d_fields(self):
        """RankedProduct constructed without Phase 2D fields must use defaults."""
        rp = RankedProduct(product_id="P1", priority=1, reason="Legacy.")
        assert rp.factors == []
        assert rp.anomaly_ids == []
        assert rp.subsystem == ""
        assert rp.confidence is None


# ===========================================================================
# CandidatePrioritization — Phase 2D model fields
# ===========================================================================


class TestCandidatePrioritizationPhase2D:
    def test_valid_with_all_phase2d_fields(self):
        rp = RankedProduct(product_id="P1", priority=1, reason="R.",
                           factors=["active anomaly"], anomaly_ids=["ANOM-001"],
                           subsystem="propulsion", confidence=0.88)
        cp = CandidatePrioritization(
            ranked_products=[rp],
            overall_reasoning="Propulsion anomaly is primary driver.",
            confidence=0.88,
            decision_factors=["active anomaly", "high criticality"],
            candidate_count=10,
        )
        assert cp.decision_factors == ["active anomaly", "high criticality"]
        assert cp.candidate_count == 10

    def test_decision_factors_default_empty(self):
        cp = CandidatePrioritization(
            ranked_products=[], overall_reasoning="None.", confidence=0.5
        )
        assert cp.decision_factors == []

    def test_candidate_count_default_none(self):
        cp = CandidatePrioritization(
            ranked_products=[], overall_reasoning="None.", confidence=0.5
        )
        assert cp.candidate_count is None

    def test_backwards_compatible_serialization(self):
        """Old code constructing CandidatePrioritization without Phase 2D fields must work."""
        cp = CandidatePrioritization(
            ranked_products=[
                RankedProduct(product_id="P1", priority=1, reason="Mission critical.")
            ],
            overall_reasoning="Top-level strategy.",
            confidence=0.75,
        )
        data = cp.model_dump()
        assert "decision_factors" in data
        assert "candidate_count" in data
        assert data["ranked_products"][0]["factors"] == []
        assert data["ranked_products"][0]["anomaly_ids"] == []


# ===========================================================================
# LocalRuleBasedProvider — Phase 2D structured factors
# ===========================================================================


class TestLocalProviderPhase2DFactors:
    def _provider(self) -> LocalRuleBasedProvider:
        return LocalRuleBasedProvider()

    def test_anomaly_linked_product_gets_active_anomaly_factor(self):
        anomaly = make_anomaly(anomaly_id="ANOM-CRITICAL", severity=0.90)
        candidate = make_candidate_summary(
            product_id="ANOM-PROD", anomaly_id="ANOM-CRITICAL",
            criticality=0.5, subsystem="propulsion"
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        rp = result.ranked_products[0]
        assert "active anomaly" in rp.factors

    def test_high_severity_anomaly_gets_high_severity_factor(self):
        anomaly = make_anomaly(anomaly_id="ANOM-SEVERE", severity=0.90)
        candidate = make_candidate_summary(
            product_id="P1", anomaly_id="ANOM-SEVERE"
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        rp = result.ranked_products[0]
        assert "high severity anomaly" in rp.factors

    def test_low_severity_anomaly_no_high_severity_factor(self):
        anomaly = make_anomaly(anomaly_id="ANOM-LOW", severity=0.50)
        candidate = make_candidate_summary(product_id="P1", anomaly_id="ANOM-LOW")
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        rp = result.ranked_products[0]
        assert "active anomaly" in rp.factors
        assert "high severity anomaly" not in rp.factors

    def test_high_criticality_product_gets_high_criticality_factor(self):
        candidate = make_candidate_summary(product_id="P1", criticality=0.90)
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert "high criticality" in rp.factors

    def test_deadline_urgency_factor_for_near_deadline(self):
        candidate = make_candidate_summary(product_id="P1", deadline_s=60.0)
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert "deadline urgency" in rp.factors

    def test_routine_housekeeping_for_unimportant_product(self):
        candidate = make_candidate_summary(
            product_id="P1", criticality=0.2, mission_relevance=0.2,
            scientific_value=0.2, deadline_s=600.0
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert "routine housekeeping" in rp.factors

    def test_anomaly_ids_populated_for_anomaly_linked_product(self):
        anomaly = make_anomaly(anomaly_id="ANOM-017")
        candidate = make_candidate_summary(
            product_id="P1", anomaly_id="ANOM-017"
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        rp = result.ranked_products[0]
        assert "ANOM-017" in rp.anomaly_ids

    def test_anomaly_ids_empty_for_non_anomaly_product(self):
        candidate = make_candidate_summary(product_id="P1", anomaly_id=None)
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert rp.anomaly_ids == []

    def test_subsystem_populated_from_candidate(self):
        candidate = make_candidate_summary(product_id="P1", subsystem="thermal")
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert rp.subsystem == "thermal"

    def test_local_provider_no_per_product_confidence(self):
        """Local provider must NOT report per-product confidence (it's not an LLM)."""
        candidate = make_candidate_summary(product_id="P1")
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert rp.confidence is None

    def test_decision_factors_populated_for_non_empty_set(self):
        anomaly = make_anomaly(anomaly_id="ANOM-001")
        candidate = make_candidate_summary(
            product_id="P1", anomaly_id="ANOM-001", criticality=0.85
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        assert len(result.decision_factors) > 0
        assert "active anomaly" in result.decision_factors

    def test_candidate_count_populated(self):
        candidates = [make_candidate_summary(product_id=f"P{i}") for i in range(5)]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        assert result.candidate_count == 5

    def test_empty_candidates_candidate_count_zero(self):
        provider = self._provider()
        result = provider.prioritize_candidates(
            [], make_link_state(), make_mission_state()
        )
        assert result.candidate_count == 0

    def test_related_products_factor(self):
        candidate = make_candidate_summary(
            product_id="P1", criticality=0.3,
            related_ids=["P2", "P3"]
        )
        provider = self._provider()
        result = provider.prioritize_candidates(
            [candidate], make_link_state(), make_mission_state()
        )
        rp = result.ranked_products[0]
        assert "related products" in rp.factors


# ===========================================================================
# GraniteAgent._parse_prioritization_response — Phase 2D field parsing
# ===========================================================================


class TestGranitePrioritizationPhase2DParsing:
    """Tests for Phase 2D field parsing in GraniteAgent (no live API calls)."""

    def _agent(self):
        from backend.app.agent.granite_agent import GraniteAgent
        return GraniteAgent(api_key="fake", project_id="fake-project")

    def _valid_response_with_phase2d(self, product_ids: list[str]) -> str:
        ranked = [
            {
                "product_id": pid,
                "priority": i + 1,
                "reason": f"Reason for {pid}.",
                "factors": ["active anomaly", "high criticality"],
                "anomaly_ids": ["ANOM-017"],
                "subsystem": "propulsion",
                "confidence": 0.92,
            }
            for i, pid in enumerate(product_ids)
        ]
        return json.dumps({
            "ranked_products": ranked,
            "overall_reasoning": "Propulsion anomaly drives prioritization.",
            "confidence": 0.91,
            "decision_factors": ["active anomaly", "deadline urgency"],
        })

    def _valid_response_without_phase2d(self, product_ids: list[str]) -> str:
        """Simulate a legacy response without Phase 2D fields."""
        ranked = [
            {"product_id": pid, "priority": i + 1, "reason": f"Reason for {pid}."}
            for i, pid in enumerate(product_ids)
        ]
        return json.dumps({
            "ranked_products": ranked,
            "overall_reasoning": "Legacy response without Phase 2D fields.",
            "confidence": 0.80,
        })

    def test_phase2d_fields_parsed_from_full_response(self):
        agent = self._agent()
        raw = self._valid_response_with_phase2d(["P1", "P2"])
        result = agent._parse_prioritization_response(raw, {"P1", "P2"})
        assert isinstance(result, CandidatePrioritization)
        # Decision factors
        assert "active anomaly" in result.decision_factors
        assert "deadline urgency" in result.decision_factors
        # Per-product Phase 2D fields
        rp = next(r for r in result.ranked_products if r.product_id == "P1")
        assert "active anomaly" in rp.factors
        assert "ANOM-017" in rp.anomaly_ids
        assert rp.subsystem == "propulsion"
        assert rp.confidence == pytest.approx(0.92)

    def test_legacy_response_without_phase2d_fields_is_valid(self):
        """Responses without Phase 2D fields must still parse (backwards compat)."""
        agent = self._agent()
        raw = self._valid_response_without_phase2d(["P1"])
        result = agent._parse_prioritization_response(raw, {"P1"})
        assert isinstance(result, CandidatePrioritization)
        rp = result.ranked_products[0]
        assert rp.factors == []
        assert rp.anomaly_ids == []
        assert rp.subsystem == ""
        assert rp.confidence is None
        assert result.decision_factors == []

    def test_invalid_phase2d_fields_ignored_gracefully(self):
        """Non-list Phase 2D fields must be coerced to empty lists, not raise."""
        agent = self._agent()
        ranked = [
            {
                "product_id": "P1", "priority": 1, "reason": "Test.",
                "factors": "not-a-list",  # wrong type
                "anomaly_ids": 42,        # wrong type
            }
        ]
        raw = json.dumps({
            "ranked_products": ranked,
            "overall_reasoning": "Test.",
            "confidence": 0.7,
            "decision_factors": "not-a-list",  # wrong type
        })
        result = agent._parse_prioritization_response(raw, {"P1"})
        rp = result.ranked_products[0]
        assert rp.factors == []
        assert rp.anomaly_ids == []
        assert result.decision_factors == []

    def test_per_product_confidence_out_of_range_ignored(self):
        """Per-product confidence outside [0, 1] must be silently ignored (set to None)."""
        agent = self._agent()
        ranked = [{"product_id": "P1", "priority": 1, "reason": "Test.", "confidence": 5.0}]
        raw = json.dumps({
            "ranked_products": ranked,
            "overall_reasoning": "Test.",
            "confidence": 0.7,
        })
        result = agent._parse_prioritization_response(raw, {"P1"})
        assert result.ranked_products[0].confidence is None

    def test_candidate_count_set_from_valid_ids_length(self):
        agent = self._agent()
        raw = self._valid_response_with_phase2d(["P1", "P2"])
        result = agent._parse_prioritization_response(raw, {"P1", "P2", "P3"})
        # candidate_count = len(valid_ids) = 3
        assert result.candidate_count == 3

    def test_hallucinated_product_id_still_rejected(self):
        """Phase 2D must not relax the hallucination guard."""
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = self._valid_response_with_phase2d(["P1", "HALLUCINATED-999"])
        with pytest.raises(GraniteResponseError, match="unknown product_id"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_duplicate_product_id_still_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P1", "priority": 1, "reason": "First.", "factors": [], "anomaly_ids": []},
                {"product_id": "P1", "priority": 2, "reason": "Dup.", "factors": [], "anomaly_ids": []},
            ],
            "overall_reasoning": "Test.",
            "confidence": 0.7,
            "decision_factors": [],
        })
        with pytest.raises(GraniteResponseError, match="duplicate product_id"):
            agent._parse_prioritization_response(raw, {"P1"})


# ===========================================================================
# API response model — prioritization_error field
# ===========================================================================


class TestRecommendResponsePhase2D:
    def test_prioritization_error_field_present(self):
        """RecommendResponse must have a prioritization_error field."""
        from backend.app.api.routes_agent import RecommendResponse
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        rec = AIRecommendation(
            recommended_plan_id="plan-1",
            packet_actions=[],
            risk_score=0.2,
            risk_level=RiskLevel.LOW,
            confidence=0.9,
            reasoning="Test.",
            evidence=[],
        )
        resp = RecommendResponse(
            provider="Local",
            requested_provider="Local",
            actual_provider="Local",
            recommendation=rec,
            prioritization=None,
            candidate_count=None,
            prioritization_error="AI unavailable — deterministic fallback.",
        )
        assert resp.prioritization_error == "AI unavailable — deterministic fallback."

    def test_prioritization_error_defaults_none(self):
        from backend.app.api.routes_agent import RecommendResponse
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        rec = AIRecommendation(
            recommended_plan_id="plan-1",
            packet_actions=[],
            risk_score=0.2,
            risk_level=RiskLevel.LOW,
            confidence=0.9,
            reasoning="Test.",
            evidence=[],
        )
        resp = RecommendResponse(
            provider="Local",
            requested_provider="Local",
            actual_provider="Local",
            recommendation=rec,
        )
        assert resp.prioritization_error is None


# ===========================================================================
# Graceful fallback: AI failure surfaces error without collapsing deterministic path
# ===========================================================================


class TestGracefulFallback:
    """The route must never raise 502/422 for prioritization failures; it must fall back."""

    def _make_scenario_v2(self):
        from pathlib import Path
        from backend.app.simulation.scenario_loader import ScenarioLoader
        path = Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json"
        return ScenarioLoader.load(str(path))

    def _make_link_state(self):
        from backend.app.models.link_state import LinkState
        from datetime import datetime, timezone
        return LinkState(
            timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
            snr_db=10.0, eb_n0_db=20.0, ber=3.87e-6, rssi_dbm=-80.0,
            nominal_data_rate_bps=100_000.0, link_goodput_bps=90_000.0,
            latency_s=0.25, link_stability=0.95, remaining_window_s=600.0,
        )

    @pytest.mark.asyncio
    async def test_ai_provider_error_falls_back_to_local_and_surfaces_error(self):
        """AIProviderError in prioritize_candidates must fall back to local and include error."""
        from backend.app.agent.base_provider import AIProviderError
        from httpx import ASGITransport, AsyncClient
        from backend.app.main import app
        import backend.app.state as app_state
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        app_state.active_scenario = self._make_scenario_v2()
        app_state.active_link_state = self._make_link_state()

        failing_provider = MagicMock()
        failing_provider.provider_name = "FakeProvider"
        failing_provider.prioritize_candidates.side_effect = AIProviderError("API key missing")
        local = LocalRuleBasedProvider()
        failing_provider.recommend.side_effect = lambda *a, **kw: local.recommend(*a, **kw)

        with patch("backend.app.api.routes_agent.get_provider", return_value=failing_provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        assert body["prioritization_error"] is not None
        assert (
            "API key missing" in body["prioritization_error"]
            or "unavailable" in body["prioritization_error"].lower()
        )
        assert body["prioritization"] is not None

        app_state.active_scenario = None
        app_state.active_link_state = None

    @pytest.mark.asyncio
    async def test_ai_prioritization_error_falls_back_gracefully(self):
        """AIPrioritizationError must fall back to local and surface error."""
        from backend.app.agent.base_provider import AIPrioritizationError
        from httpx import ASGITransport, AsyncClient
        from backend.app.main import app
        import backend.app.state as app_state
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        app_state.active_scenario = self._make_scenario_v2()
        app_state.active_link_state = self._make_link_state()

        failing_provider = MagicMock()
        failing_provider.provider_name = "FakeProvider"
        failing_provider.prioritize_candidates.side_effect = AIPrioritizationError("Invalid JSON")
        local = LocalRuleBasedProvider()
        failing_provider.recommend.side_effect = lambda *a, **kw: local.recommend(*a, **kw)

        with patch("backend.app.api.routes_agent.get_provider", return_value=failing_provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        assert body["prioritization_error"] is not None
        assert body["prioritization"] is not None

        app_state.active_scenario = None
        app_state.active_link_state = None


# ===========================================================================
# End-to-end: Phase 2D full pipeline through LocalRuleBasedProvider
# ===========================================================================


class TestPhase2DEndToEnd:
    def _load_v2_scenario(self):
        from pathlib import Path
        from backend.app.simulation.scenario_loader import ScenarioLoader
        path = Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json"
        return ScenarioLoader.load(str(path))

    def test_v2_scenario_local_provider_has_phase2d_fields(self):
        """End-to-end: v2 scenario through LocalRuleBasedProvider produces Phase 2D output."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        scenario = self._load_v2_scenario()
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            scenario.data_products,
            anomalies=scenario.anomalies,
            remaining_window_s=660.0,
        )
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates,
            make_link_state(remaining_window_s=660.0),
            make_mission_state(),
            anomalies=scenario.anomalies,
        )

        assert isinstance(result, CandidatePrioritization)
        # candidate_count must equal the number of candidates
        assert result.candidate_count == len(candidates)
        # decision_factors must be non-empty (v2 has anomalies)
        assert len(result.decision_factors) > 0
        # All ranked products have subsystem populated
        for rp in result.ranked_products:
            assert isinstance(rp.subsystem, str)
            # factors must always have at least one entry
            assert len(rp.factors) > 0

    def test_anomaly_linked_products_have_anomaly_ids_in_result(self):
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        scenario = self._load_v2_scenario()
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            scenario.data_products,
            anomalies=scenario.anomalies,
            remaining_window_s=660.0,
        )
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates,
            make_link_state(),
            make_mission_state(),
            anomalies=scenario.anomalies,
        )

        # Identify candidates that have anomaly_id set
        anomaly_candidate_ids = {
            cs.product_id for cs in candidates if cs.anomaly_id is not None
        }
        for rp in result.ranked_products:
            if rp.product_id in anomaly_candidate_ids:
                assert len(rp.anomaly_ids) > 0, (
                    f"Anomaly-linked product {rp.product_id} has empty anomaly_ids in result"
                )

    def test_serialized_response_contains_phase2d_fields(self):
        """The serialized CandidatePrioritization must contain Phase 2D field keys."""
        candidates = [
            make_candidate_summary(product_id="P1", anomaly_id="ANOM-001"),
            make_candidate_summary(product_id="P2"),
        ]
        anomaly = make_anomaly(anomaly_id="ANOM-001", severity=0.85)
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state(), anomalies=[anomaly]
        )

        data = result.model_dump(mode="json")
        # Top-level Phase 2D keys
        assert "decision_factors" in data
        assert "candidate_count" in data
        assert isinstance(data["decision_factors"], list)
        assert data["candidate_count"] == 2

        # Per-product Phase 2D keys
        for rp_data in data["ranked_products"]:
            assert "factors" in rp_data
            assert "anomaly_ids" in rp_data
            assert "subsystem" in rp_data
            assert "confidence" in rp_data
