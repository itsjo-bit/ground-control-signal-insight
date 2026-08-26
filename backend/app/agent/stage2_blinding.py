"""Stage-2 provenance blinding for external AI providers.

This module hides the real plan identity from external LLMs during Stage-2
plan recommendation.  External providers receive **opaque option aliases**
(``OPTION-A``, ``OPTION-B``, ...) rather than real plan identifiers such as
``"ai-prioritized"`` or ``"baseline"``.

Motivation
----------
External LLMs can recognise their own output from labels like
``"ai-prioritized"`` or strategy names.  This creates:

* **self-preference bias** — the model may favour the plan it or its
  architecture produced.
* **provenance bias** — "AI-prioritized" may sound more sophisticated and
  receive higher recommendation rates regardless of actual metrics.
* **automation bias** — the model may defer to the AI plan without reasoning
  about comparative metrics.

By replacing real plan identities with neutral aliases, external Stage-2
reasoning is forced to rely only on objective evaluation metrics.

Deterministic mapping
----------------------
Alias assignment uses a SHA-256-based stable hash::

    alias_rank = SHA-256(scenario_id + plan_id)[:8]  (hex, compare as int)

This ensures:
* same scenario + same plans → same aliases on every run
* AI plan is not guaranteed to receive any particular alias
* aliases reveal no information about provenance

The mapping is maintained purely inside trusted backend logic and is never
exposed to the external provider.

Compact plan summary
--------------------
Instead of serialising the full 150-packet plan (which would multiply by 5
plans = 750 packet structs), each plan is summarised into a
:class:`Stage2PlanSummary` containing only evaluation metrics.

The external LLM receives only the alias → summary mapping.  It selects an
alias.  The backend translates the alias back to the real plan.

Invalid-alias rejection
-----------------------
If the external provider returns an alias not in the current mapping,
:func:`map_alias_to_plan_id` raises :class:`InvalidStage2AliasError`.

Provenance-leak detection
--------------------------
:func:`assert_no_provenance_leak` checks the serialized context for known
provenance strings.  Use it in tests to verify the blinding works.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult

# Strings that must NEVER appear in a provenance-blind Stage-2 context.
_FORBIDDEN_PROVENANCE_STRINGS = frozenset([
    "ai-prioritized",
    "ai_prioritized",
    "baseline",
    "deadline-first",
    "deadline_first",
    "mission-critical-first",
    "mission_critical_first",
    "value-per-cost",
    "value_per_cost",
    "generated_by",
    "plan_type",
    "stage1_provider",
    "fallback_used",
    "ai_semantic",
])


class InvalidStage2AliasError(Exception):
    """Raised when an external provider returns an alias not in the mapping."""


# ---------------------------------------------------------------------------
# Compact plan summary sent to Stage-2 LLM
# ---------------------------------------------------------------------------


class Stage2PlanSummary(BaseModel):
    """Compact, provenance-free summary sent to external Stage-2 providers.

    Contains only evaluation metrics — no plan identity, strategy, or
    packet lists.  The ``option_id`` is the opaque alias assigned by the
    blinding layer (e.g. ``"OPTION-A"``).
    """

    option_id: str = Field(description="Opaque alias assigned by the provenance-blinding layer")

    # Physical / telecom metrics (from PlanEvaluator)
    total_packets: int = Field(ge=0)
    deferred_count: int = Field(ge=0)
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_level: str = Field(description="Categorical risk level (LOW/MEDIUM/HIGH/CRITICAL)")
    mission_value: float = Field(ge=0.0)
    critical_packets_delivered: int = Field(ge=0)
    total_critical_packets: int = Field(ge=0)
    deadline_misses: int = Field(ge=0)
    deadline_miss_rate: float = Field(ge=0.0, le=1.0)
    bandwidth_utilization: float = Field(ge=0.0, le=1.0)
    retransmission_overhead: float = Field(ge=0.0)
    window_pressure: float = Field(ge=0.0, le=1.0)

    # Semantic mission outcome metrics (from MissionOutcomeEvaluator) — all optional
    delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    scientific_value_capture_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    required_delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    active_anomaly_delivery_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    high_severity_anomaly_coverage_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    anomaly_weighted_coverage: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    average_delivered_age_s: Optional[float] = Field(default=None, ge=0.0)


# ---------------------------------------------------------------------------
# Blinding layer
# ---------------------------------------------------------------------------


def _stable_alias_rank(scenario_id: str, plan_id: str) -> int:
    """Return a stable integer used to assign alias order.

    Uses SHA-256 to produce a consistent hash across Python processes.
    (Python's built-in ``hash()`` is randomized per-process — not usable here.)
    """
    key = f"{scenario_id}:{plan_id}".encode()
    digest = hashlib.sha256(key).hexdigest()
    return int(digest[:16], 16)  # first 8 bytes (16 hex chars) as int


def build_blind_mapping(
    plans: Sequence[CandidatePlan],
    scenario_id: str = "default",
) -> dict[str, str]:
    """Build a deterministic alias → real_plan_id mapping.

    Args:
        plans:       List of candidate plans to anonymise.
        scenario_id: Scenario identifier used to stabilise alias ordering.
                     Using the same scenario_id with the same plans always
                     produces the same aliases.

    Returns:
        Dict mapping ``"OPTION-A"`` → real ``plan_id``, etc.
        The mapping is ordered by stable hash so the AI plan does not
        always receive a predictable alias.
    """
    if not plans:
        return {}

    # Sort plans by stable hash rank
    sorted_plans = sorted(
        plans,
        key=lambda p: _stable_alias_rank(scenario_id, p.plan_id),
    )

    option_labels = [f"OPTION-{chr(65 + i)}" for i in range(len(sorted_plans))]
    return {label: plan.plan_id for label, plan in zip(option_labels, sorted_plans)}


def map_alias_to_plan_id(alias: str, alias_map: dict[str, str]) -> str:
    """Translate an opaque alias returned by an external provider to the real plan_id.

    Args:
        alias:     The alias returned by the external provider, e.g. ``"OPTION-C"``.
        alias_map: The mapping returned by :func:`build_blind_mapping`.

    Returns:
        The real ``plan_id`` corresponding to the alias.

    Raises:
        InvalidStage2AliasError: If the alias is not in the mapping.  This
            includes the case where the provider leaked a real plan name such as
            ``"ai-prioritized"`` instead of the assigned alias.
    """
    real_id = alias_map.get(alias)
    if real_id is None:
        valid = sorted(alias_map.keys())
        raise InvalidStage2AliasError(
            f"External provider returned invalid option alias '{alias}'. "
            f"Valid aliases: {valid}. "
            "If the provider returned a real plan name, this indicates a provenance leak."
        )
    return real_id


def build_stage2_summaries(
    alias_map: dict[str, str],
    plans: Sequence[CandidatePlan],
    evaluations: Sequence[EvaluationResult],
    mission_outcomes: "Sequence[MissionOutcomeResult] | None" = None,
) -> list[Stage2PlanSummary]:
    """Build compact plan summaries for external Stage-2 providers.

    Args:
        alias_map:        Alias → real plan_id mapping from :func:`build_blind_mapping`.
        plans:            All candidate plans (used to get packet count).
        evaluations:      Deterministic PlanEvaluator results.
        mission_outcomes: Optional MissionOutcomeEvaluator results.  When provided,
                          semantic metrics are included in the summaries.

    Returns:
        List of :class:`Stage2PlanSummary` objects keyed by their alias.
    """
    # Build lookups keyed by real plan_id
    plan_map: dict[str, CandidatePlan] = {p.plan_id: p for p in plans}
    eval_map: dict[str, EvaluationResult] = {e.plan_id: e for e in evaluations}

    # Build mission outcome lookup if provided
    from ..evaluator.mission_outcome_evaluator import MissionOutcomeResult
    outcome_map: dict[str, MissionOutcomeResult] = {}
    if mission_outcomes:
        outcome_map = {mo.plan_id: mo for mo in mission_outcomes}

    summaries: list[Stage2PlanSummary] = []
    for alias, real_plan_id in alias_map.items():
        plan = plan_map.get(real_plan_id)
        ev = eval_map.get(real_plan_id)
        if plan is None or ev is None:
            continue  # should not happen in normal operation

        mo = outcome_map.get(real_plan_id)

        summaries.append(Stage2PlanSummary(
            option_id=alias,
            # Physical metrics
            total_packets=len(plan.packets),
            deferred_count=len(ev.deferred_packets),
            risk_score=ev.risk_score,
            risk_level=ev.risk_level.value,
            mission_value=ev.mission_value,
            critical_packets_delivered=ev.critical_packets_delivered,
            total_critical_packets=ev.total_critical_packets,
            deadline_misses=ev.deadline_misses,
            deadline_miss_rate=ev.deadline_miss_rate,
            bandwidth_utilization=ev.bandwidth_utilization,
            retransmission_overhead=ev.retransmission_overhead,
            window_pressure=ev.window_pressure,
            # Semantic metrics from MissionOutcomeEvaluator (when available)
            delivery_rate=mo.delivery_rate if mo else None,
            scientific_value_capture_rate=mo.scientific_value_capture_rate if mo else None,
            required_delivery_rate=mo.required_delivery_rate if mo else None,
            active_anomaly_delivery_rate=mo.active_anomaly_delivery_rate if mo else None,
            high_severity_anomaly_coverage_rate=mo.high_severity_anomaly_coverage_rate if mo else None,
            anomaly_weighted_coverage=mo.anomaly_weighted_coverage if mo else None,
            average_delivered_age_s=mo.average_delivered_age_s if mo else None,
        ))

    return summaries


def build_blind_context_json(summaries: list[Stage2PlanSummary]) -> str:
    """Serialise Stage-2 summaries as a compact JSON string (no provenance).

    The resulting JSON maps option alias → metrics dict.  It never contains
    plan identifiers, strategy names, or any other provenance information.

    Args:
        summaries: List produced by :func:`build_stage2_summaries`.

    Returns:
        Compact JSON string safe for inclusion in external LLM prompts.
    """
    ctx: dict[str, dict] = {}
    for s in summaries:
        ctx[s.option_id] = {
            k: v
            for k, v in s.model_dump(mode="json").items()
            if k != "option_id" and v is not None
        }
    return json.dumps(ctx, indent=2)


def assert_no_provenance_leak(context_json: str) -> None:
    """Assert that a Stage-2 context JSON contains no known provenance strings.

    Raises:
        AssertionError: if any forbidden provenance string is found.

    Args:
        context_json: The serialized Stage-2 context to inspect.
    """
    lower = context_json.lower()
    found = [s for s in _FORBIDDEN_PROVENANCE_STRINGS if s.lower() in lower]
    if found:
        raise AssertionError(
            f"Stage-2 context contains forbidden provenance strings: {found}. "
            "This would allow an external LLM to identify plan provenance."
        )
