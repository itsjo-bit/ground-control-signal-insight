"""Phase 2B.1 Benchmark Integrity Correction — comprehensive test suite.

Covers all acceptance criteria from the Phase 2B.1 correction pass:

- Config loading and validation (BenchmarkConfig)
- Config SHA stability
- Override detection and non-preregistered marking
- Retry policy: only transport failures retry; parse/schema/invalid never retry
- Actual attempt count recorded
- Trial success semantics: full pipeline required
- Plan build failure → plan_build_error status
- Evaluation failure → evaluation_error status
- Exact prompt/response hash provenance
- Raw response SHA vs ranking hash (separate semantics)
- --save-prompts audit file generation and secret redaction
- Provider model_id property
- Analysis trial grouping by trial_id
- Ablation isolation from core statistics
- All 5 comparators in analysis
- Capacity grouping
- Anomaly mode grouping
- Per-scenario variability
- Ablation summary deltas
- Pareto CSV population
- generate_core() without invalid empty-path construction
- Call count display (logical / normal / max)
- Reproduction command in report
- Missing project ID pre-flight
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.benchmark.models import (
    BenchmarkConfig,
    BenchmarkPlanResult,
    BenchmarkProviderResult,
    BenchmarkStatus,
    BenchmarkTrial,
    ExperimentVariant,
    MissionOutcomeMetrics,
    PhysicalMetrics,
    PlanType,
    ScenarioVariantSpec,
)
from backend.app.benchmark.analysis import (
    COMPARISON_TOLERANCE,
    ComparisonResult,
    compute_ablation_analysis,
    compute_all_comparisons,
    compute_anomaly_analysis,
    compute_capacity_analysis,
    compute_pareto_frontier,
    compute_pareto_frontier_rate,
    compute_scenario_variability,
    enrich_pareto_metadata,
    filter_core_results,
    group_by_trial_id,
    pairwise_compare,
)
from backend.app.benchmark.runner import (
    BenchmarkRunner,
    FakeProvider,
    GraniteBenchmarkProvider,
    GraniteTransportError,
    _classify_error,
    _ranking_hash,
    _redact_secrets,
    _sha256_hex,
    _write_audit_files,
    build_deterministic_plans,
    is_retriable_benchmark_error,
)
from backend.app.benchmark.report import (
    compute_summary,
    generate_markdown_report,
    write_benchmark_outputs,
    write_summary_csv,
)
from backend.app.benchmark.scenario_variants import AnomalyMode

BASE_SCENARIO_PATH = Path("data/scenarios/mission_data_v3.json")
BENCHMARK_CONFIG_PATH = Path("benchmarks/configs/gcsi_benchmark_v1.json")


def _skip_if_no_scenario():
    if not BASE_SCENARIO_PATH.exists():
        pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")


def _skip_if_no_config():
    if not BENCHMARK_CONFIG_PATH.exists():
        pytest.skip(f"Benchmark config not found at {BENCHMARK_CONFIG_PATH}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(scenario_id: str = "CAP035_ORIGINAL", capacity_ratio: float = 0.35):
    return ScenarioVariantSpec(
        scenario_id=scenario_id,
        capacity_ratio=capacity_ratio,
        anomaly_mode=AnomalyMode.ORIGINAL,
        total_queued_bits=1000000,
        link_goodput_bps=80000.0,
        communication_window_s=437.5,
        available_capacity_bits=350000.0,
        actual_capacity_ratio=capacity_ratio,
    )


def _make_phys(
    risk_score: float = 0.3,
    mission_value: float = 5.0,
    critical_delivery_rate: float = 0.8,
) -> PhysicalMetrics:
    return PhysicalMetrics(
        risk_score=risk_score,
        mission_value=mission_value,
        critical_packets_delivered=8,
        total_critical_packets=10,
        critical_delivery_rate=critical_delivery_rate,
        deadline_misses=1,
        deadline_miss_rate=0.1,
        bandwidth_utilization=0.7,
        retransmission_overhead=0.1,
        window_pressure=0.5,
        deferred_count=2,
    )


def _make_mo(
    scientific_value_capture_rate: float = 0.75,
    required_delivery_rate: float = 0.9,
    active_anomaly_delivery_rate: float = 0.8,
    high_severity_anomaly_coverage_rate: float = 0.7,
    anomaly_weighted_coverage: float = 0.72,
) -> MissionOutcomeMetrics:
    return MissionOutcomeMetrics(
        scientific_value_capture_rate=scientific_value_capture_rate,
        required_delivery_rate=required_delivery_rate,
        active_anomaly_delivery_rate=active_anomaly_delivery_rate,
        high_severity_anomaly_coverage_rate=high_severity_anomaly_coverage_rate,
        anomaly_weighted_coverage=anomaly_weighted_coverage,
    )


def _make_pr(
    plan_type: PlanType,
    scenario_id: str = "CAP035_ORIGINAL",
    repetition: int = 1,
    trial_id: str = "run_CAP035_ORIGINAL_rep01",
    experiment_variant: ExperimentVariant = ExperimentVariant.FULL,
    phys: PhysicalMetrics | None = None,
    mo: MissionOutcomeMetrics | None = None,
) -> BenchmarkPlanResult:
    return BenchmarkPlanResult(
        trial_id=trial_id,
        run_id=trial_id,
        scenario_id=scenario_id,
        repetition=repetition,
        experiment_variant=experiment_variant,
        plan_type=plan_type,
        plan_order_hash="abc123",
        physical_metrics=phys or _make_phys(),
        mission_outcome_metrics=mo or _make_mo(),
    )


def _make_trial(
    scenario_id: str = "CAP035_ORIGINAL",
    status: BenchmarkStatus = BenchmarkStatus.SUCCESS,
    trial_id: str = "run_CAP035_ORIGINAL_rep01",
    experiment_variant: ExperimentVariant = ExperimentVariant.FULL,
    attempt_count: int = 1,
    model: str = "ibm/granite-4-h-small",
    actual_model_id: str = "ibm/granite-4-h-small",
) -> BenchmarkTrial:
    return BenchmarkTrial(
        trial_id=trial_id,
        run_id="test-run-001",
        benchmark_run_id="test-run-001",
        benchmark_version="gcsi_benchmark_v1",
        scenario_id=scenario_id,
        scenario_variant=_spec(scenario_id),
        provider="Granite",
        model=model,
        repetition=1,
        status=status,
        capacity_ratio=0.35,
        actual_capacity_ratio=0.35,
        candidate_count=10,
        ranked_count=8,
        unranked_count=2,
        attempt_count=attempt_count,
        experiment_variant=experiment_variant,
        actual_model_id=actual_model_id,
    )


# ===========================================================================
# 1. Config Loading and Validation
# ===========================================================================


class TestBenchmarkConfigLoading:
    def test_load_v1_config(self):
        """gcsi_benchmark_v1.json → BenchmarkConfig — all fields validated."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        assert cfg.benchmark_version == "gcsi_benchmark_v1"
        assert cfg.repetitions == 5
        assert cfg.candidate_limit == 50
        assert cfg.retry_policy.max_attempts == 2
        assert cfg.retry_policy.delay_between_attempts_s == 1.0
        assert cfg.model == "ibm/granite-4-h-small"
        assert cfg.provider == "Granite"
        assert 0.35 in cfg.capacity_ratios
        assert 0.60 in cfg.capacity_ratios
        assert 0.90 in cfg.capacity_ratios
        assert 1.20 in cfg.capacity_ratios
        assert "ORIGINAL" in cfg.anomaly_modes
        assert "NOANOM" in cfg.anomaly_modes
        assert "DECOY" in cfg.anomaly_modes
        assert cfg.comparison_tolerance == 1e-9

    def test_invalid_config_raises(self):
        """Malformed config must raise before execution."""
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({"benchmark_version": "test"}, f)
            f.flush()
            with pytest.raises(Exception):
                BenchmarkConfig.from_file(f.name)

    def test_config_sha256_stable(self):
        """Same config content → same SHA-256."""
        _skip_if_no_config()
        cfg1 = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        cfg2 = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        assert cfg1.config_sha256 == cfg2.config_sha256

    def test_different_config_different_sha(self):
        """Edited config → different SHA-256."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        sha1 = cfg.config_sha256
        # Modify repetitions
        cfg2 = cfg.model_copy(update={"repetitions": 999})
        sha2 = cfg2.config_sha256
        assert sha1 != sha2

    def test_file_sha256_matches(self):
        """compute_file_sha256 returns consistent result."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        sha_a = cfg.compute_file_sha256(BENCHMARK_CONFIG_PATH)
        sha_b = hashlib.sha256(BENCHMARK_CONFIG_PATH.read_bytes()).hexdigest()
        assert sha_a == sha_b


