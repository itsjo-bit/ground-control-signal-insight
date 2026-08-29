"""GCSI Phase 6F-B2.2 — V2 Inventory Acquisition Layer.

Orchestrates bulk acquisition of 535 planned label representations for the
Juno PJ62 large historical replay V2.

Architecture:
  1. Load load_bound_v2_acquisition_plan() → BoundAcquisitionPlan (plan + sidecar)
  2. For each of 535 planned label representations (deterministic order):
     - Check for existing snapshot (resumption, Section 21)
     - Fetch label bytes via HTTPS (Section 12 retry logic)
     - Parse via parse_generic_pds3_label or parse_generic_pds4_label (ONLY adapters)
     - Build ArchiveCaptureRecord
     - Write to ArchiveLabelSnapshotStore (Section A)
     - Reload snapshot (Section B verify)
     - Record in ledger
  3. Compute ledger_id (Section 24)

All models: frozen=True, extra="forbid".
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.app.mission_sources.adapters.pds3_adapter import (
    FGM_PDS3_PROFILE,
    JADE_PDS3_PROFILE,
    JEDI_PDS3_PROFILE,
    JUNOCAM_PDS3_PROFILE,
    WAVES_BURST_PDS3_PROFILE,
    WAVES_SURVEY_PDS3_PROFILE,
    GenericPds3AdapterUnavailableError,
    GenericPds3AdapterValidationError,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    JIRAM_PDS4_PROFILE,
    MWR_GENERIC_PDS4_PROFILE,
    UVS_PDS4_PROFILE,
    GenericPds4AdapterUnavailableError,
    GenericPds4AdapterValidationError,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotValidationError,
)
from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_UTC,
    AcquisitionLogicalProductEntry,
    AcquisitionSourceRepresentation,
    HistoricalReplayV2AcquisitionPlan,
)
from backend.app.mission_sources.v2_acquisition_plan_builder import (
    load_bound_v2_acquisition_plan,
)
from backend.app.provenance.models import ProvenanceRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Default snapshot root (relative to repo root).
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_SNAPSHOT_ROOT: Path = (
    _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"
)

#: Ledger output path.
_DEFAULT_LEDGER_PATH: Path = (
    _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_ledger.json"
)

#: Maximum label response bytes for bounded streaming read (2 MiB for PDS4, 512 KiB for PDS3).
_MAX_LABEL_BYTES: int = 2 * 1024 * 1024

#: Ledger ID prefix.
_LEDGER_ID_PREFIX: str = "gcsi.v2_acquisition_ledger:v1:"

# ---------------------------------------------------------------------------
# Profile registry: maps profile_id → parser profile object
# ---------------------------------------------------------------------------

_PDS3_PROFILES = {
    "waves_burst_pds3": WAVES_BURST_PDS3_PROFILE,
    "waves_survey_pds3": WAVES_SURVEY_PDS3_PROFILE,
    "junocam_pds3": JUNOCAM_PDS3_PROFILE,
    "fgm_pds3": FGM_PDS3_PROFILE,
    "jade_pds3": JADE_PDS3_PROFILE,
    "jedi_pds3": JEDI_PDS3_PROFILE,
}

_PDS4_PROFILES = {
    "jiram_pds4": JIRAM_PDS4_PROFILE,
    "mwr_generic_pds4": MWR_GENERIC_PDS4_PROFILE,
    "uvs_pds4": UVS_PDS4_PROFILE,
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AcquisitionStatus(str, Enum):
    PENDING = "PENDING"
    ACQUIRED_VERIFIED = "ACQUIRED_VERIFIED"
    REUSED_VERIFIED_SNAPSHOT = "REUSED_VERIFIED_SNAPSHOT"
    FAILED_TRANSIENT = "FAILED_TRANSIENT"
    FAILED_UNAVAILABLE = "FAILED_UNAVAILABLE"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_IDENTITY = "FAILED_IDENTITY"
    FAILED_TEMPORAL = "FAILED_TEMPORAL"
    FAILED_SNAPSHOT = "FAILED_SNAPSHOT"


class TemporalVerificationStatus(str, Enum):
    VERIFIED_ELIGIBLE = "VERIFIED_ELIGIBLE"
    FAILED_PRE = "FAILED_PRE"
    FAILED_POST = "FAILED_POST"
    FAILED_STOP_BEFORE_START = "FAILED_STOP_BEFORE_START"
    PENDING = "PENDING"


class SizeVerificationStatus(str, Enum):
    SIZE_METADATA_EXACT = "SIZE_METADATA_EXACT"
    SIZE_UNKNOWN = "SIZE_UNKNOWN"
    SIZE_DISCOVERED_APPROXIMATE = "SIZE_DISCOVERED_APPROXIMATE"


# ---------------------------------------------------------------------------
# Ledger models
# ---------------------------------------------------------------------------


class AcquisitionLedgerRow(BaseModel):
    """One row in the acquisition ledger.  Captures per-representation outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acquisition_index: int
    logical_product_id: str
    instrument: str
    representation_role: str
    source_standard: str  # "pds3" or "pds4"
    label_url: str
    normalizer_id: str
    profile_id: str
    attempt_count: int
    acquisition_status: AcquisitionStatus
    retrieved_at: Optional[str] = None  # ISO UTC or None
    raw_label_sha256: Optional[str] = None
    source_record_id: Optional[str] = None
    archive_product_id: Optional[str] = None
    archive_version: Optional[str] = None
    snapshot_ref: Optional[str] = None
    snapshot_id: Optional[str] = None
    provenance_id: Optional[str] = None
    observation_start_utc: Optional[str] = None
    observation_stop_utc: Optional[str] = None
    temporal_verification_status: Optional[str] = None
    archive_total_size_bytes: Optional[int] = None
    size_verification_status: Optional[str] = None
    error_class: Optional[str] = None
    error_detail_code: Optional[str] = None


