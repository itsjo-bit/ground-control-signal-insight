"""Shared ranked-prefix plan builder — core plan construction logic.

This module provides :func:`build_ranked_prefix_plan`, the single canonical
helper that builds a transmission plan from a ranked prefix of products followed
by a deterministic BaselineScheduler tail.

Both :func:`~backend.app.candidate_generator.ai_plan_builder.build_ai_prioritized_plan`
and :func:`~backend.app.candidate_generator.semantic_rule_plan_builder.build_semantic_rule_plan`
are thin wrappers around this function.  Having a single implementation guarantees:

* identical plan-construction mechanics for AI and semantic-rule plans
* identical safeguards (duplicate ID rejection, completeness invariants)
* benchmark fairness — the only experimental difference is the ranking source

Construction policy
-------------------
::

    ranked prefix  (products in priority order 1..N)
    +
    BaselineScheduler deterministic tail (all unranked products)

De-duplication
    Products may not appear more than once.  Ranked products take
    precedence; tail products are only included if not already in the prefix.

Hallucination guard
    Any ranked ``product_id`` that does not exist in ``all_packets`` is
    silently discarded.

Completeness invariant
    ``len(output) == len(all_packets)`` and ``output_id_set == input_id_set``.
    Violations raise :class:`SharedPlanBuildError`.
"""

from __future__ import annotations

from ..config import SchedulerWeights
from ..models.candidate_plan import CandidatePlan
from ..models.candidate_prioritization import CandidatePrioritization
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..models.packet import Packet
from ..scheduler.baseline import BaselineScheduler


class SharedPlanBuildError(Exception):
    """Raised when the shared plan builder detects an invariant violation.

    Never silently ignored — always indicates corrupted or inconsistent input.
    """


def build_ranked_prefix_plan(
    all_packets: list[Packet],
    prioritization: CandidatePrioritization,
    link_state: LinkState,
    mission_state: MissionState,
    weights: SchedulerWeights | None = None,
    *,
    plan_id: str,
    strategy: str,
    generated_by: str,
    metadata: dict,
) -> CandidatePlan:
    """Build a transmission plan from a ranked prefix + BaselineScheduler tail.

    This is the single canonical plan-construction function used by both the AI
    plan builder and the semantic-rule plan builder.  The only difference between
    those two plans is the ``prioritization`` input (LLM vs deterministic rule).

    Args:
        all_packets:     Full authoritative packet set.  Not mutated.
        prioritization:  Ranked product list (any source).  May rank fewer
                         products than ``all_packets`` (normal).
        link_state:      Current link snapshot (forwarded to BaselineScheduler
                         for the deterministic tail ordering).
        mission_state:   Current mission snapshot.
        weights:         Scheduler weights for the deterministic tail.
                         Defaults to ``SchedulerWeights()`` when ``None``.
        plan_id:         The ``plan_id`` for the resulting :class:`CandidatePlan`.
        strategy:        The ``strategy`` field for the resulting plan.
        generated_by:    The ``generated_by`` field for the resulting plan.
        metadata:        Arbitrary provenance metadata dict.

    Returns:
        A :class:`CandidatePlan` with:

        * ``plan_id``, ``strategy``, ``generated_by``, ``metadata`` from args
        * Ranked products at the front (priority 1 first)
        * Unranked products appended in BaselineScheduler order
        * No duplicate packet IDs
        * Every original packet appears exactly once

    Raises:
        SharedPlanBuildError:
            * Duplicate IDs in ``all_packets``
            * Output packet count ≠ input packet count
            * Output ID set ≠ input ID set

    Notes:
        * Ranked IDs absent from ``all_packets`` are silently discarded
          (hallucination guard).
        * When ``prioritization.ranked_products`` is empty, the plan
          degrades gracefully to pure BaselineScheduler ordering.
    """
    if weights is None:
        weights = SchedulerWeights()

    # ── 1. Validate: no duplicate authoritative packet IDs ────────────────────
    seen_input: set[str] = set()
    dupe_ids: list[str] = []
    for p in all_packets:
        if p.packet_id in seen_input:
            dupe_ids.append(p.packet_id)
        seen_input.add(p.packet_id)
    if dupe_ids:
        raise SharedPlanBuildError(
            f"Authoritative all_packets contains duplicate packet IDs: {sorted(set(dupe_ids))}. "
            "This indicates corrupted input data — the plan cannot be built safely."
        )

    # ── 2. Build packet lookup ────────────────────────────────────────────────
    pkt_map: dict[str, Packet] = {p.packet_id: p for p in all_packets}

    # ── 3. Sort ranked products by priority (1 = highest priority) ───────────
    ranked_sorted = sorted(
        prioritization.ranked_products,
        key=lambda rp: rp.priority,
    )

    # ── 4. Build prefix: ranked products in priority order ───────────────────
    prefix: list[Packet] = []
    seen: set[str] = set()

    for rp in ranked_sorted:
        pid = rp.product_id
        # Hallucination guard: skip IDs not in the authoritative set.
        if pid not in pkt_map:
            continue
        # Dedup guard (should not trigger with well-formed prioritization).
        if pid in seen:
            continue
        prefix.append(pkt_map[pid])
        seen.add(pid)

    # ── 5. Compute deterministic tail via BaselineScheduler ───────────────────
    # Pass the FULL packet set; BaselineScheduler uses only packet attributes
    # and link/mission state, so its output is independent of the ranking.
    baseline_plan = BaselineScheduler.rank(all_packets, link_state, mission_state, weights)

    tail: list[Packet] = []
    for pkt in baseline_plan.packets:
        if pkt.packet_id not in seen:
            tail.append(pkt)
            seen.add(pkt.packet_id)

    ordered = prefix + tail

    # ── 6. Completeness invariants ────────────────────────────────────────────
    if len(ordered) != len(all_packets):
        raise SharedPlanBuildError(
            f"Plan '{plan_id}' packet count mismatch: expected {len(all_packets)}, "
            f"got {len(ordered)}"
        )
    ordered_ids = {p.packet_id for p in ordered}
    input_ids = {p.packet_id for p in all_packets}
    if ordered_ids != input_ids:
        missing = input_ids - ordered_ids
        extra = ordered_ids - input_ids
        raise SharedPlanBuildError(
            f"Plan '{plan_id}' packet ID set mismatch: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )

    return CandidatePlan(
        plan_id=plan_id,
        strategy=strategy,
        packets=ordered,
        generated_by=generated_by,
        metadata=metadata,
    )
