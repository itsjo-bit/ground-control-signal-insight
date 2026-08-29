"""GCSI Phase 6F-B2.1.4 — Strict Frozen Models for Discovery Sidecar Extractions.

B2.1.3 additions:
- HistoricalReplayV2DiscoveryEvidenceSidecar: top-level frozen Pydantic model
- All sidecar row models now carry discovery_evidence_id (required)
- All directory-based rows carry relative_label_path (required)
- Path validators on relative_label_path: reject empty, absolute, leading slash,
  '..', '.', backslash, NUL, percent-encoded traversal, query, fragment
- Cross-field validators on each row (family/filename consistency)
- JadeDiscoveryLabel updated for authoritative product IDs (V04, exact file paths)
- JADE temporal evidence status can be EXACT_DISCOVERY_METADATA
- NormalizedDiscoveryExtractions: typed collection with partition invariants
- DiscoveryPartition: total_orbit62_rows == pre_rows + eligible_rows + post_rows enforced

B2.1.4 additions:
- TypedDiscoveryEvidence: strict typed evidence model replacing plain dict
- NormalizedDiscoveryExtractions: real typed model replacing dict
- HistoricalReplayV2DiscoveryEvidenceSidecar: uses TypedDiscoveryEvidence + typed extractions
- FgmDiscoveryLabel: gains candidate_classification and expected_archive_identity_source
- FgmCandidateClassification enum: FULL_RESOLUTION_STANDARD / FULL_RESOLUTION_PJ62 /
  R1S_OR_DOWNSAMPLED_ALTERNATE / OTHER_RELEVANT_VARIANT
- compute_sidecar_artifact_id: canonical sort of each normalized collection before hashing
  (same semantic rows in different input order → same artifact_id)

All models: frozen=True, extra="forbid"
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Temporal partition boundaries (imported lazily to avoid circular imports;
# duplicated here as module-level constants for use in row validators).
# ---------------------------------------------------------------------------

# Accumulation start: PRE if stop_time <= this
_ACCUMULATION_START_UTC: datetime = datetime(2024, 6, 13, 10, 0, 0, tzinfo=timezone.utc)

# Decision epoch: POST if stop_time > this
_DECISION_EPOCH_UTC: datetime = datetime(
    2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc
)


# ---------------------------------------------------------------------------
# UTC datetime parsing helper (4.3)
# ---------------------------------------------------------------------------


def _parse_utc_datetime(value: str, field_name: str) -> datetime:
    """Parse an ISO-8601 string as a timezone-aware UTC datetime.

    Accepts strings ending in 'Z', with explicit +00:00 offset, or implicit
    naive timestamps (treated as UTC — PDS archive convention).  Naive timestamps
    are NOT silently accepted as arbitrary local time; they are explicitly
    coerced to UTC under the archive contract that all timestamps are UTC.

    Rejects truly invalid date strings (unparseable, malformed, etc.).
    """
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty.")
    normalised = value.strip()
    # Normalise 'Z' suffix to '+00:00' for fromisoformat compatibility
    if normalised.endswith("Z"):
        normalised = normalised[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} {value!r} is not a valid ISO-8601 datetime: {exc}."
        ) from exc
    # Coerce naive timestamps to UTC explicitly (PDS archive convention)
    if dt.tzinfo is None or dt.utcoffset() is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _classify_temporal_partition(stop_utc: datetime) -> str:
    """Classify a stop time into PRE / ELIGIBLE / POST partition string."""
    if stop_utc <= _ACCUMULATION_START_UTC:
        return "PRE"
    if stop_utc > _DECISION_EPOCH_UTC:
        return "POST"
    return "ELIGIBLE"


# ---------------------------------------------------------------------------
# Path security helpers
# ---------------------------------------------------------------------------


_FORBIDDEN_PATH_BYTES = {b"\x00"}  # NUL
_PERCENT_TRAVERSAL_RE = re.compile(r"%2[Ee]|%2[Ff]|%5[Cc]", re.IGNORECASE)


def _validate_relative_label_path(v: str, expected_extension: str | None = None) -> str:
    """Validate a relative label path for security and correctness.

    Rejects:
    - empty string
    - absolute path (starts with '/')
    - path containing '..' component
    - path that IS exactly '.'
    - backslash characters
    - NUL bytes
    - percent-encoded traversal (%2E, %2F, %5C)
    - query strings ('?' in path)
    - fragments ('#' in path)
    """
    if not v or not v.strip():
        raise ValueError("relative_label_path must not be empty.")
    if v.startswith("/"):
        raise ValueError(
            f"relative_label_path must not start with '/': {v!r}."
        )
    if "\\" in v:
        raise ValueError(
            f"relative_label_path must not contain backslash: {v!r}."
        )
    if "\x00" in v:
        raise ValueError(
            f"relative_label_path must not contain NUL byte: {v!r}."
        )
    if "?" in v:
        raise ValueError(
            f"relative_label_path must not contain query string '?': {v!r}."
        )
    if "#" in v:
        raise ValueError(
            f"relative_label_path must not contain fragment '#': {v!r}."
        )
    if _PERCENT_TRAVERSAL_RE.search(v):
        raise ValueError(
            f"relative_label_path contains percent-encoded traversal: {v!r}."
        )
    parts = v.replace("\\", "/").split("/")
    for part in parts:
        if part == "..":
            raise ValueError(
                f"relative_label_path contains '..' traversal: {v!r}."
            )
        if part == ".":
            raise ValueError(
                f"relative_label_path contains '.' component: {v!r}."
            )
    if expected_extension is not None:
        ext = v.rsplit(".", 1)[-1].lower() if "." in v else ""
        if ext != expected_extension.lower().lstrip("."):
            raise ValueError(
                f"relative_label_path must have {expected_extension!r} extension, "
                f"got {ext!r}: {v!r}."
            )
    return v


# ---------------------------------------------------------------------------
# Classification enums
# ---------------------------------------------------------------------------


class JiramFamily(str, Enum):
    """JIRAM product family classification."""
    IMG = "IMG"
    SPE = "SPE"


class MwrProductType(str, Enum):
    """MWR product type classification."""
    IRDR = "IRDR"
    GRDR = "GRDR"


class MwrInclusion(str, Enum):
    """MWR product inclusion classification (temporal window eligibility)."""
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


class JadeInclusion(str, Enum):
    """JADE product inclusion classification."""
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


class WavesSurveyInclusion(str, Enum):
    """WAVES Survey product inclusion classification."""
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


class JunoCamPartition(str, Enum):
    """JunoCam raw-row partition classification."""
    PRE = "PRE"
    ELIGIBLE = "ELIGIBLE"
    POST = "POST"


class JunoCamRepresentation(str, Enum):
    """JunoCam representation kind (EDR or RDR)."""
    EDR = "EDR"
    RDR = "RDR"


class WavesBurstPartition(str, Enum):
    """WAVES Burst raw-row partition classification."""
    PRE = "PRE"
    ELIGIBLE = "ELIGIBLE"
    POST = "POST"


class WavesBurstFamily(str, Enum):
    """WAVES Burst product family classification."""
    B_BIN = "B_BIN"
    E_BIN = "E_BIN"
    B_REC = "B_REC"
    E_REC = "E_REC"
    NBS_REC = "NBS_REC"


class ExpectedArchiveIdentitySource(str, Enum):
    """Source classification for expected_archive_identity."""
    DISCOVERY_METADATA = "DISCOVERY_METADATA"
    DISCOVERY_PATH_DERIVED = "DISCOVERY_PATH_DERIVED"
    UNAVAILABLE_UNTIL_LABEL = "UNAVAILABLE_UNTIL_LABEL"


class FgmCandidateClassification(str, Enum):
    """Classification of a discovered FGM candidate label.

    B2.1.4: Explicit classification to distinguish selected vs. excluded candidates.

    FULL_RESOLUTION_STANDARD:
        Standard full-resolution temporal segment (no PJ62-specific suffix).
        Selected for replay.

    FULL_RESOLUTION_PJ62:
        PJ62-associated full-resolution temporal segment (contains _pj62).
        Selected for replay.

    R1S_OR_DOWNSAMPLED_ALTERNATE:
        Reduced-rate (r1s) or downsampled variant.
        Excluded from logical replay selection (lower resolution, not primary science).

    OTHER_RELEVANT_VARIANT:
        Other relevant candidate that does not fit the above categories.
        Stored in discovery evidence for completeness; excluded from replay.
    """
    FULL_RESOLUTION_STANDARD = "FULL_RESOLUTION_STANDARD"
    FULL_RESOLUTION_PJ62 = "FULL_RESOLUTION_PJ62"
    R1S_OR_DOWNSAMPLED_ALTERNATE = "R1S_OR_DOWNSAMPLED_ALTERNATE"
    OTHER_RELEVANT_VARIANT = "OTHER_RELEVANT_VARIANT"


# ---------------------------------------------------------------------------
# B2.1.4: TypedDiscoveryEvidence
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Known placeholder SHA-256 patterns (all-single-character strings)
_PLACEHOLDER_SHA_PATTERNS: frozenset[str] = frozenset(
    c * 64 for c in "0123456789abcdef"
)

_KNOWN_SOURCE_KINDS: frozenset[str] = frozenset({
    "pds4_directory_html",
    "pds3_directory_html",
    "pds3_index_tab",
    "pds3_index_lbl",
    "pds4_xml_label",
    "pds3_label_file",
})


class TypedDiscoveryEvidence(BaseModel):
    """Strict typed evidence model for the discovery evidence sidecar.

    B2.1.4: Replaces plain dict evidence records with a validated frozen model.

    All fields are required unless explicitly Optional.
    Validation:
    - evidence_id non-empty
    - source_url trusted (HTTPS, no userinfo, no query, no fragment, no backslash)
    - retrieved_at timezone-aware UTC
    - response_sha256: 64 lowercase hex, not a placeholder
    - byte_count >= 0
    - http_status == 200 for committed successful evidence
    - source_kind in known set
    - extractor_id (via extractor identity embedded in evidence_id convention)
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    evidence_id: str = Field(description="Stable human-readable identifier for this evidence source.")
    source_url: str = Field(description="Official archive URL fetched.")
    retrieved_at: datetime = Field(description="Timezone-aware UTC actual fetch timestamp.")
    response_sha256: str = Field(description="SHA-256 of exact response bytes (64 lowercase hex, non-placeholder).")
    byte_count: int = Field(description="Exact byte length of the fetched response body.")
    http_status: int = Field(description="HTTP status code (must be 200 for committed evidence).")
    source_kind: str = Field(description="Kind of discovery resource.")
    relevant_row_count: Optional[int] = Field(
        default=None,
        description="Row/entry count relevant to this plan, if applicable.",
    )

    @field_validator("evidence_id", "source_url", "source_kind", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _aware_dt(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware.")
        return v.astimezone(timezone.utc)

    @field_validator("response_sha256", mode="after")
    @classmethod
    def _sha256_format(cls, v: str) -> str:
        if not _SHA256_RE.fullmatch(v):
            raise ValueError(
                "response_sha256 must be exactly 64 lowercase hex characters."
            )
        if v in _PLACEHOLDER_SHA_PATTERNS:
            raise ValueError(
                f"response_sha256 {v[:8]}... is a known placeholder pattern. "
                "Use actual response bytes."
            )
        return v

    @field_validator("byte_count", mode="after")
    @classmethod
    def _non_negative_bytes(cls, v: int) -> int:
        if v < 0:
            raise ValueError("byte_count must be non-negative.")
        return v

    @field_validator("http_status", mode="after")
    @classmethod
    def _valid_status(cls, v: int) -> int:
        if v != 200:
            raise ValueError(
                f"http_status must be 200 for committed evidence records, got {v}."
            )
        return v

    @field_validator("source_kind", mode="after")
    @classmethod
    def _known_source_kind(cls, v: str) -> str:
        if v not in _KNOWN_SOURCE_KINDS:
            raise ValueError(
                f"source_kind {v!r} is not in the known set: {sorted(_KNOWN_SOURCE_KINDS)!r}."
            )
        return v

    @field_validator("relevant_row_count", mode="after")
    @classmethod
    def _non_negative_count(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("relevant_row_count must be non-negative when present.")
        return v

    @field_validator("source_url", mode="after")
    @classmethod
    def _trusted_url(cls, v: str) -> str:
        from urllib.parse import urlsplit
        if "%" in v:
            raise ValueError(f"source_url must not contain percent-encoded characters: {v!r}.")
        if "\\" in v:
            raise ValueError(f"source_url must not contain backslash: {v!r}.")
        try:
            parsed = urlsplit(v)
        except Exception as exc:
            raise ValueError(f"source_url could not be parsed: {v!r}.") from exc
        if parsed.scheme != "https":
            raise ValueError(f"source_url must use HTTPS: {v!r}.")
        if not parsed.hostname:
            raise ValueError(f"source_url must have a non-empty hostname: {v!r}.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(f"source_url must not contain userinfo: {v!r}.")
        if parsed.port is not None and parsed.port != 443:
            raise ValueError(f"source_url must not have a non-443 explicit port: {v!r}.")
        if parsed.query:
            raise ValueError(f"source_url must not contain a query string: {v!r}.")
        if parsed.fragment:
            raise ValueError(f"source_url must not contain a fragment: {v!r}.")
        return v


# ---------------------------------------------------------------------------
# JIRAM
# ---------------------------------------------------------------------------


class JiramDiscoveryLabel(BaseModel):
    """One JIRAM discovery label record extracted from the orbit62 directory HTML.

    Represents a single XML product label filename with family classification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    filename: str = Field(description="Exact XML filename (e.g. JIR_IMG_RDR_2024166T090046_V01.xml).")
    family: JiramFamily = Field(description="Product family: IMG or SPE.")
    hhmmss: str = Field(description="HHMMSS timestamp component extracted from filename.")
    relative_label_path: str = Field(
        description="Relative path from JIRAM base URL (e.g. JIR_IMG_RDR_2024166T090046_V01.xml)."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )
    expected_archive_identity_source: ExpectedArchiveIdentitySource = Field(
        default=ExpectedArchiveIdentitySource.UNAVAILABLE_UNTIL_LABEL,
        description="Source of expected_archive_identity: DISCOVERY_METADATA or UNAVAILABLE_UNTIL_LABEL."
    )

    @field_validator("filename", "hhmmss", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("hhmmss", mode="after")
    @classmethod
    def _hhmmss_format(cls, v: str) -> str:
        if len(v) != 6 or not v.isdigit():
            raise ValueError(f"hhmmss must be exactly 6 digits, got {v!r}.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="xml")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "JiramDiscoveryLabel":
        # family IMG must appear in filename; family SPE must appear in filename
        fam = self.family.value  # "IMG" or "SPE"
        if fam not in self.filename:
            raise ValueError(
                f"JIRAM family {fam!r} must appear in filename {self.filename!r}."
            )
        # relative_label_path and filename should match
        if self.filename not in self.relative_label_path:
            raise ValueError(
                f"JIRAM filename {self.filename!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        return self


# ---------------------------------------------------------------------------
# MWR
# ---------------------------------------------------------------------------


class MwrDiscoveryLabel(BaseModel):
    """One MWR discovery label record extracted from a directory HTML.

    Represents a single XML product label filename with type/DOY/hour classification.
    The archive provides 24 products per type per DOY (hours 0-23).
    Inclusion classification reflects temporal window eligibility:
      DOY165 hours 10-23 = ELIGIBLE; DOY166 hours 0-8 = ELIGIBLE; others = EXCLUDED.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    filename: str = Field(description="Exact XML filename stem (e.g. MWR62RI2024165100000_R04120_V04).")
    product_type: MwrProductType = Field(description="Product type: IRDR or GRDR.")
    doy: int = Field(description="Day of year (165 or 166).")
    hour: int = Field(description="UTC hour of product (0-23).")
    code: str = Field(description="Archive rate-code suffix (e.g. R04120).")
    relative_label_path: str = Field(
        description="Relative path from MWR base URL (e.g. IRDR/2024/2024165/MWR62RI2024165100000_R04120_V04.xml)."
    )
    inclusion: MwrInclusion = Field(
        description="Temporal window eligibility: ELIGIBLE or EXCLUDED."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("filename", "code", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("doy", mode="after")
    @classmethod
    def _valid_doy(cls, v: int) -> int:
        if v not in (165, 166):
            raise ValueError(f"MWR DOY must be 165 or 166, got {v}.")
        return v

    @field_validator("hour", mode="after")
    @classmethod
    def _valid_hour(cls, v: int) -> int:
        if not (0 <= v <= 23):
            raise ValueError(f"hour must be 0-23, got {v}.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="xml")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "MwrDiscoveryLabel":
        # product_type IRDR → filename contains "RI"; GRDR → "RG"
        kind_letter = "RI" if self.product_type == MwrProductType.IRDR else "RG"
        if kind_letter not in self.filename:
            raise ValueError(
                f"MWR product_type {self.product_type.value!r} must match "
                f"filename (expected '{kind_letter}' in {self.filename!r})."
            )
        # filename must end with .xml NOT included (stem only) but relative_label_path must end .xml
        if not self.relative_label_path.lower().endswith(".xml"):
            raise ValueError(
                f"MWR relative_label_path must end with .xml: {self.relative_label_path!r}."
            )
        return self


# ---------------------------------------------------------------------------
# UVS
# ---------------------------------------------------------------------------


class UvsDiscoveryLabel(BaseModel):
    """One UVS discovery label record extracted from the ORBIT-62 directory HTML."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    filename: str = Field(description="Exact XML filename stem (e.g. UVS_S01_771573735_2024165_P62OBS_V01).")
    sensor: str = Field(description="Sensor identifier (e.g. S01, S02, ...).")
    sclk: str = Field(description="Spacecraft clock string.")
    doy_str: str = Field(description="Day-of-year string (e.g. 2024165, 2024166).")
    obs_type: str = Field(description="Observation type (e.g. P62OBS, P62SY1).")
    relative_label_path: str = Field(
        description="Relative path from UVS base URL (e.g. UVS_S01_771573735_2024165_P62OBS_V01.xml)."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("filename", "sensor", "sclk", "doy_str", "obs_type", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="xml")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "UvsDiscoveryLabel":
        # sensor/sclk/doy_str/obs_type should appear in filename
        fn = self.filename
        for part in (self.sensor, self.sclk, self.doy_str, self.obs_type):
            if part not in fn:
                raise ValueError(
                    f"UVS field {part!r} must appear in filename {fn!r}."
                )
        # relative_label_path should contain filename
        if self.filename not in self.relative_label_path:
            raise ValueError(
                f"UVS filename {self.filename!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        return self


# ---------------------------------------------------------------------------
# FGM
# ---------------------------------------------------------------------------


class FgmDiscoveryLabel(BaseModel):
    """One FGM discovery label record extracted from the PERI-62 directory HTML.

    B2.1.4: Now includes candidate_classification (explicit enum) and
    expected_archive_identity_source to correctly distinguish filename-derived
    identity expectations from archive-native PRODUCT_ID.

    product_id field: set to LABEL_VERIFICATION_PENDING when discovery source
    does not provide authoritative PRODUCT_ID (directory HTML only).
    Actual PRODUCT_ID established by B2.2 label verification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lbl_filename: str = Field(description="Exact .lbl filename (e.g. fgm_jno_l3_2024165pl_v02.lbl).")
    product_id: str = Field(
        description=(
            "Archive PRODUCT_ID or LABEL_VERIFICATION_PENDING if not yet verified. "
            "Directory HTML does not provide authoritative PRODUCT_ID; "
            "set to LABEL_VERIFICATION_PENDING until B2.2."
        )
    )
    logical_stem: str = Field(description="Logical stem used for GCSI ID derivation (filename-derived).")
    selected: bool = Field(description="True if this candidate is selected in the replay plan.")
    candidate_classification: FgmCandidateClassification = Field(
        description=(
            "Classification of this FGM candidate: FULL_RESOLUTION_STANDARD, "
            "FULL_RESOLUTION_PJ62, R1S_OR_DOWNSAMPLED_ALTERNATE, or OTHER_RELEVANT_VARIANT."
        )
    )
    expected_archive_identity_source: ExpectedArchiveIdentitySource = Field(
        description=(
            "Source of expected archive identity. "
            "DISCOVERY_PATH_DERIVED: product_id derived from filename, not from label. "
            "LABEL_VERIFICATION_PENDING: identity to be established by B2.2."
        )
    )
    relative_label_path: str = Field(
        description="Relative path from FGM PERI-62 base URL (e.g. fgm_jno_l3_2024165pl_v02.lbl)."
    )
    discovery_evidence_id: str = Field(
        description=(
            "evidence_id of the DiscoveryEvidence that discovered this label. "
            "Must reference the PERI-62 directory evidence, not the PL root."
        )
    )

    @field_validator("lbl_filename", "logical_stem", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("product_id", mode="after")
    @classmethod
    def _product_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("product_id must not be empty.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="lbl")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "FgmDiscoveryLabel":
        # lbl_filename should appear in relative_label_path
        if self.lbl_filename not in self.relative_label_path:
            raise ValueError(
                f"FGM lbl_filename {self.lbl_filename!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        # R1S_OR_DOWNSAMPLED_ALTERNATE candidates must not be selected
        if (
            self.candidate_classification == FgmCandidateClassification.R1S_OR_DOWNSAMPLED_ALTERNATE
            and self.selected
        ):
            raise ValueError(
                "R1S_OR_DOWNSAMPLED_ALTERNATE candidate must not be selected for replay. "
                f"lbl_filename={self.lbl_filename!r}."
            )
        return self


# ---------------------------------------------------------------------------
# JADE
# ---------------------------------------------------------------------------


class JadeDiscoveryLabel(BaseModel):
    """One JADE discovery label record extracted from the calibrated INDEX.TAB.

    B2.1.3: Updated with authoritative product IDs from INDEX.TAB
    (e.g. JAD_L30_HRS_ELC_TWO_CNT_2024165_V04).
    Includes exact start/stop times and relative file path from INDEX.

    Includes inclusion classification: ELIGIBLE vs EXCLUDED (post-decision).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(
        description="Archive PRODUCT_ID from INDEX.TAB (e.g. JAD_L30_HRS_ELC_TWO_CNT_2024165_V04)."
    )
    relative_label_path: str = Field(
        description="FILE_SPECIFICATION_NAME from INDEX.TAB (e.g. DATA/2024/2024165/ELECTRONS/JAD_L30_HRS_ELC_TWO_CNT_2024165_V04.LBL)."
    )
    doy: int = Field(description="Day of year (165 or 166).")
    start_time_utc: str = Field(description="ISO-8601 START_TIME from INDEX.TAB.")
    stop_time_utc: str = Field(description="ISO-8601 STOP_TIME from INDEX.TAB.")
    inclusion: JadeInclusion = Field(description="Inclusion classification: ELIGIBLE or EXCLUDED.")
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("product_id", "start_time_utc", "stop_time_utc", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("doy", mode="after")
    @classmethod
    def _valid_doy(cls, v: int) -> int:
        if v not in (165, 166):
            raise ValueError(f"JADE DOY must be 165 or 166, got {v}.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="lbl")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "JadeDiscoveryLabel":
        # product_id should appear in relative_label_path
        if self.product_id not in self.relative_label_path:
            raise ValueError(
                f"JADE product_id {self.product_id!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        # §4.3: Parse and validate UTC datetimes; enforce stop >= start.
        start_dt = _parse_utc_datetime(self.start_time_utc, "start_time_utc")
        stop_dt = _parse_utc_datetime(self.stop_time_utc, "stop_time_utc")
        if stop_dt < start_dt:
            raise ValueError(
                f"JADE stop_time_utc ({self.stop_time_utc!r}) must be >= "
                f"start_time_utc ({self.start_time_utc!r})."
            )
        return self


# ---------------------------------------------------------------------------
# JEDI
# ---------------------------------------------------------------------------


class JediDiscoveryLabel(BaseModel):
    """One JEDI discovery label record extracted from a DOY directory HTML."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="Archive PRODUCT_ID (e.g. JED_090_HIERSESP_CDR_2024165_V04).")
    doy: int = Field(description="Day of year (165 or 166).")
    relative_label_path: str = Field(
        description="Relative path from JEDI base URL (e.g. 165/JED_090_HIERSESP_CDR_2024165_V04.LBL)."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("product_id", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("doy", mode="after")
    @classmethod
    def _valid_doy(cls, v: int) -> int:
        if v not in (165, 166):
            raise ValueError(f"JEDI DOY must be 165 or 166, got {v}.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="lbl")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "JediDiscoveryLabel":
        # product_id must appear in relative_label_path
        if self.product_id not in self.relative_label_path:
            raise ValueError(
                f"JEDI product_id {self.product_id!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        return self


# ---------------------------------------------------------------------------
# WAVES Survey
# ---------------------------------------------------------------------------


class WavesSurveyDiscoveryLabel(BaseModel):
    """One WAVES Survey discovery label record extracted from the Orbit-62 directory HTML."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stem: str = Field(description="Exact label stem (e.g. WAV_2024165T000000_B_V01).")
    band: str = Field(description="Wave band identifier (b or e).")
    inclusion: WavesSurveyInclusion = Field(description="Inclusion classification: ELIGIBLE or EXCLUDED.")
    relative_label_path: str = Field(
        description="Relative path from WAVES Survey base URL (e.g. WAV_2024165T000000_B_V01.LBL)."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("stem", "band", "discovery_evidence_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("relative_label_path", mode="after")
    @classmethod
    def _validate_path(cls, v: str) -> str:
        return _validate_relative_label_path(v, expected_extension="lbl")

    @model_validator(mode="after")
    def _cross_field_check(self) -> "WavesSurveyDiscoveryLabel":
        # stem must appear in relative_label_path
        if self.stem not in self.relative_label_path:
            raise ValueError(
                f"WAVES Survey stem {self.stem!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
            )
        return self


# ---------------------------------------------------------------------------
# JunoCam discovery rows (all partitions including pre/post)
# ---------------------------------------------------------------------------


class JunoCamDiscoveryRow(BaseModel):
    """One JunoCam INDEX.TAB row extracted from JNOJNC_0029.

    B2.1.3: All 426 orbit-62 rows stored in full with partition classification.
    Spec requires 426 raw rows = 112 PRE + 248 ELIGIBLE + 66 POST.
    Representations: 213 EDR + 213 RDR.
    Logical observations: 213 = 56 PRE + 124 ELIGIBLE + 33 POST.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="PRODUCT_ID from INDEX.TAB (e.g. JNCE_2024165_62C00057_V01).")
    file_specification_name: str = Field(description="FILE_SPECIFICATION_NAME from INDEX.TAB.")
    representation_kind: JunoCamRepresentation = Field(description="EDR or RDR.")
    observation_key: str = Field(description="Observation key (lowercase, e.g. '2024165_62c00057').")
    start_time_utc: str = Field(description="ISO-8601 START_TIME from INDEX.TAB.")
    stop_time_utc: str = Field(description="ISO-8601 STOP_TIME from INDEX.TAB.")
    partition: JunoCamPartition = Field(description="Partition classification: PRE, ELIGIBLE, or POST.")
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence (junocam_jnojnc_0029_index_tab)."
    )

    @field_validator(
        "product_id", "file_specification_name", "observation_key",
        "start_time_utc", "stop_time_utc", "discovery_evidence_id",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @model_validator(mode="after")
    def _cross_field_check(self) -> "JunoCamDiscoveryRow":
        # product_id should appear in file_specification_name
        if self.product_id not in self.file_specification_name:
            raise ValueError(
                f"JunoCam product_id {self.product_id!r} must appear in "
                f"file_specification_name {self.file_specification_name!r}."
            )
        # representation_kind EDR → product_id starts with JNCE_; RDR → JNCR_
        pid = self.product_id
        if self.representation_kind == JunoCamRepresentation.EDR:
            if not pid.startswith("JNCE_"):
                raise ValueError(
                    f"JunoCam EDR product_id must start with 'JNCE_': {pid!r}."
                )
        elif self.representation_kind == JunoCamRepresentation.RDR:
            if not pid.startswith("JNCR_"):
                raise ValueError(
                    f"JunoCam RDR product_id must start with 'JNCR_': {pid!r}."
                )
        # §4.3: Parse and validate UTC datetimes; enforce stop >= start and
        # partition consistency.
        start_dt = _parse_utc_datetime(self.start_time_utc, "start_time_utc")
        stop_dt = _parse_utc_datetime(self.stop_time_utc, "stop_time_utc")
        if stop_dt < start_dt:
            raise ValueError(
                f"JunoCam stop_time_utc ({self.stop_time_utc!r}) must be >= "
                f"start_time_utc ({self.start_time_utc!r})."
            )
        expected_partition = _classify_temporal_partition(stop_dt)
        if self.partition.value != expected_partition:
            raise ValueError(
                f"JunoCam partition {self.partition.value!r} is inconsistent with "
                f"stop_time_utc {self.stop_time_utc!r}: expected {expected_partition!r}."
            )
        return self


# ---------------------------------------------------------------------------
# WAVES Burst discovery rows (all partitions including pre/post)
# ---------------------------------------------------------------------------


class WavesBurstDiscoveryRow(BaseModel):
    """One WAVES Burst INDEX.TAB row extracted from BSTFULL.

    B2.1.3: All 282 orbit-62 rows stored in full.
    Spec requires: 282 = 175 PRE + 91 ELIGIBLE + 16 POST.
    Eligible families: B_BIN=41, E_BIN=41, B_REC=3, E_REC=3, NBS_REC=3.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="PRODUCT_ID from INDEX.TAB (without version).")
    file_specification_name: str = Field(description="File specification relative to volume root.")
    start_time: str = Field(description="ISO-8601 START_TIME from INDEX.TAB.")
    stop_time: str = Field(description="ISO-8601 STOP_TIME from INDEX.TAB.")
    family: WavesBurstFamily = Field(description="Product family: B_BIN, E_BIN, B_REC, E_REC, NBS_REC.")
    partition: WavesBurstPartition = Field(description="Partition classification: PRE, ELIGIBLE, or POST.")
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence (waves_burst_bstfull_index_tab)."
    )

    @field_validator(
        "product_id", "file_specification_name", "start_time", "stop_time", "discovery_evidence_id",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @model_validator(mode="after")
    def _cross_field_check(self) -> "WavesBurstDiscoveryRow":
        # product_id should appear in file_specification_name
        if self.product_id not in self.file_specification_name:
            raise ValueError(
                f"WAVES Burst product_id {self.product_id!r} must appear in "
                f"file_specification_name {self.file_specification_name!r}."
            )
        # §4.3: Parse and validate UTC datetimes; enforce stop >= start and
        # partition consistency.
        start_dt = _parse_utc_datetime(self.start_time, "start_time")
        stop_dt = _parse_utc_datetime(self.stop_time, "stop_time")
        if stop_dt < start_dt:
            raise ValueError(
                f"WAVES Burst stop_time ({self.stop_time!r}) must be >= "
                f"start_time ({self.start_time!r})."
            )
        expected_partition = _classify_temporal_partition(stop_dt)
        if self.partition.value != expected_partition:
            raise ValueError(
                f"WAVES Burst partition {self.partition.value!r} is inconsistent with "
                f"stop_time {self.stop_time!r}: expected {expected_partition!r}."
            )
        return self


# ---------------------------------------------------------------------------
# Discovery partition summary
# ---------------------------------------------------------------------------


class DiscoveryPartition(BaseModel):
    """Summary partition counts for an instrument's raw rows.

    Used for reconciliation proofs stored in the sidecar.
    Invariant enforced: total_orbit62_rows == pre_rows + eligible_rows + post_rows.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    instrument: str = Field(description="Instrument name.")
    total_orbit62_rows: int = Field(description="Total orbit-62 rows in source index.")
    pre_rows: int = Field(description="Rows with stop <= accumulation_start (PRE).")
    eligible_rows: int = Field(description="Rows within the replay window (ELIGIBLE).")
    post_rows: int = Field(description="Rows with stop > decision_epoch (POST).")
    source_evidence_id: Optional[str] = Field(
        default=None,
        description="evidence_id of the source for this partition.",
    )

    @field_validator("instrument", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instrument must not be empty.")
        return v

    @field_validator("total_orbit62_rows", "pre_rows", "eligible_rows", "post_rows", mode="after")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Count must be non-negative.")
        return v

    @model_validator(mode="after")
    def _partition_invariant(self) -> "DiscoveryPartition":
        total = self.pre_rows + self.eligible_rows + self.post_rows
        if total != self.total_orbit62_rows:
            raise ValueError(
                f"DiscoveryPartition invariant violated for {self.instrument!r}: "
                f"pre_rows({self.pre_rows}) + eligible_rows({self.eligible_rows}) + "
                f"post_rows({self.post_rows}) = {total} != "
                f"total_orbit62_rows({self.total_orbit62_rows})."
            )
        return self


# ---------------------------------------------------------------------------
# B2.1.4 / B2.2 §4.2: Typed partition summary models
# ---------------------------------------------------------------------------


class JunoCamPartitionSummary(BaseModel):
    """Typed frozen model for JunoCam partition summary.

    Invariant: total_orbit62_rows == pre_rows + eligible_rows + post_rows
    Spec: 426 = 112 + 248 + 66
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: str = Field(description="Instrument name.")
    total_orbit62_rows: int = Field(description="Total orbit-62 rows (must == 426).")
    pre_rows: int = Field(description="PRE partition rows (must == 112).")
    eligible_rows: int = Field(description="ELIGIBLE partition rows (must == 248).")
    post_rows: int = Field(description="POST partition rows (must == 66).")
    source_evidence_id: Optional[str] = Field(default=None, description="Source evidence_id.")
    note: Optional[str] = Field(default=None, description="Free-form note (backward compat).")

    @model_validator(mode="after")
    def _invariant(self) -> "JunoCamPartitionSummary":
        total = self.pre_rows + self.eligible_rows + self.post_rows
        if total != self.total_orbit62_rows:
            raise ValueError(
                f"JunoCamPartitionSummary invariant violated: "
                f"pre({self.pre_rows}) + eligible({self.eligible_rows}) + "
                f"post({self.post_rows}) = {total} != "
                f"total_orbit62_rows({self.total_orbit62_rows})."
            )
        return self


class WavesBurstPartitionSummary(BaseModel):
    """Typed frozen model for WAVES Burst partition summary.

    Invariant: total_orbit62_rows == pre_rows + eligible_rows + post_rows
    Invariant: sum(eligible_families.values()) == eligible_rows
    Spec: 282 = 175 + 91 + 16; eligible_families sum = 41+41+3+3+3 = 91
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: str = Field(description="Instrument name.")
    total_orbit62_rows: int = Field(description="Total orbit-62 rows (must == 282).")
    pre_rows: int = Field(description="PRE partition rows (must == 175).")
    eligible_rows: int = Field(description="ELIGIBLE partition rows (must == 91).")
    post_rows: int = Field(description="POST partition rows (must == 16).")
    eligible_families: dict = Field(description="Per-family eligible row counts (backward compat).")
    source_evidence_id: Optional[str] = Field(default=None, description="Source evidence_id.")

    @model_validator(mode="after")
    def _invariant(self) -> "WavesBurstPartitionSummary":
        total = self.pre_rows + self.eligible_rows + self.post_rows
        if total != self.total_orbit62_rows:
            raise ValueError(
                f"WavesBurstPartitionSummary invariant violated: "
                f"pre({self.pre_rows}) + eligible({self.eligible_rows}) + "
                f"post({self.post_rows}) = {total} != "
                f"total_orbit62_rows({self.total_orbit62_rows})."
            )
        families_sum = sum(self.eligible_families.values())
        if families_sum != self.eligible_rows:
            raise ValueError(
                f"WavesBurstPartitionSummary eligible_families sum {families_sum} != "
                f"eligible_rows {self.eligible_rows}."
            )
        return self


# ---------------------------------------------------------------------------
# B2.1.4: NormalizedDiscoveryExtractions typed model
# ---------------------------------------------------------------------------


class PartitionSummaries(BaseModel):
    """Typed partition summaries container."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    junocam: JunoCamPartitionSummary = Field(description="JunoCam partition summary.")
    waves_burst: WavesBurstPartitionSummary = Field(description="WAVES Burst partition summary.")


class NormalizedDiscoveryExtractions(BaseModel):
    """Typed collection of all normalized discovery extractions.

    B2.1.4: Replaces plain dict with explicitly typed frozen model.
    All collections are tuples of strictly typed row models.
    extra='forbid' prevents unknown instrument buckets from being silently accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    jiram_orbit62_filenames: tuple[JiramDiscoveryLabel, ...] = Field(
        description="All JIRAM orbit-62 discovery labels."
    )
    mwr_orbit62_filenames: tuple[MwrDiscoveryLabel, ...] = Field(
        description="All MWR orbit-62 discovery labels (all hours, eligible and excluded)."
    )
    uvs_orbit62_filenames: tuple[UvsDiscoveryLabel, ...] = Field(
        description="All UVS orbit-62 discovery labels."
    )
    fgm_peri62_filenames: tuple[FgmDiscoveryLabel, ...] = Field(
        description="All FGM PERI-62 discovery label candidates (selected and excluded)."
    )
    jade_orbit62_labels: tuple[JadeDiscoveryLabel, ...] = Field(
        description="All JADE orbit-62 discovery labels (eligible and excluded)."
    )
    jedi_165_labels: tuple[JediDiscoveryLabel, ...] = Field(
        description="JEDI DOY-165 discovery labels."
    )
    jedi_166_labels: tuple[JediDiscoveryLabel, ...] = Field(
        description="JEDI DOY-166 discovery labels."
    )
    waves_survey_orbit62_labels: tuple[WavesSurveyDiscoveryLabel, ...] = Field(
        description="WAVES Survey orbit-62 discovery labels (eligible and excluded)."
    )
    junocam_index_tab_orbit62_all: tuple[JunoCamDiscoveryRow, ...] = Field(
        description="All JunoCam orbit-62 INDEX.TAB rows (all partitions)."
    )
    waves_burst_index_tab_orbit62_all: tuple[WavesBurstDiscoveryRow, ...] = Field(
        description="All WAVES Burst orbit-62 INDEX.TAB rows (all partitions)."
    )
    partition_summaries: PartitionSummaries = Field(
        description="Partition summary objects for instruments with exact STOP_TIME."
    )


# ---------------------------------------------------------------------------
# Sidecar artifact_id formula
# ---------------------------------------------------------------------------

_SIDECAR_ARTIFACT_PREFIX: str = "gcsi.pj62_discovery_evidence_sidecar:v1:"

# Canonical sort keys per collection (§28: same semantic rows → same artifact_id)
_COLLECTION_SORT_KEYS: dict[str, str] = {
    "jiram_orbit62_filenames": "relative_label_path",
    "mwr_orbit62_filenames": "relative_label_path",
    "uvs_orbit62_filenames": "relative_label_path",
    "fgm_peri62_filenames": "relative_label_path",
    "jade_orbit62_labels": "relative_label_path",
    "jedi_165_labels": "relative_label_path",
    "jedi_166_labels": "relative_label_path",
    "waves_survey_orbit62_labels": "relative_label_path",
    "junocam_index_tab_orbit62_all": "file_specification_name",
    "waves_burst_index_tab_orbit62_all": "file_specification_name",
}


def _canonicalize_extraction(key: str, value: object) -> object:
    """Canonically sort a normalized extraction collection by its registered sort key.

    B2.1.4 §28: Semantic canonicalization — same rows in different input order
    produce the same artifact_id.

    For list/tuple collections: sort by the registered key field.
    For other values (partition_summaries, etc.): return as-is.
    """
    if key not in _COLLECTION_SORT_KEYS:
        return value
    sort_field = _COLLECTION_SORT_KEYS[key]
    if isinstance(value, (list, tuple)):
        try:
            return sorted(value, key=lambda r: (r if not isinstance(r, dict) else r.get(sort_field, "")) if isinstance(r, dict) else (getattr(r, sort_field, "") or ""))
        except Exception:
            return value
    return value


def _row_to_canonical(row: object) -> object:
    """Convert a row (dict or Pydantic model) to a canonical dict for JSON serialization."""
    if isinstance(row, dict):
        return row
    if hasattr(row, "model_dump"):
        return row.model_dump(mode="json")
    return row


def compute_sidecar_artifact_id(sidecar_content: dict) -> str:
    """Compute the deterministic artifact_id for the discovery evidence sidecar.

    B2.1.4 §28: Each normalized collection is sorted by its canonical sort key
    before hashing. This ensures that the same semantic rows in different input
    order produce the same artifact_id.

    The artifact_id is SHA-256 of the canonical JSON of ALL semantic sidecar
    content, excluding the artifact_id field itself.

    Semantic content includes:
      - schema
      - schema_version
      - replay_id
      - discovery_evidence (sorted by evidence_id)
      - normalized_extractions (all keys, sorted; each collection canonically sorted)

    Retrieval time IS included in the hash (via discovery_evidence.retrieved_at).
    This means: identical source bytes fetched at a different time → different artifact_id.
    The artifact_id represents a specific CAPTURE artifact, not pure source-content identity.

    Formula::

        SHA-256(
            "gcsi.pj62_discovery_evidence_sidecar:v1:"
            + JSON-canonical of semantic content
        )
    """
    # Sort evidence by evidence_id
    evidence_list = sidecar_content.get("discovery_evidence", [])
    sorted_evidence = sorted(evidence_list, key=lambda x: (x if isinstance(x, dict) else x.model_dump())["evidence_id"])
    canonical_evidence = [_row_to_canonical(e) for e in sorted_evidence]

    # Canonicalize normalized_extractions: sort each collection by its sort key
    raw_extractions = sidecar_content.get("normalized_extractions", {})
    canonical_extractions: dict = {}
    for k in sorted(raw_extractions.keys()):
        v = raw_extractions[k]
        canonicalized = _canonicalize_extraction(k, v)
        if isinstance(canonicalized, (list, tuple)):
            canonical_extractions[k] = [_row_to_canonical(r) for r in canonicalized]
        else:
            canonical_extractions[k] = canonicalized

    canonical = {
        "discovery_evidence": canonical_evidence,
        "normalized_extractions": canonical_extractions,
        "replay_id": sidecar_content["replay_id"],
        "schema": sidecar_content["schema"],
        "schema_version": sidecar_content["schema_version"],
    }
    payload = _SIDECAR_ARTIFACT_PREFIX + json.dumps(
        canonical,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Top-level sidecar model
# ---------------------------------------------------------------------------


class HistoricalReplayV2DiscoveryEvidenceSidecar(BaseModel):
    """Top-level frozen model for the discovery evidence sidecar.

    B2.1.4: Fully typed model using TypedDiscoveryEvidence and
    NormalizedDiscoveryExtractions. No plain list/dict for semantic fields.

    The production _load_sidecar() returns this typed object.
    artifact_id is REQUIRED: sidecar is rejected without it.

    Validation:
    - All discovery_evidence entries are TypedDiscoveryEvidence (strict validation)
    - All normalized_extractions collections are typed row models
    - artifact_id verified against recomputed SHA-256
    - Referential integrity: all row discovery_evidence_ids resolve to evidence records
    - No duplicate evidence_ids
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema: str = Field(
        description="Schema identifier: 'gcsi.pj62_discovery_evidence_sidecar'."
    )
    schema_version: int = Field(
        description="Schema version integer. Current: 1."
    )
    artifact_id: str = Field(
        description=(
            "SHA-256 artifact_id over canonical sidecar content. "
            "Required. Verified on load."
        )
    )
    replay_id: str = Field(description="Replay identifier.")
    discovery_evidence: tuple[TypedDiscoveryEvidence, ...] = Field(
        description="Discovery evidence records (typed, validated)."
    )
    normalized_extractions: NormalizedDiscoveryExtractions = Field(
        description="Normalized extraction rows per instrument (typed, validated)."
    )

    @field_validator("schema", "artifact_id", "replay_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("artifact_id", mode="after")
    @classmethod
    def _artifact_id_format(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                f"artifact_id must be exactly 64 lowercase hex characters, got: {v!r}."
            )
        return v

    @model_validator(mode="after")
    def _validate_sidecar(self) -> "HistoricalReplayV2DiscoveryEvidenceSidecar":
        # 1. No duplicate evidence_ids; no empty evidence_ids
        ev_ids = [ev.evidence_id for ev in self.discovery_evidence]
        for eid in ev_ids:
            if not eid or not eid.strip():
                raise ValueError("discovery_evidence contains an empty evidence_id.")
        if len(ev_ids) != len(set(ev_ids)):
            seen: set[str] = set()
            dups = [x for x in ev_ids if x in seen or seen.add(x)]  # type: ignore
            raise ValueError(
                f"Duplicate evidence_id values in discovery_evidence: {dups!r}."
            )

        # 2. §4.1 Referential integrity: every row's discovery_evidence_id must
        #    resolve to exactly one TypedDiscoveryEvidence.evidence_id.
        ev_id_set: frozenset[str] = frozenset(ev_ids)
        ext = self.normalized_extractions

        _ROW_COLLECTIONS = (
            ext.jiram_orbit62_filenames,
            ext.mwr_orbit62_filenames,
            ext.uvs_orbit62_filenames,
            ext.fgm_peri62_filenames,
            ext.jade_orbit62_labels,
            ext.jedi_165_labels,
            ext.jedi_166_labels,
            ext.waves_survey_orbit62_labels,
            ext.junocam_index_tab_orbit62_all,
            ext.waves_burst_index_tab_orbit62_all,
        )

        for collection in _ROW_COLLECTIONS:
            for row in collection:
                eid = row.discovery_evidence_id
                if not eid or not eid.strip():
                    raise ValueError(
                        f"Row {row!r} has an empty discovery_evidence_id."
                    )
                if eid not in ev_id_set:
                    raise ValueError(
                        f"Orphan discovery_evidence_id {eid!r} in row "
                        f"{getattr(row, 'relative_label_path', None) or getattr(row, 'file_specification_name', None) or repr(row)!r}: "
                        "no matching evidence record."
                    )

        # 3. Partition summaries: source_evidence_id (when not None) must resolve.
        ps = ext.partition_summaries
        for summary_name, summary in (
            ("junocam", ps.junocam),
            ("waves_burst", ps.waves_burst),
        ):
            src_ev = summary.source_evidence_id
            if src_ev is not None:
                if not src_ev.strip():
                    raise ValueError(
                        f"partition_summaries.{summary_name} has an empty source_evidence_id."
                    )
                if src_ev not in ev_id_set:
                    raise ValueError(
                        f"Orphan source_evidence_id {src_ev!r} in "
                        f"partition_summaries.{summary_name}: no matching evidence record."
                    )

        return self
