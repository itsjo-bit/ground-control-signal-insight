"""Typed data models for GCSI benchmark records.

PRIMARY METRICS (pre-registered before running experiments):
    Maximize: mission_value, critical_delivery_rate, scientific_value_capture_rate,
              required_delivery_rate, active_anomaly_delivery_rate,
              high_severity_anomaly_coverage_rate, anomaly_weighted_coverage
    Minimize: risk_score, deadline_miss_rate

These lists must NOT change after benchmark results are seen.

All benchmark output is written as instances of these models.
No free-form result dicts: every field is named and typed.

Normalization
-------------
A single benchmark trial produces:
  - One BenchmarkTrial (provider run metadata + provenance)
  - N BenchmarkPlanResult objects (one per competitor plan)

The two objects link via (run_id, scenario_id, repetition).
This keeps raw data CSV-exportable without duplication.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Pre-registered primary metrics (frozen — must not change after seeing results)
# ---------------------------------------------------------------------------

PRIMARY_METRICS_MAXIMIZE: list[str] = [
    "mission_value",
    "critical_delivery_rate",
    "scientific_value_capture_rate",
    "required_delivery_rate",
    "active_anomaly_delivery_rate",
    "high_severity_anomaly_coverage_rate",
    "anomaly_weighted_coverage",
]

PRIMARY_METRICS_MINIMIZE: list[str] = [
    "risk_score",
    "deadline_miss_rate",
]

PRIMARY_METRICS: list[str] = PRIMARY_METRICS_MAXIMIZE + PRIMARY_METRICS_MINIMIZE


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BenchmarkStatus(str, Enum):
    """Terminal status of a benchmark trial."""
    SUCCESS = "success"
    PROVIDER_ERROR = "provider_error"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    SKIPPED = "skipped"


class AnomalyMode(str, Enum):
    ORIGINAL = "ORIGINAL"
    NO_ANOMALY = "NOANOM"
    RESOLVED_DECOY = "DECOY"


class PlanType(str, Enum):
    BASELINE = "baseline"
    DEADLINE_FIRST = "deadline-first"
    MISSION_CRITICAL_FIRST = "mission-critical-first"
    VALUE_PER_COST = "value-per-cost"
    SEMANTIC_RULE = "semantic-rule-based"
    AI_PRIORITIZED = "ai-prioritized"
    # Ablation variants
    AI_NO_DESCRIPTION = "ai-no-description"
    AI_NO_ANOMALY_CONTEXT = "ai-no-anomaly-context"


class MetricDirection(str, Enum):
    """Higher is better or lower is better."""
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"
    DESCRIPTIVE = "descriptive"


# ---------------------------------------------------------------------------
# Physical / telecom metrics
# ---------------------------------------------------------------------------


class PhysicalMetrics(BaseModel):
    """Metrics from PlanEvaluator (deterministic telecom physics)."""
    risk_score: float
    mission_value: float
    critical_packets_delivered: int
    total_critical_packets: int
    critical_delivery_rate: Optional[float]  # None when denominator == 0
    deadline_misses: int
    deadline_miss_rate: float
    bandwidth_utilization: float
    retransmission_overhead: float
    window_pressure: float
    deferred_count: int
    delivery_rate: Optional[float] = None  # from MissionOutcome layer


# ---------------------------------------------------------------------------
# Semantic / mission outcome metrics
# ---------------------------------------------------------------------------


class MissionOutcomeMetrics(BaseModel):
    """Metrics from MissionOutcomeEvaluator."""
    scientific_value_capture_rate: Optional[float] = None
    required_delivery_rate: Optional[float] = None
    active_anomaly_delivery_rate: Optional[float] = None
    high_severity_anomaly_coverage_rate: Optional[float] = None
    anomaly_weighted_coverage: Optional[float] = None
    average_delivered_age_s: Optional[float] = None
    delivery_rate: Optional[float] = None


# ---------------------------------------------------------------------------
# Scenario variant descriptor
# ---------------------------------------------------------------------------


class ScenarioVariantSpec(BaseModel):
    """Complete descriptor of one benchmark scenario variant.

    Created by the scenario variant generator.  Never mutates the base scenario.
    """
    scenario_id: str = Field(description="Deterministic name e.g. 'CAP035_ORIGINAL'")
    capacity_ratio: float = Field(ge=0.0, description="Target capacity ratio (bits / total queue)")
    anomaly_mode: AnomalyMode
    deadline_scale: float = Field(default=1.0, ge=0.0, description="Deadline multiplier (1.0 = original)")
    # Measured values (after generation)
    total_queued_bits: int = Field(ge=0)
    link_goodput_bps: float = Field(gt=0.0)
    communication_window_s: float = Field(gt=0.0)
    available_capacity_bits: float = Field(ge=0.0)
    actual_capacity_ratio: float = Field(ge=0.0)
    base_scenario_id: str = Field(default="mission_data_v3_high_volume_pass")
    base_scenario_sha256: str = Field(default="")


# ---------------------------------------------------------------------------
# Per-plan result
# ---------------------------------------------------------------------------


class BenchmarkPlanResult(BaseModel):
    """Metrics for a single competitor plan in one trial."""
    run_id: str
    scenario_id: str
    repetition: int
    plan_type: PlanType
    plan_order_hash: str = Field(description="SHA-256 of the ordered packet ID list")
    physical_metrics: PhysicalMetrics
    mission_outcome_metrics: MissionOutcomeMetrics
    # Pareto / comparison populated by analysis layer
    is_pareto_frontier: Optional[bool] = None
    plans_dominated_count: Optional[int] = None
    plans_dominating_this_plan_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Trial record (one LLM invocation)
# ---------------------------------------------------------------------------


class BenchmarkTrial(BaseModel):
    """One benchmark trial = one LLM invocation + all control plans.

    The trial record holds metadata.  Per-plan results are in
    BenchmarkPlanResult objects linked by (run_id, scenario_id, repetition).
    """
    run_id: str
    benchmark_version: str
    scenario_id: str
    scenario_variant: ScenarioVariantSpec
    provider: str
    model: str
    repetition: int

    # Status
    status: BenchmarkStatus
    error_type: Optional[str] = None
    error_message_sanitized: Optional[str] = None

    # Capacity / scenario verification
    capacity_ratio: float
    actual_capacity_ratio: float

    # Candidate screening
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    ranked_count: int = 0
    unranked_count: int = 0
    ranking: list[str] = Field(default_factory=list, description="Ranked product IDs in order")
    ranking_hash: str = ""

    # Provider timing / reliability
    provider_latency_ms: Optional[float] = None
    attempt_count: int = 1

    # Provenance hashes (no secrets)
    prompt_system_sha256: str = ""
    prompt_user_sha256: str = ""
    response_hash: str = ""

    # Ablation variant
    ablation_variant: Optional[str] = None  # None = full context, "no_description", "no_anomaly_context"

    # Plan IDs in this trial (for cross-reference with BenchmarkPlanResult)
    plan_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Benchmark run manifest
# ---------------------------------------------------------------------------


class BenchmarkManifest(BaseModel):
    """Machine-readable record describing one complete benchmark run.

    Written as manifest.json in every run output directory.
    Does NOT contain secrets.
    """
    benchmark_version: str
    run_id: str
    timestamp_utc: str
    git_commit_sha: str
    base_scenario_sha256: str
    provider: str
    model: str
    candidate_limit: int
    scenario_matrix: list[str] = Field(description="List of scenario_ids in this run")
    repetitions: int
    retry_policy: dict
    primary_metrics: list[str]
    comparison_tolerance: float
    python_version: str
    platform: str
    gcsi_benchmark_version: str = "2b"
    # No API keys, no IAM tokens, no project IDs
