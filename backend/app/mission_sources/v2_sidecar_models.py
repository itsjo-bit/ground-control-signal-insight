"""GCSI Phase 6F-B2.1.2 — Strict Frozen Models for Discovery Sidecar Extractions.

All models use:
  - frozen=True
  - extra="forbid"
  - non-empty identifier validation
  - valid classification enums
  - no duplicate source rows enforced at collection level

These models are the authoritative types for normalized_extractions in the
discovery evidence sidecar.  The builder MUST consume them; it MUST NOT
maintain parallel hard-coded NASA identity arrays.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("filename", "hhmmss", mode="after")
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


# ---------------------------------------------------------------------------
# MWR
# ---------------------------------------------------------------------------


class MwrDiscoveryLabel(BaseModel):
    """One MWR discovery label record extracted from a directory HTML.

    Represents a single XML product label filename with type/DOY/hour classification.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    filename: str = Field(description="Exact XML filename stem (e.g. MWR62RI20241651000000_R04120_V04).")
    product_type: MwrProductType = Field(description="Product type: IRDR or GRDR.")
    doy: int = Field(description="Day of year (165 or 166).")
    hour: int = Field(description="UTC hour of product (0-23).")
    code: str = Field(description="Archive rate-code suffix (e.g. R04120).")

    @field_validator("filename", "code", mode="after")
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

    @field_validator("filename", "sensor", "sclk", "doy_str", "obs_type", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


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

    @field_validator("lbl_filename", "product_id", "logical_stem", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# JADE
# ---------------------------------------------------------------------------


class JadeDiscoveryLabel(BaseModel):
    """One JADE discovery label record extracted from the calibrated directory HTML.

    Includes inclusion classification to distinguish eligible vs. excluded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="Archive PRODUCT_ID (e.g. JAD_L30_LRS_ION_2024165_V01).")
    path_suffix: str = Field(description="Path suffix relative to base URL (e.g. 2024/165/JAD_L30_LRS_ION_2024165_V01.LBL).")
    doy: int = Field(description="Day of year (165 or 166).")
    inclusion: JadeInclusion = Field(description="Inclusion classification: ELIGIBLE or EXCLUDED.")

    @field_validator("product_id", "path_suffix", mode="after")
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


# ---------------------------------------------------------------------------
# JEDI
# ---------------------------------------------------------------------------


class JediDiscoveryLabel(BaseModel):
    """One JEDI discovery label record extracted from a DOY directory HTML."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="Archive PRODUCT_ID (e.g. JED_090_HIERSESP_CDR_2024165_V04).")
    doy: int = Field(description="Day of year (165 or 166).")

    @field_validator("product_id", mode="after")
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


# ---------------------------------------------------------------------------
# WAVES Survey
# ---------------------------------------------------------------------------


class WavesSurveyDiscoveryLabel(BaseModel):
    """One WAVES Survey discovery label record extracted from the Orbit-62 directory HTML."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stem: str = Field(description="Exact label stem (e.g. WAV_2024165T000000_B_V01).")
    band: str = Field(description="Wave band identifier (b or e).")
    inclusion: WavesSurveyInclusion = Field(description="Inclusion classification: ELIGIBLE or EXCLUDED.")

    @field_validator("stem", "band", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# JunoCam discovery rows (all partitions including pre/post)
# ---------------------------------------------------------------------------


class JunoCamDiscoveryRow(BaseModel):
    """One JunoCam INDEX.TAB row extracted from JNOJNC_0029.

    All 426 orbit-62 rows are stored in three partitions:
      PRE (112), ELIGIBLE (248 = 124 EDR+RDR logical pairs), POST (66).
    
    Note: 248 eligible *representation* rows correspond to 124 logical
    observations (each observation has one EDR row and one RDR row).
    This model stores logical observation records (one per observation),
    which each carry both EDR and RDR file spec names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    edr_product_id: str = Field(description="EDR PRODUCT_ID from INDEX.TAB.")
    edr_file_specification_name: str = Field(description="EDR file specification relative to volume root.")
    rdr_product_id: str = Field(description="RDR PRODUCT_ID from INDEX.TAB.")
    rdr_file_specification_name: str = Field(description="RDR file specification relative to volume root.")
    obs_key: str = Field(description="Observation key (lowercase, e.g. '2024165_62c00057').")
    start_time_utc: str = Field(description="ISO-8601 START_TIME from INDEX.TAB (no tz suffix).")
    stop_time_utc: str = Field(description="ISO-8601 STOP_TIME from INDEX.TAB (no tz suffix).")
    partition: JunoCamPartition = Field(description="Partition classification: PRE, ELIGIBLE, or POST.")

    @field_validator(
        "edr_product_id", "edr_file_specification_name",
        "rdr_product_id", "rdr_file_specification_name",
        "obs_key", "start_time_utc", "stop_time_utc",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# WAVES Burst discovery rows (all partitions including pre/post)
# ---------------------------------------------------------------------------


class WavesBurstDiscoveryRow(BaseModel):
    """One WAVES Burst INDEX.TAB row extracted from BSTFULL.

    All 282 orbit-62 rows are stored in three partitions:
      PRE (175), ELIGIBLE (91), POST (16).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    product_id: str = Field(description="PRODUCT_ID from INDEX.TAB (without version).")
    file_specification_name: str = Field(description="File specification relative to volume root.")
    start_time: str = Field(description="ISO-8601 START_TIME from INDEX.TAB.")
    stop_time: str = Field(description="ISO-8601 STOP_TIME from INDEX.TAB.")
    family: WavesBurstFamily = Field(description="Product family: B_BIN, E_BIN, B_REC, E_REC, NBS_REC.")
    partition: WavesBurstPartition = Field(description="Partition classification: PRE, ELIGIBLE, or POST.")

    @field_validator(
        "product_id", "file_specification_name", "start_time", "stop_time",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# Discovery partition summary
# ---------------------------------------------------------------------------


class DiscoveryPartition(BaseModel):
    """Summary partition counts for an instrument's raw rows.

    Used for reconciliation proofs stored in the sidecar.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    instrument: str = Field(description="Instrument name.")
    total_orbit62_rows: int = Field(description="Total orbit-62 rows in source index.")
    pre_rows: int = Field(description="Rows with stop <= accumulation_start (PRE).")
    eligible_rows: int = Field(description="Rows within the replay window (ELIGIBLE).")
    post_rows: int = Field(description="Rows with stop > decision_epoch (POST).")

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
