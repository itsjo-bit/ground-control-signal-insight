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

The two objects link via trial_id (== BenchmarkTrial.trial_id).
This keeps raw data CSV-exportable without duplication.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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
    SCHEMA_ERROR = "schema_error"
    PLAN_BUILD_ERROR = "plan_build_error"
    EVALUATION_ERROR = "evaluation_error"
    PIPELINE_ERROR = "pipeline_error"
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


class ExperimentVariant(str, Enum):
    """Which experiment variant produced this trial/plan result."""
    FULL = "full"
    NO_DESCRIPTION = "no_description"
    NO_ANOMALY_CONTEXT = "no_anomaly_context"


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
    # Link to the trial that produced this result
    trial_id: str = Field(description="Unique trial identifier (matches BenchmarkTrial.trial_id)")
    # Legacy field kept for backward compatibility — equals trial_id
    run_id: str = Field(description="Alias for trial_id (backward compat)")
    scenario_id: str
    repetition: int
    # Experiment variant: full / no_description / no_anomaly_context
    experiment_variant: ExperimentVariant = Field(default=ExperimentVariant.FULL)
    plan_type: PlanType
    plan_order_hash: str = Field(description="SHA-256 of the ordered packet ID list")
    physical_metrics: PhysicalMetrics
    mission_outcome_metrics: MissionOutcomeMetrics
    # Pareto / comparison populated by analysis layer
    is_pareto_frontier: Optional[bool] = None
    plans_dominated_count: Optional[int] = None
    plans_dominating_this_plan_count: Optional[int] = None


# ---------------------------------------------------------------------------
# Provider execution result (returned by BenchmarkProvider)
# ---------------------------------------------------------------------------


class BenchmarkProviderResult(BaseModel):
    """Execution metadata returned by the benchmark provider after a call.

    Carries the actual data exchanged with the external model so the runner
    can record exact provenance without reconstructing it independently.
    """
    prioritization: Any = None          # CandidatePrioritization | None
    attempt_count: int = 1
    provider_latency_ms_total: float = 0.0
    attempt_latencies_ms: list[float] = Field(default_factory=list)
    raw_response: str = ""             # exact raw text from provider
    raw_response_sha256: str = ""      # sha256(raw_response.encode())
    actual_model_id: str = ""
    generation_config: dict = Field(default_factory=dict)
    # Exact messages actually sent (populated by provider)
    actual_system_prompt: str = ""
    actual_user_message: str = ""
    actual_system_sha256: str = ""
    actual_user_sha256: str = ""


# ---------------------------------------------------------------------------
# Trial record (one LLM invocation)
# ---------------------------------------------------------------------------


class BenchmarkTrial(BaseModel):
    """One benchmark trial = one LLM invocation + all control plans.

    The trial record holds metadata.  Per-plan results are in
    BenchmarkPlanResult objects linked by trial_id.
    """
    # Identity
    trial_id: str = Field(description="Unique trial identifier")
    # Legacy alias kept for backward compat (equals trial_id)
    run_id: str = Field(description="Benchmark run identifier (from BenchmarkRunner.run_id)")
    benchmark_run_id: str = Field(description="Parent benchmark run identifier")
    benchmark_version: str
    scenario_id: str
    scenario_variant: ScenarioVariantSpec
    provider: str
    model: str
    repetition: int

    # Experiment variant
    experiment_variant: ExperimentVariant = Field(default=ExperimentVariant.FULL)

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
    provider_latency_ms_total: Optional[float] = None
    attempt_count: int = 1
    attempt_latencies_ms: list[float] = Field(default_factory=list)

    # Provenance hashes (no secrets)
    prompt_system_sha256: str = ""
    prompt_user_sha256: str = ""
    response_hash: str = ""       # alias kept; equals ranking_hash for backward compat
    raw_response_sha256: str = ""  # sha256 of exact raw provider response text
    ranking_hash_separate: str = "" # sha256 of ranked product ID list (same as ranking_hash)

    # Provider identity
    actual_model_id: str = ""
    generation_config: dict = Field(default_factory=dict)

    # Ablation variant (legacy — use experiment_variant)
    ablation_variant: Optional[str] = None

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
    benchmark_run_id: str = ""  # same as run_id
    timestamp_utc: str
    started_at: str = ""
    completed_at: str = ""
    run_status: str = "started"   # started | completed | partial | aborted
    run_type: str = "core"        # core | pilot | ablation | dev
    git_commit_sha: str
    git_dirty: Optional[bool] = None
    base_scenario_sha256: str
    provider: str
    model: str
    actual_model_id: str = ""
    generation_config: dict = Field(default_factory=dict)
    candidate_limit: int
    scenario_matrix: list[str] = Field(description="List of scenario_ids in this run")
    repetitions: int
    retry_policy: dict
    primary_metrics: list[str]
    comparison_tolerance: float
    python_version: str
    platform: str
    gcsi_benchmark_version: str = "2b.1"
    # Config provenance
    config_id: str = ""
    config_sha256: str = ""
    preregistered: bool = False
    config_overrides: dict = Field(default_factory=dict)
    # Executed values (reflect actual execution, not just config intent)
    executed_capacity_ratios: list[float] = Field(default_factory=list)
    executed_anomaly_modes: list[str] = Field(default_factory=list)
    executed_deadline_scales: list[float] = Field(default_factory=list)
    executed_candidate_limit: int = 0
    executed_repetitions: int = 0
    executed_retry_policy: dict = Field(default_factory=dict)
    # No API keys, no IAM tokens, no project IDs


