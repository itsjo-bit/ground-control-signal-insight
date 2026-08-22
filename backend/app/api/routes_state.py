"""GCSI Backend API — routes for /state."""

from fastapi import APIRouter, HTTPException

from .. import state
from ..models.link_state import LinkState
from ..models.mission_state import MissionState

router = APIRouter()


class StateResponse:
    pass


@router.get("/state")
def get_state() -> dict:
    """Return current link_state and mission_state.

    Raises 503 if no scenario has been loaded yet.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")
    return {
        "link_state": state.active_link_state.model_dump(mode="json"),
        "mission_state": state.active_scenario.mission_state.model_dump(mode="json"),
    }
