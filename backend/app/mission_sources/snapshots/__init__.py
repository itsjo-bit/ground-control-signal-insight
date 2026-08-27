"""GCSI Phase 6D-B1 — Verified Horizons Geometry Snapshot package.

This package provides the immutable snapshot mechanism for validated
JPL Horizons geometry responses.

Public surface
--------------
HorizonsSnapshotStore
    Write and load verified snapshot files.

HorizonsSnapshotEnvelope
    Strict Pydantic model for the on-disk snapshot format.

HorizonsSnapshotError
HorizonsSnapshotUnavailableError
HorizonsSnapshotValidationError
    Typed error hierarchy.
"""

from .horizons_snapshot import HorizonsSnapshotStore
from .horizons_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    HorizonsSnapshotEnvelope,
)
from .horizons_snapshot import (
    HorizonsSnapshotError,
    HorizonsSnapshotUnavailableError,
    HorizonsSnapshotValidationError,
)

__all__ = [
    "HorizonsSnapshotStore",
    "HorizonsSnapshotEnvelope",
    "HorizonsSnapshotError",
    "HorizonsSnapshotUnavailableError",
    "HorizonsSnapshotValidationError",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
]
