"""GCSI Phase 6C — Mission Source Domain Models.

Defines the stable string enum ``MissionSourceMode`` and the boundary
container ``MissionSourceBundle`` produced by any ``BaseMissionSourceProvider``.

Design notes
------------
``MissionSourceMode``
    Stable string enum representing the top-level source mode.
    Both ``synthetic_scenario`` and ``historical_replay`` are implemented.
    ``historical_replay`` is dormant until Phase 6E-C6 activates it in the
    runtime source selector.

``MissionSourceBundle``
    Boundary object produced by a provider.  It carries:

    - ``scenario``       — the canonical GCSI :class:`Scenario` runtime object.
    - ``provenance``     — the immutable :class:`ProvenanceManifest` sidecar.
    - ``provider_name``  — human-readable stable name of the producing provider.
    - ``source_mode``    — the :class:`MissionSourceMode` of the provider.
    - ``source_ref``     — the opaque reference used to locate the source
                           (e.g. a file path string for synthetic scenarios).

    Immutability note:
    The *provenance graph* is immutable (``ProvenanceManifest`` uses
    ``frozen=True``).  The *Scenario* is the existing mutable GCSI runtime
    object; the bundle does NOT falsely claim the Scenario is frozen.
    ``MissionSourceBundle`` itself uses ``arbitrary_types_allowed=True`` so
    that the Pydantic model can hold the Scenario instance without attempting
    to serialize/validate its internals here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from ..models.scenario import Scenario
from ..provenance.models import ProvenanceManifest


# ---------------------------------------------------------------------------
# A. MissionSourceMode
# ---------------------------------------------------------------------------


class MissionSourceMode(str, Enum):
    """Top-level mission-source mode.

    ``synthetic_scenario``
        Controlled fictional ground truth loaded from a GCSI scenario JSON
        file.  The only mode implemented in Phase 6C.

    ``historical_replay``
        A reconstructed real mission scenario assembled from authoritative
        external archives (e.g. NASA PDS + JPL Horizons).  Implemented in
        Phase 6E-C5 by :class:`~backend.app.mission_sources.historical_provider.HistoricalReplayProvider`.
        Dormant until Phase 6E-C6 activation in the runtime source selector.
    """

    SYNTHETIC_SCENARIO = "synthetic_scenario"
    HISTORICAL_REPLAY = "historical_replay"  # implemented — Phase 6E-C5


# ---------------------------------------------------------------------------
# B. MissionSourceBundle
# ---------------------------------------------------------------------------


class MissionSourceBundle(BaseModel):
    """Boundary container produced by a :class:`BaseMissionSourceProvider`.

    This object is the output contract between the source layer and the
    future runtime-activation layer.  It is never passed back into
    ``ScenarioLoader`` or ``state.py`` in Phase 6C.

    Fields
    ------
    scenario
        The canonical GCSI runtime :class:`Scenario`.  Fully validated by
        the provider.  Identical in value to what ``ScenarioLoader.load()``
        would return directly for the same source.

    provenance
        Immutable :class:`ProvenanceManifest` sidecar.  Produced by the
        provider *after* ScenarioLoader validation — never extracted from
        the source JSON itself.

    provider_name
        Human-readable, stable name of the provider that produced this
        bundle, e.g. ``"GCSI-SyntheticScenarioProvider"``.

    source_mode
        The :class:`MissionSourceMode` of the provider.

    source_ref
        The opaque reference string used to locate the source.  For
        synthetic scenarios this is the scenario file path.  Stored as a
        plain string; do NOT execute or shell-out on this value.
    """

    model_config = ConfigDict(
        frozen=False,          # Scenario is a mutable runtime object; bundle is not frozen
        arbitrary_types_allowed=True,  # Scenario is not a plain Pydantic model here
        extra="forbid",
    )

    scenario: Scenario = Field(
        description="Canonical GCSI runtime Scenario produced by the provider."
    )
    provenance: ProvenanceManifest = Field(
        description="Immutable provenance manifest sidecar produced by the provider."
    )
    provider_name: str = Field(
        description=(
            "Human-readable, stable name of the provider, "
            "e.g. 'GCSI-SyntheticScenarioProvider'."
        )
    )
    source_mode: MissionSourceMode = Field(
        description="Top-level source mode of the producing provider."
    )
    source_ref: str = Field(
        description=(
            "Opaque reference used to locate the source. "
            "For synthetic scenarios: the scenario file path. "
            "Do NOT execute or shell-out on this value."
        )
    )
