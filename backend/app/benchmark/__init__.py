"""GCSI Scientific Benchmark — Phase 2B.

Reproducible scientific comparison of:
  CLASSICAL:          baseline | deadline-first | mission-critical-first | value-per-cost
  SEMANTIC CONTROL:   semantic-rule-based
  LLM:                llm-semantic / ai-prioritized (IBM Granite primary)

Evaluation is always by the same deterministic PlanEvaluator and
MissionOutcomeEvaluator.  No AI-specific scoring.  No Local fallback
counted as Granite.  No composite AI score.

See docs/benchmark_methodology.md for full protocol.
"""
