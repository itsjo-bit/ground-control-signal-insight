"""Backend tests for GCSI 1.0.0 path robustness and scenario security.

Covers:
- Default scenario path is project-relative (not cwd-dependent)
- Explicit GCSI_SCENARIO_PATH override is respected
- GCSI_SCENARIOS_DIR override is respected
- /scenarios/switch: valid switch succeeds
- /scenarios/switch: path traversal is rejected (404)
- /scenarios/switch: non-.json extension is rejected (400)
- /scenarios/switch: non-existent filename is rejected (404)
- /scenarios/switch: invalid scenario JSON returns 422 and preserves state
- /health: version is 1.0.0
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app, _DEFAULT_SCENARIO_PATH, _SCENARIOS_DIR
from backend.app import state as app_state
from backend.app.api.routes_data_products import _SCENARIOS_DIR_PATH

# ── Absolute paths to scenario files ─────────────────────────────────────────
_SCENARIOS_DIR_ABS = Path(__file__).parents[2] / "data" / "scenarios"
_V3_SCENARIO = str(_SCENARIOS_DIR_ABS / "mission_data_v3.json")
_LEGACY_SCENARIO = str(_SCENARIOS_DIR_ABS / "nominal_pass.json")


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


# ── Path resolution ───────────────────────────────────────────────────────────

class TestPathResolution:
    def test_default_scenario_path_is_absolute(self):
        """The compiled default path must be absolute (not cwd-relative)."""
        assert Path(_DEFAULT_SCENARIO_PATH).is_absolute(), (
            f"_DEFAULT_SCENARIO_PATH must be absolute, got: {_DEFAULT_SCENARIO_PATH}"
        )

    def test_default_scenario_path_exists(self):
        """The default path (ASTERIA-7) must exist on disk."""
        assert Path(_DEFAULT_SCENARIO_PATH).exists(), (
            f"asteria7_thermal_priority_contact_v1.json not found at: {_DEFAULT_SCENARIO_PATH}"
        )

    def test_default_scenario_path_is_asteria7(self):
        """Phase 4.2A: The default must point to asteria7_thermal_priority_contact_v1.json."""
        assert "asteria7_thermal_priority_contact_v1.json" in _DEFAULT_SCENARIO_PATH

    def test_scenarios_dir_in_main_is_absolute(self):
        """_SCENARIOS_DIR exposed from main must be absolute."""
        assert Path(str(_SCENARIOS_DIR)).is_absolute(), (
            f"main._SCENARIOS_DIR must be absolute, got: {_SCENARIOS_DIR}"
        )

    def test_scenarios_dir_path_in_routes_is_absolute(self):
        """_SCENARIOS_DIR_PATH exposed from routes_data_products must be absolute
        after resolution (even if the env var was relative)."""
        resolved = _SCENARIOS_DIR_PATH.resolve()
        assert resolved.is_absolute()

    def test_default_path_loads_without_env_override(self, monkeypatch):
        """Phase 4.2A: Without GCSI_SCENARIO_PATH in the environment, ASTERIA-7 path is selected."""
        monkeypatch.delenv("GCSI_SCENARIO_PATH", raising=False)
        env_path = os.getenv("GCSI_SCENARIO_PATH")
        chosen = env_path if env_path else _DEFAULT_SCENARIO_PATH
        assert "asteria7_thermal_priority_contact_v1" in chosen

    def test_explicit_env_override_is_respected(self, monkeypatch):
        """When GCSI_SCENARIO_PATH is set, it takes priority over the default."""
        monkeypatch.setenv("GCSI_SCENARIO_PATH", "data/scenarios/nominal_pass.json")
        env_path = os.getenv("GCSI_SCENARIO_PATH")
        chosen = env_path if env_path else _DEFAULT_SCENARIO_PATH
        assert "nominal_pass.json" in chosen
        assert "mission_data_v3.json" not in chosen

    def test_default_scenario_is_loadable_as_absolute(self):
        """Phase 4.2A: The absolute default path (ASTERIA-7) must load successfully."""
        # This verifies the path is correct independent of cwd.
        app_state.load_scenario(_DEFAULT_SCENARIO_PATH)
        assert app_state.active_scenario is not None
        assert len(app_state.active_scenario.data_products) == 1284


# ── Version ───────────────────────────────────────────────────────────────────

class TestVersion:
    @pytest.mark.asyncio
    async def test_health_version_is_1_0_0(self, loaded_v3):
        """Health endpoint must report version 1.0.0."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.0.0"


