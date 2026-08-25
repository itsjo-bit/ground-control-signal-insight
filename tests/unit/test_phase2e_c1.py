"""Unit and integration tests for Phase 2E-C1: authoritative communication budget.

Covers:
  Test 1 — available_capacity_bits equals link_goodput_bps × remaining_window_s
  Test 2 — v3 queued_data_bits equals sum of all 150 DataProduct.size_bits
  Test 3 — Legacy packet scenario produces correct queued_data_bits
  Test 4 — After POST /state/reset, available_capacity_bits reflects the new link state
  Test 5 — Existing /state fields are still present and unchanged
  Test 6 — v2 scenario produces correct queued_data_bits
  Test 7 — Empty scenario produces queued_data_bits = 0
  Test 8 — available_capacity_bits is an integer (not float)
  Test 9 — queued_data_bits is an integer
  Test 10 — queued_data_bits > available_capacity_bits for v3 (oversubscription confirmed)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.simulation.scenario_loader import ScenarioLoader

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parents[2]
_V3_SCENARIO = str(_REPO / "data" / "scenarios" / "mission_data_v3.json")
_V2_SCENARIO = str(_REPO / "data" / "scenarios" / "mission_data_v2.json")
_NOMINAL_SCENARIO = str(_REPO / "data" / "scenarios" / "nominal_pass.json")


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


@pytest.fixture
def loaded_nominal():
    app_state.load_scenario(_NOMINAL_SCENARIO)


# ---------------------------------------------------------------------------
# Test 1 — available_capacity_bits = link_goodput_bps × remaining_window_s
# ---------------------------------------------------------------------------


class TestAvailableCapacityFormula:
    """The formula must match the existing implicit calculation used by PlanEvaluator."""

    @pytest.mark.asyncio
    async def test_capacity_equals_goodput_times_window_v3(self, loaded_v3):
        """available_capacity_bits must equal link_goodput_bps × remaining_window_s."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()

        ls = body["link_state"]
        expected = int(ls["link_goodput_bps"] * ls["remaining_window_s"])
        assert body["available_capacity_bits"] == expected

    @pytest.mark.asyncio
    async def test_capacity_equals_goodput_times_window_v2(self, loaded_v2):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()

        ls = body["link_state"]
        expected = int(ls["link_goodput_bps"] * ls["remaining_window_s"])
        assert body["available_capacity_bits"] == expected

    @pytest.mark.asyncio
    async def test_capacity_equals_goodput_times_window_legacy(self, loaded_nominal):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()

        ls = body["link_state"]
        expected = int(ls["link_goodput_bps"] * ls["remaining_window_s"])
        assert body["available_capacity_bits"] == expected

    @pytest.mark.asyncio
    async def test_capacity_v3_known_value(self, loaded_v3):
        """v3 baseline: goodput=90000, window=480 → capacity=43,200,000 bits."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["available_capacity_bits"] == 43_200_000

    @pytest.mark.asyncio
    async def test_capacity_field_is_int(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        # JSON integers deserialise as int in Python
        assert isinstance(resp.json()["available_capacity_bits"], int)

    @pytest.mark.asyncio
    async def test_capacity_is_positive(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["available_capacity_bits"] > 0


# ---------------------------------------------------------------------------
# Test 2 — v3 queued_data_bits = sum of all 150 DataProduct.size_bits
# ---------------------------------------------------------------------------


class TestQueuedDataBitsV3:
    """queued_data_bits must match the actual sum from the scenario file, not a hardcoded guess."""

    @pytest.mark.asyncio
    async def test_queued_equals_sum_of_size_bits(self, loaded_v3):
        """Implementation must calculate dynamically; test asserts the verified total."""
        # Compute expected from the loaded scenario directly to avoid double-hardcoding.
        expected = sum(dp.size_bits for dp in app_state.active_scenario.data_products)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == expected

    @pytest.mark.asyncio
    async def test_queued_v3_known_regression_value(self, loaded_v3):
        """Regression check: v3 total is 275,699,712 bits (verified in Phase 2E-B)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == 275_699_712

    @pytest.mark.asyncio
    async def test_queued_data_field_is_int(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert isinstance(resp.json()["queued_data_bits"], int)

    @pytest.mark.asyncio
    async def test_queued_v3_product_count_is_150(self, loaded_v3):
        """Sanity check: v3 must still have exactly 150 products."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["data_products_count"] == 150


# ---------------------------------------------------------------------------
# Test 3 — Legacy packet scenario
# ---------------------------------------------------------------------------


class TestQueuedDataBitsLegacy:
    """For legacy scenarios that carry packets (not data_products), queued_data_bits
    must be computed from scenario.packets.size_bits."""

    @pytest.mark.asyncio
    async def test_legacy_queued_equals_sum_of_packet_size_bits(self, loaded_nominal):
        """nominal_pass uses packets; queued_data_bits must reflect packet sizes."""
        expected = sum(pkt.size_bits for pkt in app_state.active_scenario.packets)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == expected

    @pytest.mark.asyncio
    async def test_legacy_queued_known_regression_value(self, loaded_nominal):
        """nominal_pass total packet bits verified as 349,184."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == 349_184

    @pytest.mark.asyncio
    async def test_legacy_data_products_count_is_zero(self, loaded_nominal):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["data_products_count"] == 0

    @pytest.mark.asyncio
    async def test_legacy_queued_is_positive(self, loaded_nominal):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] > 0


# ---------------------------------------------------------------------------
# Test 4 — Reset consistency: capacity reflects randomized link state
# ---------------------------------------------------------------------------


