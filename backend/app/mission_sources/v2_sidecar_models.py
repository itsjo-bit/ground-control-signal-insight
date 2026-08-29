"""GCSI Phase 6F-B2.1.3 — Strict Frozen Models for Discovery Sidecar Extractions.

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

All models: frozen=True, extra="forbid"
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    UNAVAILABLE_UNTIL_LABEL = "UNAVAILABLE_UNTIL_LABEL"


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


class MwrInclusion(str, Enum):
    """MWR product inclusion classification (temporal window eligibility)."""
    ELIGIBLE = "ELIGIBLE"
    EXCLUDED = "EXCLUDED"


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

    Includes inclusion/exclusion classification to distinguish selected
    vs. variant/excluded candidates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lbl_filename: str = Field(description="Exact .lbl filename (e.g. fgm_jno_l3_2024165pl_v02.lbl).")
    product_id: str = Field(description="Archive PRODUCT_ID (e.g. FGM_JNO_L3_2024165PL).")
    logical_stem: str = Field(description="Logical stem used for GCSI ID derivation.")
    selected: bool = Field(description="True if this candidate is selected in the plan.")
    relative_label_path: str = Field(
        description="Relative path from FGM base URL (e.g. fgm_jno_l3_2024165pl_v02.lbl)."
    )
    discovery_evidence_id: str = Field(
        description="evidence_id of the DiscoveryEvidence that discovered this label."
    )

    @field_validator("lbl_filename", "product_id", "logical_stem", "discovery_evidence_id", mode="after")
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
    def _cross_field_check(self) -> "FgmDiscoveryLabel":
        # lbl_filename should appear in relative_label_path
        if self.lbl_filename not in self.relative_label_path:
            raise ValueError(
                f"FGM lbl_filename {self.lbl_filename!r} must appear in "
                f"relative_label_path {self.relative_label_path!r}."
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
# Sidecar artifact_id formula
# ---------------------------------------------------------------------------

_SIDECAR_ARTIFACT_PREFIX: str = "gcsi.pj62_discovery_evidence_sidecar:v1:"


def compute_sidecar_artifact_id(sidecar_content: dict) -> str:
    """Compute the deterministic artifact_id for the discovery evidence sidecar.

    The artifact_id is SHA-256 of the canonical JSON of ALL semantic sidecar
    content, excluding the artifact_id field itself.

    Semantic content includes:
      - schema
      - schema_version
      - replay_id
      - discovery_evidence (sorted by evidence_id)
      - normalized_extractions (all keys, sorted)

    Formula::

        SHA-256(
            "gcsi.pj62_discovery_evidence_sidecar:v1:"
            + JSON-canonical of semantic content
        )
    """
    # Build canonical representation excluding artifact_id
    canonical = {
        "discovery_evidence": sorted(
            sidecar_content["discovery_evidence"],
            key=lambda x: x["evidence_id"],
        ),
        "normalized_extractions": {
            k: sidecar_content["normalized_extractions"][k]
            for k in sorted(sidecar_content["normalized_extractions"].keys())
        },
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

    B2.1.3: Typed, frozen, extra="forbid" top-level model.

    The production _load_sidecar() returns this typed object.
    artifact_id is REQUIRED: sidecar is rejected without it.
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
    discovery_evidence: list = Field(description="Discovery evidence records.")
    normalized_extractions: dict = Field(description="Normalized extraction rows per instrument.")

    @field_validator("schema", "artifact_id", "replay_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("artifact_id", mode="after")
    @classmethod
    def _artifact_id_format(cls, v: str) -> str:
        import re as _re
        if not _re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                f"artifact_id must be exactly 64 lowercase hex characters, got: {v!r}."
            )
        return v
