"""Integration tests for POST /plans/generate and POST /plans/evaluate."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state


@pytest.fixture(autouse=True)
def reset_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None


@pytest.fixture
def loaded_state():
    app_state.load_scenario("data/scenarios/nominal_pass.json")


@pytest.mark.asyncio
async def test_generate_returns_503_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/plans/generate")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_generate_returns_four_plans(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/plans/generate")
    assert resp.status_code == 200
    plans = resp.json()
    assert len(plans) == 4


@pytest.mark.asyncio
async def test_generate_all_strategy_names_present(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/plans/generate")
    strategies = {p["strategy"] for p in resp.json()}
    assert "baseline" in strategies
    assert "deadline_first" in strategies
    assert "mission_critical_first" in strategies
    assert "value_per_cost" in strategies


@pytest.mark.asyncio
async def test_evaluate_returns_evaluation_result(loaded_state):
    """Fetch the baseline queue plan and evaluate it."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        queue_resp = await client.get("/queue")
        plan = queue_resp.json()
        eval_resp = await client.post("/plans/evaluate", json=plan)

    assert eval_resp.status_code == 200
    body = eval_resp.json()
    assert "plan_id" in body
    assert "risk_score" in body
    assert "risk_level" in body
    assert "mission_value" in body
    assert 0.0 <= body["risk_score"] <= 1.0


@pytest.mark.asyncio
async def test_evaluate_returns_503_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/plans/evaluate", json={
            "plan_id": "x", "strategy": "baseline", "packets": [], "generated_by": "test"
        })
    assert resp.status_code == 503
