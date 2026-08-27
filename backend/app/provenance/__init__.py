"""GCSI Phase 6B — Mission-Data Provenance Foundation.

This package implements a clean, standalone provenance sidecar for GCSI
mission-data facts.

IMPORTANT TERMINOLOGY
---------------------
This package is completely independent of the plan-integrity / AI-trust
provenance already present in ``backend.app.domain.plan_integrity``.

- ``plan_integrity.PlanSource`` answers: "Who/what generated this plan?"
- This package answers: "Where did this exact mission-data fact come from?"

Do NOT import anything from this package into existing GCSI runtime
modules (ScenarioLoader, TelecomEngine, PlanEvaluator, etc.).

Public surface
--------------
All stable public names are re-exported from this module so callers
need only import from ``backend.app.provenance``.
"""

from .models import (
    ProvenanceKind,
    ProvenanceValidationStatus,
    ProvenanceRecord,
    FieldProvenanceBinding,
    ProvenanceManifest,
)

__all__ = [
    "ProvenanceKind",
    "ProvenanceValidationStatus",
    "ProvenanceRecord",
    "FieldProvenanceBinding",
    "ProvenanceManifest",
]
