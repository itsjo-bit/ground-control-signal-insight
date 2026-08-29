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
        ↓  normalizer_id + profile_id resolved via CONTROLLED registry
        ↓  raw bytes re-validated by the resolved registered parser
        ↓  re-derived product == stored product
        ↓  re-derived provenance == stored provenance
        ↓  source_record_id consistency check
        ↓  recomputed snapshot_id == stored snapshot_id
        ↓  snapshot_source_standard cross-check
    VERIFIED SNAPSHOT ACCEPTED

Zero network activity during load
----------------------------------
``ArchiveLabelSnapshotStore.load()`` does NOT contact PDS.  It works entirely
from the local snapshot file plus the same shared offline parsers.

Both PDS3 and PDS4 snapshots share this store.  The snapshot_source_standard
field distinguishes them.

Parser registry (Section N)
-----------------------------
Callers cannot inject an arbitrary reparser at load time.  All parsers are
registered via ``register_parser(normalizer_id, profile_id, reparser)`` before
any snapshot loading.  ``load()`` resolves the reparser from the envelope's
``normalizer_id`` + ``profile_id`` fields using the controlled registry.

The write path retains an explicit ``reparser`` parameter for consistency
checking at write time, but also stores ``normalizer_id`` and ``profile_id``
in the envelope for future registry-based reload.

Backward compatibility
----------------------
``load_from_explicit_reparser()`` is provided for callers that pass a reparser
directly (e.g. existing tests).  It bypasses the registry but is intended only
for testing/migration purposes.
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
    snapshot_id mismatch, capture write failure, unknown normalizer/profile,
    source_standard cross-check failure.
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
# Section N — Controlled parser registry
# ---------------------------------------------------------------------------

# The registry maps (normalizer_id, profile_id) → ReparserFn.
# Callers register parsers before loading snapshots; load() resolves via
# this registry rather than accepting arbitrary callable injection.
#
# Item 7: duplicate registration (overwrite) is forbidden.  Once a parser
# is registered for a (normalizer_id, profile_id) pair it cannot be changed.
# Production code must register at startup; tests that need a different parser
# must use load_from_explicit_reparser() which bypasses the registry entirely.
_PARSER_REGISTRY: dict[tuple[str, str], ReparserFn] = {}


def register_parser(
    normalizer_id: str, profile_id: str, reparser: ReparserFn
) -> None:
    """Register a reparser for a specific normalizer_id + profile_id pair.

    Must be called BEFORE loading any snapshot that uses this combination.

    Raises
    ------
    ValueError
        If normalizer_id or profile_id is empty.

    ArchiveSnapshotValidationError
        If the (normalizer_id, profile_id) pair is already registered.
        Duplicate registration / overwrite is forbidden.

    Parameters
    ----------
    normalizer_id:
        Normalizer identifier, e.g. ``"gcsi.generic_pds3_label.v1"``.
    profile_id:
        Profile identifier, e.g. ``"waves_burst_pds3"``.
    reparser:
        Callable accepting (raw_bytes, source_ref, retrieved_at) and
        returning (ArchiveScienceProduct, ProvenanceRecord).
    """
    if not normalizer_id.strip():
        raise ValueError("normalizer_id must not be empty.")
    if not profile_id.strip():
        raise ValueError("profile_id must not be empty.")
    key = (normalizer_id, profile_id)
    if key in _PARSER_REGISTRY:
        raise ArchiveSnapshotValidationError(
            f"Parser for normalizer_id={normalizer_id!r} / profile_id={profile_id!r} "
            "is already registered. Duplicate registration / overwrite is forbidden. "
            "Use load_from_explicit_reparser() for test/migration paths."
        )
    _PARSER_REGISTRY[key] = reparser


def _register_parser_force(
    normalizer_id: str, profile_id: str, reparser: ReparserFn
) -> None:
    """Internal helper: register or overwrite a parser in the registry.

    FOR TEST USE ONLY.  Production code must use register_parser() which
    forbids overwrite.  This function bypasses the overwrite guard to allow
    test isolation (re-registering between tests).

    Not exported from the public module interface.
    """
    if not normalizer_id.strip():
        raise ValueError("normalizer_id must not be empty.")
    if not profile_id.strip():
        raise ValueError("profile_id must not be empty.")
    _PARSER_REGISTRY[(normalizer_id, profile_id)] = reparser


