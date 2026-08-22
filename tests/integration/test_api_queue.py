"""Integration tests for GET /queue."""

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
async def test_queue_returns_503_before_load():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_queue_returns_candidate_plan(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "baseline"
    assert body["plan_id"] == "baseline"
    assert isinstance(body["packets"], list)
    assert len(body["packets"]) > 0


@pytest.mark.asyncio
async def test_queue_plan_has_correct_packet_count(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue")
    body = resp.json()
    # nominal_pass.json has 5 packets
    assert len(body["packets"]) == 5


@pytest.mark.asyncio
async def test_queue_no_priority_field_on_packets(loaded_state):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/queue")
    body = resp.json()
    for pkt in body["packets"]:
        assert "priority" not in pkt
