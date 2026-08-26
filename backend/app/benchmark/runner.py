"""Benchmark runner — executes the benchmark experiment matrix.

Strict provider mode:
    In benchmark mode, Granite failure → record GRANITE FAILURE.
    No Local fallback is substituted.  No alternate provider.
    A failed run is part of provider reliability statistics.

Retry policy:
    Only genuine transient transport/service failures are retried.
    max_attempts and delay come from the effective BenchmarkConfig.

    RETRIABLE:
        GraniteTransportError  (connection failure, timeout, transient 5xx)
    NOT RETRIABLE:
        GraniteResponseError   (malformed JSON, schema failure, invalid IDs)
        GraniteSchemaError     (subclass of GraniteResponseError)
        GraniteParseError      (subclass of GraniteResponseError)
        Any other exception    (classified appropriately)

    A malformed model response counts as PARSE_ERROR / SCHEMA_ERROR and
    terminates the trial immediately — it is NOT retried.
    A valid (even poor) ranking is accepted as-is — NOT retried.

Trial success semantics:
    status=SUCCESS requires ALL of:
        1. External provider response valid
        2. CandidatePrioritization valid
        3. AI ranked plan constructed (build_ai_prioritized_plan succeeds)
        4. PlanEvaluator succeeds
        5. MissionOutcomeEvaluator succeeds
        6. BenchmarkPlanResult for AI exists
    Any failure in the pipeline produces a non-success status.

Provenance:
    The exact system prompt and user message are captured from the provider.
    Hashes are computed over the exact bytes sent to the model.
    Raw response SHA-256 is recorded separately from the ranking hash.

Isolation:
    Benchmark execution NEVER modifies global FastAPI state, transmission
    history, operator approval state, or frontend data.
    All scenario loading and plan generation uses pure local objects.

Deterministic controls:
    The four classical plans and the semantic-rule plan are computed ONCE
    per scenario and reused for all repetitions.
    They are stable across repetitions (same inputs → same hash).

Fairness:
    The same CandidatePrioritizer output feeds both the SemanticRulePrioritizer
    and the Granite Stage-1 call.
    Both use build_ranked_prefix_plan() via their respective wrappers.
    Both are evaluated by the same PlanEvaluator and MissionOutcomeEvaluator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from pydantic import ValidationError

from ..agent.candidate_prioritizer import CandidatePrioritizer
from ..agent.semantic_rule_prioritizer import SemanticRulePrioritizer
from ..candidate_generator.ai_plan_builder import build_ai_prioritized_plan
from ..candidate_generator.generator import CandidateGenerator
from ..candidate_generator.semantic_rule_plan_builder import build_semantic_rule_plan
from ..config import SchedulerWeights
from ..evaluator.mission_outcome_evaluator import MissionOutcomeEvaluator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_summary import CandidateSummary
from ..models.evaluation_result import EvaluationResult
from ..models.scenario import Scenario
from ..telecom.engine import TelecomEngine
from .models import (
    BenchmarkConfig,
    BenchmarkManifest,
    BenchmarkPlanResult,
    BenchmarkProviderResult,
    BenchmarkStatus,
    BenchmarkTrial,
    ExperimentVariant,
    MissionOutcomeMetrics,
    PhysicalMetrics,
    PlanType,
    PRIMARY_METRICS,
    ScenarioVariantSpec,
)
from .scenario_variants import BenchmarkScenarioVariant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BENCHMARK_VERSION = "gcsi_benchmark_v1"
COMPARISON_TOLERANCE = 1e-9  # floating-point tolerance for pairwise comparisons

# Default benchmark config path (canonical v1)
DEFAULT_BENCHMARK_CONFIG_PATH = Path("benchmarks/configs/gcsi_benchmark_v1.json")

PRIMARY_METRICS_MAXIMIZE = [
    "mission_value",
    "critical_delivery_rate",
    "scientific_value_capture_rate",
    "required_delivery_rate",
    "active_anomaly_delivery_rate",
    "high_severity_anomaly_coverage_rate",
    "anomaly_weighted_coverage",
]
PRIMARY_METRICS_MINIMIZE = [
    "risk_score",
    "deadline_miss_rate",
]
PRIMARY_METRICS = PRIMARY_METRICS_MAXIMIZE + PRIMARY_METRICS_MINIMIZE

ALL_PLAN_TYPES_ORDERED = [
    PlanType.BASELINE,
    PlanType.DEADLINE_FIRST,
    PlanType.MISSION_CRITICAL_FIRST,
    PlanType.VALUE_PER_COST,
    PlanType.SEMANTIC_RULE,
    PlanType.AI_PRIORITIZED,
]


# ---------------------------------------------------------------------------
# Explicit error type hierarchy for retry classification
# ---------------------------------------------------------------------------


class GraniteTransportError(Exception):
    """Raised for retriable transport/service failures.

    Examples: connection failure, timeout, HTTP 429/500/502/503/504.
    These may be retried up to max_attempts.
    """


class GraniteTransientHTTPError(GraniteTransportError):
    """HTTP status code that indicates transient service unavailability."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(message or f"HTTP {status_code}")


class GraniteTimeoutError(GraniteTransportError):
    """Request timed out — retriable."""


def is_retriable_benchmark_error(exc: Exception) -> bool:
    """Return True if *exc* is a retriable transient transport/service failure.

    RETRIABLE (explicit list only — default is NO):
        GraniteTransportError (and subclasses: GraniteTransientHTTPError,
            GraniteTimeoutError)
        httpx.TimeoutException / ConnectError / TransportError subclasses
        GraniteAPIError signalling connection error, timeout, or HTTP 429/500/502/503/504

    NOT RETRIABLE (default — everything else):
        GraniteResponseError (malformed JSON, invalid product IDs, schema errors)
        GraniteAPIError with HTTP 400/401/403/404/409/422 or unknown application errors
        Unexpected response shape (missing results[0].generated_text)
        Any validation error
        Any other exception

    The default branch is ``return False`` (default-deny).
    Unknown GraniteAPIError cases are NOT retried.
    """
    # Explicit retriable types
    if isinstance(exc, GraniteTransportError):
        return True

    # httpx transport errors — only connection / timeout / transport failures
    exc_class = type(exc).__name__
    exc_module = type(exc).__module__

    if "httpx" in exc_module:
        if "Timeout" in exc_class or "Connect" in exc_class or "Transport" in exc_class:
            return True

    # GraniteAPIError can be either transport OR response — inspect carefully
    from ..agent.granite_agent import GraniteAPIError, GraniteResponseError

    if isinstance(exc, GraniteResponseError):
        # GraniteResponseError always means model output was invalid — not retriable
        return False

    if isinstance(exc, GraniteAPIError):
        msg = str(exc).lower()
        # Connection/timeout failures are retriable
        if "connection" in msg or "timeout" in msg:
            return True
        # Explicit HTTP transient codes ONLY — matched against known patterns
        # "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"
        for code in ("429", "500", "502", "503", "504"):
            if f"http {code}" in msg:
                return True
        # Explicitly non-retriable HTTP status codes — default-deny for all others
        for bad_code in ("400", "401", "403", "404", "409", "422"):
            if f"http {bad_code}" in msg:
                return False
        # Unknown GraniteAPIError → default-deny (NOT retriable)
        # This is intentional: unknown errors should NOT be silently retried
        return False

    # Anything else (parse errors, validation errors, etc.) — not retriable
    return False