def _resolve_reparser(normalizer_id: str, profile_id: str) -> ReparserFn:
    """Resolve a reparser from the controlled registry.

    Raises
    ------
    ArchiveSnapshotValidationError
        If the (normalizer_id, profile_id) pair is not registered.
    """
    key = (normalizer_id, profile_id)
    if key not in _PARSER_REGISTRY:
        raise ArchiveSnapshotValidationError(
            f"Unknown normalizer_id {normalizer_id!r} / profile_id {profile_id!r}. "
            "Register the parser via register_parser() before loading snapshots."
        )
    return _PARSER_REGISTRY[key]


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

    normalizer_id
        Normalizer identifier used to produce this snapshot,
        e.g. ``"gcsi.generic_pds3_label.v1"``.

    profile_id
        Profile identifier used to produce this snapshot,
        e.g. ``"waves_burst_pds3"``.
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
    normalizer_id: str = Field(
        description=(
            "Normalizer identifier used to produce this snapshot, "
            "e.g. 'gcsi.generic_pds3_label.v1'."
        )
    )
    profile_id: str = Field(
        description=(
            "Profile identifier used to produce this snapshot, "
            "e.g. 'waves_burst_pds3'."
        )
    )

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

    @field_validator("normalizer_id", "profile_id", mode="after")
    @classmethod
    def _check_non_empty_ids(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("normalizer_id and profile_id must not be empty.")
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
    shared parser, stores ``normalizer_id`` and ``profile_id`` in the envelope,
    and writes atomically via temp file + ``os.replace()``.

    Load (registry-based — recommended)
    ------------------------------------
    :meth:`load` performs a genuinely bounded file read, full structural
    validation, strict Base64 decode, hash verification, resolves the
    reparser from the CONTROLLED registry using the envelope's
    ``normalizer_id`` + ``profile_id``, re-runs the parser, compares
    re-derived values, verifies snapshot_id, and checks source_standard.

    Load (explicit reparser — backward compatible)
    -----------------------------------------------
    :meth:`load_from_explicit_reparser` accepts a direct reparser callable.
    Intended for test fixtures and migration; does NOT use the registry.

    Zero network activity during load
    ----------------------------------
    Both load methods do NOT contact PDS.
    """

    @staticmethod
    def write(
        raw_label_bytes: bytes,
        source_ref: Optional[str],
        product: ArchiveScienceProduct,
        provenance: ProvenanceRecord,
        reparser: ReparserFn,
        path: Union[str, Path],
        normalizer_id: str = "",
        profile_id: str = "",
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

        normalizer_id:
            Normalizer identifier to store in the envelope,
            e.g. ``"gcsi.generic_pds3_label.v1"``.
            Required for registry-based reload.

        profile_id:
            Profile identifier to store in the envelope,
            e.g. ``"waves_burst_pds3"``.
            Required for registry-based reload.

        Raises
        ------
        ArchiveSnapshotValidationError
            On any self-consistency, re-validation, or size-limit failure.

        ArchiveSnapshotUnavailableError
            On OS-level write failure.
        """
        path = Path(path)

        # 1. Reject unknown/empty normalizer_id or profile_id BEFORE any I/O (Item 8).
        if not normalizer_id.strip():
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: normalizer_id must not be empty."
            )
        if not profile_id.strip():
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: profile_id must not be empty."
            )

        # 2. retrieved_at.
        retrieved_at = provenance.retrieved_at
        if retrieved_at is None:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is missing."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is not timezone-aware."
            )

        # 3. SHA-256 consistency.
        computed_hash = hashlib.sha256(raw_label_bytes).hexdigest()
        if provenance.content_sha256 is None or computed_hash != provenance.content_sha256:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: raw label SHA-256 does not match "
                "provenance.content_sha256."
            )

        # 4. source_record_id consistency.
        if provenance.source_record_id != product.source_record_id:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.source_record_id does not "
                "match product.source_record_id."
            )

        # 5. Re-run the SAME shared parser.
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

        # 6. Base64 encode.
        raw_b64 = base64.b64encode(raw_label_bytes).decode("ascii")

        # 7. Snapshot ID.
        retrieved_at_iso = _canonical_retrieved_at(retrieved_at)
        standard_val = product.source_standard.value
        snapshot_id = _compute_snapshot_id(
            standard_val, provenance.provenance_id, retrieved_at_iso
        )

        # 8. Construct and validate the envelope with Pydantic BEFORE serializing (Item 8).
        # This rejects empty/mismatched normalizer_id or profile_id that would only
        # fail later on load.
        try:
            envelope = ArchiveLabelSnapshotEnvelope(
                snapshot_schema=SNAPSHOT_SCHEMA,
                snapshot_version=SNAPSHOT_VERSION,
                snapshot_id=snapshot_id,
                snapshot_source_standard=standard_val,
                source_ref=source_ref,
                retrieved_at=retrieved_at,
                raw_label_base64=raw_b64,
                raw_label_sha256=computed_hash,
                product=product,
                provenance=provenance,
                normalizer_id=normalizer_id,
                profile_id=profile_id,
            )
        except PydanticValidationError as exc:
            raise ArchiveSnapshotValidationError(
                "Snapshot write rejected: envelope validation failed."
            ) from exc

        # 9. Serialize from the validated envelope.
        envelope_dict: dict = {
            "snapshot_schema": envelope.snapshot_schema,
            "snapshot_version": envelope.snapshot_version,
            "snapshot_id": envelope.snapshot_id,
            "snapshot_source_standard": envelope.snapshot_source_standard,
            "source_ref": envelope.source_ref,
            "retrieved_at": retrieved_at_iso,
            "raw_label_base64": envelope.raw_label_base64,
            "raw_label_sha256": envelope.raw_label_sha256,
            "product": product.model_dump(mode="json"),
            "provenance": provenance.model_dump(mode="json"),
            "normalizer_id": envelope.normalizer_id,
            "profile_id": envelope.profile_id,
        }
        serialized = json.dumps(envelope_dict, sort_keys=True, indent=2)
        content_bytes = (serialized + "\n").encode("utf-8")

        # 10. Size check.
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
    ) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
        """Load and fully re-validate a snapshot using the controlled parser registry.

        ZERO network activity.

        The reparser is resolved from the envelope's ``normalizer_id`` and
        ``profile_id`` fields via the controlled registry.  Call
        ``register_parser()`` for all relevant (normalizer_id, profile_id)
        pairs before loading.

        Parameters
        ----------
        path:
            Path to the snapshot file.

        Returns
        -------
        tuple[ArchiveScienceProduct, ProvenanceRecord]
            Fully re-validated product and provenance.

        Raises
        ------
        ArchiveSnapshotUnavailableError
            If the file is missing or cannot be read.

        ArchiveSnapshotValidationError
            If any integrity or re-validation check fails, including unknown
            normalizer_id/profile_id pair.
        """
        envelope = _load_and_validate_envelope(path)
        reparser = _resolve_reparser(envelope.normalizer_id, envelope.profile_id)
        return _finish_load(envelope, reparser)

    @staticmethod
    def load_from_explicit_reparser(
        path: Union[str, Path],
        reparser: ReparserFn,
    ) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
        """Load and re-validate a snapshot using a directly supplied reparser.

        ZERO network activity.

        This method bypasses the controlled parser registry.  It is intended
        for backward compatibility with existing test fixtures and for
        migration purposes.  New production code should use ``load()``.

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
        envelope = _load_and_validate_envelope(path)
        return _finish_load(envelope, reparser)


# ---------------------------------------------------------------------------
# Internal helpers for load path
# ---------------------------------------------------------------------------


def _load_and_validate_envelope(
    path: Union[str, Path],
) -> ArchiveLabelSnapshotEnvelope:
    """Read, decode, and structurally validate the snapshot envelope.

    Returns the validated ArchiveLabelSnapshotEnvelope.
    Raises ArchiveSnapshotUnavailableError or ArchiveSnapshotValidationError.
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

    return envelope