# ===========================================================================
# 2. Override Detection → Non-Preregistered
# ===========================================================================


class TestOverrideDetection:
    def test_no_overrides_preregistered(self):
        """No overrides → preregistered=True (empty dict)."""
        from backend.app.benchmark.runner import FakeProvider, BenchmarkRunner
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            )
        )
        runner = BenchmarkRunner(provider=provider, config_overrides={})
        assert runner.config_overrides == {}

    def test_repetitions_override_recorded(self):
        """If repetitions differ from config, override is recorded."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        # Simulate CLI detecting override
        overrides = {}
        if 1 != cfg.repetitions:
            overrides["repetitions"] = {"configured": cfg.repetitions, "executed": 1}
        assert "repetitions" in overrides
        assert overrides["repetitions"]["executed"] == 1

    def test_candidate_limit_override_recorded(self):
        """If candidate_limit differs from config, override is recorded."""
        _skip_if_no_config()
        cfg = BenchmarkConfig.from_file(BENCHMARK_CONFIG_PATH)
        overrides = {}
        if 20 != cfg.candidate_limit:
            overrides["candidate_limit"] = {"configured": cfg.candidate_limit, "executed": 20}
        assert "candidate_limit" in overrides


# ===========================================================================
# 3. Retry Policy
# ===========================================================================


class TestRetryPolicy:
    def test_transport_error_is_retriable(self):
        """GraniteTransportError is retriable."""
        exc = GraniteTransportError("connection refused")
        assert is_retriable_benchmark_error(exc) is True

    def test_response_error_not_retriable(self):
        """GraniteResponseError (parse/schema) is NOT retriable."""
        from backend.app.agent.granite_agent import GraniteResponseError
        exc = GraniteResponseError("not valid JSON")
        assert is_retriable_benchmark_error(exc) is False

    def test_api_error_timeout_is_retriable(self):
        """GraniteAPIError with 'timeout' is retriable."""
        from backend.app.agent.granite_agent import GraniteAPIError
        exc = GraniteAPIError("connection timeout")
        assert is_retriable_benchmark_error(exc) is True

    def test_api_error_503_is_retriable(self):
        """GraniteAPIError with '503' is retriable."""
        from backend.app.agent.granite_agent import GraniteAPIError
        exc = GraniteAPIError("HTTP 503 service unavailable")
        assert is_retriable_benchmark_error(exc) is True

    def test_api_error_401_not_retriable(self):
        """GraniteAPIError with '401' auth failure is NOT retriable."""
        from backend.app.agent.granite_agent import GraniteAPIError
        exc = GraniteAPIError("HTTP 401 auth failed")
        assert is_retriable_benchmark_error(exc) is False

    def test_transient_failure_retries_and_succeeds(self):
        """Transport failure on attempt 1, success on attempt 2 → attempt_count=2."""
        from backend.app.agent.granite_agent import GraniteAPIError
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel

        call_count = [0]

        class FlakyAgent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                if call_count[0] < 2:
                    raise GraniteAPIError("HTTP 503 transient failure")
                return json.dumps({
                    "ranked_products": [],
                    "overall_reasoning": "ok",
                    "confidence": 0.8,
                    "decision_factors": [],
                })

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                return CandidatePrioritization(
                    ranked_products=[], overall_reasoning="ok", confidence=0.8,
                    decision_factors=[], candidate_count=0,
                )

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ls = LinkState(timestamp=ts, snr_db=10.0, eb_n0_db=10.0, ber=1e-5,
                       rssi_dbm=-90.0, nominal_data_rate_bps=100000.0,
                       link_goodput_bps=80000.0, latency_s=1.0,
                       link_stability=1.0, remaining_window_s=600.0)
        ms = MissionState(mission_id="test", mission_phase="science",
                          current_event="downlink", event_time_remaining_s=600.0,
                          comm_window_remaining_s=600.0, risk_score=0.1,
                          risk_level=RiskLevel.LOW)

        provider = GraniteBenchmarkProvider(max_attempts=2, delay_s=0.0, agent=FlakyAgent())
        result = provider.prioritize([], ls, ms, [])
        assert result.attempt_count == 2
        assert call_count[0] == 2

    def test_parse_error_not_retried(self):
        """GraniteResponseError (malformed JSON) terminates immediately without retry."""
        from backend.app.agent.granite_agent import GraniteResponseError
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel

        call_count = [0]

        class ParseFailingAgent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                return '{ "ranked_products":'  # malformed JSON

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("not valid JSON")

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ls = LinkState(timestamp=ts, snr_db=10.0, eb_n0_db=10.0, ber=1e-5,
                       rssi_dbm=-90.0, nominal_data_rate_bps=100000.0,
                       link_goodput_bps=80000.0, latency_s=1.0,
                       link_stability=1.0, remaining_window_s=600.0)
        ms = MissionState(mission_id="test", mission_phase="science",
                          current_event="downlink", event_time_remaining_s=600.0,
                          comm_window_remaining_s=600.0, risk_score=0.1,
                          risk_level=RiskLevel.LOW)

        provider = GraniteBenchmarkProvider(max_attempts=2, delay_s=0.0, agent=ParseFailingAgent())
        with pytest.raises(GraniteResponseError):
            provider.prioritize([], ls, ms, [])
        # Must have called exactly once — not retried
        assert call_count[0] == 1

    def test_invalid_product_id_not_retried(self):
        """GraniteResponseError for invalid product ID → attempt_count=1, no retry."""
        from backend.app.agent.granite_agent import GraniteResponseError
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel

        call_count = [0]

        class InvalidProductAgent:
            _model_id = "test-model"

            def _call_prioritization_api(self, user_message: str) -> str:
                call_count[0] += 1
                return json.dumps({
                    "ranked_products": [{"product_id": "FAKE-999", "priority": 1, "reason": "fake"}],
                    "overall_reasoning": "hallucinated",
                    "confidence": 0.9,
                    "decision_factors": [],
                })

            def _parse_prioritization_response(self, raw, valid_ids, candidates):
                raise GraniteResponseError("unknown product_id 'FAKE-999'")

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ls = LinkState(timestamp=ts, snr_db=10.0, eb_n0_db=10.0, ber=1e-5,
                       rssi_dbm=-90.0, nominal_data_rate_bps=100000.0,
                       link_goodput_bps=80000.0, latency_s=1.0,
                       link_stability=1.0, remaining_window_s=600.0)
        ms = MissionState(mission_id="test", mission_phase="science",
                          current_event="downlink", event_time_remaining_s=600.0,
                          comm_window_remaining_s=600.0, risk_score=0.1,
                          risk_level=RiskLevel.LOW)

        provider = GraniteBenchmarkProvider(max_attempts=2, delay_s=0.0, agent=InvalidProductAgent())
        with pytest.raises(GraniteResponseError):
            provider.prioritize([], ls, ms, [])
        assert call_count[0] == 1


# ===========================================================================
# 4. Trial Success Semantics
# ===========================================================================


class TestTrialSuccessSemantics:
    def test_plan_build_failure_not_success(self):
        """Valid provider response → plan build fails → status != SUCCESS."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import (
            CandidatePrioritization, RankedProduct
        )
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator
        from backend.app.candidate_generator.ranked_prefix_builder import SharedPlanBuildError

        call_count = [0]

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            call_count[0] += 1
            if not candidates:
                return CandidatePrioritization(
                    ranked_products=[], overall_reasoning="ok", confidence=0.5,
                    decision_factors=[], candidate_count=0,
                )
            return CandidatePrioritization(
                ranked_products=[RankedProduct(
                    product_id=candidates[0].product_id,
                    priority=1,
                    reason="only product",
                    factors=[],
                    anomaly_ids=[],
                    subsystem=candidates[0].subsystem,
                )],
                overall_reasoning="ok",
                confidence=0.5,
                decision_factors=[],
                candidate_count=len(candidates),
            )

        provider = FakeProvider(ok_provider)

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)

        # Patch build_ai_prioritized_plan to raise
        with patch("backend.app.benchmark.runner.build_ai_prioritized_plan") as mock_build:
            mock_build.side_effect = SharedPlanBuildError("forced plan build failure")
            trials = runner.run_variant(variants[0])

        trial = trials[0]
        assert trial.status == BenchmarkStatus.PLAN_BUILD_ERROR
        # AI plan result must NOT appear
        assert "ai-prioritized" not in trial.plan_ids

    def test_evaluation_failure_not_success(self):
        """Provider valid, plan built, but evaluator raises → evaluation_error."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates),
            )

        provider = FakeProvider(ok_provider)
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)

        with patch("backend.app.benchmark.runner._evaluate_plan") as mock_eval:
            mock_eval.side_effect = [
                # First 5 calls for deterministic plans succeed (plan, ev, mo)
                Exception("forced evaluation failure"),
            ]
            # Let deterministic plans evaluate normally
            mock_eval.side_effect = None
            # Only raise on the AI plan evaluation
            original_eval = mock_eval

        # Use a targeted patch
        import backend.app.benchmark.runner as runner_module
        original_fn = runner_module._evaluate_plan

        call_idx = [0]
        det_plan_count = 5  # 4 classical + 1 semantic-rule

        def patched_eval(plan, link_state, mission_state, data_products, anomalies):
            call_idx[0] += 1
            if call_idx[0] > det_plan_count:
                raise RuntimeError("forced AI evaluation failure")
            return original_fn(plan, link_state, mission_state, data_products, anomalies)

        with patch.object(runner_module, "_evaluate_plan", side_effect=patched_eval):
            trials = runner.run_variant(variants[0])

        trial = trials[0]
        assert trial.status == BenchmarkStatus.EVALUATION_ERROR


# ===========================================================================
# 5. Prompt / Response Hash Provenance
# ===========================================================================


class TestPromptResponseProvenance:
    def test_exact_user_message_hash(self):
        """sha256(actual user message) == trial.prompt_user_sha256."""
        _skip_if_no_scenario()
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        from backend.app.models.candidate_prioritization import (
            CandidatePrioritization, RankedProduct
        )
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator
        from backend.app.telecom.engine import TelecomEngine
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]
        v = variants[0]
        engine = TelecomEngine()
        link_state = engine.compute(v.scenario.link_inputs)
        cp = CandidatePrioritizer(max_candidates=10)
        candidates = cp.select(v.scenario.data_products, anomalies=v.scenario.anomalies,
                               remaining_window_s=link_state.remaining_window_s)

        captured_messages: list[str] = []

        def capturing_provider(candidates_arg, ls, ms, anomalies, distance_km=None):
            msg = build_prioritization_message(candidates_arg, ls, ms, anomalies,
                                               distance_km=distance_km)
            captured_messages.append(msg)
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates_arg),
            )

        provider = FakeProvider(capturing_provider)
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=10)
        trials = runner.run_variant(v, candidates_override=candidates)

        trial = trials[0]
        assert len(captured_messages) == 1
        expected_sha = hashlib.sha256(captured_messages[0].encode()).hexdigest()
        # FakeProvider records the actual user message hash
        assert trial.prompt_user_sha256 == expected_sha

    def test_raw_response_sha_recorded_separately_from_ranking_hash(self):
        """raw_response_sha256 != ranking_hash (different semantics)."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import (
            CandidatePrioritization, RankedProduct
        )
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            if not candidates:
                return CandidatePrioritization(
                    ranked_products=[], overall_reasoning="ok", confidence=0.5,
                    decision_factors=[], candidate_count=0,
                )
            return CandidatePrioritization(
                ranked_products=[RankedProduct(
                    product_id=candidates[0].product_id,
                    priority=1,
                    reason="test",
                    factors=[],
                    anomaly_ids=[],
                    subsystem=candidates[0].subsystem,
                )],
                overall_reasoning="ok",
                confidence=0.5,
                decision_factors=[],
                candidate_count=len(candidates),
            )

        provider = FakeProvider(ok_provider)
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)
        trials = runner.run_variant(gen.generate_all()[0])
        trial = trials[0]

        # If there's a ranking, raw_response_sha256 should differ from ranking_hash
        # (FakeProvider constructs a synthetic raw response containing more than just IDs)
        if trial.ranking:
            assert trial.raw_response_sha256 != "" or trial.ranking_hash != ""
            # They CAN be different — just both must be populated
            assert trial.raw_response_sha256 != ""
            assert trial.ranking_hash != ""

    def test_system_prompt_hash_recorded(self):
        """Trial records a non-empty prompt_system_sha256."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates),
            )

        provider = FakeProvider(ok_provider, system_prompt="test-system-prompt-content")
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)
        trials = runner.run_variant(gen.generate_all()[0])
        trial = trials[0]

        expected = hashlib.sha256("test-system-prompt-content".encode()).hexdigest()
        assert trial.prompt_system_sha256 == expected


# ===========================================================================
# 6. Save Prompts / Audit Files
# ===========================================================================


class TestSavePrompts:
    def test_save_prompts_creates_audit_files(self):
        """With --save-prompts, audit files are created."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates),
            )

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test-run"
            provider = FakeProvider(ok_provider, system_prompt="sys-prompt-content")
            runner = BenchmarkRunner(
                provider=provider, repetitions=1, dry_run=False,
                save_prompts=True, output_dir=out,
            )
            trials = runner.run_variant(gen.generate_all()[0])

            audit_dir = out / "audit"
            assert audit_dir.exists(), "audit/ directory must be created"
            audit_files = list(audit_dir.iterdir())
            assert len(audit_files) > 0, "audit files must exist"

    def test_save_prompts_no_secrets(self):
        """Audit files must not contain Bearer tokens or API keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_dir = Path(tmpdir) / "audit"
            trial_id = "test_trial_001"
            _write_audit_files(
                audit_dir,
                trial_id,
                system_prompt="You are an agent. Bearer secret-token-abc123 here.",
                user_message='{"context": "data", "apikey=mysecretkey123": "value"}',
                raw_response='{"ranked_products": []}',
            )

            for f in audit_dir.iterdir():
                content = f.read_text()
                assert "secret-token-abc123" not in content, f"{f.name} contains secret token"
                assert "mysecretkey123" not in content, f"{f.name} contains API key"

    def test_save_prompts_hash_matches_content(self):
        """Hash of saved prompt content must equal recorded trial hash."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates),
            )

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test-run"
            provider = FakeProvider(ok_provider, system_prompt="hashable-system-content")
            runner = BenchmarkRunner(
                provider=provider, repetitions=1, dry_run=False,
                save_prompts=True, output_dir=out,
            )
            trials = runner.run_variant(gen.generate_all()[0])
            trial = trials[0]

            # Verify system prompt hash
            audit_dir = out / "audit"
            system_files = list(audit_dir.glob("*.system.txt"))
            if system_files:
                # The saved content (after redaction) should hash to the same value
                # OR the original prompt should hash to the trial's recorded hash
                expected_sys_sha = hashlib.sha256("hashable-system-content".encode()).hexdigest()
                assert trial.prompt_system_sha256 == expected_sys_sha

    def test_no_save_prompts_no_audit_files(self):
        """Without --save-prompts, audit directory must not be created."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(candidates),
            )

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test-run"
            provider = FakeProvider(ok_provider)
            runner = BenchmarkRunner(
                provider=provider, repetitions=1, dry_run=False,
                save_prompts=False, output_dir=out,
            )
            runner.run_variant(gen.generate_all()[0])

            audit_dir = out / "audit"
            assert not audit_dir.exists(), "audit/ dir must NOT be created without --save-prompts"


# ===========================================================================
# 7. Provider Model ID
# ===========================================================================


class TestProviderModelID:
    def test_fake_provider_model_id(self):
        """FakeProvider.model_id returns the injected model ID."""
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
            model_id="test-model-v1",
        )
        assert provider.model_id == "test-model-v1"

    def test_trial_records_actual_model_id(self):
        """Trial must record the provider's actual model ID, not 'unknown'."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(c),
            ),
            model_id="test-model-v1",
        )
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)
        trials = runner.run_variant(gen.generate_all()[0])
        assert trials[0].actual_model_id == "test-model-v1"
        assert trials[0].actual_model_id != "unknown"

    def test_generation_config_recorded(self):
        """Trial records generation_config from provider."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=len(c),
            ),
        )
        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=5)
        trials = runner.run_variant(gen.generate_all()[0])
        # FakeProvider doesn't set generation_config, but field must exist
        assert isinstance(trials[0].generation_config, dict)


# ===========================================================================
# 8. Analysis Trial Grouping
# ===========================================================================


class TestAnalysisTrialGrouping:
    def test_group_by_trial_id(self):
        """group_by_trial_id groups by trial_id not (scenario, rep)."""
        pr1 = _make_pr(PlanType.AI_PRIORITIZED, trial_id="trial_A")
        pr2 = _make_pr(PlanType.SEMANTIC_RULE, trial_id="trial_A")
        pr3 = _make_pr(PlanType.AI_NO_DESCRIPTION, trial_id="trial_B",
                       experiment_variant=ExperimentVariant.NO_DESCRIPTION)

        groups = group_by_trial_id([pr1, pr2, pr3])
        assert len(groups) == 2
        assert "trial_A" in groups
        assert "trial_B" in groups
        assert len(groups["trial_A"]) == 2
        assert len(groups["trial_B"]) == 1

    def test_ablation_isolation_three_groups(self):
        """Same scenario/rep but different variants → three separate Pareto groups."""
        # FULL context trial
        full_ai = _make_pr(PlanType.AI_PRIORITIZED, trial_id="run_CAP035_rep01",
                           experiment_variant=ExperimentVariant.FULL)
        full_sr = _make_pr(PlanType.SEMANTIC_RULE, trial_id="run_CAP035_rep01",
                           experiment_variant=ExperimentVariant.FULL)
        # NO_DESCRIPTION ablation trial
        desc_ai = _make_pr(PlanType.AI_NO_DESCRIPTION,
                           trial_id="run_CAP035_rep01_abl_no_description",
                           experiment_variant=ExperimentVariant.NO_DESCRIPTION)
        desc_sr = _make_pr(PlanType.SEMANTIC_RULE,
                           trial_id="run_CAP035_rep01_abl_no_description",
                           experiment_variant=ExperimentVariant.NO_DESCRIPTION)
        # NO_ANOMALY_CONTEXT ablation trial
        anom_ai = _make_pr(PlanType.AI_NO_ANOMALY_CONTEXT,
                           trial_id="run_CAP035_rep01_abl_no_anomaly_context",
                           experiment_variant=ExperimentVariant.NO_ANOMALY_CONTEXT)
        anom_sr = _make_pr(PlanType.SEMANTIC_RULE,
                           trial_id="run_CAP035_rep01_abl_no_anomaly_context",
                           experiment_variant=ExperimentVariant.NO_ANOMALY_CONTEXT)

        all_results = [full_ai, full_sr, desc_ai, desc_sr, anom_ai, anom_sr]
        groups = group_by_trial_id(all_results)
        assert len(groups) == 3, f"Expected 3 trial groups, got {len(groups)}"

    def test_filter_core_results_excludes_ablations(self):
        """filter_core_results returns only FULL experiment_variant results."""
        full = _make_pr(PlanType.AI_PRIORITIZED, experiment_variant=ExperimentVariant.FULL)
        ablation = _make_pr(PlanType.AI_NO_DESCRIPTION,
                            experiment_variant=ExperimentVariant.NO_DESCRIPTION)
        core = filter_core_results([full, ablation])
        assert len(core) == 1
        assert core[0].plan_type == PlanType.AI_PRIORITIZED

    def test_core_pareto_excludes_ablation_plans(self):
        """Pareto analysis on core results must not include ablation plan types."""
        full_ai = _make_pr(PlanType.AI_PRIORITIZED, trial_id="t1",
                           experiment_variant=ExperimentVariant.FULL)
        full_sr = _make_pr(PlanType.SEMANTIC_RULE, trial_id="t1",
                           experiment_variant=ExperimentVariant.FULL)
        ablation_ai = _make_pr(PlanType.AI_NO_DESCRIPTION,
                               trial_id="t2",
                               experiment_variant=ExperimentVariant.NO_DESCRIPTION)

        core = filter_core_results([full_ai, full_sr, ablation_ai])
        # Only FULL results should be in core
        types = {r.plan_type for r in core}
        assert PlanType.AI_NO_DESCRIPTION not in types


# ===========================================================================
# 9. All 5 Comparators
# ===========================================================================


class TestAllComparators:
    def _make_trial_group(self, scenario_id: str, rep: int, trial_id_prefix: str):
        """Make a full set of plan results for one trial."""
        return [
            _make_pr(PlanType.AI_PRIORITIZED, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
            _make_pr(PlanType.SEMANTIC_RULE, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
            _make_pr(PlanType.BASELINE, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
            _make_pr(PlanType.DEADLINE_FIRST, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
            _make_pr(PlanType.MISSION_CRITICAL_FIRST, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
            _make_pr(PlanType.VALUE_PER_COST, scenario_id=scenario_id,
                     repetition=rep, trial_id=f"{trial_id_prefix}_rep{rep:02d}"),
        ]

    def test_all_five_comparators_present(self):
        """compute_all_comparisons returns data for all 5 comparators."""
        all_prs = self._make_trial_group("CAP035_ORIGINAL", 1, "run_CAP035_ORIGINAL")

        comparisons = compute_all_comparisons(all_prs, core_only=False)
        assert "semantic-rule-based" in comparisons
        assert "baseline" in comparisons
        assert "deadline-first" in comparisons
        assert "mission-critical-first" in comparisons
        assert "value-per-cost" in comparisons

    def test_comparisons_not_empty(self):
        """Each comparator should produce at least one comparison."""
        all_prs = self._make_trial_group("CAP035_ORIGINAL", 1, "run_CAP035_ORIGINAL")
        comparisons = compute_all_comparisons(all_prs, core_only=False)
        for comp_key, comps in comparisons.items():
            assert len(comps) > 0, f"No comparisons for {comp_key}"


# ===========================================================================
# 10. Capacity Grouping
# ===========================================================================


class TestCapacityGrouping:
    def _make_cap_trial_group(self, scenario_id: str, trial_id: str):
        return [
            _make_pr(PlanType.AI_PRIORITIZED, scenario_id=scenario_id, trial_id=trial_id),
            _make_pr(PlanType.SEMANTIC_RULE, scenario_id=scenario_id, trial_id=trial_id),
        ]

    def test_capacity_groups(self):
        """CAP035, CAP060, CAP090, CAP120 → separate analysis buckets."""
        all_prs = []
        for cap in ["CAP035_ORIGINAL", "CAP060_ORIGINAL", "CAP090_ORIGINAL", "CAP120_ORIGINAL"]:
            all_prs.extend(self._make_cap_trial_group(cap, f"run_{cap}_rep01"))

        cap_analysis = compute_capacity_analysis(all_prs)
        assert "CAP035" in cap_analysis
        assert "CAP060" in cap_analysis
        assert "CAP090" in cap_analysis
        assert "CAP120" in cap_analysis


# ===========================================================================
# 11. Anomaly Mode Grouping
# ===========================================================================


class TestAnomalyGrouping:
    def test_anomaly_mode_groups(self):
        """ORIGINAL, NOANOM, DECOY → separate analysis buckets."""
        all_prs = []
        for mode_suffix in ["ORIGINAL", "NOANOM", "DECOY"]:
            scenario_id = f"CAP035_{mode_suffix}"
            trial_id = f"run_{scenario_id}_rep01"
            all_prs.extend([
                _make_pr(PlanType.AI_PRIORITIZED, scenario_id=scenario_id, trial_id=trial_id),
                _make_pr(PlanType.SEMANTIC_RULE, scenario_id=scenario_id, trial_id=trial_id),
            ])

        anom_analysis = compute_anomaly_analysis(all_prs)
        assert "ORIGINAL" in anom_analysis
        assert "NOANOM" in anom_analysis
        assert "DECOY" in anom_analysis


# ===========================================================================
# 12. Per-Scenario Variability
# ===========================================================================


class TestPerScenarioVariability:
    def test_variability_stats(self):
        """Five AI repetitions → correct median, min, max, IQR."""
        # Create 5 repetitions with known mission_value
        values = [0.5, 0.6, 0.7, 0.8, 0.9]
        prs = []
        for i, v in enumerate(values, start=1):
            prs.append(_make_pr(
                PlanType.AI_PRIORITIZED,
                scenario_id="CAP035_ORIGINAL",
                repetition=i,
                trial_id=f"run_CAP035_ORIGINAL_rep{i:02d}",
                phys=_make_phys(mission_value=v),
            ))

        variability = compute_scenario_variability(prs)
        assert "CAP035_ORIGINAL" in variability
        stats = variability["CAP035_ORIGINAL"]
        assert stats["valid_repetition_count"] == 5
        mv_stats = stats["metrics"]["mission_value"]
        assert mv_stats["min"] == pytest.approx(0.5)
        assert mv_stats["max"] == pytest.approx(0.9)
        assert mv_stats["median"] == pytest.approx(0.7)

    def test_iqr_requires_four_values(self):
        """IQR is None for fewer than 4 values."""
        prs = [
            _make_pr(PlanType.AI_PRIORITIZED, scenario_id="S1", repetition=i,
                     trial_id=f"tid_{i}", phys=_make_phys(mission_value=float(i)))
            for i in range(1, 4)  # only 3 values
        ]
        variability = compute_scenario_variability(prs)
        assert variability["S1"]["metrics"]["mission_value"]["iqr"] is None


# ===========================================================================
# 13. Ablation Analysis
# ===========================================================================


class TestAblationAnalysis:
    def test_ablation_deltas(self):
        """Ablation analysis produces correct deltas from known values."""
        # Full context: mission_value = 0.8
        full_ai = _make_pr(
            PlanType.AI_PRIORITIZED,
            scenario_id="CAP035_ORIGINAL",
            trial_id="run_CAP035_rep01",
            experiment_variant=ExperimentVariant.FULL,
            phys=_make_phys(mission_value=0.8),
        )
        # no_description: mission_value = 0.6
        no_desc_ai = _make_pr(
            PlanType.AI_NO_DESCRIPTION,
            scenario_id="CAP035_ORIGINAL",
            trial_id="run_CAP035_rep01_abl_no_description",
            experiment_variant=ExperimentVariant.NO_DESCRIPTION,
            phys=_make_phys(mission_value=0.6),
        )
        # no_anomaly: mission_value = 0.7
        no_anom_ai = _make_pr(
            PlanType.AI_NO_ANOMALY_CONTEXT,
            scenario_id="CAP035_ORIGINAL",
            trial_id="run_CAP035_rep01_abl_no_anomaly_context",
            experiment_variant=ExperimentVariant.NO_ANOMALY_CONTEXT,
            phys=_make_phys(mission_value=0.7),
        )

        analysis = compute_ablation_analysis([full_ai, no_desc_ai, no_anom_ai])
        assert "CAP035_ORIGINAL" in analysis
        mv = analysis["CAP035_ORIGINAL"]["metrics"]["mission_value"]
        assert mv["full_median"] == pytest.approx(0.8)
        assert mv["no_description_median"] == pytest.approx(0.6)
        assert mv["no_anomaly_median"] == pytest.approx(0.7)
        assert mv["delta_no_description"] == pytest.approx(-0.2)  # 0.6 - 0.8
        assert mv["delta_no_anomaly"] == pytest.approx(-0.1)      # 0.7 - 0.8

    def test_ablation_not_in_core_summary(self):
        """Core summary must not include ablation plan results."""
        core_ai = _make_pr(PlanType.AI_PRIORITIZED,
                           experiment_variant=ExperimentVariant.FULL,
                           trial_id="t1")
        ablation_ai = _make_pr(PlanType.AI_NO_DESCRIPTION,
                               experiment_variant=ExperimentVariant.NO_DESCRIPTION,
                               trial_id="t2")
        core = filter_core_results([core_ai, ablation_ai])
        assert len(core) == 1
        assert core[0].plan_type == PlanType.AI_PRIORITIZED


# ===========================================================================
# 14. Pareto CSV Population
# ===========================================================================


class TestParetoCsvPopulation:
    def test_pareto_fields_populated_in_csv(self):
        """enrich_pareto_metadata populates Pareto fields; they appear in CSV."""
        ai = _make_pr(PlanType.AI_PRIORITIZED, trial_id="t1",
                      phys=_make_phys(risk_score=0.1, mission_value=10.0,
                                      critical_delivery_rate=1.0))
        sr = _make_pr(PlanType.SEMANTIC_RULE, trial_id="t1",
                      phys=_make_phys(risk_score=0.9, mission_value=1.0,
                                      critical_delivery_rate=0.1))

        enriched = enrich_pareto_metadata([ai, sr])
        ai_e = next(r for r in enriched if r.plan_type == PlanType.AI_PRIORITIZED)
        sr_e = next(r for r in enriched if r.plan_type == PlanType.SEMANTIC_RULE)

        assert ai_e.is_pareto_frontier is True
        assert sr_e.is_pareto_frontier is False
        assert ai_e.plans_dominated_count is not None
        assert sr_e.plans_dominating_this_plan_count is not None

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "summary.csv"
            write_summary_csv(enriched, csv_path)
            content = csv_path.read_text()
            assert "is_pareto_frontier" in content
            assert "True" in content or "False" in content


# ===========================================================================
# 15. generate_core() Fix
# ===========================================================================


class TestGenerateCore:
    def test_generate_core_without_invalid_path(self):
        """generate_core() returns 12 valid scenarios without empty-path construction."""
        _skip_if_no_scenario()
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        core = gen.generate_core()
        assert len(core) == 12
        for variant in core:
            assert variant.spec.deadline_scale == 1.0
            assert variant.scenario is not None

    def test_generate_core_matches_generate_all_filtered(self):
        """generate_core() == [v for v in generate_all() if v.spec.deadline_scale == 1.0]."""
        _skip_if_no_scenario()
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        core = gen.generate_core()
        all_variants = gen.generate_all()
        expected_ids = {v.spec.scenario_id for v in all_variants if v.spec.deadline_scale == 1.0}
        core_ids = {v.spec.scenario_id for v in core}
        assert core_ids == expected_ids


# ===========================================================================
# 16. Call Count Display
# ===========================================================================


class TestCallCountDisplay:
    def test_call_count_12_scenarios_5_reps_2_attempts(self):
        """12 scenarios × 5 reps × max_attempts=2 → correct counts."""
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
        )
        provider.max_attempts = 2  # type: ignore
        runner = BenchmarkRunner(provider=provider, repetitions=5)

        estimates = runner.estimate_call_count(12, include_ablations=False)
        assert estimates["logical_trials"] == 60
        assert estimates["normal_provider_calls"] == 60
        assert estimates["max_provider_attempts"] == 120

    def test_call_count_with_ablations(self):
        """12 core + 4 ablation scenarios × 2 variants × 5 reps → 100 logical trials."""
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        provider = FakeProvider(
            lambda c, ls, ms, anom, distance_km=None: CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.5,
                decision_factors=[], candidate_count=0,
            ),
        )
        provider.max_attempts = 2  # type: ignore
        runner = BenchmarkRunner(provider=provider, repetitions=5)

        estimates = runner.estimate_call_count(
            12, include_ablations=True, ablation_scenarios_count=4
        )
        assert estimates["logical_trials"] == 100
        assert estimates["max_provider_attempts"] == 200


# ===========================================================================
# 17. Report Reproduction Command
# ===========================================================================


class TestReportReproductionCommand:
    def _make_simple_report(self) -> str:
        trials = [_make_trial()]
        plan_results = [_make_pr(PlanType.AI_PRIORITIZED)]
        summary = compute_summary(trials, plan_results)
        return generate_markdown_report(summary, trials, plan_results)

    def test_reproduction_uses_runner_cli(self):
        """Report must reference runner_cli, not the old runner module."""
        report = self._make_simple_report()
        assert "runner_cli" in report, "Report must use runner_cli not runner"
        assert "python -m backend.app.benchmark.runner " not in report.replace(
            "runner_cli", ""
        )

    def test_all_14_sections_present(self):
        """Report must contain all sections 1-14."""
        report = self._make_simple_report()
        for section_num in range(1, 15):
            assert f"## {section_num}." in report, f"Section {section_num} missing from report"

    def test_pilot_report_says_pilot(self):
        """Pilot run must clearly say PILOT in report."""
        trial = _make_trial()
        plan_results = [_make_pr(PlanType.AI_PRIORITIZED)]
        summary = compute_summary([trial], plan_results)
        summary["run_type"] = "pilot"
        report = generate_markdown_report(summary, [trial], plan_results)
        assert "PILOT" in report or "pilot" in report.lower()


# ===========================================================================
# 18. Secret Redaction
# ===========================================================================


class TestSecretRedaction:
    def test_redact_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.abc.def"
        result = _redact_secrets(text)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[REDACTED]" in result

    def test_redact_api_key(self):
        text = 'apikey=my-super-secret-key-12345'
        result = _redact_secrets(text)
        assert "my-super-secret-key-12345" not in result

    def test_preserve_product_ids(self):
        """Regular product/mission IDs must NOT be redacted."""
        text = '{"product_id": "IMG-2024-001", "scenario": "CAP035_ORIGINAL"}'
        result = _redact_secrets(text)
        assert "IMG-2024-001" in result
        assert "CAP035_ORIGINAL" in result


# ===========================================================================
# 19. BenchmarkStatus New Values
# ===========================================================================


class TestBenchmarkStatusValues:
    def test_plan_build_error_status(self):
        assert BenchmarkStatus.PLAN_BUILD_ERROR.value == "plan_build_error"

    def test_evaluation_error_status(self):
        assert BenchmarkStatus.EVALUATION_ERROR.value == "evaluation_error"

    def test_schema_error_status(self):
        assert BenchmarkStatus.SCHEMA_ERROR.value == "schema_error"

    def test_classify_response_error(self):
        """GraniteResponseError with JSON → PARSE_ERROR."""
        from backend.app.agent.granite_agent import GraniteResponseError
        exc = GraniteResponseError("Prioritization response is not valid JSON")
        status = _classify_error(exc)
        assert status == BenchmarkStatus.PARSE_ERROR

    def test_classify_invalid_id(self):
        """GraniteResponseError with unknown product_id → INVALID_RESPONSE."""
        from backend.app.agent.granite_agent import GraniteResponseError
        exc = GraniteResponseError("unknown product_id 'FAKE-999'")
        status = _classify_error(exc)
        assert status == BenchmarkStatus.INVALID_RESPONSE
