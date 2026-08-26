"""Phase 4.2B: Tests for POST /plans/assess — non-mutating manual plan assessment.

Verifies:
- POST /plans/assess returns plan + evaluation + mission_outcome + capacity_summary
- Works with ASTERIA-7 products
- Does NOT mutate server state
- Does NOT require an AI recommendation
- Rejects unknown product IDs with 422
"""
from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state

_SCENARIOS_DIR = Path(__file__).parents[2] / "data" / "scenarios"
_ASTERIA_SCENARIO = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")
_V3_SCENARIO = str(_SCENARIOS_DIR / "mission_data_v3.json")


@pytest.fixture(autouse=True)
def reset_state():
    """Isolate every test — clear global state before and after."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


@pytest.fixture
def loaded_asteria():
    app_state.load_scenario(_ASTERIA_SCENARIO)


@pytest.fixture
def loaded_v3():
    app_state.load_scenario(_V3_SCENARIO)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestManualAssessment:
    @pytest.mark.asyncio
    async def test_assess_returns_plan_and_evaluation(self, loaded_asteria):
        """POST /plans/assess returns a plan, evaluation, and capacity_summary."""
        # Use first 3 anchor products
        product_ids = ["TEL-THERM-HR-042", "DIAG-THERM-EVT-017", "TEL-PWR-CORR-031"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200
        body = resp.json()
        assert "plan" in body
        assert "evaluation" in body
        assert "capacity_summary" in body
        assert body["plan"]["plan_id"] == "operator-manual-assess"
        assert len(body["plan"]["packets"]) == 3

    @pytest.mark.asyncio
    async def test_assess_evaluation_has_risk_score(self, loaded_asteria):
        """Assessment evaluation includes a risk_score."""
        product_ids = ["TEL-THERM-HR-042", "DIAG-THERM-EVT-017"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200
        body = resp.json()
        assert "risk_score" in body["evaluation"]
        assert "risk_level" in body["evaluation"]
        assert 0.0 <= body["evaluation"]["risk_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_assess_does_not_mutate_issued_plans(self, loaded_asteria):
        """POST /plans/assess must NOT invalidate the issued-plan registry."""
        product_ids = ["TEL-THERM-HR-042"]
        # Assess doesn't touch the registry
        registry_size_before = len(app_state.issued_plans)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/plans/assess", json={"product_ids": product_ids})
        assert len(app_state.issued_plans) == registry_size_before

    @pytest.mark.asyncio
    async def test_assess_does_not_require_ai_recommendation(self, loaded_asteria):
        """Manual assessment works without any AI recommendation being present."""
        # No recommendation has been requested — registry is empty
        assert len(app_state.issued_plans) == 0
        product_ids = ["FDIR-THERM-017", "CMD-THERM-571"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_assess_rejects_unknown_product_ids(self, loaded_asteria):
        """Unknown product IDs must be rejected with HTTP 422."""
        product_ids = ["NONEXISTENT-PRODUCT-000"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_assess_returns_mission_outcome_for_asteria(self, loaded_asteria):
        """For ASTERIA-7 (data_products scenario), mission_outcome is returned."""
        product_ids = ["TEL-THERM-HR-042", "DIAG-THERM-EVT-017", "FDIR-THERM-017"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200
        body = resp.json()
        # For data_products scenarios, mission_outcome should be present (not null)
        assert body["mission_outcome"] is not None

    @pytest.mark.asyncio
    async def test_assess_capacity_summary_structure(self, loaded_asteria):
        """Capacity summary must include expected keys."""
        product_ids = ["TEL-THERM-HR-042"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200
        cs = resp.json()["capacity_summary"]
        assert "available_capacity_bits" in cs
        assert "selected_bits" in cs
        assert "selected_count" in cs
        assert "exceeds_capacity" in cs
        assert cs["selected_count"] == 1

    @pytest.mark.asyncio
    async def test_assess_works_with_v3_scenario(self, loaded_v3):
        """POST /plans/assess also works for the v3 scenario."""
        # Get some product IDs from the v3 scenario
        products = app_state.active_scenario.data_products
        product_ids = [p.product_id for p in products[:3]]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_assess_503_when_no_scenario(self):
        """Returns 503 when no scenario is loaded."""
        product_ids = ["TEL-THERM-HR-042"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})
        assert resp.status_code == 503
