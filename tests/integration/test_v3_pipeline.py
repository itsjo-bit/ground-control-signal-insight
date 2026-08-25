"""Integration tests for Phase 2E-B: v3 scenario (150 data products) pipeline.

Covers:
- /state returns correct metadata for v3 (150 products, 3 anomalies)
- /plans/generate works for v3 (bridges 150 data_products → packets)
- /agent/recommend works with the local provider on v3
- candidate_count <= 50 for 150-product v3 scenario
- ranked_products reference valid product IDs
- Plans are generated and deterministic evaluation succeeds
- v2 scenario is completely unaffected
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state

# Paths
_V3_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json")
_V2_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
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
def loaded_v2():
    app_state.load_scenario(_V2_SCENARIO)


# ---------------------------------------------------------------------------
# /state — v3 metadata fields
# ---------------------------------------------------------------------------


class TestStateV3:
    @pytest.mark.asyncio
    async def test_state_returns_200_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_state_data_products_count_is_150(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["data_products_count"] == 150

    @pytest.mark.asyncio
    async def test_state_anomalies_count_is_3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["anomalies_count"] == 3

    @pytest.mark.asyncio
    async def test_state_anomalies_have_expected_ids(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        anomaly_ids = {a["anomaly_id"] for a in resp.json()["anomalies"]}
        assert anomaly_ids == {"ANOM-017", "ANOM-021", "ANOM-034"}

    @pytest.mark.asyncio
    async def test_state_link_state_present_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "link_state" in body
        assert "mission_state" in body

    @pytest.mark.asyncio
    async def test_v2_still_returns_50_products(self, loaded_v2):
        """v2 scenario must be completely unaffected."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["data_products_count"] == 50


# ---------------------------------------------------------------------------
# /plans/generate — v3 bridge path
# ---------------------------------------------------------------------------


class TestPlansGenerateV3:
    @pytest.mark.asyncio
    async def test_generate_returns_four_plans_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        assert resp.status_code == 200
        assert len(resp.json()) == 4

    @pytest.mark.asyncio
    async def test_generate_strategy_names_v3(self, loaded_v3):
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
    async def test_generate_packets_come_from_150_products(self, loaded_v3):
        """Each plan must contain 150 packets bridged from data_products."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        for plan in resp.json():
            assert len(plan["packets"]) == 150

    @pytest.mark.asyncio
    async def test_generate_packet_ids_match_product_ids(self, loaded_v3):
        expected_ids = {dp.product_id for dp in app_state.active_scenario.data_products}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        baseline = next(p for p in resp.json() if p["strategy"] == "baseline")
        plan_ids = {pkt["packet_id"] for pkt in baseline["packets"]}
        assert plan_ids == expected_ids

    @pytest.mark.asyncio
    async def test_generate_mission_critical_first_v3(self, loaded_v3):
        """The mission_critical_first plan must start with the highest-criticality product."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        mc_plan = next(p for p in resp.json() if p["strategy"] == "mission_critical_first")
        first_id = mc_plan["packets"][0]["packet_id"]
        max_criticality = max(dp.criticality for dp in app_state.active_scenario.data_products)
        first_criticality = next(
            dp.criticality
            for dp in app_state.active_scenario.data_products
            if dp.product_id == first_id
        )
        assert first_criticality == pytest.approx(max_criticality)


# ---------------------------------------------------------------------------
# /agent/recommend — v3 with local provider
# ---------------------------------------------------------------------------


class TestAgentRecommendV3:
    @pytest.fixture(autouse=True)
    def force_local_provider(self, monkeypatch):
        monkeypatch.setenv("GCSI_AI_PROVIDER", "local")

    @pytest.mark.asyncio
    async def test_recommend_returns_200_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_recommend_provider_is_local_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.json()["provider"] == "Local"

    @pytest.mark.asyncio
    async def test_recommend_candidate_count_is_50_for_v3(self, loaded_v3):
        """150 products must result in candidate_count <= 50."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body.get("candidate_count") is not None
        assert body["candidate_count"] <= 50

    @pytest.mark.asyncio
    async def test_recommend_returns_prioritization_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        p = body.get("prioritization")
        assert p is not None
        assert "ranked_products" in p
        assert "overall_reasoning" in p
        assert "confidence" in p
        assert len(p["ranked_products"]) > 0

    @pytest.mark.asyncio
    async def test_recommend_ranked_products_are_valid_ids_v3(self, loaded_v3):
        """Every ranked product ID must be a real product from the v3 scenario."""
        product_ids = {dp.product_id for dp in app_state.active_scenario.data_products}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        ranked = resp.json()["prioritization"]["ranked_products"]
        for item in ranked:
            assert item["product_id"] in product_ids, (
                f"ranked product_id {item['product_id']!r} not in v3 scenario"
            )

    @pytest.mark.asyncio
    async def test_recommend_plan_id_is_valid_v3(self, loaded_v3):
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
    async def test_recommend_contains_anomaly_context_v3(self, loaded_v3):
        """With 3 active anomalies including ANOM-017, reasoning must mention anomaly."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        reasoning = resp.json()["recommendation"]["reasoning"]
        assert "anomaly" in reasoning.lower() or "ANOM" in reasoning

    @pytest.mark.asyncio
    async def test_recommend_evaluation_risk_score_present_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        rec = resp.json()["recommendation"]
        assert "risk_score" in rec
        assert 0.0 <= rec["risk_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_v2_still_works_alongside_v3(self, loaded_v2):
        """v2 scenario must continue to work after v3 was added."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("prioritization") is not None
        assert body["candidate_count"] <= 50
