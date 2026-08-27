"""GCSI Phase 6E-A — NASA PDS Science Product Domain Models.

These strict Pydantic models represent the domain objects produced by and
consumed by the :class:`~backend.app.mission_sources.adapters.pds.PdsRegistryAdapter`.

They are completely independent of:
- GCSI runtime state (state.py)
- Scenario / ScenarioLoader
- DataProduct
- TelecomEngine, RF, BER, SNR, link margin
- API routes
- Frontend

Models
------
PdsProductRequest
    Input contract for one exact LIDVID metadata fetch.
    Accepts only a fully-versioned LIDVID (urn:nasa:pds:<lid>::<version>).
    Rejects bare LIDs, whitespace, path tricks, and injection patterns.

PdsDataFile
    Normalized representation of one data file associated with a PDS
    observational product.  Derived from ops:Data_File_Info fields only.
    Does NOT include label-file metadata.

PdsScienceProduct
    Normalized external fact object produced from a validated PDS Search API
    response.  This is NOT a GCSI DataProduct — it is a lower-level
    external metadata fact.

Notes
-----
- These models are frozen and forbid extra fields.
- PdsScienceProduct.total_data_size_bytes is the deterministic sum of all
  data-file sizes, derived from ops:Data_File_Info only (not label size).
- The PDS Search API may not contain every PDS product.  Absence from the
  Search API does NOT mean the product does not exist in the PDS archive.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Expose prefix/regex constants so PdsScienceProduct._validate_model can
# reference them without duplication.


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Conservative safe-subset LIDVID pattern for Phase 6E-A.
#
# Rules (all must be satisfied):
#   1. Begins with  urn:nasa:pds:
#   2. LID portion  contains only: letters, digits, colon, underscore, hyphen,
#      dot.  No slash, backslash, question mark, percent, hash.
#   3. Contains an explicit ::version suffix (at least one char after ::)
#   4. No whitespace, no control characters.
#   5. No URL scheme embedded inside (no :// inside LIDVID after the urn: prefix).
#
# Deliberately conservative — does not attempt to encode the full PDS4 LID
# grammar.
_LIDVID_PREFIX = "urn:nasa:pds:"
_LIDVID_RE = re.compile(
    r"^urn:nasa:pds:[a-zA-Z0-9:._-]+::[a-zA-Z0-9._-]+$"
)

# MD5 pattern: exactly 32 hex characters (case-insensitive input, normalized
# to lowercase).
_MD5_RE = re.compile(r"^[0-9a-f]{32}$")

# Authoritative product class for science/observational products.
_PRODUCT_OBSERVATIONAL = "Product_Observational"


# ---------------------------------------------------------------------------
# A. PdsProductRequest
# ---------------------------------------------------------------------------


class PdsProductRequest(BaseModel):
    """Input contract for one exact-LIDVID PDS metadata fetch.

    Fields
    ------
    lidvid : str
        A fully-versioned PDS4 LIDVID of the form::

            urn:nasa:pds:<logical-identifier-components>::<version>

        Examples::

            urn:nasa:pds:juno_juv_raw:data_raw:jno_juv_2016187s_v01::1.0
            urn:nasa:pds:test_bundle:data_collection:test_product::2.0

    Constraints
    -----------
    - Must begin with ``urn:nasa:pds:``.
    - Must contain an explicit ``::version`` suffix (mutable "latest"
      resolution is deliberately rejected for reproducibility).
    - Only letters, digits, colon, underscore, hyphen, and dot are allowed
      in the LID and version components.
    - Whitespace, control characters, slash, backslash, question mark,
      percent-encoded tricks, fragment ``#``, and embedded URL schemes are
      all rejected.
    - Extra fields are forbidden.
    - The model is frozen after construction.

    Notes
    -----
    Phase 6E-A accepts ONLY an exact versioned LIDVID.  A bare LID would
    resolve to the "latest" version, which changes over time and therefore
    cannot be used for reproducible historical replay.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lidvid: str = Field(
        description=(
            "Exact versioned PDS4 LIDVID. "
            "Must begin with 'urn:nasa:pds:' and contain an explicit '::version' suffix. "
            "Only letters, digits, colon, underscore, hyphen, and dot are allowed. "
            "Whitespace, slash, backslash, ?, #, and percent-encoding are rejected."
        )
    )

    @field_validator("lidvid", mode="after")
    @classmethod
    def _validate_lidvid(cls, v: str) -> str:
        """Apply conservative safe-subset LIDVID validation rules."""
        # Rule: no whitespace or control characters anywhere.
        if any(c.isspace() or ord(c) < 32 for c in v):
            raise ValueError(
                "lidvid must not contain whitespace or control characters."
            )

        # Rule: no slash (path traversal / URL path confusion).
        if "/" in v or "\\" in v:
            raise ValueError(
                "lidvid must not contain slash or backslash."
            )

        # Rule: no question mark (query injection).
        if "?" in v:
            raise ValueError(
                "lidvid must not contain '?' (query injection rejected)."
            )

        # Rule: no fragment '#'.
        if "#" in v:
            raise ValueError(
                "lidvid must not contain '#' (fragment injection rejected)."
            )

        # Rule: no percent-encoding tricks.
        if "%" in v:
            raise ValueError(
                "lidvid must not contain '%' (percent-encoded path tricks rejected)."
            )

        # Rule: no embedded URL scheme (e.g. 'http://' inside LIDVID).
        # The urn: prefix itself is fine; only reject :// sequences.
        if "://" in v:
            raise ValueError(
                "lidvid must not contain '://' (embedded URL scheme rejected)."
            )

        # Rule: must begin with urn:nasa:pds:.
        if not v.startswith(_LIDVID_PREFIX):
            raise ValueError(
                f"lidvid must begin with '{_LIDVID_PREFIX}'. "
                "A bare LID or unrecognized URN namespace is rejected."
            )

        # Rule: must contain '::' separating LID from version — exactly once.
        # A LID must not itself contain '::'; only one version delimiter is valid.
        colon_pair_count = v.count("::")
        if colon_pair_count == 0:
            raise ValueError(
                "lidvid must contain an explicit '::version' suffix. "
                "A bare LID (without version) is rejected because it resolves "
                "to the latest version and is not suitable for reproducible "
                "historical replay."
            )
        if colon_pair_count > 1:
            raise ValueError(
                "lidvid must contain exactly one '::' version delimiter. "
                "Multiple '::' pairs are not valid."
            )

        # Rule: conservative character allow-list (letters, digits, : _ - .).
        if not _LIDVID_RE.match(v):
            raise ValueError(
                "lidvid contains disallowed characters. "
                "Allowed: letters, digits, colon, underscore, hyphen, dot. "
                "Slash, backslash, whitespace, ?, #, % are not allowed."
            )

        # Rule: version component (after '::') must be non-empty.
        # The regex already requires at least one character after '::',
        # but confirm explicitly for clarity.
        parts = v.rsplit("::", 1)
        if len(parts) != 2 or not parts[1]:
            raise ValueError(
                "lidvid version component (after '::') must not be empty."
            )

        return v


