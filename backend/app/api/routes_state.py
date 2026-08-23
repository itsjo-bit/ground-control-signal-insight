"""GCSI Backend API — routes for /state."""

from fastapi import APIRouter, HTTPException

from .. import state

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


@router.post("/state/reset")
def reset_state() -> dict:
    """Reload the active scenario from disk, discarding all post-simulation mutations.

    Re-calls ``state.load_scenario()`` with the path that was used when the
    scenario was first loaded.  This restores ``active_scenario`` and
    ``active_link_state`` to their original, unconsumed values — exactly as
    if the server had just started with ``GCSI_SCENARIO_PATH`` set.

    Use this after running a simulation or approving a plan to get a clean
    slate without restarting the server process.

    Raises 503 if no scenario has ever been loaded (nothing to reset to).
    Raises 500 if the scenario file can no longer be read from disk.
    """
    if state.active_scenario_path is None:
        raise HTTPException(
            status_code=503,
            detail="No scenario has been loaded yet — nothing to reset to.",
        )
    try:
        state.load_scenario(state.active_scenario_path)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload scenario from '{state.active_scenario_path}': {exc}",
        ) from exc
    return {
        "status": "reset",
        "scenario_path": state.active_scenario_path,
        "comm_window_remaining_s": state.active_scenario.mission_state.comm_window_remaining_s,  # type: ignore[union-attr]
    }
