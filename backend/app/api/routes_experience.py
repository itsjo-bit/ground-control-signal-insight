"""GET /experience — returns experience manifest for the active scenario."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import ValidationError

from .. import state
from ..models.experience import ExperienceManifest, ExperienceResponse

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


@router.get("/experience", response_model=ExperienceResponse)
def get_experience() -> ExperienceResponse:
    """Return the experience manifest for the active scenario.

    Returns ``{"available": true, "manifest": <typed manifest>}`` when the
    active scenario is ASTERIA-7 (has a registered sidecar file).
    Returns ``{"available": false, "manifest": null}`` for all other scenarios
    or when no scenario is loaded.

    Raises HTTP 500 if the registered sidecar fails Pydantic validation —
    a malformed sidecar is a server configuration error, not a client error.

    No path traversal is possible — the sidecar map is a hard-coded dict.
    """
    scenario = state.active_scenario
    if scenario is None:
        return ExperienceResponse(available=False, manifest=None)

    sidecar_path = _SIDECAR_REGISTRY.get(scenario.scenario_id)
    if sidecar_path is None or not sidecar_path.exists():
        return ExperienceResponse(available=False, manifest=None)

    raw = json.loads(sidecar_path.read_text(encoding="utf-8"))

    # Validate and parse into typed model.  A malformed sidecar is a server
    # configuration error — raise clearly rather than silently returning garbage.
    try:
        manifest = ExperienceManifest.model_validate(raw)
    except ValidationError as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Experience sidecar validation failed: {exc}",
        ) from exc

    return ExperienceResponse(available=True, manifest=manifest)
