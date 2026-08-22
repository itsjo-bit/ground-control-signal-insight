"""GCSI Backend API — routes for /queue."""

from fastapi import APIRouter, HTTPException

from .. import state
from ..config import SchedulerWeights
from ..models.candidate_plan import CandidatePlan
from ..scheduler.baseline import BaselineScheduler

router = APIRouter()


@router.get("/queue", response_model=CandidatePlan)
def get_queue() -> CandidatePlan:
    """Return the baseline-ranked transmission queue.

    Calls BaselineScheduler.rank() on the active scenario.
    Raises 503 if no scenario has been loaded yet.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    weights = SchedulerWeights()
    return BaselineScheduler.rank(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )
