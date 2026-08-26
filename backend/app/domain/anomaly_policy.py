"""Shared anomaly applicability policy — single source of truth.

This module owns the canonical definition of which anomalies are *applicable*
for prioritization, coverage metrics, and AI context.

Policy
------
An anomaly is **applicable** when its ``status`` is one of::

    "active"     — anomaly is actively affecting the spacecraft
    "monitoring" — anomaly is being monitored (degraded but not resolved)

An anomaly is **NOT applicable** when its ``status`` is::

    "resolved"       — anomaly has been resolved; coverage no longer urgent
    any other value  — unknown status; cannot establish applicability

All components that need anomaly status filtering must import from here:

* :class:`~backend.app.agent.candidate_prioritizer.CandidatePrioritizer`
* :class:`~backend.app.agent.semantic_rule_prioritizer.SemanticRulePrioritizer`
* :func:`~backend.app.agent.prioritization_helpers.build_prioritization_message`
* :class:`~backend.app.evaluator.mission_outcome_evaluator.MissionOutcomeEvaluator`
* Stage-2 anomaly context (``routes_agent``)

Do NOT duplicate the status set in any other module.  Import from here.

Backwards Compatibility
-----------------------
``mission_outcome_evaluator`` re-exports :data:`APPLICABLE_ANOMALY_STATUSES`
and :func:`is_applicable_anomaly` for existing tests and imports that reference
those names from the evaluator module.  New code should import from here.
"""

from __future__ import annotations

from ..models.anomaly_event import AnomalyEvent

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Anomaly statuses that count as "applicable" for prioritization and coverage.
#: Resolved anomalies are excluded — their diagnostic data delivery is no
#: longer operationally urgent.
APPLICABLE_ANOMALY_STATUSES: frozenset[str] = frozenset({"active", "monitoring"})


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def is_applicable_anomaly(anomaly: AnomalyEvent) -> bool:
    """Return ``True`` when *anomaly* should be counted in coverage metrics.

    An anomaly is applicable when its status is ``"active"`` or
    ``"monitoring"``.  Resolved anomalies and anomalies with unknown statuses
    are **not** applicable.

    This is the single canonical status filter.  Import and use it everywhere
    anomaly applicability is tested — do not duplicate the logic.

    Args:
        anomaly: The anomaly event to test.

    Returns:
        ``True`` when ``anomaly.status`` is in
        :data:`APPLICABLE_ANOMALY_STATUSES`.
    """
    return anomaly.status in APPLICABLE_ANOMALY_STATUSES
