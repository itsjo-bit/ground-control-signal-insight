"""GCSI Phase 6F-B2.2.1 — V2 Temporal Reconciliation Manifest.

Separates acquisition success from temporal eligibility.

A label can be:
  - successfully acquired (ACQUIRED_VERIFIED or REUSED_VERIFIED_SNAPSHOT)
  AND
  - temporally ineligible (INELIGIBLE_PRE_WINDOW or INELIGIBLE_POST_DECISION)

The reconciliation manifest binds every logical acquisition candidate to its
temporal classification, derived from verified authoritative label snapshots.

Architecture
------------
- V2TemporalReconciliationManifest: strict frozen model
- V2ReconciliationEntry: per-logical binding of classification + evidence
- ReconciliationClassification: ELIGIBLE / INELIGIBLE_PRE_WINDOW / INELIGIBLE_POST_DECISION
- reconciliation_id: deterministic SHA-256 over ALL semantic content

Mutation of any field changes reconciliation_id:
  stop time, classification, source record, snapshot ref, provenance, reason.

All models: frozen=True, extra="forbid".
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ReconciliationClassification(str, Enum):
    """Temporal classification for a logical acquisition candidate.

    ELIGIBLE:
        Observation stop time satisfies:
        ACCUMULATION_START_UTC < stop_utc <= DECISION_EPOCH_UTC.

    INELIGIBLE_PRE_WINDOW:
        Observation stop time is at or before ACCUMULATION_START_UTC.
        The product pre-dates the replay accumulation window.

    INELIGIBLE_POST_DECISION:
        Observation stop time is after DECISION_EPOCH_UTC.
        The product post-dates the replay decision epoch.
    """

    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE_PRE_WINDOW = "INELIGIBLE_PRE_WINDOW"
    INELIGIBLE_POST_DECISION = "INELIGIBLE_POST_DECISION"


# ---------------------------------------------------------------------------
# V2ReconciliationEntry
# ---------------------------------------------------------------------------


class V2ReconciliationEntry(BaseModel):
    """Temporal reconciliation entry for one logical acquisition candidate.

    Binds:
    - logical_product_id: the logical candidate (matches plan entry)
    - source_record_ids: source record IDs for each representation
    - snapshot_refs: POSIX repository-relative paths to label snapshots
    - provenance_ids: provenance IDs for each representation
    - authoritative_observation_stop_utc: authoritative stop time from verified label
    - classification: temporal eligibility result
    - reason_code: short machine-readable reason string
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    logical_product_id: str = Field(description="Logical product ID from acquisition plan.")
    source_record_ids: tuple[str, ...] = Field(
        description="Source record IDs (one per representation)."
    )
    snapshot_refs: tuple[str, ...] = Field(
        description="POSIX repository-relative snapshot paths (one per representation)."
    )
    provenance_ids: tuple[str, ...] = Field(
        description="Provenance IDs (one per representation)."
    )
    authoritative_observation_stop_utc: Optional[str] = Field(
        description=(
            "Authoritative ISO-8601 UTC observation stop time from verified label. "
            "None only if label acquisition did not yield a stop time."
        )
    )
    classification: ReconciliationClassification = Field(
        description="Temporal classification derived from authoritative label."
    )
    reason_code: str = Field(
        description=(
            "Short machine-readable reason for classification. "
            "E.g. 'STOP_WITHIN_WINDOW', 'STOP_PRE_ACCUMULATION_START', "
            "'STOP_POST_DECISION_EPOCH'."
        )
    )

    @model_validator(mode="after")
    def _validate_arrays_consistent(self) -> "V2ReconciliationEntry":
        """Require source_record_ids, snapshot_refs, and provenance_ids to have same length."""
        n = len(self.source_record_ids)
        if len(self.snapshot_refs) != n:
            raise ValueError(
                f"snapshot_refs length {len(self.snapshot_refs)} != "
                f"source_record_ids length {n} for {self.logical_product_id!r}."
            )
        if len(self.provenance_ids) != n:
            raise ValueError(
                f"provenance_ids length {len(self.provenance_ids)} != "
                f"source_record_ids length {n} for {self.logical_product_id!r}."
            )
        return self


# ---------------------------------------------------------------------------
# V2TemporalReconciliationManifest
# ---------------------------------------------------------------------------


