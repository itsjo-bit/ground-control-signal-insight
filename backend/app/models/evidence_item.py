from typing import Any
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """A single structured piece of evidence cited by the AI agent.

    Evidence must cite only fields present in the data exposed to the agent.
    Invented field names are rejected by providers.

    Fields
    ------
    option_id
        For ``source == "candidate_option"``: the **real** trusted plan
        identity after the alias→real-plan mapping has been applied.  Set to
        ``None`` for ``link_state`` and ``mission_state`` evidence which is
        not option-specific.

        During the Stage-2 trust boundary (inside external providers), this
        field temporarily holds the opaque OPTION alias.  After the backend
        applies the trusted alias→plan mapping the field contains the real
        plan_id (e.g. ``"ai-prioritized"``).  Operator-facing output always
        contains the real plan identity.

    source
        The data source the evidence comes from.  One of:
        ``"candidate_option"`` | ``"link_state"`` | ``"mission_state"`` |
        legacy values such as ``"evaluation_result"``.

    field
        Exact field name on the source model, e.g. ``"risk_score"``.

    value
        The authoritative backend-supplied value.  Never supplied by the LLM.

    interpretation
        Human-readable explanation of why this value is significant.
    """

    option_id: str | None = Field(
        default=None,
        description=(
            "For candidate_option evidence: the real trusted plan identity "
            "(after alias mapping). Null for link_state and mission_state evidence."
        ),
    )
    source: str = Field(description="Data source: candidate_option | link_state | mission_state")
    field: str = Field(description="Field name on the source model, e.g. 'risk_score'")
    value: Any = Field(description="The authoritative backend-supplied field value")
    interpretation: str = Field(description="Human-readable explanation of why this value is significant")
