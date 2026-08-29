"""GCSI Mission Source Catalog — immutable backend source catalog.

This module defines the built-in set of available mission sources that the
one-click scenario switcher can activate at runtime.

Design notes
------------
``MissionSourceCatalogEntry``
    A lightweight read-only description of one catalog entry.  It exposes only
    UI-safe metadata; the ``source_ref`` (filesystem path) stays backend-internal
    and is never returned to the public frontend API.

``AVAILABLE_MISSION_SOURCES``
    The complete, immutable, ordered catalog.  Order determines display ordering
    in the frontend dropdown:

        1. asteria-7       (synthetic)
        2. juno-pj62-v1    (historical V1 — 2 products)
        3. juno-pj62-v2    (historical V2 — 403 products)

Security notes
--------------
The frontend sends only a ``source_id`` string.  The backend performs a
catalog lookup to resolve the trusted ``source_ref``.  Arbitrary filesystem
paths CANNOT reach the loader via this API.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import MissionSourceMode


@dataclass(frozen=True)
class MissionSourceCatalogEntry:
    """One entry in the mission source catalog.

    Fields
    ------
    source_id
        Stable, unique identifier used by the public API (e.g. "asteria-7").
    display_name
        Human-readable display name for the frontend dropdown.
    mode
        Underlying :class:`MissionSourceMode` for this source.
    description
        Short user-facing description (1–2 sentences).
    historical
        True when the source is derived from real historical mission data.
    simulated
        True when the source is a simulated replay (all GCSI sources are simulated).
    source_ref
        Backend-internal trusted reference used to load this source.
        For synthetic: project-relative scenario JSON path.
        For historical: project-relative replay descriptor JSON path.
        NEVER exposed to the public API.
    """

    source_id: str
    display_name: str
    mode: MissionSourceMode
    description: str
    historical: bool
    simulated: bool
    source_ref: str  # backend-internal; never sent to frontend


# ---------------------------------------------------------------------------
# Canonical ASTERIA-7 scenario path
# ---------------------------------------------------------------------------
#
# Resolved lazily at import time from this file's own location so it is always
# correct regardless of the current working directory.  __file__ is:
#   <repo>/backend/app/mission_sources/source_catalog.py
#   parents[0] = mission_sources/
#   parents[1] = app/
#   parents[2] = backend/
#   parents[3] = <repo root>
#
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[3]
# Synthetic scenario: use absolute path (ScenarioLoader requires it or works with cwd-relative).
_ASTERIA7_PATH = str(
    _REPO_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json"
)
# Historical replays: use repository-relative path (HistoricalReplayProvider security contract).
_V1_PATH = "data/replays/juno_pj62_mwr_v1.json"
_V2_PATH = "data/replays/juno_pj62_large_replay_v2_descriptor.json"


# ---------------------------------------------------------------------------
# Immutable catalog — deterministic ordering
# ---------------------------------------------------------------------------

AVAILABLE_MISSION_SOURCES: tuple[MissionSourceCatalogEntry, ...] = (
    MissionSourceCatalogEntry(
        source_id="asteria-7",
        display_name="ASTERIA-7",
        mode=MissionSourceMode.SYNTHETIC_SCENARIO,
        description="Fictional synthetic thermal-priority contact scenario.",
        historical=False,
        simulated=True,
        source_ref=_ASTERIA7_PATH,
    ),
    MissionSourceCatalogEntry(
        source_id="juno-pj62-v1",
        display_name="Juno PJ62 Historical V1",
        mode=MissionSourceMode.HISTORICAL_REPLAY,
        description=(
            "Small historical replay based on verified Juno PJ62 MWR archive evidence."
        ),
        historical=True,
        simulated=True,
        source_ref=_V1_PATH,
    ),
    MissionSourceCatalogEntry(
        source_id="juno-pj62-v2",
        display_name="Juno PJ62 Historical V2",
        mode=MissionSourceMode.HISTORICAL_REPLAY,
        description=(
            "Large historical replay using 403 eligible products reconstructed "
            "from verified Juno PJ62 archive evidence."
        ),
        historical=True,
        simulated=True,
        source_ref=_V2_PATH,
    ),
)

# Fast O(1) lookup by source_id
_CATALOG_BY_ID: dict[str, MissionSourceCatalogEntry] = {
    entry.source_id: entry for entry in AVAILABLE_MISSION_SOURCES
}


def get_catalog_entry(source_id: str) -> MissionSourceCatalogEntry | None:
    """Return the catalog entry for ``source_id``, or ``None`` if not found."""
    return _CATALOG_BY_ID.get(source_id)
