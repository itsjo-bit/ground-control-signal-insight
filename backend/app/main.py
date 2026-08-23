"""GCSI Backend FastAPI application.

Entrypoint for the backend server.  All route handlers contain no business
logic — they delegate to domain modules (scheduler, evaluator, simulator,
state).

Run locally:
    cd backend
    uvicorn app.main:app --reload --port 8000
"""

import os
import warnings
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env before any module reads os.getenv().  This is a no-op when the
# variables are already present in the environment (e.g. in CI or when the
# caller exports them in the shell), so it is safe to call unconditionally.
load_dotenv()

from .api.routes_agent import router as agent_router
from .api.routes_approve import router as approve_router
from .api.routes_plans import router as plans_router
from .api.routes_queue import router as queue_router
from .api.routes_simulate import router as simulate_router
from .api.routes_state import router as state_router
from . import state as app_state


@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    """Auto-load scenario on startup if GCSI_SCENARIO_PATH is set."""
    scenario_path = os.getenv("GCSI_SCENARIO_PATH")
    if scenario_path:
        try:
            app_state.load_scenario(scenario_path)
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"GCSI: Failed to auto-load scenario from '{scenario_path}': {exc}")
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


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {
        "status": "ok",
        "scenario_loaded": app_state.active_scenario is not None,
    }


