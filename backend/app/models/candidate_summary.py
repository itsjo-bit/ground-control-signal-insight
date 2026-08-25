"""CandidateSummary — compact representation of a DataProduct for AI reasoning.

A ``CandidateSummary`` contains only the fields required for mission-level
semantic prioritization.  It is intentionally smaller than the full
``DataProduct`` object: sending the complete DataProduct graph to an LLM
would waste tokens and risk context overflow when hundreds of products are
available.

Design constraints
------------------
- All fields map 1-to-1 from ``DataProduct`` — no invented fields.
- Fields used only by the deterministic pipeline (``retry_cost``,
  ``delivery_requirement``) are omitted here; the scheduler retains them.
- Optional fields default to ``None`` / empty list to match ``DataProduct``.
- The model is immutable (``frozen=True``) so summaries cannot be mutated
  after construction.
- ``description`` carries the human-readable semantic context of the product
  (Phase 2E-A).  Defaults to empty string for full backward compatibility.

Relationship to DataProduct
---------------------------
``CandidateSummary`` is derived from ``DataProduct`` by
``candidate_prioritizer._summarise()``.  The mapping is explicit and lossless
for the fields the AI needs; fields not relevant to semantic reasoning are
dropped.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateSummary(BaseModel):
    """Compact, AI-readable summary of a spacecraft data product.

    Fields are a strict subset of :class:`~backend.app.models.data_product.DataProduct`.
    No priority, rank, or score fields are present here — those are outputs of
    AI reasoning, not inputs.

    Phase 2E-A adds ``description``: a concise human-readable explanation of
    what the product contains, enabling semantic reasoning beyond numeric scores.
    """

    model_config = {"frozen": True}

    product_id: str = Field(description="Unique identifier for this data product")
    product_type: str = Field(description="Category of data, e.g. 'telemetry', 'diagnostic'")
    description: str = Field(
        default="",
        description=(
            "Concise human-readable explanation of what this data product contains. "
            "Enables AI semantic reasoning beyond numeric scores. "
            "Empty string when not provided (backwards compatible)."
        ),
    )
    subsystem: str = Field(description="Spacecraft subsystem that generated this product")
    size_bits: int = Field(gt=0, description="Size of the data product in bits")
    criticality: float = Field(ge=0.0, le=1.0, description="Operational importance [0, 1]")
    mission_relevance: float = Field(ge=0.0, le=1.0, description="Relevance to current mission objective [0, 1]")
    scientific_value: float = Field(ge=0.0, le=1.0, description="Scientific usefulness [0, 1]")
    deadline_s: float = Field(ge=0.0, description="Seconds until transmission deadline")
    age_s: float = Field(ge=0.0, description="Age of the information in seconds at decision time")
    anomaly_id: str | None = Field(default=None, description="Linked anomaly ID, or None")
    experiment_id: str | None = Field(default=None, description="Linked experiment ID, or None")
    related_ids: list[str] = Field(
        default_factory=list,
        description="IDs of related data products for contextual AI reasoning",
    )
