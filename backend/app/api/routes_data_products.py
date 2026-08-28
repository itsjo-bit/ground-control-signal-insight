"""GCSI Backend API — routes for /data-products and /scenarios.

GET  /data-products        — returns all raw data products from the active scenario
GET  /scenarios            — lists available scenario files
POST /scenarios/switch     — switches the active scenario at runtime
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import state
from ..models.data_product import DataProduct
from ..simulation.scenario_loader import ScenarioLoader

router = APIRouter()

# ---------------------------------------------------------------------------
# Scenario data directory.
#
# Resolved in this order:
#   1. GCSI_SCENARIOS_DIR env var — absolute or relative to cwd (explicit override).
#   2. Project-relative default derived from this file's location:
#      <project>/backend/app/api/routes_data_products.py
#      → <project>/backend/app/api/../../.. → <project>/
#      → <project>/data/scenarios/
#
# Using a project-relative absolute path makes the directory reachable whether
# uvicorn is started from <project>/ or from <project>/backend/.
# ---------------------------------------------------------------------------
_env_scenarios_dir = os.getenv("GCSI_SCENARIOS_DIR")
if _env_scenarios_dir:
    _SCENARIOS_DIR_PATH: Path = Path(_env_scenarios_dir)
else:
    # __file__ is <project>/backend/app/api/routes_data_products.py
    _THIS_FILE: Path = Path(__file__).resolve()
    _PROJECT_ROOT: Path = _THIS_FILE.parent.parent.parent.parent  # → <project>/
    _SCENARIOS_DIR_PATH = _PROJECT_ROOT / "data" / "scenarios"

_SCENARIOS_DIR: str = str(_SCENARIOS_DIR_PATH)


# ---------------------------------------------------------------------------
# GET /data-products
# ---------------------------------------------------------------------------


class DataProductsResponse(BaseModel):
    """All raw data products for the active scenario."""

    scenario_id: str
    data_products: list[DataProduct]
    total: int
    has_data_products: bool
    """True when the active scenario uses the v2+ data_products model."""


@router.get("/data-products", response_model=DataProductsResponse)
def get_data_products() -> DataProductsResponse:
    """Return all raw data products for the currently active scenario.

    This endpoint exposes the complete, unfiltered data-product list — NOT
    the subset selected by the baseline scheduler.  For a high-volume v3
    scenario this will typically return ~150 products.

    Legacy scenarios that carry no data_products return an empty list with
    ``has_data_products: false``.

    Raises 503 if no scenario has been loaded yet.
    """
    if state.active_scenario is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")

    products = state.active_scenario.data_products
    return DataProductsResponse(
        scenario_id=state.active_scenario.scenario_id,
        data_products=products,
        total=len(products),
        has_data_products=len(products) > 0,
    )


# ---------------------------------------------------------------------------
# GET /scenarios
# ---------------------------------------------------------------------------


class ScenarioInfo(BaseModel):
    """Metadata for a single scenario file."""

    filename: str
    scenario_id: str | None = None
    has_data_products: bool = False
    has_anomalies: bool = False
    data_products_count: int = 0
    anomalies_count: int = 0
    is_active: bool = False
    label: str = ""
    """Human-readable display label."""


class ScenariosResponse(BaseModel):
    scenarios: list[ScenarioInfo]
    active_scenario_path: str | None


@router.get("/scenarios", response_model=ScenariosResponse)
def list_scenarios() -> ScenariosResponse:
    """List available scenario files.

    Scans the configured scenarios directory for ``.json`` files and returns
    lightweight metadata for each.  The caller can use this to build a
    scenario selector UI without loading every file's full content.

    The ``is_active`` flag is set on the file whose path matches
    ``state.active_scenario_path``.

    Raises 503 if the scenarios directory cannot be read.
    """
    scenarios_dir = _SCENARIOS_DIR_PATH.resolve()
    if not scenarios_dir.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Scenarios directory '{_SCENARIOS_DIR}' not found",
        )

    json_files = sorted(scenarios_dir.glob("*.json"))
    if not json_files:
        raise HTTPException(
            status_code=503,
            detail=f"No scenario files found in '{_SCENARIOS_DIR}'",
        )

    active_path_norm = (
        str(Path(state.active_scenario_path).resolve())
        if state.active_scenario_path
        else None
    )

    import json as _json  # avoid re-importing inside the hot loop

    results: list[ScenarioInfo] = []
    for path in json_files:
        norm_path = str(path.resolve())
        is_active = active_path_norm is not None and norm_path == active_path_norm

        # ── Reset per-file metadata before each iteration ────────────────────
        # This prevents a failed parse from leaking state from the previous
        # file into the current one.
        raw: dict = {}
        scenario_id: str | None = None
        has_dp = False
        has_anom = False
        dp_count = 0
        anom_count = 0
        has_packets = False

        try:
            with open(path) as f:
                raw = _json.load(f)
            scenario_id = raw.get("scenario_id")
            dp_list = raw.get("data_products", [])
            anom_list = raw.get("anomalies", [])
            dp_count = len(dp_list) if isinstance(dp_list, list) else 0
            anom_count = len(anom_list) if isinstance(anom_list, list) else 0
            has_dp = dp_count > 0
            has_anom = anom_count > 0
            has_packets = bool(raw.get("packets"))
        except Exception:  # noqa: BLE001
            pass  # unreadable file → safe minimal metadata only

        # Build human-readable label (uses only per-file variables)
        if scenario_id:
            label = scenario_id.replace("_", " ").title()
        else:
            label = path.stem.replace("_", " ").title()
        if dp_count:
            label += f" ({dp_count} products)"
        elif has_packets:
            label += " (legacy packets)"

        results.append(
            ScenarioInfo(
                filename=path.name,
                scenario_id=scenario_id,
                has_data_products=has_dp,
                has_anomalies=has_anom,
                data_products_count=dp_count,
                anomalies_count=anom_count,
                is_active=is_active,
                label=label,
            )
        )

    return ScenariosResponse(
        scenarios=results,
        active_scenario_path=state.active_scenario_path,
    )


# ---------------------------------------------------------------------------
# POST /scenarios/switch
# ---------------------------------------------------------------------------


class SwitchScenarioRequest(BaseModel):
    """Request body for scenario switching."""

    filename: str
    """Filename (not full path) of the scenario to load, e.g. 'mission_data_v3.json'."""


class SwitchScenarioResponse(BaseModel):
    status: str
    scenario_id: str
    scenario_path: str
    data_products_count: int
    anomalies_count: int


@router.post("/scenarios/switch", response_model=SwitchScenarioResponse)
def switch_scenario(req: SwitchScenarioRequest) -> SwitchScenarioResponse:
    """Switch the active scenario to a different scenario file.

    The file must exist inside the configured scenarios directory
    (``data/scenarios`` by default).  Path traversal is explicitly rejected:
    the resolved target path must be a child of the resolved scenarios directory.
    Only ``.json`` files are accepted.

    Switching a scenario invalidates:
    - The active scenario and link state.
    - Any previous AI recommendation (AI is reset to STANDBY on the frontend).
    - Any operator draft transmission plan.

    If loading the new scenario fails, the current state is fully preserved.

    Raises 400 if the filename contains path separators or is not a .json file.
    Raises 404 if the requested filename is not found inside the scenarios directory.
    Raises 422 if the file exists but fails schema/validation.
    """
    # ── Validate: filename must be a plain basename, not a path ─────────────
    # Reject anything that contains path separators or is an absolute path.
    # The contract is: callers supply a bare filename such as
    # "mission_data_v3.json", not "subdir/test.json" or "../other.json".
    #
    # Two-step validation for cross-platform correctness:
    #   1. Explicit character check for both / and \ — ensures Windows-style
    #      backslash paths ("subdir\file.json", "C:\folder\file.json") are
    #      rejected on POSIX systems where Path(...).name does NOT strip them.
    #   2. Path(...).name == filename — catches remaining non-basename forms
    #      such as absolute paths, ".." components, and drive letters.
    if "/" in req.filename or "\\" in req.filename:
        raise HTTPException(
            status_code=400,
            detail=(
                "filename must be a plain filename with no path separators "
                "(e.g. 'mission_data_v3.json', not 'subdir/test.json' or 'subdir\\test.json')."
            ),
        )
    if Path(req.filename).name != req.filename:
        raise HTTPException(
            status_code=400,
            detail=(
                "filename must be a plain filename with no path separators "
                "(e.g. 'mission_data_v3.json', not 'subdir/test.json')."
            ),
        )

    # ── Reject non-.json files ───────────────────────────────────────────────
    if not req.filename.endswith(".json"):
        raise HTTPException(
            status_code=400,
            detail="Only .json scenario files are accepted.",
        )

    # ── Resolve both base and target to absolute paths (defense in depth) ────
    # After the basename check above, ".." and path separators are already
    # rejected.  Resolving here provides an additional layer of safety in case
    # of platform-specific edge cases.
    base: Path = _SCENARIOS_DIR_PATH.resolve()
    target: Path = (base / req.filename).resolve()

    # ── Ensure target is strictly inside the scenarios directory ─────────────
    try:
        target.relative_to(base)
    except ValueError:
        # target resolved outside base — should not reach here after basename check
        raise HTTPException(
            status_code=404,
            detail=f"Scenario file '{req.filename}' not found in scenarios directory.",
        )

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scenario file '{req.filename}' not found in '{_SCENARIOS_DIR}'",
        )

    # ── Snapshot current state in case the new scenario fails to load ────────
    # Phase 6E-C6: also snapshot source metadata globals so that a failed
    # switch while historical replay is active leaves all historical state intact.
    _prev_scenario = state.active_scenario
    _prev_link_state = state.active_link_state
    _prev_path = state.active_scenario_path
    _prev_source_mode = state.active_source_mode
    _prev_source_ref = state.active_source_ref
    _prev_source_provider_name = state.active_source_provider_name
    _prev_source_provenance = state.active_source_provenance

    try:
        state.load_scenario(str(target))
    except (ValueError, FileNotFoundError) as exc:
        # Restore the previous state so the application is not left broken.
        state.active_scenario = _prev_scenario
        state.active_link_state = _prev_link_state
        state.active_scenario_path = _prev_path
        state.active_source_mode = _prev_source_mode
        state.active_source_ref = _prev_source_ref
        state.active_source_provider_name = _prev_source_provider_name
        state.active_source_provenance = _prev_source_provenance
        raise HTTPException(
            status_code=422,
            detail=f"Failed to load scenario '{req.filename}': {exc}",
        ) from exc

    scenario = state.active_scenario
    assert scenario is not None  # guaranteed after load_scenario succeeds

    return SwitchScenarioResponse(
        status="switched",
        scenario_id=scenario.scenario_id,
        scenario_path=str(target),
        data_products_count=len(scenario.data_products),
        anomalies_count=len(scenario.anomalies),
    )