# ---------------------------------------------------------------------------
# Typed Benchmark Configuration Model
# ---------------------------------------------------------------------------


class RetryPolicyConfig(BaseModel):
    max_attempts: int = Field(ge=1, default=2)
    delay_between_attempts_s: float = Field(ge=0.0, default=1.0)


class AblationConfig(BaseModel):
    ablation_a: dict = Field(default_factory=dict)
    ablation_b: dict = Field(default_factory=dict)
    ablation_scenarios: list[str] = Field(default_factory=list)


class BenchmarkConfig(BaseModel):
    """Typed, validated benchmark configuration.

    Loaded from gcsi_benchmark_v1.json and used as the sole source of truth
    for official benchmark runs.  All fields are validated before execution.
    """
    benchmark_version: str
    base_scenario: str
    capacity_ratios: list[float]
    anomaly_modes: list[str]
    deadline_modes: list[float]
    repetitions: int = Field(ge=1)
    repetitions_quick: int = Field(ge=1, default=1)
    candidate_limit: int = Field(ge=1)
    provider: str
    model: str
    retry_policy: RetryPolicyConfig
    primary_metrics: dict  # {"maximize": [...], "minimize": [...]}
    secondary_metrics_descriptive: list[str] = Field(default_factory=list)
    ablation_configuration: AblationConfig = Field(default_factory=AblationConfig)
    comparison_tolerance: float = Field(gt=0.0)
    pareto_analysis: bool = True
    win_tie_loss_analysis: bool = True
    capacity_stress_analysis: bool = True
    anomaly_mode_analysis: bool = True
    negative_control_scenario: str = ""
    headline_comparator: str = ""
    fairness_controls: dict = Field(default_factory=dict)
    integrity_gates_passed: dict = Field(default_factory=dict)

    @field_validator("capacity_ratios")
    @classmethod
    def validate_capacity_ratios(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("capacity_ratios must be non-empty")
        for r in v:
            if r <= 0:
                raise ValueError(f"capacity_ratio must be > 0, got {r}")
        return v

    @field_validator("anomaly_modes")
    @classmethod
    def validate_anomaly_modes(cls, v: list[str]) -> list[str]:
        valid = {"ORIGINAL", "NOANOM", "DECOY"}
        for m in v:
            if m not in valid:
                raise ValueError(f"anomaly_mode '{m}' not in {valid}")
        return v

    @field_validator("primary_metrics")
    @classmethod
    def validate_primary_metrics(cls, v: dict) -> dict:
        if "maximize" not in v or "minimize" not in v:
            raise ValueError("primary_metrics must have 'maximize' and 'minimize' keys")
        if not v["maximize"] or not v["minimize"]:
            raise ValueError("primary_metrics.maximize and .minimize must be non-empty")
        return v

    @classmethod
    def from_file(cls, path: str | Path) -> "BenchmarkConfig":
        """Load and validate a benchmark config from a JSON file."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Benchmark config not found: {p}")
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Remove comment keys before validation
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        # Coerce retry_policy dict to RetryPolicyConfig
        if "retry_policy" in data and isinstance(data["retry_policy"], dict):
            rp = {k: v for k, v in data["retry_policy"].items() if not k.startswith("_")}
            data["retry_policy"] = rp
        return cls.model_validate(data)

    @property
    def config_sha256(self) -> str:
        """SHA-256 of the canonical JSON representation of this config."""
        canonical = self.model_dump_json(indent=2)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def compute_file_sha256(self, path: str | Path) -> str:
        """SHA-256 of the raw file bytes — for provenance of the source file."""
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