class V2TemporalReconciliationManifest(BaseModel):
    """Temporal reconciliation manifest for one V2 acquisition run.

    Binds every logical candidate from the acquisition plan to its temporal
    classification, derived from verified authoritative label snapshots.

    All counts are derived from entries; they are stored for fast loading
    and cross-checked on model validation.

    reconciliation_id is a deterministic SHA-256 over all semantic content
    (excluding reconciliation_id itself). Any mutation of any semantic field
    changes reconciliation_id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema: str = Field(description="Schema identifier: 'gcsi.v2_temporal_reconciliation'.")
    schema_version: int = Field(description="Schema version integer. Current: 1.")
    reconciliation_id: str = Field(
        description=(
            "SHA-256 of canonical content excluding reconciliation_id. "
            "Changes when any semantic content changes."
        )
    )

    replay_id: str = Field(description="Stable identifier for the replay.")
    candidate_plan_id: str = Field(description="plan_id of the candidate acquisition plan.")
    discovery_evidence_artifact_id: str = Field(
        description="artifact_id of the discovery evidence sidecar."
    )

    accumulation_start_utc: str = Field(
        description="ISO-8601 UTC accumulation start (frozen)."
    )
    decision_epoch_utc: str = Field(
        description="ISO-8601 UTC decision epoch (frozen)."
    )

    candidate_logical_count: int = Field(
        description="Total logical candidate count from acquisition plan."
    )
    candidate_source_count: int = Field(
        description="Total source representation count from acquisition plan."
    )

    eligible_logical_count: int = Field(
        description="Number of ELIGIBLE logical products."
    )
    eligible_source_count: int = Field(
        description="Number of ELIGIBLE source representations."
    )

    ineligible_logical_count: int = Field(
        description="Number of ineligible logical products (PRE + POST)."
    )
    ineligible_source_count: int = Field(
        description="Number of ineligible source representations."
    )

    entries: tuple[V2ReconciliationEntry, ...] = Field(
        description="One entry per logical candidate, sorted by logical_product_id."
    )

    @model_validator(mode="after")
    def _validate_counts(self) -> "V2TemporalReconciliationManifest":
        """Cross-check all counts against entries."""
        total = len(self.entries)
        if total != self.candidate_logical_count:
            raise ValueError(
                f"candidate_logical_count {self.candidate_logical_count} != "
                f"entry count {total}."
            )

        eligible_logical = sum(
            1 for e in self.entries
            if e.classification == ReconciliationClassification.ELIGIBLE
        )
        ineligible_logical = total - eligible_logical

        if eligible_logical != self.eligible_logical_count:
            raise ValueError(
                f"eligible_logical_count {self.eligible_logical_count} != "
                f"derived {eligible_logical}."
            )
        if ineligible_logical != self.ineligible_logical_count:
            raise ValueError(
                f"ineligible_logical_count {self.ineligible_logical_count} != "
                f"derived {ineligible_logical}."
            )

        # Source counts: each entry has len(source_record_ids) reps
        candidate_source = sum(len(e.source_record_ids) for e in self.entries)
        if candidate_source != self.candidate_source_count:
            raise ValueError(
                f"candidate_source_count {self.candidate_source_count} != "
                f"derived {candidate_source}."
            )

        eligible_source = sum(
            len(e.source_record_ids)
            for e in self.entries
            if e.classification == ReconciliationClassification.ELIGIBLE
        )
        ineligible_source = candidate_source - eligible_source

        if eligible_source != self.eligible_source_count:
            raise ValueError(
                f"eligible_source_count {self.eligible_source_count} != "
                f"derived {eligible_source}."
            )
        if ineligible_source != self.ineligible_source_count:
            raise ValueError(
                f"ineligible_source_count {self.ineligible_source_count} != "
                f"derived {ineligible_source}."
            )

        return self


# ---------------------------------------------------------------------------
# Reconciliation ID computation
# ---------------------------------------------------------------------------

_RECONCILIATION_ID_PREFIX: str = "gcsi.v2_temporal_reconciliation:v1:"


def compute_reconciliation_id(
    replay_id: str,
    candidate_plan_id: str,
    discovery_evidence_artifact_id: str,
    accumulation_start_utc: str,
    decision_epoch_utc: str,
    candidate_logical_count: int,
    candidate_source_count: int,
    eligible_logical_count: int,
    eligible_source_count: int,
    ineligible_logical_count: int,
    ineligible_source_count: int,
    entries: list[dict],
) -> str:
    """Compute deterministic reconciliation_id over canonical content.

    All parameters except reconciliation_id participate.
    Entries are canonically sorted by logical_product_id.
    """
    canonical_entries = sorted(entries, key=lambda e: e["logical_product_id"])
    canonical = {
        "accumulation_start_utc": accumulation_start_utc,
        "candidate_logical_count": candidate_logical_count,
        "candidate_plan_id": candidate_plan_id,
        "candidate_source_count": candidate_source_count,
        "decision_epoch_utc": decision_epoch_utc,
        "discovery_evidence_artifact_id": discovery_evidence_artifact_id,
        "eligible_logical_count": eligible_logical_count,
        "eligible_source_count": eligible_source_count,
        "entries": canonical_entries,
        "ineligible_logical_count": ineligible_logical_count,
        "ineligible_source_count": ineligible_source_count,
        "replay_id": replay_id,
    }
    payload = _RECONCILIATION_ID_PREFIX + json.dumps(
        canonical, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def save_reconciliation_manifest(
    manifest: V2TemporalReconciliationManifest,
    path: Path,
) -> None:
    """Serialize manifest to JSON at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


