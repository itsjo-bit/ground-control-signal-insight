"""GCSI Phase 2B.1 Benchmark CLI.

Usage:
    python -m backend.app.benchmark.runner_cli [options]
    python -m backend.app.benchmark [options]

Options:
    --config         Path to benchmark config JSON (default: benchmarks/configs/gcsi_benchmark_v1.json)
    --provider       Provider to use: Granite (default — also from config)
    --suite          Scenario suite: quick|core|full (default: core)
    --repetitions    LLM repetitions per scenario (overrides config; marks run non-preregistered)
    --output-dir     Output directory (default: benchmarks/results/<run-id>/)
    --save-prompts   Save sanitized prompt content to audit/ directory
    --include-ablations  Run ablation variants
    --dry-run        Generate scenarios and show call count without API calls
    --execute-live   Required flag for live API execution
    --candidate-limit  Max candidates per AI call (overrides config; marks run non-preregistered)
    --max-attempts   Max retry attempts for transient failures (overrides config)
    --base-scenario  Path to base scenario JSON
    --run-type       Run type label: core|pilot|dev (default: core)

Safety:
    --dry-run  performs ZERO external API calls.
    Live execution requires --execute-live flag to prevent accidents.
    Expected call counts (logical / normal / max) displayed before live execution.
    Missing GCSI_GRANITE_API_KEY OR GCSI_GRANITE_PROJECT_ID aborts before any trial.

Config provenance:
    If --repetitions or --candidate-limit differ from the config, the run is marked
    preregistered=false with explicit config_overrides recorded.

Provider credentials:
    GCSI_GRANITE_API_KEY     (required for Granite)
    GCSI_GRANITE_PROJECT_ID  (required for Granite)

Never commit API keys.  Configure via environment variables only.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Canonical config path for official benchmark
_CANONICAL_CONFIG = Path("benchmarks/configs/gcsi_benchmark_v1.json")


def _find_base_scenario(base_scenario_arg: str | None = None) -> Path:
    """Locate mission_data_v3.json relative to the project root."""
    if base_scenario_arg:
        p = Path(base_scenario_arg)
        if p.exists():
            return p
        raise FileNotFoundError(f"Base scenario not found at {p}")

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


def _find_config() -> Path | None:
    """Find the canonical benchmark config file."""
    candidates = [
        _CANONICAL_CONFIG,
        Path("../benchmarks/configs/gcsi_benchmark_v1.json"),
        Path("ground-control-signal-insight/benchmarks/configs/gcsi_benchmark_v1.json"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="GCSI Phase 2B.1 Scientific Benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to benchmark config JSON. Defaults to benchmarks/configs/gcsi_benchmark_v1.json"
    )
    parser.add_argument("--provider", default=None, help="Provider: Granite (from config if omitted)")
    parser.add_argument(
        "--suite", default="core", choices=["quick", "core", "full"],
        help="Scenario suite: quick (2 scenarios), core (12), full (24)"
    )
    parser.add_argument(
        "--repetitions", type=int, default=None,
        help="LLM repetitions per scenario (overrides config; marks run non-preregistered)"
    )
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory for results")
    parser.add_argument("--save-prompts", action="store_true",
                        help="Save sanitized prompt/response audit files")
    parser.add_argument("--include-ablations", action="store_true",
                        help="Run ablation variants")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate scenarios and estimate calls without making API calls")
    parser.add_argument("--execute-live", action="store_true",
                        help="Required flag to permit live external API calls")
    parser.add_argument(
        "--candidate-limit", type=int, default=None,
        help="Maximum candidates per AI call (overrides config; marks run non-preregistered)"
    )
    parser.add_argument(
        "--max-attempts", type=int, default=None,
        help="Maximum retry attempts for transient API failures (overrides config)"
    )
    parser.add_argument("--base-scenario", type=str, default=None,
                        help="Path to base scenario JSON")
    parser.add_argument(
        "--run-type", type=str, default="core", choices=["core", "pilot", "dev"],
        help="Run type label: core|pilot|dev"
    )

    args = parser.parse_args(argv)

    # Import here to keep CLI startup fast
    from .models import BenchmarkConfig
    from .runner import (
        BENCHMARK_VERSION,
        BenchmarkRunner,
        DEFAULT_BENCHMARK_CONFIG_PATH,
        GraniteBenchmarkProvider,
    )
    from .scenario_variants import (
        DEFAULT_ANOMALY_MODES,
        DEFAULT_CAPACITY_RATIOS,
        DEFAULT_DEADLINE_SCALES,
        FULL_DEADLINE_SCALES,
        AnomalyMode,
        ScenarioVariantGenerator,
    )
    from .report import write_benchmark_outputs, load_raw_results

    # ---------------------------------------------------------------------------
    # Load benchmark config
    # ---------------------------------------------------------------------------
    config_path: Path | None = None
    benchmark_cfg: BenchmarkConfig | None = None

    if args.config:
        config_path = Path(args.config)
    else:
        config_path = _find_config()

    if config_path and config_path.exists():
        try:
            benchmark_cfg = BenchmarkConfig.from_file(config_path)
        except Exception as exc:
            print(f"ERROR: Failed to load/validate benchmark config '{config_path}': {exc}")
            return 1
    else:
        logger.warning(
            "No benchmark config found at default path. Proceeding with CLI defaults. "
            "Run is NOT preregistered."
        )

    # Extract config values
    cfg_repetitions = benchmark_cfg.repetitions if benchmark_cfg else 5
    cfg_candidate_limit = benchmark_cfg.candidate_limit if benchmark_cfg else 50
    cfg_max_attempts = benchmark_cfg.retry_policy.max_attempts if benchmark_cfg else 2
    cfg_delay_s = benchmark_cfg.retry_policy.delay_between_attempts_s if benchmark_cfg else 1.0
    cfg_model = benchmark_cfg.model if benchmark_cfg else "ibm/granite-4-h-small"
    cfg_provider = benchmark_cfg.provider if benchmark_cfg else "Granite"
    cfg_capacity_ratios = benchmark_cfg.capacity_ratios if benchmark_cfg else list(DEFAULT_CAPACITY_RATIOS)
    cfg_anomaly_modes_str = benchmark_cfg.anomaly_modes if benchmark_cfg else [m.value for m in DEFAULT_ANOMALY_MODES]

    # Resolve anomaly modes
    anomaly_mode_map = {"ORIGINAL": AnomalyMode.ORIGINAL, "NOANOM": AnomalyMode.NO_ANOMALY, "DECOY": AnomalyMode.RESOLVED_DECOY}
    cfg_anomaly_modes = [anomaly_mode_map[m] for m in cfg_anomaly_modes_str if m in anomaly_mode_map]

    # Detect CLI overrides (mark non-preregistered if any differ from config)
    config_overrides: dict = {}

    effective_repetitions = args.repetitions if args.repetitions is not None else cfg_repetitions
    if args.repetitions is not None and benchmark_cfg and args.repetitions != cfg_repetitions:
        config_overrides["repetitions"] = {
            "configured": cfg_repetitions,
            "executed": args.repetitions,
        }

    effective_candidate_limit = args.candidate_limit if args.candidate_limit is not None else cfg_candidate_limit
    if args.candidate_limit is not None and benchmark_cfg and args.candidate_limit != cfg_candidate_limit:
        config_overrides["candidate_limit"] = {
            "configured": cfg_candidate_limit,
            "executed": args.candidate_limit,
        }

    effective_max_attempts = args.max_attempts if args.max_attempts is not None else cfg_max_attempts
    if args.max_attempts is not None and benchmark_cfg and args.max_attempts != cfg_max_attempts:
        config_overrides["max_attempts"] = {
            "configured": cfg_max_attempts,
            "executed": args.max_attempts,
        }

    effective_provider = args.provider or cfg_provider

    # ---------------------------------------------------------------------------
    # Locate base scenario
    # ---------------------------------------------------------------------------
    try:
        base_path = _find_base_scenario(
            benchmark_cfg.base_scenario if benchmark_cfg else args.base_scenario
        )
    except FileNotFoundError:
        # Try the CLI override path
        try:
            base_path = _find_base_scenario(args.base_scenario)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1

    # ---------------------------------------------------------------------------
    # Build scenario matrix
    # ---------------------------------------------------------------------------
    deadline_scales = FULL_DEADLINE_SCALES if args.suite == "full" else DEFAULT_DEADLINE_SCALES

    gen = ScenarioVariantGenerator(
        base_scenario_path=base_path,
        capacity_ratios=cfg_capacity_ratios,
        anomaly_modes=cfg_anomaly_modes,
        deadline_scales=deadline_scales,
    )
    variants = gen.generate_all()

    # Quick suite: only 2 representative scenarios for infrastructure validation
    if args.suite == "quick":
        quick_ids = {"CAP035_ORIGINAL", "CAP090_ORIGINAL"}
        filtered = [v for v in variants if v.spec.scenario_id in quick_ids]
        variants = filtered if filtered else variants[:2]

    n_scenarios = len(variants)
    ablation_subset_count = len(
        benchmark_cfg.ablation_configuration.ablation_scenarios
    ) if benchmark_cfg else 4

    # ---------------------------------------------------------------------------
    # Provider setup
    # ---------------------------------------------------------------------------
    if effective_provider.lower() == "granite":
        if not args.dry_run:
            # Pre-flight credential check — refuse before any trial
            api_key = os.getenv("GCSI_GRANITE_API_KEY", "")
            project_id = os.getenv("GCSI_GRANITE_PROJECT_ID", "")
            missing = []
            if not api_key:
                missing.append("GCSI_GRANITE_API_KEY")
            if not project_id:
                missing.append("GCSI_GRANITE_PROJECT_ID")
            if missing:
                print(
                    f"ERROR: The following required environment variables are not set:\n"
                    + "\n".join(f"  - {m}" for m in missing)
                    + "\n\nSet them and retry, or use --dry-run.\n"
                    "Do NOT commit credentials to source control."
                )
                return 1
        provider = GraniteBenchmarkProvider(
            max_attempts=effective_max_attempts,
            delay_s=cfg_delay_s,
            config_model_id=cfg_model,
        )
    else:
        print(f"ERROR: Unknown provider '{effective_provider}'. Supported: Granite")
        return 1

    # Model identity check for official runs
    if not args.dry_run and benchmark_cfg and not config_overrides:
        actual_model = provider.model_id
        if actual_model not in ("unknown", cfg_model):
            # Model mismatch on what would be a preregistered run
            env_model = os.getenv("GCSI_GRANITE_MODEL_ID", "")
            if env_model and env_model != cfg_model:
                print(
                    f"WARNING: Config requires model '{cfg_model}' but "
                    f"GCSI_GRANITE_MODEL_ID={env_model!r}.\n"
                    "This run is marked NON-PREREGISTERED due to model mismatch."
                )
                config_overrides["model"] = {
                    "configured": cfg_model,
                    "executed": env_model,
                }

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
        repetitions=effective_repetitions,
        candidate_limit=effective_candidate_limit,
        dry_run=args.dry_run,
        save_prompts=args.save_prompts,
        output_dir=output_dir if not args.dry_run else None,
        run_id=run_id,
        benchmark_config=benchmark_cfg,
        config_overrides=config_overrides,
        run_type=args.run_type,
    )

    estimates = runner.estimate_call_count(
        n_scenarios,
        include_ablations=args.include_ablations,
        ablation_scenarios_count=ablation_subset_count,
    )

    # ---------------------------------------------------------------------------
    # Print pre-run summary
    # ---------------------------------------------------------------------------
    is_preregistered = len(config_overrides) == 0
    config_sha = benchmark_cfg.config_sha256 if benchmark_cfg else "N/A"
    config_file_sha = benchmark_cfg.compute_file_sha256(config_path) if (benchmark_cfg and config_path) else "N/A"

    print(f"\n{'='*60}")
    print(f"GCSI Phase 2B.1 Benchmark — {BENCHMARK_VERSION}")
    print(f"{'='*60}")
    print(f"Config file:      {config_path or '(none — using defaults)'}")
    print(f"Config file SHA:  {config_file_sha}")
    print(f"Config model SHA: {config_sha}")
    print(f"Preregistered:    {is_preregistered}")
    if config_overrides:
        print(f"Config overrides: {config_overrides}")
    print(f"Run type:         {args.run_type}")
    print(f"Provider:         {effective_provider}")
    print(f"Model (config):   {cfg_model}")
    print(f"Suite:            {args.suite} ({n_scenarios} scenarios)")
    print(f"Repetitions:      {effective_repetitions} per scenario")
    print(f"Candidate limit:  {effective_candidate_limit}")
    print(f"Max attempts:     {effective_max_attempts}")
    print(f"Retry delay (s):  {cfg_delay_s}")
    print(f"Base scenario:    {base_path}")
    print(f"Output dir:       {output_dir}")
    print(f"Dry run:          {'YES' if args.dry_run else 'NO'}")
    print(f"Save prompts:     {'YES' if args.save_prompts else 'NO'}")
    print(f"Ablations:        {'YES' if args.include_ablations else 'NO'}")
    print(f"\nScenario matrix:")
    for v in variants:
        print(f"  {v.spec.scenario_id:<30} cap={v.spec.actual_capacity_ratio:.3f}")
    print(f"\nCall count estimate:")
    print(f"  Logical trials:              {estimates['logical_trials']}")
    print(f"  Normal provider calls:       {estimates['normal_provider_calls']}")
    print(f"  Max provider attempts:       {estimates['max_provider_attempts']}  (if max_attempts={effective_max_attempts})")
    if args.include_ablations:
        print(f"  (Core: {estimates['core_trials']}, Ablations: {estimates['ablation_trials']})")
    print(f"{'='*60}\n")

    # ---------------------------------------------------------------------------
    # Dry run
    # ---------------------------------------------------------------------------
    if args.dry_run:
        print("DRY RUN: Zero external API calls will be made.")
        print("Running deterministic scenario generation and plan construction...")
        from ..telecom.engine import TelecomEngine
        from ..agent.candidate_prioritizer import CandidatePrioritizer
        from .runner import build_deterministic_plans
        from ..config import SchedulerWeights

        engine = TelecomEngine()
        for v in variants:
            link_state = engine.compute(v.scenario.link_inputs)
            cp = CandidatePrioritizer(max_candidates=effective_candidate_limit)
            candidates = cp.select(
                v.scenario.data_products,
                anomalies=v.scenario.anomalies,
                remaining_window_s=link_state.remaining_window_s,
            )
            det = build_deterministic_plans(v.scenario, link_state, candidates, SchedulerWeights())
            print(f"  {v.spec.scenario_id}: {len(candidates)} candidates, "
                  f"{len(det)} deterministic plans OK")
        print("\nDRY RUN COMPLETE. No API calls made.")
        return 0

    if not args.execute_live:
        print("LIVE EXECUTION NOT ENABLED.")
        print("Add --execute-live to run the actual benchmark.")
        print(f"\nExpected call counts:")
        print(f"  Logical trials:         {estimates['logical_trials']}")
        print(f"  Normal provider calls:  {estimates['normal_provider_calls']}")
        print(f"  Max provider attempts:  {estimates['max_provider_attempts']}")
        return 0

    # ---------------------------------------------------------------------------
    # Live benchmark execution
    # ---------------------------------------------------------------------------
    print(f"Starting live benchmark... Output: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Reset runner with output dir
    runner = BenchmarkRunner(
        provider=provider,
        repetitions=effective_repetitions,
        candidate_limit=effective_candidate_limit,
        dry_run=False,
        save_prompts=args.save_prompts,
        output_dir=output_dir,
        run_id=run_id,
        benchmark_config=benchmark_cfg,
        config_overrides=config_overrides,
        run_type=args.run_type,
    )

    # Write manifest BEFORE live trials
    manifest = runner.write_manifest(
        variants,
        gen.base_sha256,
        run_type=args.run_type,
        preregistered=is_preregistered,
    )

    # Write effective config snapshot
    runner.write_effective_config(benchmark_cfg)

    # Run matrix
    try:
        all_trials = runner.run_matrix(
            variants,
            include_ablations=args.include_ablations,
        )
        runner.finalize_manifest(manifest, status="completed")
    except KeyboardInterrupt:
        runner.finalize_manifest(manifest, status="aborted")
        print("\nBenchmark aborted by user. Partial results saved.")
        return 1
    except Exception as exc:
        runner.finalize_manifest(manifest, status="partial")
        logger.error("Benchmark failed: %s", exc)
        print(f"\nBenchmark failed: {exc}")
        raise

    # Load results and write outputs
    trials, plan_results = load_raw_results(output_dir)
    write_benchmark_outputs(output_dir, trials, plan_results, manifest)

    success_count = sum(1 for t in all_trials if t.status.value == "success")
    print(f"\nBenchmark complete.")
    print(f"  Trials: {len(all_trials)}")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {len(all_trials) - success_count}")
    print(f"  Results: {output_dir}")
    print(f"  Report: {output_dir / 'report.md'}")
    if not is_preregistered:
        print(f"\n  NOTE: This run is NON-PREREGISTERED (config overrides applied).")
        print(f"  Overrides: {config_overrides}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
