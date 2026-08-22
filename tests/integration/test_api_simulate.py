"""Integration tests for POST /simulate and POST /simulate/what-if."""

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
async def test_simulate_returns_503_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/simulate", json={"plan_id": "baseline"})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_simulate_returns_simulation_result(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/simulate", json={"plan_id": "baseline", "seed": 42})
    assert resp.status_code == 200
    body = resp.json()
    assert "plan_id" in body
    assert "delivered_packets" in body
    assert "elapsed_time_s" in body
    assert "link_state" in body
    assert "mission_state" in body


@pytest.mark.asyncio
async def test_simulate_unknown_plan_returns_404(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/simulate", json={"plan_id": "does-not-exist"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_simulate_with_seed_is_deterministic(loaded_state):
    """Same seed → identical simulation results."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/simulate", json={"plan_id": "baseline", "seed": 7})
        # Reload scenario to reset state for second run.
        app_state.load_scenario("data/scenarios/nominal_pass.json")
        r2 = await client.post("/simulate", json={"plan_id": "baseline", "seed": 7})

    assert r1.json()["delivered_packets"] == r2.json()["delivered_packets"]
    assert r1.json()["elapsed_time_s"] == pytest.approx(r2.json()["elapsed_time_s"])


@pytest.mark.asyncio
async def test_simulate_updates_server_state(loaded_state):
    """After simulate, GET /state should reflect the updated window."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial_state = await client.get("/state")
        await client.post("/simulate", json={"plan_id": "baseline", "seed": 0})
        updated_state = await client.get("/state")

    initial_window = initial_state.json()["link_state"]["remaining_window_s"]
    updated_window = updated_state.json()["link_state"]["remaining_window_s"]
    # Window should have decreased after simulation.
    assert updated_window < initial_window


@pytest.mark.asyncio
async def test_what_if_does_not_mutate_state(loaded_state):
    """POST /simulate/what-if must not change server state."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        initial_state = await client.get("/state")
        queue_resp = await client.get("/queue")
        plan = queue_resp.json()
        await client.post("/simulate/what-if", json={"plan": plan, "seed": 0})
        post_state = await client.get("/state")

    assert initial_state.json()["link_state"]["remaining_window_s"] == post_state.json()["link_state"]["remaining_window_s"]


@pytest.mark.asyncio
async def test_what_if_returns_simulation_result(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        queue_resp = await client.get("/queue")
        plan = queue_resp.json()
        resp = await client.post("/simulate/what-if", json={"plan": plan, "seed": 1})
    assert resp.status_code == 200
    assert "elapsed_time_s" in resp.json()
