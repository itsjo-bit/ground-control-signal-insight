"""Benchmark runner — executes the benchmark experiment matrix.

Strict provider mode:
    In benchmark mode, Granite failure → record GRANITE FAILURE.
    No Local fallback is substituted.  No alternate provider.
    A failed run is part of provider reliability statistics.

Retry policy:
    Documented fixed retry: max_attempts = 2 (configurable).
    A failed API transport failure may be retried once.
    A valid model output (even a poor plan) is accepted as the final result.
    Results are NEVER retried because of poor metric performance.

Isolation:
    Benchmark execution NEVER modifies global FastAPI state, transmission
    history, operator approval state, or frontend data.
    All scenario loading and plan generation uses pure local objects.

Deterministic controls:
    The four classical plans and the semantic-rule plan are computed ONCE
    per scenario and reused for all Granite repetitions.
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
import platform
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
    BenchmarkManifest,
    BenchmarkPlanResult,
    BenchmarkStatus,
    BenchmarkTrial,
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
# Provider protocol (strict benchmark mode — no fallback)
# ---------------------------------------------------------------------------


class BenchmarkProvider:
    """Minimal provider interface for benchmark mode.

    Strict mode: if the external call fails, raise — do NOT fall back to Local.
    The caller records the failure status and continues.
    """

    def prioritize(
        self,
        candidates: list[CandidateSummary],
        link_state,
        mission_state,
        anomalies: list,
        distance_km: Optional[float] = None,
    ):
        """Return CandidatePrioritization or raise on failure."""
        raise NotImplementedError


class GraniteBenchmarkProvider(BenchmarkProvider):
    """IBM Granite provider in strict benchmark mode (no Local fallback).

    Args:
        max_attempts: Maximum retry attempts for transient transport failures.
        agent:        Optional pre-built GraniteAgent (for testing / injection).
                      If None, a default GraniteAgent() is constructed on first call.
    """

    def __init__(self, max_attempts: int = 2, agent=None) -> None:
        self.max_attempts = max_attempts
        self.provider_name = "Granite"
        self._agent = agent  # None until first call unless injected

    def prioritize(self, candidates, link_state, mission_state, anomalies, distance_km=None):
        from ..agent.granite_agent import GraniteAgent, GraniteAPIError, GraniteResponseError
        if self._agent is None:
            self._agent = GraniteAgent()
        agent = self._agent
        last_exc: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return agent.prioritize_candidates(
                    candidates, link_state, mission_state, anomalies,
                    distance_km=distance_km,
                )
            except (GraniteAPIError, GraniteResponseError) as exc:
                last_exc = exc
                logger.warning("Granite attempt %d/%d failed: %s", attempt, self.max_attempts, exc)
                if attempt < self.max_attempts:
                    time.sleep(0.1)  # brief pause before retry (reduced for benchmark)
        raise last_exc  # type: ignore[misc]


class FakeProvider(BenchmarkProvider):
    """Test/dry-run provider that returns a fixed prioritization from a callback."""

    def __init__(self, callback: Callable) -> None:
        self.provider_name = "fake"
        self._cb = callback

    def prioritize(self, candidates, link_state, mission_state, anomalies, distance_km=None):
        return self._cb(candidates, link_state, mission_state, anomalies)


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


def _get_git_commit() -> str:
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


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
# Main trial runner
# ---------------------------------------------------------------------------


class BenchmarkRunner:
    """Execute benchmark trials for a scenario variant.

    Args:
        provider:       BenchmarkProvider implementing strict mode (no fallback).
        repetitions:    Number of LLM repetitions per scenario.
        candidate_limit: Max candidates to pass to the LLM.
        dry_run:        If True, skip all external provider calls.
        save_prompts:   If True, save sanitized prompt content to output_dir.
        output_dir:     Where to write results (raw_results.jsonl, manifest.json, etc.).
        run_id:         Stable run identifier (auto-generated if None).
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
    ) -> None:
        self.provider = provider
        self.repetitions = repetitions
        self.candidate_limit = candidate_limit
        self.dry_run = dry_run
        self.save_prompts = save_prompts
        self.output_dir = output_dir
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

        # Ablation input modification (Stage-1 input only)
        llm_candidates = candidates
        llm_anomalies = list(scenario.anomalies)
        if ablation_variant == "no_description":
            llm_candidates = [
                cs.model_copy(update={"description": ""}) for cs in candidates
            ]
        elif ablation_variant == "no_anomaly_context":
            llm_anomalies = []  # Remove active_anomalies from LLM context

        # Build system prompt hash for reproducibility
        try:
            from ..agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
            sys_prompt_hash = _sha256_hex(_PRIORITIZATION_SYSTEM_PROMPT)
        except ImportError:
            sys_prompt_hash = "unknown"

        trials: list[BenchmarkTrial] = []

        for rep in range(1, self.repetitions + 1):
            logger.info(
                "Running %s rep %d/%d (ablation=%s)",
                spec.scenario_id, rep, self.repetitions, ablation_variant
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
                sys_prompt_hash=sys_prompt_hash,
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
                                Defaults to canonical 4-scenario subset.
        """
        all_trials: list[BenchmarkTrial] = []

        # Core runs
        for variant in variants:
            trials = self.run_variant(variant)
            all_trials.extend(trials)

        # Ablation runs
        if include_ablations:
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
        """Estimate external API calls before running live.

        Returns:
            Dict with core_calls, ablation_calls, total_calls.
        """
        core = n_scenarios * self.repetitions
        ablations = 0
        if include_ablations:
            ablations = ablation_scenarios_count * 2 * self.repetitions
        return {
            "core_scenarios": n_scenarios,
            "repetitions_per_scenario": self.repetitions,
            "core_calls": core,
            "ablation_calls": ablations,
            "total_calls": core + ablations,
        }

    def write_manifest(
        self,
        variants: list[BenchmarkScenarioVariant],
        base_sha256: str,
    ) -> BenchmarkManifest:
        manifest = BenchmarkManifest(
            benchmark_version=BENCHMARK_VERSION,
            run_id=self.run_id,
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            git_commit_sha=_get_git_commit(),
            base_scenario_sha256=base_sha256,
            provider=self.provider.provider_name,
            model=getattr(self.provider, "_model_id", "unknown"),
            candidate_limit=self.candidate_limit,
            scenario_matrix=[v.spec.scenario_id for v in variants],
            repetitions=self.repetitions,
            retry_policy={"max_attempts": getattr(self.provider, "max_attempts", 1)},
            primary_metrics=PRIMARY_METRICS,
            comparison_tolerance=COMPARISON_TOLERANCE,
            python_version=sys.version,
            platform=platform.platform(),
        )
        if self.output_dir:
            manifest_path = self.output_dir / "manifest.json"
            manifest_path.write_text(manifest.model_dump_json(indent=2))
        return manifest

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
        sys_prompt_hash: str,
        ablation_variant: str | None,
    ) -> BenchmarkTrial:
        """Execute one LLM repetition and return trial record."""
        from ..agent.prioritization_helpers import build_prioritization_message

        # Build user context for hash
        user_msg = build_prioritization_message(
            candidates, link_state, scenario.mission_state, llm_anomalies
        )
        user_prompt_hash = _sha256_hex(user_msg)

        if self.dry_run:
            # Dry run — no external call
            return self._make_trial(
                spec=spec, repetition=repetition, candidates=candidates,
                candidate_ids=candidate_ids, candidate_count=candidate_count,
                det_plans=det_plans, scenario=scenario, link_state=link_state,
                status=BenchmarkStatus.SKIPPED, ranked_ids=None,
                response_hash="", provider_latency_ms=None, attempt_count=0,
                sys_prompt_hash=sys_prompt_hash, user_prompt_hash=user_prompt_hash,
                ablation_variant=ablation_variant,
            )

        # Live provider call
        start = time.monotonic()
        status = BenchmarkStatus.SUCCESS
        error_type: str | None = None
        error_msg: str | None = None
        prioritization = None
        response_hash = ""
        attempt_count = 1

        try:
            prioritization = self.provider.prioritize(
                candidates, link_state, scenario.mission_state, llm_anomalies,
                distance_km=scenario.distance_km,
            )
            # Hash the raw ranking for reproducibility
            ranked_ids = [rp.product_id for rp in prioritization.ranked_products]
            response_hash = _sha256_hex(json.dumps(ranked_ids, separators=(",", ":")))
        except Exception as exc:  # noqa: BLE001
            status = _classify_error(exc)
            error_type = type(exc).__name__
            error_msg = _sanitize_error(str(exc))
            ranked_ids = None
            attempt_count = getattr(self.provider, "max_attempts", 1)

        latency_ms = (time.monotonic() - start) * 1000.0

        return self._make_trial(
            spec=spec, repetition=repetition, candidates=candidates,
            candidate_ids=candidate_ids, candidate_count=candidate_count,
            det_plans=det_plans, scenario=scenario, link_state=link_state,
            status=status, ranked_ids=ranked_ids, prioritization=prioritization,
            response_hash=response_hash, provider_latency_ms=latency_ms,
            attempt_count=attempt_count, error_type=error_type, error_msg=error_msg,
            sys_prompt_hash=sys_prompt_hash, user_prompt_hash=user_prompt_hash,
            ablation_variant=ablation_variant,
        )

    def _make_trial(
        self,
        spec: ScenarioVariantSpec,
        repetition: int,
        candidates: list[CandidateSummary],
        candidate_ids: list[str],
        candidate_count: int,
        det_plans: dict,
        scenario: Scenario,
        link_state,
        status: BenchmarkStatus,
        ranked_ids: list[str] | None,
        prioritization=None,
        response_hash: str = "",
        provider_latency_ms: float | None = None,
        attempt_count: int = 1,
        error_type: str | None = None,
        error_msg: str | None = None,
        sys_prompt_hash: str = "",
        user_prompt_hash: str = "",
        ablation_variant: str | None = None,
    ) -> BenchmarkTrial:
        """Build BenchmarkTrial and all associated BenchmarkPlanResult objects."""
        trial_id = f"{self.run_id}_{spec.scenario_id}_rep{repetition:02d}"
        if ablation_variant:
            trial_id += f"_abl_{ablation_variant}"

        ranked_count = len(ranked_ids) if ranked_ids else 0
        unranked_count = candidate_count - ranked_count
        ranking_hash_val = _ranking_hash(ranked_ids) if ranked_ids else ""

        plan_results: list[BenchmarkPlanResult] = []
        plan_ids: list[str] = []

        # Collect deterministic plan results
        for pt, (plan, ev, mo) in det_plans.items():
            plan_ids.append(plan.plan_id)
            pr = BenchmarkPlanResult(
                run_id=trial_id,
                scenario_id=spec.scenario_id,
                repetition=repetition,
                plan_type=pt,
                plan_order_hash=_plan_order_hash(plan),
                physical_metrics=_physical_metrics(ev, mo),
                mission_outcome_metrics=_mission_metrics(mo),
            )
            plan_results.append(pr)

        # Build AI plan if ranking succeeded
        if status == BenchmarkStatus.SUCCESS and prioritization is not None:
            all_packets = data_products_to_packets(scenario.data_products)
            try:
                ai_plan = build_ai_prioritized_plan(
                    all_packets, prioritization, link_state, scenario.mission_state, self._weights,
                )
                ev_ai, mo_ai = _evaluate_plan(
                    ai_plan, link_state, scenario.mission_state,
                    scenario.data_products, scenario.anomalies
                )
                plan_ids.append(ai_plan.plan_id)
                pt_ai = (
                    PlanType.AI_NO_DESCRIPTION if ablation_variant == "no_description"
                    else PlanType.AI_NO_ANOMALY_CONTEXT if ablation_variant == "no_anomaly_context"
                    else PlanType.AI_PRIORITIZED
                )
                pr_ai = BenchmarkPlanResult(
                    run_id=trial_id,
                    scenario_id=spec.scenario_id,
                    repetition=repetition,
                    plan_type=pt_ai,
                    plan_order_hash=_plan_order_hash(ai_plan),
                    physical_metrics=_physical_metrics(ev_ai, mo_ai),
                    mission_outcome_metrics=_mission_metrics(mo_ai),
                )
                plan_results.append(pr_ai)
            except Exception as exc:  # noqa: BLE001
                logger.error("AI plan construction failed: %s", exc)

        # Persist plan results
        if self._result_path:
            for pr in plan_results:
                self._append_plan_result(pr)

        trial = BenchmarkTrial(
            run_id=trial_id,
            benchmark_version=BENCHMARK_VERSION,
            scenario_id=spec.scenario_id,
            scenario_variant=spec,
            provider=self.provider.provider_name,
            model=getattr(self.provider, "_model_id", "unknown"),
            repetition=repetition,
            status=status,
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
            provider_latency_ms=provider_latency_ms,
            attempt_count=attempt_count,
            prompt_system_sha256=sys_prompt_hash,
            prompt_user_sha256=user_prompt_hash,
            response_hash=response_hash,
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
    exc_name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in exc_name or "timeout" in msg:
        return BenchmarkStatus.TIMEOUT
    if "api" in exc_name or "http" in msg or "connection" in msg:
        return BenchmarkStatus.PROVIDER_ERROR
    if "parse" in exc_name or "json" in msg or "response" in exc_name:
        return BenchmarkStatus.PARSE_ERROR
    if "invalid" in msg or "schema" in msg or "validation" in msg:
        return BenchmarkStatus.INVALID_RESPONSE
    return BenchmarkStatus.PROVIDER_ERROR


def _sanitize_error(msg: str) -> str:
    """Remove potential secrets from error messages."""
    # Remove anything that looks like a token or key
    import re
    msg = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", msg)
    msg = re.sub(r"apikey[=:]\s*\S+", "apikey=[REDACTED]", msg, flags=re.IGNORECASE)
    return msg[:500]  # cap length
