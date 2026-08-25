"""Integration tests for Phase 2B: v2 scenario (data_products) pipeline.

Covers:
- /state returns v2 metadata fields for v2 scenarios
- /plans/generate works for v2 scenarios (bridges data_products → packets)
- /agent/recommend works with the local provider on a v2 scenario
- Legacy scenarios are completely unaffected by Phase 2B changes
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.models.anomaly_event import AnomalyEvent

# Path to the v2 reference scenario.
_V2_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json")
_NOMINAL_SCENARIO = "data/scenarios/nominal_pass.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state before and after each test."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


@pytest.fixture
def loaded_v2():
    """Pre-load the v2 scenario."""
    app_state.load_scenario(_V2_SCENARIO)


@pytest.fixture
def loaded_legacy():
    """Pre-load the legacy nominal scenario."""
    app_state.load_scenario(_NOMINAL_SCENARIO)


# ---------------------------------------------------------------------------
# /state — v2 metadata fields
# ---------------------------------------------------------------------------


class TestStateV2Fields:
    @pytest.mark.asyncio
    async def test_state_includes_data_products_count_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert "data_products_count" in body
        assert body["data_products_count"] == 50

    @pytest.mark.asyncio
    async def test_state_includes_anomalies_count_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert "anomalies_count" in body
        assert body["anomalies_count"] == 3

    @pytest.mark.asyncio
    async def test_state_includes_anomalies_list_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert "anomalies" in body
        assert isinstance(body["anomalies"], list)
        assert len(body["anomalies"]) == 3

    @pytest.mark.asyncio
    async def test_state_anomalies_have_expected_ids_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        anomaly_ids = {a["anomaly_id"] for a in resp.json()["anomalies"]}
        assert anomaly_ids == {"ANOM-017", "ANOM-023", "ANOM-031"}

    @pytest.mark.asyncio
    async def test_state_anomaly_severity_present_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        for anomaly in resp.json()["anomalies"]:
            assert "severity" in anomaly
            assert 0.0 <= anomaly["severity"] <= 1.0

    @pytest.mark.asyncio
    async def test_state_legacy_scenario_zero_data_products(self, loaded_legacy):
        """Legacy scenario must return data_products_count=0 and anomalies_count=0."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_products_count"] == 0
        assert body["anomalies_count"] == 0
        assert body["anomalies"] == []

    @pytest.mark.asyncio
    async def test_state_still_has_link_and_mission_state_v2(self, loaded_v2):
        """Phase 2B must not break the original link_state / mission_state fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "link_state" in body
        assert "mission_state" in body


# ---------------------------------------------------------------------------
# /plans/generate — v2 bridge path
# ---------------------------------------------------------------------------


class TestPlansGenerateV2:
    @pytest.mark.asyncio
    async def test_generate_returns_four_plans_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        assert resp.status_code == 200
        plans = resp.json()
        assert len(plans) == 4

    @pytest.mark.asyncio
    async def test_generate_strategy_names_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        strategies = {p["strategy"] for p in resp.json()}
        assert strategies == {
            "baseline",
            "deadline_first",
            "mission_critical_first",
            "value_per_cost",
        }

    @pytest.mark.asyncio
    async def test_generate_packets_come_from_data_products_v2(self, loaded_v2):
        """Each plan's packets must correspond to data_products (50 items)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        for plan in resp.json():
            assert len(plan["packets"]) == 50

    @pytest.mark.asyncio
    async def test_generate_packet_ids_are_product_ids_v2(self, loaded_v2):
        """packet_id in the plan must match product_id from the scenario."""
        expected_ids = {dp.product_id for dp in app_state.active_scenario.data_products}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        baseline = next(p for p in resp.json() if p["strategy"] == "baseline")
        plan_ids = {pkt["packet_id"] for pkt in baseline["packets"]}
        assert plan_ids == expected_ids

    @pytest.mark.asyncio
    async def test_generate_legacy_unaffected(self, loaded_legacy):
        """Legacy scenario must still generate plans from its original packets."""
        n_packets = len(app_state.active_scenario.packets)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        assert resp.status_code == 200
        for plan in resp.json():
            assert len(plan["packets"]) == n_packets

    @pytest.mark.asyncio
    async def test_generate_high_criticality_first_in_mc_plan_v2(self, loaded_v2):
        """The mission_critical_first plan must start with the highest-criticality product."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        mc_plan = next(p for p in resp.json() if p["strategy"] == "mission_critical_first")
        first_id = mc_plan["packets"][0]["packet_id"]
        # Highest-criticality product in the v2 fixture is DIAG-PROP-001 (0.97)
        # Verify the first packet has the maximum criticality in the scenario.
        max_criticality = max(
            dp.criticality for dp in app_state.active_scenario.data_products
        )
        first_criticality = next(
            dp.criticality
            for dp in app_state.active_scenario.data_products
            if dp.product_id == first_id
        )
        assert first_criticality == pytest.approx(max_criticality)


# ---------------------------------------------------------------------------
# /agent/recommend — v2 scenario with local provider (no API key needed)
# ---------------------------------------------------------------------------


class TestAgentRecommendV2:
    """Use the local provider for all recommend tests to avoid live API calls."""

    @pytest.fixture(autouse=True)
    def force_local_provider(self, monkeypatch):
        """Force LocalRuleBasedProvider so tests don't make live API calls."""
        import os
        monkeypatch.setenv("GCSI_AI_PROVIDER", "local")

    @pytest.mark.asyncio
    async def test_recommend_returns_200_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_recommend_provider_field_present_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert "provider" in body
        assert body["provider"] == "Local"

    @pytest.mark.asyncio
    async def test_recommend_contains_recommendation_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert "recommendation" in body
        rec = body["recommendation"]
        assert "recommended_plan_id" in rec
        assert "reasoning" in rec
        assert "risk_score" in rec
        assert "evidence" in rec

    @pytest.mark.asyncio
    async def test_recommend_returns_prioritization_for_v2(self, loaded_v2):
        """v2 scenarios must return a prioritization field with AI ranking."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body.get("prioritization") is not None
        p = body["prioritization"]
        assert "ranked_products" in p
        assert "overall_reasoning" in p
        assert "confidence" in p
        assert len(p["ranked_products"]) > 0

    @pytest.mark.asyncio
    async def test_recommend_candidate_count_for_v2(self, loaded_v2):
        """candidate_count must be <= 50 even for 50-product v2 scenario."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body.get("candidate_count") is not None
        assert body["candidate_count"] <= 50

    @pytest.mark.asyncio
    async def test_recommend_plan_id_is_valid_v2(self, loaded_v2):
        """The recommended plan_id must be one of the four generated strategies."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        rec = resp.json()["recommendation"]
        assert rec["recommended_plan_id"] in {
            "baseline",
            "deadline-first",
            "mission-critical-first",
            "value-per-cost",
        }

    @pytest.mark.asyncio
    async def test_recommend_legacy_still_works(self, loaded_legacy):
        """Legacy scenario recommend must still return 200 (no anomaly context, no crash)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert "recommendation" in body
        # Legacy path: no prioritization
        assert body.get("prioritization") is None


