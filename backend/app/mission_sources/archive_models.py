"""GCSI Phase 6F-B1 — Generic Archive Source Domain Models.

This module defines the mission-generic source-layer data model that sits
between NASA/PDS archive sources and the GCSI operational domain.

Layer relationship
------------------
::

    PDS3 / PDS4 archive labels
            ↓
    ArchiveScienceProduct   ← this module
            ↓
    VerifiedInventoryEntry
            +
    ReplayProductPolicy [future B3]
            ↓
    ReplayAssemblerV2 [future B3]
            ↓
    DataProduct  (source-agnostic GCSI operational model — UNCHANGED)

Architectural rule
------------------
NASA/PDS source concerns MUST remain outside the generic GCSI operational
DataProduct domain model.  DataProduct (backend/app/models/data_product.py)
is NOT changed by this module.

Design principles
-----------------
All models are:
- frozen (immutable after construction)
- strict (no type coercion)
- extra="forbid" (no unexpected fields)
- timezone-aware UTC datetime fields
- deterministic source_record_id (stable, not random)

Key distinctions
----------------
- archive existence / facts           = EXTERNAL_AUTHORITATIVE
- replay queue membership             = MODELED
- archive file size ≠ historical downlink bytes
- replay-size proxy policy            = MODELED
- DataProduct                         = source-agnostic
- V1 replay infrastructure            = unchanged and supported

Parts A–H implemented here:

A. ArchiveSourceStandard        — PDS3 / PDS4 enum
B. ArchiveDataFileSizeCertainty — size-certainty taxonomy (Part E)
   ArchiveSnapshotVerificationStatus — snapshot-verification state (Part E)
C. ArchiveDataFile              — one data-file metadata record
D. ArchiveScienceProduct        — normalized external archive fact
E. ArchiveCaptureRecord         — raw bytes + provenance (Part H)
F. ProductRepresentationKind    — relationship taxonomy (Part C)
G. ProductRepresentationRelationship — immutable relationship (Part C)
H. VerifiedInventoryEntry       — one logical replay candidate (Part D)
I. VerifiedInventoryManifest    — full validated manifest (Part D)
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# SHA-256 helper (reused from provenance pattern)
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256(v: str) -> str:
    """Validate exactly 64 lowercase hex characters."""
    if not _SHA256_RE.match(v):
        raise ValueError(
            "SHA-256 value must be exactly 64 lowercase hexadecimal characters."
        )
    return v


def _require_aware_datetime(v: datetime) -> datetime:
    """Reject naive datetimes; normalize to UTC."""
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError(
            "Datetime must be timezone-aware (tzinfo and utcoffset() must not be None). "
            "Use datetime(..., tzinfo=timezone.utc) or an offset-aware ISO string."
        )
    return v.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# A. ArchiveSourceStandard
# ---------------------------------------------------------------------------


class ArchiveSourceStandard(str, Enum):
    """Archive metadata standard for a source product.

    PDS3
        NASA Planetary Data System version 3 — fixed-width ASCII labels,
        PVL keyword/value format.

    PDS4
        NASA Planetary Data System version 4 — XML labels, LID/LIDVID
        identity scheme.
    """

    PDS3 = "pds3"
    PDS4 = "pds4"


# ---------------------------------------------------------------------------
# B (Part E). ArchiveDataFileSizeCertainty + ArchiveSnapshotVerificationStatus
# ---------------------------------------------------------------------------


class ArchiveDataFileSizeCertainty(str, Enum):
    """Precision level of a data-file size value.

    SIZE_UNKNOWN
        No size information is available from the archive metadata.
        The corresponding size field will be None.

    SIZE_DISCOVERED_APPROXIMATE
        Size was inferred from a human-readable archive directory listing
        (e.g. HTML index) and is NOT authoritative.  This certainty level
        is NOT permitted for replay scheduler execution.  It must be
        promoted to SIZE_METADATA_EXACT before any operational use.

    SIZE_METADATA_EXACT
        Exact integer bytes parsed from authoritative archive metadata
        (e.g. PDS4 ``file_size`` element with unit=byte, or PDS3
        ``FILE_SIZE`` keyword, or a deterministic formula proven by the
        label contract such as ``RECORD_BYTES × FILE_RECORDS`` when the
        label explicitly describes the payload).

    NOTE: ``SIZE_DISCOVERED_APPROXIMATE`` must NEVER be silently promoted to
    ``SIZE_METADATA_EXACT`` without explicit re-derivation from authoritative
    label metadata.

    NOTE: ``SIZE_SNAPSHOT_VERIFIED`` has been moved to
    ``ArchiveSnapshotVerificationStatus`` where it belongs semantically.
    """

    SIZE_UNKNOWN = "size_unknown"
    SIZE_DISCOVERED_APPROXIMATE = "size_discovered_approximate"
    SIZE_METADATA_EXACT = "size_metadata_exact"


class ArchiveSnapshotVerificationStatus(str, Enum):
    """Snapshot verification state for an archive source record.

    Tracks whether a source record has been independently verified by
    loading a checksum-addressed snapshot.  This is separate from the
    source metadata size certainty (ArchiveDataFileSizeCertainty).

    UNVERIFIED
        No snapshot verification has been performed yet.

    SNAPSHOT_VERIFIED
        The source record has been verified against a checksum-addressed
        GCSI snapshot (the highest trust level for offline use).
    """

    UNVERIFIED = "unverified"
    SNAPSHOT_VERIFIED = "snapshot_verified"


# ---------------------------------------------------------------------------
# C. ArchiveDataFile
# ---------------------------------------------------------------------------


class ArchiveDataFile(BaseModel):
    """Normalized representation of one archive science data file.

    Fields
    ------
    file_name : str
        Data-file name as reported in the archive label.  Must be non-empty.

    file_size_bytes : int | None
        Data-file size in bytes.  None when size is unknown
        (size_certainty == SIZE_UNKNOWN).  Non-None values must be >= 0.

    size_certainty : ArchiveDataFileSizeCertainty
        Precision level of ``file_size_bytes``.

    checksum_algorithm : str | None
        Checksum algorithm identifier (e.g. ``"MD5"``, ``"SHA-256"``).
        None if not provided by the archive label.

    checksum_value : str | None
        Lowercase hex checksum value matching ``checksum_algorithm``.
        None if not provided.  If present, ``checksum_algorithm`` must
        also be present.  Format is validated: MD5 = 32 hex chars,
        SHA-256 = 64 hex chars.

    mime_type : str | None
        MIME type of the data file, if present.

    file_ref : str | None
        Reference URL/path for this science data file, if derivable.
        This adapter does NOT follow or download file_ref.
        Stored as external metadata only.

    Notes
    -----
    - file_ref is NOT followed or downloaded.
    - Checksum cross-validation (algorithm↔value) is enforced.
    - Checksum format is validated (MD5: 32 hex, SHA-256: 64 hex).
    - Duplicate file names within a product are rejected by
      ArchiveScienceProduct.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    file_name: str = Field(description="Data-file name as reported in the archive label.")
    file_size_bytes: Optional[int] = Field(
        default=None,
        description="Data-file size in bytes. None when unknown. Non-None must be >= 0.",
    )
    size_certainty: ArchiveDataFileSizeCertainty = Field(
        description="Precision level of file_size_bytes."
    )
    checksum_algorithm: Optional[str] = Field(
        default=None,
        description="Checksum algorithm (e.g. 'MD5', 'SHA-256'). None if not provided.",
    )
    checksum_value: Optional[str] = Field(
        default=None,
        description="Lowercase hex checksum value. None if not provided.",
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="MIME type of the data file, if present.",
    )
    file_ref: Optional[str] = Field(
        default=None,
        description=(
            "Reference URL/path for this data file, if derivable. "
            "Not followed or downloaded — stored as external metadata only."
        ),
    )

    @field_validator("file_name", mode="after")
    @classmethod
    def _non_empty_file_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("file_name must not be empty.")
        return v

    @field_validator("file_size_bytes", mode="after")
    @classmethod
    def _non_negative_size(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("file_size_bytes must be >= 0 when not None.")
        return v

    @field_validator("checksum_value", mode="after")
    @classmethod
    def _normalize_checksum_value(cls, v: Optional[str]) -> Optional[str]:
        """Normalize checksum_value to lowercase."""
        if v is not None:
            return v.lower()
        return v

    @model_validator(mode="after")
    def _validate_checksum_consistency(self) -> "ArchiveDataFile":
        """Require algorithm↔value co-presence and validate checksum format."""
        alg = self.checksum_algorithm
        val = self.checksum_value
        if (alg is None) != (val is None):
            raise ValueError(
                "checksum_algorithm and checksum_value must both be present or both None."
            )
        if alg is not None and val is not None:
            alg_upper = alg.upper()
            if alg_upper == "MD5":
                if not re.fullmatch(r"[0-9a-fA-F]{32}", val):
                    raise ValueError(
                        "MD5 checksum must be exactly 32 hexadecimal characters."
                    )
            elif alg_upper in ("SHA-256", "SHA256"):
                if not re.fullmatch(r"[0-9a-fA-F]{64}", val):
                    raise ValueError(
                        "SHA-256 checksum must be exactly 64 hexadecimal characters."
                    )
            else:
                raise ValueError(
                    f"Unsupported checksum algorithm {alg!r}. Supported: MD5, SHA-256."
                )
        return self


# ---------------------------------------------------------------------------
# D. ArchiveScienceProduct
# ---------------------------------------------------------------------------

# source_record_id formula (documented here and in DEVELOPERS.md):
#
# PDS4:
#   "pds4:" + lidvid
#   Example: "pds4:urn:nasa:pds:juno_jiram:data_calibrated:jir_img_rec_2024165T055551_v01::1.0"
#   Stable, globally unique, standard-prefixed, version-explicit.
#
# PDS3:
#   "pds3:" + DATA_SET_ID + ":" + PRODUCT_ID [ + ":v" + PRODUCT_VERSION_ID ]
#   Example: "pds3:JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0:WAV_2024165T055551_B_BIN:v01"
#   If PRODUCT_VERSION_ID is absent: "pds3:JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0:WAV_2024165T055551_B_BIN"
#   Stable: depends only on immutable archive identity fields, not on URL or
#   retrieval timestamp.
#
# Both forms:
#   - deterministic (same inputs → same id)
#   - source-standard-distinguishable (prefix pds3: / pds4:)
#   - dataset-distinguishable (data_set_id or lidvid lid component)
#   - product-identity-distinguishable (product_id or full lidvid)
#   - version-distinguishable where archive exposes one
#   - not dependent on retrieval time or local path


def build_pds4_source_record_id(lidvid: str) -> str:
    """Build a stable PDS4 source_record_id.

    Formula: ``"pds4:" + lidvid``

    Parameters
    ----------
    lidvid:
        Fully-versioned PDS4 LIDVID (already validated).

    Returns
    -------
    str
        Stable source_record_id, e.g.
        ``"pds4:urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri...::1.0"``
    """
    if not lidvid:
        raise ValueError("lidvid must not be empty.")
    return f"pds4:{lidvid}"


def build_pds3_source_record_id(
    data_set_id: str,
    product_id: str,
    product_version_id: Optional[str] = None,
) -> str:
    """Build a stable PDS3 source_record_id.

    Formula::

        "pds3:" + DATA_SET_ID + ":" + PRODUCT_ID
        [ + ":v" + PRODUCT_VERSION_ID ]   (when version is present)

    Parameters
    ----------
    data_set_id:
        PDS3 DATA_SET_ID keyword value (non-empty).

    product_id:
        PDS3 PRODUCT_ID keyword value (non-empty).

    product_version_id:
        PDS3 PRODUCT_VERSION_ID keyword value, or None.

    Returns
    -------
    str
        Stable source_record_id.
    """
    if not data_set_id.strip():
        raise ValueError("data_set_id must not be empty.")
    if not product_id.strip():
        raise ValueError("product_id must not be empty.")
    base = f"pds3:{data_set_id}:{product_id}"
    if product_version_id is not None:
        if not product_version_id.strip():
            raise ValueError("product_version_id must not be empty when provided.")
        return f"{base}:v{product_version_id}"
    return base


class ArchiveScienceProduct(BaseModel):
    """Normalized external archive science product metadata fact.

    This is the generic lower-level source fact produced by both the PDS3
    and PDS4 adapter paths.  It is NOT a GCSI DataProduct.

    Architecture
    ------------
    This model is completely independent of:
    - DataProduct (backend/app/models/data_product.py)  — UNCHANGED
    - Scenario, ScenarioLoader
    - TelecomEngine, RF, BER, SNR, link margin
    - API routes, frontend

    Fields
    ------
    source_record_id : str
        Deterministic, stable, globally unique identity for this source record.

        PDS4 formula: ``"pds4:" + lidvid``
        PDS3 formula: ``"pds3:" + DATA_SET_ID + ":" + PRODUCT_ID
                       [ + ":v" + PRODUCT_VERSION_ID ]``

        Must be non-empty.

    source_standard : ArchiveSourceStandard
        Archive metadata standard (PDS3 or PDS4).

    source_dataset_id : str
        Dataset identifier as reported by the archive:
        - PDS4: bundle or collection LID component
        - PDS3: DATA_SET_ID keyword value

    source_product_id : str
        Product identifier:
        - PDS4: logical_identifier (LID, without version)
        - PDS3: PRODUCT_ID keyword value

    source_version : str | None
        Product version, if exposed by the archive:
        - PDS4: version component after ``::``
        - PDS3: PRODUCT_VERSION_ID keyword value if present

    mission_name : str
        Mission name (e.g. ``"JUNO"``).

    spacecraft_name : str | None
        Spacecraft identifier (e.g. ``"JNO"``).  None when the archive
        metadata does not supply it and the profile does not require it.

    instrument_name : str | None
        Instrument identifier (e.g. ``"JIRAM"``, ``"MWR"``, ``"WAVES"``).
        None when the archive metadata does not supply it and the profile
        does not require it.

    product_family : str
        Science product family string (e.g. ``"B_BIN"``, ``"E_REC"``, ``"SURVEY"``).

    processing_level : str | None
        Processing level as reported by the archive, if present.

    observation_start_utc : datetime | None
        Observation start time in UTC.  None if not available in metadata.

    observation_stop_utc : datetime | None
        Observation stop time in UTC.  None if not available in metadata.
        If both are present, start <= stop is enforced.

    target_names : tuple[str, ...]
        Target names for this observation (e.g. ``("JUPITER",)``).

    data_files : tuple[ArchiveDataFile, ...]
        Normalized data-file metadata.  File names must be unique within
        this product.  May be empty for products where file metadata is
        not yet captured.

    total_data_size_bytes : int | None
        Exact aggregate size in bytes when ALL relevant data files have a
        known, exact size (ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        or SIZE_DISCOVERED_APPROXIMATE).

        None when ANY data file has an unknown size
        (size_certainty == SIZE_UNKNOWN or file_size_bytes is None).

        An empty data_files tuple produces total_data_size_bytes = 0 (not None),
        because "zero files" is distinct from "files with unknown size".

        Zero-byte payload (all files have file_size_bytes == 0) is also
        distinct from unknown-size payload: total_data_size_bytes == 0 (not None).

        Callers must NOT treat None as 0.

    Notes on source vs. normalization facts
    ----------------------------------------
    The following fields are SOURCE-NORMALIZED archive facts (verbatim or
    lightly normalised from the label):

        source_dataset_id, source_product_id, source_version,
        observation_start_utc, observation_stop_utc, target_names,
        payload metadata (data_files, total_data_size_bytes),
        instrument_name / spacecraft_name (when present in the label).

    The following fields are DETERMINISTIC NORMALIZATION CLASSIFICATIONS
    derived from validated dataset identity and/or profile binding:

        product_family  — from the adapter profile (not a NASA label field)
        mission_name    — from profile.expected_mission (bound to the profile
                          identity; provenance already records the profile_id)

    If mission_name is populated from profile.expected_mission rather than
    an explicit label field, the derivation is explicit: the provenance record
    carries the profile_id, which binds mission_name to the profile.

    source_label_ref : str | None
        Reference URL/path to the authoritative source label for this
        product, if known.  Not followed or downloaded.

    Notes
    -----
    - observation_start_utc and observation_stop_utc are None when the
      archive metadata does not supply them.  Do not substitute retrieved_at.
    - total_data_size_bytes is derived from data_files, not from an
      independent trusted input.
    - size ≠ historical downlink bytes; replay-size proxy policy is MODELED.
    - spacecraft_name and instrument_name may be None when genuinely absent
      from archive metadata.  Profile validators enforce presence when required.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_record_id: str = Field(
        description=(
            "Deterministic stable globally-unique source record identity. "
            "PDS4: 'pds4:' + lidvid. "
            "PDS3: 'pds3:' + DATA_SET_ID + ':' + PRODUCT_ID [+ ':v' + VERSION]."
        )
    )
    source_standard: ArchiveSourceStandard = Field(
        description="Archive metadata standard (PDS3 or PDS4)."
    )
    source_dataset_id: str = Field(
        description="Dataset identifier as reported by the archive."
    )
    source_product_id: str = Field(
        description="Product identifier as reported by the archive."
    )
    source_version: Optional[str] = Field(
        default=None,
        description="Product version if exposed by the archive, else None.",
    )
    mission_name: str = Field(description="Mission name, e.g. 'JUNO'.")
    spacecraft_name: Optional[str] = Field(
        default=None,
        description="Spacecraft identifier, e.g. 'JNO'. None if genuinely absent.",
    )
    instrument_name: Optional[str] = Field(
        default=None,
        description="Instrument identifier, e.g. 'JIRAM'. None if genuinely absent.",
    )
    product_family: str = Field(
        description="Science product family string, e.g. 'B_BIN', 'SURVEY'."
    )
    processing_level: Optional[str] = Field(
        default=None,
        description="Processing level as reported by the archive.",
    )
    observation_start_utc: Optional[datetime] = Field(
        default=None,
        description="Observation start time in UTC. None if not available.",
    )
    observation_stop_utc: Optional[datetime] = Field(
        default=None,
        description="Observation stop time in UTC. None if not available.",
    )
    target_names: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Target names for this observation.",
    )
    data_files: tuple[ArchiveDataFile, ...] = Field(
        default_factory=tuple,
        description="Normalized data-file metadata. File names must be unique.",
    )
    total_data_size_bytes: Optional[int] = Field(
        default=None,
        description=(
            "Exact aggregate size in bytes when ALL data files have known size. "
            "None when ANY file has unknown size. "
            "0 when there are no data files (empty payload). "
            "0 when all files have file_size_bytes == 0 (zero-byte payload). "
            "Must NOT be treated as 0 when None."
        ),
    )
    source_label_ref: Optional[str] = Field(
        default=None,
        description=(
            "Reference URL/path to the authoritative source label. "
            "Not followed or downloaded."
        ),
    )

    @field_validator("source_record_id", "source_dataset_id", "source_product_id",
                     "mission_name", "product_family", mode="after")
    @classmethod
    def _non_empty_strings(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty or whitespace-only.")
        return v

    @field_validator("spacecraft_name", "instrument_name", mode="after")
    @classmethod
    def _non_empty_optional_strings(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("Field must not be empty or whitespace-only when present.")
        return v

    @field_validator("observation_start_utc", "observation_stop_utc", mode="after")
    @classmethod
    def _validate_aware_datetimes(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return _require_aware_datetime(v)

    @field_validator("total_data_size_bytes", mode="after")
    @classmethod
    def _non_negative_total(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("total_data_size_bytes must be >= 0 when not None.")
        return v

    @model_validator(mode="after")
    def _validate_model(self) -> "ArchiveScienceProduct":
        """Enforce cross-field invariants."""
        # 1. Time ordering.
        if (
            self.observation_start_utc is not None
            and self.observation_stop_utc is not None
        ):
            if self.observation_start_utc > self.observation_stop_utc:
                raise ValueError(
                    "observation_start_utc must be <= observation_stop_utc."
                )

        # 2. Total size semantics (Item 5: unknown size != 0):
        #    - If any file has unknown size → total must be None.
        #    - If all files have known size → total must equal exact sum.
        #    - If no files → total must be 0 (not None).
        has_unknown = any(
            f.file_size_bytes is None for f in self.data_files
        )
        if has_unknown:
            if self.total_data_size_bytes is not None:
                raise ValueError(
                    "total_data_size_bytes must be None when any data file has "
                    "unknown size (file_size_bytes is None)."
                )
        else:
            expected_total = sum(f.file_size_bytes or 0 for f in self.data_files)
            if self.total_data_size_bytes != expected_total:
                raise ValueError(
                    f"total_data_size_bytes ({self.total_data_size_bytes!r}) must equal "
                    f"sum of data_files sizes ({expected_total}) when all sizes are known."
                )

        # 3. File names must be unique within this product.
        names = [f.file_name for f in self.data_files]
        if len(names) != len(set(names)):
            seen: set[str] = set()
            dups = [n for n in names if n in seen or seen.add(n)]  # type: ignore[func-returns-value]
            raise ValueError(
                f"Duplicate data_file names within ArchiveScienceProduct: {dups!r}."
            )

        return self


# ---------------------------------------------------------------------------
# E (Part H). ArchiveCaptureRecord
# ---------------------------------------------------------------------------


class ArchiveCaptureRecord(BaseModel):
    """Immutable capture binding an ArchiveScienceProduct to its raw label bytes.

    Produced by both GenericPds4ObservationalLabelAdapter and
    GenericPds3ObservationalLabelAdapter after successful validation.

    This is the raw-capture contract for the generic archive snapshot store.

    Capture invariants
    ------------------
    1. ``product.source_label_ref == source_label_ref`` (when both present)
    2. ``provenance.source_record_id == product.source_record_id``
    3. ``provenance.kind == EXTERNAL_AUTHORITATIVE``
    4. ``provenance.validation_status == VALIDATED``
    5. ``provenance.retrieved_at`` is present and timezone-aware.
    6. ``SHA-256(raw_label_bytes) == provenance.content_sha256``

    Notes
    -----
    - raw_label_bytes is ``bytes``; ``arbitrary_types_allowed=False`` because
      ``bytes`` is a native Pydantic type.
    - This model does NOT re-run the parser.  Full re-derivation happens
      in the ArchiveLabelSnapshotStore.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    source_label_ref: Optional[str] = Field(
        description="URL/path to the source label (matches product.source_label_ref)."
    )
    product: ArchiveScienceProduct = Field(
        description="Fully validated normalized ArchiveScienceProduct."
    )
    provenance: ProvenanceRecord = Field(
        description="EXTERNAL_AUTHORITATIVE provenance record for this capture."
    )
    raw_label_bytes: bytes = Field(
        description="Exact raw bytes of the source label as fetched from the archive."
    )

    @model_validator(mode="after")
    def _validate_capture_consistency(self) -> "ArchiveCaptureRecord":
        """Enforce capture self-consistency invariants."""
        import hashlib as _hashlib

        # 1. label_ref consistency.
        if (
            self.source_label_ref is not None
            and self.product.source_label_ref is not None
            and self.source_label_ref != self.product.source_label_ref
        ):
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                "source_label_ref does not match product.source_label_ref."
            )

        # 2. provenance.source_record_id must match product.source_record_id.
        if self.provenance.source_record_id != self.product.source_record_id:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                f"provenance.source_record_id ({self.provenance.source_record_id!r}) "
                f"!= product.source_record_id ({self.product.source_record_id!r})."
            )

        # 3. provenance.kind must be EXTERNAL_AUTHORITATIVE.
        if self.provenance.kind != ProvenanceKind.EXTERNAL_AUTHORITATIVE:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                f"provenance.kind must be EXTERNAL_AUTHORITATIVE; "
                f"got {self.provenance.kind!r}."
            )

        # 4. provenance.validation_status must be VALIDATED.
        if self.provenance.validation_status != ProvenanceValidationStatus.VALIDATED:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                f"provenance.validation_status must be VALIDATED; "
                f"got {self.provenance.validation_status!r}."
            )

        # 5. provenance.retrieved_at must be present and timezone-aware.
        ret = self.provenance.retrieved_at
        if ret is None:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                "provenance.retrieved_at must be present."
            )
        if ret.tzinfo is None or ret.utcoffset() is None:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                "provenance.retrieved_at must be timezone-aware."
            )

        # 6. SHA-256(raw_label_bytes) must equal provenance.content_sha256.
        computed = _hashlib.sha256(self.raw_label_bytes).hexdigest()
        if self.provenance.content_sha256 is None:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                "provenance.content_sha256 must be present."
            )
        if computed != self.provenance.content_sha256:
            raise ValueError(
                "ArchiveCaptureRecord invariant violated: "
                "SHA-256(raw_label_bytes) does not match provenance.content_sha256."
            )

        return self


