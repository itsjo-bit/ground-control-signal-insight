"""GCSI Backend FastAPI application.

Entrypoint for the backend server.  All route handlers contain no business
logic — they delegate to domain modules (scheduler, evaluator, simulator,
state).

Run locally:
    cd backend
    uvicorn app.main:app --reload --port 8000
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

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
from .api.routes_plans import router as plans_router
from .api.routes_queue import router as queue_router
from .api.routes_simulate import router as simulate_router
from .api.routes_state import router as state_router
from . import state as app_state

logger = logging.getLogger(__name__)

# Default to the high-volume v3 demo scenario when no explicit path is configured.
# This ensures the interactive application starts with the full 150-product dataset.
# Tests that need a specific scenario should set GCSI_SCENARIO_PATH explicitly.
_DEFAULT_SCENARIO_PATH = "data/scenarios/mission_data_v3.json"


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
        # ── High-volume V3+ scenario ─────────────────────────────────────────
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
            f"  [GCSI] To use the full V3.4 demo experience, set:\n"
            f"  [GCSI]   GCSI_SCENARIO_PATH=data/scenarios/mission_data_v3.json\n"
            f"  [GCSI]   (or remove GCSI_SCENARIO_PATH from .env to use the default)\n",
            file=sys.stderr,
        )


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    """Auto-load scenario on startup.

    Priority:
    1. GCSI_SCENARIO_PATH env var (explicit override — used by tests and CI).
    2. _DEFAULT_SCENARIO_PATH (the high-volume v3 demo scenario).

    If GCSI_SCENARIO_PATH is absent or empty, the application always starts
    with the high-volume V3 demo scenario (mission_data_v3.json).
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
    version="0.1.0",
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


# ---------------------------------------------------------------------------
# Health / info endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    scenario = app_state.active_scenario
    return {
        "status": "ok",
        "version": "3.4.1",
        "scenario_loaded": scenario is not None,
        "scenario_path": app_state.active_scenario_path,
        "has_data_products": len(scenario.data_products) > 0 if scenario else False,
        "data_products_count": len(scenario.data_products) if scenario else 0,
        "anomalies_count": len(scenario.anomalies) if scenario else 0,
        "has_geometry": scenario.distance_km is not None if scenario else False,
    }