# ---------------------------------------------------------------------------
# Provider protocol (strict benchmark mode — no fallback)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Structured benchmark provider failure (Part B — failure provenance)
# ---------------------------------------------------------------------------


class BenchmarkProviderFailure(Exception):
    """Raised by GraniteBenchmarkProvider when a call fails.

    Carries the same provenance metadata that a successful BenchmarkProviderResult
    carries, wherever that data is available at the point of failure.

    This ensures failed trials record:
      - attempt_count  (actual number of attempts made)
      - attempt_latencies_ms  (per-attempt latency list)
      - raw_response  (any response body received before failure)
      - raw_response_sha256  (hash of above)
      - actual_model_id  (the model used in the request)
      - generation_config  (the generation parameters sent)
      - actual_system_prompt / actual_system_sha256
      - actual_user_message / actual_user_sha256
      - cause  (the original exception)
      - status_hint  (BenchmarkStatus suggested for this failure)

    The runner converts this to a BenchmarkProviderResult via to_provider_result().
    """

    def __init__(
        self,
        cause: Exception,
        *,
        status_hint: "BenchmarkStatus",
        attempt_count: int,
        provider_latency_ms_total: float = 0.0,
        attempt_latencies_ms: list[float] | None = None,
        raw_response: str = "",
        raw_response_sha256: str = "",
        actual_model_id: str = "",
        generation_config: dict | None = None,
        actual_system_prompt: str = "",
        actual_user_message: str = "",
        actual_system_sha256: str = "",
        actual_user_sha256: str = "",
    ) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.status_hint = status_hint
        self.attempt_count = attempt_count
        self.provider_latency_ms_total = provider_latency_ms_total
        self.attempt_latencies_ms = attempt_latencies_ms or []
        self.raw_response = raw_response
        self.raw_response_sha256 = raw_response_sha256
        self.actual_model_id = actual_model_id
        self.generation_config = generation_config or {}
        self.actual_system_prompt = actual_system_prompt
        self.actual_user_message = actual_user_message
        self.actual_system_sha256 = actual_system_sha256
        self.actual_user_sha256 = actual_user_sha256

    def to_provider_result(self) -> "BenchmarkProviderResult":
        """Convert this failure into a BenchmarkProviderResult with no prioritization."""
        return BenchmarkProviderResult(
            prioritization=None,
            attempt_count=self.attempt_count,
            provider_latency_ms_total=self.provider_latency_ms_total,
            attempt_latencies_ms=self.attempt_latencies_ms,
            raw_response=self.raw_response,
            raw_response_sha256=self.raw_response_sha256,
            actual_model_id=self.actual_model_id,
            generation_config=self.generation_config,
            actual_system_prompt=self.actual_system_prompt,
            actual_user_message=self.actual_user_message,
            actual_system_sha256=self.actual_system_sha256,
            actual_user_sha256=self.actual_user_sha256,
        )


class BenchmarkProvider:
    """Minimal provider interface for benchmark mode.

    Strict mode: if the external call fails, raise BenchmarkProviderFailure —
    do NOT fall back to Local.  The caller records the failure status and continues.

    The provider must return a BenchmarkProviderResult containing:
    - prioritization (CandidatePrioritization or None on failure)
    - attempt_count
    - provider_latency_ms_total
    - raw_response (exact text from model)
    - raw_response_sha256
    - actual_model_id
    - generation_config
    - actual_system_prompt / actual_user_message / their hashes
    """

    @property
    def provider_name(self) -> str:
        return "unknown"

    @property
    def model_id(self) -> str:
        return "unknown"

    def prioritize(
        self,
        candidates: list[CandidateSummary],
        link_state,
        mission_state,
        anomalies: list,
        distance_km: Optional[float] = None,
    ) -> BenchmarkProviderResult:
        """Return BenchmarkProviderResult or raise on failure."""
        raise NotImplementedError