# ---------------------------------------------------------------------------
# F (Part C). ProductRepresentationKind
# ---------------------------------------------------------------------------


class ProductRepresentationKind(str, Enum):
    """Taxonomy of representational relationships between archive science products.

    Values describe the nature of the relationship between two logical
    products that share the same observation or are otherwise related.
    No instrument names are encoded in this enum.

    SAME_OBSERVATION_ALTERNATE_PROCESSING
        Two representations derived from the same instrument acquisition
        through different processing pipelines.
        Example: JunoCam EDR ↔ RDR.

    INDEPENDENT_ACQUISITION
        Two products from independent instrument acquisitions.
        Example: WAVES Survey ↔ WAVES Burst (independent product families).

    INDEPENDENT_TEMPORAL_SEGMENT
        Two products from independent temporal segments of a mission phase.
        Example: FGM standard segment ↔ PJ62-specific segment.

    DERIVED_REPRESENTATION
        One product is derived from another as a secondary data product.
        Example: a calibrated product derived from raw telemetry.

    COMPONENT_RELATION
        One product is a component of another larger composite product.
        Example: a single-orbit slice of a multi-orbit data bundle.
    """

    SAME_OBSERVATION_ALTERNATE_PROCESSING = "same_observation_alternate_processing"
    INDEPENDENT_ACQUISITION = "independent_acquisition"
    INDEPENDENT_TEMPORAL_SEGMENT = "independent_temporal_segment"
    DERIVED_REPRESENTATION = "derived_representation"
    COMPONENT_RELATION = "component_relation"


