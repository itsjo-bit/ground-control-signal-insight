"""GCSI Phase 6E-C3B — PDS Archive-Label Content-Addressed Snapshot Store.

This module provides:

1. Typed snapshot error hierarchy
   - PdsArchiveSnapshotError              (base)
   - PdsArchiveSnapshotUnavailableError
   - PdsArchiveSnapshotValidationError

2. PdsArchiveSnapshotStore
   - write(capture, path)   — atomically persist a checksum-verified snapshot
   - load(path)             — offline reload with full integrity re-validation

PDS archive snapshot authority model
--------------------------------------
A snapshot is NOT trusted because it says it is valid.  Trust is established
only after the following sequence:

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
        ↓  raw bytes re-validated by SAME shared _validate_pds_archive_label_response()
        ↓  re-derived product == stored product
        ↓  re-derived provenance == stored provenance
        ↓  product.lidvid == request.lidvid
        ↓  recomputed snapshot_id == stored snapshot_id
    VERIFIED SNAPSHOT ACCEPTED

One authoritative parser
------------------------
``_validate_pds_archive_label_response`` (module-level pure function in
pds_archive.py) is the single shared parser for both the live fetch path
and the snapshot reload path.  There is no separate parser_for_snapshot.

Load performs zero network activity
------------------------------------
``PdsArchiveSnapshotStore.load()`` does NOT contact PDS.  It operates entirely
from the local snapshot file, using only the stored raw bytes plus the shared
offline validator.

Note on archive payload integrity
----------------------------------
The archive snapshot authenticates the XML label bytes only.  It does NOT
authenticate CSV science payload bytes.
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
from typing import Optional, Union

from pydantic import ValidationError as PydanticValidationError

from backend.app.mission_sources.adapters.pds_archive import (
    PdsArchiveLabelError,
    PdsArchiveLabelValidationError,
    _validate_pds_archive_label_response,
)
from backend.app.mission_sources.adapters.pds_archive_models import (
    PdsArchiveLabelCapture,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)

from .pds_archive_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    PdsArchiveSnapshotEnvelope,
)


# ---------------------------------------------------------------------------
# Typed error hierarchy
# ---------------------------------------------------------------------------


class PdsArchiveSnapshotError(Exception):
    """Base class for all PDS archive-label snapshot failures.

    Catch this class to handle any archive snapshot-specific error.
    """


class PdsArchiveSnapshotUnavailableError(
    PdsArchiveSnapshotError, MissionSourceUnavailableError
):
    """Snapshot file cannot be accessed.

    Raised for:
    - Missing snapshot file (FileNotFoundError)
    - Permission or other OS-level read/write failure (OSError)

    Public messages do not expose raw file paths or file contents.
    """


class PdsArchiveSnapshotValidationError(
    PdsArchiveSnapshotError, MissionSourceValidationError
):
    """Snapshot exists but fails integrity or re-validation.

    Raised for:
    - Oversized snapshot file
    - Malformed UTF-8
    - Malformed JSON
    - Wrong schema name or unsupported version
    - Invalid Base64
    - Hash mismatch (raw label bytes vs stored raw_label_sha256)
    - Hash mismatch (raw label bytes vs provenance.content_sha256)
    - retrieved_at mismatch (envelope vs provenance)
    - Raw label re-validation failure
    - Stored product mismatch vs re-derived product
    - Stored provenance mismatch vs re-derived provenance
    - product.lidvid inconsistent with request.lidvid
    - Snapshot ID mismatch
    - Capture self-consistency failure (on write)
    - Oversized capture/serialized content (on write)

    Public messages are sanitized and do not expose raw label content.
    """


# ---------------------------------------------------------------------------
# Size limits
# ---------------------------------------------------------------------------

# The archive raw-label limit is 2 MiB.  Base64 adds ~33% overhead;
# product/provenance metadata adds further overhead.
# 4 MiB provides safe serialisation headroom.
_MAX_SNAPSHOT_BYTES: int = 4 * 1024 * 1024  # 4 MiB


# ---------------------------------------------------------------------------
# Deterministic snapshot_id formula
# ---------------------------------------------------------------------------


def _compute_archive_snapshot_id(
    provenance_id: str,
    retrieved_at_utc_iso: str,
) -> str:
    """Compute the deterministic snapshot_id for the archive snapshot.

    Formula::

        SHA-256(
            "gcsi.pds_archive_label_snapshot:v1:"
            + provenance_id
            + ":"
            + retrieved_at_utc_iso
        )

    Returns a 64-character lowercase hex string.
    """
    payload = (
        f"{SNAPSHOT_SCHEMA}:v{SNAPSHOT_VERSION}:"
        f"{provenance_id}:{retrieved_at_utc_iso}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_retrieved_at(dt: datetime) -> str:
    """Return the canonical UTC ISO-8601 string for snapshot_id computation."""
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# PdsArchiveSnapshotStore
# ---------------------------------------------------------------------------


class PdsArchiveSnapshotStore:
    """Write and load checksum-verified reproducible PDS archive-label snapshots.

    This class has no instance state; all methods are static.

    Write
    -----
    :meth:`write` performs full self-consistency verification of the capture
    (independently re-computes SHA-256, re-runs the shared raw-label
    validator, compares re-derived values), then writes atomically via a
    temporary file + ``os.replace()``.

    Load
    ----
    :meth:`load` uses a genuinely bounded file read (reads at most
    ``_MAX_SNAPSHOT_BYTES + 1`` bytes), performs full structural validation,
    strictly decodes and verifies the raw bytes, re-runs the same shared PDS
    archive validator using the stored ``retrieved_at`` timestamp, and
    compares re-derived values against stored values before returning the
    verified result.

    Zero network activity during load
    ----------------------------------
    :meth:`load` does NOT contact PDS.  It works entirely from the local
    snapshot file and the shared offline validator.

    Note on archive payload integrity
    ----------------------------------
    The archive snapshot authenticates XML label bytes ONLY.  CSV science
    payload bytes are NOT authenticated.
    """

    @staticmethod
    def write(
        capture: PdsArchiveLabelCapture,
        path: Union[str, Path],
    ) -> None:
        """Atomically write a self-consistent, checksum-verified archive snapshot.

        The capture is fully re-validated before any file is written.

        Parameters
        ----------
        capture:
            A :class:`PdsArchiveLabelCapture` holding fully validated product,
            provenance, and the exact raw XML label bytes.

        path:
            Destination file path.  Parent directory must already exist.

        Raises
        ------
        PdsArchiveSnapshotValidationError
            If the capture fails self-consistency verification, re-validation,
            or the serialised snapshot exceeds the size limit.

        PdsArchiveSnapshotUnavailableError
            If the file cannot be written due to an OS-level error.
        """
        path = Path(path)
        raw_bytes = capture.raw_label

        # 1. Verify provenance.retrieved_at is present and timezone-aware.
        retrieved_at = capture.provenance.retrieved_at
        if retrieved_at is None:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is missing."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: provenance.retrieved_at is not timezone-aware."
            )

        # 2. Independently verify SHA-256(raw_bytes) == provenance.content_sha256.
        computed_hash = hashlib.sha256(raw_bytes).hexdigest()
        stored_hash = capture.provenance.content_sha256
        if stored_hash is None or computed_hash != stored_hash:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: raw label SHA-256 does not match "
                "provenance.content_sha256."
            )

        # 3. Re-run the SAME shared raw-label validator.
        try:
            rederived_product, rederived_provenance = _validate_pds_archive_label_response(
                request=capture.request,
                raw_bytes=raw_bytes,
                retrieved_at=retrieved_at,
            )
        except PdsArchiveLabelError as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: capture failed raw-label re-validation."
            ) from exc

        if rederived_product != capture.product:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: stored product is not consistent "
                "with the raw label."
            )
        if rederived_provenance != capture.provenance:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: stored provenance is not consistent "
                "with the raw label."
            )

        # 4. Encode raw bytes as standard Base64.
        raw_b64 = base64.b64encode(raw_bytes).decode("ascii")

        # 5. Compute deterministic snapshot_id.
        retrieved_at_iso = _canonical_retrieved_at(retrieved_at)
        snapshot_id = _compute_archive_snapshot_id(
            capture.provenance.provenance_id, retrieved_at_iso
        )

        # 6. Assemble the envelope dict for stable serialisation.
        envelope_dict: dict = {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": capture.request.model_dump(mode="json"),
            "retrieved_at": retrieved_at_iso,
            "raw_label_base64": raw_b64,
            "raw_label_sha256": computed_hash,
            "product": capture.product.model_dump(mode="json"),
            "provenance": capture.provenance.model_dump(mode="json"),
        }

        # 7. Deterministic JSON serialisation: sorted keys, indent=2, UTF-8, newline.
        serialized = json.dumps(envelope_dict, sort_keys=True, indent=2)
        content_bytes = (serialized + "\n").encode("utf-8")

        # 8. Enforce serialised snapshot size limit.
        if len(content_bytes) > _MAX_SNAPSHOT_BYTES:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot write rejected: serialised snapshot exceeds maximum "
                f"allowed size ({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 9. Atomic write: temp file in same directory, then os.replace().
        dir_path = path.parent
        tmp_path_str: Optional[str] = None
        try:
            fd, tmp_path_str = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
            with os.fdopen(fd, "wb") as f:
                f.write(content_bytes)
            os.replace(tmp_path_str, path)
        except OSError as exc:
            if tmp_path_str is not None:
                try:
                    os.unlink(tmp_path_str)
                except OSError:
                    pass
            raise PdsArchiveSnapshotUnavailableError(
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
    ) -> tuple:
        """Load and fully re-validate a checksum-verified PDS archive snapshot.

        This method performs ZERO network activity.

        Parameters
        ----------
        path:
            Path to the snapshot file to load.

        Returns
        -------
        tuple[PdsScienceProduct, ProvenanceRecord]
            Fully re-validated product and provenance.

        Raises
        ------
        PdsArchiveSnapshotUnavailableError
            If the file is missing or cannot be read.

        PdsArchiveSnapshotValidationError
            If any integrity or re-validation check fails.
        """
        from backend.app.mission_sources.adapters.pds_models import PdsScienceProduct
        from backend.app.provenance.models import ProvenanceRecord

        path = Path(path)

        # 1. Genuinely bounded file read.
        try:
            with open(path, "rb") as fh:
                raw_file_bytes = fh.read(_MAX_SNAPSHOT_BYTES + 1)
        except FileNotFoundError as exc:
            raise PdsArchiveSnapshotUnavailableError(
                "PDS archive snapshot is not available."
            ) from exc
        except OSError as exc:
            raise PdsArchiveSnapshotUnavailableError(
                "PDS archive snapshot could not be read."
            ) from exc

        # 2. Size limit.
        if len(raw_file_bytes) > _MAX_SNAPSHOT_BYTES:
            raise PdsArchiveSnapshotValidationError(
                f"Snapshot file exceeds maximum allowed size "
                f"({_MAX_SNAPSHOT_BYTES} bytes)."
            )

        # 3. Decode UTF-8.
        try:
            text = raw_file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot file is not valid UTF-8."
            ) from exc

        # 4. Parse JSON.
        try:
            raw_envelope = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot file contains malformed JSON."
            ) from exc

        if not isinstance(raw_envelope, dict):
            raise PdsArchiveSnapshotValidationError(
                "Snapshot JSON top level is not an object."
            )

        # 5. Pre-check schema name and version.
        schema_val = raw_envelope.get("snapshot_schema")
        if schema_val != SNAPSHOT_SCHEMA:
            raise PdsArchiveSnapshotValidationError(
                f"Snapshot has wrong schema name; expected {SNAPSHOT_SCHEMA!r}."
            )
        version_val = raw_envelope.get("snapshot_version")
        if version_val != SNAPSHOT_VERSION:
            raise PdsArchiveSnapshotValidationError(
                f"Snapshot has unsupported version; expected {SNAPSHOT_VERSION}, "
                f"got {version_val!r}."
            )

        # 6. Validate full Pydantic envelope.
        try:
            envelope = PdsArchiveSnapshotEnvelope.model_validate_json(text)
        except PydanticValidationError as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot envelope failed structural validation."
            ) from exc

        # 7. Strict Base64 decode — validate=True rejects whitespace/garbage.
        try:
            decoded_raw = base64.b64decode(envelope.raw_label_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot raw_label_base64 is invalid Base64."
            ) from exc

        # 8. Compute SHA-256 of decoded bytes.
        computed_hash = hashlib.sha256(decoded_raw).hexdigest()

        # 9. Verify hash == raw_label_sha256.
        if computed_hash != envelope.raw_label_sha256:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot raw label bytes do not match stored raw_label_sha256."
            )

        # 10. Verify hash == provenance.content_sha256.
        if computed_hash != envelope.provenance.content_sha256:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot raw label hash does not match stored "
                "provenance.content_sha256."
            )

        # 11. Verify retrieved_at == provenance.retrieved_at (UTC-normalised).
        env_ret_utc = envelope.retrieved_at.astimezone(timezone.utc)
        prov_ret = envelope.provenance.retrieved_at
        if prov_ret is None:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot provenance.retrieved_at is missing."
            )
        prov_ret_utc = prov_ret.astimezone(timezone.utc)
        if env_ret_utc != prov_ret_utc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot envelope retrieved_at does not match "
                "provenance.retrieved_at."
            )

        # 12. Re-run the SAME shared raw-label validator.
        try:
            rederived_product, rederived_provenance = _validate_pds_archive_label_response(
                request=envelope.request,
                raw_bytes=decoded_raw,
                retrieved_at=envelope.retrieved_at,
            )
        except PdsArchiveLabelError as exc:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot raw PDS archive label failed re-validation."
            ) from exc

        # 13. Compare re-derived product == stored product.
        if rederived_product != envelope.product:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot stored product does not match re-derived product."
            )

        # 14. Compare re-derived provenance == stored provenance.
        if rederived_provenance != envelope.provenance:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot stored provenance does not match re-derived provenance."
            )

        # 15. Verify product.lidvid == request.lidvid.
        if rederived_product.lidvid != envelope.request.lidvid:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot product.lidvid does not match request.lidvid."
            )

        # 16. Recompute snapshot_id and require match.
        retrieved_at_iso = _canonical_retrieved_at(envelope.retrieved_at)
        expected_id = _compute_archive_snapshot_id(
            rederived_provenance.provenance_id, retrieved_at_iso
        )
        if expected_id != envelope.snapshot_id:
            raise PdsArchiveSnapshotValidationError(
                "Snapshot ID does not match expected deterministic value."
            )

        # 17. Return verified result.
        return rederived_product, rederived_provenance
