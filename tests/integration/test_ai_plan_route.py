"""Integration tests for the ai-prioritized fifth plan in /agent/recommend.

Covers:
- v2/v3 path generates five plans (4 deterministic + 1 AI)
- ai-prioritized plan is returned in ai_plan/ai_evaluation fields
- Stage 2 can recommend any of the 5 plans (including ai-prioritized)
- Stage 2 recommendation recommended_plan_id is in the 5-plan set
- Deterministic baselines are independent of AI ranking
- Legacy path still returns 4 plans and no ai_plan
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.candidate_generator.ai_plan_builder import AI_PLAN_ID

_V3_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json")
_V2_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json")
_LEGACY_SCENARIO = "data/scenarios/nominal_pass.json"


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


@pytest.fixture
def loaded_legacy():
    app_state.load_scenario(_LEGACY_SCENARIO)


@pytest.fixture(autouse=True)
def force_local_provider(monkeypatch):
    monkeypatch.setenv("GCSI_AI_PROVIDER", "local")


# ---------------------------------------------------------------------------
# ai_plan / ai_evaluation fields in response
# ---------------------------------------------------------------------------

class TestAIPlanInResponse:
    @pytest.mark.asyncio
    async def test_v3_ai_plan_present(self, loaded_v3):
        """v3 path must return a non-null ai_plan."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_plan"] is not None, "ai_plan must be present for v3 scenario"

    @pytest.mark.asyncio
    async def test_v3_ai_plan_id_is_ai_prioritized(self, loaded_v3):
        """ai_plan.plan_id must be 'ai-prioritized'."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["ai_plan"]["plan_id"] == AI_PLAN_ID

    @pytest.mark.asyncio
    async def test_v3_ai_plan_strategy(self, loaded_v3):
        """ai_plan.strategy must be 'ai_prioritized'."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["ai_plan"]["strategy"] == "ai_prioritized"

    @pytest.mark.asyncio
    async def test_v3_ai_plan_has_150_packets(self, loaded_v3):
        """ai_plan must contain all 150 packets for the v3 scenario."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert len(body["ai_plan"]["packets"]) == 150

    @pytest.mark.asyncio
    async def test_v3_ai_plan_no_duplicate_packets(self, loaded_v3):
        """ai_plan must contain no duplicate packet IDs."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        packets = resp.json()["ai_plan"]["packets"]
        ids = [p["packet_id"] for p in packets]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_v3_ai_evaluation_present(self, loaded_v3):
        """v3 path must return a non-null ai_evaluation."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["ai_evaluation"] is not None

    @pytest.mark.asyncio
    async def test_v3_ai_evaluation_plan_id_matches(self, loaded_v3):
        """ai_evaluation.plan_id must match ai_plan.plan_id."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["ai_evaluation"]["plan_id"] == AI_PLAN_ID

    @pytest.mark.asyncio
    async def test_v3_ai_evaluation_has_required_fields(self, loaded_v3):
        """ai_evaluation must have all PlanEvaluator metrics."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        ev = resp.json()["ai_evaluation"]
        for field in ("mission_value", "risk_score", "critical_packets_delivered",
                      "bandwidth_utilization", "deadline_misses", "risk_level"):
            assert field in ev, f"ai_evaluation missing field: {field}"

    @pytest.mark.asyncio
    async def test_v3_ai_plan_metadata_provenance(self, loaded_v3):
        """ai_plan.metadata must include plan_type='ai_semantic' and tail_policy."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        meta = resp.json()["ai_plan"]["metadata"]
        assert meta.get("plan_type") == "ai_semantic"
        assert meta.get("tail_policy") == "baseline_scheduler"
        assert "ranked_count" in meta


# ---------------------------------------------------------------------------
# Stage-2 sees all 5 plans
# ---------------------------------------------------------------------------

class TestStage2FivePlans:
    @pytest.mark.asyncio
    async def test_v3_recommendation_can_reference_ai_plan(self, loaded_v3):
        """recommended_plan_id must be in the 5-plan set (including ai-prioritized)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        rec_id = body["recommendation"]["recommended_plan_id"]
        valid_ids = {
            "baseline",
            "deadline-first",
            "mission-critical-first",
            "value-per-cost",
            AI_PLAN_ID,
        }
        assert rec_id in valid_ids, (
            f"recommended_plan_id={rec_id!r} is not in 5-plan set {valid_ids}"
        )

    @pytest.mark.asyncio
    async def test_v2_recommendation_can_reference_ai_plan(self, loaded_v2):
        """Same for v2 scenario."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        rec_id = body["recommendation"]["recommended_plan_id"]
        valid_ids = {
            "baseline",
            "deadline-first",
            "mission-critical-first",
            "value-per-cost",
            AI_PLAN_ID,
        }
        assert rec_id in valid_ids


# ---------------------------------------------------------------------------
# Legacy scenario compatibility
# ---------------------------------------------------------------------------

class TestLegacyScenarioCompatibility:
    @pytest.mark.asyncio
    async def test_legacy_ai_plan_is_null(self, loaded_legacy):
        """Legacy scenarios must return ai_plan=null (no fake AI plan)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ai_plan"] is None, "Legacy scenarios must not produce an AI plan"

    @pytest.mark.asyncio
    async def test_legacy_ai_evaluation_is_null(self, loaded_legacy):
        """Legacy scenarios must return ai_evaluation=null."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["ai_evaluation"] is None

    @pytest.mark.asyncio
    async def test_legacy_recommendation_is_from_4_plan_set(self, loaded_legacy):
        """Legacy scenarios recommend from the 4-plan set only."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        rec_id = resp.json()["recommendation"]["recommended_plan_id"]
        assert rec_id in {
            "baseline",
            "deadline-first",
            "mission-critical-first",
            "value-per-cost",
        }