class GraniteBenchmarkProvider(BenchmarkProvider):
    """IBM Granite provider in strict benchmark mode (no Local fallback).

    Retry policy:
        Only GraniteTransportError / transient transport failures are retried.
        GraniteResponseError and all model-output errors terminate the trial.

    Model enforcement:
        The benchmark config model is the sole authority for which model is used.
        If ``model_id`` is supplied, GraniteAgent is explicitly instantiated with
        that model_id — the environment variable GCSI_GRANITE_MODEL_ID is never
        consulted for benchmark execution.  This prevents silent model substitution.

    Args:
        max_attempts:   Maximum retry attempts for transient transport failures.
        delay_s:        Delay between retry attempts (seconds).
        agent:          Optional pre-built GraniteAgent (for testing / injection).
                        If provided, model_id is derived from the agent.
        model_id:       The effective benchmark model ID. Required for official runs.
                        GraniteAgent is instantiated with this exact model_id.
                        (Formerly ``config_model_id`` — rename enforces strict usage.)
    """

    def __init__(
        self,
        max_attempts: int = 2,
        delay_s: float = 1.0,
        agent=None,
        model_id: str = "",
        # Legacy alias kept for backward compat — same semantic
        config_model_id: str = "",
    ) -> None:
        self.max_attempts = max_attempts
        self.delay_s = delay_s
        self._agent = agent
        self._provider_name = "Granite"
        # model_id wins over config_model_id (legacy)
        self._effective_model_id: str = model_id or config_model_id or ""

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_id(self) -> str:
        """Return the actual model ID that will be/was used by GraniteAgent.

        If an agent has already been instantiated, reflect its actual _model_id.
        Otherwise return the effective benchmark model (set at construction).
        Never returns 'unknown' for a properly configured benchmark provider.
        """
        if self._agent is not None:
            return getattr(self._agent, "_model_id", self._effective_model_id or "unknown")
        return self._effective_model_id or "unknown"

    def _ensure_agent(self):
        """Create GraniteAgent using the effective benchmark model.

        The benchmark config model_id is passed explicitly to GraniteAgent so the
        environment variable GCSI_GRANITE_MODEL_ID cannot silently override it.
        """
        if self._agent is None:
            from ..agent.granite_agent import GraniteAgent
            if self._effective_model_id:
                # Explicit model enforcement: pass the config model to GraniteAgent.
                # This prevents GCSI_GRANITE_MODEL_ID from substituting another model.
                self._agent = GraniteAgent(model_id=self._effective_model_id)
            else:
                # No configured model — fall through to GraniteAgent default / env var.
                # This path should not occur for official benchmark execution.
                self._agent = GraniteAgent()
        return self._agent

    def prioritize(
        self,
        candidates: list[CandidateSummary],
        link_state,
        mission_state,
        anomalies: list,
        distance_km: Optional[float] = None,
    ) -> BenchmarkProviderResult:
        """Execute the prioritization call and return a BenchmarkProviderResult.

        Only transient transport failures trigger a retry.
        Model output failures (parse/schema/invalid) terminate immediately.
        """
        from ..agent.granite_agent import (
            GraniteAgent,
            GraniteAPIError,
            GraniteParseError,
            GraniteResponseError,
            GraniteSchemaError,
            STAGE1_GENERATION_CONFIG,
            _PRIORITIZATION_SYSTEM_PROMPT,
        )
        from ..agent.prioritization_helpers import build_prioritization_message

        agent = self._ensure_agent()

        # Build the exact messages that will be sent
        actual_user_message = build_prioritization_message(
            candidates, link_state, mission_state, anomalies,
            distance_km=distance_km,
        )
        actual_system_prompt = _PRIORITIZATION_SYSTEM_PROMPT
        actual_system_sha256 = _sha256_hex(actual_system_prompt)
        actual_user_sha256 = _sha256_hex(actual_user_message)

        actual_model_id = getattr(agent, "_model_id", "unknown")
        # Use canonical STAGE1_GENERATION_CONFIG — single source of truth
        generation_config = dict(STAGE1_GENERATION_CONFIG)

        last_exc: Exception | None = None
        attempt_latencies: list[float] = []
        total_start = time.monotonic()
        raw_response = ""

        for attempt in range(1, self.max_attempts + 1):
            attempt_start = time.monotonic()
            try:
                # Call the low-level prioritization API directly so we can
                # capture the raw response before parsing
                raw_response = agent._call_prioritization_api(actual_user_message)
                attempt_latencies.append((time.monotonic() - attempt_start) * 1000.0)

                # Parse the response — parse errors are NOT retriable
                valid_ids = {cs.product_id for cs in candidates}
                prioritization = agent._parse_prioritization_response(
                    raw_response, valid_ids, candidates
                )

                raw_sha = _sha256_hex(raw_response)
                ranked_ids = [rp.product_id for rp in prioritization.ranked_products]
                total_latency_ms = (time.monotonic() - total_start) * 1000.0

                return BenchmarkProviderResult(
                    prioritization=prioritization,
                    attempt_count=attempt,
                    provider_latency_ms_total=total_latency_ms,
                    attempt_latencies_ms=attempt_latencies,
                    raw_response=raw_response,
                    raw_response_sha256=raw_sha,
                    actual_model_id=actual_model_id,
                    generation_config=generation_config,
                    actual_system_prompt=actual_system_prompt,
                    actual_user_message=actual_user_message,
                    actual_system_sha256=actual_system_sha256,
                    actual_user_sha256=actual_user_sha256,
                )

            except GraniteResponseError as exc:
                # Model output error — NOT retriable, terminate immediately.
                # Preserve the raw response (already captured above if API call succeeded).
                attempt_latencies.append((time.monotonic() - attempt_start) * 1000.0)
                logger.warning(
                    "Granite attempt %d/%d: non-retriable response error: %s",
                    attempt, self.max_attempts, exc
                )
                total_latency_ms = (time.monotonic() - total_start) * 1000.0
                raw_sha = _sha256_hex(raw_response) if raw_response else ""
                # Determine status hint: distinguish parse errors from schema/invalid errors
                from ..agent.granite_agent import GraniteParseError, GraniteSchemaError
                if isinstance(exc, GraniteParseError):
                    hint = BenchmarkStatus.PARSE_ERROR
                elif isinstance(exc, GraniteSchemaError):
                    hint = BenchmarkStatus.SCHEMA_ERROR
                else:
                    hint = BenchmarkStatus.INVALID_RESPONSE
                raise BenchmarkProviderFailure(
                    cause=exc,
                    status_hint=hint,
                    attempt_count=attempt,
                    provider_latency_ms_total=total_latency_ms,
                    attempt_latencies_ms=list(attempt_latencies),
                    raw_response=raw_response,
                    raw_response_sha256=raw_sha,
                    actual_model_id=actual_model_id,
                    generation_config=generation_config,
                    actual_system_prompt=actual_system_prompt,
                    actual_user_message=actual_user_message,
                    actual_system_sha256=actual_system_sha256,
                    actual_user_sha256=actual_user_sha256,
                ) from exc

            except GraniteAPIError as exc:
                attempt_latencies.append((time.monotonic() - attempt_start) * 1000.0)
                if not is_retriable_benchmark_error(exc):
                    logger.warning(
                        "Granite attempt %d/%d: non-retriable API error: %s",
                        attempt, self.max_attempts, exc
                    )
                    total_latency_ms = (time.monotonic() - total_start) * 1000.0
                    raise BenchmarkProviderFailure(
                        cause=exc,
                        status_hint=BenchmarkStatus.PROVIDER_ERROR,
                        attempt_count=attempt,
                        provider_latency_ms_total=total_latency_ms,
                        attempt_latencies_ms=list(attempt_latencies),
                        raw_response=raw_response,
                        raw_response_sha256=_sha256_hex(raw_response) if raw_response else "",
                        actual_model_id=actual_model_id,
                        generation_config=generation_config,
                        actual_system_prompt=actual_system_prompt,
                        actual_user_message=actual_user_message,
                        actual_system_sha256=actual_system_sha256,
                        actual_user_sha256=actual_user_sha256,
                    ) from exc
                last_exc = exc
                logger.warning(
                    "Granite attempt %d/%d: retriable transport error: %s",
                    attempt, self.max_attempts, exc
                )
                if attempt < self.max_attempts:
                    time.sleep(self.delay_s)

            except Exception as exc:
                attempt_latencies.append((time.monotonic() - attempt_start) * 1000.0)
                if not is_retriable_benchmark_error(exc):
                    total_latency_ms = (time.monotonic() - total_start) * 1000.0
                    raise BenchmarkProviderFailure(
                        cause=exc,
                        status_hint=BenchmarkStatus.PROVIDER_ERROR,
                        attempt_count=attempt,
                        provider_latency_ms_total=total_latency_ms,
                        attempt_latencies_ms=list(attempt_latencies),
                        raw_response=raw_response,
                        raw_response_sha256=_sha256_hex(raw_response) if raw_response else "",
                        actual_model_id=actual_model_id,
                        generation_config=generation_config,
                        actual_system_prompt=actual_system_prompt,
                        actual_user_message=actual_user_message,
                        actual_system_sha256=actual_system_sha256,
                        actual_user_sha256=actual_user_sha256,
                    ) from exc
                last_exc = exc
                logger.warning(
                    "Granite attempt %d/%d: retriable error: %s",
                    attempt, self.max_attempts, exc
                )
                if attempt < self.max_attempts:
                    time.sleep(self.delay_s)

        # All attempts exhausted — raise with full provenance
        total_latency_ms = (time.monotonic() - total_start) * 1000.0
        raise BenchmarkProviderFailure(
            cause=last_exc,  # type: ignore[arg-type]
            status_hint=BenchmarkStatus.PROVIDER_ERROR,
            attempt_count=self.max_attempts,
            provider_latency_ms_total=total_latency_ms,
            attempt_latencies_ms=list(attempt_latencies),
            raw_response=raw_response,
            raw_response_sha256=_sha256_hex(raw_response) if raw_response else "",
            actual_model_id=actual_model_id,
            generation_config=generation_config,
            actual_system_prompt=actual_system_prompt,
            actual_user_message=actual_user_message,
            actual_system_sha256=actual_system_sha256,
            actual_user_sha256=actual_user_sha256,
        )


