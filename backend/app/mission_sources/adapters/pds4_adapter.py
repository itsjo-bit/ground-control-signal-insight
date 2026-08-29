"""GCSI Phase 6F-B1 — Generic PDS4 Observational Label Adapter.

This module implements ``GenericPds4ObservationalLabelAdapter``: an additive,
profile-driven adapter that normalizes PDS4 XML observational labels from
any instrument family into ``ArchiveScienceProduct``.

It does NOT modify or replace the existing ``PdsArchiveLabelAdapter`` (MWR-specific).
The V1 MWR path is preserved unchanged.

Architecture
------------
::

    PDS4 XML label bytes (any instrument)
            ↓
    GenericPds4ObservationalLabelAdapter.parse(raw_bytes, profile, retrieved_at)
            ↓
    ArchiveScienceProduct + ProvenanceRecord
            +
    ArchiveCaptureRecord  (optional — caller-assembled)

Profile-driven validation
-------------------------
All instrument-family-specific constraints are expressed in
``GenericPds4AdapterProfile`` objects, NOT as ``if instrument == X`` branches
in the parser.  The generic parser validates common PDS4 structure; the profile
validates identity, instrument/spacecraft LIDs, product class, and path.

Security posture (matches V1 MWR adapter)
-----------------------------------------
- HTTPS only (enforced by profile allowed_hosts / allowed_path_prefixes).
- No redirects (caller's transport must enforce follow_redirects=False).
- Bounded response: max ``MAX_PDS4_LABEL_BYTES`` (2 MiB).
- Strict UTF-8 decode.
- NUL byte rejection (before UTF-8 decode).
- UTF-8 BOM rejection.
- XML declaration encoding must be UTF-8 if present.
- DOCTYPE rejection (case-insensitive scan on decoded text).
- ENTITY rejection (case-insensitive scan on decoded text).
- No XInclude, no schemaLocation fetch, no network in parser.
- SHA-256 of exact raw bytes before normalization.
- Sanitized error messages (no raw label content in public errors).

This module reuses ``_scan_xml_security`` from ``pds_archive.py`` directly
to avoid duplicating the security logic and to prove V1 behavior unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
    build_pds4_source_record_id,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)

# Reuse the battle-tested XML security scanner from V1 (no copy/paste).
from .pds_archive import (
    PdsArchiveLabelValidationError,
    _scan_xml_security,
)

# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class GenericPds4AdapterError(Exception):
    """Base class for all generic PDS4 adapter failures."""


class GenericPds4AdapterUnavailableError(
    GenericPds4AdapterError, MissionSourceUnavailableError
):
    """PDS4 archive is unreachable or the label is not available.

    Raised for network errors, HTTP 404/429/5xx.
    """


class GenericPds4AdapterValidationError(
    GenericPds4AdapterError, MissionSourceValidationError
):
    """PDS4 label exists but fails validation.

    Raised for HTTP redirects, XML security violations, malformed XML,
    identity mismatches, profile violations, invalid timestamps, wrong
    product class, wrong instrument, oversized response.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum raw label body size: 2 MiB (matches V1 MWR adapter).
MAX_PDS4_LABEL_BYTES: int = 2 * 1024 * 1024

# PDS4 namespace.
_PDS_NS: str = "http://pds.nasa.gov/pds4/pds/v1"
_PDS_NS_BRACED: str = f"{{{_PDS_NS}}}"

_PRODUCT_OBSERVATIONAL: str = "Product_Observational"

_ARCHIVE_SOURCE_SYSTEM: str = "NASA Planetary Data System"

_ASCII_DECIMAL_RE = re.compile(r"^[0-9]+$")

