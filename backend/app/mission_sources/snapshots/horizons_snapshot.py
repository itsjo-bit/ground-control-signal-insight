"""GCSI Phase 6D-B1 — Horizons Verified Snapshot Store.

This module provides:

1. Typed snapshot error hierarchy
   - HorizonsSnapshotError         (base)
   - HorizonsSnapshotUnavailableError
   - HorizonsSnapshotValidationError

2. HorizonsSnapshotStore
   - write(capture, path)   — atomically persist a verified snapshot
   - load(path)             — offline reload with full re-validation

Trust principle
---------------
A snapshot is NOT trusted because it says it is valid.

Load sequence
-------------
::

    stored snapshot
        ↓  structural validation
        ↓  raw-response hash verification
        ↓  raw Horizons response re-validated by SAME adapter parser
        ↓  geometry re-derived
        ↓  provenance re-derived
        ↓  stored normalized values must match
        ↓  snapshot_id recomputed and verified
    VERIFIED SNAPSHOT ACCEPTED

One authoritative parser
------------------------
``HorizonsAdapter._process_response`` is the single shared parser for both
the live fetch path and the snapshot reload path.  There is no separate
``parser_for_snapshot``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Union

from backend.app.mission_sources.adapters.horizons import HorizonsAdapter
from backend.app.mission_sources.adapters.horizons_models import (
    HorizonsGeometryCapture,
    HorizonsGeometryRequest,
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
    - Hash mismatch (raw bytes vs stored sha256)
    - Hash mismatch (raw bytes vs provenance.content_sha256)
    - Request validation failure
    - Raw Horizons response re-validation failure
    - Stored geometry mismatch
    - Stored provenance mismatch
    - Snapshot ID mismatch

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


def _compute_snapshot_id(provenance_id: str) -> str:
    """Compute the deterministic snapshot_id.

    Formula:
        SHA-256("gcsi.horizons_geometry_snapshot:v1:" + provenance_id)

    Returns a 64-character lowercase hex string.
    """
    payload = f"{SNAPSHOT_SCHEMA}:v{SNAPSHOT_VERSION}:{provenance_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# HorizonsSnapshotStore
# ---------------------------------------------------------------------------


class HorizonsSnapshotStore:
    """Write and load verified immutable Horizons geometry snapshots.

    This class has no instance state; all methods are static.

    Write
    -----
    :meth:`write` accepts a :class:`HorizonsGeometryCapture` (validated result
    + raw bytes), independently re-verifies the hash, builds the envelope,
    and writes it atomically via a temporary file + ``os.replace()``.

    Load
    ----
    :meth:`load` reads the snapshot, performs full structural validation,
    decodes and verifies the raw bytes, re-runs the SAME Horizons parser
    (``HorizonsAdapter._process_response``) using the stored ``retrieved_at``
    timestamp, and compares the re-derived geometry and provenance against the
    stored values before returning the verified result.

    No HTTP requests are made during load.
    """

    @staticmethod
    def write(
        capture: HorizonsGeometryCapture,
        path: Union[str, Path],
    ) -> None:
        """Atomically write a verified snapshot to *path*.

        Parameters
        ----------
        capture:
            A :class:`HorizonsGeometryCapture` holding a fully validated result
            and the exact raw HTTP response bytes.

        path:
            Destination file path.  Parent directories must already exist.

        Raises
        ------
        HorizonsSnapshotValidationError
            If the capture's content_sha256 does not match SHA-256(raw_bytes).

        OSError
            Raw OS write failure (not wrapped — callers may handle directly).
        """
        path = Path(path)
        result = capture.result
        raw_bytes = capture.raw_response

        # 1. Independently verify hash matches provenance.
        computed_hash = hashlib.sha256(raw_bytes).hexdigest()
        stored_hash = result.provenance.content_sha256
        if stored_hash is None or computed_hash != stored_hash:
            raise HorizonsSnapshotValidationError(
                "Snapshot write rejected: raw response SHA-256 does not match "
                "provenance.content_sha256."
            )

        # 2. Encode raw bytes as standard Base64.
        raw_b64 = base64.b64encode(raw_bytes).decode("ascii")

        # 3. Compute deterministic snapshot_id.
        snapshot_id = _compute_snapshot_id(result.provenance.provenance_id)

        # 4. Build the retrieved_at value (from stored provenance, not clock).
        retrieved_at: datetime = result.provenance.retrieved_at  # type: ignore[assignment]

        # 5. Assemble the envelope as a dict for stable serialization.
        #    Use model_dump(mode="json") for nested Pydantic models so that
        #    datetimes and enums serialize as JSON-native values.
        envelope_dict: dict = {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": result.request.model_dump(mode="json"),
            "retrieved_at": retrieved_at.isoformat(),
            "raw_response_base64": raw_b64,
            "raw_response_sha256": computed_hash,
            "geometry": result.geometry.model_dump(mode="json"),
            "provenance": result.provenance.model_dump(mode="json"),
        }

        # 6. Serialize deterministically: sorted keys, indent=2, UTF-8, newline at EOF.
        serialized = json.dumps(envelope_dict, sort_keys=True, indent=2)
        content_bytes = (serialized + "\n").encode("utf-8")

        # 7. Atomic write: write to temp file in same directory, then os.replace().
        dir_path = path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content_bytes)
            os.replace(tmp_path, path)
        except BaseException:
            # Clean up temp file on any failure.
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @staticmethod
    def load(
        path: Union[str, Path],
    ) -> HorizonsGeometryResult:
        """Load and fully re-validate a verified snapshot from *path*.

        Re-validation sequence
        ----------------------
        1.  Read bytes with conservative max size check.
        2.  Decode UTF-8.
        3.  Parse JSON.
        4.  Validate strict snapshot envelope.
        5.  Validate schema name and version.
        6.  Strict Base64 decode raw response.
        7.  Compute SHA-256 of decoded bytes.
        8.  Require hash == raw_response_sha256.
        9.  Require hash == provenance.content_sha256.
        10. Reconstruct HorizonsGeometryRequest.
        11. Run SAME adapter parser with stored retrieved_at.
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

        # 1. Read bytes.
        try:
            raw_file_bytes = path.read_bytes()
        except FileNotFoundError as exc:
            raise HorizonsSnapshotUnavailableError(
                "Horizons snapshot is not available."
            ) from exc
        except OSError as exc:
            raise HorizonsSnapshotUnavailableError(
                "Horizons snapshot could not be read."
            ) from exc

        # 2. Conservative size check.
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

        # 5. Validate schema name and version before Pydantic to give clean errors.
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

        # 6. Validate full Pydantic envelope.
        try:
            from pydantic import ValidationError as PydanticValidationError
            envelope = HorizonsSnapshotEnvelope.model_validate(raw_envelope)
        except Exception as exc:
            raise HorizonsSnapshotValidationError(
                "Snapshot envelope failed structural validation."
            ) from exc

        # 7. Strict Base64 decode — validate=True rejects garbage/whitespace.
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

        # 11. Run SAME adapter parser using stored retrieved_at (not current time).
        #     Build a minimal HorizonsAdapter with no clock and no HTTP client.
        #     We reuse _process_response which is the single authoritative parser.
        adapter = HorizonsAdapter.__new__(HorizonsAdapter)
        # _process_response does not use _client; only used when retrieved_at=None.
        # We pass retrieved_at explicitly so the clock is never called.

        try:
            rederived = adapter._process_response(  # noqa: SLF001
                request=envelope.request,
                raw_bytes=decoded_raw,
                retrieved_at=envelope.retrieved_at,
            )
        except Exception as exc:
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
        expected_id = _compute_snapshot_id(rederived.provenance.provenance_id)
        if expected_id != envelope.snapshot_id:
            raise HorizonsSnapshotValidationError(
                "Snapshot ID does not match expected deterministic value."
            )

        # 15. Return verified result.
        return rederived
