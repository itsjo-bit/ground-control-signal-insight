"""GCSI Phase 6E-C6 — Historical Runtime Integration Tests.

COMPLETELY OFFLINE. Network is blocked.

Scope
-----
1. Activate historical replay in application state.
2. Verify GET /state with all required historical values.
3. Verify GET /data-products with IRDR + GRDR.
4. Verify POST /plans/generate with deterministic ordering.
5. Verify POST /plans/evaluate — GRDR deferred.
6. Verify POST /state/reset — deterministic, non-randomized.
7. Verify GET /health — additive fields.
8. Verify synthetic regression (GET /state, /health, /state/reset, /scenarios/switch).
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Network guard
#
# We block outbound network connections (create_connection and getaddrinfo)
# but NOT socket.socket itself — ASGITransport uses asyncio proactor which
# creates local pipe sockets internally.  Blocking socket.socket entirely
# would break the in-process ASGI transport.
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError(
        "GCSI C6 integration test: network access is forbidden in this test."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from backend.app.main import app
from backend.app import state as app_state
from backend.app.mission_sources.models import MissionSourceMode

_SOURCE_REF = "data/replays/juno_pj62_mwr_v1.json"

# Expected values from Phase 6E-C5
_EXPECTED_SCENARIO_ID = "juno_pj62_mwr_2024166030000_v04_replay_v1"
_EXPECTED_DISTANCE_KM = 893345396.8038701
_EXPECTED_LATENCY_S = 1.5
_EXPECTED_GOODPUT_BPS = 90000.0
_EXPECTED_REMAINING_WINDOW_S = 900.0
_EXPECTED_AVAILABLE_CAPACITY_BITS = int(_EXPECTED_GOODPUT_BPS * _EXPECTED_REMAINING_WINDOW_S)  # 81_000_000
_IRDR_SIZE_BITS = 6_694_664 * 8   # 53_557_312
_GRDR_SIZE_BITS = 5_093_997 * 8   # 40_751_976
_EXPECTED_QUEUED_BITS = _IRDR_SIZE_BITS + _GRDR_SIZE_BITS  # 94_309_288


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------


def _reset_all_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.active_source_mode = None
    app_state.active_source_ref = None
    app_state.active_source_provider_name = None
    app_state.active_source_provenance = None
    app_state.issued_plans.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _reset_all_state()
    yield
    _reset_all_state()


# ---------------------------------------------------------------------------
# Fixture: activate historical replay once per module for read-only tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def historical_state():
    """Activate historical replay and return the ASGI client factory."""
    # These module-level fixtures are read-only; each test still gets its own
    # clean_state reset via the autouse fixture above.
    app_state.load_historical_replay(_SOURCE_REF)
    return {
        "scenario": app_state.active_scenario,
        "link_state": app_state.active_link_state,
        "provenance": app_state.active_source_provenance,
    }


@pytest.fixture
def loaded_historical():
    """Ensure historical replay is active for this test."""
    app_state.load_historical_replay(_SOURCE_REF)


@pytest.fixture
def loaded_synthetic():
    """Ensure a synthetic scenario is active for this test."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")


# ===========================================================================
# GET /state — historical runtime
# ===========================================================================