_MAX_RECONCILIATION_BYTES: int = 4 * 1024 * 1024  # 4 MiB
_ALLOWED_DIR: Path = Path(__file__).resolve().parents[3] / "data" / "replays"


def load_reconciliation_manifest(path: Path) -> V2TemporalReconciliationManifest:
    """Production trust loader: bounded, confined, ID-verified.

    Enforces:
    - No path traversal (..)
    - Path must resolve inside data/replays/
    - Not a symlink
    - Regular JSON file
    - Bounded read (4 MiB)
    - Strict typed model validation
    - reconciliation_id recomputed and verified
    """
    # Check for traversal
    original_parts = path.parts
    if any(part == ".." for part in original_parts):
        raise ValueError(
            f"Reconciliation manifest path contains traversal sequences: {path!r}."
        )

    resolved = path.resolve()
    allowed_dir = _ALLOWED_DIR.resolve()

    try:
        resolved.relative_to(allowed_dir)
    except ValueError as exc:
        raise ValueError(
            f"Reconciliation manifest path {path!r} resolves outside allowed directory "
            f"{allowed_dir!r}."
        ) from exc

    if path.is_symlink():
        raise ValueError(f"Reconciliation manifest path must not be a symlink: {path!r}.")
    if resolved.is_symlink():
        raise ValueError(
            f"Reconciliation manifest resolved path must not be a symlink: {resolved!r}."
        )

    if not resolved.is_file():
        raise FileNotFoundError(
            f"Reconciliation manifest file not found: {resolved!r}."
        )

    size = resolved.stat().st_size
    if size > _MAX_RECONCILIATION_BYTES:
        raise ValueError(
            f"Reconciliation manifest file exceeds maximum size "
            f"({_MAX_RECONCILIATION_BYTES} bytes): {size}."
        )

    raw = resolved.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Reconciliation manifest is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Reconciliation manifest must be a JSON object.")

    # Require reconciliation_id before model validation
    if "reconciliation_id" not in data:
        raise ValueError(
            "Reconciliation manifest is missing required field: 'reconciliation_id'."
        )

    # Recompute reconciliation_id before model validation
    entries_raw = [
        {
            "authoritative_observation_stop_utc": e.get("authoritative_observation_stop_utc"),
            "classification": e["classification"],
            "logical_product_id": e["logical_product_id"],
            "provenance_ids": e["provenance_ids"],
            "reason_code": e["reason_code"],
            "snapshot_refs": e["snapshot_refs"],
            "source_record_ids": e["source_record_ids"],
        }
        for e in data.get("entries", [])
    ]
    expected_id = compute_reconciliation_id(
        replay_id=data.get("replay_id", ""),
        candidate_plan_id=data.get("candidate_plan_id", ""),
        discovery_evidence_artifact_id=data.get("discovery_evidence_artifact_id", ""),
        accumulation_start_utc=data.get("accumulation_start_utc", ""),
        decision_epoch_utc=data.get("decision_epoch_utc", ""),
        candidate_logical_count=data.get("candidate_logical_count", 0),
        candidate_source_count=data.get("candidate_source_count", 0),
        eligible_logical_count=data.get("eligible_logical_count", 0),
        eligible_source_count=data.get("eligible_source_count", 0),
        ineligible_logical_count=data.get("ineligible_logical_count", 0),
        ineligible_source_count=data.get("ineligible_source_count", 0),
        entries=entries_raw,
    )
    if data["reconciliation_id"] != expected_id:
        raise ValueError(
            f"Reconciliation manifest reconciliation_id mismatch: "
            f"stored {data['reconciliation_id']!r} != "
            f"computed {expected_id!r}. Manifest has been mutated."
        )

    try:
        manifest = V2TemporalReconciliationManifest.model_validate(data, strict=False)
    except Exception as exc:
        raise ValueError(
            f"Reconciliation manifest failed typed model validation: {exc}"
        ) from exc

    return manifest
