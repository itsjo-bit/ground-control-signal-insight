"""GCSI Phase 6E-C5 — Mission Source Provider Boundary.

This package introduces the top-level mission-source boundary that allows
interchangeable providers of canonical GCSI Scenarios and their provenance
sidecars.

Architecture
------------
::

    BaseMissionSourceProvider (abstract)
        |
        +-- SyntheticScenarioProvider      [Phase 6C — implemented]
        |
        +-- HistoricalReplayProvider       [Phase 6E-C5 — implemented]

The output of any provider is a :class:`MissionSourceBundle`:

::

    source provider
          ↓
    MissionSourceBundle (Scenario + ProvenanceManifest + metadata)
          ↓
    future runtime activation (Phase 6E-C6)

IMPORTANT
---------
- ``HistoricalReplayProvider`` is implemented but dormant.  It is NOT
  wired into ``state.py``, ``ScenarioLoader``, or any API route.
- The existing application startup path is unchanged.
- All historical replay snapshot IO is performed through verified stores only
  (HorizonsSnapshotStore, PdsArchiveSnapshotStore).
- ``ReplayAssembler`` is a pure function — it performs zero IO.

Public surface
--------------
All stable public names are re-exported from this module so callers
need only import from ``backend.app.mission_sources``.
"""

from .base import BaseMissionSourceProvider
from .errors import (
    MissionSourceError,
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from .historical_provider import HistoricalReplayProvider
from .models import MissionSourceBundle, MissionSourceMode
from .replay_assembler import ReplayAssembler
from .replay_descriptor import (
    DESCRIPTOR_SCHEMA,
    DESCRIPTOR_VERSION,
    MAX_DESCRIPTOR_BYTES,
    RISK_LEVEL_POLICY_V1,
    HistoricalReplayDescriptorV1,
    ReplayDataProductPolicyV1,
    ReplayLinkPolicyV1,
    ReplayMissionPolicyV1,
    load_historical_replay_descriptor,
    replay_risk_level_from_score,
)
from .synthetic_provider import SyntheticScenarioProvider

__all__ = [
    # Mode enum
    "MissionSourceMode",
    # Bundle
    "MissionSourceBundle",
    # Abstract base
    "BaseMissionSourceProvider",
    # Concrete providers
    "SyntheticScenarioProvider",
    "HistoricalReplayProvider",  # Phase 6E-C5 — implemented, dormant
    # Assembler
    "ReplayAssembler",           # Phase 6E-C5 — implemented
    # Errors
    "MissionSourceError",
    "MissionSourceUnavailableError",
    "MissionSourceValidationError",
    # Replay descriptor (Phase 6E-C4B)
    "DESCRIPTOR_SCHEMA",
    "DESCRIPTOR_VERSION",
    "MAX_DESCRIPTOR_BYTES",
    "RISK_LEVEL_POLICY_V1",
    "HistoricalReplayDescriptorV1",
    "ReplayDataProductPolicyV1",
    "ReplayLinkPolicyV1",
    "ReplayMissionPolicyV1",
    "load_historical_replay_descriptor",
    "replay_risk_level_from_score",
]