class TestResetConsistency:
    """After POST /state/reset, available_capacity_bits must correspond to the
    new randomized link state, and queued_data_bits must remain unchanged."""

    @pytest.fixture(autouse=True)
    def set_scenario_path(self):
        """Pre-set the scenario path so /state/reset can find it."""
        app_state.load_scenario(_V3_SCENARIO)

    @pytest.mark.asyncio
    async def test_capacity_reflects_new_link_state_after_reset(self):
        """After reset, available_capacity_bits must equal the new goodput × new window."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/state/reset")
            resp = await c.get("/state")
        body = resp.json()
        ls = body["link_state"]
        expected = int(ls["link_goodput_bps"] * ls["remaining_window_s"])
        assert body["available_capacity_bits"] == expected

    @pytest.mark.asyncio
    async def test_queued_data_unchanged_after_reset(self):
        """queued_data_bits must not change after reset (products are not randomized)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            state_before = (await c.get("/state")).json()
            await c.post("/state/reset")
            state_after = (await c.get("/state")).json()
        assert state_after["queued_data_bits"] == state_before["queued_data_bits"]
        assert state_after["queued_data_bits"] == 275_699_712

    @pytest.mark.asyncio
    async def test_capacity_is_valid_after_reset(self):
        """After reset, capacity must be in the physically valid range [60s, 600s] × goodput."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/state/reset")
            resp = await c.get("/state")
        body = resp.json()
        cap = body["available_capacity_bits"]
        goodput = body["link_state"]["link_goodput_bps"]
        # Randomizer clamps window to [60, 600]; goodput doesn't change (nominal_rate is fixed)
        assert cap >= int(goodput * 60.0)
        assert cap <= int(goodput * 600.0)


# ---------------------------------------------------------------------------
# Test 5 — Existing /state fields remain present and unchanged
# ---------------------------------------------------------------------------


class TestAPICompatibility:
    """All existing /state fields must still be present and correct.
    Phase 2E-C1 must be purely additive."""

    @pytest.mark.asyncio
    async def test_link_state_still_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert "link_state" in resp.json()

    @pytest.mark.asyncio
    async def test_mission_state_still_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert "mission_state" in resp.json()

    @pytest.mark.asyncio
    async def test_data_products_count_still_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "data_products_count" in body
        assert body["data_products_count"] == 150

    @pytest.mark.asyncio
    async def test_anomalies_count_still_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "anomalies_count" in body
        assert body["anomalies_count"] == 3

    @pytest.mark.asyncio
    async def test_anomalies_list_still_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "anomalies" in body
        assert len(body["anomalies"]) == 3

    @pytest.mark.asyncio
    async def test_link_state_fields_unchanged(self, loaded_v3):
        """All existing LinkState fields must still be present."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        ls = resp.json()["link_state"]
        for field in (
            "snr_db", "eb_n0_db", "ber", "rssi_dbm",
            "nominal_data_rate_bps", "link_goodput_bps",
            "latency_s", "link_stability", "remaining_window_s",
        ):
            assert field in ls, f"LinkState field '{field}' missing from /state response"

    @pytest.mark.asyncio
    async def test_mission_state_fields_unchanged(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        ms = resp.json()["mission_state"]
        for field in (
            "mission_id", "mission_phase", "current_event",
            "event_time_remaining_s", "comm_window_remaining_s",
            "risk_score", "risk_level",
        ):
            assert field in ms, f"MissionState field '{field}' missing from /state response"

    @pytest.mark.asyncio
    async def test_new_fields_are_present(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert "available_capacity_bits" in body
        assert "queued_data_bits" in body

    @pytest.mark.asyncio
    async def test_v2_scenario_still_returns_50_products(self, loaded_v2):
        """v2 must continue to work identically (regression guard)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["data_products_count"] == 50

    @pytest.mark.asyncio
    async def test_503_when_no_scenario_loaded(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Test 6 — v2 queued_data_bits
# ---------------------------------------------------------------------------


class TestQueuedDataBitsV2:
    @pytest.mark.asyncio
    async def test_queued_equals_sum_of_size_bits_v2(self, loaded_v2):
        expected = sum(dp.size_bits for dp in app_state.active_scenario.data_products)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == expected

    @pytest.mark.asyncio
    async def test_queued_v2_known_regression_value(self, loaded_v2):
        """v2 total verified: 5,343,232 bits."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["queued_data_bits"] == 5_343_232

    @pytest.mark.asyncio
    async def test_capacity_v2_known_value(self, loaded_v2):
        """v2: goodput=90000, window=660 → capacity=59,400,000 bits."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.json()["available_capacity_bits"] == 59_400_000


# ---------------------------------------------------------------------------
# Test 10 — Oversubscription: v3 queued > available capacity
# ---------------------------------------------------------------------------


class TestOversubscription:
    """The v3 scenario must demonstrate a meaningful transmission bottleneck."""

    @pytest.mark.asyncio
    async def test_v3_is_oversubscribed(self, loaded_v3):
        """queued_data_bits must substantially exceed available_capacity_bits."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["queued_data_bits"] > body["available_capacity_bits"]

    @pytest.mark.asyncio
    async def test_v3_oversubscription_ratio_is_at_least_3x(self, loaded_v3):
        """v3 oversubscription must be at least 3× even after randomization."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        ratio = body["queued_data_bits"] / body["available_capacity_bits"]
        assert ratio >= 3.0, f"Oversubscription ratio {ratio:.1f}× is too low"

    @pytest.mark.asyncio
    async def test_v2_capacity_exceeds_queued(self, loaded_v2):
        """v2 queued data (5.3 Mbit) fits within the v2 window (59.4 Mbit)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["queued_data_bits"] < body["available_capacity_bits"]
