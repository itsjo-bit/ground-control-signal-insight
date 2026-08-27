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


def _log_active_scenario() -> None:
    """Emit a clear startup banner showing the active scenario capabilities.

    This makes it immediately obvious which scenario is loaded and whether the
    full V3.4 high-volume workflow is available — eliminating silent fallbacks.
    """
    scenario = app_state.active_scenario
    if scenario is None:
        logger.warning("[GCSI] No scenario loaded — all API endpoints will return 503.")
        return

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


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    """Auto-load scenario on startup.

    Priority:
    1. GCSI_SCENARIO_PATH env var (explicit override — used by tests and CI).
       Relative paths in GCSI_SCENARIO_PATH are left as-is (resolved against
       the process cwd, matching the documented behaviour in .env.example).
    2. _DEFAULT_SCENARIO_PATH — the project-relative absolute path to
       asteria7_thermal_priority_contact_v1.json (ASTERIA-7 canonical mission).
       This path is always correct regardless of which directory uvicorn was
       started from.

    A clear startup banner is printed so the active scenario is immediately
    visible in the terminal — no silent fallbacks.
    """
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

    try:
        app_state.load_scenario(scenario_path)
        _log_active_scenario()
    except Exception as exc:  # noqa: BLE001
        print(  # noqa: T201
            f"\n"
            f"  [GCSI] SCENARIO LOAD ERROR\n"
            f"  [GCSI] Requested : {scenario_path}\n"
            f"  [GCSI] Reason    : {exc}\n"
            f"  [GCSI] Application cannot initialize the requested scenario.\n"
            f"  [GCSI] All API endpoints will return 503 until a valid scenario is loaded.\n",
            file=sys.stderr,
        )
        logger.error("[GCSI] Failed to load scenario '%s': %s", scenario_path, exc)
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
    """Liveness probe."""
    scenario = app_state.active_scenario
    return {
        "status": "ok",
        "version": "1.0.0",
        "scenario_loaded": scenario is not None,
        "scenario_path": app_state.active_scenario_path,
        "has_data_products": len(scenario.data_products) > 0 if scenario else False,
        "data_products_count": len(scenario.data_products) if scenario else 0,
        "anomalies_count": len(scenario.anomalies) if scenario else 0,
        "has_geometry": scenario.distance_km is not None if scenario else False,
    }


