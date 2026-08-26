"""
backend/app/presentation/ground_evidence.py

Production ground-evidence presentation helper for GCSI Phase 4.2F.

Computes objective coverage from actually delivered packet IDs and the
ground_information_objectives map from the experience manifest.

PRESENTATION LOGIC ONLY — do not add these thresholds to MissionOutcomeEvaluator.
Tests must import from this module, not reimplement helpers locally.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Coverage level thresholds ─────────────────────────────────────────────────

EVIDENCE_THRESHOLD_HIGH: float = 0.80
EVIDENCE_THRESHOLD_MEDIUM: float = 0.40


def ground_evidence_level(fraction: float) -> str:
    """
    Classify a coverage fraction into a display level.

    LOW:    < 40%
    MEDIUM: >= 40% and < 80%
    HIGH:   >= 80%
    """
    if fraction >= EVIDENCE_THRESHOLD_HIGH:
        return "HIGH"
    if fraction >= EVIDENCE_THRESHOLD_MEDIUM:
        return "MEDIUM"
    return "LOW"


# ── Per-objective coverage ────────────────────────────────────────────────────

@dataclass
class ObjectiveCoverage:
    name: str
    required_ids: list[str]
    delivered_ids: list[str]
    fraction: float          # 0.0 – 1.0
    level: str               # HIGH | MEDIUM | LOW


def assess_ground_objectives(
    delivered_ids: frozenset[str] | set[str],
    objectives: dict[str, list[str]],
) -> list[ObjectiveCoverage]:
    """
    Compute per-objective coverage as fraction of required products delivered.

    Args:
        delivered_ids:  Set of product IDs confirmed delivered by the simulator.
        objectives:     Mapping of objective name -> required product IDs.

    Returns:
        List of ObjectiveCoverage, one per objective entry.
    """
    result: list[ObjectiveCoverage] = []
    for name, ids in objectives.items():
        if not ids:
            result.append(ObjectiveCoverage(
                name=name,
                required_ids=[],
                delivered_ids=[],
                fraction=1.0,
                level="HIGH",
            ))
            continue
        delivered = [pid for pid in ids if pid in delivered_ids]
        fraction = len(delivered) / len(ids)
        result.append(ObjectiveCoverage(
            name=name,
            required_ids=ids,
            delivered_ids=delivered,
            fraction=fraction,
            level=ground_evidence_level(fraction),
        ))
    return result


# ── Overall coverage ──────────────────────────────────────────────────────────

def overall_ground_evidence_coverage(
    delivered_ids: frozenset[str] | set[str],
    objectives: dict[str, list[str]],
) -> float:
    """
    Compute overall coverage as fraction of all required objective IDs delivered.

    Args:
        delivered_ids:  Set of product IDs confirmed delivered.
        objectives:     Mapping of objective name -> required product IDs.

    Returns:
        Fraction in [0, 1].
    """
    all_ids = [pid for ids in objectives.values() for pid in ids]
    if not all_ids:
        return 1.0
    delivered_count = sum(1 for pid in all_ids if pid in delivered_ids)
    return delivered_count / len(all_ids)


# ── Availability label ────────────────────────────────────────────────────────

def objective_availability_label(fraction: float) -> str:
    """Convert a fraction to a human-readable availability label."""
    if fraction >= 1.0:
        return "AVAILABLE"
    if fraction > 0.0:
        return "PARTIAL"
    return "UNAVAILABLE"
