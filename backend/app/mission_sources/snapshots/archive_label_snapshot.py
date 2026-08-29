"""GCSI Phase 6F-B1 — Generic Archive Label Content-Addressed Snapshot Store.

This module provides a generic snapshot store suitable for BOTH PDS3 and PDS4
archive label captures.  It does NOT delete or modify the existing
``PdsArchiveSnapshotStore`` (V1 MWR adapter path, preserved unchanged).

This is an additive V2 store for all B1 target instrument families.

Archive snapshot authority model
---------------------------------
A snapshot is NOT trusted because it claims to be valid.  Trust is established
only by:

::

    stored snapshot
        ↓  genuinely bounded file read  (at most MAX_SNAPSHOT_BYTES + 1 bytes)
        ↓  UTF-8 decode
        ↓  JSON parse
        ↓  structural Pydantic envelope validation
        ↓  schema name + version pre-check
        ↓  strict Base64 decode raw label  (validate=True)
        ↓  SHA-256(raw bytes) == raw_label_sha256
        ↓  SHA-256(raw bytes) == provenance.content_sha256
        ↓  retrieved_at == provenance.retrieved_at  (UTC-normalised)
        ↓  raw bytes re-validated by the SAME parser (PDS3 or PDS4)
        ↓  re-derived product == stored product
        ↓  re-derived provenance == stored provenance
        ↓  source_record_id consistency check
        ↓  recomputed snapshot_id == stored snapshot_id
    VERIFIED SNAPSHOT ACCEPTED

Zero network activity during load
----------------------------------
``ArchiveLabelSnapshotStore.load()`` does NOT contact PDS.  It works entirely
from the local snapshot file plus the same shared offline parsers.

Both PDS3 and PDS4 snapshots share this store.  The snapshot_source_standard
field distinguishes them.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError, field_validator

from backend.app.mission_sources.archive_models import (
    ArchiveScienceProduct,
    ArchiveSourceStandard,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from backend.app.provenance.models import ProvenanceRecord


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class ArchiveSnapshotError(Exception):
    """Base class for all archive snapshot failures."""


class ArchiveSnapshotUnavailableError(
    ArchiveSnapshotError, MissionSourceUnavailableError
):
    """Snapshot file cannot be accessed (missing file, OS error)."""


class ArchiveSnapshotValidationError(
    ArchiveSnapshotError, MissionSourceValidationError
):
    """Snapshot exists but fails integrity or re-validation.

    Raised for: oversized file, malformed UTF-8, malformed JSON,
    wrong schema/version, invalid Base64, hash mismatch, retrieved_at
    mismatch, re-validation failure, product/provenance mismatch,
    snapshot_id mismatch, capture write failure.
    """


# ---------------------------------------------------------------------------
# Schema identity
# ---------------------------------------------------------------------------

SNAPSHOT_SCHEMA: str = "gcsi.archive_label_snapshot"
SNAPSHOT_VERSION: int = 1

_MAX_SNAPSHOT_BYTES: int = 4 * 1024 * 1024  # 4 MiB

_SHA256_RE_LOCAL = __import__("re").compile(r"^[0-9a-f]{64}$")


def _validate_sha256_local(v: str) -> str:
    if not _SHA256_RE_LOCAL.match(v):
        raise ValueError("SHA-256 field must be exactly 64 lowercase hex chars.")
    return v


# ---------------------------------------------------------------------------
# Snapshot envelope model
# ---------------------------------------------------------------------------


class ArchiveLabelSnapshotEnvelope(BaseModel):
    """Strict on-disk envelope for a generic archive label snapshot.

    Fields
    ------
    snapshot_schema
        Must equal ``"gcsi.archive_label_snapshot"``.

    snapshot_version
        Must equal ``1``.

    snapshot_id
        Deterministic SHA-256 fingerprint.
        Formula: SHA-256("gcsi.archive_label_snapshot:v1:"
                         + source_standard_value + ":"
                         + provenance_id + ":"
                         + retrieved_at_utc_iso)

    snapshot_source_standard
        ``"pds3"`` or ``"pds4"`` — used to select the correct re-parser.

    source_ref
        Source URL/path from the capture.

    retrieved_at
        Timezone-aware UTC datetime of original acquisition.

    raw_label_base64
        Standard Base64 of exact raw label bytes.

    raw_label_sha256
        SHA-256 of raw label bytes (64 lowercase hex).

    product
        Stored ArchiveScienceProduct for offline verification.

    provenance
        Stored ProvenanceRecord for offline verification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_schema: str = Field(description="Schema identity string.")
    snapshot_version: int = Field(description="Schema version.")
    snapshot_id: str = Field(description="Deterministic SHA-256 snapshot fingerprint.")
    snapshot_source_standard: str = Field(
        description="Archive source standard: 'pds3' or 'pds4'."
    )
    source_ref: Optional[str] = Field(
        default=None, description="Source URL/path for this label."
    )
    retrieved_at: datetime = Field(
        description="Timezone-aware UTC datetime of original acquisition."
    )
    raw_label_base64: str = Field(description="Standard Base64 of raw label bytes.")
    raw_label_sha256: str = Field(description="SHA-256 of raw label bytes.")
    product: ArchiveScienceProduct = Field(description="Stored normalized product.")
    provenance: ProvenanceRecord = Field(description="Stored provenance record.")

    @field_validator("snapshot_schema", mode="after")
    @classmethod
    def _check_schema(cls, v: str) -> str:
        if v != SNAPSHOT_SCHEMA:
            raise ValueError(
                f"snapshot_schema must be {SNAPSHOT_SCHEMA!r}; got {v!r}."
            )
        return v

    @field_validator("snapshot_version", mode="after")
    @classmethod
    def _check_version(cls, v: int) -> int:
        if v != SNAPSHOT_VERSION:
            raise ValueError(
                f"snapshot_version must be {SNAPSHOT_VERSION}; got {v!r}."
            )
        return v

    @field_validator("snapshot_id", "raw_label_sha256", mode="after")
    @classmethod
    def _check_sha256_format(cls, v: str) -> str:
        return _validate_sha256_local(v)

    @field_validator("snapshot_source_standard", mode="after")
    @classmethod
    def _check_standard(cls, v: str) -> str:
        if v not in ("pds3", "pds4"):
            raise ValueError(
                f"snapshot_source_standard must be 'pds3' or 'pds4'; got {v!r}."
            )
        return v

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _check_aware_retrieved_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware.")
        return v


