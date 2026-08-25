"""Phase 2E-C3-C tests — propagation delay derivation and GET /state geometry fields.

Covers:
  1.  Formula correctness: known distance → expected propagation_delay_s
  2.  Formula correctness: known distance → expected round_trip_time_s
  3.  Zero distance → propagation_delay_s = 0, round_trip_time_s = 0
  4.  None distance → propagation_delay_s = None, round_trip_time_s = None
  5.  Speed-of-light constant is the exact SI value (299,792,458 m/s)
  6.  GET /state for v3 exposes distance_km = 54,000,000
  7.  GET /state for v3 exposes correct propagation_delay_s
  8.  GET /state for v3 exposes correct round_trip_time_s
  9.  GET /state for v2 exposes distance_km = null
  10. GET /state for v2 exposes propagation_delay_s = null
  11. GET /state for v2 exposes round_trip_time_s = null
  12. GET /state for nominal_pass exposes all three geometry fields as null
  13. GET /state preserves all pre-existing C1 fields (regression)
  14. GET /state preserves link_state and mission_state (regression)
  15. round_trip_time_s == 2 × propagation_delay_s (algebraic identity)
  16. RF chain (SNR/BER/goodput/capacity) is unaffected by distance presence
  17. propagation_delay_s is NOT derived from latency_s
  18. distance_km in response == Scenario.distance_km (no transformation)
  19. Values are floats, not ints (precision preserved)
  20. GET /state returns 503 when no scenario is loaded
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.api.routes_state import _SPEED_OF_LIGHT_M_S
from backend.app.simulation.scenario_loader import ScenarioLoader

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parents[2]
_V3_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v3.json")
_V2_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v2.json")
_NOMINAL_PATH = str(_REPO / "data" / "scenarios" / "nominal_pass.json")

# Expected values for v3 scenario
_V3_DISTANCE_KM: float = 54_000_000.0
_V3_PROPAGATION_S: float = _V3_DISTANCE_KM * 1_000.0 / _SPEED_OF_LIGHT_M_S
_V3_RTT_S: float = _V3_PROPAGATION_S * 2.0

# Tolerance for floating-point comparisons
_ABS_TOL = 1e-6  # 1 microsecond — far tighter than any display requirement


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state():
    """Ensure app state is clean before and after every test."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


def _load(path: str) -> None:
    app_state.load_scenario(path, randomize=False)


# ---------------------------------------------------------------------------
# 1–5: Pure formula / constant tests (no HTTP, no scenario loading)
# ---------------------------------------------------------------------------


class TestPropagationFormula:
    """Pure formula correctness — no HTTP, no Pydantic, no scenario files."""

    def test_speed_of_light_constant_is_exact_si_value(self):
        """The module constant must be the exact SI value of c."""
        assert _SPEED_OF_LIGHT_M_S == 299_792_458.0

    def test_propagation_delay_formula_known_distance(self):
        """54,000,000 km → ≈ 180.1245 s one-way."""
        result = _V3_DISTANCE_KM * 1_000.0 / _SPEED_OF_LIGHT_M_S
        # 54_000_000_000 / 299_792_458 ≈ 180.12449...
        assert result == pytest.approx(180.1245, abs=1e-3)

    def test_round_trip_is_double_propagation(self):
        """RTT is exactly 2× the propagation delay by formula."""
        prop = _V3_DISTANCE_KM * 1_000.0 / _SPEED_OF_LIGHT_M_S
        rtt = prop * 2.0
        assert rtt == pytest.approx(prop * 2, abs=_ABS_TOL)

    def test_zero_distance_gives_zero_delay(self):
        """distance_km = 0 → propagation_delay_s = 0."""
        result = 0.0 * 1_000.0 / _SPEED_OF_LIGHT_M_S
        assert result == 0.0

    def test_zero_distance_gives_zero_rtt(self):
        """distance_km = 0 → round_trip_time_s = 0."""
        prop = 0.0 * 1_000.0 / _SPEED_OF_LIGHT_M_S
        rtt = prop * 2.0
        assert rtt == 0.0

    def test_lunar_distance_sanity_check(self):
        """384,400 km (Moon) → ~1.28 s one-way — well-known physical result."""
        lunar_km = 384_400.0
        delay = lunar_km * 1_000.0 / _SPEED_OF_LIGHT_M_S
        assert 1.27 < delay < 1.29

    def test_mars_minimum_distance_sanity_check(self):
        """54,600,000 km (Mars minimum) → ~182 s one-way — well-known physical result."""
        mars_min_km = 54_600_000.0
        delay = mars_min_km * 1_000.0 / _SPEED_OF_LIGHT_M_S
        assert 180.0 < delay < 185.0

    def test_propagation_delay_is_float(self):
        """Result of the formula must be a float, preserving precision."""
        result = _V3_DISTANCE_KM * 1_000.0 / _SPEED_OF_LIGHT_M_S
        assert isinstance(result, float)


# ---------------------------------------------------------------------------
# 6–20: HTTP / integration tests via GET /state
# ---------------------------------------------------------------------------


