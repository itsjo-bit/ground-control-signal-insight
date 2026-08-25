from pydantic import BaseModel, Field
from .mission_state import MissionState
from .packet import Packet
from .data_product import DataProduct
from .anomaly_event import AnomalyEvent


class Scenario(BaseModel):
    """Typed container for a loaded mission scenario.

    simulated must be True for any scenario in this system.
    The scenario_loader enforces this at load time.

    Phase 2A extension: ``data_products`` and ``anomalies`` are new optional
    fields.  All existing scenarios that omit them will default to empty lists
    and continue to load and behave exactly as before.  The ``packets`` field
    is preserved unchanged for full backward compatibility with all existing
    packet-based code paths.

    Phase 2E-C3-B extension: ``distance_km`` is a new optional top-level field
    representing the spacecraft-to-Earth distance in kilometres at the start of
    the communication pass.  It is mission geometry metadata only.

    Design constraints (non-negotiable):
    - distance_km is NEVER passed to TelecomEngine or any RF formula.
    - distance_km does NOT affect SNR, Eb/N0, BER, goodput, or capacity.
    - distance_km is stored here for future use by GET /state (C3-C),
      frontend display (C3-D), and AI context (C3-E).
    - Legacy scenarios that omit this field continue to load without change.
    """

    scenario_id: str
    simulated: bool = Field(description="Must be True; distinguishes simulated data from real telemetry")
    link_inputs: dict = Field(
        description="Raw inputs consumed by TelecomEngine.compute(); keys must match engine expectations"
    )
    mission_state: MissionState
    packets: list[Packet] = Field(default_factory=list, description="Packets awaiting transmission")
    data_products: list[DataProduct] = Field(
        default_factory=list,
        description=(
            "Phase 2A: Mission data products awaiting transmission. "
            "Optional — existing scenarios without this field default to an empty list."
        )
    )
    anomalies: list[AnomalyEvent] = Field(
        default_factory=list,
        description=(
            "Phase 2A: Active anomaly events on the spacecraft. "
            "Optional — existing scenarios without this field default to an empty list."
        )
    )
    distance_km: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Phase 2E-C3-B: Spacecraft-to-Earth distance in kilometres at the start of "
            "the communication pass.  Optional — legacy scenarios without this field "
            "default to None.  Must be >= 0 when provided.  "
            "This is mission geometry metadata only: it is never consumed by "
            "TelecomEngine, PlanEvaluator, or TransmissionSimulator."
        ),
    )