class FakeProvider(BenchmarkProvider):
    """Test/dry-run provider that returns a fixed prioritization from a callback.

    The callback signature matches the original provider.prioritize signature:
        callback(candidates, link_state, mission_state, anomalies, distance_km=None)
            -> CandidatePrioritization  (or raises)

    FakeProvider wraps it in BenchmarkProviderResult for the runner.
    """

    def __init__(
        self,
        callback: Callable,
        model_id: str = "fake-model",
        system_prompt: str = "fake-system-prompt",
    ) -> None:
        self._cb = callback
        self._model_id = model_id
        self._system_prompt = system_prompt
        self._provider_name = "fake"

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_id(self) -> str:
        return self._model_id

    def prioritize(
        self,
        candidates: list[CandidateSummary],
        link_state,
        mission_state,
        anomalies: list,
        distance_km: Optional[float] = None,
    ) -> BenchmarkProviderResult:
        from ..agent.prioritization_helpers import build_prioritization_message

        # Build the exact user message for hash provenance
        actual_user_message = build_prioritization_message(
            candidates, link_state, mission_state, anomalies,
            distance_km=distance_km,
        )
        actual_system_sha256 = _sha256_hex(self._system_prompt)
        actual_user_sha256 = _sha256_hex(actual_user_message)

        start = time.monotonic()
        raw_response = ""

        prioritization = self._cb(candidates, link_state, mission_state, anomalies, distance_km=distance_km)

        latency_ms = (time.monotonic() - start) * 1000.0
        raw_response = json.dumps(
            {"ranked_products": [
                {"product_id": rp.product_id} for rp in prioritization.ranked_products
            ]},
            separators=(",", ":"),
        )
        raw_sha = _sha256_hex(raw_response)

        return BenchmarkProviderResult(
            prioritization=prioritization,
            attempt_count=1,
            provider_latency_ms_total=latency_ms,
            attempt_latencies_ms=[latency_ms],
            raw_response=raw_response,
            raw_response_sha256=raw_sha,
            actual_model_id=self._model_id,
            generation_config={},
            actual_system_prompt=self._system_prompt,
            actual_user_message=actual_user_message,
            actual_system_sha256=actual_system_sha256,
            actual_user_sha256=actual_user_sha256,
        )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def _plan_order_hash(plan: CandidatePlan) -> str:
    ids = json.dumps([p.packet_id for p in plan.packets], separators=(",", ":"))
    return hashlib.sha256(ids.encode()).hexdigest()


def _ranking_hash(ranked_ids: list[str]) -> str:
    return hashlib.sha256(json.dumps(ranked_ids, separators=(",", ":")).encode()).hexdigest()


