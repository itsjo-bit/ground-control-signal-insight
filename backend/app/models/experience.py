"""Pydantic models for the GCSI experience manifest (GET /experience).

Strongly typed counterpart to data/demo/asteria7_experience.json.
These models are for presentation metadata only — never feed them
into PlanEvaluator, MissionOutcomeEvaluator, or the simulator.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExperienceDisplay(BaseModel):
    """Display metadata for the mission hero panel."""

    mission_name: str
    scenario_name: str
    spacecraft_name: str
    ground_station_name: str
    ground_station_description: str
    disclaimer: str = ""


class ExperienceSchedule(BaseModel):
    """Presentation schedule hints.

    one_way_signal_s_note is a reminder string — not a computed value.
    The actual propagation delay is always derived from distance_km at runtime.
    """

    next_contact_in_s: float
    plan_uplink_margin_s: float
    contact_duration_s: float
    one_way_signal_s_note: str = ""


class SubsystemStatus(BaseModel):
    """Presentation status for one spacecraft subsystem."""

    status: str  # e.g. "degraded", "nominal", "stable"
    trend: str   # e.g. "rising", "falling", "stable"
    label: str   # e.g. "▲ DEGRADED"
    note: str = ""


class SubsystemStatusMap(BaseModel):
    """All subsystem statuses used by the spacecraft health panel."""

    thermal: SubsystemStatus
    communications: SubsystemStatus
    power: SubsystemStatus
    propulsion: SubsystemStatus


class HistoricalSnrPoint(BaseModel):
    """One SNR history data point."""

    offset_s: float   # negative = past (e.g. -900 = 15 min ago), 0 = now
    snr_db: float


class HistoricalThermalPoint(BaseModel):
    """One thermal history data point."""

    offset_s: float
    temp_c: float


class IngestBatchProduct(BaseModel):
    """One product-type entry within a batch."""

    type: str
    count: int


class IngestBatch(BaseModel):
    """One batch in the ingest replay sequence."""

    offset_ms: int
    products: list[IngestBatchProduct]

    @property
    def total_count(self) -> int:
        return sum(p.count for p in self.products)


class IngestReplay(BaseModel):
    """Full ingest replay configuration.

    total_products and total_bytes are the authoritative scenario values;
    the sum of all batch product counts must equal total_products.
    """

    total_products: int
    total_bytes: int
    batches: list[IngestBatch]


class ExperiencePlaybackConfig(BaseModel):
    """Presentation-only timing for demo choreography.

    These durations control the *visual* playback only.
    They are NEVER fed into the simulator, evaluator, or telecom formulas.
    """

    ingest_duration_ms: int = 6600
    uplink_duration_ms: int = 1500
    contact_acquisition_ms: int = 2000
    transmission_min_duration_ms: int = 2000
    propagation_duration_ms: int = 3000
    ground_receive_interval_ms: int = 400


class ExperienceManifest(BaseModel):
    """The full experience manifest for a scenario."""

    schema_version: str
    scenario_id: str
    display: ExperienceDisplay
    schedule: ExperienceSchedule
    subsystem_status: SubsystemStatusMap
    snr_history: list[HistoricalSnrPoint]
    thermal_history: list[HistoricalThermalPoint]
    ingest_replay: IngestReplay
    ground_information_objectives: dict[str, list[str]]
    curated_candidate_ids: list[str]
    playback: ExperiencePlaybackConfig = Field(default_factory=ExperiencePlaybackConfig)


class ExperienceResponse(BaseModel):
    """Response for GET /experience."""

    available: bool
    manifest: ExperienceManifest | None = None
