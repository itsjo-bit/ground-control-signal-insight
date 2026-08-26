"""GET /experience — returns experience manifest for the active scenario."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from .. import state

router = APIRouter()

# ---------------------------------------------------------------------------
# Sidecar file registry.
# Maps scenario_id → sidecar path (relative to project root).
# No arbitrary path traversal — only explicitly registered entries are served.
# ---------------------------------------------------------------------------
_BACKEND_APP_DIR: Path = Path(__file__).resolve().parent.parent   # .../backend/app/
_PROJECT_ROOT: Path = _BACKEND_APP_DIR.parent.parent               # .../ground-control-signal-insight/

_SIDECAR_REGISTRY: dict[str, Path] = {
    "asteria7_thermal_priority_contact_v1": _PROJECT_ROOT / "data" / "demo" / "asteria7_experience.json",
}


@router.get("/experience")
def get_experience() -> dict:
    """Return the experience manifest for the active scenario.

    Returns ``{"available": true, "manifest": <sidecar JSON>}`` when the
    active scenario is ASTERIA-7 (has a registered sidecar file).
    Returns ``{"available": false, "manifest": null}`` for all other scenarios
    or when no scenario is loaded.

    No path traversal is possible — the sidecar map is a hard-coded dict.
    """
    scenario = state.active_scenario
    if scenario is None:
        return {"available": False, "manifest": None}

    sidecar_path = _SIDECAR_REGISTRY.get(scenario.scenario_id)
    if sidecar_path is None or not sidecar_path.exists():
        return {"available": False, "manifest": None}

    manifest = json.loads(sidecar_path.read_text(encoding="utf-8"))
    return {"available": True, "manifest": manifest}