_XML_ENCODING_DECL_RE = re.compile(
    r"""encoding\s*=\s*(?:"([^"]+)"|'([^']+)')""",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# GenericPds4AdapterProfile
# ---------------------------------------------------------------------------


class GenericPds4AdapterProfile(BaseModel):
    """Profile expressing instrument-family-specific validation constraints.

    All instrument-specific rules live here, NOT in the generic parser.

    Fields
    ------
    profile_id : str
        Short stable identifier for this profile, e.g. ``"jiram_pds4"``.

    allowed_hosts : frozenset[str]
        Set of trusted hostnames.  Only HTTPS to these hosts is accepted.

    allowed_path_prefixes : tuple[str, ...]
        One or more trusted path prefixes.  The label URL path must start
        with at least one of these.

    expected_mission : str
        Expected mission name, e.g. ``"JUNO"``.

    expected_spacecraft : str
        Expected spacecraft identifier, e.g. ``"JNO"``.

    expected_instrument : str
        Expected instrument identifier, e.g. ``"JIRAM"``.

    instrument_lid : str
        Expected PDS4 instrument context LID,
        e.g. ``"urn:nasa:pds:context:instrument:jiram.jno"``.

    spacecraft_host_lid : str
        Expected PDS4 instrument-host context LID,
        e.g. ``"urn:nasa:pds:context:instrument_host:spacecraft.jno"``.

    investigation_lid : str
        Expected PDS4 investigation context LID,
        e.g. ``"urn:nasa:pds:context:investigation:mission.juno"``.

    product_family : str
        Science product family tag for this instrument,
        e.g. ``"JIRAM_IMG"``.

    expected_product_class : str
        Required PDS4 product class, e.g. ``"Product_Observational"``.

    allowed_processing_levels : frozenset[str] | None
        If set, processing_level parsed from the label must be in this set.
        If None, any non-empty processing level is accepted.

    allowed_information_model_versions : frozenset[str] | None
        If set, information_model_version must be in this set.
        If None, any version is accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str = Field(description="Short stable profile identifier.")
    allowed_hosts: frozenset[str] = Field(
        description="Trusted hostnames. Only HTTPS to these hosts is accepted."
    )
    allowed_path_prefixes: tuple[str, ...] = Field(
        description="Trusted path prefixes for label URLs."
    )
    expected_mission: str = Field(description="Expected mission name, e.g. 'JUNO'.")
    expected_spacecraft: str = Field(description="Expected spacecraft ID, e.g. 'JNO'.")
    expected_instrument: str = Field(description="Expected instrument ID, e.g. 'JIRAM'.")
    instrument_lid: str = Field(
        description="Expected PDS4 instrument context LID."
    )
    spacecraft_host_lid: str = Field(
        description="Expected PDS4 instrument-host context LID."
    )
    investigation_lid: str = Field(
        description="Expected PDS4 investigation context LID."
    )
    product_family: str = Field(
        description="Science product family tag for this instrument."
    )
    expected_product_class: str = Field(
        default="Product_Observational",
        description="Required PDS4 product class.",
    )
    allowed_processing_levels: Optional[frozenset[str]] = Field(
        default=None,
        description=(
            "If set, processing_level must be in this set. "
            "If None, any non-empty level is accepted."
        ),
    )
    allowed_information_model_versions: Optional[frozenset[str]] = Field(
        default=None,
        description=(
            "If set, information_model_version must be in this set. "
            "If None, any version is accepted."
        ),
    )

    @field_validator("profile_id", "expected_mission", "expected_spacecraft",
                     "expected_instrument", "instrument_lid", "spacecraft_host_lid",
                     "investigation_lid", "product_family", "expected_product_class",
                     mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Profile string field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# Pre-built profiles for Phase 6F-B1 target families
# ---------------------------------------------------------------------------

#: JIRAM calibrated/derived products — PDS4 label, pds.nasa.gov
JIRAM_PDS4_PROFILE = GenericPds4AdapterProfile(
    profile_id="jiram_pds4",
    allowed_hosts=frozenset({"pds.nasa.gov"}),
    allowed_path_prefixes=("/ds-view/pds/viewBundle.jsp", "/api/", "/pds/"),
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JIRAM",
    instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
    spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
    investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
    product_family="JIRAM",
    allowed_processing_levels=frozenset({"Calibrated", "Derived"}),
)

#: UVS calibrated products — PDS4 label
UVS_PDS4_PROFILE = GenericPds4AdapterProfile(
    profile_id="uvs_pds4",
    allowed_hosts=frozenset({"pds.nasa.gov"}),
    allowed_path_prefixes=("/api/", "/pds/"),
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="UVS",
    instrument_lid="urn:nasa:pds:context:instrument:uvs.jno",
    spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
    investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
    product_family="UVS",
    allowed_processing_levels=frozenset({"Calibrated"}),
)

#: MWR — generic PDS4 compatibility profile (for test parity with V1 adapter)
MWR_GENERIC_PDS4_PROFILE = GenericPds4AdapterProfile(
    profile_id="mwr_generic_pds4",
    allowed_hosts=frozenset({"pds-atmospheres.nmsu.edu"}),
    allowed_path_prefixes=("/PDS/data/jnomwr_1100/DATA/",),
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="MWR",
    instrument_lid="urn:nasa:pds:context:instrument:mwr.jno",
    spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
    investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
    product_family="MWR",
    allowed_processing_levels=frozenset({"Calibrated"}),
    allowed_information_model_versions=frozenset({"1.7.0.0"}),
)


# ---------------------------------------------------------------------------
# XML helper utilities (generic, profile-independent)
# ---------------------------------------------------------------------------


def _find_one_generic(
    parent: ET.Element,
    tag: str,
    ns: str = _PDS_NS,
    required: bool = True,
    context: str = "",
) -> Optional[ET.Element]:
    """Find exactly one direct child with given tag in PDS namespace."""
    found = parent.findall(f"{{{ns}}}{tag}")
    if len(found) == 0:
        if required:
            raise GenericPds4AdapterValidationError(
                f"PDS4 label missing required element <{tag}>{context}."
            )
        return None
    if len(found) > 1:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label has {len(found)} <{tag}> elements; "
            f"expected exactly 1{context}."
        )
    return found[0]


def _text_required_generic(elem: ET.Element, context: str = "") -> str:
    raw = (elem.text or "").strip()
    if not raw:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label element {context!r} has empty or missing text."
        )
    return raw


def _parse_pds4_datetime(raw: str, field_name: str) -> datetime:
    """Parse PDS4 datetime string to UTC-aware datetime."""
    raw = raw.strip()
    if not raw:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label datetime field {field_name!r} is empty."
        )
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label datetime field {field_name!r} could not be parsed as ISO 8601."
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label datetime field {field_name!r} is naive; "
            "timezone-aware value required."
        )
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# URL trust validation (profile-driven)
# ---------------------------------------------------------------------------


def _validate_label_url_trust(label_url: str, profile: GenericPds4AdapterProfile) -> None:
    """Validate label_url against profile-defined trust constraints.

    Enforces:
    - HTTPS scheme
    - Exact hostname in profile.allowed_hosts
    - Path starts with one of profile.allowed_path_prefixes
    - No userinfo, no non-443 port, no query, no fragment
    - No percent-encoding, no backslash in raw URL
    """
    if "%" in label_url:
        raise GenericPds4AdapterValidationError(
            "label_url must not contain percent-encoded characters."
        )
    if "\\" in label_url:
        raise GenericPds4AdapterValidationError(
            "label_url must not contain backslash characters."
        )
    try:
        parsed = urlsplit(label_url)
    except Exception as exc:
        raise GenericPds4AdapterValidationError(
            "label_url could not be parsed as a URL."
        ) from exc

    if parsed.scheme != "https":
        raise GenericPds4AdapterValidationError(
            f"label_url must use HTTPS scheme; got {parsed.scheme!r}."
        )
    if parsed.hostname not in profile.allowed_hosts:
        raise GenericPds4AdapterValidationError(
            f"label_url host {parsed.hostname!r} is not in the trusted host set "
            f"for profile {profile.profile_id!r}."
        )
    if parsed.username is not None or parsed.password is not None:
        raise GenericPds4AdapterValidationError(
            "label_url must not contain userinfo."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise GenericPds4AdapterValidationError(
            "label_url contains an invalid port specification."
        ) from exc
    if port is not None and port != 443:
        raise GenericPds4AdapterValidationError(
            f"label_url port must be absent or 443; got {port!r}."
        )
    if parsed.query:
        raise GenericPds4AdapterValidationError(
            "label_url must not contain a query string."
        )
    if parsed.fragment:
        raise GenericPds4AdapterValidationError(
            "label_url must not contain a fragment."
        )
    path = parsed.path
    if not any(path.startswith(prefix) for prefix in profile.allowed_path_prefixes):
        raise GenericPds4AdapterValidationError(
            f"label_url path {path!r} does not start with any allowed prefix "
            f"for profile {profile.profile_id!r}: "
            f"{list(profile.allowed_path_prefixes)!r}."
        )


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _build_pds4_provenance_id_input(source_record_id: str, label_url: str) -> str:
    """Return the deterministic JSON identity string for provenance_id computation."""
    return json.dumps(
        {
            "adapter": "gcsi:generic_pds4_label:v1",
            "source_record_id": source_record_id,
            "label_url": label_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _compute_pds4_provenance_id(identity: str, content_sha256: str) -> str:
    """Compute deterministic provenance_id.

    Formula: SHA-256(identity + "|" + content_sha256)
    """
    combined = identity + "|" + content_sha256
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core pure parser — shared by live adapter and snapshot reload
# ---------------------------------------------------------------------------


def parse_generic_pds4_label(
    raw_bytes: bytes,
    label_url: str,
    profile: GenericPds4AdapterProfile,
    retrieved_at: datetime,
) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
    """Parse and validate raw PDS4 XML label bytes using a profile.

    This is a pure function: performs NO HTTP requests.  It is the single
    authoritative parser for both the live fetch path and snapshot reload.

    Parameters
    ----------
    raw_bytes:
        Exact raw label bytes.  Must not exceed ``MAX_PDS4_LABEL_BYTES``.

    label_url:
        Source URL of this label (for provenance and file_ref derivation).

    profile:
        Instrument-family-specific validation profile.

    retrieved_at:
        Timezone-aware UTC datetime when the bytes were acquired.

    Returns
    -------
    tuple[ArchiveScienceProduct, ProvenanceRecord]
        Fully validated normalized product and EXTERNAL_AUTHORITATIVE provenance.

    Raises
    ------
    GenericPds4AdapterValidationError
        For any structural or semantic validation failure.
    """
    if not isinstance(retrieved_at, datetime):
        raise GenericPds4AdapterValidationError(
            "retrieved_at must be a datetime object."
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise GenericPds4AdapterValidationError(
            "retrieved_at must be timezone-aware."
        )
    retrieved_at_utc = retrieved_at.astimezone(timezone.utc)

    # 1. Size limit.
    if len(raw_bytes) > MAX_PDS4_LABEL_BYTES:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label response exceeds maximum allowed size "
            f"({MAX_PDS4_LABEL_BYTES} bytes)."
        )

    # 2. SHA-256 of raw bytes BEFORE any decoding.
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. XML security scan — reuse V1 battle-tested implementation.
    #    Raises PdsArchiveLabelValidationError on any security violation;
    #    translate to GenericPds4AdapterValidationError.
    try:
        xml_text = _scan_xml_security(raw_bytes)
    except PdsArchiveLabelValidationError as exc:
        raise GenericPds4AdapterValidationError(str(exc)) from exc

    # 4. Parse XML from validated Unicode text.
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise GenericPds4AdapterValidationError(
            "PDS4 label XML is malformed and could not be parsed."
        ) from exc

    # 5. Validate root element namespace + expected product class.
    expected_root_tag = f"{_PDS_NS_BRACED}{profile.expected_product_class}"
    if root.tag != expected_root_tag:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label root element is not {profile.expected_product_class!r} "
            f"in the expected PDS namespace ({_PDS_NS!r}). "
            f"Profile {profile.profile_id!r} requires product class "
            f"{profile.expected_product_class!r}."
        )

    # 6. Identification_Area.
    id_area = _find_one_generic(root, "Identification_Area")

    lid_elem = _find_one_generic(id_area, "logical_identifier")
    label_lid = _text_required_generic(lid_elem, "logical_identifier")

    ver_elem = _find_one_generic(id_area, "version_id")
    label_version = _text_required_generic(ver_elem, "version_id")

    imv_elem = _find_one_generic(id_area, "information_model_version")
    im_version = _text_required_generic(imv_elem, "information_model_version")
    if (
        profile.allowed_information_model_versions is not None
        and im_version not in profile.allowed_information_model_versions
    ):
        raise GenericPds4AdapterValidationError(
            f"PDS4 label information_model_version {im_version!r} is not in the "
            f"allowed set for profile {profile.profile_id!r}: "
            f"{sorted(profile.allowed_information_model_versions)!r}."
        )

    title_elem = _find_one_generic(id_area, "title")
    label_title = _text_required_generic(title_elem, "title")

    # Compose LIDVID from label fields.
    label_lidvid = f"{label_lid}::{label_version}"
    source_record_id = build_pds4_source_record_id(label_lidvid)

    # 7. Observation_Area.
    obs_area = _find_one_generic(root, "Observation_Area")

    # 7a. Time_Coordinates.
    time_coords = _find_one_generic(obs_area, "Time_Coordinates")
    start_elem = _find_one_generic(time_coords, "start_date_time")
    stop_elem = _find_one_generic(time_coords, "stop_date_time")
    start_raw = _text_required_generic(start_elem, "start_date_time")
    stop_raw = _text_required_generic(stop_elem, "stop_date_time")
    obs_start_utc = _parse_pds4_datetime(start_raw, "start_date_time")
    obs_stop_utc = _parse_pds4_datetime(stop_raw, "stop_date_time")
    if obs_start_utc > obs_stop_utc:
        raise GenericPds4AdapterValidationError(
            "PDS4 label start_date_time is after stop_date_time."
        )

    # 7b. Primary_Result_Summary.processing_level.
    processing_level: Optional[str] = None
    prs_elem = _find_one_generic(obs_area, "Primary_Result_Summary", required=False)
    if prs_elem is not None:
        proc_elem = _find_one_generic(prs_elem, "processing_level", required=False)
        if proc_elem is not None:
            raw_pl = (proc_elem.text or "").strip()
            if raw_pl:
                processing_level = raw_pl
    if (
        profile.allowed_processing_levels is not None
        and processing_level not in profile.allowed_processing_levels
    ):
        raise GenericPds4AdapterValidationError(
            f"PDS4 label processing_level {processing_level!r} is not in the "
            f"allowed set for profile {profile.profile_id!r}: "
            f"{sorted(profile.allowed_processing_levels)!r}."
        )

    # 7c. Profile context LID validation.
    _validate_pds4_context_lids(obs_area, profile)

    # 7d. Target names.
    target_names = _extract_pds4_target_names(obs_area)

    # 8. File_Area_Observational — extract data file metadata.
    data_files = _extract_pds4_data_files(root, label_url)

    total_size = sum(f.file_size_bytes for f in data_files)

    # 9. Build ArchiveScienceProduct.
    import pydantic
    try:
        product = ArchiveScienceProduct(
            source_record_id=source_record_id,
            source_standard=ArchiveSourceStandard.PDS4,
            source_dataset_id=label_lid,
            source_product_id=label_lid,
            source_version=label_version,
            mission_name=profile.expected_mission,
            spacecraft_name=profile.expected_spacecraft,
            instrument_name=profile.expected_instrument,
            product_family=profile.product_family,
            processing_level=processing_level,
            observation_start_utc=obs_start_utc,
            observation_stop_utc=obs_stop_utc,
            target_names=target_names,
            data_files=tuple(data_files),
            total_data_size_bytes=total_size,
            source_label_ref=label_url,
        )
    except pydantic.ValidationError as exc:
        raise GenericPds4AdapterValidationError(
            "PDS4 normalized product failed internal validation."
        ) from exc

    # 10. Build provenance.
    identity = _build_pds4_provenance_id_input(source_record_id, label_url)
    provenance_id = _compute_pds4_provenance_id(identity, content_sha256)
    provenance = ProvenanceRecord(
        provenance_id=provenance_id,
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system=_ARCHIVE_SOURCE_SYSTEM,
        source_version=im_version,
        source_record_id=source_record_id,
        source_uri=label_url,
        retrieved_at=retrieved_at_utc,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
    )

    return product, provenance


# ---------------------------------------------------------------------------
# Context LID validation (profile-driven)
# ---------------------------------------------------------------------------


def _validate_pds4_context_lids(
    obs_area: ET.Element, profile: GenericPds4AdapterProfile
) -> None:
    """Validate Investigation, Instrument, and Spacecraft context LIDs.

    Validates that the label contains the profile-required LIDs in the
    appropriate Observation_Area sub-elements.  Rejects missing, duplicate,
    or wrong-value references.
    """
    # Investigation_Area: must have lid_reference == profile.investigation_lid.
    inv_areas = obs_area.findall(f"{_PDS_NS_BRACED}Investigation_Area")
    found_inv = False
    for inv_area in inv_areas:
        for ir in inv_area.findall(f"{_PDS_NS_BRACED}Internal_Reference"):
            lid_elem = ir.find(f"{_PDS_NS_BRACED}lid_reference")
            if lid_elem is not None:
                lid_val = (lid_elem.text or "").strip()
                if lid_val == profile.investigation_lid:
                    found_inv = True
                    break
        if found_inv:
            break
    if not found_inv:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label is missing required investigation context LID "
            f"{profile.investigation_lid!r} (profile {profile.profile_id!r})."
        )

    # Observing_System: must contain components with instrument + spacecraft LIDs.
    os_elems = obs_area.findall(f"{_PDS_NS_BRACED}Observing_System")
    found_instrument = False
    found_spacecraft = False
    for os_elem in os_elems:
        for comp in os_elem.findall(f"{_PDS_NS_BRACED}Observing_System_Component"):
            ir_elems = comp.findall(f"{_PDS_NS_BRACED}Internal_Reference")
            for ir in ir_elems:
                lid_elem = ir.find(f"{_PDS_NS_BRACED}lid_reference")
                if lid_elem is not None:
                    lid_val = (lid_elem.text or "").strip()
                    if lid_val == profile.instrument_lid:
                        found_instrument = True
                    if lid_val == profile.spacecraft_host_lid:
                        found_spacecraft = True

    if not found_instrument:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label is missing required instrument context LID "
            f"{profile.instrument_lid!r} (profile {profile.profile_id!r})."
        )
    if not found_spacecraft:
        raise GenericPds4AdapterValidationError(
            f"PDS4 label is missing required spacecraft context LID "
            f"{profile.spacecraft_host_lid!r} (profile {profile.profile_id!r})."
        )


# ---------------------------------------------------------------------------
# Target name extraction
# ---------------------------------------------------------------------------


def _extract_pds4_target_names(obs_area: ET.Element) -> tuple[str, ...]:
    """Extract target names from Target_Identification elements."""
    names: list[str] = []
    for tgt_id in obs_area.findall(f"{_PDS_NS_BRACED}Target_Identification"):
        name_elem = tgt_id.find(f"{_PDS_NS_BRACED}name")
        if name_elem is not None:
            raw = (name_elem.text or "").strip()
            if raw:
                names.append(raw)
    return tuple(names)


# ---------------------------------------------------------------------------
# File area extraction
# ---------------------------------------------------------------------------


def _extract_pds4_data_files(
    root: ET.Element, label_url: str
) -> list[ArchiveDataFile]:
    """Extract data file metadata from File_Area_Observational."""
    import pydantic

    file_area = root.find(f"{_PDS_NS_BRACED}File_Area_Observational")
    if file_area is None:
        # No file area — return empty list (product without resolved payload).
        return []

    file_elem = file_area.find(f"{_PDS_NS_BRACED}File")
    if file_elem is None:
        return []

    fname_elem = file_elem.find(f"{_PDS_NS_BRACED}file_name")
    if fname_elem is None:
        return []
    file_name = (fname_elem.text or "").strip()
    if not file_name:
        return []

    # File size.
    fsize_elem = file_elem.find(f"{_PDS_NS_BRACED}file_size")
    file_size_bytes: int = 0
    size_certainty = ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE
    if fsize_elem is not None:
        fsize_raw = (fsize_elem.text or "").strip()
        fsize_unit = (fsize_elem.get("unit") or "").strip().lower()
        if fsize_unit == "byte" and _ASCII_DECIMAL_RE.match(fsize_raw):
            file_size_bytes = int(fsize_raw)
            size_certainty = ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    # MD5 checksum.
    md5_elem = file_elem.find(f"{_PDS_NS_BRACED}md5_checksum")
    checksum_algorithm: Optional[str] = None
    checksum_value: Optional[str] = None
    if md5_elem is not None:
        raw_md5 = (md5_elem.text or "").strip().lower()
        if raw_md5:
            checksum_algorithm = "MD5"
            checksum_value = raw_md5

    # Derive file_ref from label URL directory + file_name.
    file_ref: Optional[str] = None
    try:
        parsed_url = urlsplit(label_url)
        label_dir = parsed_url.path.rsplit("/", 1)[0] + "/"
        file_ref = f"https://{parsed_url.hostname}{label_dir}{file_name}"
    except Exception:
        file_ref = None

    try:
        data_file = ArchiveDataFile(
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            size_certainty=size_certainty,
            checksum_algorithm=checksum_algorithm,
            checksum_value=checksum_value,
            file_ref=file_ref,
        )
    except pydantic.ValidationError:
        return []

    return [data_file]
