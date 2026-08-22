from pydantic import BaseModel, Field
from .packet import Packet


class CandidatePlan(BaseModel):
    """An ordered transmission plan produced by the scheduler or candidate generator."""

    plan_id: str
    strategy: str = Field(
        description=(
            "Strategy name, e.g. 'baseline', 'deadline_first', "
            "'mission_critical_first', 'value_per_cost'"
        )
    )
    packets: list[Packet] = Field(description="Packets in intended transmission order")
    generated_by: str = Field(description="Module that produced this plan, e.g. 'BaselineScheduler'")
    metadata: dict = Field(default_factory=dict, description="Optional plan-level metadata")
