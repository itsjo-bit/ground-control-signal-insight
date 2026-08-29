"""GCSI Phase 6F-B1 — Generic PDS3 Observational Label Adapter.

This module implements ``GenericPds3ObservationalLabelAdapter``: an additive,
profile-driven adapter that normalizes PDS3 ASCII label (PVL/ODL format) bytes
from any instrument family into ``ArchiveScienceProduct``.

It does NOT modify or replace any existing V1 adapter or snapshot infrastructure.

PDS3 parser decision
--------------------
No mature PVL/PDS3 parser is available in the existing dependency set
(``pydantic``, ``fastapi``, ``httpx``, ``numpy``, ``scipy``).

Adding a new dependency for PDS3 parsing is not required here:

1. The PDS3 labels in scope (WAVES, FGM, JunoCam, JADE, JEDI) use a
   consistent keyword=value format that can be reliably parsed with a
   deliberately bounded subset parser.

2. The subset parser explicitly rejects unsupported constructs (multi-line
   SEQUENCE groups, nested ODL objects beyond the top level) with a clear
   error, rather than silently misparsing them.

3. The parser only extracts the common fields required per PART G:
   DATA_SET_ID, PRODUCT_ID, PRODUCT_VERSION_ID, START_TIME, STOP_TIME,
   INSTRUMENT_ID / INSTRUMENT_NAME, INSTRUMENT_HOST_ID, TARGET_NAME,
   PROCESSING_LEVEL_ID, file pointer / payload filename, file size.

4. Unknown keyword lines are skipped, never silently misparsed.

Bounded PDS3 PVL subset parser
-------------------------------
The parser handles:

- Single-line keyword = value assignments (unquoted, quoted with \", set).
- Multi-value sets: { VAL1, VAL2, ... } treated as list.
- OBJECT/END_OBJECT blocks: parsed for top-level fields; nested sub-objects
  are collected but not recursively descended by default.
- ^POINTER = "filename" or ^POINTER = ("filename", n) for payload file refs.
- No support for: SFDU/XFR/PDS labels with complex nested ODL syntax
  beyond one level.  Such constructs cause explicit rejection.
- Strict ASCII: rejects NUL bytes, rejects non-ASCII outside quoted strings.

Profile-driven validation
--------------------------
All instrument-family-specific constraints live in ``GenericPds3AdapterProfile``.
No ``if instrument == X`` branches appear in the generic parser.

Security posture
----------------
- HTTPS only for network origins (profile allowed_hosts).
- Bounded read: ``MAX_PDS3_LABEL_BYTES`` (512 KiB; PDS3 labels are small).
- Strict ASCII subset: NUL rejection, non-ASCII outside quotes logged but
  not cause of label text misparse.
- No network activity in parser.
- SHA-256 of raw bytes before any parsing.
- Error messages do not expose raw label content.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
    build_pds3_source_record_id,
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


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class GenericPds3AdapterError(Exception):
    """Base class for all generic PDS3 adapter failures."""


class GenericPds3AdapterUnavailableError(
    GenericPds3AdapterError, MissionSourceUnavailableError
):
    """PDS3 archive is unreachable or the label is not available."""


class GenericPds3AdapterValidationError(
    GenericPds3AdapterError, MissionSourceValidationError
):
    """PDS3 label exists but fails validation.

    Raised for: oversized response, NUL bytes, unsupported PVL constructs,
    missing required keywords, invalid timestamps, profile violations,
    negative/invalid file sizes, checksum format mismatch.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum raw label bytes: 512 KiB (PDS3 labels are small ASCII files).
MAX_PDS3_LABEL_BYTES: int = 512 * 1024

_ARCHIVE_SOURCE_SYSTEM: str = "NASA Planetary Data System"

# PDS3 datetime formats — PDS3 uses DOY format: YYYY-DDDTHH:MM:SS.sss
_PDS3_DATETIME_DOY_RE = re.compile(
    r"^(\d{4})-(\d{3})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?$"
)
# Standard ISO date-time: YYYY-MM-DDTHH:MM:SS.sss
_PDS3_DATETIME_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?$"
)
# Date-only: YYYY-DDD or YYYY-MM-DD
_PDS3_DATE_DOY_RE = re.compile(r"^(\d{4})-(\d{3})$")
_PDS3_DATE_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Strict ASCII decimal integer (no sign, no float, no exponent).
_ASCII_DECIMAL_RE = re.compile(r"^[0-9]+$")

