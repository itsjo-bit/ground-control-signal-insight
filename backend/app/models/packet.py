from pydantic import BaseModel, Field


class Packet(BaseModel):
    """Data unit awaiting transmission.

    Priority is NOT a field on this model. Priority is computed by the
    BaselineScheduler and expressed as ordering within a CandidatePlan.
    """

    packet_id: str
    packet_type: str = Field(description="Packet category, e.g. 'telemetry', 'command', 'science'")
    size_bits: int = Field(gt=0, description="Packet size in bits")
    criticality: float = Field(ge=0.0, le=1.0, description="Packet criticality score [0, 1]")
    mission_relevance: float = Field(ge=0.0, le=1.0, description="Mission relevance score [0, 1]")
    deadline_s: float = Field(ge=0.0, description="Transmission deadline relative to window start, in seconds")
    retry_cost: float = Field(ge=0.0, description="Cost weight associated with retransmitting this packet")
    delivery_requirement: str = Field(
        description="Delivery constraint, e.g. 'required', 'best-effort', 'optional'"
    )