class AcquisitionLedger(BaseModel):
    """Complete acquisition ledger for one replay run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ledger_id: str  # SHA-256 of canonical content excluding ledger_id
    replay_id: str
    plan_id: str
    rows: tuple[AcquisitionLedgerRow, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _snapshot_path_for_url(
    instrument: str,
    label_url: str,
    snapshot_root: Path,
) -> Path:
    """Deterministic path: <snapshot_root>/<instrument>/<sha256(label_url)>.json"""
    url_hash = hashlib.sha256(label_url.encode("utf-8")).hexdigest()
    return snapshot_root / instrument.lower() / f"{url_hash}.json"


def _build_representation_sequence(
    plan: HistoricalReplayV2AcquisitionPlan,
) -> list[tuple[int, AcquisitionLogicalProductEntry, AcquisitionSourceRepresentation]]:
    """Return (acquisition_index, entry, rep) sorted by logical_product_id, role, url."""
    items: list[tuple[str, str, str, AcquisitionLogicalProductEntry, AcquisitionSourceRepresentation]] = []
    for entry in plan.logical_entries:
        for rep in entry.representations:
            items.append((
                entry.logical_product_id,
                rep.representation_role.value,
                rep.label_url,
                entry,
                rep,
            ))
    items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [
        (idx, item[3], item[4])
        for idx, item in enumerate(items)
    ]


def _fetch_label_bytes(
    label_url: str,
    source_standard: str,
    client: httpx.Client,
    max_bytes: int = _MAX_LABEL_BYTES,
) -> tuple[bytes, datetime]:
    """Fetch exactly one label. HTTPS only, follow_redirects=False.

    Returns (raw_bytes, retrieved_at).

    Raises:
    - GenericPds3/4AdapterUnavailableError for transient errors (network timeout,
      connection reset, HTTP 429, HTTP 5xx)
    - GenericPds3/4AdapterValidationError for 404, other 4xx, 3xx redirect,
      URL trust failure
    """
    is_pds4 = source_standard == "pds4"
    unavailable_err = GenericPds4AdapterUnavailableError if is_pds4 else GenericPds3AdapterUnavailableError
    validation_err = GenericPds4AdapterValidationError if is_pds4 else GenericPds3AdapterValidationError

    retrieved_at = datetime.now(tz=timezone.utc)
    try:
        with client.stream("GET", label_url) as response:
            status = response.status_code
            if 300 <= status <= 399:
                raise validation_err(
                    f"HTTP redirect {status} for {label_url!r}: redirects not permitted."
                )
            if status == 404:
                raise validation_err(
                    f"HTTP 404 for {label_url!r}: resource not found (not retryable)."
                )
            if status == 429:
                raise unavailable_err(
                    f"HTTP 429 for {label_url!r}: rate limited (transient)."
                )
            if 500 <= status <= 599:
                raise unavailable_err(
                    f"HTTP {status} for {label_url!r}: server error (transient)."
                )
            if status != 200:
                raise validation_err(
                    f"HTTP {status} for {label_url!r}: unexpected status."
                )
            # Bounded streaming read
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > max_bytes:
                    raise validation_err(
                        f"Label response exceeds maximum size ({max_bytes} bytes) for {label_url!r}."
                    )
                chunks.append(chunk)
            raw_bytes = b"".join(chunks)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
        raise unavailable_err(
            f"Network error fetching {label_url!r}: {exc}"
        ) from exc
    except (GenericPds4AdapterUnavailableError, GenericPds3AdapterUnavailableError,
            GenericPds4AdapterValidationError, GenericPds3AdapterValidationError):
        raise
    except httpx.HTTPError as exc:
        raise unavailable_err(
            f"HTTP error fetching {label_url!r}: {exc}"
        ) from exc

    retrieved_at = datetime.now(tz=timezone.utc)
    return raw_bytes, retrieved_at


def _parse_label(
    raw_bytes: bytes,
    label_url: str,
    source_standard: str,
    normalizer_id: str,
    profile_id: str,
    retrieved_at: datetime,
) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
    """Dispatch to parse_generic_pds3_label or parse_generic_pds4_label.

    These are the ONLY parsers permitted. Never bypass adapters.
    """
    if source_standard == "pds4":
        profile = _PDS4_PROFILES.get(profile_id)
        if profile is None:
            raise GenericPds4AdapterValidationError(
                f"Unknown PDS4 profile_id: {profile_id!r}."
            )
        return parse_generic_pds4_label(
            raw_bytes=raw_bytes,
            label_url=label_url,
            profile=profile,
            retrieved_at=retrieved_at,
        )
    elif source_standard == "pds3":
        profile = _PDS3_PROFILES.get(profile_id)
        if profile is None:
            raise GenericPds3AdapterValidationError(
                f"Unknown PDS3 profile_id: {profile_id!r}."
            )
        return parse_generic_pds3_label(
            raw_bytes=raw_bytes,
            source_ref=label_url,
            profile=profile,
            retrieved_at=retrieved_at,
        )
    else:
        raise GenericPds3AdapterValidationError(
            f"Unknown source_standard: {source_standard!r}."
        )


def _check_temporal_eligibility(
    observation_stop_utc: Optional[datetime],
) -> TemporalVerificationStatus:
    """Check stop time against the frozen accumulation window.

    Eligible iff: ACCUMULATION_START_UTC < stop_utc <= DECISION_EPOCH_UTC.
    """
    if observation_stop_utc is None:
        return TemporalVerificationStatus.PENDING

    stop = observation_stop_utc.astimezone(timezone.utc) if observation_stop_utc.tzinfo else observation_stop_utc.replace(tzinfo=timezone.utc)

    if stop <= ACCUMULATION_START_UTC:
        return TemporalVerificationStatus.FAILED_PRE
    if stop > DECISION_EPOCH_UTC:
        return TemporalVerificationStatus.FAILED_POST
    return TemporalVerificationStatus.VERIFIED_ELIGIBLE


def _derive_size_verification_status(product: ArchiveScienceProduct) -> SizeVerificationStatus:
    """Map product size certainty to SizeVerificationStatus."""
    from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty
    if not product.data_files:
        return SizeVerificationStatus.SIZE_UNKNOWN
    certainties = {f.size_certainty for f in product.data_files}
    if ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT in certainties and len(certainties) == 1:
        return SizeVerificationStatus.SIZE_METADATA_EXACT
    if ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE in certainties:
        return SizeVerificationStatus.SIZE_DISCOVERED_APPROXIMATE
    return SizeVerificationStatus.SIZE_UNKNOWN


def _compute_ledger_id(
    rows: list[AcquisitionLedgerRow],
    replay_id: str,
    plan_id: str,
) -> str:
    """SHA-256 over canonical sorted content excluding ledger_id."""
    canonical_rows = []
    for row in sorted(rows, key=lambda r: r.acquisition_index):
        canonical_rows.append({
            "acquisition_index": row.acquisition_index,
            "acquisition_status": row.acquisition_status.value,
            "attempt_count": row.attempt_count,
            "instrument": row.instrument,
            "label_url": row.label_url,
            "logical_product_id": row.logical_product_id,
            "normalizer_id": row.normalizer_id,
            "profile_id": row.profile_id,
            "provenance_id": row.provenance_id,
            "raw_label_sha256": row.raw_label_sha256,
            "representation_role": row.representation_role,
            "retrieved_at": row.retrieved_at,
            "snapshot_id": row.snapshot_id,
            "snapshot_ref": row.snapshot_ref,
            "source_record_id": row.source_record_id,
            "source_standard": row.source_standard,
            "temporal_verification_status": row.temporal_verification_status,
        })
    canonical = {
        "plan_id": plan_id,
        "replay_id": replay_id,
        "rows": canonical_rows,
    }
    payload = _LEDGER_ID_PREFIX + json.dumps(canonical, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Canary selection (Section 14)
# ---------------------------------------------------------------------------

#: Canary profile-to-instrument mapping: maps profile_id to first-entry selector key.
_CANARY_PROFILE_INSTRUMENT: dict[str, str] = {
    "jiram_pds4": "JIRAM",
    "mwr_generic_pds4": "MWR",
    "uvs_pds4": "UVS",
    "junocam_pds3": "JUNOCAM",
    "fgm_pds3": "FGM",
    "jade_pds3": "JADE",
    "jedi_pds3": "JEDI",
    "waves_survey_pds3": "WAVES",
    "waves_burst_pds3": "WAVES",
}


def _select_canary_indices(
    sequence: list[tuple[int, AcquisitionLogicalProductEntry, AcquisitionSourceRepresentation]],
) -> set[int]:
    """Select one deterministic representative per profile (canary-first ordering).

    Returns the set of acquisition indices that are canaries.
    """
    seen_profiles: set[str] = set()
    canary_indices: set[int] = set()
    for idx, _entry, rep in sequence:
        if rep.profile_id not in seen_profiles:
            seen_profiles.add(rep.profile_id)
            canary_indices.add(idx)
    return canary_indices


# ---------------------------------------------------------------------------
# Core acquisition logic per representation
# ---------------------------------------------------------------------------


def _acquire_one(
    idx: int,
    entry: AcquisitionLogicalProductEntry,
    rep: AcquisitionSourceRepresentation,
    snapshot_root: Path,
    client: httpx.Client,
    max_attempts: int,
    backoff_seconds: tuple[float, ...],
    dry_run: bool,
) -> AcquisitionLedgerRow:
    """Acquire one representation and return the ledger row."""
    label_url = rep.label_url
    source_standard = rep.source_standard.value
    normalizer_id = rep.normalizer_id
    profile_id = rep.profile_id
    instrument = entry.instrument

    snapshot_path = _snapshot_path_for_url(instrument, label_url, snapshot_root)

    # -----------------------------------------------------------------------
    # Section 21: Snapshot resumption — check if valid snapshot already exists
    # -----------------------------------------------------------------------
    if snapshot_path.exists():
        try:
            existing_product, existing_prov = ArchiveLabelSnapshotStore.load(snapshot_path)
            # Verify the snapshot is for this exact URL/normalizer/profile
            if (
                existing_product.source_label_ref == label_url
                and existing_prov is not None
            ):
                # Check normalizer/profile consistency via envelope fields
                import json as _json
                raw_envelope = snapshot_path.read_text(encoding="utf-8")
                env_data = _json.loads(raw_envelope)
                env_normalizer = env_data.get("normalizer_id", "")
                env_profile = env_data.get("profile_id", "")
                if env_normalizer == normalizer_id and env_profile == profile_id:
                    temporal_status = _check_temporal_eligibility(
                        existing_product.observation_stop_utc
                    )
                    size_status = _derive_size_verification_status(existing_product)
                    return AcquisitionLedgerRow(
                        acquisition_index=idx,
                        logical_product_id=entry.logical_product_id,
                        instrument=instrument,
                        representation_role=rep.representation_role.value,
                        source_standard=source_standard,
                        label_url=label_url,
                        normalizer_id=normalizer_id,
                        profile_id=profile_id,
                        attempt_count=0,
                        acquisition_status=AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT,
                        retrieved_at=(
                            existing_prov.retrieved_at.isoformat()
                            if existing_prov.retrieved_at
                            else None
                        ),
                        raw_label_sha256=existing_prov.content_sha256,
                        source_record_id=existing_product.source_record_id,
                        archive_product_id=existing_product.source_product_id,
                        archive_version=existing_product.source_version,
                        snapshot_ref=str(snapshot_path),
                        snapshot_id=env_data.get("snapshot_id"),
                        provenance_id=existing_prov.provenance_id,
                        observation_start_utc=(
                            existing_product.observation_start_utc.isoformat()
                            if existing_product.observation_start_utc
                            else None
                        ),
                        observation_stop_utc=(
                            existing_product.observation_stop_utc.isoformat()
                            if existing_product.observation_stop_utc
                            else None
                        ),
                        temporal_verification_status=temporal_status.value,
                        archive_total_size_bytes=existing_product.total_data_size_bytes,
                        size_verification_status=size_status.value,
                    )
                # Wrong URL/normalizer/profile in snapshot → treat as corrupt
        except Exception as exc:
            logger.warning(
                "Snapshot at %s failed validation: %s. Treating as corrupt.",
                snapshot_path, exc
            )
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=0,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                error_class="SnapshotIntegrityError",
                error_detail_code=type(exc).__name__,
            )

    # -----------------------------------------------------------------------
    # Dry-run mode (for testing)
    # -----------------------------------------------------------------------
    if dry_run:
        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=entry.logical_product_id,
            instrument=instrument,
            representation_role=rep.representation_role.value,
            source_standard=source_standard,
            label_url=label_url,
            normalizer_id=normalizer_id,
            profile_id=profile_id,
            attempt_count=0,
            acquisition_status=AcquisitionStatus.PENDING,
        )

    # -----------------------------------------------------------------------
    # Section 12: Fetch with retry logic
    # -----------------------------------------------------------------------
    last_exc: Optional[Exception] = None
    attempt = 0

    for attempt in range(1, max_attempts + 1):
        try:
            raw_bytes, retrieved_at = _fetch_label_bytes(
                label_url=label_url,
                source_standard=source_standard,
                client=client,
            )
        except (GenericPds3AdapterUnavailableError, GenericPds4AdapterUnavailableError) as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = backoff_seconds[attempt - 1] if attempt - 1 < len(backoff_seconds) else backoff_seconds[-1]
                logger.warning(
                    "Transient error for %s (attempt %d/%d): %s — retrying in %.1fs",
                    label_url, attempt, max_attempts, exc, delay,
                )
                time.sleep(delay)
                continue
            # Exhausted retries
            http_404 = "404" in str(exc)
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_UNAVAILABLE if http_404 else AcquisitionStatus.FAILED_TRANSIENT,
                error_class=type(exc).__name__,
                error_detail_code=str(exc)[:120],
            )
        except (GenericPds3AdapterValidationError, GenericPds4AdapterValidationError) as exc:
            # Not retryable: 3xx, 4xx, trust failure
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_VALIDATION,
                error_class=type(exc).__name__,
                error_detail_code=str(exc)[:120],
            )

        # Successful fetch → parse
        raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        try:
            product, provenance = _parse_label(
                raw_bytes=raw_bytes,
                label_url=label_url,
                source_standard=source_standard,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                retrieved_at=retrieved_at,
            )
        except (GenericPds3AdapterValidationError, GenericPds4AdapterValidationError) as exc:
            # Parser failure → not retryable
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_VALIDATION,
                retrieved_at=retrieved_at.isoformat(),
                raw_label_sha256=raw_sha256,
                error_class=type(exc).__name__,
                error_detail_code=str(exc)[:120],
            )

        # -----------------------------------------------------------------------
        # Section 25: Identity Verification
        # -----------------------------------------------------------------------
        expected_id = rep.expected_archive_identity
        if expected_id is not None:
            actual_id = product.source_product_id
            identity_ok = False
            if source_standard == "pds3":
                # PDS3: case-insensitive comparison of PRODUCT_ID values.
                # Section 25.1 — SOURCE_NATIVE_IDENTITY or 25.2 PATH_DERIVED.
                identity_ok = actual_id.upper() == expected_id.upper()
            else:
                # PDS4: source_product_id is the full LID.
                # Section 25.1: if expected is a full LID, exact match.
                # Section 25.2: if expected is a filename stem (PATH_DERIVED),
                #   check that the LID's final colon-delimited component matches
                #   the expected stem case-insensitively — this is the deterministic
                #   PDS4 naming contract (LID = urn:nasa:pds:<bundle>:<col>:<stem>).
                if ":" in actual_id:
                    lid_stem = actual_id.split(":")[-1]
                    identity_ok = lid_stem.lower() == expected_id.lower()
                else:
                    identity_ok = actual_id == expected_id

            if not identity_ok:
                return AcquisitionLedgerRow(
                    acquisition_index=idx,
                    logical_product_id=entry.logical_product_id,
                    instrument=instrument,
                    representation_role=rep.representation_role.value,
                    source_standard=source_standard,
                    label_url=label_url,
                    normalizer_id=normalizer_id,
                    profile_id=profile_id,
                    attempt_count=attempt,
                    acquisition_status=AcquisitionStatus.FAILED_IDENTITY,
                    retrieved_at=retrieved_at.isoformat(),
                    raw_label_sha256=raw_sha256,
                    source_record_id=product.source_record_id,
                    archive_product_id=product.source_product_id,
                    error_class="IdentityMismatch",
                    error_detail_code=f"expected={expected_id!r} actual={actual_id!r}",
                )

        # -----------------------------------------------------------------------
        # Temporal verification
        # -----------------------------------------------------------------------
        temporal_status = _check_temporal_eligibility(product.observation_stop_utc)
        if temporal_status in (
            TemporalVerificationStatus.FAILED_PRE,
            TemporalVerificationStatus.FAILED_POST,
            TemporalVerificationStatus.FAILED_STOP_BEFORE_START,
        ):
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_TEMPORAL,
                retrieved_at=retrieved_at.isoformat(),
                raw_label_sha256=raw_sha256,
                source_record_id=product.source_record_id,
                archive_product_id=product.source_product_id,
                archive_version=product.source_version,
                observation_start_utc=(
                    product.observation_start_utc.isoformat()
                    if product.observation_start_utc else None
                ),
                observation_stop_utc=(
                    product.observation_stop_utc.isoformat()
                    if product.observation_stop_utc else None
                ),
                temporal_verification_status=temporal_status.value,
                error_class="TemporalEligibilityFailed",
                error_detail_code=temporal_status.value,
            )

        # -----------------------------------------------------------------------
        # Write snapshot
        # -----------------------------------------------------------------------
        capture = ArchiveCaptureRecord(
            source_label_ref=label_url,
            product=product,
            provenance=provenance,
            raw_label_bytes=raw_bytes,
        )

        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            ArchiveLabelSnapshotStore.write(capture, snapshot_path, normalizer_id, profile_id)
        except (ArchiveSnapshotValidationError, Exception) as exc:
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                retrieved_at=retrieved_at.isoformat(),
                raw_label_sha256=raw_sha256,
                source_record_id=product.source_record_id,
                error_class="SnapshotWriteError",
                error_detail_code=str(exc)[:120],
            )

        # Reload snapshot to verify integrity
        try:
            reloaded_product, reloaded_prov = ArchiveLabelSnapshotStore.load(snapshot_path)
        except Exception as exc:
            return AcquisitionLedgerRow(
                acquisition_index=idx,
                logical_product_id=entry.logical_product_id,
                instrument=instrument,
                representation_role=rep.representation_role.value,
                source_standard=source_standard,
                label_url=label_url,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
                attempt_count=attempt,
                acquisition_status=AcquisitionStatus.FAILED_SNAPSHOT,
                retrieved_at=retrieved_at.isoformat(),
                raw_label_sha256=raw_sha256,
                source_record_id=product.source_record_id,
                error_class="SnapshotReloadError",
                error_detail_code=str(exc)[:120],
            )

        # Read envelope for snapshot_id
        try:
            import json as _json
            env_data = _json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot_id = env_data.get("snapshot_id")
        except Exception:
            snapshot_id = None

        size_status = _derive_size_verification_status(reloaded_product)

        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=entry.logical_product_id,
            instrument=instrument,
            representation_role=rep.representation_role.value,
            source_standard=source_standard,
            label_url=label_url,
            normalizer_id=normalizer_id,
            profile_id=profile_id,
            attempt_count=attempt,
            acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
            retrieved_at=retrieved_at.isoformat(),
            raw_label_sha256=raw_sha256,
            source_record_id=reloaded_product.source_record_id,
            archive_product_id=reloaded_product.source_product_id,
            archive_version=reloaded_product.source_version,
            snapshot_ref=str(snapshot_path),
            snapshot_id=snapshot_id,
            provenance_id=reloaded_prov.provenance_id,
            observation_start_utc=(
                reloaded_product.observation_start_utc.isoformat()
                if reloaded_product.observation_start_utc else None
            ),
            observation_stop_utc=(
                reloaded_product.observation_stop_utc.isoformat()
                if reloaded_product.observation_stop_utc else None
            ),
            temporal_verification_status=temporal_status.value,
            archive_total_size_bytes=reloaded_product.total_data_size_bytes,
            size_verification_status=size_status.value,
        )

    # Should never be reached
    return AcquisitionLedgerRow(
        acquisition_index=idx,
        logical_product_id=entry.logical_product_id,
        instrument=instrument,
        representation_role=rep.representation_role.value,
        source_standard=source_standard,
        label_url=label_url,
        normalizer_id=normalizer_id,
        profile_id=profile_id,
        attempt_count=attempt,
        acquisition_status=AcquisitionStatus.FAILED_TRANSIENT,
        error_class="ExhaustedRetries",
        error_detail_code=str(last_exc)[:120] if last_exc else None,
    )


# ---------------------------------------------------------------------------
# Main acquisition runner
# ---------------------------------------------------------------------------


class V2InventoryAcquisitionRunner:
    """Orchestrates 535-label acquisition for Juno PJ62 V2 replay.

    Usage::

        runner = V2InventoryAcquisitionRunner(snapshot_root)
        ledger = runner.run()
    """

    def __init__(
        self,
        snapshot_root: Path = _DEFAULT_SNAPSHOT_ROOT,
        inter_request_delay_s: float = 0.15,
        max_attempts: int = 3,
        backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0),
        dry_run: bool = False,
        plan_path: Optional[str] = None,
    ) -> None:
        self.snapshot_root = snapshot_root
        self.inter_request_delay_s = inter_request_delay_s
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.dry_run = dry_run
        self.plan_path = plan_path

    def _load_plan(self) -> HistoricalReplayV2AcquisitionPlan:
        bound = load_bound_v2_acquisition_plan(self.plan_path)
        return bound.plan

    def run(self) -> AcquisitionLedger:
        """Execute full acquisition with canary-first ordering.

        Returns completed AcquisitionLedger.
        """
        plan = self._load_plan()
        sequence = _build_representation_sequence(plan)
        canary_indices = _select_canary_indices(sequence)

        # Canary-first ordering: canaries come first, then remainder
        canary_seq = [item for item in sequence if item[0] in canary_indices]
        rest_seq = [item for item in sequence if item[0] not in canary_indices]
        ordered_seq = canary_seq + rest_seq

        rows: list[AcquisitionLedgerRow] = []

        with httpx.Client(
            follow_redirects=False,
            timeout=30.0,
        ) as client:
            for idx, (acq_idx, entry, rep) in enumerate(ordered_seq):
                if idx > 0 and not self.dry_run:
                    time.sleep(self.inter_request_delay_s)
                row = _acquire_one(
                    idx=acq_idx,
                    entry=entry,
                    rep=rep,
                    snapshot_root=self.snapshot_root,
                    client=client,
                    max_attempts=self.max_attempts,
                    backoff_seconds=self.backoff_seconds,
                    dry_run=self.dry_run,
                )
                rows.append(row)
                logger.info(
                    "[%d/%d] %s — %s",
                    idx + 1, len(ordered_seq),
                    rep.label_url.split("/")[-1],
                    row.acquisition_status.value,
                )

        # Sort rows back into deterministic acquisition_index order
        rows.sort(key=lambda r: r.acquisition_index)

        ledger_id = _compute_ledger_id(rows, plan.replay_id, plan.plan_id)
        return AcquisitionLedger(
            ledger_id=ledger_id,
            replay_id=plan.replay_id,
            plan_id=plan.plan_id,
            rows=tuple(rows),
        )

    def run_canary_only(self) -> dict[str, AcquisitionLedgerRow]:
        """Run only canary acquisitions (one per profile) and return profile→row dict."""
        plan = self._load_plan()
        sequence = _build_representation_sequence(plan)
        canary_indices = _select_canary_indices(sequence)
        canary_seq = [item for item in sequence if item[0] in canary_indices]

        results: dict[str, AcquisitionLedgerRow] = {}
        with httpx.Client(follow_redirects=False, timeout=30.0) as client:
            for acq_idx, entry, rep in canary_seq:
                row = _acquire_one(
                    idx=acq_idx,
                    entry=entry,
                    rep=rep,
                    snapshot_root=self.snapshot_root,
                    client=client,
                    max_attempts=self.max_attempts,
                    backoff_seconds=self.backoff_seconds,
                    dry_run=self.dry_run,
                )
                results[rep.profile_id] = row
        return results


# ---------------------------------------------------------------------------
# Ledger save / load helpers
# ---------------------------------------------------------------------------


def save_ledger(ledger: AcquisitionLedger, path: Path) -> None:
    """Serialize ledger to JSON at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = ledger.model_dump(mode="json")
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_ledger(path: Path) -> AcquisitionLedger:
    """Load and validate ledger from JSON at path."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    return AcquisitionLedger.model_validate(data, strict=False)