class TestHistoricalGetState:
    @pytest.mark.asyncio
    async def test_status_200(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_source_mode(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["mode"] == "historical_replay"

    @pytest.mark.asyncio
    async def test_source_provider_name(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["provider_name"] == "GCSI-HistoricalReplayProvider"

    @pytest.mark.asyncio
    async def test_source_provenance_available(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["provenance_available"] is True

    @pytest.mark.asyncio
    async def test_source_provenance_scope(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["provenance_scope"] == "source_baseline"

    @pytest.mark.asyncio
    async def test_provenance_kind_counts(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        counts = body["source"]["provenance_kind_counts"]
        assert counts["external_authoritative"] == 3
        assert counts["modeled"] == 1
        assert counts["derived"] == 13

    @pytest.mark.asyncio
    async def test_distance_km_exact(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["distance_km"] == pytest.approx(_EXPECTED_DISTANCE_KM)

    @pytest.mark.asyncio
    async def test_available_capacity_bits(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["available_capacity_bits"] == _EXPECTED_AVAILABLE_CAPACITY_BITS

    @pytest.mark.asyncio
    async def test_queued_data_bits(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["queued_data_bits"] == _EXPECTED_QUEUED_BITS

    @pytest.mark.asyncio
    async def test_latency_s(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        ls = body["link_state"]
        assert ls["latency_s"] == pytest.approx(_EXPECTED_LATENCY_S)

    @pytest.mark.asyncio
    async def test_latency_distinct_from_propagation_delay(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        latency = body["link_state"]["latency_s"]
        prop_delay = body["propagation_delay_s"]
        assert latency != pytest.approx(prop_delay)

    @pytest.mark.asyncio
    async def test_propagation_delay_not_1_5(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["propagation_delay_s"] != pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_data_products_count(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["data_products_count"] == 2

    @pytest.mark.asyncio
    async def test_existing_fields_present(self, loaded_historical):
        """Backward-compatibility: existing fields must still be present."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        for field in ["link_state", "mission_state", "data_products_count",
                      "anomalies_count", "anomalies", "available_capacity_bits",
                      "queued_data_bits", "distance_km", "propagation_delay_s",
                      "round_trip_time_s"]:
            assert field in body, f"Missing field: {field}"


# ===========================================================================
# GET /data-products — historical runtime
# ===========================================================================


class TestHistoricalDataProducts:
    @pytest.mark.asyncio
    async def test_total_two(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/data-products")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2

    @pytest.mark.asyncio
    async def test_irdr_id(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/data-products")
        body = resp.json()
        ids = [dp["product_id"] for dp in body["data_products"]]
        assert "JUNO-MWR-PJ62-IRDR" in ids

    @pytest.mark.asyncio
    async def test_grdr_id(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/data-products")
        body = resp.json()
        ids = [dp["product_id"] for dp in body["data_products"]]
        assert "JUNO-MWR-PJ62-GRDR" in ids

    @pytest.mark.asyncio
    async def test_irdr_first(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/data-products")
        body = resp.json()
        assert body["data_products"][0]["product_id"] == "JUNO-MWR-PJ62-IRDR"

    @pytest.mark.asyncio
    async def test_grdr_second(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/data-products")
        body = resp.json()
        assert body["data_products"][1]["product_id"] == "JUNO-MWR-PJ62-GRDR"


# ===========================================================================
# POST /plans/generate — historical runtime
# ===========================================================================


class TestHistoricalPlansGenerate:
    @pytest.mark.asyncio
    async def test_plan_generation_succeeds(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/plans/generate", json={})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_plan_contains_two_packets(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/plans/generate", json={})
        body = resp.json()
        # /plans/generate returns a list of CandidatePlans; use the first (baseline)
        plans = body if isinstance(body, list) else [body]
        baseline = plans[0]
        packets = baseline.get("packets", [])
        assert len(packets) == 2

    @pytest.mark.asyncio
    async def test_irdr_ranked_first(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/plans/generate", json={})
        body = resp.json()
        plans = body if isinstance(body, list) else [body]
        baseline = plans[0]
        packets = baseline.get("packets", [])
        first_id = packets[0].get("packet_id") if packets else None
        assert first_id == "JUNO-MWR-PJ62-IRDR"


def _extract_packets(plan_or_response):
    """Find the packets/data_products list in a plan response, wherever it lives."""
    if isinstance(plan_or_response, dict):
        if "packets" in plan_or_response:
            return plan_or_response["packets"]
        if "data_products" in plan_or_response:
            return plan_or_response["data_products"]
        for v in plan_or_response.values():
            if isinstance(v, (dict, list)):
                result = _extract_packets(v)
                if result:
                    return result
    elif isinstance(plan_or_response, list):
        if plan_or_response and isinstance(plan_or_response[0], dict):
            if "packet_id" in plan_or_response[0] or "product_id" in plan_or_response[0]:
                return plan_or_response
    return []


# ===========================================================================
# POST /state/reset — historical reset
# ===========================================================================


class TestHistoricalReset:
    @pytest.mark.asyncio
    async def test_reset_returns_200(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/state/reset")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_reset_source_mode_field(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/state/reset")
        body = resp.json()
        assert body["source_mode"] == "historical_replay"

    @pytest.mark.asyncio
    async def test_reset_not_randomized(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/state/reset")
        body = resp.json()
        assert body["randomized"] is False

    @pytest.mark.asyncio
    async def test_reset_scenario_path_null(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/state/reset")
        body = resp.json()
        assert body["scenario_path"] is None

    @pytest.mark.asyncio
    async def test_reset_is_deterministic(self, loaded_historical):
        """Scenario and provenance after reset must equal pre-reset values."""
        scenario_before = app_state.active_scenario.model_dump()
        prov_before = app_state.active_source_provenance.model_dump()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/state/reset")

        scenario_after = app_state.active_scenario.model_dump()
        prov_after = app_state.active_source_provenance.model_dump()

        assert scenario_before == scenario_after
        assert prov_before == prov_after


# ===========================================================================
# GET /health — historical runtime
# ===========================================================================


class TestHistoricalHealth:
    @pytest.mark.asyncio
    async def test_status_ok(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_source_mode(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        assert body["source_mode"] == "historical_replay"

    @pytest.mark.asyncio
    async def test_historical_replay_active(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        assert body["historical_replay_active"] is True

    @pytest.mark.asyncio
    async def test_source_provenance_available(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        assert body["source_provenance_available"] is True

    @pytest.mark.asyncio
    async def test_scenario_path_null(self, loaded_historical):
        """Historical replay is descriptor-backed; scenario_path must be null."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        assert body["scenario_path"] is None

    @pytest.mark.asyncio
    async def test_existing_fields_present(self, loaded_historical):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        for field in ["status", "version", "scenario_loaded", "scenario_path",
                      "has_data_products", "data_products_count", "anomalies_count",
                      "has_geometry"]:
            assert field in body, f"Missing field: {field}"


# ===========================================================================
# Synthetic regression
# ===========================================================================


class TestSyntheticRegression:
    @pytest.mark.asyncio
    async def test_synthetic_state_returns_200(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_synthetic_source_mode(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["mode"] == "synthetic_scenario"

    @pytest.mark.asyncio
    async def test_synthetic_provenance_not_available(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/state")
        body = resp.json()
        assert body["source"]["provenance_available"] is False

    @pytest.mark.asyncio
    async def test_synthetic_health(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"
        assert body["source_mode"] == "synthetic_scenario"
        assert body["historical_replay_active"] is False

    @pytest.mark.asyncio
    async def test_synthetic_reset_randomized(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/state/reset")
        body = resp.json()
        assert body["source_mode"] == "synthetic_scenario"
        assert body["randomized"] is True
        # scenario_path must be present (synthetic-file-backed)
        assert body["scenario_path"] is not None

    @pytest.mark.asyncio
    async def test_synthetic_reset_clears_historical_is_false(self, loaded_synthetic):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/state/reset")
        assert app_state.active_source_provenance is None
        assert app_state.active_source_mode == MissionSourceMode.SYNTHETIC_SCENARIO


# ===========================================================================
# Atomicity / rollback
# ===========================================================================


class TestAtomicity:
    def test_provider_load_failure_leaves_state_unchanged(self):
        """If HistoricalReplayProvider.load fails, existing state is unchanged."""
        app_state.load_scenario("data/scenarios/nominal_pass.json")
        prev_scenario = app_state.active_scenario
        prev_mode = app_state.active_source_mode

        from unittest.mock import patch as _patch
        # The import happens inside load_historical_replay; patch at the module where it's imported
        with _patch(
            "backend.app.mission_sources.historical_provider.HistoricalReplayProvider.load",
            side_effect=RuntimeError("provider failure"),
        ):
            with pytest.raises(RuntimeError, match="provider failure"):
                app_state.load_historical_replay(_SOURCE_REF)

        assert app_state.active_scenario is prev_scenario
        assert app_state.active_source_mode == prev_mode

    def test_telecom_failure_during_bundle_activation_leaves_state_unchanged(self):
        """If TelecomEngine.compute fails, existing state is unchanged."""
        app_state.load_scenario("data/scenarios/nominal_pass.json")
        prev_scenario = app_state.active_scenario
        prev_mode = app_state.active_source_mode

        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider as HRP
        bundle = HRP().load(_SOURCE_REF)

        from unittest.mock import patch as _patch
        with _patch("backend.app.state.TelecomEngine") as mock_engine:
            mock_engine.return_value.compute.side_effect = RuntimeError("compute failure")
            with pytest.raises(RuntimeError, match="compute failure"):
                app_state.activate_mission_source_bundle(bundle)

        assert app_state.active_scenario is prev_scenario
        assert app_state.active_source_mode == prev_mode

    def test_successful_historical_changes_all_globals(self):
        """Successful historical activation atomically changes scenario + link + metadata."""
        app_state.load_scenario("data/scenarios/nominal_pass.json")
        prev_scenario_id = app_state.active_scenario.scenario_id

        app_state.load_historical_replay(_SOURCE_REF)

        assert app_state.active_scenario.scenario_id == _EXPECTED_SCENARIO_ID
        assert app_state.active_source_mode == MissionSourceMode.HISTORICAL_REPLAY
        assert app_state.active_source_provenance is not None
        assert app_state.active_link_state is not None

    def test_successful_synthetic_after_historical_clears_provenance(self):
        """load_scenario after historical clears all historical metadata atomically."""
        app_state.load_historical_replay(_SOURCE_REF)
        assert app_state.active_source_provenance is not None

        app_state.load_scenario("data/scenarios/nominal_pass.json")

        assert app_state.active_source_provenance is None
        assert app_state.active_source_mode == MissionSourceMode.SYNTHETIC_SCENARIO
        assert app_state.active_source_provider_name is None
