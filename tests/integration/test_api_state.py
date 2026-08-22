"""Integration tests for GET /state and GET /health."""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state


@pytest.fixture(autouse=True)
def reset_state():
    """Reset module-level state between tests."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None


@pytest.fixture
def loaded_state():
    """Pre-load the nominal scenario into state."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")


@pytest.mark.asyncio
async def test_health_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_reports_no_scenario_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.json()["scenario_loaded"] is False


@pytest.mark.asyncio
async def test_health_reports_scenario_after_load(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.json()["scenario_loaded"] is True


@pytest.mark.asyncio
async def test_state_returns_503_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/state")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_state_returns_link_and_mission_state(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/state")
    assert resp.status_code == 200
    body = resp.json()
    assert "link_state" in body
    assert "mission_state" in body
    assert "ber" in body["link_state"]
    assert "risk_score" in body["mission_state"]