# ---------------------------------------------------------------------------
# B. PdsDataFile
# ---------------------------------------------------------------------------


class PdsDataFile(BaseModel):
    """Normalized representation of one PDS science data file.

    Derived exclusively from ``ops:Data_File_Info`` fields.

    DO NOT confuse with ``ops:Label_File_Info``.  The XML label is metadata;
    it is not the science data payload.

    Fields
    ------
    file_name : str
        Data-file name as reported by PDS.

    file_ref : str
        Data-file reference URL/path as reported by PDS.
        This adapter does NOT follow or download file_ref.
        It is stored only as external metadata.

    file_size_bytes : int
        Data-file size in bytes as reported by PDS.
        Must be >= 0.

    md5_checksum : str | None
        MD5 hex digest (exactly 32 lowercase hex characters), if present.

    mime_type : str | None
        MIME type of the data file, if present.

    Notes
    -----
    - file_ref is NOT followed or downloaded.
    - file_size_bytes is used to compute
      :attr:`PdsScienceProduct.total_data_size_bytes`.
    - strict=True prevents coercion of non-str to str, non-int to int.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    file_name: str = Field(description="Data-file name as reported by PDS.")
    file_ref: str = Field(
        description=(
            "Data-file reference URL/path as reported by PDS. "
            "Not followed or downloaded — stored as external metadata only."
        )
    )
    file_size_bytes: int = Field(
        description="Data-file size in bytes as reported by PDS. Must be >= 0."
    )
    md5_checksum: Optional[str] = Field(
        default=None,
        description="MD5 hex digest (32 lowercase hex chars), if present.",
    )
    mime_type: Optional[str] = Field(
        default=None,
        description="MIME type of the data file, if present.",
    )

    @field_validator("file_name", mode="after")
    @classmethod
    def _non_empty_file_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("file_name must not be empty.")
        return v

    @field_validator("file_ref", mode="after")
    @classmethod
    def _non_empty_file_ref(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("file_ref must not be empty.")
        return v

    @field_validator("file_size_bytes", mode="after")
    @classmethod
    def _non_negative_size(cls, v: int) -> int:
        if v < 0:
            raise ValueError("file_size_bytes must be >= 0.")
        return v

    @field_validator("md5_checksum", mode="after")
    @classmethod
    def _validate_md5(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        normalized = v.lower()
        if not _MD5_RE.match(normalized):
            raise ValueError(
                "md5_checksum must be exactly 32 hexadecimal characters."
            )
        return normalized


# ---------------------------------------------------------------------------
# C. PdsScienceProduct
# ---------------------------------------------------------------------------


class PdsScienceProduct(BaseModel):
    """Normalized external PDS science/observational product metadata fact.

    This is a lower-level external metadata fact produced by
    :class:`~backend.app.mission_sources.adapters.pds.PdsRegistryAdapter`.

    It is NOT a GCSI DataProduct.  Do not import or subclass DataProduct.

    The PDS Search API may not contain every PDS product.  Absence of this
    product from a Search API response does NOT imply the product does not
    exist in the PDS archive.

    Fields
    ------
    lid : str
        Logical identifier (without version).

    lidvid : str
        Full versioned LIDVID (lid::version).

    logical_identifier : str
        ``pds:Identification_Area.pds:logical_identifier`` field value.
        Must match ``lid``.

    version_id : str
        ``pds:Identification_Area.pds:version_id`` field value.
        Must match the version component of ``lidvid``.

    product_class : str
        Must be ``Product_Observational``.

    title : str
        Product title.

    observation_start_utc : datetime | None
        Observation start time in UTC.  None if not available in metadata.

    observation_stop_utc : datetime | None
        Observation stop time in UTC.  None if not available in metadata.
        If both are present, start <= stop is enforced.

    processing_level : str | None
        Processing level as reported by PDS, if present.

    instrument_lids : tuple[str, ...]
        Instrument logical identifiers referenced by this product.

    instrument_host_lids : tuple[str, ...]
        Instrument-host logical identifiers referenced by this product.

    investigation_lids : tuple[str, ...]
        Investigation logical identifiers referenced by this product.

    target_lids : tuple[str, ...]
        Target logical identifiers referenced by this product.

    data_files : tuple[PdsDataFile, ...]
        Normalized data-file metadata derived from ``ops:Data_File_Info``.
        Does NOT include label-file metadata.

    total_data_size_bytes : int
        Sum of all data-file sizes.  Excludes label-file size.

    registry_node : str | None
        ``ops:Harvest_Info.ops:node_name``, if present.

    registry_harvested_at : datetime | None
        ``ops:Harvest_Info.ops:harvest_date_time``, if present.

    Notes
    -----
    - observation_start_utc and observation_stop_utc are None when the PDS
      metadata does not supply them.  Do not substitute retrieved_at.
    - total_data_size_bytes is derived from data_files, not from label size.
    - strict=True prevents coercion of non-int to int for total_data_size_bytes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lid: str = Field(description="Logical identifier without version.")
    lidvid: str = Field(description="Full versioned LIDVID.")
    logical_identifier: str = Field(
        description="pds:Identification_Area.pds:logical_identifier value."
    )
    version_id: str = Field(
        description="pds:Identification_Area.pds:version_id value."
    )
    product_class: str = Field(
        description="Product class; must be 'Product_Observational'."
    )
    title: str = Field(description="Product title.")

    observation_start_utc: Optional[datetime] = Field(
        default=None,
        description="Observation start time in UTC. None if not available.",
    )
    observation_stop_utc: Optional[datetime] = Field(
        default=None,
        description="Observation stop time in UTC. None if not available.",
    )

    processing_level: Optional[str] = Field(
        default=None,
        description="Processing level as reported by PDS.",
    )

    instrument_lids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Instrument logical identifiers referenced by this product.",
    )
    instrument_host_lids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Instrument-host logical identifiers referenced by this product.",
    )
    investigation_lids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Investigation logical identifiers referenced by this product.",
    )
    target_lids: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Target logical identifiers referenced by this product.",
    )

    data_files: tuple[PdsDataFile, ...] = Field(
        default_factory=tuple,
        description=(
            "Normalized data-file metadata from ops:Data_File_Info. "
            "Does NOT include label-file metadata."
        ),
    )

    total_data_size_bytes: int = Field(
        description=(
            "Sum of all data-file sizes in bytes. "
            "Derived from ops:Data_File_Info only, not label-file size."
        )
    )

    registry_node: Optional[str] = Field(
        default=None,
        description="ops:Harvest_Info.ops:node_name, if present.",
    )
    registry_harvested_at: Optional[datetime] = Field(
        default=None,
        description="ops:Harvest_Info.ops:harvest_date_time, if present.",
    )

    @field_validator("product_class", mode="after")
    @classmethod
    def _require_product_observational(cls, v: str) -> str:
        if v != _PRODUCT_OBSERVATIONAL:
            raise ValueError(
                "PDS product class is not supported by this observational-product adapter."
            )
        return v

    @field_validator("observation_start_utc", "observation_stop_utc", "registry_harvested_at", mode="after")
    @classmethod
    def _validate_aware_datetime(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "Observation/registry datetime must be timezone-aware. "
                "Naive timestamps are rejected."
            )
        # Normalize to UTC.
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_model(self) -> "PdsScienceProduct":
        """Enforce cross-field identity consistency and time ordering."""
        # ---- Self-identity invariants (F) --------------------------------

        # 1. Non-empty string invariants.
        for field_name, value in (
            ("lid", self.lid),
            ("logical_identifier", self.logical_identifier),
            ("version_id", self.version_id),
            ("lidvid", self.lidvid),
            ("title", self.title),
        ):
            if not value:
                raise ValueError(f"{field_name} must not be empty.")

        # 2. logical_identifier == lid
        if self.logical_identifier != self.lid:
            raise ValueError(
                "logical_identifier must equal lid. "
                f"Got lid={self.lid!r}, logical_identifier={self.logical_identifier!r}."
            )

        # 3. Decompose lidvid on exactly the final '::' and cross-check lid / version_id.
        colon_pair_count = self.lidvid.count("::")
        if colon_pair_count != 1:
            raise ValueError(
                "lidvid must contain exactly one '::' version delimiter."
            )
        lidvid_lid, lidvid_version = self.lidvid.rsplit("::", 1)
        if lidvid_lid != self.lid:
            raise ValueError(
                "The LID portion of lidvid must equal lid. "
                f"Got lid={self.lid!r}, lidvid LID portion={lidvid_lid!r}."
            )
        if lidvid_version != self.version_id:
            raise ValueError(
                "The version portion of lidvid must equal version_id. "
                f"Got version_id={self.version_id!r}, lidvid version={lidvid_version!r}."
            )

        # 4. lidvid must satisfy the conservative exact-versioned LIDVID shape.
        if not _LIDVID_RE.match(self.lidvid):
            raise ValueError(
                "lidvid does not satisfy the required LIDVID character/format constraints."
            )
        if not self.lidvid.startswith(_LIDVID_PREFIX):
            raise ValueError(
                f"lidvid must begin with '{_LIDVID_PREFIX}'."
            )

        # ---- Total size invariant -----------------------------------------

        # 5. total_data_size_bytes must equal sum of data_files.
        expected_total = sum(f.file_size_bytes for f in self.data_files)
        if self.total_data_size_bytes != expected_total:
            raise ValueError(
                f"total_data_size_bytes ({self.total_data_size_bytes}) must equal "
                f"sum of data_files sizes ({expected_total})."
            )

        # ---- Time ordering -----------------------------------------------

        # 6. observation time ordering.
        if (
            self.observation_start_utc is not None
            and self.observation_stop_utc is not None
        ):
            if self.observation_start_utc > self.observation_stop_utc:
                raise ValueError(
                    "observation_start_utc must be <= observation_stop_utc."
                )

        return self