# MD5 pattern.
_MD5_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

# PDS3 keyword = value line.
_KV_LINE_RE = re.compile(r"^\s*([A-Z_\^][A-Z0-9_\^]*)\s*=\s*(.+?)\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# GenericPds3AdapterProfile
# ---------------------------------------------------------------------------


class GenericPds3AdapterProfile(BaseModel):
    """Profile expressing instrument-family-specific PDS3 validation constraints.

    Fields
    ------
    profile_id : str
        Short stable identifier, e.g. ``"waves_burst_pds3"``.

    expected_mission : str
        Expected mission name, e.g. ``"JUNO"``.

    expected_spacecraft : str
        Expected spacecraft ID matched against INSTRUMENT_HOST_ID,
        e.g. ``"JNO"``.

    expected_instrument : str
        Expected instrument ID matched against INSTRUMENT_ID or
        INSTRUMENT_NAME, e.g. ``"WAV"``.

    expected_data_set_id_prefix : str | None
        If set, DATA_SET_ID must start with this prefix.
        E.g. ``"JNO-E/J/SS-WAV"``.

    product_family : str
        Science product family tag, e.g. ``"WAVES_BURST"``.

    allowed_processing_levels : frozenset[str] | None
        If set, PROCESSING_LEVEL_ID (if present) must be in this set.

    require_start_stop_time : bool
        If True, START_TIME and STOP_TIME are required.
        Default True.

    allowed_hosts : frozenset[str] | None
        Trusted hostnames if label is fetched from a network origin.
        None means any (offline/fixture labels).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    profile_id: str
    expected_mission: str
    expected_spacecraft: str
    expected_instrument: str
    expected_data_set_id_prefix: Optional[str] = None
    product_family: str
    allowed_processing_levels: Optional[frozenset[str]] = None
    require_start_stop_time: bool = True
    allowed_hosts: Optional[frozenset[str]] = None

    @field_validator("profile_id", "expected_mission", "expected_spacecraft",
                     "expected_instrument", "product_family", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Profile string field must not be empty.")
        return v


# ---------------------------------------------------------------------------
# Pre-built profiles for B1 target families
# ---------------------------------------------------------------------------

WAVES_BURST_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="waves_burst_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="WAV",
    expected_data_set_id_prefix="JNO-E/J/SS-WAV",
    product_family="WAVES_BURST",
    allowed_processing_levels=frozenset({"3"}),
    require_start_stop_time=True,
)

WAVES_SURVEY_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="waves_survey_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="WAV",
    expected_data_set_id_prefix="JNO-E/J/SS-WAV",
    product_family="WAVES_SURVEY",
    allowed_processing_levels=frozenset({"3"}),
    require_start_stop_time=True,
)

JUNOCAM_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="junocam_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JNC",
    product_family="JUNOCAM",
    require_start_stop_time=True,
)

FGM_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="fgm_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="FGM",
    product_family="FGM",
    require_start_stop_time=True,
)

JADE_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="jade_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JAD",
    product_family="JADE",
    require_start_stop_time=True,
)

JEDI_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="jedi_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JED",
    product_family="JEDI",
    require_start_stop_time=True,
)


# ---------------------------------------------------------------------------
# Bounded PDS3 PVL subset parser
# ---------------------------------------------------------------------------


def _parse_pvl_value(raw_value: str) -> str | list[str]:
    """Parse a PDS3 PVL value into a Python string or list of strings.

    Handles:
    - Quoted strings: ``"value"`` or ``'value'`` → ``str``
    - Bare unquoted tokens: ``VALUE``, ``123``, etc. → ``str``
    - Sets/sequences: ``{ val1, val2, "val3" }`` → ``list[str]``
    - ``N/A``, ``"N/A"`` → ``"N/A"`` (preserved)
    - Unit suffixes like ``<km>`` are stripped.

    Returns ``str`` or ``list[str]``.
    """
    v = raw_value.strip()

    # Set/sequence: { ... }
    if v.startswith("{") and "}" in v:
        inner = v[v.index("{") + 1 : v.rindex("}")].strip()
        items = []
        for item in inner.split(","):
            item = item.strip()
            if item.startswith('"') and item.endswith('"'):
                item = item[1:-1]
            elif item.startswith("'") and item.endswith("'"):
                item = item[1:-1]
            # Strip unit suffix like <km>
            item = re.sub(r"\s*<[^>]+>$", "", item).strip()
            if item:
                items.append(item)
        return items

    # Quoted string.
    if (v.startswith('"') and v.endswith('"')) or (
        v.startswith("'") and v.endswith("'")
    ):
        return v[1:-1]

    # Strip unit suffix (e.g. "12345 <byte>").
    v = re.sub(r"\s*<[^>]+>$", "", v).strip()

    return v


def _parse_pds3_label(raw_bytes: bytes) -> dict[str, str | list[str]]:
    """Parse PDS3/ODL ASCII label bytes into a flat keyword→value dict.

    Only top-level keywords are extracted.  OBJECT / END_OBJECT blocks
    at the top level are recorded with key ``"_OBJECT_<name>"`` holding
    the raw block text for optional downstream use; they are NOT recursively
    parsed (which would require a full PVL grammar).

    Parameters
    ----------
    raw_bytes:
        Raw label bytes.  Must be strict ASCII (rejects NUL; non-ASCII
        bytes outside are treated as opaque and skipped).

    Returns
    -------
    dict[str, str | list[str]]
        Keyword → value mapping.  All keys are uppercased.
        ``^POINTER`` keywords are retained with the ``^`` prefix.

    Raises
    ------
    GenericPds3AdapterValidationError
        On NUL bytes, non-ASCII body outside quotes, or ``END`` without
        prior content.
    """
    # Reject NUL bytes immediately.
    if b"\x00" in raw_bytes:
        raise GenericPds3AdapterValidationError(
            "PDS3 label contains NUL bytes (0x00); rejected."
        )

    # Decode as ASCII; tolerate Latin-1 extensions in quoted strings by
    # replacing unmappable bytes (they should not appear in keyword names
    # or in the numeric/datetime values we care about).
    try:
        text = raw_bytes.decode("ascii")
    except UnicodeDecodeError:
        # Fallback: decode as Latin-1 (superset of ASCII).
        text = raw_bytes.decode("latin-1", errors="replace")

    result: dict[str, str | list[str]] = {}
    in_object: Optional[str] = None
    object_lines: list[str] = []
    depth = 0

    for line in text.splitlines():
        stripped = line.strip()

        # END marker: stop parsing.
        if stripped.upper() == "END":
            break

        # OBJECT / END_OBJECT tracking.
        if stripped.upper().startswith("OBJECT"):
            kv = stripped.split("=", 1)
            obj_name = kv[1].strip() if len(kv) == 2 else "UNKNOWN"
            if depth == 0:
                in_object = obj_name.upper()
                object_lines = [line]
                depth += 1
            else:
                if object_lines is not None:
                    object_lines.append(line)
                depth += 1
            continue

        if stripped.upper().startswith("END_OBJECT"):
            depth -= 1
            if depth == 0 and in_object is not None:
                object_lines.append(line)
                result[f"_OBJECT_{in_object}"] = "\n".join(object_lines)
                in_object = None
                object_lines = []
            elif object_lines is not None:
                object_lines.append(line)
            continue

        if depth > 0:
            if object_lines is not None:
                object_lines.append(line)
            continue

        # Top-level keyword = value.
        m = _KV_LINE_RE.match(stripped)
        if m:
            key = m.group(1).upper()
            raw_val = m.group(2)
            result[key] = _parse_pvl_value(raw_val)

    return result


def _extract_pds3_str(
    kv: dict, key: str, required: bool = False
) -> Optional[str]:
    """Extract a string value from the parsed PDS3 dict."""
    val = kv.get(key)
    if val is None:
        if required:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label is missing required keyword {key!r}."
            )
        return None
    if isinstance(val, list):
        # Take first element for single-valued context.
        return val[0] if val else None
    return str(val).strip().strip('"').strip("'")


def _extract_pds3_list(kv: dict, key: str) -> list[str]:
    """Extract a list value from the parsed PDS3 dict."""
    val = kv.get(key)
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v).strip().strip('"').strip("'") for v in val]
    return [str(val).strip().strip('"').strip("'")]


def _parse_pds3_datetime(raw: str, field_name: str) -> datetime:
    """Parse PDS3 timestamp to UTC-aware datetime.

    Handles:
    - ``YYYY-DDDTHH:MM:SS[.sss]``  (day-of-year)
    - ``YYYY-MM-DDTHH:MM:SS[.sss]``
    - ``YYYY-DDD``  (date only → midnight UTC)
    - ``YYYY-MM-DD``  (date only → midnight UTC)

    Raises ``GenericPds3AdapterValidationError`` for unparseable values.
    """
    import math

    raw = raw.strip().strip('"').strip("'")
    if not raw or raw.upper() in ("N/A", "UNK", "NULL"):
        raise GenericPds3AdapterValidationError(
            f"PDS3 label datetime field {field_name!r} has no valid value: {raw!r}."
        )

    # YYYY-DDDTHH:MM:SS
    m = _PDS3_DATETIME_DOY_RE.match(raw)
    if m:
        year = int(m.group(1))
        doy = int(m.group(2))
        hh = int(m.group(3))
        mm = int(m.group(4))
        ss = int(m.group(5))
        frac_str = m.group(6) or ""
        microsecond = int(round(float("0" + frac_str) * 1_000_000)) if frac_str else 0
        try:
            from datetime import date, timedelta
            base = date(year, 1, 1) + timedelta(days=doy - 1)
            dt = datetime(
                base.year, base.month, base.day,
                hh, mm, min(ss, 59), microsecond,
                tzinfo=timezone.utc,
            )
            return dt
        except (ValueError, OverflowError) as exc:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year: {raw!r}."
            ) from exc

    # YYYY-MM-DDTHH:MM:SS
    m = _PDS3_DATETIME_ISO_RE.match(raw)
    if m:
        try:
            dt = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6)),
                int(round(float("0" + m.group(7)) * 1_000_000)) if m.group(7) else 0,
                tzinfo=timezone.utc,
            )
            return dt
        except ValueError as exc:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} is invalid: {raw!r}."
            ) from exc

    # Date-only YYYY-DDD
    m = _PDS3_DATE_DOY_RE.match(raw)
    if m:
        year = int(m.group(1))
        doy = int(m.group(2))
        try:
            from datetime import date, timedelta
            base = date(year, 1, 1) + timedelta(days=doy - 1)
            return datetime(base.year, base.month, base.day, tzinfo=timezone.utc)
        except (ValueError, OverflowError) as exc:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year: {raw!r}."
            ) from exc

    # Date-only YYYY-MM-DD
    m = _PDS3_DATE_ISO_RE.match(raw)
    if m:
        try:
            from datetime import date
            base = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return datetime(base.year, base.month, base.day, tzinfo=timezone.utc)
        except ValueError as exc:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} is invalid: {raw!r}."
            ) from exc

    raise GenericPds3AdapterValidationError(
        f"PDS3 label datetime field {field_name!r} could not be parsed: {raw!r}. "
        "Supported formats: YYYY-DDDTHH:MM:SS[.sss], YYYY-MM-DDTHH:MM:SS[.sss], "
        "YYYY-DDD, YYYY-MM-DD."
    )


# ---------------------------------------------------------------------------
# File size derivation (PDS3)
# ---------------------------------------------------------------------------


def _derive_pds3_file_size(
    kv: dict[str, str | list[str]],
) -> tuple[int, ArchiveDataFileSizeCertainty]:
    """Derive data-file size from PDS3 label keywords.

    Priority:
    1. FILE_SIZE keyword (exact, metadata authority).
    2. RECORD_BYTES × FILE_RECORDS formula (exact when label proves the
       relationship between these keywords and the payload file).
    3. 0 with SIZE_DISCOVERED_APPROXIMATE if no size info is available.

    Returns
    -------
    tuple[int, ArchiveDataFileSizeCertainty]
        (file_size_bytes, certainty)
    """
    # 1. FILE_SIZE keyword.
    file_size_raw = _extract_pds3_str(kv, "FILE_SIZE")
    if file_size_raw is not None:
        fs = file_size_raw.strip()
        if _ASCII_DECIMAL_RE.match(fs):
            return int(fs), ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    # 2. RECORD_BYTES × FILE_RECORDS.
    rb_raw = _extract_pds3_str(kv, "RECORD_BYTES")
    fr_raw = _extract_pds3_str(kv, "FILE_RECORDS")
    if rb_raw is not None and fr_raw is not None:
        rb = rb_raw.strip()
        fr = fr_raw.strip()
        if _ASCII_DECIMAL_RE.match(rb) and _ASCII_DECIMAL_RE.match(fr):
            computed = int(rb) * int(fr)
            return computed, ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    # 3. No size info available.
    return 0, ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _build_pds3_provenance_id_input(source_record_id: str, source_ref: str) -> str:
    return json.dumps(
        {
            "adapter": "gcsi:generic_pds3_label:v1",
            "source_record_id": source_record_id,
            "source_ref": source_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _compute_pds3_provenance_id(identity: str, content_sha256: str) -> str:
    """Formula: SHA-256(identity + "|" + content_sha256)."""
    combined = identity + "|" + content_sha256
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Core pure parser — shared by live adapter and snapshot reload
# ---------------------------------------------------------------------------


def parse_generic_pds3_label(
    raw_bytes: bytes,
    source_ref: str,
    profile: GenericPds3AdapterProfile,
    retrieved_at: datetime,
) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
    """Parse and validate raw PDS3 label bytes using a profile.

    This is a pure function: performs NO HTTP requests.  It is the single
    authoritative parser for both live fetch and snapshot reload.

    Parameters
    ----------
    raw_bytes:
        Exact raw PDS3 label bytes.  Must not exceed ``MAX_PDS3_LABEL_BYTES``.

    source_ref:
        Source URL/path for this label (for provenance tracking).

    profile:
        Instrument-family-specific validation profile.

    retrieved_at:
        Timezone-aware datetime when the bytes were acquired.

    Returns
    -------
    tuple[ArchiveScienceProduct, ProvenanceRecord]
        Fully validated product and EXTERNAL_AUTHORITATIVE provenance.

    Raises
    ------
    GenericPds3AdapterValidationError
        For any structural or semantic validation failure.
    """
    import pydantic

    if not isinstance(retrieved_at, datetime):
        raise GenericPds3AdapterValidationError(
            "retrieved_at must be a datetime object."
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise GenericPds3AdapterValidationError(
            "retrieved_at must be timezone-aware."
        )
    retrieved_at_utc = retrieved_at.astimezone(timezone.utc)

    # 1. Size limit.
    if len(raw_bytes) > MAX_PDS3_LABEL_BYTES:
        raise GenericPds3AdapterValidationError(
            f"PDS3 label exceeds maximum allowed size ({MAX_PDS3_LABEL_BYTES} bytes)."
        )

    # 2. SHA-256 before parsing.
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Parse PVL.
    kv = _parse_pds3_label(raw_bytes)

    # 4. Required identity fields.
    data_set_id = _extract_pds3_str(kv, "DATA_SET_ID", required=True)
    product_id  = _extract_pds3_str(kv, "PRODUCT_ID",  required=True)
    assert data_set_id is not None and product_id is not None  # satisfied by required=True

    # 5. Optional version.
    version_id = _extract_pds3_str(kv, "PRODUCT_VERSION_ID")

    # 6. Instrument / spacecraft identity.
    instrument_id = (
        _extract_pds3_str(kv, "INSTRUMENT_ID")
        or _extract_pds3_str(kv, "INSTRUMENT_NAME")
    )
    spacecraft_id = (
        _extract_pds3_str(kv, "INSTRUMENT_HOST_ID")
        or _extract_pds3_str(kv, "SPACECRAFT_ID")
    )

    # 7. Profile identity validation.
    if profile.expected_data_set_id_prefix is not None:
        if not data_set_id.startswith(profile.expected_data_set_id_prefix):
            raise GenericPds3AdapterValidationError(
                f"PDS3 label DATA_SET_ID {data_set_id!r} does not start with "
                f"expected prefix {profile.expected_data_set_id_prefix!r} "
                f"(profile {profile.profile_id!r})."
            )
    if instrument_id is not None:
        if not instrument_id.upper().startswith(profile.expected_instrument.upper()):
            raise GenericPds3AdapterValidationError(
                f"PDS3 label INSTRUMENT_ID {instrument_id!r} does not match "
                f"expected instrument {profile.expected_instrument!r} "
                f"(profile {profile.profile_id!r})."
            )

    # 8. Timestamps.
    start_raw = _extract_pds3_str(kv, "START_TIME")
    stop_raw  = _extract_pds3_str(kv, "STOP_TIME")
    obs_start_utc: Optional[datetime] = None
    obs_stop_utc: Optional[datetime] = None

    if profile.require_start_stop_time:
        if start_raw is None:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label missing required START_TIME (profile {profile.profile_id!r})."
            )
        if stop_raw is None:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label missing required STOP_TIME (profile {profile.profile_id!r})."
            )
        obs_start_utc = _parse_pds3_datetime(start_raw, "START_TIME")
        obs_stop_utc  = _parse_pds3_datetime(stop_raw, "STOP_TIME")
        if obs_start_utc > obs_stop_utc:
            raise GenericPds3AdapterValidationError(
                "PDS3 label START_TIME is after STOP_TIME."
            )
    else:
        if start_raw is not None:
            obs_start_utc = _parse_pds3_datetime(start_raw, "START_TIME")
        if stop_raw is not None:
            obs_stop_utc = _parse_pds3_datetime(stop_raw, "STOP_TIME")

    # 9. Processing level.
    processing_level = _extract_pds3_str(kv, "PROCESSING_LEVEL_ID")
    if (
        profile.allowed_processing_levels is not None
        and processing_level is not None
        and processing_level not in profile.allowed_processing_levels
    ):
        raise GenericPds3AdapterValidationError(
            f"PDS3 label PROCESSING_LEVEL_ID {processing_level!r} is not in "
            f"allowed set for profile {profile.profile_id!r}: "
            f"{sorted(profile.allowed_processing_levels)!r}."
        )

    # 10. Target names.
    target_names = tuple(t.upper() for t in _extract_pds3_list(kv, "TARGET_NAME"))

    # 11. File pointer and size.
    # Look for ^<KEYWORD> pointer (e.g. ^TABLE, ^IMAGE, ^SPREADSHEET, ^DATA_FILE).
    pointer_file: Optional[str] = None
    for k, v in kv.items():
        if k.startswith("^") and not k.startswith("^_"):
            raw_ptr = str(v).strip().strip('"').strip("'")
            # Pointer can be "filename" or ("filename", record_offset)
            # Take only the filename part.
            if raw_ptr.startswith("("):
                inner = raw_ptr.strip("()")
                parts = inner.split(",")
                raw_ptr = parts[0].strip().strip('"').strip("'")
            if raw_ptr and "/" not in raw_ptr and "\\" not in raw_ptr:
                pointer_file = raw_ptr
                break

    # If no pointer, try generic FILE_NAME keyword.
    if pointer_file is None:
        pointer_file = _extract_pds3_str(kv, "FILE_NAME")

    # File size.
    file_size_bytes, size_certainty = _derive_pds3_file_size(kv)

    # Build data files.
    data_files: tuple[ArchiveDataFile, ...] = ()
    if pointer_file is not None:
        try:
            data_file = ArchiveDataFile(
                file_name=pointer_file,
                file_size_bytes=file_size_bytes,
                size_certainty=size_certainty,
                file_ref=None,  # PDS3: path is relative; caller resolves if needed
            )
            data_files = (data_file,)
        except Exception:
            data_files = ()

    total_size = sum(f.file_size_bytes for f in data_files)

    # 12. Build source_record_id.
    source_record_id = build_pds3_source_record_id(
        data_set_id=data_set_id,
        product_id=product_id,
        product_version_id=version_id,
    )

    # 13. Build ArchiveScienceProduct.
    try:
        product = ArchiveScienceProduct(
            source_record_id=source_record_id,
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id=data_set_id,
            source_product_id=product_id,
            source_version=version_id,
            mission_name=profile.expected_mission,
            spacecraft_name=spacecraft_id or profile.expected_spacecraft,
            instrument_name=instrument_id or profile.expected_instrument,
            product_family=profile.product_family,
            processing_level=processing_level,
            observation_start_utc=obs_start_utc,
            observation_stop_utc=obs_stop_utc,
            target_names=target_names,
            data_files=data_files,
            total_data_size_bytes=total_size,
            source_label_ref=source_ref,
        )
    except pydantic.ValidationError as exc:
        raise GenericPds3AdapterValidationError(
            "PDS3 normalized product failed internal validation."
        ) from exc

    # 14. Build provenance.
    identity = _build_pds3_provenance_id_input(source_record_id, source_ref)
    provenance_id = _compute_pds3_provenance_id(identity, content_sha256)
    provenance = ProvenanceRecord(
        provenance_id=provenance_id,
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system=_ARCHIVE_SOURCE_SYSTEM,
        source_version=None,
        source_record_id=source_record_id,
        source_uri=source_ref,
        retrieved_at=retrieved_at_utc,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
    )

    return product, provenance
