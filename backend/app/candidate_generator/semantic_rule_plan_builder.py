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
from ..scheduler.baseline import BaselineScheduler

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

    Uses :class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
    to rank the candidate set, then appends the tail in BaselineScheduler order.
    This mirrors exactly what :func:`build_ai_prioritized_plan` does when using
    an LLM prioritization, ensuring a fair comparison.

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
    """
    if weights is None:
        weights = SchedulerWeights()

    # Build packet lookup
    pkt_map: dict[str, Packet] = {p.packet_id: p for p in all_packets}

    # Rank candidates using the deterministic semantic rule prioritizer
    prioritizer = SemanticRulePrioritizer()
    prioritization = prioritizer.prioritize(candidates, anomalies=anomalies)

    # Build prefix from semantic-rule ranking (same logic as build_ai_prioritized_plan)
    ranked_sorted = sorted(
        prioritization.ranked_products,
        key=lambda rp: rp.priority,
    )

    prefix: list[Packet] = []
    seen: set[str] = set()
    for rp in ranked_sorted:
        pid = rp.product_id
        if pid not in pkt_map:
            continue
        if pid in seen:
            continue
        prefix.append(pkt_map[pid])
        seen.add(pid)

    # Compute deterministic tail via BaselineScheduler
    baseline_plan = BaselineScheduler.rank(all_packets, link_state, mission_state, weights)
    tail: list[Packet] = []
    for pkt in baseline_plan.packets:
        if pkt.packet_id not in seen:
            tail.append(pkt)
            seen.add(pkt.packet_id)

    ordered = prefix + tail

    metadata: dict = {
        "plan_type": "semantic_rule_based",
        "candidate_count": len(candidates),
        "ranked_count": len(prefix),
        "tail_policy": "baseline_scheduler",
        "comparator": "SemanticRulePrioritizer",
        "benchmark_only": True,
    }

    return CandidatePlan(
        plan_id=SEMANTIC_RULE_PLAN_ID,
        strategy=SEMANTIC_RULE_PLAN_STRATEGY,
        packets=ordered,
        generated_by="build_semantic_rule_plan",
        metadata=metadata,
    )
