"""GCSI Phase 6F-B2.2.1 — V2 Source Bundle Index.

B2.2.1 changes from B2.2:
- Source bundle now explicitly distinguishes candidate census from eligible census.
- Added candidate_plan_id (was just acquisition_plan_id = 403 plan).
- Added candidate_logical_count / candidate_source_count.
- Added temporal_reconciliation_id.
- Added ineligible_logical_count / ineligible_source_count.
- label_snapshot_count = 535 (was 527, now includes ineligible snapshots).
- logical_product_count = 403 (eligible only).
- source_record_count = 527 (eligible only).
- load_source_bundle(): production trust loader with bounded read + bundle_id verify.

All models: frozen=True, extra="forbid".
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Maximum bundle file size: 2 MiB.
_MAX_BUNDLE_BYTES: int = 2 * 1024 * 1024

_BUNDLE_ID_PREFIX: str = "gcsi.v2_source_bundle:v2:"

_ALLOWED_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "replays"


# ---------------------------------------------------------------------------
# Bundle ID computation
# ---------------------------------------------------------------------------


def _compute_bundle_id(
    replay_id: str,
    candidate_plan_id: str,
    discovery_evidence_artifact_id: str,
    acquisition_ledger_id: str,
    temporal_reconciliation_id: str,
    verified_inventory_manifest_id: str,
    verified_inventory_manifest_ref: str,
    label_snapshot_count: int,
    candidate_logical_count: int,
    candidate_source_count: int,
    eligible_logical_count: int,
    eligible_source_count: int,
    ineligible_logical_count: int,
    ineligible_source_count: int,
    decision_epoch_utc: str,
    horizons_snapshot_id: Optional[str],
    horizons_snapshot_ref: Optional[str],
) -> str:
    """Compute deterministic bundle_id over canonical content (excluding bundle_id)."""
    canonical = {
        "acquisition_ledger_id": acquisition_ledger_id,
        "candidate_logical_count": candidate_logical_count,
        "candidate_plan_id": candidate_plan_id,
        "candidate_source_count": candidate_source_count,
        "decision_epoch_utc": decision_epoch_utc,
        "discovery_evidence_artifact_id": discovery_evidence_artifact_id,
        "eligible_logical_count": eligible_logical_count,
        "eligible_source_count": eligible_source_count,
        "horizons_snapshot_id": horizons_snapshot_id,
        "horizons_snapshot_ref": horizons_snapshot_ref,
        "ineligible_logical_count": ineligible_logical_count,
        "ineligible_source_count": ineligible_source_count,
        "label_snapshot_count": label_snapshot_count,
        "replay_id": replay_id,
        "temporal_reconciliation_id": temporal_reconciliation_id,
        "verified_inventory_manifest_id": verified_inventory_manifest_id,
        "verified_inventory_manifest_ref": verified_inventory_manifest_ref,
    }
    payload = _BUNDLE_ID_PREFIX + json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# V2SourceBundle model
# ---------------------------------------------------------------------------


class V2SourceBundle(BaseModel):
    """Source bundle index for one completed V2 acquisition run.

    B2.2.1: Explicitly separates candidate census from eligible census.
    - candidate_logical_count = 411 (all sidecar candidates)
    - candidate_source_count = 535 (all planned source representations)
    - eligible_logical_count = 403 (temporally eligible logical products)
    - eligible_source_count = 527 (eligible source representations)
    - ineligible_logical_count = 8 (temporally ineligible, still evidenced)
    - label_snapshot_count = 535 (all acquired, including ineligible)
    - logical_product_count = 403 (alias for eligible_logical_count)
    - source_record_count = 527 (alias for eligible_source_count)

    Integrity: bundle_id is a deterministic SHA-256 over all semantic content.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema: str = Field(description="Schema identifier: 'gcsi.v2_source_bundle'.")
    schema_version: int = Field(description="Schema version integer. Current: 2.")
    bundle_id: str = Field(
        description=(
            "SHA-256 of canonical content excluding bundle_id. "
            "Changes when any semantic content changes."
        )
    )
    replay_id: str = Field(description="Stable identifier for the replay.")
    candidate_plan_id: str = Field(
        description="plan_id of the candidate acquisition plan (411 logical / 535 refs)."
    )
    discovery_evidence_artifact_id: str = Field(
        description="artifact_id of the discovery evidence sidecar."
    )
    acquisition_ledger_id: str = Field(
        description="ledger_id of the acquisition ledger (535 rows)."
    )
    temporal_reconciliation_id: str = Field(
        description="reconciliation_id of the temporal reconciliation manifest."
    )
    verified_inventory_manifest_id: str = Field(
        description="manifest_id of the verified inventory manifest (403 eligible entries)."
    )
    verified_inventory_manifest_ref: str = Field(
        description="POSIX repository-relative path to the verified inventory manifest file."
    )
    label_snapshot_count: int = Field(
        description=(
            "Number of label snapshots acquired (candidate total). "
            "B2.2.1: 535 (all 411 candidates, including 8 ineligible)."
        )
    )
    candidate_logical_count: int = Field(
        description="Number of logical candidate products from acquisition plan. B2.2.1: 411."
    )
    candidate_source_count: int = Field(
        description="Number of candidate source representations. B2.2.1: 535."
    )
    eligible_logical_count: int = Field(
        description="Number of eligible logical products. B2.2.1: 403."
    )
    eligible_source_count: int = Field(
        description="Number of eligible source representations. B2.2.1: 527."
    )
    ineligible_logical_count: int = Field(
        description="Number of temporally ineligible logical products. B2.2.1: 8."
    )
    ineligible_source_count: int = Field(
        description="Number of temporally ineligible source representations. B2.2.1: 8."
    )
    # Aliases for backward compatibility with callers expecting old field names
    logical_product_count: int = Field(
        description="Alias for eligible_logical_count. B2.2.1: 403."
    )
    source_record_count: int = Field(
        description="Alias for eligible_source_count. B2.2.1: 527."
    )
    decision_epoch_utc: str = Field(description="ISO-8601 UTC decision epoch (frozen).")
    horizons_snapshot_id: Optional[str] = Field(
        default=None,
        description="snapshot_id of the Horizons geometry snapshot, if captured.",
    )
    horizons_snapshot_ref: Optional[str] = Field(
        default=None,
        description="POSIX relative path to the Horizons snapshot file, if captured.",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_source_bundle(
    replay_id: str,
    candidate_plan_id: str,
    discovery_evidence_artifact_id: str,
    acquisition_ledger_id: str,
    temporal_reconciliation_id: str,
    verified_inventory_manifest_id: str,
    verified_inventory_manifest_ref: str,
    label_snapshot_count: int,
    candidate_logical_count: int,
    candidate_source_count: int,
    eligible_logical_count: int,
    eligible_source_count: int,
    ineligible_logical_count: int,
    ineligible_source_count: int,
    decision_epoch_utc: str,
    horizons_snapshot_id: Optional[str] = None,
    horizons_snapshot_ref: Optional[str] = None,
) -> V2SourceBundle:
    """Build a V2SourceBundle with auto-computed bundle_id."""
    bundle_id = _compute_bundle_id(
        replay_id=replay_id,
        candidate_plan_id=candidate_plan_id,
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
        acquisition_ledger_id=acquisition_ledger_id,
        temporal_reconciliation_id=temporal_reconciliation_id,
        verified_inventory_manifest_id=verified_inventory_manifest_id,
        verified_inventory_manifest_ref=verified_inventory_manifest_ref,
        label_snapshot_count=label_snapshot_count,
        candidate_logical_count=candidate_logical_count,
        candidate_source_count=candidate_source_count,
        eligible_logical_count=eligible_logical_count,
        eligible_source_count=eligible_source_count,
        ineligible_logical_count=ineligible_logical_count,
        ineligible_source_count=ineligible_source_count,
        decision_epoch_utc=decision_epoch_utc,
        horizons_snapshot_id=horizons_snapshot_id,
        horizons_snapshot_ref=horizons_snapshot_ref,
    )
    return V2SourceBundle(
        schema="gcsi.v2_source_bundle",
        schema_version=2,
        bundle_id=bundle_id,
        replay_id=replay_id,
        candidate_plan_id=candidate_plan_id,
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
        acquisition_ledger_id=acquisition_ledger_id,
        temporal_reconciliation_id=temporal_reconciliation_id,
        verified_inventory_manifest_id=verified_inventory_manifest_id,
        verified_inventory_manifest_ref=verified_inventory_manifest_ref,
        label_snapshot_count=label_snapshot_count,
        candidate_logical_count=candidate_logical_count,
        candidate_source_count=candidate_source_count,
        eligible_logical_count=eligible_logical_count,
        eligible_source_count=eligible_source_count,
        ineligible_logical_count=ineligible_logical_count,
        ineligible_source_count=ineligible_source_count,
        logical_product_count=eligible_logical_count,
        source_record_count=eligible_source_count,
        decision_epoch_utc=decision_epoch_utc,
        horizons_snapshot_id=horizons_snapshot_id,
        horizons_snapshot_ref=horizons_snapshot_ref,
    )


def save_source_bundle(bundle: V2SourceBundle, path: Path) -> None:
    """Serialize bundle to JSON at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bundle.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("Saved source bundle index to %s", path)


def load_source_bundle(path: Path) -> V2SourceBundle:
    """Production trust loader: bounded, confined, bundle_id verified.

    Enforces:
    - No path traversal (..)
    - Path must resolve inside data/replays/
    - Not a symlink
    - Regular JSON file
    - Bounded read (2 MiB)
    - Strict typed model validation
    - bundle_id recomputed and verified

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is too large, invalid JSON, fails validation, or bundle_id mismatch.
    """
    # Check for traversal
    original_parts = path.parts
    if any(part == ".." for part in original_parts):
        raise ValueError(
            f"Source bundle path contains traversal sequences: {path!r}."
        )

    resolved = path.resolve()
    allowed_dir = _ALLOWED_DIR.resolve()

    try:
        resolved.relative_to(allowed_dir)
    except ValueError as exc:
        raise ValueError(
            f"Source bundle path {path!r} resolves outside allowed directory "
            f"{allowed_dir!r}."
        ) from exc

    if path.is_symlink():
        raise ValueError(f"Source bundle path must not be a symlink: {path!r}.")
    if resolved.is_symlink():
        raise ValueError(
            f"Source bundle resolved path must not be a symlink: {resolved!r}."
        )

    if not resolved.is_file():
        raise FileNotFoundError(f"Source bundle file not found: {resolved!r}")

    size = resolved.stat().st_size
    if size > _MAX_BUNDLE_BYTES:
        raise ValueError(
            f"Source bundle file exceeds maximum size ({_MAX_BUNDLE_BYTES} bytes): "
            f"{path!r} is {size} bytes."
        )

    raw = resolved.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > _MAX_BUNDLE_BYTES:
        raise ValueError(
            f"Source bundle content exceeds maximum size ({_MAX_BUNDLE_BYTES} bytes)."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Source bundle file is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Source bundle must be a JSON object.")

    if "bundle_id" not in data:
        raise ValueError("Source bundle is missing required field: 'bundle_id'.")

    # Recompute bundle_id before model validation
    expected_id = _compute_bundle_id(
        replay_id=data.get("replay_id", ""),
        candidate_plan_id=data.get("candidate_plan_id", ""),
        discovery_evidence_artifact_id=data.get("discovery_evidence_artifact_id", ""),
        acquisition_ledger_id=data.get("acquisition_ledger_id", ""),
        temporal_reconciliation_id=data.get("temporal_reconciliation_id", ""),
        verified_inventory_manifest_id=data.get("verified_inventory_manifest_id", ""),
        verified_inventory_manifest_ref=data.get("verified_inventory_manifest_ref", ""),
        label_snapshot_count=data.get("label_snapshot_count", 0),
        candidate_logical_count=data.get("candidate_logical_count", 0),
        candidate_source_count=data.get("candidate_source_count", 0),
        eligible_logical_count=data.get("eligible_logical_count", 0),
        eligible_source_count=data.get("eligible_source_count", 0),
        ineligible_logical_count=data.get("ineligible_logical_count", 0),
        ineligible_source_count=data.get("ineligible_source_count", 0),
        decision_epoch_utc=data.get("decision_epoch_utc", ""),
        horizons_snapshot_id=data.get("horizons_snapshot_id"),
        horizons_snapshot_ref=data.get("horizons_snapshot_ref"),
    )
    if data["bundle_id"] != expected_id:
        raise ValueError(
            f"Source bundle bundle_id mismatch: "
            f"stored {data['bundle_id']!r} != computed {expected_id!r}. "
            "Source bundle has been mutated since bundle_id was computed."
        )

    try:
        bundle = V2SourceBundle.model_validate(data, strict=False)
    except Exception as exc:
        raise ValueError(
            f"Source bundle failed typed model validation: {exc}"
        ) from exc

    return bundle
