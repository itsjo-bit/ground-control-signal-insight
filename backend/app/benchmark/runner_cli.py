"""GCSI Phase 2B Benchmark CLI.

Usage:
    python -m backend.app.benchmark.runner_cli [options]

Or via script:
    python scripts/run_benchmark.py [options]

Options:
    --provider       Provider to use: Granite (default)
    --suite          Scenario suite: quick|core|full (default: core)
    --repetitions    LLM repetitions per scenario (default: 5)
    --output-dir     Output directory (default: benchmarks/results/<run-id>/)
    --save-prompts   Save sanitized prompt content
    --include-ablations  Run ablation variants
    --dry-run        Generate scenarios and show call count without API calls
    --execute-live   Required flag for live API execution
    --seed-list      Comma-separated random seeds for stochastic components

Safety:
    --dry-run  performs ZERO external API calls.
    Live execution requires --execute-live flag to prevent accidents.
    Expected call count is always displayed before live execution.

Provider credentials:
    GCSI_GRANITE_API_KEY     (required for Granite)
    GCSI_GRANITE_PROJECT_ID  (required for Granite)

Never commit API keys.  Configure via environment variables only.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _find_base_scenario() -> Path:
    """Locate mission_data_v3.json relative to the project root."""
    # Try common locations
    candidates = [
        Path("data/scenarios/mission_data_v3.json"),
        Path("../data/scenarios/mission_data_v3.json"),
        Path("ground-control-signal-insight/data/scenarios/mission_data_v3.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        "Could not locate mission_data_v3.json. "
        "Run from the ground-control-signal-insight/ directory."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GCSI Phase 2B Scientific Benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--provider", default="Granite", help="Provider: Granite")
    parser.add_argument(
        "--suite", default="core", choices=["quick", "core", "full"],
        help="Scenario suite: quick (2 scenarios), core (12), full (24)"
    )
    parser.add_argument("--repetitions", type=int, default=5,
                        help="LLM repetitions per scenario")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for results")
    parser.add_argument("--save-prompts", action="store_true",
                        help="Save sanitized prompt content")
    parser.add_argument("--include-ablations", action="store_true",
                        help="Run ablation variants (no-description, no-anomaly-context)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate scenarios and estimate calls without making API calls")
    parser.add_argument("--execute-live", action="store_true",
                        help="Required flag to permit live external API calls")
    parser.add_argument("--candidate-limit", type=int, default=50,
                        help="Maximum candidates per AI call")
    parser.add_argument("--max-attempts", type=int, default=2,
                        help="Maximum retry attempts for transient API failures")
    parser.add_argument("--base-scenario", type=str, default=None,
                        help="Path to base scenario JSON (default: data/scenarios/mission_data_v3.json)")

    args = parser.parse_args(argv)

    # Import here to keep CLI startup fast
    from .scenario_variants import (
        DEFAULT_ANOMALY_MODES,
        DEFAULT_CAPACITY_RATIOS,
        DEFAULT_DEADLINE_SCALES,
        FULL_DEADLINE_SCALES,
        AnomalyMode,
        ScenarioVariantGenerator,
    )
    from .runner import BenchmarkRunner, GraniteBenchmarkProvider, BENCHMARK_VERSION
    from .report import write_benchmark_outputs

    # ---------------------------------------------------------------------------
    # Locate base scenario
    # ---------------------------------------------------------------------------
    if args.base_scenario:
        base_path = Path(args.base_scenario)
    else:
        try:
            base_path = _find_base_scenario()
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1

    # ---------------------------------------------------------------------------
    # Build scenario matrix
    # ---------------------------------------------------------------------------
    deadline_scales = FULL_DEADLINE_SCALES if args.suite == "full" else DEFAULT_DEADLINE_SCALES

    gen = ScenarioVariantGenerator(
        base_scenario_path=base_path,
        capacity_ratios=DEFAULT_CAPACITY_RATIOS,
        anomaly_modes=DEFAULT_ANOMALY_MODES,
        deadline_scales=deadline_scales,
    )
    variants = gen.generate_all()

    # Quick suite: only 2 representative scenarios for infrastructure validation
    if args.suite == "quick":
        quick_ids = {"CAP035_ORIGINAL", "CAP090_ORIGINAL"}
        variants = [v for v in variants if v.spec.scenario_id in quick_ids]
        if not variants:
            variants = variants[:2]  # fallback

    n_scenarios = len(variants)
    ablation_subset_count = 4

    # ---------------------------------------------------------------------------
    # Provider setup
    # ---------------------------------------------------------------------------
    if args.provider.lower() == "granite":
        if not args.dry_run:
            if not os.getenv("GCSI_GRANITE_API_KEY"):
                print(
                    "ERROR: GCSI_GRANITE_API_KEY is not set.\n"
                    "Set the environment variable and retry, or use --dry-run.\n"
                    "Do NOT commit API keys to source control."
                )
                return 1
        provider = GraniteBenchmarkProvider(max_attempts=args.max_attempts)
    else:
        print(f"ERROR: Unknown provider '{args.provider}'. Supported: Granite")
        return 1

    # ---------------------------------------------------------------------------
    # Output directory
    # ---------------------------------------------------------------------------
    import uuid
    from datetime import datetime, timezone
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path("benchmarks/results") / run_id

    # Guard: refuse to overwrite an existing run directory
    if output_dir.exists() and list(output_dir.iterdir()):
        print(f"ERROR: Output directory '{output_dir}' already exists and is non-empty.")
        print("Use a different --output-dir or let the auto-generated run ID create a new one.")
        return 1

    # ---------------------------------------------------------------------------
    # Call count estimate
    # ---------------------------------------------------------------------------
    runner = BenchmarkRunner(
        provider=provider,
        repetitions=args.repetitions,
        candidate_limit=args.candidate_limit,
        dry_run=args.dry_run,
        save_prompts=args.save_prompts,
        output_dir=output_dir if not args.dry_run else None,
        run_id=run_id,
    )

    estimates = runner.estimate_call_count(
        n_scenarios,
        include_ablations=args.include_ablations,
        ablation_scenarios_count=ablation_subset_count,
    )

    print(f"\n{'='*60}")
    print(f"GCSI Phase 2B Benchmark — {BENCHMARK_VERSION}")
    print(f"{'='*60}")
    print(f"Provider:         {args.provider}")
    print(f"Suite:            {args.suite} ({n_scenarios} scenarios)")
    print(f"Repetitions:      {args.repetitions} per scenario")
    print(f"Candidate limit:  {args.candidate_limit}")
    print(f"Base scenario:    {base_path}")
    print(f"Output dir:       {output_dir}")
    print(f"Dry run:          {'YES' if args.dry_run else 'NO'}")
    print(f"Ablations:        {'YES' if args.include_ablations else 'NO'}")
    print(f"\nScenario matrix:")
    for v in variants:
        print(f"  {v.spec.scenario_id:<30} cap={v.spec.actual_capacity_ratio:.3f}")
    print(f"\nExpected external Stage-1 API calls:")
    print(f"  Core:       {estimates['core_calls']}")
    if args.include_ablations:
        print(f"  Ablations:  {estimates['ablation_calls']}")
    print(f"  TOTAL:      {estimates['total_calls']}")
    print(f"{'='*60}\n")

    if args.dry_run:
        print("DRY RUN: Zero external API calls will be made.")
        print("Running deterministic scenario generation and plan construction...")
        # Still generate and evaluate deterministic plans to validate the framework
        from ..telecom.engine import TelecomEngine
        from ..agent.candidate_prioritizer import CandidatePrioritizer
        engine = TelecomEngine()
        for v in variants:
            link_state = engine.compute(v.scenario.link_inputs)
            cp = CandidatePrioritizer(max_candidates=args.candidate_limit)
            candidates = cp.select(
                v.scenario.data_products,
                anomalies=v.scenario.anomalies,
                remaining_window_s=link_state.remaining_window_s,
            )
            from .runner import build_deterministic_plans
            from ..config import SchedulerWeights
            det = build_deterministic_plans(v.scenario, link_state, candidates, SchedulerWeights())
            print(f"  {v.spec.scenario_id}: {len(candidates)} candidates, "
                  f"{len(det)} deterministic plans OK")
        print("\nDRY RUN COMPLETE. No API calls made.")
        return 0

    if not args.execute_live:
        print("LIVE EXECUTION NOT ENABLED.")
        print("Add --execute-live to run the actual benchmark.")
        print(f"Expected total external calls: {estimates['total_calls']}")
        return 0

    # ---------------------------------------------------------------------------
    # Live benchmark execution
    # ---------------------------------------------------------------------------
    print(f"Starting live benchmark... Output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reset runner with output dir
    runner = BenchmarkRunner(
        provider=provider,
        repetitions=args.repetitions,
        candidate_limit=args.candidate_limit,
        dry_run=False,
        save_prompts=args.save_prompts,
        output_dir=output_dir,
        run_id=run_id,
    )

    # Write manifest
    runner.write_manifest(variants, gen.base_sha256)

    # Run matrix
    all_trials = runner.run_matrix(
        variants,
        include_ablations=args.include_ablations,
    )

    # Load results and write outputs
    from .report import load_raw_results
    trials, plan_results = load_raw_results(output_dir)
    write_benchmark_outputs(output_dir, trials, plan_results)

    success_count = sum(1 for t in all_trials if t.status.value == "success")
    print(f"\nBenchmark complete.")
    print(f"  Trials: {len(all_trials)}")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {len(all_trials) - success_count}")
    print(f"  Results: {output_dir}")
    print(f"  Report: {output_dir / 'report.md'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
