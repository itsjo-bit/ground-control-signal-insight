"""Semantic-rule-based comparator plan builder — benchmark infrastructure.

This module provides :func:`build_semantic_rule_plan` which constructs a
``CandidatePlan`` using the deterministic
:class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
instead of an LLM.

Purpose
-------
The ``semantic-rule-based`` plan is a **scientific comparator**, not a
normal operational plan.  It is used for:

* ablation experiments
* benchmarking LLM prioritization against a structured-rule baseline
* proving the LLM provides genuine value over deterministic heuristics

Implementation
--------------
This is a thin wrapper around the shared
:func:`~backend.app.candidate_generator.ranked_prefix_builder.build_ranked_prefix_plan`
function.  Both :func:`build_semantic_rule_plan` and
:func:`~backend.app.candidate_generator.ai_plan_builder.build_ai_prioritized_plan`
delegate to the same ``build_ranked_prefix_plan`` so the plan construction
mechanics are provably identical.

Construction policy (intentionally identical to the LLM plan)
--------------------------------------------------------------
::

    structured rule-based ranked prefix  (SemanticRulePrioritizer)
    +
    BaselineScheduler deterministic tail

This is exactly the same policy as :func:`build_ai_prioritized_plan` so
the only experimental difference between the two plans is:

    LLM semantic reasoning
    VS
    deterministic structured semantic heuristic

Both receive:
* same CandidatePrioritizer screening
* same bounded candidate set
* same authoritative structured metadata
* same deterministic tail policy
* same PlanEvaluator
* same MissionOutcomeEvaluator
* same duplicate-ID / completeness invariant checks (via shared builder)

Do NOT add this plan to the normal 5-plan operator workflow yet.

Plan identity
-------------
``plan_id = "semantic-rule-based"``
``strategy = "semantic_rule_based"``
"""

from __future__ import annotations

from ..agent.semantic_rule_prioritizer import SemanticRulePrioritizer
from ..config import SchedulerWeights
from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_summary import CandidateSummary
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.packet import Packet
from .ranked_prefix_builder import SharedPlanBuildError, build_ranked_prefix_plan

#: Stable plan_id and strategy for the semantic-rule-based comparator plan.
SEMANTIC_RULE_PLAN_ID = "semantic-rule-based"
SEMANTIC_RULE_PLAN_STRATEGY = "semantic_rule_based"


def build_semantic_rule_plan(
    all_packets: list[Packet],
    candidates: list[CandidateSummary],
    anomalies: list[AnomalyEvent],
    link_state: LinkState,
    mission_state: MissionState,
    weights: SchedulerWeights | None = None,
) -> CandidatePlan:
    """Build a deterministic semantic-rule comparator plan.

    Thin wrapper around :func:`~backend.app.candidate_generator.ranked_prefix_builder.build_ranked_prefix_plan`.
    Uses :class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
    to rank the candidate set, then delegates to the same shared builder used by
    :func:`~backend.app.candidate_generator.ai_plan_builder.build_ai_prioritized_plan`.

    The only experimental difference between the AI plan and this comparator plan
    is the ranking source:

    * AI plan: LLM output
    * Semantic-rule plan: deterministic structured heuristic

    Both use identical plan-construction mechanics, identical invariant checks,
    and are evaluated by the same PlanEvaluator and MissionOutcomeEvaluator.

    Args:
        all_packets:   Full authoritative packet set.
        candidates:    Pre-screened :class:`CandidateSummary` list (same set
                       the LLM would receive).
        anomalies:     Active anomaly events.
        link_state:    Current link snapshot (used for BaselineScheduler tail).
        mission_state: Current mission snapshot.
        weights:       Scheduler weights for the deterministic tail.
                       Defaults to ``SchedulerWeights()``.

    Returns:
        A :class:`CandidatePlan` with:
        - ``plan_id = "semantic-rule-based"``
        - ``strategy = "semantic_rule_based"``
        - Semantic-rule ranked products first (highest priority first)
        - Unranked products appended in BaselineScheduler order
        - No duplicate packet IDs
        - Every original packet appears exactly once
        - ``metadata`` with provenance information

    Raises:
        SharedPlanBuildError:
            * Duplicate IDs in ``all_packets``
            * Output count or ID set mismatch
    """
    # Rank candidates using the deterministic semantic rule prioritizer
    prioritizer = SemanticRulePrioritizer()
    prioritization = prioritizer.prioritize(candidates, anomalies=anomalies)

    metadata: dict = {
        "plan_type": "semantic_rule_based",
        "candidate_count": len(candidates),
        "ranked_count": len(prioritization.ranked_products),
        "tail_policy": "baseline_scheduler",
        "comparator": "SemanticRulePrioritizer",
        "benchmark_only": True,
    }

    return build_ranked_prefix_plan(
        all_packets=all_packets,
        prioritization=prioritization,
        link_state=link_state,
        mission_state=mission_state,
        weights=weights,
        plan_id=SEMANTIC_RULE_PLAN_ID,
        strategy=SEMANTIC_RULE_PLAN_STRATEGY,
        generated_by="build_semantic_rule_plan",
        metadata=metadata,
    )