# ── /scenarios/switch — security ─────────────────────────────────────────────

class TestSwitchScenarioSecurity:
    @pytest.mark.asyncio
    async def test_non_json_extension_rejected(self, loaded_v3):
        """Filenames without .json must be rejected with 400."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json.bak"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_no_extension_rejected(self, loaded_v3):
        """Filenames without any extension must be rejected with 400."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_path_traversal_dotdot_rejected(self, loaded_v3):
        """Path traversal with ../ must be rejected.

        ../../etc/passwd.json contains a path separator, so the basename check
        returns 400 before any path resolution occurs.
        """
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "../../etc/passwd.json"})
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_path_traversal_single_dotdot_rejected(self, loaded_v3):
        """Single ../ traversal must be rejected.

        ../mission_data_v3.json contains a path separator, so the basename
        check returns 400 before any path resolution occurs.
        """
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "../mission_data_v3.json"})
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_absolute_path_rejected(self, loaded_v3):
        """An absolute path as filename must be rejected (404, not inside scenarios dir)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "/etc/passwd.json"})
        # Either 400 (no .json) or 404 (traversal), but never 200
        assert resp.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_404(self, loaded_v3):
        """A valid .json filename that does not exist must return 404."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "does_not_exist.json"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_state_preserved_after_failed_switch(self, loaded_v3):
        """If scenario switching fails (invalid JSON), current state must remain intact."""
        original_path = app_state.active_scenario_path
        original_dp_count = len(app_state.active_scenario.data_products)

        # Create a temporary invalid scenario JSON file inside the scenarios dir
        base = _SCENARIOS_DIR_PATH.resolve()
        tmp_path = base / "_test_invalid_scenario.json"
        try:
            tmp_path.write_text('{"this": "is not a valid scenario"}', encoding="utf-8")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/scenarios/switch", json={"filename": tmp_path.name})

            # Must return 422 (failed validation)
            assert resp.status_code == 422

            # State must be fully preserved
            assert app_state.active_scenario_path == original_path
            assert app_state.active_scenario is not None
            assert len(app_state.active_scenario.data_products) == original_dp_count
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    @pytest.mark.asyncio
    async def test_valid_switch_to_legacy_succeeds(self, loaded_v3):
        """A valid switch to a legacy scenario must succeed."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "nominal_pass.json"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "switched"
        assert body["data_products_count"] == 0  # legacy = no data products

    @pytest.mark.asyncio
    async def test_valid_switch_to_v3_succeeds(self, loaded_legacy):
        """A valid switch to the v3 scenario must succeed and report 150 products."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/scenarios/switch", json={"filename": "mission_data_v3.json"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "switched"
        assert body["data_products_count"] == 150


# ── /scenarios/switch — state rollback with bad JSON ─────────────────────────

class TestSwitchScenarioRollback:
    @pytest.mark.asyncio
    async def test_rollback_leaves_link_state_intact(self, loaded_v3):
        """After a failed switch, active_link_state must remain set."""
        original_link_state = app_state.active_link_state

        base = _SCENARIOS_DIR_PATH.resolve()
        tmp_path = base / "_test_rollback.json"
        try:
            # Valid JSON but not a valid Scenario (missing required fields)
            tmp_path.write_text('{"scenario_id": "bad", "simulated": true}', encoding="utf-8")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                await c.post("/scenarios/switch", json={"filename": tmp_path.name})

            # Link state must be the original object, not None
            assert app_state.active_link_state is original_link_state
        finally:
            if tmp_path.exists():
                tmp_path.unlink()