def _get_git_commit() -> tuple[str, bool]:
    """Return (commit_sha, is_dirty)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent.parent),
        )
        sha = result.stdout.strip() if result.returncode == 0 else "unknown"

        dirty_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent.parent.parent.parent),
        )
        dirty = bool(dirty_result.stdout.strip()) if dirty_result.returncode == 0 else False
        return sha, dirty
    except Exception:  # noqa: BLE001
        return "unknown", False


def _physical_metrics(ev: EvaluationResult, mo=None) -> PhysicalMetrics:
    total_crit = ev.total_critical_packets
    crit_rate = (ev.critical_packets_delivered / total_crit) if total_crit > 0 else None
    return PhysicalMetrics(
        risk_score=ev.risk_score,
        mission_value=ev.mission_value,
        critical_packets_delivered=ev.critical_packets_delivered,
        total_critical_packets=ev.total_critical_packets,
        critical_delivery_rate=crit_rate,
        deadline_misses=ev.deadline_misses,
        deadline_miss_rate=ev.deadline_miss_rate,
        bandwidth_utilization=ev.bandwidth_utilization,
        retransmission_overhead=ev.retransmission_overhead,
        window_pressure=ev.window_pressure,
        deferred_count=len(ev.deferred_packets),
        delivery_rate=mo.delivery_rate if mo else None,
    )


def _mission_metrics(mo) -> MissionOutcomeMetrics:
    if mo is None:
        return MissionOutcomeMetrics()
    return MissionOutcomeMetrics(
        scientific_value_capture_rate=mo.scientific_value_capture_rate,
        required_delivery_rate=mo.required_delivery_rate,
        active_anomaly_delivery_rate=mo.active_anomaly_delivery_rate,
        high_severity_anomaly_coverage_rate=mo.high_severity_anomaly_coverage_rate,
        anomaly_weighted_coverage=mo.anomaly_weighted_coverage,
        average_delivered_age_s=mo.average_delivered_age_s,
        delivery_rate=mo.delivery_rate,
    )


def _evaluate_plan(
    plan: CandidatePlan,
    link_state,
    mission_state,
    data_products,
    anomalies,
) -> tuple:
    """Run both evaluators on a plan.  Returns (EvaluationResult, MissionOutcomeResult)."""
    evaluator = PlanEvaluator()
    ev = evaluator.evaluate(plan, link_state, mission_state)

    mo_evaluator = MissionOutcomeEvaluator()
    mo = mo_evaluator.evaluate(plan, ev, data_products, anomalies)
    return ev, mo


def _sanitize_error(msg: str) -> str:
    """Remove potential secrets from error messages."""
    msg = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", msg)
    msg = re.sub(r"apikey[=:]\s*\S+", "apikey=[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"api[_-]?key[=:]\s*\S+", "api_key=[REDACTED]", msg, flags=re.IGNORECASE)
    return msg[:500]


# ---------------------------------------------------------------------------
# Deterministic control plans
# ---------------------------------------------------------------------------


def build_deterministic_plans(
    scenario: Scenario,
    link_state,
    candidates: list[CandidateSummary],
    weights: SchedulerWeights,
) -> dict[PlanType, tuple[CandidatePlan, EvaluationResult, object]]:
    """Build all deterministic plans (4 classical + 1 semantic-rule).

    Classical plans see the full packet queue.
    Semantic-rule plan uses the same bounded candidate set as the LLM.

    Returns:
        Dict mapping PlanType → (plan, eval_result, mission_outcome_result).
    """
    all_packets = data_products_to_packets(scenario.data_products)
    anomalies = scenario.anomalies

    # Four classical plans from full queue
    gen = CandidateGenerator()
    classical_plans = gen.generate(all_packets, link_state, scenario.mission_state, weights)

    # Semantic-rule plan from same candidate set
    sr_plan = build_semantic_rule_plan(
        all_packets=all_packets,
        candidates=candidates,
        anomalies=anomalies,
        link_state=link_state,
        mission_state=scenario.mission_state,
        weights=weights,
    )

    plan_type_map = {
        "baseline": PlanType.BASELINE,
        "deadline-first": PlanType.DEADLINE_FIRST,
        "mission-critical-first": PlanType.MISSION_CRITICAL_FIRST,
        "value-per-cost": PlanType.VALUE_PER_COST,
    }

    results: dict[PlanType, tuple] = {}

    for plan in classical_plans:
        pt = plan_type_map.get(plan.plan_id)
        if pt is None:
            continue
        ev, mo = _evaluate_plan(plan, link_state, scenario.mission_state, scenario.data_products, anomalies)
        results[pt] = (plan, ev, mo)

    ev_sr, mo_sr = _evaluate_plan(sr_plan, link_state, scenario.mission_state, scenario.data_products, anomalies)
    results[PlanType.SEMANTIC_RULE] = (sr_plan, ev_sr, mo_sr)

    return results


# ---------------------------------------------------------------------------
# Audit file helpers
# ---------------------------------------------------------------------------


_SECRET_PATTERNS = [
    re.compile(r"Bearer\s+\S+"),
    re.compile(r"apikey[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"api[_-]?key[=:]\s*\S+", re.IGNORECASE),
    re.compile(r'"Authorization"\s*:\s*"[^"]*"', re.IGNORECASE),
    re.compile(r"iam_token[=:]\s*\S+", re.IGNORECASE),
    re.compile(r"access_token[=:]\s*\"[^\"]*\"", re.IGNORECASE),
]


def _redact_secrets(text: str) -> str:
    """Redact known secret patterns from text before writing audit files."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _write_audit_files(
    audit_dir: Path,
    trial_id: str,
    system_prompt: str,
    user_message: str,
    raw_response: str,
) -> None:
    """Write sanitized audit files for one trial."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    safe_id = trial_id.replace("/", "_").replace("\\", "_")

    # System prompt
    sys_file = audit_dir / f"{safe_id}.system.txt"
    sys_file.write_text(_redact_secrets(system_prompt), encoding="utf-8")

    # User message
    user_file = audit_dir / f"{safe_id}.user.json"
    user_file.write_text(_redact_secrets(user_message), encoding="utf-8")

    # Raw response
    resp_file = audit_dir / f"{safe_id}.response.txt"
    resp_file.write_text(_redact_secrets(raw_response), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main trial runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    """Execute benchmark trials for a scenario variant.

    Args:
        provider:         BenchmarkProvider implementing strict mode (no fallback).
        repetitions:      Number of LLM repetitions per scenario.
        candidate_limit:  Max candidates to pass to the LLM.
        dry_run:          If True, skip all external provider calls.
        save_prompts:     If True, save sanitized prompt content to output_dir/audit/.
        output_dir:       Where to write results (raw_results.jsonl, manifest.json, etc.).
        run_id:           Stable run identifier (auto-generated if None).
        benchmark_config: Optional BenchmarkConfig for recording provenance.
        config_overrides: Dict of overridden parameters (marks run non-preregistered).
        run_type:         "core" | "pilot" | "dev"
    """

    def __init__(
        self,
        provider: BenchmarkProvider,
        repetitions: int = 5,
        candidate_limit: int = 50,
        dry_run: bool = False,
        save_prompts: bool = False,
        output_dir: Path | None = None,
        run_id: str | None = None,
        benchmark_config: Optional[BenchmarkConfig] = None,
        config_overrides: dict | None = None,
        run_type: str = "core",
    ) -> None:
        self.provider = provider
        self.repetitions = repetitions
        self.candidate_limit = candidate_limit
        self.dry_run = dry_run
        self.save_prompts = save_prompts
        self.output_dir = output_dir
        self.benchmark_config = benchmark_config
        self.config_overrides = config_overrides or {}
        self.run_type = run_type
        self.run_id = run_id or f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self._weights = SchedulerWeights()
        self._plan_evaluator = PlanEvaluator()
        self._mo_evaluator = MissionOutcomeEvaluator()

        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
            self._result_path = output_dir / "raw_results.jsonl"
        else:
            self._result_path = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_variant(
        self,
        variant: BenchmarkScenarioVariant,
        *,
        ablation_variant: str | None = None,
        candidates_override: list[CandidateSummary] | None = None,
    ) -> list[BenchmarkTrial]:
        """Run all repetitions for one scenario variant.

        Returns:
            List of BenchmarkTrial objects (one per repetition, plus plan results appended
            to raw output).
        """
        scenario = variant.scenario
        spec = variant.spec

        # Compute link state
        engine = TelecomEngine()
        link_state = engine.compute(scenario.link_inputs)

        # Candidate selection — ONCE per scenario, shared by LLM and semantic-rule
        if candidates_override is not None:
            candidates = candidates_override
        else:
            prioritizer = CandidatePrioritizer(max_candidates=self.candidate_limit)
            candidates = prioritizer.select(
                scenario.data_products,
                anomalies=scenario.anomalies,
                remaining_window_s=link_state.remaining_window_s,
            )

        candidate_ids = [cs.product_id for cs in candidates]
        candidate_count = len(candidates)

        # Deterministic control plans — computed ONCE, reused for all repetitions
        logger.info("Building deterministic control plans for %s", spec.scenario_id)
        det_plans = build_deterministic_plans(
            scenario, link_state, candidates, self._weights
        )

        # Determine experiment variant
        if ablation_variant == "no_description":
            experiment_variant = ExperimentVariant.NO_DESCRIPTION
            llm_candidates = [
                cs.model_copy(update={"description": ""}) for cs in candidates
            ]
            llm_anomalies = list(scenario.anomalies)
        elif ablation_variant == "no_anomaly_context":
            experiment_variant = ExperimentVariant.NO_ANOMALY_CONTEXT
            llm_candidates = candidates
            llm_anomalies = []
        else:
            experiment_variant = ExperimentVariant.FULL
            llm_candidates = candidates
            llm_anomalies = list(scenario.anomalies)

        trials: list[BenchmarkTrial] = []

        for rep in range(1, self.repetitions + 1):
            logger.info(
                "Running %s rep %d/%d (variant=%s)",
                spec.scenario_id, rep, self.repetitions, experiment_variant.value
            )
            trial = self._run_single_repetition(
                scenario=scenario,
                spec=spec,
                link_state=link_state,
                candidates=llm_candidates,
                llm_anomalies=llm_anomalies,
                candidate_ids=candidate_ids,
                candidate_count=candidate_count,
                det_plans=det_plans,
                repetition=rep,
                experiment_variant=experiment_variant,
                ablation_variant=ablation_variant,
            )
            trials.append(trial)
            if self._result_path:
                self._append_trial(trial)

        return trials

    def run_matrix(
        self,
        variants: list[BenchmarkScenarioVariant],
        *,
        include_ablations: bool = False,
        ablation_scenarios: list[str] | None = None,
    ) -> list[BenchmarkTrial]:
        """Run all variants in the matrix.

        Args:
            variants:          List of scenario variants to run.
            include_ablations: Whether to run ablation variants.
            ablation_scenarios: Scenario IDs for which to run ablations.
                                Defaults to canonical 4-scenario subset from config.
        """
        all_trials: list[BenchmarkTrial] = []

        # Core runs
        for variant in variants:
            trials = self.run_variant(variant)
            all_trials.extend(trials)

        # Ablation runs
        if include_ablations:
            if ablation_scenarios is None and self.benchmark_config:
                ablation_scenarios = list(
                    self.benchmark_config.ablation_configuration.ablation_scenarios
                )
            abl_subset = ablation_scenarios or [
                "CAP035_ORIGINAL", "CAP060_ORIGINAL",
                "CAP035_NOANOM", "CAP060_DECOY",
            ]
            abl_variants = [v for v in variants if v.spec.scenario_id in abl_subset]
            for ablation in ("no_description", "no_anomaly_context"):
                for variant in abl_variants:
                    trials = self.run_variant(variant, ablation_variant=ablation)
                    all_trials.extend(trials)

        return all_trials

    def estimate_call_count(
        self,
        n_scenarios: int,
        *,
        include_ablations: bool = False,
        ablation_scenarios_count: int = 4,
    ) -> dict[str, int]:
        """Estimate external API call counts before running live.

        Returns a dict with:
            logical_trials:             total distinct LLM trial invocations
            normal_provider_calls:      expected calls if no retries occur (== logical_trials)
            max_provider_attempts:      worst-case attempts (logical_trials × max_attempts)
            core_scenarios, core_trials, ablation_trials, total_trials
        """
        core = n_scenarios * self.repetitions
        ablations = 0
        if include_ablations:
            ablations = ablation_scenarios_count * 2 * self.repetitions
        total = core + ablations
        max_attempts = getattr(self.provider, "max_attempts", 1)
        return {
            "core_scenarios": n_scenarios,
            "repetitions_per_scenario": self.repetitions,
            "core_trials": core,
            "ablation_trials": ablations,
            "logical_trials": total,
            "normal_provider_calls": total,
            "max_provider_attempts": total * max_attempts,
            # Legacy keys for backward compat
            "core_calls": core,
            "ablation_calls": ablations,
            "total_calls": total,
        }

    def write_manifest(
        self,
        variants: list[BenchmarkScenarioVariant],
        base_sha256: str,
        *,
        run_type: str | None = None,
        preregistered: bool | None = None,
    ) -> BenchmarkManifest:
        """Write manifest.json BEFORE live trial execution."""
        git_sha, git_dirty = _get_git_commit()
        effective_run_type = run_type or self.run_type

        # Determine preregistration status
        is_preregistered = len(self.config_overrides) == 0
        if preregistered is not None:
            is_preregistered = preregistered

        # Config provenance
        config_id = ""
        config_sha = ""
        if self.benchmark_config:
            config_id = self.benchmark_config.benchmark_version
            config_sha = self.benchmark_config.config_sha256

        actual_model = self.provider.model_id

        manifest = BenchmarkManifest(
            benchmark_version=BENCHMARK_VERSION,
            run_id=self.run_id,
            benchmark_run_id=self.run_id,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            started_at=datetime.now(timezone.utc).isoformat(),
            run_status="started",
            run_type=effective_run_type,
            git_commit_sha=git_sha,
            git_dirty=git_dirty,
            base_scenario_sha256=base_sha256,
            provider=self.provider.provider_name,
            model=actual_model,
            actual_model_id=actual_model,
            generation_config={
                "decoding_method": "greedy",
                "max_new_tokens": 2048,
                "stop_sequences": ["<|user|>"],
            },
            candidate_limit=self.candidate_limit,
            scenario_matrix=[v.spec.scenario_id for v in variants],
            repetitions=self.repetitions,
            retry_policy={
                "max_attempts": getattr(self.provider, "max_attempts", 1),
                "delay_between_attempts_s": getattr(self.provider, "delay_s", 0.0),
            },
            primary_metrics=PRIMARY_METRICS,
            comparison_tolerance=COMPARISON_TOLERANCE,
            python_version=sys.version,
            platform=platform.platform(),
            config_id=config_id,
            config_sha256=config_sha,
            preregistered=is_preregistered,
            config_overrides=self.config_overrides,
            # Executed values
            executed_capacity_ratios=sorted({v.spec.capacity_ratio for v in variants}),
            executed_anomaly_modes=sorted({v.spec.anomaly_mode.value for v in variants}),
            executed_deadline_scales=sorted({v.spec.deadline_scale for v in variants}),
            executed_candidate_limit=self.candidate_limit,
            executed_repetitions=self.repetitions,
            executed_retry_policy={
                "max_attempts": getattr(self.provider, "max_attempts", 1),
                "delay_between_attempts_s": getattr(self.provider, "delay_s", 0.0),
            },
        )
        if self.output_dir:
            manifest_path = self.output_dir / "manifest.json"
            manifest_path.write_text(manifest.model_dump_json(indent=2))
        return manifest

    def finalize_manifest(self, manifest: BenchmarkManifest, *, status: str = "completed") -> None:
        """Update manifest with completion metadata."""
        if self.output_dir is None:
            return
        manifest_path = self.output_dir / "manifest.json"
        if not manifest_path.exists():
            return
        # Read and update
        data = json.loads(manifest_path.read_text())
        data["completed_at"] = datetime.now(timezone.utc).isoformat()
        data["run_status"] = status
        manifest_path.write_text(json.dumps(data, indent=2))

    def write_effective_config(
        self,
        benchmark_config: BenchmarkConfig | None = None,
        *,
        variants: list | None = None,
        suite: str = "core",
        run_type: str | None = None,
        preregistered: bool | None = None,
    ) -> dict:
        """Write effective_config.json to the result directory.

        The effective config captures what was ACTUALLY EXECUTED — not merely the
        source config intent.  This includes the actual scenario IDs, suite name,
        run_type, model, repetitions, and all other execution parameters.

        The effective_config_sha256 is computed over the executed snapshot
        (excluding the hash field itself) using canonical sorted JSON serialization.
        Same effective execution → same SHA.  Different scenario subset → different SHA.

        Args:
            benchmark_config: Source benchmark config (for source_config_sha256).
            variants:         Actually-executed BenchmarkScenarioVariant list.
                              Used to record exact scenario IDs and capacity/anomaly/deadline info.
            suite:            Suite name ("quick", "core", "full") — recorded explicitly.
            run_type:         Override for run_type label.
            preregistered:    Override for preregistered flag.

        Returns:
            The effective config dict (also written to disk if output_dir is set).
        """
        cfg = benchmark_config or self.benchmark_config
        effective_run_type = run_type or self.run_type
        is_preregistered = preregistered if preregistered is not None else len(self.config_overrides) == 0

        # Executed scenario IDs and matrices (from actual variants if provided)
        if variants is not None:
            executed_scenario_ids = [v.spec.scenario_id for v in variants]
            executed_capacity_ratios = sorted({v.spec.capacity_ratio for v in variants})
            executed_anomaly_modes = sorted({v.spec.anomaly_mode.value for v in variants})
            executed_deadline_scales = sorted({v.spec.deadline_scale for v in variants})
        else:
            executed_scenario_ids = []
            executed_capacity_ratios = cfg.capacity_ratios if cfg else []
            executed_anomaly_modes = cfg.anomaly_modes if cfg else []
            executed_deadline_scales = cfg.deadline_modes if cfg else [1.0]

        effective: dict = {
            "suite": suite,
            "run_type": effective_run_type,
            "preregistered": is_preregistered,
            "executed_scenario_ids": executed_scenario_ids,
            "executed_values": {
                "capacity_ratios": executed_capacity_ratios,
                "anomaly_modes": executed_anomaly_modes,
                "deadline_scales": executed_deadline_scales,
                "repetitions": self.repetitions,
                "candidate_limit": self.candidate_limit,
                "provider": self.provider.provider_name,
                "model": self.provider.model_id,
                "retry_policy": {
                    "max_attempts": getattr(self.provider, "max_attempts", 1),
                    "delay_between_attempts_s": getattr(self.provider, "delay_s", 0.0),
                },
            },
            "config_overrides": self.config_overrides,
        }

        # Record source config provenance if available
        if cfg is not None:
            effective["source_config_version"] = cfg.benchmark_version
            effective["source_config_sha256"] = cfg.config_sha256
            effective["executed_values"]["comparison_tolerance"] = cfg.comparison_tolerance
            effective["executed_values"]["primary_metrics"] = cfg.primary_metrics
            effective["executed_values"]["ablation_scenarios"] = cfg.ablation_configuration.ablation_scenarios

        # Record scenario_matrix override explicitly when suite != core
        if cfg is not None and variants is not None:
            configured_count = len(cfg.capacity_ratios) * len(cfg.anomaly_modes) * len(cfg.deadline_modes)
            if len(executed_scenario_ids) != configured_count or suite != "core":
                effective["config_overrides"] = dict(self.config_overrides)
                effective["config_overrides"]["scenario_matrix"] = {
                    "configured_count": configured_count,
                    "executed": executed_scenario_ids,
                }

        # Compute effective_config_sha256 over the snapshot (excluding the hash field)
        effective_sha = hashlib.sha256(
            json.dumps(effective, indent=2, sort_keys=True).encode()
        ).hexdigest()
        effective["effective_config_sha256"] = effective_sha

        if self.output_dir is not None:
            (self.output_dir / "effective_config.json").write_text(
                json.dumps(effective, indent=2), encoding="utf-8"
            )
        return effective

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_single_repetition(
        self,
        scenario: Scenario,
        spec: ScenarioVariantSpec,
        link_state,
        candidates: list[CandidateSummary],
        llm_anomalies: list,
        candidate_ids: list[str],
        candidate_count: int,
        det_plans: dict,
        repetition: int,
        experiment_variant: ExperimentVariant,
        ablation_variant: str | None,
    ) -> BenchmarkTrial:
        """Execute one LLM repetition and return trial record."""

        # Build unique trial ID
        trial_id = f"{self.run_id}_{spec.scenario_id}_rep{repetition:02d}"
        if ablation_variant:
            trial_id += f"_abl_{ablation_variant}"

        if self.dry_run:
            # Dry run — no external call
            return self._make_trial(
                trial_id=trial_id,
                spec=spec,
                repetition=repetition,
                candidates=candidates,
                candidate_ids=candidate_ids,
                candidate_count=candidate_count,
                det_plans=det_plans,
                scenario=scenario,
                link_state=link_state,
                status=BenchmarkStatus.SKIPPED,
                provider_result=None,
                experiment_variant=experiment_variant,
                ablation_variant=ablation_variant,
            )

        # Live provider call
        status = BenchmarkStatus.SUCCESS
        error_type: str | None = None
        error_msg: str | None = None
        provider_result: BenchmarkProviderResult | None = None

        try:
            provider_result = self.provider.prioritize(
                candidates, link_state, scenario.mission_state, llm_anomalies,
                distance_km=scenario.distance_km,
            )
        except BenchmarkProviderFailure as failure:
            # Structured failure — full provenance carried by the exception.
            # Use the status hint from the provider (parse_error, invalid_response, etc.)
            status = failure.status_hint
            error_type = type(failure.cause).__name__
            error_msg = _sanitize_error(str(failure.cause))
            # Convert to BenchmarkProviderResult — no guessing, authoritative metadata.
            provider_result = failure.to_provider_result()
        except Exception as exc:  # noqa: BLE001
            # Non-BenchmarkProviderFailure exceptions (e.g. from FakeProvider callbacks)
            status = _classify_error(exc)
            error_type = type(exc).__name__
            error_msg = _sanitize_error(str(exc))
            provider_result = BenchmarkProviderResult(
                prioritization=None,
                attempt_count=1,
                raw_response="",
                raw_response_sha256="",
            )

        return self._make_trial(
            trial_id=trial_id,
            spec=spec,
            repetition=repetition,
            candidates=candidates,
            candidate_ids=candidate_ids,
            candidate_count=candidate_count,
            det_plans=det_plans,
            scenario=scenario,
            link_state=link_state,
            status=status,
            provider_result=provider_result,
            error_type=error_type,
            error_msg=error_msg,
            experiment_variant=experiment_variant,
            ablation_variant=ablation_variant,
        )

    def _make_trial(
        self,
        trial_id: str,
        spec: ScenarioVariantSpec,
        repetition: int,
        candidates: list[CandidateSummary],
        candidate_ids: list[str],
        candidate_count: int,
        det_plans: dict,
        scenario: Scenario,
        link_state,
        status: BenchmarkStatus,
        provider_result: BenchmarkProviderResult | None,
        error_type: str | None = None,
        error_msg: str | None = None,
        experiment_variant: ExperimentVariant = ExperimentVariant.FULL,
        ablation_variant: str | None = None,
    ) -> BenchmarkTrial:
        """Build BenchmarkTrial and all associated BenchmarkPlanResult objects."""
        pr = provider_result or BenchmarkProviderResult()

        prioritization = pr.prioritization
        ranked_ids = (
            [rp.product_id for rp in prioritization.ranked_products]
            if prioritization is not None
            else None
        )

        ranked_count = len(ranked_ids) if ranked_ids else 0
        unranked_count = candidate_count - ranked_count
        ranking_hash_val = _ranking_hash(ranked_ids) if ranked_ids else ""

        plan_results: list[BenchmarkPlanResult] = []
        plan_ids: list[str] = []

        # Collect deterministic plan results
        for pt, (plan, ev, mo) in det_plans.items():
            plan_ids.append(plan.plan_id)
            bpr = BenchmarkPlanResult(
                trial_id=trial_id,
                run_id=trial_id,
                scenario_id=spec.scenario_id,
                repetition=repetition,
                experiment_variant=experiment_variant,
                plan_type=pt,
                plan_order_hash=_plan_order_hash(plan),
                physical_metrics=_physical_metrics(ev, mo),
                mission_outcome_metrics=_mission_metrics(mo),
            )
            plan_results.append(bpr)

        # Build AI plan only if provider succeeded
        final_status = status
        if status == BenchmarkStatus.SUCCESS and prioritization is not None:
            all_packets = data_products_to_packets(scenario.data_products)
            try:
                ai_plan = build_ai_prioritized_plan(
                    all_packets, prioritization, link_state, scenario.mission_state, self._weights,
                )
                try:
                    ev_ai, mo_ai = _evaluate_plan(
                        ai_plan, link_state, scenario.mission_state,
                        scenario.data_products, scenario.anomalies
                    )
                except Exception as eval_exc:  # noqa: BLE001
                    logger.error("AI plan evaluation failed for %s rep %d: %s", spec.scenario_id, repetition, eval_exc)
                    final_status = BenchmarkStatus.EVALUATION_ERROR
                    if error_type is None:
                        error_type = type(eval_exc).__name__
                    if error_msg is None:
                        error_msg = _sanitize_error(str(eval_exc))
                else:
                    plan_ids.append(ai_plan.plan_id)
                    pt_ai = (
                        PlanType.AI_NO_DESCRIPTION if experiment_variant == ExperimentVariant.NO_DESCRIPTION
                        else PlanType.AI_NO_ANOMALY_CONTEXT if experiment_variant == ExperimentVariant.NO_ANOMALY_CONTEXT
                        else PlanType.AI_PRIORITIZED
                    )
                    bpr_ai = BenchmarkPlanResult(
                        trial_id=trial_id,
                        run_id=trial_id,
                        scenario_id=spec.scenario_id,
                        repetition=repetition,
                        experiment_variant=experiment_variant,
                        plan_type=pt_ai,
                        plan_order_hash=_plan_order_hash(ai_plan),
                        physical_metrics=_physical_metrics(ev_ai, mo_ai),
                        mission_outcome_metrics=_mission_metrics(mo_ai),
                    )
                    plan_results.append(bpr_ai)

            except Exception as build_exc:  # noqa: BLE001
                logger.error("AI plan construction failed for %s rep %d: %s", spec.scenario_id, repetition, build_exc)
                final_status = BenchmarkStatus.PLAN_BUILD_ERROR
                if error_type is None:
                    error_type = type(build_exc).__name__
                if error_msg is None:
                    error_msg = _sanitize_error(str(build_exc))

        # Persist plan results
        if self._result_path:
            for bpr in plan_results:
                self._append_plan_result(bpr)

        # Save audit files if requested
        if self.save_prompts and self.output_dir and pr.actual_user_message:
            audit_dir = self.output_dir / "audit"
            _write_audit_files(
                audit_dir,
                trial_id,
                pr.actual_system_prompt,
                pr.actual_user_message,
                pr.raw_response,
            )

        trial = BenchmarkTrial(
            trial_id=trial_id,
            run_id=self.run_id,
            benchmark_run_id=self.run_id,
            benchmark_version=BENCHMARK_VERSION,
            scenario_id=spec.scenario_id,
            scenario_variant=spec,
            provider=self.provider.provider_name,
            model=pr.actual_model_id or self.provider.model_id,
            repetition=repetition,
            status=final_status,
            error_type=error_type,
            error_message_sanitized=error_msg,
            capacity_ratio=spec.capacity_ratio,
            actual_capacity_ratio=spec.actual_capacity_ratio,
            candidate_ids=candidate_ids,
            candidate_count=candidate_count,
            ranked_count=ranked_count,
            unranked_count=unranked_count,
            ranking=ranked_ids or [],
            ranking_hash=ranking_hash_val,
            provider_latency_ms=pr.provider_latency_ms_total or None,
            provider_latency_ms_total=pr.provider_latency_ms_total or None,
            attempt_count=pr.attempt_count,
            attempt_latencies_ms=pr.attempt_latencies_ms,
            prompt_system_sha256=pr.actual_system_sha256,
            prompt_user_sha256=pr.actual_user_sha256,
            response_hash=ranking_hash_val,          # backward compat alias
            raw_response_sha256=pr.raw_response_sha256,
            ranking_hash_separate=ranking_hash_val,
            actual_model_id=pr.actual_model_id or self.provider.model_id,
            generation_config=pr.generation_config,
            experiment_variant=experiment_variant,
            ablation_variant=ablation_variant,
            plan_ids=plan_ids,
        )
        return trial

    def _append_trial(self, trial: BenchmarkTrial) -> None:
        """Append one trial as a JSON line to raw_results.jsonl."""
        if self._result_path is None:
            return
        with self._result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"record_type": "trial", **trial.model_dump(mode="json")}) + "\n")

    def _append_plan_result(self, pr: BenchmarkPlanResult) -> None:
        """Append one plan result as a JSON line to raw_results.jsonl."""
        if self._result_path is None:
            return
        with self._result_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"record_type": "plan_result", **pr.model_dump(mode="json")}) + "\n")


# ---------------------------------------------------------------------------
# Error classification helpers
# ---------------------------------------------------------------------------


def _classify_error(exc: Exception) -> BenchmarkStatus:
    """Classify provider exception to BenchmarkStatus."""
    from ..agent.granite_agent import GraniteAPIError, GraniteResponseError

    if isinstance(exc, GraniteResponseError):
        exc_name = type(exc).__name__.lower()
        msg = str(exc).lower()
        if "parse" in exc_name or "json" in msg or "not valid json" in msg:
            return BenchmarkStatus.PARSE_ERROR
        if "schema" in exc_name or "schema" in msg or "validation" in msg or "missing field" in msg:
            return BenchmarkStatus.SCHEMA_ERROR
        if "invalid" in msg or "unknown product" in msg or "unknown plan" in msg or "duplicate" in msg:
            return BenchmarkStatus.INVALID_RESPONSE
        return BenchmarkStatus.INVALID_RESPONSE

    if isinstance(exc, GraniteAPIError):
        msg = str(exc).lower()
        if "timeout" in msg:
            return BenchmarkStatus.TIMEOUT
        return BenchmarkStatus.PROVIDER_ERROR

    exc_name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in exc_name or "timeout" in msg:
        return BenchmarkStatus.TIMEOUT
    if "parse" in exc_name or "json" in msg:
        return BenchmarkStatus.PARSE_ERROR
    if "schema" in exc_name:
        return BenchmarkStatus.SCHEMA_ERROR
    if "invalid" in msg or "validation" in msg:
        return BenchmarkStatus.INVALID_RESPONSE
    return BenchmarkStatus.PROVIDER_ERROR
