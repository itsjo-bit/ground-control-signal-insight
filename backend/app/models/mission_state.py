from pydantic import BaseModel, Field
from .risk_level import RiskLevel


class MissionState(BaseModel):
    """Snapshot of mission context at a point in time."""

    mission_id: str
    mission_phase: str = Field(description="Current mission phase identifier")
    current_event: str = Field(description="Human-readable description of the current mission event")
    event_time_remaining_s: float = Field(ge=0.0, description="Time remaining until current event ends, in seconds")
    comm_window_remaining_s: float = Field(ge=0.0, description="Remaining communication window in seconds")
    risk_score: float = Field(ge=0.0, le=1.0, description="Mission risk scalar [0, 1]")
    risk_level: RiskLevel = Field(description="Categorical risk classification derived from risk_score")
