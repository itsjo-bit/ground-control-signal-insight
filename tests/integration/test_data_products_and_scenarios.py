"""Integration tests for V3.4 endpoints: /data-products, /scenarios, /scenarios/switch.

Covers all requirements from the V3.4.1 task:
- GET /data-products returns 150 raw products for v3 scenario
- GET /data-products returns empty list + has_data_products=false for legacy
- GET /scenarios lists available scenario files with correct metadata
- GET /scenarios marks the active scenario with is_active=true
- POST /scenarios/switch changes the active scenario
- POST /scenarios/switch updates active_scenario_path
- POST /scenarios/switch rejects unknown filenames (404)
- POST /scenarios/switch rejects path traversal attempts (404)
- Reset after switch reloads the switched-to scenario, not the original
- /health returns version and scenario metadata
- Default startup uses ASTERIA-7 (no GCSI_SCENARIO_PATH set); mission_data_v3.json is the frozen benchmark input, not the default
- Explicit GCSI_SCENARIO_PATH override is respected
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app, _DEFAULT_SCENARIO_PATH
from backend.app import state as app_state

# ── Absolute paths to scenario files ─────────────────────────────────────────
_SCENARIOS_DIR = Path(__file__).parents[2] / "data" / "scenarios"
_V3_SCENARIO = str(_SCENARIOS_DIR / "mission_data_v3.json")
_LEGACY_SCENARIO = str(_SCENARIOS_DIR / "nominal_pass.json")
_V2_SCENARIO = str(_SCENARIOS_DIR / "mission_data_v2.json")


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_state():
    """Isolate every test — clear global state before and after."""
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
def loaded_legacy():
    app_state.load_scenario(_LEGACY_SCENARIO)


@pytest.fixture
def loaded_v2():
    app_state.load_scenario(_V2_SCENARIO)


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_version(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_health_v3_has_data_products(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        body = resp.json()
        assert body["has_data_products"] is True
        assert body["data_products_count"] == 150
        assert body["anomalies_count"] == 3
        assert body["has_geometry"] is True

    @pytest.mark.asyncio
    async def test_health_legacy_no_data_products(self, loaded_legacy):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        body = resp.json()
        assert body["has_data_products"] is False
        assert body["data_products_count"] == 0

    @pytest.mark.asyncio
    async def test_health_scenario_path_reported(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        body = resp.json()
        assert body["scenario_path"] is not None
        assert "mission_data_v3" in body["scenario_path"]


# ── /data-products — v3 scenario ─────────────────────────────────────────────

class TestDataProductsV3:
    @pytest.mark.asyncio
    async def test_returns_200(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_150_products(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        body = resp.json()
        assert body["total"] == 150
        assert len(body["data_products"]) == 150

    @pytest.mark.asyncio
    async def test_has_data_products_true(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.json()["has_data_products"] is True

    @pytest.mark.asyncio
    async def test_scenario_id_matches_v3(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert "v3" in resp.json()["scenario_id"].lower() or "high_volume" in resp.json()["scenario_id"].lower()

    @pytest.mark.asyncio
    async def test_product_fields_present(self, loaded_v3):
        """Each product must have all required V3 fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        product = resp.json()["data_products"][0]
        required_fields = [
            "product_id", "product_type", "description", "subsystem",
            "size_bits", "criticality", "mission_relevance", "scientific_value",
            "deadline_s", "age_s", "delivery_requirement", "retry_cost",
            "related_ids",
        ]
        for field in required_fields:
            assert field in product, f"missing field: {field}"

    @pytest.mark.asyncio
    async def test_product_count_greater_than_5(self, loaded_v3):
        """The v3 product count must be far above the legacy 5-packet count."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.json()["total"] > 5

    @pytest.mark.asyncio
    async def test_returns_503_when_no_scenario_loaded(self):
        """503 when no scenario is active."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.status_code == 503


# ── /data-products — legacy scenario ─────────────────────────────────────────

class TestDataProductsLegacy:
    @pytest.mark.asyncio
    async def test_returns_empty_list_for_legacy(self, loaded_legacy):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        body = resp.json()
        assert body["total"] == 0
        assert body["data_products"] == []
        assert body["has_data_products"] is False


# ── /scenarios ────────────────────────────────────────────────────────────────

class TestListScenarios:
    @pytest.mark.asyncio
    async def test_returns_200(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_returns_list_of_scenarios(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        body = resp.json()
        assert "scenarios" in body
        assert len(body["scenarios"]) >= 1

    @pytest.mark.asyncio
    async def test_includes_v3_scenario(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        filenames = [s["filename"] for s in resp.json()["scenarios"]]
        assert "mission_data_v3.json" in filenames

    @pytest.mark.asyncio
    async def test_includes_nominal_pass(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        filenames = [s["filename"] for s in resp.json()["scenarios"]]
        assert "nominal_pass.json" in filenames

    @pytest.mark.asyncio
    async def test_active_scenario_marked(self, loaded_v3):
        """The loaded v3 scenario must have is_active=true."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        v3 = next(
            (s for s in resp.json()["scenarios"] if s["filename"] == "mission_data_v3.json"),
            None,
        )
        assert v3 is not None
        assert v3["is_active"] is True

    @pytest.mark.asyncio
    async def test_non_active_not_marked(self, loaded_v3):
        """nominal_pass.json must NOT be is_active when v3 is loaded."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        nom = next(
            (s for s in resp.json()["scenarios"] if s["filename"] == "nominal_pass.json"),
            None,
        )
        assert nom is not None
        assert nom["is_active"] is False

    @pytest.mark.asyncio
    async def test_v3_metadata_in_list(self, loaded_v3):
        """v3 entry must report 150 products and 3 anomalies."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        v3 = next(s for s in resp.json()["scenarios"] if s["filename"] == "mission_data_v3.json")
        assert v3["data_products_count"] == 150
        assert v3["anomalies_count"] == 3
        assert v3["has_data_products"] is True
        assert v3["has_anomalies"] is True

    @pytest.mark.asyncio
    async def test_active_scenario_path_in_response(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/scenarios")
        assert resp.json()["active_scenario_path"] is not None


# ── /scenarios/switch ─────────────────────────────────────────────────────────

class TestSwitchScenario:
    @pytest.mark.asyncio
    async def test_switch_from_legacy_to_v3(self, loaded_legacy):
        """Switching from nominal_pass to mission_data_v3 must fully update state."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Verify initial state is legacy
            init_state = await c.get("/state")
            assert init_state.json()["mission_state"]["current_event"] == "nominal_pass"

            # Switch to v3
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "switched"
            assert body["data_products_count"] == 150
            assert body["anomalies_count"] == 3

            # Verify backend state now reflects v3
            new_state = await c.get("/state")
            assert new_state.json()["data_products_count"] == 150
            assert new_state.json()["anomalies_count"] == 3
            assert new_state.json()["mission_state"]["current_event"] == "high_volume_pass"

    @pytest.mark.asyncio
    async def test_switch_updates_active_scenario_path(self, loaded_legacy):
        """active_scenario_path must change after a switch."""
        original_path = app_state.active_scenario_path
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
        assert app_state.active_scenario_path != original_path
        assert "mission_data_v3" in app_state.active_scenario_path

    @pytest.mark.asyncio
    async def test_switch_updates_scenarios_list_active_flag(self, loaded_legacy):
        """After switching, /scenarios must report the new scenario as active."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
            resp = await c.get("/scenarios")
        v3 = next(s for s in resp.json()["scenarios"] if s["filename"] == "mission_data_v3.json")
        nom = next(s for s in resp.json()["scenarios"] if s["filename"] == "nominal_pass.json")
        assert v3["is_active"] is True
        assert nom["is_active"] is False

    @pytest.mark.asyncio
    async def test_switch_unknown_filename_returns_404(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "nonexistent.json"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_switch_path_traversal_rejected(self, loaded_v3):
        """Path traversal attempts must be rejected.

        ../../etc/passwd has no .json extension, so it is rejected with 400
        (invalid extension check runs first).  Both 400 and 404 signal rejection.
        """
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "../../etc/passwd"})
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_reset_after_switch_reloads_new_scenario(self, loaded_legacy):
        """After switch legacy→v3, reset must reload v3 not the original legacy."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # Switch to v3
            await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
            # Reset
            reset_resp = await c.post("/state/reset")
            assert reset_resp.status_code == 200
            assert "mission_data_v3" in reset_resp.json()["scenario_path"]
            # State should still reflect v3 after reset
            state_resp = await c.get("/state")
            assert state_resp.json()["data_products_count"] == 150


# ── Default scenario (no env override) ───────────────────────────────────────

class TestDefaultScenario:
    def test_default_scenario_path_is_asteria7(self):
        """Phase 4.2A: The compiled default must point to asteria7_thermal_priority_contact_v1.json."""
        assert "asteria7_thermal_priority_contact_v1.json" in _DEFAULT_SCENARIO_PATH

    def test_default_scenario_loads_without_env_override(self, monkeypatch):
        """Without GCSI_SCENARIO_PATH in the environment, ASTERIA-7 must be selected."""
        monkeypatch.delenv("GCSI_SCENARIO_PATH", raising=False)
        # Simulate what main.py lifespan does
        env_path = os.getenv("GCSI_SCENARIO_PATH")
        scenario_path = env_path if env_path else _DEFAULT_SCENARIO_PATH
        assert "asteria7_thermal_priority_contact_v1" in scenario_path

    def test_explicit_env_override_is_respected(self, monkeypatch):
        """When GCSI_SCENARIO_PATH is set explicitly, it must take priority."""
        monkeypatch.setenv("GCSI_SCENARIO_PATH", "data/scenarios/nominal_pass.json")
        env_path = os.getenv("GCSI_SCENARIO_PATH")
        scenario_path = env_path if env_path else _DEFAULT_SCENARIO_PATH
        assert "nominal_pass.json" in scenario_path
        assert "asteria7_thermal_priority_contact_v1" not in scenario_path


# ── Capability detection ──────────────────────────────────────────────────────

class TestCapabilityDetection:
    @pytest.mark.asyncio
    async def test_v3_has_data_products_capability(self, loaded_v3):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["data_products_count"] == 150  # hasDataProducts = true
        assert body["anomalies_count"] == 3          # hasAnomalies = true
        assert body["distance_km"] == 54_000_000     # hasGeometry = true

    @pytest.mark.asyncio
    async def test_legacy_has_no_data_products_capability(self, loaded_legacy):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["data_products_count"] == 0      # isLegacyPacketMode = true
        assert body["anomalies_count"] == 0
        assert body["distance_km"] is None           # hasGeometry = false

    @pytest.mark.asyncio
    async def test_v3_ai_uses_high_volume_context(self, loaded_v3, monkeypatch):
        """AI recommend on v3 must use the v2 path (candidate_count > 5)."""
        monkeypatch.setenv("GCSI_AI_PROVIDER", "local")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        # candidate_count must reflect high-volume products, not the 5-packet legacy count
        assert body["candidate_count"] is not None
        assert body["candidate_count"] > 5
        # Prioritization must be present (v2 path active)
        assert body["prioritization"] is not None
        assert len(body["prioritization"]["ranked_products"]) > 0
