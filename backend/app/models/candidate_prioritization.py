"""CandidatePrioritization — the AI's response to a prioritize_candidates() call.

This model captures the output of Phase 2C AI candidate prioritization.
The AI ranks a bounded set of ``CandidateSummary`` objects by mission importance
and explains the reasoning for each placement.

Phase 2D extends this model with structured decision factors and anomaly
relationships so the operator can inspect WHY each product was prioritized.

Phase 2E-D3 extends ``RankedProduct`` with an optional ``description`` field
that carries the human-readable semantic context from ``DataProduct.description``.
This allows the frontend to display "Thruster-2 chamber pressure diagnostic" instead
of the bare product ID.  Defaults to ``""`` for full backward compatibility.

Design constraints
------------------
- ``ranked_products`` contains only ``product_id`` values that appeared in the
  supplied candidate set.  Any hallucinated ID is rejected by the validation
  layer before this model is populated.
- Priority values must be contiguous positive integers starting at 1 (1 = most
  important).
- The model does NOT contain transmission feasibility claims (link capacity,
  window fit, success probability).  Those remain deterministic pipeline outputs.
- ``confidence`` is the AI's self-reported confidence in the ranking [0, 1].
- ``decision_factors`` are the AI's explanation of the primary decision drivers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RankedProduct(BaseModel):
    """A single entry in the AI's prioritized product ranking.

    Phase 2D extends this with structured factors, anomaly links, subsystem,
    and per-product confidence so the operator can inspect each decision.

    Fields
    ------
    product_id
        Must match a ``product_id`` that was supplied in the candidate set.
        The validation layer rejects any ID not in the set.
    priority
        Rank position starting at 1.  Lower = higher priority.
    reason
        Human-readable explanation of why this product was ranked at this
        position.  Must be present and non-empty.
    factors
        Structured labels describing the decision drivers, e.g.
        ``["active anomaly", "high criticality", "deadline urgency"]``.
        May be empty for the Local (deterministic) provider.
    anomaly_ids
        IDs of active anomalies that influenced the prioritization of this
        product.  May be empty when no anomaly relationship exists.
    subsystem
        Spacecraft subsystem this product originates from.
        May be empty when not available.
    confidence
        Per-product AI confidence [0, 1].  Optional — absent from the Local
        provider; present when an LLM provides per-item confidence.
    """

    product_id: str = Field(description="product_id matching a supplied CandidateSummary")
    priority: int = Field(ge=1, description="Rank position (1 = highest priority)")
    reason: str = Field(min_length=1, description="AI reasoning for this ranking position")
    # Phase 2E-D3: optional human-readable product description forwarded from DataProduct.
    # Defaults to "" for full backward compatibility with existing providers and tests.
    description: str = Field(
        default="",
        description=(
            "Concise human-readable explanation of what this data product contains. "
            "Forwarded from DataProduct.description. "
            "Empty string when not provided (backwards compatible)."
        ),
    )
    # Phase 2D fields — all optional for backwards compatibility
    factors: list[str] = Field(
        default_factory=list,
        description="Structured decision factor labels (AI-derived)",
    )
    anomaly_ids: list[str] = Field(
        default_factory=list,
        description="Active anomaly IDs that influenced this ranking",
    )
    subsystem: str = Field(
        default="",
        description="Spacecraft subsystem that generated this product",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Per-product AI confidence [0, 1]; None when not reported",
    )


class CandidatePrioritization(BaseModel):
    """The AI's complete response to a candidate prioritization request.

    Phase 2D extends this with a list of top-level ``decision_factors`` that
    summarise the primary drivers across the entire prioritization decision.

    Fields
    ------
    ranked_products
        Ordered list of :class:`RankedProduct` objects.  The AI may return
        fewer items than the supplied candidate count (e.g. if some products
        are clearly irrelevant), but may never return more, and may never
        include IDs that were not supplied.
    overall_reasoning
        Human-readable explanation of the global prioritization strategy.
    confidence
        AI self-reported confidence [0, 1].  Not authoritative for
        scheduling; used for transparency.
    decision_factors
        Top-level structured labels describing the primary factors that drove
        the overall prioritization decision, e.g.
        ``["active anomaly", "deadline urgency", "mission criticality"]``.
    candidate_count
        How many candidates were analysed.  Used in the UI summary.
    """

    ranked_products: list[RankedProduct] = Field(
        description="Prioritized products, most important first"
    )
    overall_reasoning: str = Field(
        min_length=1,
        description="High-level explanation of the prioritization strategy",
    )
    confidence: float = Field(ge=0.0, le=1.0, description="AI confidence in the ranking [0, 1]")
    # Phase 2D fields — all optional for backwards compatibility
    decision_factors: list[str] = Field(
        default_factory=list,
        description="Top-level decision factor labels for the overall prioritization",
    )
    candidate_count: int | None = Field(
        default=None,
        description="Number of candidates analysed (informational)",
    )
