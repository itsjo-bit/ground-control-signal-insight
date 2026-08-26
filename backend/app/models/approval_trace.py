"""ApprovalTrace — typed record of a plan approval execution.

Created whenever the backend executes a plan via POST /approve or
POST /approve/custom.  Returned in ApproveResponse and stored as
``state.last_approval_trace``.

This model supports audit consistency — it proves that the plan that was
evaluated is the plan that was executed.  The SHA-256 fingerprints are
*integrity fingerprints*, NOT cryptographic signatures or authentication tokens.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class ApprovalTrace(BaseModel):
    """Complete provenance record for one plan approval execution.

    Fields
    ------
    approval_id
        Unique identifier for this approval event (plan_id + short timestamp).
        Not globally unique across processes; sufficient for single-session audit.
    timestamp_utc
        ISO-8601 UTC timestamp when the approval was processed by the backend.
    scenario_id
        The active scenario_id at the time of approval.
    plan_id
        The plan_id that was executed.
    decision
        Always "approved" for successful executions.
    plan_source
        Trusted source classification assigned by the backend.
        One of: deterministic_generated, ai_generated, operator_custom,
        legacy_regenerated, client_intent.
        CLIENT-SUPPLIED ``generated_by`` IS NEVER USED HERE.
    operator_notes
        Operator-supplied notes (trimmed, max 500 chars).
    authoritative_reconstruction
        True when all packet facts were rebound from the authoritative scenario.
        Should always be True for Phase 4+ approvals.
    issued_plan_verified
        True when the plan matched a server-generated issued plan in the registry.
        False for operator-custom plans and legacy-regenerated plans.
    packet_count
        Number of packets in the executed plan.
    packet_order_sha256
        SHA-256 hex digest of the ordered packet ID sequence.
        Useful for quick order-equality checks.
    canonical_plan_sha256
        SHA-256 hex digest of the full canonical plan content
        (scenario_id + plan_id + source + ordered authoritative packets).
        The same hash is computable from ``executed_plan`` + scenario_id.
    """

    approval_id: str = Field(description="Short approval event identifier")
    timestamp_utc: str = Field(description="ISO-8601 UTC timestamp")
    scenario_id: str = Field(description="Active scenario ID at approval time")
    plan_id: str = Field(description="Executed plan_id")
    decision: str = Field(default="approved", description="Always 'approved'")
    plan_source: str = Field(
        description=(
            "Trusted plan source: deterministic_generated | ai_generated | "
            "operator_custom | legacy_regenerated | client_intent.  "
            "NOT derived from client-supplied generated_by."
        )
    )
    operator_notes: str = Field(
        default="",
        description="Operator-supplied notes (trimmed, max 500 chars)",
    )
    authoritative_reconstruction: bool = Field(
        description="True when all packet facts were rebound from scenario inventory"
    )
    issued_plan_verified: bool = Field(
        description=(
            "True when this plan was found in the server-generated issued-plan "
            "registry with matching order.  False for operator-custom plans."
        )
    )
    packet_count: int = Field(ge=0, description="Number of packets in executed plan")
    packet_order_sha256: str = Field(
        description="SHA-256 of ordered packet IDs (integrity fingerprint, not a signature)"
    )
    canonical_plan_sha256: str = Field(
        description="SHA-256 of full canonical plan content (integrity fingerprint, not a signature)"
    )
