"""GCSI Phase 6F-B2.2 — V2 Source Bundle Index.

A source bundle index that aggregates all B2.2 artifacts:
  - acquisition plan
  - discovery evidence sidecar
  - acquisition ledger
  - verified inventory manifest
  - (optionally) Horizons geometry snapshot

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

_BUNDLE_ID_PREFIX: str = "gcsi.v2_source_bundle:v1:"


# ---------------------------------------------------------------------------
# Bundle ID computation
# ---------------------------------------------------------------------------


def _compute_bundle_id(
    replay_id: str,
    acquisition_plan_id: str,
    discovery_evidence_artifact_id: str,
    acquisition_ledger_id: str,
    verified_inventory_manifest_id: str,
    verified_inventory_manifest_ref: str,
    label_snapshot_count: int,
    logical_product_count: int,
    source_record_count: int,
    decision_epoch_utc: str,
    horizons_snapshot_id: Optional[str],
    horizons_snapshot_ref: Optional[str],
) -> str:
    """Compute deterministic bundle_id over canonical content (excluding bundle_id)."""
    canonical = {
        "acquisition_ledger_id": acquisition_ledger_id,
        "acquisition_plan_id": acquisition_plan_id,
        "decision_epoch_utc": decision_epoch_utc,
        "discovery_evidence_artifact_id": discovery_evidence_artifact_id,
        "horizons_snapshot_id": horizons_snapshot_id,
        "horizons_snapshot_ref": horizons_snapshot_ref,
        "label_snapshot_count": label_snapshot_count,
        "logical_product_count": logical_product_count,
        "replay_id": replay_id,
        "source_record_count": source_record_count,
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

    Aggregates all B2.2 artifact identifiers and metadata for the
    Juno PJ62 large replay V2.

    Integrity: bundle_id is a deterministic SHA-256 over all semantic
    content (excluding bundle_id itself).

    B2.2 authoritative reconciliation counts:
    - label_snapshot_count = 527 (was 535; 8 products confirmed outside window)
    - logical_product_count = 403 (was 411; 6 JEDI + 2 UVS excluded)
    - source_record_count = 527 (was 535)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema: str = Field(description="Schema identifier: 'gcsi.v2_source_bundle'.")
    schema_version: int = Field(description="Schema version integer. Current: 1.")
    bundle_id: str = Field(
        description=(
            "SHA-256 of canonical content excluding bundle_id. "
            "Changes when any semantic content changes."
        )
    )
    replay_id: str = Field(description="Stable identifier for the replay.")
    acquisition_plan_id: str = Field(description="plan_id of the acquisition plan.")
    discovery_evidence_artifact_id: str = Field(
        description="artifact_id of the discovery evidence sidecar."
    )
    acquisition_ledger_id: str = Field(description="ledger_id of the acquisition ledger.")
    verified_inventory_manifest_id: str = Field(
        description="manifest_id of the verified inventory manifest."
    )
    verified_inventory_manifest_ref: str = Field(
        description="Relative path to the verified inventory manifest file."
    )
    label_snapshot_count: int = Field(
        description=(
            "Number of label snapshots acquired. "
            "B2.2 authoritative: 527 (was 535; 8 products confirmed outside window)."
        )
    )
    horizons_snapshot_id: Optional[str] = Field(
        default=None,
        description="snapshot_id of the Horizons geometry snapshot, if captured.",
    )
    horizons_snapshot_ref: Optional[str] = Field(
        default=None,
        description="Relative path to the Horizons snapshot file, if captured.",
    )
    logical_product_count: int = Field(
        description=(
            "Number of logical products in the verified manifest. "
            "B2.2 authoritative: 403 (was 411; 6 JEDI + 2 UVS excluded)."
        )
    )
    source_record_count: int = Field(
        description=(
            "Number of source records in the verified manifest. "
            "B2.2 authoritative: 527 (was 535)."
        )
    )
    decision_epoch_utc: str = Field(
        description="ISO-8601 UTC decision epoch (frozen)."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_source_bundle(
    replay_id: str,
    acquisition_plan_id: str,
    discovery_evidence_artifact_id: str,
    acquisition_ledger_id: str,
    verified_inventory_manifest_id: str,
    verified_inventory_manifest_ref: str,
    label_snapshot_count: int,
    logical_product_count: int,
    source_record_count: int,
    decision_epoch_utc: str,
    horizons_snapshot_id: Optional[str] = None,
    horizons_snapshot_ref: Optional[str] = None,
) -> V2SourceBundle:
    """Build a V2SourceBundle with auto-computed bundle_id.

    Parameters
    ----------
    replay_id:
        Stable replay identifier.
    acquisition_plan_id:
        plan_id from the acquisition plan.
    discovery_evidence_artifact_id:
        artifact_id from the discovery evidence sidecar.
    acquisition_ledger_id:
        ledger_id from the acquisition ledger.
    verified_inventory_manifest_id:
        manifest_id from the verified inventory manifest.
    verified_inventory_manifest_ref:
        Relative path to the verified inventory manifest JSON file.
    label_snapshot_count:
        Number of successfully acquired label snapshots.
    logical_product_count:
        Number of logical products in the manifest.
    source_record_count:
        Number of source records in the manifest.
    decision_epoch_utc:
        ISO-8601 UTC decision epoch string.
    horizons_snapshot_id:
        snapshot_id of the Horizons geometry snapshot (optional).
    horizons_snapshot_ref:
        Relative path to the Horizons snapshot file (optional).

    Returns
    -------
    V2SourceBundle
        Fully validated bundle.
    """
    bundle_id = _compute_bundle_id(
        replay_id=replay_id,
        acquisition_plan_id=acquisition_plan_id,
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
        acquisition_ledger_id=acquisition_ledger_id,
        verified_inventory_manifest_id=verified_inventory_manifest_id,
        verified_inventory_manifest_ref=verified_inventory_manifest_ref,
        label_snapshot_count=label_snapshot_count,
        logical_product_count=logical_product_count,
        source_record_count=source_record_count,
        decision_epoch_utc=decision_epoch_utc,
        horizons_snapshot_id=horizons_snapshot_id,
        horizons_snapshot_ref=horizons_snapshot_ref,
    )
    return V2SourceBundle(
        schema="gcsi.v2_source_bundle",
        schema_version=1,
        bundle_id=bundle_id,
        replay_id=replay_id,
        acquisition_plan_id=acquisition_plan_id,
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
        acquisition_ledger_id=acquisition_ledger_id,
        verified_inventory_manifest_id=verified_inventory_manifest_id,
        verified_inventory_manifest_ref=verified_inventory_manifest_ref,
        label_snapshot_count=label_snapshot_count,
        horizons_snapshot_id=horizons_snapshot_id,
        horizons_snapshot_ref=horizons_snapshot_ref,
        logical_product_count=logical_product_count,
        source_record_count=source_record_count,
        decision_epoch_utc=decision_epoch_utc,
    )


def save_source_bundle(bundle: V2SourceBundle, path: Path) -> None:
    """Serialize bundle to JSON at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bundle.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    logger.info("Saved source bundle index to %s", path)


def load_source_bundle(path: Path) -> V2SourceBundle:
    """Load and validate source bundle from JSON at path.

    Parameters
    ----------
    path:
        Path to the bundle JSON file.

    Returns
    -------
    V2SourceBundle
        Fully validated bundle.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    ValueError
        If the file is too large, invalid JSON, or fails validation.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Source bundle file not found: {path!r}")

    size = path.stat().st_size
    if size > _MAX_BUNDLE_BYTES:
        raise ValueError(
            f"Source bundle file exceeds maximum size ({_MAX_BUNDLE_BYTES} bytes): "
            f"{path!r} is {size} bytes."
        )

    raw = path.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > _MAX_BUNDLE_BYTES:
        raise ValueError(
            f"Source bundle content exceeds maximum size ({_MAX_BUNDLE_BYTES} bytes)."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Source bundle file is not valid JSON: {exc}") from exc

    return V2SourceBundle.model_validate(data, strict=False)
