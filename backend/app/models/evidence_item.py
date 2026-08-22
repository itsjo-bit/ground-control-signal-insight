from typing import Any
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    """A single structured piece of evidence cited by the AI agent.

    Evidence must cite only fields present in the LinkState or MissionState
    provided to the agent. Invented field names are rejected by GraniteAgent.
    """

    source: str = Field(description="Model class the evidence comes from, e.g. 'LinkState'")
    field: str = Field(description="Field name on the source model, e.g. 'snr_db'")
    value: Any = Field(description="The actual field value at the time of recommendation")
    interpretation: str = Field(description="Human-readable explanation of why this value is significant")
