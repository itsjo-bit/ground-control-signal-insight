"""Deterministic candidate preparation layer for AI prioritization (Phase 2C).

``CandidatePrioritizer`` selects a bounded, representative set of
:class:`~backend.app.models.candidate_summary.CandidateSummary` objects from
all available :class:`~backend.app.models.data_product.DataProduct` objects in
the active scenario.  The output is passed directly to the AI
``prioritize_candidates()`` call.

Design principles
-----------------
This layer is deterministic — it uses no LLM and makes no probabilistic
decisions.  Its purpose is:

    "Ensure the AI receives the most relevant AND representative candidates
    while keeping the context bounded."

It is NOT a mission priority algorithm.  The AI is responsible for semantic
prioritization; this layer is responsible for context management.

Selection strategy
------------------
The selection process is quota-based and operates in a fixed priority order.
Slots are allocated greedily; a product cannot occupy more than one slot:

1. Anomaly-linked products (strongest protection)
   All products with ``anomaly_id != None`` AND linked to an applicable
   (active/monitoring) anomaly are included first, prioritised by the severity
   of their linked anomaly (descending).  Resolved/unknown anomaly references
   do NOT receive this protection.

2. Subsystem representation pass (coverage pass)
   From remaining products, deterministically allocate slots to ensure each
   available subsystem receives at least one strong representative, and — when
   the budget allows — a second representative per subsystem.

   Grouping key: normalised ``subsystem`` field.
   Fallback grouping key when subsystem is empty: normalised ``product_type``.

   Coverage budget:
   - First round: one representative per subsystem (sorted by composite urgency
     desc, then product_id for determinism).
   - Second round: one additional representative per subsystem when the budget
     permits.
   - The coverage budget is soft: if coverage itself would consume the entire
     budget, the remaining stages still run with whatever budget remains (which
     may be zero).  Coverage never exceeds the available max_candidates.

3. Critical products (criticality >= threshold, default 0.7)
   Products meeting the criticality threshold that have not yet been selected,
   sorted by criticality desc then product_id.

4. Near-deadline products (deadline_s below window budget)
   Products whose deadline falls within the remaining communication window,
   sorted by deadline_s ascending.

5. High mission-relevance products
   Products with ``mission_relevance >= threshold`` (default 0.6) not yet
   selected.

6. High scientific-value products
   Products with ``scientific_value >= threshold`` (default 0.5) not yet
   selected.

7. Recent products
   Products with lowest ``age_s`` not yet selected (freshest data first).

8. Related products
   Products referenced in ``related_ids`` of already-selected products, not
   yet selected.

9. Fill-up
   Any remaining products sorted by a composite urgency score to fill the
   budget to ``max_candidates``.

Within each category, products are sorted deterministically by their
``product_id`` as a secondary key to guarantee reproducibility.

The final list preserves selection order so the AI can see which products
survived each selection stage (via the summary list's position).

Token safety
------------
The output is capped at ``max_candidates`` (default 50, configurable via
``GCSI_AI_MAX_CANDIDATES``).  The caller must NOT call the LLM directly with
the raw DataProduct list.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Sequence

from ..config import AICandidateConfig
from ..domain.anomaly_policy import is_applicable_anomaly
from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_summary import CandidateSummary
from ..models.data_product import DataProduct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level defaults (all overridable via constructor)
# ---------------------------------------------------------------------------

_DEFAULT_CRITICALITY_THRESHOLD: float = 0.7
_DEFAULT_RELEVANCE_THRESHOLD: float = 0.6
_DEFAULT_SCIENTIFIC_THRESHOLD: float = 0.5


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarise(dp: DataProduct) -> CandidateSummary:
    """Convert a :class:`DataProduct` to a :class:`CandidateSummary`.

    Drops fields used only by the deterministic pipeline (``retry_cost``,
    ``delivery_requirement``) and retains all fields needed for AI reasoning.
    """
    return CandidateSummary(
        product_id=dp.product_id,
        product_type=dp.product_type,
        description=dp.description,
        subsystem=dp.subsystem,
        size_bits=dp.size_bits,
        criticality=dp.criticality,
        mission_relevance=dp.mission_relevance,
        scientific_value=dp.scientific_value,
        deadline_s=dp.deadline_s,
        age_s=dp.age_s,
        anomaly_id=dp.anomaly_id,
        experiment_id=dp.experiment_id,
        related_ids=list(dp.related_ids),
    )


def _composite_urgency(dp: DataProduct) -> float:
    """Compute a rough composite urgency score for fill-up ordering.

    Higher score → more urgent.  This is used only as a tiebreaker in the
    fill-up step and is NOT exposed to the AI.
    """
    # Normalise deadline urgency: shorter deadline → higher urgency
    # Use a sigmoid-like mapping capped at 600 s (10 min window).
    deadline_urgency = max(0.0, 1.0 - dp.deadline_s / 600.0)
    return (
        0.35 * dp.criticality
        + 0.25 * dp.mission_relevance
        + 0.15 * dp.scientific_value
        + 0.15 * deadline_urgency
        + 0.10 * max(0.0, 1.0 - dp.age_s / 3600.0)  # fresher = better
    )


def _group_key(dp: DataProduct) -> str:
    """Return the normalised grouping key for subsystem representation.

    Uses ``subsystem`` when non-empty; falls back to ``product_type`` when
    ``subsystem`` is empty or whitespace-only.  Both are lower-cased and
    stripped so that minor casing differences do not create phantom groups.

    This function does NOT use mission-specific names — it relies solely on
    the generic fields present on every :class:`DataProduct`.
    """
    key = dp.subsystem.strip().lower()
    if not key:
        key = dp.product_type.strip().lower()
    return key or "unknown"


# ---------------------------------------------------------------------------
# CandidatePrioritizer
# ---------------------------------------------------------------------------


class CandidatePrioritizer:
    """Select a bounded, representative set of candidates for AI prioritization.

    Args:
        max_candidates:          Maximum number of :class:`CandidateSummary`
                                 objects to return.  Must be > 0.
                                 Defaults to :class:`AICandidateConfig` env value.
        criticality_threshold:   Minimum criticality to include in the critical
                                 quota.  Default 0.7.
        relevance_threshold:     Minimum mission_relevance for the relevance
                                 quota.  Default 0.6.
        scientific_threshold:    Minimum scientific_value for the science quota.
                                 Default 0.5.
    """

    def __init__(
        self,
        max_candidates: int | None = None,
        criticality_threshold: float = _DEFAULT_CRITICALITY_THRESHOLD,
        relevance_threshold: float = _DEFAULT_RELEVANCE_THRESHOLD,
        scientific_threshold: float = _DEFAULT_SCIENTIFIC_THRESHOLD,
    ) -> None:
        cfg = AICandidateConfig()
        self._max = max_candidates if max_candidates is not None else cfg.max_candidates
        if self._max <= 0:
            raise ValueError(f"max_candidates must be > 0; got {self._max}")
        self._criticality_threshold = criticality_threshold
        self._relevance_threshold = relevance_threshold
        self._scientific_threshold = scientific_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        products: Sequence[DataProduct],
        anomalies: Sequence[AnomalyEvent] | None = None,
        remaining_window_s: float = 600.0,
    ) -> list[CandidateSummary]:
        """Select a bounded, representative candidate set from *products*.

        Args:
            products:           All available data products.
            anomalies:          Active anomaly events (used to sort anomaly-linked
                                products by anomaly severity).
            remaining_window_s: Current remaining communication window in seconds.
                                Used to identify near-deadline products.

        Returns:
            A list of :class:`CandidateSummary` objects, at most
            ``self._max`` items, in selection-stage order.
            Returns an empty list when ``products`` is empty.
        """
        if not products:
            return []

        # Build anomaly severity lookup: anomaly_id → severity
        # Only APPLICABLE anomalies (active + monitoring) participate.
        # Resolved anomalies must NOT grant "anomaly-linked" protection to products.
        # Unknown anomaly references (anomaly_id not in severity_map) also get no
        # protection — applicability cannot be established authoritatively.
        severity_map: dict[str, float] = {}
        if anomalies:
            for ae in anomalies:
                if is_applicable_anomaly(ae):
                    severity_map[ae.anomaly_id] = ae.severity

        product_map: dict[str, DataProduct] = {dp.product_id: dp for dp in products}

        selected_ids: list[str] = []     # ordered, no duplicates
        seen: set[str] = set()

        def _add(pid: str) -> bool:
            """Add pid if not already selected and budget remains.  Return True if added."""
            if pid in seen or pid not in product_map:
                return False
            if len(selected_ids) >= self._max:
                return False
            selected_ids.append(pid)
            seen.add(pid)
            return True

        def _budget_remaining() -> int:
            return self._max - len(selected_ids)

        # ── Stage 1: Anomaly-linked products ─────────────────────────────
        # Only products linked to APPLICABLE anomalies (active + monitoring)
        # receive this protected slot.  Products linked to resolved anomalies
        # or unknown anomaly IDs are NOT included here — they may still be
        # selected by subsequent stages (criticality, relevance, etc.).
        anomaly_linked = [
            dp for dp in products
            if dp.anomaly_id is not None and dp.anomaly_id in severity_map
        ]
        # Sort by linked anomaly severity desc, then product_id for determinism
        anomaly_linked.sort(
            key=lambda dp: (
                -severity_map.get(dp.anomaly_id, 0.0),
                dp.product_id,
            )
        )
        for dp in anomaly_linked:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 2: Subsystem representation pass ────────────────────────
        # Ensure each available subsystem receives at least one strong
        # representative (and a second when the budget permits).
        # This prevents a single high-criticality subsystem from consuming
        # the entire budget via the greedy critical-products stage.
        #
        # The grouping key is generic (_group_key); no mission-specific names
        # are used here.
        if _budget_remaining() > 0:
            # Partition unselected products by group key
            groups: dict[str, list[DataProduct]] = defaultdict(list)
            for dp in products:
                if dp.product_id not in seen:
                    groups[_group_key(dp)].append(dp)

            # Sort each group internally: best representative first.
            # Rank by composite urgency desc, then product_id for determinism.
            for grp in groups.values():
                grp.sort(key=lambda dp: (-_composite_urgency(dp), dp.product_id))

            # Sort the group keys deterministically so selection order is stable
            # regardless of the input sequence.
            sorted_keys = sorted(groups.keys())
            n_groups = len(sorted_keys)

            # Determine how many rounds of coverage to perform.
            # First round: one representative per subsystem (mandatory when budget exists).
            # Second round: one additional representative per subsystem, when affordable.
            #
            # Soft heuristic: we aim for at most ~half of max_candidates for coverage,
            # BUT we never sacrifice one-per-subsystem representation when sufficient
            # budget exists.  That is: if n_groups <= remaining budget, round-1 always
            # happens in full.  The "half" heuristic only constrains round-2.
            budget_for_coverage = _budget_remaining()

            # Round 1: one representative per subsystem
            round1_needed = min(n_groups, budget_for_coverage)
            round1_added: list[str] = []
            round1_idx: dict[str, int] = {}  # track next index into sorted group
            for key in sorted_keys[:round1_needed]:
                grp = groups[key]
                if grp and _budget_remaining() > 0:
                    pid = grp[0].product_id
                    if _add(pid):
                        round1_added.append(pid)
                        round1_idx[key] = 1  # next candidate is index 1
                    else:
                        round1_idx[key] = 0

            # Round 2: a second representative per subsystem, when affordable.
            # Target: total coverage ≤ roughly half of max_candidates (soft heuristic).
            # But never drop round-2 when there is abundant remaining budget.
            soft_coverage_cap = max(n_groups, self._max // 2)
            round1_count = len(round1_added)
            round2_budget = max(0, min(
                _budget_remaining(),             # hard cap: never exceed overall budget
                soft_coverage_cap - round1_count,  # soft heuristic cap
            ))
            # Override: if remaining budget is still large, allow full round-2.
            if _budget_remaining() >= n_groups:
                round2_budget = min(_budget_remaining(), n_groups)

            round2_count = 0
            for key in sorted_keys:
                if round2_count >= round2_budget:
                    break
                if _budget_remaining() <= 0:
                    break
                grp = groups[key]
                next_idx = round1_idx.get(key, 0)
                # Find the next unselected product in this group
                while next_idx < len(grp) and grp[next_idx].product_id in seen:
                    next_idx += 1
                if next_idx < len(grp):
                    if _add(grp[next_idx].product_id):
                        round2_count += 1

        # ── Stage 3: Critical products ────────────────────────────────────
        critical = [
            dp for dp in products
            if dp.criticality >= self._criticality_threshold
        ]
        critical.sort(key=lambda dp: (-dp.criticality, dp.product_id))
        for dp in critical:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 4: Near-deadline products ──────────────────────────────
        near_deadline = [
            dp for dp in products
            if 0.0 < dp.deadline_s <= remaining_window_s
        ]
        near_deadline.sort(key=lambda dp: (dp.deadline_s, dp.product_id))
        for dp in near_deadline:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 5: High mission-relevance products ──────────────────────
        high_relevance = [
            dp for dp in products
            if dp.mission_relevance >= self._relevance_threshold
        ]
        high_relevance.sort(key=lambda dp: (-dp.mission_relevance, dp.product_id))
        for dp in high_relevance:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 6: High scientific-value products ───────────────────────
        high_science = [
            dp for dp in products
            if dp.scientific_value >= self._scientific_threshold
        ]
        high_science.sort(key=lambda dp: (-dp.scientific_value, dp.product_id))
        for dp in high_science:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 7: Recent products (lowest age_s) ───────────────────────
        recent = sorted(products, key=lambda dp: (dp.age_s, dp.product_id))
        for dp in recent:
            if _budget_remaining() <= 0:
                break
            _add(dp.product_id)

        # ── Stage 8: Related products ─────────────────────────────────────
        # Collect all related_ids from currently selected products.
        related_candidates: list[str] = []
        for pid in list(selected_ids):  # snapshot to avoid mid-loop mutation
            dp = product_map.get(pid)
            if dp:
                for rid in dp.related_ids:
                    if rid not in seen and rid in product_map:
                        related_candidates.append(rid)

        # Deduplicate while preserving first-occurrence order
        seen_related: set[str] = set()
        for rid in related_candidates:
            if rid not in seen_related:
                seen_related.add(rid)
                if _budget_remaining() > 0:
                    _add(rid)

        # ── Stage 9: Fill-up with composite urgency ───────────────────────
        if _budget_remaining() > 0:
            remaining = [dp for dp in products if dp.product_id not in seen]
            remaining.sort(key=lambda dp: (-_composite_urgency(dp), dp.product_id))
            for dp in remaining:
                if _budget_remaining() <= 0:
                    break
                _add(dp.product_id)

        n_total = len(products)
        n_selected = len(selected_ids)
        if n_total > self._max:
            # Gather per-group counts for diagnostic logging
            group_counts: dict[str, int] = defaultdict(int)
            for pid in selected_ids:
                dp = product_map.get(pid)
                if dp:
                    group_counts[_group_key(dp)] += 1
            logger.debug(
                "CandidatePrioritizer: selected %d/%d products (max_candidates=%d) "
                "subsystem distribution: %s",
                n_selected, n_total, self._max,
                dict(sorted(group_counts.items(), key=lambda kv: -kv[1])),
            )

        return [_summarise(product_map[pid]) for pid in selected_ids]


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------


def select_candidates(
    products: Sequence[DataProduct],
    anomalies: Sequence[AnomalyEvent] | None = None,
    remaining_window_s: float = 600.0,
    max_candidates: int | None = None,
) -> list[CandidateSummary]:
    """Convenience wrapper around :class:`CandidatePrioritizer`.

    Args:
        products:           All available data products.
        anomalies:          Active anomaly events.
        remaining_window_s: Remaining communication window in seconds.
        max_candidates:     Override the default maximum; uses
                            ``AICandidateConfig`` if None.

    Returns:
        A bounded list of :class:`CandidateSummary` objects.
    """
    prioritizer = CandidatePrioritizer(max_candidates=max_candidates)
    return prioritizer.select(products, anomalies=anomalies, remaining_window_s=remaining_window_s)
