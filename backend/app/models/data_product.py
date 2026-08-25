from __future__ import annotations

from pydantic import BaseModel, Field


class DataProduct(BaseModel):
    """A spacecraft-generated data product awaiting transmission.

    Represents a piece of mission information — telemetry, science, imagery,
    diagnostic data, housekeeping, etc. — that the ground station wants to
    receive and that the spacecraft must decide whether to transmit during the
    current communication window.

    Priority is NOT a field on this model. Priority is an output of future
    AI-assisted reasoning and deterministic feasibility analysis, not a static
    mission attribute.

    Fields describe mission facts — what the data is, where it came from, how
    valuable it is, how urgent it is, and what it relates to.
    """

    product_id: str = Field(
        description="Unique identifier for this data product, e.g. 'TEL-PROP-001'"
    )
    product_type: str = Field(
        description=(
            "Category of data, e.g. 'telemetry', 'science', 'image', "
            "'diagnostic', 'housekeeping', 'experiment', 'command_ack', "
            "'navigation', 'health'"
        )
    )
    description: str = Field(
        default="",
        description=(
            "Concise human-readable explanation of what this data product contains. "
            "Used by AI reasoning to understand operational meaning beyond numeric scores. "
            "Examples: 'Thruster-2 valve position telemetry captured during the active "
            "propulsion anomaly window.' or 'Routine housekeeping telemetry covering "
            "power, thermal, and processor health.' "
            "Optional — defaults to empty string for backward compatibility."
        ),
    )
    subsystem: str = Field(
        description=(
            "Spacecraft subsystem that generated this product, "
            "e.g. 'propulsion', 'power', 'thermal', 'communications', "
            "'navigation', 'attitude_control', 'payload', 'flight_computer'"
        )
    )
    size_bits: int = Field(gt=0, description="Size of the data product in bits")
    criticality: float = Field(
        ge=0.0, le=1.0,
        description=(
            "Operational importance of this product [0, 1]. "
            "This is a mission attribute, NOT the final transmission priority."
        )
    )
    mission_relevance: float = Field(
        ge=0.0, le=1.0,
        description="How directly this product relates to the current mission objective [0, 1]"
    )
    scientific_value: float = Field(
        ge=0.0, le=1.0,
        description="Scientific usefulness of this product [0, 1]"
    )
    deadline_s: float = Field(
        ge=0.0,
        description="Seconds until the product's useful transmission deadline"
    )
    age_s: float = Field(
        ge=0.0,
        description=(
            "Age of the information at the time of the current decision, in seconds. "
            "Supports reasoning such as 'this telemetry is already 240 s old'."
        )
    )
    anomaly_id: str | None = Field(
        default=None,
        description=(
            "Optional identifier linking this product to an active anomaly event, "
            "e.g. 'ANOM-017'. None when not associated with an anomaly."
        )
    )
    experiment_id: str | None = Field(
        default=None,
        description=(
            "Optional identifier linking this product to a scientific experiment, "
            "e.g. 'EXP-MARS-004'. None when not applicable."
        )
    )
    related_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of related data products, e.g. ['TEL-PROP-001', 'DIAG-PROP-002']. "
            "Used by AI reasoning to understand relationships between products."
        )
    )
    delivery_requirement: str = Field(
        description=(
            "Delivery constraint, e.g. 'required', 'best_effort', "
            "'redundant', 'latest_only'"
        )
    )
    retry_cost: float = Field(
        ge=0.0,
        description=(
            "Operational cost weight associated with retransmitting this product. "
            "Mission metadata for future use; not consumed by the evaluator in Phase 2A."
        )
    )