# ---------------------------------------------------------------------------
# Snapshot ID formula
# ---------------------------------------------------------------------------


def _compute_snapshot_id(
    source_standard: str,
    provenance_id: str,
    retrieved_at_utc_iso: str,
) -> str:
    """Compute deterministic snapshot_id.

    Formula::

        SHA-256(
            "gcsi.archive_label_snapshot:v1:"
            + source_standard_value + ":"
            + provenance_id + ":"
            + retrieved_at_utc_iso
        )
    """
    payload = (
        f"{SNAPSHOT_SCHEMA}:v{SNAPSHOT_VERSION}:"
        f"{source_standard}:{provenance_id}:{retrieved_at_utc_iso}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_retrieved_at(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Re-parser type alias
# ---------------------------------------------------------------------------

# A re-parser is a callable that takes (raw_bytes, source_ref, retrieved_at)
# and returns (ArchiveScienceProduct, ProvenanceRecord).
# This is the SAME parser used for both live acquisition and snapshot reload.
ReparserFn = Callable[
    [bytes, str, datetime],
    tuple[ArchiveScienceProduct, ProvenanceRecord],
]


# ---------------------------------------------------------------------------
# ArchiveLabelSnapshotStore
# ---------------------------------------------------------------------------


class ArchiveLabelSnapshotStore:
    """Write and load checksum-verified reproducible archive label snapshots.

    Supports both PDS3 and PDS4 label captures.
    Preserves V1 PdsArchiveSnapshotStore unchanged.

    This class has no instance state; all methods are static.

    Write
    -----
    :meth:`write` performs full self-consistency verification, re-runs the
    shared parser, and writes atomically via temp file + ``os.replace()``.

    Load
    ----
    :meth:`load` performs a genuinely bounded file read (at most
    ``_MAX_SNAPSHOT_BYTES + 1``), full structural validation, strict
    Base64 decode, hash verification, re-runs the same shared parser,
    compares re-derived values, and verifies snapshot_id.

    Zero network activity during load
    ----------------------------------
    :meth:`load` does NOT contact PDS.
    """

    @staticmethod
    def write(
        raw_label_bytes: bytes,
        source_ref: Optional[str],
        product: ArchiveScienceProduct,
        provenance: ProvenanceRecord,
        reparser: ReparserFn,
        path: Union[str, Path],
    ) -> None:
        """Atomically write a self-consistent, checksum-verified snapshot.

        Parameters
        ----------
        raw_label_bytes:
            Exact raw bytes of the source label.

        source_ref:
            Source URL/path for this label (for provenance).

        product:
            Fully validated ArchiveScienceProduct.

        provenance:
            EXTERNAL_AUTHORITATIVE ProvenanceRecord with content_sha256 set.

        reparser:
            The SAME pure-function parser used during acquisition.
            Must accept (raw_bytes: bytes, source_ref: str, retrieved_at: datetime)
            and return (ArchiveScienceProduct, ProvenanceRecord).

        path:
            Destination file path.  Parent directory must exist.

        Raises
        ------
        ArchiveSnapshotValidationError
            On any self-consistency, re-validation, or size-limit failure.

        ArchiveSnapshotUnavailableError
            On OS-level write failure.
        """
        path = Path(path)

        # 1. retrieved_at.
        retrieved_at = provenance.retrieved_at
        if retrieved_at is None:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is missing."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is not timezone-aware."
            )

        # 2. SHA-256 consistency.
        computed_hash = hashlib.sha256(raw_label_bytes).hexdigest()
        if provenance.content_sha256 is None or computed_hash != provenance.content_sha256:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: raw label SHA-256 does not match "
                "provenance.content_sha256."
            )

        # 3. source_record_id consistency.
        if provenance.source_record_id != product.source_record_id:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.source_record_id does not "
                "match product.source_record_id."
            )

        # 4. Re-run the SAME shared parser.
        effective_ref = source_ref or product.source_label_ref or ""
        try:
            rederived_product, rederived_provenance = reparser(
                raw_label_bytes, effective_ref, retrieved_at
            )
        except Exception as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: capture failed raw-label re-validation."
            ) from exc

        if rederived_product != product:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: stored product is not consistent "
                "with the raw label."
            )
        if rederived_provenance != provenance:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: stored provenance is not consistent "
                "with the raw label."
            )

        # 5. Base64 encode.
        raw_b64 = base64.b64encode(raw_label_bytes).decode("ascii")

        # 6. Snapshot ID.
        retrieved_at_iso = _canonical_retrieved_at(retrieved_at)
        standard_val = product.source_standard.value
        snapshot_id = _compute_snapshot_id(
            standard_val, provenance.provenance_id, retrieved_at_iso
        )

        # 7. Envelope dict.
        envelope_dict: dict = {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "snapshot_source_standard": standard_val,
            "source_ref": source_ref,
            "retrieved_at": retrieved_at_iso,
            "raw_label_base64": raw_b64,
            "raw_label_sha256": computed_hash,
            "product": product.model_dump(mode="json"),
            "provenance": provenance.model_dump(mode="json"),
        }

        # 8. Serialize.
        serialized = json.dumps(envelope_dict, sort_keys=True, indent=2)
        content_bytes = (serialized + "\n").encode("utf-8")

        # 9. Size check.
        if len(content_bytes) > _MAX_SNAPSHOT_BYTES:
            raise ArchiveSnapshotValidationError(
                f"Snapshot write rejected: serialised snapshot exceeds maximum "
                f"allowed size ({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 10. Atomic write.
        tmp_path_str: Optional[str] = None
        try:
            fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(content_bytes)
            os.replace(tmp_path_str, path)
        except OSError as exc:
            if tmp_path_str is not None:
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise ArchiveSnapshotUnavailableError(
                "Snapshot could not be written due to a filesystem error."
            ) from exc
        except BaseException:
            if tmp_path_str is not None:
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise

    @staticmethod
    def load(
        path: Union[str, Path],
        reparser: ReparserFn,
    ) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
        """Load and fully re-validate a checksum-verified archive snapshot.

        ZERO network activity.

        Parameters
        ----------
        path:
            Path to the snapshot file.

        reparser:
            The SAME pure-function parser used during acquisition.

        Returns
        -------
        tuple[ArchiveScienceProduct, ProvenanceRecord]
            Fully re-validated product and provenance.

        Raises
        ------
        ArchiveSnapshotUnavailableError
            If the file is missing or cannot be read.

        ArchiveSnapshotValidationError
            If any integrity or re-validation check fails.
        """
        path = Path(path)

        # 1. Bounded file read.
        try:
            with open(path, "rb") as fh:
                raw_file_bytes = fh.read(_MAX_SNAPSHOT_BYTES + 1)
        except FileNotFoundError as exc:
            raise ArchiveSnapshotUnavailableError(
                "Archive label snapshot is not available."
            ) from exc
        except OSError as exc:
            raise ArchiveSnapshotUnavailableError(
                "Archive label snapshot could not be read."
            ) from exc

        # 2. Size check.
        if len(raw_file_bytes) > _MAX_SNAPSHOT_BYTES:
            raise ArchiveSnapshotValidationError(
                f"Snapshot file exceeds maximum allowed size "
                f"({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 3. UTF-8 decode.
        try:
            text = raw_file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot file is not valid UTF-8."
            ) from exc

        # 4. JSON parse.
        try:
            raw_envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot file contains malformed JSON."
            ) from exc

        if not isinstance(raw_envelope, dict):
            raise ArchiveSnapshotValidationError(
                "Snapshot JSON top level is not an object."
            )

        # 5. Schema pre-check.
        if raw_envelope.get("snapshot_schema") != SNAPSHOT_SCHEMA:
            raise ArchiveSnapshotValidationError(
                f"Snapshot has wrong schema name; expected {SNAPSHOT_SCHEMA!r}."
            )
        if raw_envelope.get("snapshot_version") != SNAPSHOT_VERSION:
            raise ArchiveSnapshotValidationError(
                f"Snapshot has unsupported version; expected {SNAPSHOT_VERSION}."
            )

        # 6. Full Pydantic envelope validation.
        try:
            envelope = ArchiveLabelSnapshotEnvelope.model_validate_json(text)
        except PydanticValidationError as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot envelope failed structural validation."
            ) from exc

        # 7. Strict Base64 decode.
        try:
            decoded_raw = base64.b64decode(envelope.raw_label_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot raw_label_base64 is invalid Base64."
            ) from exc

        # 8. SHA-256 of decoded bytes.
        computed_hash = hashlib.sha256(decoded_raw).hexdigest()

        # 9. Hash == raw_label_sha256.
        if computed_hash != envelope.raw_label_sha256:
            raise ArchiveSnapshotValidationError(
                "Snapshot raw label bytes do not match stored raw_label_sha256."
            )

        # 10. Hash == provenance.content_sha256.
        if computed_hash != envelope.provenance.content_sha256:
            raise ArchiveSnapshotValidationError(
                "Snapshot raw label hash does not match provenance.content_sha256."
            )

        # 11. retrieved_at consistency.
        env_ret_utc = envelope.retrieved_at.astimezone(timezone.utc)
        prov_ret = envelope.provenance.retrieved_at
        if prov_ret is None:
            raise ArchiveSnapshotValidationError(
                "Snapshot provenance.retrieved_at is missing."
            )
        prov_ret_utc = prov_ret.astimezone(timezone.utc)
        if env_ret_utc != prov_ret_utc:
            raise ArchiveSnapshotValidationError(
                "Snapshot envelope retrieved_at does not match "
                "provenance.retrieved_at."
            )

        # 12. Re-run the SAME shared parser.
        effective_ref = envelope.source_ref or envelope.product.source_label_ref or ""
        try:
            rederived_product, rederived_provenance = reparser(
                decoded_raw, effective_ref, envelope.retrieved_at
            )
        except Exception as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot raw label failed re-validation."
            ) from exc

        # 13. Re-derived product == stored product.
        if rederived_product != envelope.product:
            raise ArchiveSnapshotValidationError(
                "Snapshot stored product does not match re-derived product."
            )

        # 14. Re-derived provenance == stored provenance.
        if rederived_provenance != envelope.provenance:
            raise ArchiveSnapshotValidationError(
                "Snapshot stored provenance does not match re-derived provenance."
            )

        # 15. source_record_id consistency.
        if rederived_product.source_record_id != rederived_provenance.source_record_id:
            raise ArchiveSnapshotValidationError(
                "Snapshot source_record_id mismatch between product and provenance."
            )

        # 16. Recompute snapshot_id.
        retrieved_at_iso = _canonical_retrieved_at(envelope.retrieved_at)
        expected_id = _compute_snapshot_id(
            envelope.snapshot_source_standard,
            rederived_provenance.provenance_id,
            retrieved_at_iso,
        )
        if expected_id != envelope.snapshot_id:
            raise ArchiveSnapshotValidationError(
                "Snapshot ID does not match expected deterministic value."
            )

        # 17. Return verified result.
        return rederived_product, rederived_provenance
