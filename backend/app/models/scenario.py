from pydantic import BaseModel, Field
from .mission_state import MissionState
from .packet import Packet


class Scenario(BaseModel):
    """Typed container for a loaded mission scenario.

    simulated must be True for any scenario in this system.
    The scenario_loader enforces this at load time.
    """

    scenario_id: str
    simulated: bool = Field(description="Must be True; distinguishes simulated data from real telemetry")
    link_inputs: dict = Field(
        description="Raw inputs consumed by TelecomEngine.compute(); keys must match engine expectations"
    )
    mission_state: MissionState
    packets: list[Packet] = Field(default_factory=list, description="Packets awaiting transmission")
