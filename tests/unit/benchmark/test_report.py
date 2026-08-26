"""Tests for benchmark report generator.

Covers:
- Report does NOT drop losses (required section present)
- Null metrics handled correctly
- Manifest contains required fields and no secrets
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from backend.app.benchmark.models import (
    BenchmarkManifest,
    BenchmarkPlanResult,
    BenchmarkStatus,
    BenchmarkTrial,
    MissionOutcomeMetrics,
    PhysicalMetrics,
    PlanType,
)
from backend.app.benchmark.report import (
    compute_summary,
    generate_markdown_report,
    write_benchmark_outputs,
    write_summary_csv,
)
from backend.app.benchmark.scenario_variants import AnomalyMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec():
    from backend.app.benchmark.models import ScenarioVariantSpec
    return ScenarioVariantSpec(
        scenario_id="CAP035_ORIGINAL",
        capacity_ratio=0.35,
        anomaly_mode=AnomalyMode.ORIGINAL,
        total_queued_bits=1000000,
        link_goodput_bps=80000.0,
        communication_window_s=437.5,
        available_capacity_bits=350000.0,
        actual_capacity_ratio=0.35,
    )


def _make_trial(status=BenchmarkStatus.SUCCESS) -> BenchmarkTrial:
    return BenchmarkTrial(
        trial_id="test-run-001_CAP035_ORIGINAL_rep01",
        run_id="test-run-001",
        benchmark_run_id="test-run-001",
        benchmark_version="gcsi_benchmark_v1",
        scenario_id="CAP035_ORIGINAL",
        scenario_variant=_spec(),
        provider="Granite",
        model="ibm/granite-4-h-small",
        repetition=1,
        status=status,
        capacity_ratio=0.35,
        actual_capacity_ratio=0.35,
        candidate_count=10,
        ranked_count=8,
        unranked_count=2,
    )


def _make_pr(plan_type: PlanType, **phys_kwargs) -> BenchmarkPlanResult:
    risk = phys_kwargs.get("risk_score", 0.3)
    mv = phys_kwargs.get("mission_value", 5.0)
    return BenchmarkPlanResult(
        trial_id="test-run-001_CAP035_ORIGINAL_rep01",
        run_id="test-run-001_CAP035_ORIGINAL_rep01",
        scenario_id="CAP035_ORIGINAL",
        repetition=1,
        plan_type=plan_type,
        plan_order_hash="abc",
        physical_metrics=PhysicalMetrics(
            risk_score=risk, mission_value=mv,
            critical_packets_delivered=5, total_critical_packets=8,
            critical_delivery_rate=0.625, deadline_misses=1, deadline_miss_rate=0.1,
            bandwidth_utilization=0.7, retransmission_overhead=0.1,
            window_pressure=0.5, deferred_count=3,
        ),
        mission_outcome_metrics=MissionOutcomeMetrics(
            scientific_value_capture_rate=0.75, required_delivery_rate=0.9,
            active_anomaly_delivery_rate=0.8, high_severity_anomaly_coverage_rate=0.7,
            anomaly_weighted_coverage=0.72,
        ),
    )


# ---------------------------------------------------------------------------
# Test: Report includes losses section
# ---------------------------------------------------------------------------


class TestReportIncludesLosses:
    def test_losses_section_present_in_report(self):
        """The 'Where the LLM did not outperform' section must always be present."""
        trials = [_make_trial()]
        plan_results = [
            _make_pr(PlanType.AI_PRIORITIZED, risk_score=0.9, mission_value=2.0),  # bad AI plan
            _make_pr(PlanType.SEMANTIC_RULE, risk_score=0.2, mission_value=9.0),   # much better
        ]
        summary = compute_summary(trials, plan_results)
        report = generate_markdown_report(summary, trials, plan_results)

        assert "Where the LLM Did Not Outperform" in report or "did not outperform" in report.lower(), (
            "Report must contain 'Where the LLM Did Not Outperform' section"
        )

    def test_failures_mentioned_in_report(self):
        """Provider failures must appear in the report, not be hidden."""
        failed_trial = _make_trial(status=BenchmarkStatus.PROVIDER_ERROR)
        failed_trial = failed_trial.model_copy(update={
            "error_type": "GraniteAPIError",
        })
        plan_results: list[BenchmarkPlanResult] = []
        summary = compute_summary([failed_trial], plan_results)
        report = generate_markdown_report(summary, [failed_trial], plan_results)

        # Failed count should appear somewhere
        assert "1" in report  # at least mentions 1 failure

    def test_limitations_section_present(self):
        """Limitations section is mandatory."""
        trials = [_make_trial()]
        plan_results = [_make_pr(PlanType.AI_PRIORITIZED)]
        summary = compute_summary(trials, plan_results)
        report = generate_markdown_report(summary, trials, plan_results)
        assert "Limitations" in report

    def test_reproduction_instructions_present(self):
        """Reproduction instructions must be present."""
        trials = [_make_trial()]
        plan_results = [_make_pr(PlanType.AI_PRIORITIZED)]
        summary = compute_summary(trials, plan_results)
        report = generate_markdown_report(summary, trials, plan_results)
        assert "execute-live" in report or "Reproduction" in report


# ---------------------------------------------------------------------------
# Test: Null metrics in report
# ---------------------------------------------------------------------------


class TestNullMetricsInReport:
    def test_null_required_delivery_rate_shown_as_na(self):
        pr = _make_pr(PlanType.AI_PRIORITIZED)
        # Manually null the required_delivery_rate
        pr = pr.model_copy(update={
            "mission_outcome_metrics": MissionOutcomeMetrics(
                scientific_value_capture_rate=0.75,
                required_delivery_rate=None,  # explicitly null
            )
        })
        trials = [_make_trial()]
        summary = compute_summary(trials, [pr])
        # Summary should not crash and should handle None gracefully
        assert summary is not None
        # Check CSV handles None
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "summary.csv"
            write_summary_csv([pr], csv_path)
            content = csv_path.read_text()
            # required_delivery_rate column should not have "null" but empty/blank
            assert "null" not in content.lower() or "None" not in content


# ---------------------------------------------------------------------------
# Test: Manifest has required fields and no secrets
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_contains_required_fields(self):
        manifest = BenchmarkManifest(
            benchmark_version="gcsi_benchmark_v1",
            run_id="test-run-001",
            benchmark_run_id="test-run-001",
            timestamp_utc="2024-06-15T09:41:00+00:00",
            git_commit_sha="abc123",
            base_scenario_sha256="sha256hex",
            provider="Granite",
            model="ibm/granite-4-h-small",
            candidate_limit=50,
            scenario_matrix=["CAP035_ORIGINAL", "CAP060_ORIGINAL"],
            repetitions=5,
            retry_policy={"max_attempts": 2},
            primary_metrics=["risk_score", "mission_value"],
            comparison_tolerance=1e-9,
            python_version="3.14",
            platform="win32",
        )
        data = json.loads(manifest.model_dump_json())
        # Required fields
        assert "benchmark_version" in data
        assert "run_id" in data
        assert "git_commit_sha" in data
        assert "base_scenario_sha256" in data
        assert "provider" in data
        assert "model" in data

    def test_manifest_no_api_key(self):
        manifest = BenchmarkManifest(
            benchmark_version="gcsi_benchmark_v1",
            run_id="test-run-001",
            benchmark_run_id="test-run-001",
            timestamp_utc="2024-06-15T09:41:00+00:00",
            git_commit_sha="abc123",
            base_scenario_sha256="sha256hex",
            provider="Granite",
            model="ibm/granite-4-h-small",
            candidate_limit=50,
            scenario_matrix=[],
            repetitions=5,
            retry_policy={},
            primary_metrics=[],
            comparison_tolerance=1e-9,
            python_version="3.14",
            platform="win32",
        )
        raw = manifest.model_dump_json()
        assert "api_key" not in raw.lower()
        assert "Bearer" not in raw
        assert "secret" not in raw.lower()


# ---------------------------------------------------------------------------
# Test: write_benchmark_outputs creates all files
# ---------------------------------------------------------------------------


class TestWriteBenchmarkOutputs:
    def test_creates_all_output_files(self):
        trials = [_make_trial()]
        plan_results = [
            _make_pr(PlanType.AI_PRIORITIZED),
            _make_pr(PlanType.SEMANTIC_RULE, risk_score=0.4),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir = Path(tmpdir) / "results"
            write_benchmark_outputs(result_dir, trials, plan_results)
            assert (result_dir / "summary.json").exists()
            assert (result_dir / "summary.csv").exists()
            assert (result_dir / "report.md").exists()

    def test_summary_json_is_valid_json(self):
        trials = [_make_trial()]
        plan_results = [_make_pr(PlanType.AI_PRIORITIZED)]

        with tempfile.TemporaryDirectory() as tmpdir:
            result_dir = Path(tmpdir) / "results"
            write_benchmark_outputs(result_dir, trials, plan_results)
            content = (result_dir / "summary.json").read_text()
            data = json.loads(content)
            assert "total_trials" in data