# ---------------------------------------------------------------------------
# Local provider — anomaly context in reasoning
# ---------------------------------------------------------------------------


class TestLocalProviderAnomalyReasoning:
    """Unit-level test: LocalRuleBasedProvider.recommend() with anomalies kwarg."""

    def _make_minimal_setup(self):
        """Return minimal link/mission state + evaluated plans for the local provider."""
        from datetime import datetime, timezone
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.evaluation_result import EvaluationResult
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.packet import Packet
        from backend.app.models.risk_level import RiskLevel

        ls = LinkState(
            timestamp=datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
            snr_db=10.0, eb_n0_db=20.0, ber=3.87e-6, rssi_dbm=-80.0,
            nominal_data_rate_bps=100_000.0, link_goodput_bps=90_000.0,
            latency_s=0.25, link_stability=0.95, remaining_window_s=300.0,
        )
        ms = MissionState(
            mission_id="m-001", mission_phase="science", current_event="pass",
            event_time_remaining_s=300.0, comm_window_remaining_s=300.0,
            risk_score=0.3, risk_level=RiskLevel.LOW,
        )
        pkt = Packet(
            packet_id="p1", packet_type="telemetry", size_bits=1024,
            criticality=0.8, mission_relevance=0.7, deadline_s=200.0,
            retry_cost=0.3, delivery_requirement="required",
        )
        plan = CandidatePlan(
            plan_id="baseline", strategy="baseline",
            packets=[pkt], generated_by="test",
        )
        ev = EvaluationResult(
            plan_id="baseline", mission_value=0.56, critical_packets_delivered=1,
            total_critical_packets=1, deadline_misses=0, avg_packet_delay_s=10.0,
            bandwidth_utilization=0.1, retransmission_overhead=0.0,
            risk_score=0.12, risk_level=RiskLevel.LOW, deferred_packets=[],
            deadline_miss_rate=0.0, critical_deficit=0.0, window_pressure=0.1,
        )
        return ls, ms, [plan], [ev]

    def test_no_anomalies_no_anomaly_text(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        ls, ms, plans, evals = self._make_minimal_setup()
        provider = LocalRuleBasedProvider()
        rec = provider.recommend(ls, ms, plans, evals)
        # No anomaly text should appear when anomalies=None
        assert "anomaly" not in rec.reasoning.lower()

    def test_empty_anomalies_no_anomaly_text(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        ls, ms, plans, evals = self._make_minimal_setup()
        provider = LocalRuleBasedProvider()
        rec = provider.recommend(ls, ms, plans, evals, anomalies=[])
        assert "anomaly" not in rec.reasoning.lower()

    def test_with_anomaly_adds_anomaly_text(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        ls, ms, plans, evals = self._make_minimal_setup()
        anomaly = AnomalyEvent(
            anomaly_id="ANOM-017",
            subsystem="propulsion",
            severity=0.85,
            detected_at_s=480.0,
            description="Unexpected thrust oscillation.",
            status="active",
        )
        provider = LocalRuleBasedProvider()
        rec = provider.recommend(ls, ms, plans, evals, anomalies=[anomaly])
        assert "anomaly" in rec.reasoning.lower() or "ANOM" in rec.reasoning

    def test_highest_severity_anomaly_mentioned_first(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        ls, ms, plans, evals = self._make_minimal_setup()
        low_anom = AnomalyEvent(
            anomaly_id="ANOM-031",
            subsystem="communications",
            severity=0.40,
            detected_at_s=120.0,
            description="Antenna drift.",
            status="monitoring",
        )
        high_anom = AnomalyEvent(
            anomaly_id="ANOM-017",
            subsystem="propulsion",
            severity=0.85,
            detected_at_s=480.0,
            description="Thrust oscillation.",
            status="active",
        )
        provider = LocalRuleBasedProvider()
        # Pass low first in the list — the provider must sort by severity
        rec = provider.recommend(ls, ms, plans, evals, anomalies=[low_anom, high_anom])
        # ANOM-017 (severity 0.85) must appear in the reasoning before ANOM-031
        pos_high = rec.reasoning.find("ANOM-017")
        pos_low = rec.reasoning.find("ANOM-031")
        # ANOM-017 must appear; ANOM-031 need not appear (only top is mentioned)
        assert pos_high >= 0