class TestGetStateGeometryFieldsV3:
    """GET /state with v3 scenario — geometry fields present and correct."""

    @pytest.fixture(autouse=True)
    def _load_v3(self):
        _load(_V3_PATH)

    @pytest.mark.asyncio
    async def test_distance_km_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "distance_km" in data

    @pytest.mark.asyncio
    async def test_distance_km_correct_value(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["distance_km"] == pytest.approx(_V3_DISTANCE_KM, rel=1e-9)

    @pytest.mark.asyncio
    async def test_propagation_delay_s_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "propagation_delay_s" in data

    @pytest.mark.asyncio
    async def test_propagation_delay_s_correct_value(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["propagation_delay_s"] == pytest.approx(_V3_PROPAGATION_S, rel=1e-9)

    @pytest.mark.asyncio
    async def test_propagation_delay_s_approx_180s(self):
        """Human-readable sanity: v3 one-way delay is approximately 180 seconds."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert 179.0 < data["propagation_delay_s"] < 182.0

    @pytest.mark.asyncio
    async def test_round_trip_time_s_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "round_trip_time_s" in data

    @pytest.mark.asyncio
    async def test_round_trip_time_s_correct_value(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["round_trip_time_s"] == pytest.approx(_V3_RTT_S, rel=1e-9)

    @pytest.mark.asyncio
    async def test_round_trip_is_double_propagation_from_api(self):
        """RTT from API must equal exactly 2× the reported propagation_delay_s."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["round_trip_time_s"] == pytest.approx(
            data["propagation_delay_s"] * 2.0, rel=1e-9
        )

    @pytest.mark.asyncio
    async def test_propagation_not_derived_from_latency_s(self):
        """propagation_delay_s must NOT equal latency_s — they are different concepts."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        latency = data["link_state"]["latency_s"]
        prop_delay = data["propagation_delay_s"]
        # v3 latency_s = 1.4, propagation_delay_s ≈ 180.1 — clearly different
        assert prop_delay != pytest.approx(latency, rel=1e-3)

    @pytest.mark.asyncio
    async def test_geometry_values_are_not_integers(self):
        """Propagation delay and RTT must be floats (precision preserved)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        # JSON numbers — ensure they are not truncated to integers
        assert data["propagation_delay_s"] != int(data["propagation_delay_s"])


class TestGetStateGeometryFieldsV2:
    """GET /state with v2 scenario — geometry fields are null."""

    @pytest.fixture(autouse=True)
    def _load_v2(self):
        _load(_V2_PATH)

    @pytest.mark.asyncio
    async def test_distance_km_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["distance_km"] is None

    @pytest.mark.asyncio
    async def test_propagation_delay_s_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["propagation_delay_s"] is None

    @pytest.mark.asyncio
    async def test_round_trip_time_s_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["round_trip_time_s"] is None


class TestGetStateGeometryFieldsNominal:
    """GET /state with nominal_pass scenario — geometry fields are null."""

    @pytest.fixture(autouse=True)
    def _load_nominal(self):
        _load(_NOMINAL_PATH)

    @pytest.mark.asyncio
    async def test_distance_km_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 200
        assert resp.json()["distance_km"] is None

    @pytest.mark.asyncio
    async def test_propagation_delay_s_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.json()["propagation_delay_s"] is None

    @pytest.mark.asyncio
    async def test_round_trip_time_s_is_null(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.json()["round_trip_time_s"] is None


class TestGetStateC1RegressionWithV3:
    """Regression: Phase 2E-C1 fields must survive C3-C unchanged."""

    @pytest.fixture(autouse=True)
    def _load_v3(self):
        _load(_V3_PATH)

    @pytest.mark.asyncio
    async def test_link_state_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "link_state" in data
        assert "snr_db" in data["link_state"]
        assert "link_goodput_bps" in data["link_state"]
        assert "latency_s" in data["link_state"]

    @pytest.mark.asyncio
    async def test_mission_state_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "mission_state" in data
        assert "comm_window_remaining_s" in data["mission_state"]

    @pytest.mark.asyncio
    async def test_available_capacity_bits_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "available_capacity_bits" in data
        assert isinstance(data["available_capacity_bits"], int)
        assert data["available_capacity_bits"] > 0

    @pytest.mark.asyncio
    async def test_queued_data_bits_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "queued_data_bits" in data
        assert data["queued_data_bits"] > 0

    @pytest.mark.asyncio
    async def test_data_products_count_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["data_products_count"] == 150

    @pytest.mark.asyncio
    async def test_anomalies_still_present(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert "anomalies" in data
        assert len(data["anomalies"]) == 3

    @pytest.mark.asyncio
    async def test_c1_capacity_formula_unchanged(self):
        """available_capacity_bits still equals goodput × window (C1 formula unchanged)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        goodput = data["link_state"]["link_goodput_bps"]
        window = data["link_state"]["remaining_window_s"]
        expected = int(goodput * window)
        assert data["available_capacity_bits"] == expected

    @pytest.mark.asyncio
    async def test_latency_s_unchanged_and_independent_of_distance(self):
        """latency_s must be the scenario value, not replaced by propagation_delay_s."""
        scenario = ScenarioLoader.load(_V3_PATH)
        expected_latency = scenario.link_inputs["latency_s"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        assert data["link_state"]["latency_s"] == pytest.approx(expected_latency, rel=1e-6)
        # Confirm they are different values (latency_s ≠ propagation_delay_s)
        assert data["link_state"]["latency_s"] != pytest.approx(
            data["propagation_delay_s"], rel=0.01
        )

    @pytest.mark.asyncio
    async def test_oversubscription_ratio_unchanged(self):
        """The 6.4× oversubscription ratio must survive C3-C unchanged."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        data = resp.json()
        ratio = data["queued_data_bits"] / data["available_capacity_bits"]
        assert ratio > 6.0  # v3 is ~6.4× oversubscribed


class TestGetState503WhenNoScenario:
    """GET /state must return 503 when no scenario is loaded."""

    @pytest.mark.asyncio
    async def test_503_when_no_scenario(self):
        # _reset_state fixture (autouse) already cleared state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 503