# ---------------------------------------------------------------------------
# G (Part C). ProductRepresentationRelationship
# ---------------------------------------------------------------------------


class ProductRepresentationRelationship(BaseModel):
    """Immutable directed relationship between two source archive products.

    The relationship references products by their stable ``source_record_id``
    values so it remains valid regardless of how the products are stored.

    Fields
    ------
    from_record_id : str
        source_record_id of the first (from) product.

    to_record_id : str
        source_record_id of the second (to) product.

    kind : ProductRepresentationKind
        Relationship kind.

    notes : str | None
        Optional human-readable annotation.

    Constraints
    -----------
    - from_record_id != to_record_id (no self-relation).
    - Both IDs must be non-empty.
    - The relationship is directed (from → to); callers may create both
      directions if bidirectional semantics are required.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    from_record_id: str = Field(
        description="source_record_id of the from product."
    )
    to_record_id: str = Field(
        description="source_record_id of the to product."
    )
    kind: ProductRepresentationKind = Field(
        description="Nature of the relationship."
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional human-readable annotation.",
    )

    @model_validator(mode="after")
    def _no_self_relation(self) -> "ProductRepresentationRelationship":
        if self.from_record_id == self.to_record_id:
            raise ValueError(
                "ProductRepresentationRelationship: from_record_id and "
                "to_record_id must not be the same (no self-relation)."
            )
        if not self.from_record_id.strip():
            raise ValueError("from_record_id must not be empty.")
        if not self.to_record_id.strip():
            raise ValueError("to_record_id must not be empty.")
        return self


# ---------------------------------------------------------------------------
# H (Part D). VerifiedInventoryEntry
# ---------------------------------------------------------------------------


class VerifiedInventoryEntry(BaseModel):
    """One logical GCSI replay candidate.

    A single ``VerifiedInventoryEntry`` represents ONE logical GCSI replay
    candidate, which may correspond to one or more archive representations.

    For example, a JunoCam observation that has both an EDR and an RDR
    would be represented as ONE VerifiedInventoryEntry with two
    ``representation_record_ids``.

    Fields
    ------
    logical_product_id : str
        Stable unique identifier for this logical replay candidate within
        the manifest.  Must be unique within a VerifiedInventoryManifest.

    representation_record_ids : tuple[str, ...]
        Ordered tuple of source_record_id values for the archive
        representations of this logical product.  Must be non-empty.
        No duplicates within the same entry.

    availability_time_utc : datetime
        For V2 completed-product backlog: the authoritative observation_stop
        time from the source archive.  Must be timezone-aware.

    source_fact_provenance_ids : tuple[str, ...]
        Provenance record IDs from a ProvenanceManifest that document
        the external-authoritative sources for the facts in this entry.
        May be empty at inventory construction time; must be populated
        before replay queue assembly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    logical_product_id: str = Field(
        description=(
            "Stable unique identifier for this logical replay candidate. "
            "Must be unique within a VerifiedInventoryManifest."
        )
    )
    representation_record_ids: tuple[str, ...] = Field(
        description=(
            "Ordered source_record_id values for archive representations "
            "of this logical product. Must be non-empty. No duplicates."
        )
    )
    availability_time_utc: datetime = Field(
        description=(
            "Authoritative observation_stop time in UTC. "
            "Timezone-aware. Used as availability time for V2 replay backlog."
        )
    )
    source_fact_provenance_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Provenance record IDs documenting external-authoritative sources "
            "for this entry's facts."
        ),
    )

    @field_validator("logical_product_id", mode="after")
    @classmethod
    def _non_empty_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("logical_product_id must not be empty.")
        return v

    @field_validator("availability_time_utc", mode="after")
    @classmethod
    def _aware_availability(cls, v: datetime) -> datetime:
        return _require_aware_datetime(v)

    @model_validator(mode="after")
    def _validate_entry(self) -> "VerifiedInventoryEntry":
        # representation_record_ids must be non-empty.
        if not self.representation_record_ids:
            raise ValueError(
                "representation_record_ids must contain at least one source_record_id."
            )
        # No duplicates within an entry.
        rids = list(self.representation_record_ids)
        if len(rids) != len(set(rids)):
            raise ValueError(
                "representation_record_ids must not contain duplicates within "
                "a single VerifiedInventoryEntry."
            )
        # All representation IDs must be non-empty.
        for rid in rids:
            if not rid.strip():
                raise ValueError(
                    "representation_record_ids must not contain empty strings."
                )
        return self


