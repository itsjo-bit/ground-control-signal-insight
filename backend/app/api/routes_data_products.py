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
# Scenario data directory (relative to process CWD, or absolute).
# Mirrors the convention used by main.py lifespan for GCSI_SCENARIO_PATH.
# ---------------------------------------------------------------------------
_SCENARIOS_DIR = os.getenv("GCSI_SCENARIOS_DIR", "data/scenarios")


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
    scenarios_dir = Path(_SCENARIOS_DIR)
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

    results: list[ScenarioInfo] = []
    for path in json_files:
        norm_path = str(path.resolve())
        is_active = active_path_norm is not None and norm_path == active_path_norm

        # Try to peek at metadata without full validation.
        scenario_id: str | None = None
        has_dp = False
        has_anom = False
        dp_count = 0
        anom_count = 0
        try:
            import json
            with open(path) as f:
                raw = json.load(f)
            scenario_id = raw.get("scenario_id")
            dp_list = raw.get("data_products", [])
            anom_list = raw.get("anomalies", [])
            dp_count = len(dp_list)
            anom_count = len(anom_list)
            has_dp = dp_count > 0
            has_anom = anom_count > 0
        except Exception:  # noqa: BLE001
            pass  # unreadable file → minimal info

        # Build human-readable label
        if scenario_id:
            label = scenario_id.replace("_", " ").title()
        else:
            label = path.stem.replace("_", " ").title()
        if dp_count:
            label += f" ({dp_count} products)"
        elif raw.get("packets") if "raw" in dir() else False:
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
    (``data/scenarios`` by default).  This is a safety constraint — callers
    cannot load arbitrary paths from the filesystem.

    Switching a scenario invalidates:
    - The active scenario and link state.
    - Any previous AI recommendation (AI is reset to STANDBY on the frontend).
    - Any operator draft transmission plan.

    Raises 404 if the requested filename is not found.
    Raises 422 if the file exists but fails validation.
    """
    scenarios_dir = Path(_SCENARIOS_DIR)
    target = scenarios_dir / req.filename

    if not target.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scenario file '{req.filename}' not found in '{_SCENARIOS_DIR}'",
        )

    try:
        state.load_scenario(str(target))
    except (ValueError, FileNotFoundError) as exc:
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
