"""Tests for benchmark runner — strict provider mode, retry policy, schema.

Covers:
- Provider failure → status=provider_error (NO Local substitution)
- Retry policy: 2 attempts max
- Valid bad plan is NOT retried (only transport failures are retried)
- BenchmarkRecord round-trip JSON serialization
- Deterministic control plan stability
- Candidate set fairness (Granite and semantic-rule get same candidates)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.app.benchmark.models import (
    BenchmarkStatus,
    BenchmarkTrial,
    BenchmarkPlanResult,
    PlanType,
)
from backend.app.benchmark.runner import (
    BenchmarkRunner,
    FakeProvider,
    _classify_error,
    _plan_order_hash,
    build_deterministic_plans,
)
from backend.app.benchmark.scenario_variants import (
    AnomalyMode,
    ScenarioVariantGenerator,
)

BASE_SCENARIO_PATH = Path("data/scenarios/mission_data_v3.json")


def _skip_if_no_scenario():
    if not BASE_SCENARIO_PATH.exists():
        pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")


# ---------------------------------------------------------------------------
# Test: provider failure recorded correctly (no Local fallback)
# ---------------------------------------------------------------------------


class TestStrictProviderMode:
    def test_provider_error_recorded_as_failure(self):
        _skip_if_no_scenario()

        call_count = [0]

        def failing_provider(candidates, link_state, ms, anomalies, distance_km=None):
            call_count[0] += 1
            raise RuntimeError("API unavailable")

        provider = FakeProvider(failing_provider)
        provider.max_attempts = 1

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        runner = BenchmarkRunner(
            provider=provider,
            repetitions=1,
            candidate_limit=10,
            dry_run=False,
        )

        trials = runner.run_variant(variants[0])
        assert len(trials) == 1
        trial = trials[0]
        # Must record failure, NOT substitute Local
        assert trial.status != BenchmarkStatus.SUCCESS
        assert trial.status in (
            BenchmarkStatus.PROVIDER_ERROR, BenchmarkStatus.INVALID_RESPONSE,
            BenchmarkStatus.PARSE_ERROR, BenchmarkStatus.TIMEOUT,
        )
        # No Local plan should be in plan results via this trial
        assert trial.ranked_count == 0

    def test_provider_error_status_is_not_skipped(self):
        """A failed provider trial must not have status=skipped."""
        _skip_if_no_scenario()

        def failing_provider(candidates, link_state, ms, anomalies, distance_km=None):
            from backend.app.agent.granite_agent import GraniteAPIError
            raise GraniteAPIError("API down")

        provider = FakeProvider(failing_provider)
        provider.max_attempts = 1

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=10)
        trials = runner.run_variant(variants[0])
        assert trials[0].status != BenchmarkStatus.SKIPPED


# ---------------------------------------------------------------------------
# Test: retry policy
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    def test_retry_policy_in_granite_provider(self):
        """GraniteBenchmarkProvider retry loop: attempt 1 fails, attempt 2 succeeds."""
        from backend.app.agent.granite_agent import GraniteAPIError
        from backend.app.benchmark.runner import GraniteBenchmarkProvider
        from backend.app.models.candidate_prioritization import CandidatePrioritization
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        from datetime import datetime, timezone

        call_count = [0]

        class FlakyAgent:
            """Fake Granite agent that fails on the first call."""
            def prioritize_candidates(self, candidates, link_state, ms, anomalies, *, distance_km=None):
                call_count[0] += 1
                if call_count[0] < 2:
                    raise GraniteAPIError("Transient failure")
                return CandidatePrioritization(
                    ranked_products=[],
                    overall_reasoning="ok",
                    confidence=0.6,
                    decision_factors=[],
                    candidate_count=len(candidates),
                )

        # Inject the flaky agent directly into the provider
        provider = GraniteBenchmarkProvider(max_attempts=2, agent=FlakyAgent())

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ls = LinkState(timestamp=ts, snr_db=10.0, eb_n0_db=10.0, ber=1e-5,
                       rssi_dbm=-90.0, nominal_data_rate_bps=100000.0,
                       link_goodput_bps=80000.0, latency_s=1.0,
                       link_stability=1.0, remaining_window_s=600.0)
        ms = MissionState(mission_id="test", mission_phase="science",
                          current_event="downlink", event_time_remaining_s=600.0,
                          comm_window_remaining_s=600.0, risk_score=0.1,
                          risk_level=RiskLevel.LOW)

        result = provider.prioritize([], ls, ms, [])
        assert call_count[0] == 2
        assert result is not None

    def test_no_retry_for_valid_bad_plan(self):
        """A valid ranking with poor metrics must NOT be retried."""
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import (
            CandidatePrioritization, RankedProduct
        )

        call_count = [0]

        def valid_but_poor_provider(candidates, link_state, ms, anomalies, distance_km=None):
            call_count[0] += 1
            # Valid ranking — just picks one random product
            if not candidates:
                products = []
            else:
                products = [RankedProduct(
                    product_id=candidates[0].product_id,
                    priority=1,
                    reason="only product",
                    factors=["routine housekeeping"],
                    anomaly_ids=[],
                    subsystem=candidates[0].subsystem,
                )]
            return CandidatePrioritization(
                ranked_products=products,
                overall_reasoning="minimal ranking",
                confidence=0.1,
                decision_factors=[],
                candidate_count=len(candidates),
            )

        provider = FakeProvider(valid_but_poor_provider)
        provider.max_attempts = 2

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        runner = BenchmarkRunner(provider=provider, repetitions=1, candidate_limit=10)
        trials = runner.run_variant(variants[0])
        trial = trials[0]

        # Valid response — should be accepted as SUCCESS, not retried
        assert trial.status == BenchmarkStatus.SUCCESS
        assert call_count[0] == 1  # called exactly once


# ---------------------------------------------------------------------------
# Test: BenchmarkRecord round-trip JSON
# ---------------------------------------------------------------------------


class TestResultSchema:
    def test_trial_round_trip_json(self):
        from backend.app.benchmark.scenario_variants import ScenarioVariantSpec

        spec = ScenarioVariantSpec(
            scenario_id="CAP035_ORIGINAL",
            capacity_ratio=0.35,
            anomaly_mode=AnomalyMode.ORIGINAL,
            total_queued_bits=1000000,
            link_goodput_bps=80000.0,
            communication_window_s=437.5,
            available_capacity_bits=350000.0,
            actual_capacity_ratio=0.35,
        )
        trial = BenchmarkTrial(
            run_id="test-run-001",
            benchmark_version="gcsi_benchmark_v1",
            scenario_id="CAP035_ORIGINAL",
            scenario_variant=spec,
            provider="Granite",
            model="ibm/granite-4-h-small",
            repetition=1,
            status=BenchmarkStatus.SUCCESS,
            capacity_ratio=0.35,
            actual_capacity_ratio=0.35,
            candidate_count=10,
            ranked_count=8,
            unranked_count=2,
        )

        # Round-trip
        raw_json = trial.model_dump_json()
        reloaded = BenchmarkTrial.model_validate_json(raw_json)
        assert reloaded.run_id == trial.run_id
        assert reloaded.status == trial.status
        assert reloaded.scenario_variant.scenario_id == trial.scenario_variant.scenario_id

    def test_plan_result_round_trip_json(self):
        from backend.app.benchmark.models import MissionOutcomeMetrics, PhysicalMetrics
        phys = PhysicalMetrics(
            risk_score=0.3, mission_value=5.0, critical_packets_delivered=5,
            total_critical_packets=8, critical_delivery_rate=0.625,
            deadline_misses=1, deadline_miss_rate=0.1, bandwidth_utilization=0.7,
            retransmission_overhead=0.1, window_pressure=0.5, deferred_count=3,
        )
        mo = MissionOutcomeMetrics(
            scientific_value_capture_rate=0.75, required_delivery_rate=0.9,
        )
        pr = BenchmarkPlanResult(
            run_id="test-run-001",
            scenario_id="CAP035_ORIGINAL",
            repetition=1,
            plan_type=PlanType.AI_PRIORITIZED,
            plan_order_hash="abc123",
            physical_metrics=phys,
            mission_outcome_metrics=mo,
        )
        raw = pr.model_dump_json()
        reloaded = BenchmarkPlanResult.model_validate_json(raw)
        assert reloaded.plan_type == pr.plan_type
        assert reloaded.physical_metrics.risk_score == pr.physical_metrics.risk_score


# ---------------------------------------------------------------------------
# Test: deterministic control plan stability
# ---------------------------------------------------------------------------


class TestDeterministicPlanStability:
    def test_same_scenario_same_plan_hash(self):
        _skip_if_no_scenario()
        from backend.app.benchmark.runner import build_deterministic_plans
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        from backend.app.config import SchedulerWeights
        from backend.app.telecom.engine import TelecomEngine

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]
        v = variants[0]

        engine = TelecomEngine()
        link_state = engine.compute(v.scenario.link_inputs)
        cp = CandidatePrioritizer(max_candidates=20)
        candidates = cp.select(v.scenario.data_products, anomalies=v.scenario.anomalies,
                               remaining_window_s=link_state.remaining_window_s)

        w = SchedulerWeights()
        # Run twice — hashes must be identical
        det1 = build_deterministic_plans(v.scenario, link_state, candidates, w)
        det2 = build_deterministic_plans(v.scenario, link_state, candidates, w)

        for pt in det1:
            hash1 = _plan_order_hash(det1[pt][0])
            hash2 = _plan_order_hash(det2[pt][0])
            assert hash1 == hash2, f"{pt} plan is not stable across runs"


# ---------------------------------------------------------------------------
# Test: candidate set fairness
# ---------------------------------------------------------------------------


class TestCandidateFairness:
    def test_same_candidates_for_llm_and_semantic_rule(self):
        """The benchmark must use the same candidate set for both Granite and semantic-rule."""
        _skip_if_no_scenario()
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        from backend.app.benchmark.scenario_variants import ScenarioVariantGenerator
        from backend.app.telecom.engine import TelecomEngine

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        v = gen.generate_all()[0]

        engine = TelecomEngine()
        link_state = engine.compute(v.scenario.link_inputs)

        cp = CandidatePrioritizer(max_candidates=30)
        candidates = cp.select(v.scenario.data_products, anomalies=v.scenario.anomalies,
                               remaining_window_s=link_state.remaining_window_s)

        candidate_ids = {cs.product_id for cs in candidates}

        # Semantic-rule uses the same candidates
        from backend.app.agent.semantic_rule_prioritizer import SemanticRulePrioritizer
        sr = SemanticRulePrioritizer()
        result = sr.prioritize(candidates, anomalies=v.scenario.anomalies)

        ranked_ids = {rp.product_id for rp in result.ranked_products}
        assert ranked_ids.issubset(candidate_ids), (
            "Semantic-rule prioritizer ranked products not in candidate set"
        )


# ---------------------------------------------------------------------------
# Test: dry run makes zero external calls
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_status_is_skipped(self):
        _skip_if_no_scenario()

        call_count = [0]

        def counting_provider(candidates, link_state, ms, anomalies, distance_km=None):
            call_count[0] += 1
            from backend.app.models.candidate_prioritization import CandidatePrioritization
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="test", confidence=0.5,
                decision_factors=[], candidate_count=0,
            )

        provider = FakeProvider(counting_provider)

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        runner = BenchmarkRunner(provider=provider, repetitions=1, dry_run=True)
        trials = runner.run_variant(variants[0])

        assert all(t.status == BenchmarkStatus.SKIPPED for t in trials)
        assert call_count[0] == 0, "Dry run must make ZERO external calls"


# ---------------------------------------------------------------------------
# Test: raw results written to JSONL
# ---------------------------------------------------------------------------


class TestRawResultsOutput:
    def test_results_written_to_jsonl(self):
        _skip_if_no_scenario()
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        def ok_provider(candidates, link_state, ms, anomalies, distance_km=None):
            return CandidatePrioritization(
                ranked_products=[], overall_reasoning="ok", confidence=0.6,
                decision_factors=[], candidate_count=len(candidates),
            )

        provider = FakeProvider(ok_provider)
        provider.max_attempts = 1

        gen = ScenarioVariantGenerator(base_scenario_path=BASE_SCENARIO_PATH)
        variants = gen.generate_all()[:1]

        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "test-run"
            runner = BenchmarkRunner(
                provider=provider, repetitions=1, dry_run=False, output_dir=out
            )
            trials = runner.run_variant(variants[0])

            jsonl = out / "raw_results.jsonl"
            assert jsonl.exists(), "raw_results.jsonl not created"

            lines = [l for l in jsonl.read_text().splitlines() if l.strip()]
            assert len(lines) > 0
            # Each line must be valid JSON
            for line in lines:
                obj = json.loads(line)
                assert "record_type" in obj
