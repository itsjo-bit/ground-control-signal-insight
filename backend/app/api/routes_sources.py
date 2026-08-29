"""GCSI Backend API — GET /sources and POST /sources/select.

One-click mission source switcher API (Phase 7 Switcher).

Security contract
-----------------
The frontend sends ONLY a stable ``source_id`` string such as ``"asteria-7"``.
The backend resolves the trusted filesystem path from the immutable catalog.
Arbitrary paths, path-traversal strings, URLs, and unknown identifiers are ALL
rejected as 404/422 — they are never treated as filesystem paths.

Endpoints
---------
GET  /sources
    Returns the catalog of available sources and the active source_id.
    Does NOT expose ``source_ref`` (filesystem paths) to the caller.

POST /sources/select
    Accepts ``{"source_id": "<catalog_id>"}`` and activates the requested source.
    Unknown source_id → 404.
    If loading fails → 422, active source unchanged (atomicity guarantee).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state as app_state
from ..mission_sources.source_catalog import (
    AVAILABLE_MISSION_SOURCES,
    get_catalog_entry,
)
from ..mission_sources.models import MissionSourceMode

router = APIRouter()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MissionSourceInfo(BaseModel):
    """UI-safe summary of one catalog source.  Never includes ``source_ref``."""

    source_id: str
    display_name: str
    mode: str  # "synthetic_scenario" | "historical_replay"
    description: str
    historical: bool
    simulated: bool


class SourcesResponse(BaseModel):
    """Response for GET /sources."""

    active_source_id: str | None
    sources: list[MissionSourceInfo]


class SelectSourceRequest(BaseModel):
    """Request body for POST /sources/select."""

    source_id: str


class SelectSourceResponse(BaseModel):
    """Response for POST /sources/select."""

    status: str  # "switched" | "already_active"
    active_source_id: str
    display_name: str
    mode: str
    data_products_count: int
    scenario_id: str | None


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=SourcesResponse)
def get_sources() -> SourcesResponse:
    """Return available mission sources and the currently active source.

    The ``active_source_id`` is ``null`` when the active source was not loaded
    through the catalog (e.g. set via GCSI_SCENARIO_PATH env var pointing to a
    non-catalog scenario file).

    ``source_ref`` (filesystem paths) is NOT included in the response.
    """
    sources = [
        MissionSourceInfo(
            source_id=entry.source_id,
            display_name=entry.display_name,
            mode=entry.mode.value,
            description=entry.description,
            historical=entry.historical,
            simulated=entry.simulated,
        )
        for entry in AVAILABLE_MISSION_SOURCES
    ]
    return SourcesResponse(
        active_source_id=app_state.active_source_id,
        sources=sources,
    )


# ---------------------------------------------------------------------------
# POST /sources/select
# ---------------------------------------------------------------------------


@router.post("/sources/select", response_model=SelectSourceResponse)
def select_source(req: SelectSourceRequest) -> SelectSourceResponse:
    """Switch the active GCSI mission source by catalog ID.

    The ``source_id`` must be an exact match to a catalog entry.
    Unknown IDs (including path traversal attempts, URLs, empty strings, etc.)
    are rejected with 404.

    If the requested source is already active, returns immediately without
    reloading (no-op for same-source selection).

    If loading the new source fails, the current active source is fully
    preserved (atomicity guarantee from :func:`state.activate_mission_source_bundle`
    and :func:`state.load_scenario`).

    Raises 404 for unknown source_id.
    Raises 422 if the source loads but fails validation.
    """
    # Step 1: strict catalog lookup — rejects ALL non-catalog strings
    entry = get_catalog_entry(req.source_id)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown source_id: {req.source_id!r}. "
                f"Valid IDs: {[e.source_id for e in AVAILABLE_MISSION_SOURCES]}"
            ),
        )

    # Step 2: no-op if already active
    if app_state.active_source_id == entry.source_id:
        scenario = app_state.active_scenario
        return SelectSourceResponse(
            status="already_active",
            active_source_id=entry.source_id,
            display_name=entry.display_name,
            mode=entry.mode.value,
            data_products_count=len(scenario.data_products) if scenario else 0,
            scenario_id=scenario.scenario_id if scenario else None,
        )

    # Step 3: load from trusted catalog source_ref
    logger.info(
        "[GCSI] Switching source: %s → %s (ref=%s)",
        app_state.active_source_id,
        entry.source_id,
        entry.source_ref,
    )

    try:
        if entry.mode == MissionSourceMode.SYNTHETIC_SCENARIO:
            app_state.load_scenario(entry.source_ref, source_id=entry.source_id)
        else:
            # HISTORICAL_REPLAY — route through HistoricalReplayProvider
            app_state.load_historical_replay(
                entry.source_ref,
                source_id=entry.source_id,
            )
    except Exception as exc:
        logger.error(
            "[GCSI] Source switch failed: %s → %s: %s",
            app_state.active_source_id,
            entry.source_id,
            exc,
        )
        raise HTTPException(
            status_code=422,
            detail=f"Failed to load source '{entry.source_id}': {exc}",
        ) from exc

    scenario = app_state.active_scenario
    return SelectSourceResponse(
        status="switched",
        active_source_id=entry.source_id,
        display_name=entry.display_name,
        mode=entry.mode.value,
        data_products_count=len(scenario.data_products) if scenario else 0,
        scenario_id=scenario.scenario_id if scenario else None,
    )
