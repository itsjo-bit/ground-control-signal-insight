"""GCSI Phase 6D-B1 — Horizons Content-Addressed Snapshot Store.

This module provides:

1. Typed snapshot error hierarchy
   - HorizonsSnapshotError         (base)
   - HorizonsSnapshotUnavailableError
   - HorizonsSnapshotValidationError

2. HorizonsSnapshotStore
   - write(capture, path)   — atomically persist a checksum-verified snapshot
   - load(path)             — offline reload with full integrity re-validation

Trust principle
---------------
A snapshot is NOT trusted because it says it is valid.

Load sequence
-------------
::

    stored snapshot
        ↓  genuinely bounded file read
        ↓  UTF-8 decode
        ↓  JSON parse
        ↓  structural Pydantic envelope validation
        ↓  schema name + version check
        ↓  strict Base64 decode raw response
        ↓  SHA-256(raw bytes) == raw_response_sha256
        ↓  SHA-256(raw bytes) == provenance.content_sha256
        ↓  raw bytes re-validated by SAME shared parser
        ↓  re-derived geometry == stored geometry
        ↓  re-derived provenance == stored provenance
        ↓  recomputed snapshot_id == stored snapshot_id
    VERIFIED SNAPSHOT ACCEPTED

One authoritative parser
------------------------
``_validate_horizons_raw_response`` (module-level pure function in horizons.py)
is the single shared parser for both the live fetch path and the snapshot
reload path.  There is no separate ``parser_for_snapshot``.

Integrity model
---------------
The SHA-256 checksums provide content-integrity verification and
reproducibility.  They are NOT a digital signature.  An agent with write
access to the file system could rewrite the file and recompute consistent
hashes.  NASA/JPL authority is established by the validated live source
acquisition, not by the hash alone.
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
from typing import Union

from pydantic import ValidationError as PydanticValidationError

from backend.app.mission_sources.adapters.horizons import (
    HorizonsValidationError,
    _validate_horizons_raw_response,
)
from backend.app.mission_sources.adapters.horizons_models import (
    HorizonsGeometryCapture,
    HorizonsGeometryResult,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)

from .horizons_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    HorizonsSnapshotEnvelope,
)


# ---------------------------------------------------------------------------
# Typed error hierarchy
# ---------------------------------------------------------------------------


class HorizonsSnapshotError(Exception):
    """Base class for all Horizons snapshot failures.

    Catch this class to handle any snapshot-specific error.
    Catch the subclasses to distinguish availability from validation.
    """


class HorizonsSnapshotUnavailableError(
    HorizonsSnapshotError, MissionSourceUnavailableError
):
    """Snapshot file cannot be accessed.

    Raised for:
    - Missing snapshot file (FileNotFoundError)
    - Permission or other OS-level read failure (OSError)

    Public messages do not expose raw file paths or file contents.
    """


class HorizonsSnapshotValidationError(
    HorizonsSnapshotError, MissionSourceValidationError
):
    """Snapshot exists but fails integrity or re-validation.

    Raised for:
    - Oversized snapshot file
    - Malformed UTF-8
    - Malformed JSON
    - Wrong schema name or unsupported version
    - Invalid Base64
    - Hash mismatch (raw bytes vs stored raw_response_sha256)
    - Hash mismatch (raw bytes vs provenance.content_sha256)
    - Raw Horizons response re-validation failure
    - Stored geometry mismatch vs re-derived geometry
    - Stored provenance mismatch vs re-derived provenance
    - Snapshot ID mismatch
    - Capture self-consistency failure (on write)
    - Oversized capture/serialized content (on write)

    Public messages are sanitized and do not expose raw response content,
    file paths, or arbitrary internal validation text.
    """


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

# Phase 6D-A raw response limit is 1 MiB.
# Base64 adds ~33% overhead; metadata also stored.  2 MiB provides headroom.
_MAX_SNAPSHOT_BYTES: int = 2 * 1024 * 1024  # 2 MiB


# ---------------------------------------------------------------------------
# Deterministic snapshot_id formula
# ---------------------------------------------------------------------------


def _compute_snapshot_id(provenance_id: str, retrieved_at_utc_iso: str) -> str:
    """Compute the deterministic snapshot_id.

    The snapshot_id binds both the content provenance and the historical
    acquisition timestamp, so the same Horizons query/response retrieved at a
    different time produces a different snapshot_id.

    Formula:
        SHA-256(
            "gcsi.horizons_geometry_snapshot:v1:"
            + provenance_id
            + ":"
            + retrieved_at_utc_iso
        )

    ``retrieved_at_utc_iso`` must be the canonical UTC ISO-8601 representation
    of the acquisition datetime (e.g. ``"2026-08-27T20:41:00+00:00"``).

    Returns a 64-character lowercase hex string.
    """
    payload = (
        f"{SNAPSHOT_SCHEMA}:v{SNAPSHOT_VERSION}:"
        f"{provenance_id}:{retrieved_at_utc_iso}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_retrieved_at(dt: datetime) -> str:
    """Return the canonical UTC ISO-8601 string for a snapshot_id input.

    The datetime is normalised to UTC before formatting so that the
    snapshot_id is independent of input timezone representation.
    """
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# HorizonsSnapshotStore
# ---------------------------------------------------------------------------


class HorizonsSnapshotStore:
    """Write and load checksum-verified reproducible Horizons geometry snapshots.

    This class has no instance state; all methods are static.

    Write
    -----
    :meth:`write` performs full self-consistency verification of the capture
    (re-runs the shared raw-response validator, compares results), then writes
    atomically via a temporary file + ``os.replace()``.

    Load
    ----
    :meth:`load` uses a genuinely bounded file read (reads at most
    MAX_SNAPSHOT_BYTES + 1), performs full structural validation, decodes and
    verifies the raw bytes, re-runs the same shared Horizons validator using
    the stored ``retrieved_at`` timestamp, and compares re-derived values
    against stored values before returning the verified result.

    No HTTP requests are made during load.

    Integrity model
    ---------------
    SHA-256 checksums provide content-integrity verification and reproducibility.
    They are NOT a digital signature.  Source authority comes from the validated
    live acquisition provenance, not from the checksum alone.
    """

    @staticmethod
    def write(
        capture: HorizonsGeometryCapture,
        path: Union[str, Path],
    ) -> None:
        """Atomically write a self-consistent, checksum-verified snapshot to *path*.

        The capture is fully re-validated before any file is written.  The
        re-derived result must exactly match the stored result.

        Parameters
        ----------
        capture:
            A :class:`HorizonsGeometryCapture` holding a fully validated result
            and the exact raw HTTP response bytes.

        path:
            Destination file path.  Parent directory must already exist.

        Raises
        ------
        HorizonsSnapshotValidationError
            If the capture fails self-consistency verification (hash mismatch,
            geometry/provenance/request inconsistency, missing retrieved_at,
            oversized raw response, or oversized serialized snapshot).

        HorizonsSnapshotUnavailableError
            If the file cannot be written due to an OS-level error.
        """
        path = Path(path)
        result = capture.result
        raw_bytes = capture.raw_response

        # 1. Verify provenance.retrieved_at is present and timezone-aware.
        retrieved_at = result.provenance.retrieved_at
        if retrieved_at is None:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is missing."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is not timezone-aware."
            )

        # 2. Independently verify SHA-256(raw_bytes) == provenance.content_sha256.
        computed_hash = hashlib.sha256(raw_bytes).hexdigest()
        stored_hash = result.provenance.content_sha256
        if stored_hash is None or computed_hash != stored_hash:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: raw response SHA-256 does not match "
                "provenance.content_sha256."
            )

        # 3. Re-run the SAME shared raw-response validator to confirm the capture
        #    is internally self-consistent.  The re-derived result must equal
        #    the stored result so that load() will accept this snapshot.
        try:
            rederived = _validate_horizons_raw_response(
                request=result.request,
                raw_bytes=raw_bytes,
                retrieved_at=retrieved_at,
            )
        except HorizonsValidationError as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: capture failed raw-response re-validation."
            ) from exc

        if rederived.geometry != result.geometry:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: stored geometry is not consistent "
                "with the raw response."
            )
        if rederived.provenance != result.provenance:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: stored provenance is not consistent "
                "with the raw response."
            )
        if rederived.request != result.request:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: stored request is not consistent "
                "with the raw response."
            )

        # 4. Encode raw bytes as standard Base64.
        raw_b64 = base64.b64encode(raw_bytes).decode("ascii")

        # 5. Compute deterministic snapshot_id (binds provenance_id + retrieved_at).
        retrieved_at_iso = _canonical_retrieved_at(retrieved_at)
        snapshot_id = _compute_snapshot_id(
            result.provenance.provenance_id, retrieved_at_iso
        )

        # 6. Assemble the envelope as a dict for stable serialization.
        #    Use model_dump(mode="json") for nested Pydantic models so that
        #    datetimes and enums serialize as JSON-native values.
        envelope_dict: dict = {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": result.request.model_dump(mode="json"),
            "retrieved_at": retrieved_at_iso,
            "raw_response_base64": raw_b64,
            "raw_response_sha256": computed_hash,
            "geometry": result.geometry.model_dump(mode="json"),
            "provenance": result.provenance.model_dump(mode="json"),
        }

        # 7. Serialize deterministically: sorted keys, indent=2, UTF-8, newline at EOF.
        serialized = json.dumps(envelope_dict, sort_keys=True, indent=2)
        content_bytes = (serialized + "\n").encode("utf-8")

        # 8. Enforce serialized snapshot size limit.
        if len(content_bytes) > _MAX_SNAPSHOT_BYTES:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: serialized snapshot exceeds maximum "
                f"allowed size ({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 9. Atomic write: temp file in same directory, then os.replace().
        dir_path = path.parent
        fd, tmp_path_str = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content_bytes)
            os.replace(tmp_path_str, path)
        except OSError as exc:
            # Clean up temp file; raise sanitized typed error.
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise HorizonsSnapshotUnavailableError(
                "Snapshot could not be written due to a filesystem error."
            ) from exc
        except BaseException:
            # Any other unexpected failure: clean up temp file and re-raise.
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise

    @staticmethod
    def load(
        path: Union[str, Path],
    ) -> HorizonsGeometryResult:
        """Load and fully re-validate a checksum-verified snapshot from *path*.

        Re-validation sequence
        ----------------------
        1.  Genuinely bounded file read (reads at most MAX_SNAPSHOT_BYTES + 1).
        2.  Enforce size limit.
        3.  Decode UTF-8.
        4.  Parse JSON.
        5.  Pre-check schema name and version.
        6.  Validate strict Pydantic envelope.
        7.  Strict Base64 decode raw response.
        8.  Compute SHA-256 of decoded bytes.
        9.  Require hash == raw_response_sha256.
        10. Require hash == provenance.content_sha256.
        11. Run shared raw-response validator with stored retrieved_at.
        12. Compare re-derived geometry == stored geometry.
        13. Compare re-derived provenance == stored provenance.
        14. Recompute snapshot_id; require match.
        15. Return verified result.

        Parameters
        ----------
        path:
            Path to the snapshot file to load.

        Returns
        -------
        HorizonsGeometryResult
            Fully re-validated geometry result.

        Raises
        ------
        HorizonsSnapshotUnavailableError
            If the file is missing or cannot be read.

        HorizonsSnapshotValidationError
            If any integrity or re-validation check fails.
        """
        path = Path(path)

        # 1. Genuinely bounded file read — never request more than MAX + 1 bytes.
        try:
            with open(path, "rb") as fh:
                raw_file_bytes = fh.read(_MAX_SNAPSHOT_BYTES + 1)
        except FileNotFoundError as exc:
            raise HorizonsSnapshotUnavailableError(
                "Horizons snapshot is not available."
            ) from exc
        except OSError as exc:
            raise HorizonsSnapshotUnavailableError(
                "Horizons snapshot could not be read."
            ) from exc

        # 2. Size limit (exact check after bounded read).
        if len(raw_file_bytes) > _MAX_SNAPSHOT_BYTES:
            raise HorizonsSnapshotValidationError(
                f"Snapshot file exceeds maximum allowed size "
                f"({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 3. Decode UTF-8.
        try:
            text = raw_file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot file is not valid UTF-8."
            ) from exc

        # 4. Parse JSON.
        try:
            raw_envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot file contains malformed JSON."
            ) from exc

        if not isinstance(raw_envelope, dict):
            raise HorizonsSnapshotValidationError(
                "Snapshot JSON top level is not an object."
            )

        # 5. Pre-check schema name and version for clean error messages.
        schema_val = raw_envelope.get("snapshot_schema")
        if schema_val != SNAPSHOT_SCHEMA:
            raise HorizonsSnapshotValidationError(
                f"Snapshot has wrong schema name; expected {SNAPSHOT_SCHEMA!r}."
            )
        version_val = raw_envelope.get("snapshot_version")
        if version_val != SNAPSHOT_VERSION:
            raise HorizonsSnapshotValidationError(
                f"Snapshot has unsupported version; expected {SNAPSHOT_VERSION}, "
                f"got {version_val!r}."
            )

        # 6. Validate full Pydantic envelope (catches type/constraint violations).
        try:
            envelope = HorizonsSnapshotEnvelope.model_validate(raw_envelope)
        except PydanticValidationError as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot envelope failed structural validation."
            ) from exc

        # 7. Strict Base64 decode — validate=True rejects whitespace/garbage.
        try:
            decoded_raw = base64.b64decode(envelope.raw_response_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot raw_response_base64 is invalid Base64."
            ) from exc

        # 8. Compute SHA-256 of decoded bytes.
        computed_hash = hashlib.sha256(decoded_raw).hexdigest()

        # 9. Require hash == raw_response_sha256.
        if computed_hash != envelope.raw_response_sha256:
            raise HorizonsSnapshotValidationError(
                "Snapshot raw response bytes do not match stored raw_response_sha256."
            )

        # 10. Require hash == stored provenance.content_sha256.
        if computed_hash != envelope.provenance.content_sha256:
            raise HorizonsSnapshotValidationError(
                "Snapshot raw response hash does not match stored "
                "provenance.content_sha256."
            )

        # 11. Re-run the SAME shared raw-response validator.
        #     Uses the stored retrieved_at (historical timestamp) — NOT current time.
        try:
            rederived = _validate_horizons_raw_response(
                request=envelope.request,
                raw_bytes=decoded_raw,
                retrieved_at=envelope.retrieved_at,
            )
        except HorizonsValidationError as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot raw Horizons response failed re-validation."
            ) from exc

        # 12. Compare re-derived geometry == stored geometry.
        if rederived.geometry != envelope.geometry:
            raise HorizonsSnapshotValidationError(
                "Snapshot stored geometry does not match re-derived geometry."
            )

        # 13. Compare re-derived provenance == stored provenance.
        if rederived.provenance != envelope.provenance:
            raise HorizonsSnapshotValidationError(
                "Snapshot stored provenance does not match re-derived provenance."
            )

        # 14. Recompute snapshot_id and require match.
        retrieved_at_iso = _canonical_retrieved_at(envelope.retrieved_at)
        expected_id = _compute_snapshot_id(
            rederived.provenance.provenance_id, retrieved_at_iso
        )
        if expected_id != envelope.snapshot_id:
            raise HorizonsSnapshotValidationError(
                "Snapshot ID does not match expected deterministic value."
            )

        # 15. Return verified result.
        return rederived
