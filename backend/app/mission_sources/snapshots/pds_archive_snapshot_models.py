"""GCSI Phase 6E-C3B — PDS Archive-Label Snapshot Pydantic Models.

Strict envelope model for the on-disk verified PDS archive-label snapshot format.

Schema identity
---------------
snapshot_schema  = "gcsi.pds_archive_label_snapshot"
snapshot_version = 1

The envelope stores:
- snapshot_schema / snapshot_version  — schema identity
- snapshot_id                          — deterministic SHA-256 fingerprint
- request                              — original PdsArchiveLabelRequest
- retrieved_at                         — historical acquisition timestamp
- raw_label_base64                     — exact raw XML label bytes, standard Base64
- raw_label_sha256                     — SHA-256 of raw label bytes (lowercase hex)
- product                              — normalized PdsScienceProduct (for verification)
- provenance                           — ProvenanceRecord (for verification)

All fields are required (extra="forbid", frozen=True).

The envelope model does NOT validate raw-content consistency (e.g. hash
matches, re-derived product equality).  That responsibility belongs to
:class:`~backend.app.mission_sources.snapshots.pds_archive_snapshot.PdsArchiveSnapshotStore`.

Note on archive payload integrity
----------------------------------
The archive snapshot authenticates the XML label bytes only.  It does NOT
authenticate CSV science payload bytes.  This is documented in the provenance
notes of every record produced by this adapter.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.mission_sources.adapters.pds_archive_models import (
    PdsArchiveLabelRequest,
)
from backend.app.mission_sources.adapters.pds_models import PdsScienceProduct
from backend.app.provenance.models import ProvenanceRecord


# ---------------------------------------------------------------------------
# Schema identity constants
# ---------------------------------------------------------------------------

SNAPSHOT_SCHEMA: str = "gcsi.pds_archive_label_snapshot"
SNAPSHOT_VERSION: int = 1

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_field(v: str) -> str:
    """Validate a 64-char lowercase hex SHA-256 string."""
    if not _SHA256_RE.match(v):
        raise ValueError(
            "SHA-256 field must be exactly 64 lowercase hexadecimal characters."
        )
    return v


# ---------------------------------------------------------------------------
# PdsArchiveSnapshotEnvelope
# ---------------------------------------------------------------------------


class PdsArchiveSnapshotEnvelope(BaseModel):
    """Strict on-disk envelope for a verified PDS archive-label snapshot.

    PDS archive snapshot authority model
    ------------------------------------
    A snapshot is NOT trusted because it claims to be valid.  Trust is
    established only by:

    1. Decoding the stored raw bytes from ``raw_label_base64``.
    2. Verifying ``SHA-256(raw bytes) == raw_label_sha256``.
    3. Verifying the same hash equals ``provenance.content_sha256``.
    4. Re-running the same shared ``_validate_pds_archive_label_response()``
       function.
    5. Comparing the re-derived product to the stored ``product``.
    6. Comparing the re-derived provenance to the stored ``provenance``.
    7. Recomputing the ``snapshot_id`` and verifying it matches.

    The raw bytes are the authoritative capture evidence.
    The SHA-256 checksums provide content-integrity and reproducibility
    assurance.  They are NOT a digital signature.  Authority originates
    from the validated live acquisition, not the hash alone.

    Offline load performs zero network activity.  No PDS requests are
    made during :meth:`PdsArchiveSnapshotStore.load`.

    Note on archive payload integrity
    ----------------------------------
    The archive snapshot authenticates XML label bytes ONLY.  CSV science
    payload bytes are NOT authenticated.

    Fields
    ------
    snapshot_schema
        Must equal ``"gcsi.pds_archive_label_snapshot"``.

    snapshot_version
        Must equal ``1``.

    snapshot_id
        Deterministic SHA-256 fingerprint that binds the provenance_id and
        the historical retrieval timestamp.
        Formula::

            SHA-256("gcsi.pds_archive_label_snapshot:v1:"
                    + provenance_id + ":"
                    + retrieved_at_utc_iso)

    request
        Original :class:`PdsArchiveLabelRequest` for this capture.

    retrieved_at
        Timezone-aware UTC datetime when the raw label was acquired.
        This is the HISTORICAL timestamp from the original live capture,
        NOT the current time.

    raw_label_base64
        Standard Base64 encoding of the exact raw XML label bytes.

    raw_label_sha256
        SHA-256 hex digest (64 lowercase chars) of the raw label bytes.

    product
        Normalized :class:`PdsScienceProduct` stored for offline verification.
        MUST match what is re-derived from raw_label during load.

    provenance
        :class:`ProvenanceRecord` stored for offline verification.
        MUST match what is re-derived from raw_label during load.

    Notes
    -----
    - extra="forbid" ensures no unknown fields survive.
    - frozen=True prevents mutation after construction.
    - This model does NOT validate hash consistency or re-derive the product;
      that is the responsibility of :class:`PdsArchiveSnapshotStore`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_schema: str = Field(
        description=(
            "Schema identity string. Must equal "
            f'"{SNAPSHOT_SCHEMA}".'
        )
    )
    snapshot_version: int = Field(
        description=f"Schema version integer. Must equal {SNAPSHOT_VERSION}."
    )
    snapshot_id: str = Field(
        description=(
            "Deterministic SHA-256 snapshot fingerprint. "
            f"Formula: SHA-256('{SNAPSHOT_SCHEMA}:v{SNAPSHOT_VERSION}:' "
            "+ provenance_id + ':' + retrieved_at_utc_iso)"
        )
    )
    request: PdsArchiveLabelRequest = Field(
        description="Original PdsArchiveLabelRequest for this capture."
    )
    retrieved_at: datetime = Field(
        description=(
            "Timezone-aware UTC datetime when the raw label was acquired. "
            "Historical acquisition timestamp — NOT the current time."
        )
    )
    raw_label_base64: str = Field(
        description="Standard Base64 encoding of the exact raw XML label bytes."
    )
    raw_label_sha256: str = Field(
        description="SHA-256 hex digest (64 lowercase chars) of the raw label bytes."
    )
    product: PdsScienceProduct = Field(
        description=(
            "Normalized PdsScienceProduct stored for offline verification. "
            "Must match what is re-derived from raw_label during load."
        )
    )
    provenance: ProvenanceRecord = Field(
        description=(
            "ProvenanceRecord stored for offline verification. "
            "Must match what is re-derived from raw_label during load."
        )
    )

    @field_validator("snapshot_schema", mode="after")
    @classmethod
    def _validate_schema(cls, v: str) -> str:
        if v != SNAPSHOT_SCHEMA:
            raise ValueError(
                f"snapshot_schema must be {SNAPSHOT_SCHEMA!r}; got {v!r}."
            )
        return v

    @field_validator("snapshot_version", mode="after")
    @classmethod
    def _validate_version(cls, v: int) -> int:
        if v != SNAPSHOT_VERSION:
            raise ValueError(
                f"snapshot_version must be {SNAPSHOT_VERSION}; got {v!r}."
            )
        return v

    @field_validator("snapshot_id", mode="after")
    @classmethod
    def _validate_snapshot_id_format(cls, v: str) -> str:
        return _validate_sha256_field(v)

    @field_validator("raw_label_sha256", mode="after")
    @classmethod
    def _validate_raw_sha256_format(cls, v: str) -> str:
        return _validate_sha256_field(v)

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _validate_aware_retrieved_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "retrieved_at must be timezone-aware."
            )
        return v