# ---------------------------------------------------------------------------
# I (Part D). VerifiedInventoryManifest
# ---------------------------------------------------------------------------

# Maximum serialized manifest size: 16 MiB (suitable for 411+ entries with
# full metadata, with comfortable headroom; not hard-coded to 411).
_MAX_MANIFEST_BYTES: int = 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# L. VerifiedSourceRecordRef — source record registry entry
# ---------------------------------------------------------------------------


class VerifiedSourceRecordRef(BaseModel):
    """Lightweight registry entry for a verified source record.

    Enables VerifiedInventoryManifest to prove that referenced
    representation_record_ids actually exist and have been verified.

    Fields
    ------
    source_record_id : str
        Stable identity for this source record (pds3:... or pds4:...).
    source_standard : ArchiveSourceStandard
        Archive standard for this record.
    snapshot_ref : str | None
        Reference to the snapshot file/ID that captured this record.
    provenance_id : str
        Provenance record ID for the EXTERNAL_AUTHORITATIVE capture.
    normalizer_id : str
        Stable identifier for the normalizer used, e.g.
        ``"gcsi.generic_pds3_label.v1"`` or ``"gcsi.generic_pds4_label.v1"``.
    profile_id : str
        Stable identifier for the validation profile used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_record_id: str = Field(description="Stable source record identity.")
    source_standard: ArchiveSourceStandard = Field(description="Archive standard.")
    snapshot_ref: Optional[str] = Field(
        default=None, description="Snapshot reference."
    )
    provenance_id: str = Field(description="EXTERNAL_AUTHORITATIVE provenance ID.")
    normalizer_id: str = Field(description="Normalizer identifier.")
    profile_id: str = Field(description="Profile identifier.")

    @field_validator(
        "source_record_id", "provenance_id", "normalizer_id", "profile_id",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# M. Manifest ID formula (v2 — covers all semantic content)
# ---------------------------------------------------------------------------


def _compute_manifest_id(
    entries: tuple[VerifiedInventoryEntry, ...],
    source_records: tuple[VerifiedSourceRecordRef, ...] = (),
) -> str:
    """Compute a deterministic manifest_id covering ALL semantic content.

    Formula::

        SHA-256(
            "gcsi.verified_inventory_manifest:v2:"
            + JSON-canonical-repr of sorted entry content + source records
        )

    Every field in each entry and each source record contributes to the ID.
    Entries are sorted by logical_product_id; source records by source_record_id.
    Returns 64-char lowercase hex.

    The version string changed from v1 to v2 because this formula is more
    comprehensive: it covers all entry fields and source record fields rather
    than only logical_product_ids.
    """
    canonical_entries = []
    for e in sorted(entries, key=lambda x: x.logical_product_id):
        canonical_entries.append({
            "logical_product_id": e.logical_product_id,
            "representation_record_ids": list(e.representation_record_ids),
            "availability_time_utc": e.availability_time_utc.astimezone(
                timezone.utc
            ).isoformat(),
            "source_fact_provenance_ids": list(e.source_fact_provenance_ids),
        })
    canonical_sources = []
    for s in sorted(source_records, key=lambda x: x.source_record_id):
        canonical_sources.append({
            "source_record_id": s.source_record_id,
            "source_standard": s.source_standard.value,
            "snapshot_ref": s.snapshot_ref,
            "provenance_id": s.provenance_id,
            "normalizer_id": s.normalizer_id,
            "profile_id": s.profile_id,
        })
    payload = "gcsi.verified_inventory_manifest:v2:" + json.dumps(
        {"entries": canonical_entries, "source_records": canonical_sources},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VerifiedInventoryManifest(BaseModel):
    """Validated manifest of all logical GCSI V2 replay candidates.

    One manifest represents the complete set of eligible logical products
    for one replay accumulation window.

    Integrity rules enforced
    ------------------------
    1. ``logical_product_id`` values are unique across all entries.
    2. No duplicate ``representation_record_ids`` across different entries
       (a source record may not belong to two logical products).
    3. ``manifest_id`` is a deterministic SHA-256 over all semantic content
       (entry fields + source record fields).
    4. Serialized manifest must not exceed ``_MAX_MANIFEST_BYTES`` (16 MiB).
    5. All ``availability_time_utc`` values are timezone-aware (enforced
       per-entry by VerifiedInventoryEntry).
    6. When ``source_records`` is non-empty:
       - No duplicate ``source_record_id`` values.
       - Every ``representation_record_id`` in every entry must resolve to
         exactly one ``source_record_id`` in ``source_records``.
       - Every ``source_fact_provenance_id`` in every entry must resolve to
         exactly one ``provenance_id`` in ``source_records``.

    Design decisions
    ----------------
    - No max product count hard-coded to 411.  The manifest is designed for
      411+ products and the constraint is a reasonable file-size limit only.
    - The manifest does NOT contain modeled replay priority scores.  It is
      a source/inventory artifact.
    - Empty manifests (0 entries) are rejected — a manifest must have at
      least one entry.
    - ``source_records`` is mandatory and non-empty.  A VerifiedInventoryManifest
      without a source registry is not a valid verified manifest.
      Every representation_record_id must resolve to a source record.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str = Field(
        description=(
            "Deterministic SHA-256 fingerprint over all semantic manifest content. "
            "Formula: SHA-256('gcsi.verified_inventory_manifest:v2:' "
            "+ JSON-canonical of sorted entries + source records)"
        )
    )
    entries: tuple[VerifiedInventoryEntry, ...] = Field(
        description=(
            "All logical inventory entries. "
            "Must be non-empty. logical_product_id values must be unique."
        )
    )
    source_records: tuple[VerifiedSourceRecordRef, ...] = Field(
        description=(
            "Registry of verified source records referenced by entries. "
            "Must be non-empty: a VerifiedInventoryManifest without a source "
            "registry is not a valid verified manifest. "
            "All representation_record_ids and source_fact_provenance_ids in "
            "entries must resolve here."
        ),
    )

    @field_validator("manifest_id", mode="after")
    @classmethod
    def _validate_manifest_id_format(cls, v: str) -> str:
        return _require_sha256(v)

    @model_validator(mode="after")
    def _validate_manifest_integrity(self) -> "VerifiedInventoryManifest":
        """Enforce all manifest integrity rules.

        Validation order:
        1. entries non-empty (structural)
        2. unique logical_product_ids (structural)
        3. no duplicate representation_record_ids (structural)
        4. manifest_id correct (integrity)
        5. source_records non-empty (Item 6: verified manifest must have registry)
        6. source_records no-duplicate + referential integrity
        """
        # Rule: non-empty entries.
        if not self.entries:
            raise ValueError(
                "VerifiedInventoryManifest must contain at least one entry."
            )

        # Rule 1: unique logical_product_ids.
        seen_ids: set[str] = set()
        for entry in self.entries:
            if entry.logical_product_id in seen_ids:
                raise ValueError(
                    f"Duplicate logical_product_id "
                    f"{entry.logical_product_id!r} in VerifiedInventoryManifest."
                )
            seen_ids.add(entry.logical_product_id)

        # Rule 2: no duplicate representation_record_ids across entries.
        seen_rids: dict[str, str] = {}  # rid -> logical_product_id
        for entry in self.entries:
            for rid in entry.representation_record_ids:
                if rid in seen_rids:
                    raise ValueError(
                        f"representation_record_id {rid!r} appears in both "
                        f"logical_product_id={seen_rids[rid]!r} and "
                        f"logical_product_id={entry.logical_product_id!r}. "
                        "A source record may not belong to two logical products."
                    )
                seen_rids[rid] = entry.logical_product_id

        # Rule 3: manifest_id must match deterministic recomputation.
        expected_id = _compute_manifest_id(self.entries, self.source_records)
        if self.manifest_id != expected_id:
            raise ValueError(
                "manifest_id does not match expected deterministic value. "
                f"Expected: {expected_id!r}. Got: {self.manifest_id!r}."
            )

        # Rule 4: source_records must be non-empty (Item 6).
        # A VerifiedInventoryManifest without a source registry is not verified.
        if not self.source_records:
            raise ValueError(
                "VerifiedInventoryManifest.source_records must be non-empty. "
                "A verified manifest must include its source registry. "
                "Every representation_record_id must resolve to a source record."
            )

        # Rules 5–7: source_records referential integrity (always enforced).
        # Rule 5: no duplicate source_record_id in source_records.
        seen_srids: set[str] = set()
        seen_prov_ids: set[str] = set()
        for sr in self.source_records:
            if sr.source_record_id in seen_srids:
                raise ValueError(
                    f"Duplicate source_record_id {sr.source_record_id!r} "
                    "in VerifiedInventoryManifest.source_records."
                )
            seen_srids.add(sr.source_record_id)
            seen_prov_ids.add(sr.provenance_id)

        # Rule 6: every representation_record_id must resolve.
        for entry in self.entries:
            for rid in entry.representation_record_ids:
                if rid not in seen_srids:
                    raise ValueError(
                        f"representation_record_id {rid!r} in entry "
                        f"{entry.logical_product_id!r} does not resolve to "
                        "any source_record_id in source_records."
                    )

        # Rule 7: every source_fact_provenance_id must resolve.
        for entry in self.entries:
            for prov_id in entry.source_fact_provenance_ids:
                if prov_id not in seen_prov_ids:
                    raise ValueError(
                        f"source_fact_provenance_id {prov_id!r} in entry "
                        f"{entry.logical_product_id!r} does not resolve to "
                        "any provenance_id in source_records."
                    )

        return self

    @classmethod
    def build(
        cls,
        entries: tuple[VerifiedInventoryEntry, ...] | list[VerifiedInventoryEntry],
        source_records: (
            tuple[VerifiedSourceRecordRef, ...]
            | list[VerifiedSourceRecordRef]
        ),
    ) -> "VerifiedInventoryManifest":
        """Construct a manifest with auto-computed manifest_id.

        Parameters
        ----------
        entries:
            Non-empty collection of VerifiedInventoryEntry objects.

        source_records:
            Mandatory non-empty collection of VerifiedSourceRecordRef objects.
            Every representation_record_id in entries must resolve to an entry here.

        Returns
        -------
        VerifiedInventoryManifest
            Fully validated manifest.

        Raises
        ------
        pydantic.ValidationError
            If entries violate any integrity rule.
        """
        entries_tuple: tuple[VerifiedInventoryEntry, ...] = (
            tuple(entries) if not isinstance(entries, tuple) else entries
        )
        source_records_tuple: tuple[VerifiedSourceRecordRef, ...] = (
            tuple(source_records)
            if not isinstance(source_records, tuple)
            else source_records
        )
        manifest_id = _compute_manifest_id(entries_tuple, source_records_tuple)
        return cls(
            manifest_id=manifest_id,
            entries=entries_tuple,
            source_records=source_records_tuple,
        )
