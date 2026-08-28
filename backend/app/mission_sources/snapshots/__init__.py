"""GCSI Mission Source Snapshot package.

Provides immutable snapshot mechanisms for validated external source responses.

Horizons (Phase 6D-B1)
-----------------------
HorizonsSnapshotStore
    Write and load verified Horizons geometry snapshot files.

PDS (Phase 6E-D0)
-----------------
PdsSnapshotStore
    Write and load verified PDS science product snapshot files.

Error hierarchy — Horizons
--------------------------
HorizonsSnapshotError
HorizonsSnapshotUnavailableError
HorizonsSnapshotValidationError

Error hierarchy — PDS
---------------------
PdsSnapshotError
PdsSnapshotUnavailableError
PdsSnapshotValidationError
"""

from .horizons_snapshot import (
    HorizonsSnapshotError,
    HorizonsSnapshotStore,
    HorizonsSnapshotUnavailableError,
    HorizonsSnapshotValidationError,
)
from .horizons_snapshot_models import (
    SNAPSHOT_SCHEMA as HORIZONS_SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION as HORIZONS_SNAPSHOT_VERSION,
    HorizonsSnapshotEnvelope,
)
from .pds_snapshot import (
    PdsSnapshotError,
    PdsSnapshotStore,
    PdsSnapshotUnavailableError,
    PdsSnapshotValidationError,
)
from .pds_snapshot_models import (
    SNAPSHOT_SCHEMA as PDS_SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION as PDS_SNAPSHOT_VERSION,
    PdsSnapshotEnvelope,
)

# Backward-compatible Phase 6D aliases.
# New code should prefer source-qualified constants.
SNAPSHOT_SCHEMA = HORIZONS_SNAPSHOT_SCHEMA
SNAPSHOT_VERSION = HORIZONS_SNAPSHOT_VERSION

__all__ = [
    # Horizons
    "HorizonsSnapshotStore",
    "HorizonsSnapshotEnvelope",
    "HorizonsSnapshotError",
    "HorizonsSnapshotUnavailableError",
    "HorizonsSnapshotValidationError",
    "HORIZONS_SNAPSHOT_SCHEMA",
    "HORIZONS_SNAPSHOT_VERSION",
    # PDS
    "PdsSnapshotStore",
    "PdsSnapshotEnvelope",
    "PdsSnapshotError",
    "PdsSnapshotUnavailableError",
    "PdsSnapshotValidationError",
    "PDS_SNAPSHOT_SCHEMA",
    "PDS_SNAPSHOT_VERSION",
    # Backward-compatible Phase 6D aliases
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_VERSION",
]
