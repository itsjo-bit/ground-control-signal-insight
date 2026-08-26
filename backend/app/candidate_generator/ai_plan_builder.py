"""AI-prioritized transmission plan builder.

This module contains the single pure helper :func:`build_ai_prioritized_plan`
that converts a :class:`CandidatePrioritization` result into a full
:class:`CandidatePlan`.

Design principles
-----------------
* No AI calls inside this helper — it is purely deterministic given its inputs.
* No randomness.
* Independently testable.

Ordering policy (Section 4 of the architecture spec)
-----------------------------------------------------
AI-ranked prefix
    Products returned in ``CandidatePrioritization.ranked_products`` are
    placed first, ordered by ``priority`` ascending (1 = highest priority).

Deterministic tail
    Products NOT ranked by AI are appended using the existing
    **BaselineScheduler** order derived from the same full packet set.
    This uses the *same* underlying weighted scoring as the ``baseline`` plan
    so the tail is defensible and reproducible.

De-duplication
    A product may not appear more than once.  AI-ranked products take
    precedence; tail products are only included if they were not already
    placed in the prefix.

Hallucinated IDs
    Any ``product_id`` returned by the AI that does not exist in the
    provided packet set is silently discarded.  The plan must never contain
    packets that were not in the original authoritative set.

Provenance metadata
    The returned plan includes a ``metadata`` dict with keys:
    ``plan_type``, ``candidate_count``, ``ranked_count``,
    ``tail_policy``, ``stage1_provider``, ``fallback_used``.

Example
-------
AI ranked (priority order): A, C, B
Baseline order:              B, D, A, E, C, F

Final AI-prioritized plan:   A, C, B, D, E, F

Packets D, E, F were not ranked by AI → appended in baseline order.
Packets A, B, C appear at AI-assigned positions; they are skipped from tail.
"""

from __future__ import annotations

from ..config import SchedulerWeights
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.packet import Packet
from ..scheduler.baseline import BaselineScheduler

#: Stable plan_id and strategy for the AI-prioritized plan.
AI_PLAN_ID = "ai-prioritized"
AI_PLAN_STRATEGY = "ai_prioritized"


def build_ai_prioritized_plan(
    all_packets: list[Packet],
    prioritization: CandidatePrioritization,
    link_state: LinkState,
    mission_state: MissionState,
    weights: SchedulerWeights | None = None,
    *,
    stage1_provider: str = "unknown",
    fallback_used: bool = False,
) -> CandidatePlan:
    """Construct the AI-prioritized transmission plan.

    Args:
        all_packets:     Full authoritative packet set (150 products in v3).
                         Not mutated.
        prioritization:  Stage-1 AI prioritization result.
                         May rank fewer products than ``all_packets`` (normal).
        link_state:      Current link snapshot — forwarded to BaselineScheduler
                         for the deterministic tail ordering.
        mission_state:   Current mission snapshot — forwarded to BaselineScheduler.
        weights:         Scheduler weights for the deterministic tail.
                         Defaults to ``SchedulerWeights()`` when ``None``.
        stage1_provider: Name of the provider that produced the prioritization
                         (used in provenance metadata).
        fallback_used:   True when a fallback provider was used for Stage 1
                         (used in provenance metadata).

    Returns:
        A :class:`CandidatePlan` with:
        - ``plan_id = "ai-prioritized"``
        - ``strategy = "ai_prioritized"``
        - AI-ranked products at the front (priority 1 first)
        - Unranked products appended in BaselineScheduler order
        - No duplicate packet IDs
        - Every original packet appears exactly once
        - ``metadata`` containing full provenance information

    Notes:
        * AI-ranked IDs that do not appear in ``all_packets`` are discarded
          (hallucination guard).
        * When ``prioritization.ranked_products`` is empty the plan degrades
          gracefully to pure BaselineScheduler ordering.
    """
    if weights is None:
        weights = SchedulerWeights()

    # -- Build lookup: packet_id → Packet ----------------------------------
    pkt_map: dict[str, Packet] = {p.packet_id: p for p in all_packets}

    # -- Sort AI-ranked products by priority ascending (1 = most important) -
    ranked_sorted = sorted(
        prioritization.ranked_products,
        key=lambda rp: rp.priority,
    )

    # -- Build AI prefix: validate IDs, deduplicate -------------------------
    prefix: list[Packet] = []
    seen: set[str] = set()

    for rp in ranked_sorted:
        pid = rp.product_id
        # Hallucination guard: skip IDs that are not in the authoritative set.
        if pid not in pkt_map:
            continue
        # Dedup guard (should never trigger with a well-formed prioritization).
        if pid in seen:
            continue
        prefix.append(pkt_map[pid])
        seen.add(pid)

    # -- Compute deterministic tail ordering via BaselineScheduler -----------
    # Pass the FULL packet set; BaselineScheduler uses only packet attributes
    # and link/mission state, so its output is independent of AI ranking.
    baseline_plan = BaselineScheduler.rank(all_packets, link_state, mission_state, weights)

    # Append tail packets in baseline order, skipping already-seen AI packets.
    tail: list[Packet] = []
    for pkt in baseline_plan.packets:
        if pkt.packet_id not in seen:
            tail.append(pkt)
            seen.add(pkt.packet_id)

    ordered = prefix + tail

    # Sanity check: we must never lose or gain packets.
    assert len(ordered) == len(all_packets), (
        f"AI plan packet count mismatch: expected {len(all_packets)}, "
        f"got {len(ordered)}"
    )

    # -- Build provenance metadata ------------------------------------------
    metadata: dict = {
        "plan_type": "ai_semantic",
        "candidate_count": prioritization.candidate_count,
        "ranked_count": len(prefix),
        "tail_policy": "baseline_scheduler",
        "stage1_provider": stage1_provider,
        "fallback_used": fallback_used,
    }

    return CandidatePlan(
        plan_id=AI_PLAN_ID,
        strategy=AI_PLAN_STRATEGY,
        packets=ordered,
        generated_by="build_ai_prioritized_plan",
        metadata=metadata,
    )
