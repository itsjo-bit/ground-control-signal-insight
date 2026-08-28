"""GCSI Backend FastAPI application.

Entrypoint for the backend server.  All route handlers contain no business
logic — they delegate to domain modules (scheduler, evaluator, simulator,
state).

Run locally (from backend/ directory):
    uvicorn app.main:app --reload --port 8000

Run locally (from project root):
    uvicorn backend.app.main:app --reload --port 8000

The default scenario is ASTERIA-7 (asteria7_thermal_priority_contact_v1.json),
resolved as an absolute path relative to this file's location so the startup
path is always correct regardless of the current working directory.
Set GCSI_SCENARIO_PATH to override with a different scenario.

Phase 6E-C6 adds startup source selection via GCSI_SOURCE_MODE:
  synthetic_scenario (default) — existing GCSI_SCENARIO_PATH / ASTERIA-7 behaviour.
  historical_replay — requires GCSI_REPLAY_DESCRIPTOR.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Load .env before any module reads os.getenv().  This is a no-op when the
# variables are already present in the environment (e.g. in CI or when the
# caller exports them in the shell), so it is safe to call unconditionally.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes_agent import router as agent_router
from .api.routes_approve import router as approve_router
from .api.routes_data_products import router as data_products_router
from .api.routes_experience import router as experience_router
from .api.routes_plans import router as plans_router
from .api.routes_queue import router as queue_router
from .api.routes_simulate import router as simulate_router
from .api.routes_state import router as state_router
from . import state as app_state

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project-relative path resolution
#
# __file__ is  <project>/backend/app/main.py
# _BACKEND_DIR is  <project>/backend/app/../  →  <project>/backend/
# _PROJECT_ROOT is  <project>/backend/../     →  <project>/
# _SCENARIOS_DIR is  <project>/data/scenarios/
#
# This ensures the default scenario and the scenarios directory are always
# found correctly regardless of whether uvicorn is started from the project
# root or from the backend/ sub-directory.
# ---------------------------------------------------------------------------
_BACKEND_APP_DIR: Path = Path(__file__).resolve().parent   # .../backend/app/
_PROJECT_ROOT: Path = _BACKEND_APP_DIR.parent.parent       # .../ground-control-signal-insight/
_SCENARIOS_DIR: Path = _PROJECT_ROOT / "data" / "scenarios"

# Default to the ASTERIA-7 canonical mission scenario when no explicit path is configured.
# Resolved as an absolute path so it is independent of cwd.
_DEFAULT_SCENARIO_PATH: str = str(_SCENARIOS_DIR / "asteria7_thermal_priority_contact_v1.json")

# ---------------------------------------------------------------------------
# Supported GCSI_SOURCE_MODE values (Phase 6E-C6)
# ---------------------------------------------------------------------------
_SOURCE_MODE_SYNTHETIC = "synthetic_scenario"
_SOURCE_MODE_HISTORICAL = "historical_replay"
_VALID_SOURCE_MODES = {_SOURCE_MODE_SYNTHETIC, _SOURCE_MODE_HISTORICAL}


def _log_active_scenario() -> None:
    """Emit a clear startup banner showing the active scenario capabilities.

    Phase 6E-C6: source-aware — emits a distinct banner for historical replay.
    """
    from .mission_sources.models import MissionSourceMode

    scenario = app_state.active_scenario
    if scenario is None:
        logger.warning("[GCSI] No scenario loaded — all API endpoints will return 503.")
        return

    source_mode = app_state.active_source_mode

    # ── Historical replay banner ───────────────────────────────────────────
    if source_mode == MissionSourceMode.HISTORICAL_REPLAY:
        prov = app_state.active_source_provenance
        prov_count = len(prov.records) if prov else 0
        ms = scenario.mission_state
        dp_count = len(scenario.data_products)
        has_geometry = scenario.distance_km is not None
        print(  # noqa: T201
            f"\n"
            f"  [GCSI] ═══════════════════════════════════════════════════\n"
            f"  [GCSI] Source mode      : HISTORICAL REPLAY\n"
            f"  [GCSI] Provider         : {app_state.active_source_provider_name}\n"
            f"  [GCSI] Replay source    : {app_state.active_source_ref}\n"
            f"  [GCSI] Mission          : {ms.mission_id}\n"
            f"  [GCSI] Data products    : {dp_count}\n"
            f"  [GCSI] Geometry         : {'available' if has_geometry else 'unavailable'}\n"
            f"  [GCSI] Provenance       : {prov_count} source-lineage records\n"
            f"  [GCSI] Replay semantics : reconstructed historical scenario\n"
            f"  [GCSI]                    NOT live spacecraft telemetry\n"
            f"  [GCSI] Data origin      : NASA/JPL/PDS facts + explicit GCSI\n"
            f"  [GCSI]                    modeled communications policy\n"
            f"  [GCSI] ═══════════════════════════════════════════════════\n",
            file=sys.stderr,
        )
        return

    # ── Synthetic scenario banners ─────────────────────────────────────────
    path = app_state.active_scenario_path or "(unknown)"
    ms = scenario.mission_state
    dp_count = len(scenario.data_products)
    anom_count = len(scenario.anomalies)
    has_geometry = scenario.distance_km is not None

    if dp_count > 0:
        if scenario.scenario_id == "asteria7_thermal_priority_contact_v1":
            # ── ASTERIA-7 canonical mission banner ────────────────────────────
            from .telecom.geometry import compute_propagation_delay
            one_way_s = round(compute_propagation_delay(scenario.distance_km), 3) if has_geometry else None
            print(  # noqa: T201
                f"\n"
                f"  [GCSI] Active scenario : {path}\n"
                f"  [GCSI] Mission         : {ms.mission_id}\n"
                f"  [GCSI] ASTERIA-7       : THERMAL PRIORITY CONTACT\n"
                f"  [GCSI] {dp_count:,} data products\n"
                f"  [GCSI] Thermal anomaly : ACTIVE\n"
                f"  [GCSI] Geometry        : {int(scenario.distance_km):,} km\n"
                f"  [GCSI] One-way signal  : {one_way_s:.3f} s\n"
                f"  [GCSI] Canonical mission experience : READY\n",
                file=sys.stderr,
            )
        else:
            # ── High-volume V3+ scenario ──────────────────────────────────────
            print(  # noqa: T201
                f"\n"
                f"  [GCSI] Active scenario : {path}\n"
                f"  [GCSI] Mission         : {ms.mission_id}\n"
                f"  [GCSI] Event           : {ms.current_event}\n"
                f"  [GCSI] Data products   : {dp_count}\n"
                f"  [GCSI] Active anomalies: {anom_count}\n"
                f"  [GCSI] Geometry        : {'available (' + str(int(scenario.distance_km)) + ' km)' if has_geometry else 'unavailable'}\n"
                f"  [GCSI] Mode            : High-volume data products (V3+ full experience)\n",
                file=sys.stderr,
            )
    else:
        # ── Legacy packet scenario ────────────────────────────────────────────
        pkt_count = len(scenario.packets)
        print(  # noqa: T201
            f"\n"
            f"  [GCSI] WARNING: Legacy packet scenario active\n"
            f"  [GCSI] Path            : {path}\n"
            f"  [GCSI] Mission         : {ms.mission_id}\n"
            f"  [GCSI] Packets         : {pkt_count}\n"
            f"  [GCSI] High-volume AI prioritization : UNAVAILABLE\n"
            f"  [GCSI] To use the canonical ASTERIA-7 demo experience:\n"
            f"  [GCSI]   Remove GCSI_SCENARIO_PATH from .env (uses the default)\n"
            f"  [GCSI]   or explicitly set:\n"
            f"  [GCSI]   GCSI_SCENARIO_PATH=data/scenarios/asteria7_thermal_priority_contact_v1.json\n"
            f"  [GCSI] Alternative lightweight scenario (150 products):\n"
            f"  [GCSI]   GCSI_SCENARIO_PATH=data/scenarios/mission_data_v3.json\n",
            file=sys.stderr,
        )


def _load_configured_mission_source() -> None:
    """Read environment variables and invoke the appropriate state loading path.

    This is a small testable helper that implements the startup source-selection
    contract (Phase 6E-C6 Part A).

    Reads:
        GCSI_SOURCE_MODE         — "synthetic_scenario" | "historical_replay" | absent/blank
        GCSI_REPLAY_DESCRIPTOR   — required when GCSI_SOURCE_MODE=historical_replay
        GCSI_SCENARIO_PATH       — synthetic mode only

    Rules:
        - Absent / blank GCSI_SOURCE_MODE → synthetic_scenario (backward compat).
        - historical_replay without GCSI_REPLAY_DESCRIPTOR → raises RuntimeError.
        - Invalid GCSI_SOURCE_MODE → raises ValueError (no fallback).
        - GCSI_REPLAY_DESCRIPTOR without historical mode → ignored.
        - historical mode + GCSI_SCENARIO_PATH → historical wins; GCSI_SCENARIO_PATH not loaded.

    Raises:
        ValueError:   Invalid GCSI_SOURCE_MODE value.
        RuntimeError: historical_replay mode with missing GCSI_REPLAY_DESCRIPTOR.
        Any exception from state.load_scenario or state.load_historical_replay.
    """
    raw_mode = os.getenv("GCSI_SOURCE_MODE", "").strip()
    source_mode = raw_mode if raw_mode else _SOURCE_MODE_SYNTHETIC

    if source_mode not in _VALID_SOURCE_MODES:
        raise ValueError(
            f"Invalid GCSI_SOURCE_MODE value: {source_mode!r}. "
            f"Allowed values: {sorted(_VALID_SOURCE_MODES)}. "
            f"No fallback — fix the configuration and restart."
        )

    if source_mode == _SOURCE_MODE_HISTORICAL:
        replay_descriptor = os.getenv("GCSI_REPLAY_DESCRIPTOR", "").strip()
        if not replay_descriptor:
            raise RuntimeError(
                "GCSI_SOURCE_MODE=historical_replay requires GCSI_REPLAY_DESCRIPTOR to be set. "
                "No fallback to synthetic — fix the configuration and restart."
            )
        print(  # noqa: T201
            f"  [GCSI] Source mode       : historical_replay\n"
            f"  [GCSI] Replay descriptor : {replay_descriptor}",
            file=sys.stderr,
        )
        scenario_path_env = os.getenv("GCSI_SCENARIO_PATH", "").strip()
        if scenario_path_env:
            print(  # noqa: T201
                f"  [GCSI] GCSI_SCENARIO_PATH ignored — historical_replay mode active.",
                file=sys.stderr,
            )
        app_state.load_historical_replay(replay_descriptor)
    else:
        # synthetic_scenario (default)
        env_path = os.getenv("GCSI_SCENARIO_PATH")
        scenario_path = env_path if env_path else _DEFAULT_SCENARIO_PATH

        if env_path:
            print(  # noqa: T201
                f"  [GCSI] GCSI_SCENARIO_PATH override: {env_path}",
                file=sys.stderr,
            )
        else:
            print(  # noqa: T201
                f"  [GCSI] No GCSI_SCENARIO_PATH set — using default: {_DEFAULT_SCENARIO_PATH}",
                file=sys.stderr,
            )
        app_state.load_scenario(scenario_path)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    """Auto-load scenario on startup.

    Phase 6E-C6: delegates source selection to _load_configured_mission_source().
    """
    try:
        _load_configured_mission_source()
        _log_active_scenario()
    except Exception as exc:  # noqa: BLE001
        print(  # noqa: T201
            f"\n"
            f"  [GCSI] SOURCE LOAD ERROR\n"
            f"  [GCSI] Reason    : {exc}\n"
            f"  [GCSI] Application cannot initialize the requested source.\n"
            f"  [GCSI] All API endpoints will return 503 until a valid source is loaded.\n",
            file=sys.stderr,
        )
        logger.error("[GCSI] Failed to load mission source: %s", exc)
    yield


app = FastAPI(
    title="GCSI — Ground Control Signal Insight",
    description="AI-powered communication decision-support for spacecraft ground operations.",
    version="1.0.0",
    lifespan=_lifespan,
)

# Allow Vite dev server and production build to call the API.
_allowed_origins = [
    "http://localhost:5173",
    "http://localhost:4173",  # vite preview
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(state_router)
app.include_router(queue_router)
app.include_router(plans_router)
app.include_router(simulate_router)
app.include_router(approve_router)
app.include_router(agent_router)
app.include_router(data_products_router)
app.include_router(experience_router)


# ---------------------------------------------------------------------------
# Health / info endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness probe.

    Phase 6E-C6: adds additive source-mode fields.
    Existing fields are preserved unchanged.
    For historical replay, scenario_path is None (descriptor-backed).
    """
    from .mission_sources.models import MissionSourceMode

    scenario = app_state.active_scenario
    source_mode = app_state.active_source_mode
    is_historical = source_mode == MissionSourceMode.HISTORICAL_REPLAY

    return {
        "status": "ok",
        "version": "1.0.0",
        "scenario_loaded": scenario is not None,
        "scenario_path": app_state.active_scenario_path,
        "has_data_products": len(scenario.data_products) > 0 if scenario else False,
        "data_products_count": len(scenario.data_products) if scenario else 0,
        "anomalies_count": len(scenario.anomalies) if scenario else 0,
        "has_geometry": scenario.distance_km is not None if scenario else False,
        # Phase 6E-C6 additive fields
        "source_mode": source_mode.value if source_mode else None,
        "source_provider_name": app_state.active_source_provider_name,
        "historical_replay_active": is_historical,
        "source_provenance_available": app_state.active_source_provenance is not None,
    }
