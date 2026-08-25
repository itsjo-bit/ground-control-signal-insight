from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AICandidateConfig(BaseSettings):
    """Configuration for the AI candidate prioritization layer (Phase 2C).

    Controls how many data products are selected for AI context before the
    LLM is invoked.  Keeping this bounded prevents LLM token overflow when
    hundreds or thousands of data products are available.

    Override via environment variable::

        GCSI_AI_MAX_CANDIDATES=50
    """

    model_config = SettingsConfigDict(env_prefix="GCSI_AI_", extra="ignore")

    max_candidates: int = Field(
        default=50,
        gt=0,
        description=(
            "Maximum number of CandidateSummary objects passed to the AI "
            "prioritization call.  Must be > 0.  Default 50."
        ),
    )


class SchedulerWeights(BaseSettings):
    """Configurable weights for the BaselineScheduler five-factor scoring.

    Scoring formula::

        score =
            w_criticality        * criticality
          + w_deadline_urgency   * deadline_urgency
          + w_mission_relevance  * mission_relevance
          + w_delivery_reliability * packet_success_probability
          + w_cost_efficiency    * cost_efficiency

    where cost_efficiency = 1 - min(expected_cost / comm_window, 1.0).

    All five factors are in [0, 1] so weights are directly comparable.
    """

    model_config = SettingsConfigDict(env_prefix="GCSI_SCHED_", extra="ignore")

    w_criticality: float = Field(default=0.30, gt=0.0, description="Weight for packet criticality")
    w_deadline_urgency: float = Field(default=0.25, gt=0.0, description="Weight for deadline urgency")
    w_mission_relevance: float = Field(default=0.20, gt=0.0, description="Weight for mission relevance")
    w_delivery_reliability: float = Field(default=0.15, gt=0.0, description="Weight for packet delivery reliability (packet_success_probability)")
    w_cost_efficiency: float = Field(default=0.10, gt=0.0, description="Weight for cost efficiency (1 - cost_pressure)")


class RiskWeights(BaseSettings):
    """Configurable weights for the PlanEvaluator risk_score formula.

    Formula:
        risk_score = clamp(
            w_deadline_miss  * deadline_miss_rate
          + w_critical_deficit * critical_deficit
          + w_window_pressure  * window_pressure,
          0.0, 1.0
        )

    Defaults sum to 1.0.
    """

    model_config = SettingsConfigDict(env_prefix="GCSI_RISK_", extra="ignore")

    w_deadline_miss: float = Field(default=0.40, gt=0.0, description="Weight for deadline miss rate")
    w_critical_deficit: float = Field(default=0.40, gt=0.0, description="Weight for critical packet delivery deficit")
    w_window_pressure: float = Field(default=0.20, gt=0.0, description="Weight for communication-window pressure")


class TelecomConfig(BaseSettings):
    """Telecom model configuration.

    Contains only the constants actually consumed by the current BPSK/AWGN model.
    Do NOT add noise figure or frequency band — they are not consumed by the current model.

    Eb/N0 formula:
        Eb/N0_dB = snr_db + 10 * log10(channel_bandwidth_hz / bit_rate_bps)

    Link goodput formula:
        link_goodput_bps = nominal_data_rate_bps * protocol_efficiency
    """

    model_config = SettingsConfigDict(env_prefix="GCSI_TELECOM_", extra="ignore")

    modulation: str = Field(default="BPSK", description="Modulation scheme; only BPSK is supported by current formulas")
    channel_bandwidth_hz: float = Field(default=1_000_000.0, gt=0.0, description="Channel bandwidth B in Hz")
    bit_rate_bps: float = Field(default=100_000.0, gt=0.0, description="Signal bit rate Rb in bps")
    protocol_efficiency: float = Field(
        default=0.9,
        gt=0.0,
        le=1.0,
        description="Link-layer protocol efficiency (0, 1]; accounts for headers, ACKs, and framing overhead",
    )

    @field_validator("modulation")
    @classmethod
    def modulation_must_be_bpsk(cls, v: str) -> str:
        if v != "BPSK":
            raise ValueError(f"Only 'BPSK' is supported by the current telecom model; got '{v}'")
        return v


class GCSIConfig(BaseSettings):
    """Top-level GCSI configuration.  Composes all sub-configs."""

    model_config = SettingsConfigDict(env_prefix="GCSI_", extra="ignore")

    scheduler: SchedulerWeights = Field(default_factory=SchedulerWeights)
    risk: RiskWeights = Field(default_factory=RiskWeights)
    telecom: TelecomConfig = Field(default_factory=TelecomConfig)