def _finish_load(
    envelope: ArchiveLabelSnapshotEnvelope,
    reparser: ReparserFn,
) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
    """Complete the load verification pipeline given a validated envelope + reparser.

    Performs steps 7–17 of the trust chain.
    """
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

    # 17. Cross-check snapshot_source_standard vs rederived product.
    if envelope.snapshot_source_standard != rederived_product.source_standard.value:
        raise ArchiveSnapshotValidationError(
            f"Snapshot snapshot_source_standard {envelope.snapshot_source_standard!r} "
            f"does not match re-derived product source_standard "
            f"{rederived_product.source_standard.value!r}."
        )

    # 18. Cross-check normalizer_id vs source_standard (Item 7).
    # A PDS3 normalizer must not be used with a PDS4 snapshot and vice versa.
    # Convention: PDS3 normalizer IDs contain "pds3"; PDS4 normalizer IDs contain "pds4".
    norm_id_lower = envelope.normalizer_id.lower()
    std_val = rederived_product.source_standard.value  # "pds3" or "pds4"
    if "pds3" in norm_id_lower and std_val != "pds3":
        raise ArchiveSnapshotValidationError(
            f"Snapshot normalizer_id {envelope.normalizer_id!r} is a PDS3 normalizer "
            f"but snapshot_source_standard is {std_val!r}. "
            "Normalizer/source_standard mismatch."
        )
    if "pds4" in norm_id_lower and std_val != "pds4":
        raise ArchiveSnapshotValidationError(
            f"Snapshot normalizer_id {envelope.normalizer_id!r} is a PDS4 normalizer "
            f"but snapshot_source_standard is {std_val!r}. "
            "Normalizer/source_standard mismatch."
        )

    # 19. Return verified result.
    return rederived_product, rederived_provenance
