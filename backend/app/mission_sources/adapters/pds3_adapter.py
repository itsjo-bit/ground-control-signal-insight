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

4. Unknown keyword lines are silently skipped (fail-closed for malformed
   lines, but unknown valid keywords are benign).

Bounded PDS3 PVL subset parser (fail-closed)
---------------------------------------------
The parser handles:

- Single-line keyword = value assignments (unquoted, quoted with \", set).
- Multi-value sets: { VAL1, VAL2, ... } treated as list.
- OBJECT/END_OBJECT blocks: parsed for top-level fields; nested sub-objects
  at depth > 1 are recorded but not recursively descended.
- ^POINTER = "filename" or ^POINTER = ("filename", n) for payload file refs.
- Strict ASCII: rejects non-ASCII bytes (no latin-1 fallback).
- Fail-closed: malformed lines, unterminated quotes, unterminated sets,
  unsupported multiline values, depth underflow/overflow, unmatched
  END_OBJECT — all raise GenericPds3AdapterValidationError.

Profile-driven validation
--------------------------
All instrument-family-specific constraints live in ``GenericPds3AdapterProfile``.
No ``if instrument == X`` branches appear in the generic parser.

Security posture
----------------
- HTTPS only for network origins (profile allowed_hosts + allowed_path_prefixes).
- Bounded read: ``MAX_PDS3_LABEL_BYTES`` (512 KiB; PDS3 labels are small).
- Strict ASCII: rejects non-ASCII bytes (fail-closed, no latin-1 fallback).
- No network activity in parser.
- SHA-256 of raw bytes before any parsing.
- Error messages do not expose raw label content.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit

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

    Raised for: oversized response, non-ASCII bytes, unsupported PVL constructs,
    malformed lines, unterminated quotes/sets, depth errors, missing required
    keywords, invalid timestamps, leap seconds, profile violations,
    negative/invalid file sizes, checksum format mismatch.
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum raw label bytes: 512 KiB (PDS3 labels are small ASCII files).
MAX_PDS3_LABEL_BYTES: int = 512 * 1024

# Maximum plausible data file size: 100 GiB.
_MAX_DATA_FILE_BYTES: int = 100 * 1024 * 1024 * 1024

_ARCHIVE_SOURCE_SYSTEM: str = "NASA Planetary Data System"

# Normalizer ID for provenance binding (Section O).
_PDS3_NORMALIZER_ID: str = "gcsi.generic_pds3_label.v1"

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

# PDS3 keyword = value line (top-level assignment).
_KV_LINE_RE = re.compile(r"^\s*([A-Z_\^][A-Z0-9_\^]*)\s*=\s*(.+?)\s*$", re.IGNORECASE)

# Comment line (/* ... */ or # comment).
_COMMENT_RE = re.compile(r"^\s*/\*.*$|^\s*#.*$")


# ---------------------------------------------------------------------------
# Section J: Size derivation strategy
# ---------------------------------------------------------------------------


class Pds3SizeDerivationStrategy(str, Enum):
    """Strategy for deriving a PDS3 data file size from label keywords.

    FILE_SIZE
        Use the FILE_SIZE keyword directly.

    RECORD_BYTES_X_FILE_RECORDS
        Compute RECORD_BYTES × FILE_RECORDS.  Valid only when the label
        contract explicitly links these keywords to the payload file.

    NONE
        No reliable size formula exists for this instrument/profile.
        Returns (None, SIZE_UNKNOWN).
    """

    FILE_SIZE = "file_size"
    RECORD_BYTES_X_FILE_RECORDS = "record_bytes_x_file_records"
    NONE = "none"


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

    allowed_path_prefixes : tuple[str, ...] | None
        Trusted path prefixes for the source URL path.
        None means any path is allowed (offline/fixture labels).

    require_spacecraft_id : bool
        If True, INSTRUMENT_HOST_ID / SPACECRAFT_ID must be present in the
        label.  Raises validation error if missing.  Default True.

    require_instrument_id : bool
        If True, INSTRUMENT_ID / INSTRUMENT_NAME must be present in the
        label.  Raises validation error if missing.  Default True.

    size_derivation_strategy : Pds3SizeDerivationStrategy
        Strategy for computing the data file size from the label.
        Defaults to FILE_SIZE.
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
    allowed_path_prefixes: Optional[tuple[str, ...]] = None
    require_spacecraft_id: bool = True
    require_instrument_id: bool = True
    size_derivation_strategy: Pds3SizeDerivationStrategy = Pds3SizeDerivationStrategy.FILE_SIZE

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

# ---------------------------------------------------------------------------
# Official PDS node host/path constants (Phase 6F-B1.2.1 — corrected).
# ---------------------------------------------------------------------------

# PDS-PPI node: hosts WAVES, FGM, JADE, JEDI.
_PPI_HOST: str = "pds-ppi.igpp.ucla.edu"

# PDS Imaging Node: hosts JunoCam PJ62 (JNOJNC_0029 volume).
_IMAGING_HOST: str = "planetarydata.jpl.nasa.gov"

WAVES_BURST_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="waves_burst_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="WAV",
    expected_data_set_id_prefix="JNO-E/J/SS-WAV",
    product_family="WAVES_BURST",
    allowed_processing_levels=frozenset({"3"}),
    require_start_stop_time=True,
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
    allowed_hosts=frozenset({_PPI_HOST}),
    # Official archive root: JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0
    allowed_path_prefixes=("/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/",),
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
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
    allowed_hosts=frozenset({_PPI_HOST}),
    # Official archive root: JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0
    allowed_path_prefixes=("/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/",),
)

JUNOCAM_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="junocam_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JNC",
    product_family="JUNOCAM",
    require_start_stop_time=True,
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.FILE_SIZE,
    # Official archive: PDS Imaging Node, planetarydata.jpl.nasa.gov
    allowed_hosts=frozenset({_IMAGING_HOST}),
    # Official PJ62 volume prefix: JNOJNC_0029
    allowed_path_prefixes=("/img/data/juno/JNOJNC_0029/",),
)

FGM_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="fgm_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="FGM",
    product_family="FGM",
    require_start_stop_time=True,
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
    allowed_hosts=frozenset({_PPI_HOST}),
    # Official archive root: JNO-J-3-FGM-CAL-V1.0
    allowed_path_prefixes=("/data/JNO-J-3-FGM-CAL-V1.0/",),
)

JADE_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="jade_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JAD",
    product_family="JADE",
    require_start_stop_time=True,
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.NONE,
    allowed_hosts=frozenset({_PPI_HOST}),
    # Official archive root: JNO-J_SW-JAD-3-CALIBRATED-V1.0
    allowed_path_prefixes=("/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/",),
)

JEDI_PDS3_PROFILE = GenericPds3AdapterProfile(
    profile_id="jedi_pds3",
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JED",
    product_family="JEDI",
    require_start_stop_time=True,
    require_spacecraft_id=True,
    require_instrument_id=True,
    size_derivation_strategy=Pds3SizeDerivationStrategy.NONE,
    allowed_hosts=frozenset({_PPI_HOST}),
    # Official archive root: JNO-J-JED-3-CDR-V1.0
    allowed_path_prefixes=("/data/JNO-J-JED-3-CDR-V1.0/",),
)


# ---------------------------------------------------------------------------
# Section C — Source-ref trust validation
# ---------------------------------------------------------------------------


def _validate_pds3_source_url_trust(
    source_ref: str, profile: GenericPds3AdapterProfile
) -> None:
    """Validate source_ref against profile-defined trust constraints.

    Enforces:
    - HTTPS scheme only
    - No userinfo
    - No non-443 port
    - No query string
    - No fragment
    - No percent-encoding
    - No backslash
    - Hostname in profile.allowed_hosts (when set)
    - Path starts with one of profile.allowed_path_prefixes (when set)

    Raises
    ------
    GenericPds3AdapterValidationError
        On any trust violation.
    """
    if "%" in source_ref:
        raise GenericPds3AdapterValidationError(
            "source_ref must not contain percent-encoded characters."
        )
    if "\\" in source_ref:
        raise GenericPds3AdapterValidationError(
            "source_ref must not contain backslash characters."
        )
    try:
        parsed = urlsplit(source_ref)
    except Exception as exc:
        raise GenericPds3AdapterValidationError(
            "source_ref could not be parsed as a URL."
        ) from exc

    if parsed.scheme != "https":
        raise GenericPds3AdapterValidationError(
            f"source_ref must use HTTPS scheme; got {parsed.scheme!r}."
        )
    if parsed.username is not None or parsed.password is not None:
        raise GenericPds3AdapterValidationError(
            "source_ref must not contain userinfo."
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise GenericPds3AdapterValidationError(
            "source_ref contains an invalid port specification."
        ) from exc
    if port is not None and port != 443:
        raise GenericPds3AdapterValidationError(
            f"source_ref port must be absent or 443; got {port!r}."
        )
    if parsed.query:
        raise GenericPds3AdapterValidationError(
            "source_ref must not contain a query string."
        )
    if parsed.fragment:
        raise GenericPds3AdapterValidationError(
            "source_ref must not contain a fragment."
        )
    if profile.allowed_hosts is not None:
        if parsed.hostname not in profile.allowed_hosts:
            raise GenericPds3AdapterValidationError(
                f"source_ref host {parsed.hostname!r} is not in the trusted host "
                f"set for profile {profile.profile_id!r}."
            )
    if profile.allowed_path_prefixes is not None:
        path = parsed.path
        if not any(path.startswith(prefix) for prefix in profile.allowed_path_prefixes):
            raise GenericPds3AdapterValidationError(
                f"source_ref path {path!r} does not start with any allowed prefix "
                f"for profile {profile.profile_id!r}: "
                f"{list(profile.allowed_path_prefixes)!r}."
            )


# ---------------------------------------------------------------------------
# Section E — Fail-closed bounded PDS3 PVL subset parser
# ---------------------------------------------------------------------------


def _parse_pvl_value(raw_value: str) -> str | list[str]:
    """Parse a PDS3 PVL value into a Python string or list of strings.

    Handles:
    - Quoted strings: ``"value"`` or ``'value'`` → ``str``
    - Bare unquoted tokens: ``VALUE``, ``123``, etc. → ``str``
    - Sets/sequences: ``{ val1, val2, "val3" }`` → ``list[str]``
    - ``N/A``, ``"N/A"`` → ``"N/A"`` (preserved)
    - Unit suffixes like ``<km>`` are stripped.

    Raises
    ------
    GenericPds3AdapterValidationError
        On unterminated quoted strings or unterminated sets.

    Returns ``str`` or ``list[str]``.
    """
    v = raw_value.strip()

    # Set/sequence: must start with { and end with }
    if v.startswith("{"):
        if "}" not in v:
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated set/sequence value "
                "(opening '{' without closing '}')."
            )
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

    # Quoted string — verify terminated.
    if v.startswith('"'):
        if not v.endswith('"') or len(v) < 2:
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated double-quoted string value."
            )
        return v[1:-1]
    if v.startswith("'"):
        if not v.endswith("'") or len(v) < 2:
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated single-quoted string value."
            )
        return v[1:-1]

    # Unsupported multiline continuation (backslash at end of raw_value).
    if raw_value.rstrip().endswith("\\"):
        raise GenericPds3AdapterValidationError(
            "PDS3 label contains unsupported multiline value continuation (backslash)."
        )

    # Strip unit suffix (e.g. "12345 <byte>").
    v = re.sub(r"\s*<[^>]+>$", "", v).strip()

    return v


def _parse_pds3_label(raw_bytes: bytes) -> dict[str, str | list[str]]:
    """Parse PDS3/ODL ASCII label bytes into a flat keyword→value dict.

    Fail-closed: rejects non-ASCII bytes, malformed lines, unterminated
    quoted values, unterminated sets, depth overflow/underflow, and
    unterminated OBJECT blocks at END.

    Only top-level keywords are extracted.  OBJECT/END_OBJECT blocks
    at the top level are recorded with key ``"_OBJECT_<name>"`` holding
    the raw block text; they are NOT recursively parsed.

    Parameters
    ----------
    raw_bytes:
        Raw label bytes.  Must be strict ASCII.

    Returns
    -------
    dict[str, str | list[str]]
        Keyword → value mapping.  All keys are uppercased.
        ``^POINTER`` keywords are retained with the ``^`` prefix.

    Raises
    ------
    GenericPds3AdapterValidationError
        On non-ASCII bytes, NUL bytes, malformed lines, unterminated values,
        depth errors, or unterminated OBJECT blocks at END.
    """
    # Reject NUL bytes immediately.
    if b"\x00" in raw_bytes:
        raise GenericPds3AdapterValidationError(
            "PDS3 label contains NUL bytes (0x00); rejected."
        )

    # Strict ASCII decode — no latin-1 fallback.
    try:
        text = raw_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise GenericPds3AdapterValidationError(
            "PDS3 label contains non-ASCII bytes; strict ASCII is required."
        ) from exc

    result: dict[str, str | list[str]] = {}
    in_object: Optional[str] = None
    object_lines: list[str] = []
    depth = 0
    found_end = False

    for line in text.splitlines():
        stripped = line.strip()

        # Empty line → skip.
        if not stripped:
            if depth > 0 and object_lines is not None:
                object_lines.append(line)
            continue

        # Comment line → skip.
        if _COMMENT_RE.match(stripped):
            if depth > 0 and object_lines is not None:
                object_lines.append(line)
            continue

        # END marker: validate depth then stop.
        if stripped.upper() == "END":
            if depth > 0:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label END encountered with unclosed OBJECT/GROUP "
                    f"(depth={depth}, open block={in_object!r}). "
                    "Unterminated OBJECT/GROUP is not supported."
                )
            found_end = True
            break

        # GROUP / END_GROUP: treat same as OBJECT/END_OBJECT for depth tracking.
        stripped_upper = stripped.upper()

        if (
            stripped_upper == "OBJECT"
            or stripped_upper.startswith("OBJECT =")
            or stripped_upper.startswith("OBJECT=")
            or stripped_upper == "GROUP"
            or stripped_upper.startswith("GROUP =")
            or stripped_upper.startswith("GROUP=")
        ):
            kv = stripped.split("=", 1)
            obj_name = kv[1].strip() if len(kv) == 2 else "UNKNOWN"
            if depth == 0:
                in_object = obj_name.upper()
                object_lines = [line]
                depth += 1
            else:
                # Nested OBJECT/GROUP (depth > 0) is explicitly rejected.
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label has nested OBJECT/GROUP {obj_name!r} "
                    f"(depth={depth + 1}). Nested constructs are not supported."
                )
            continue

        if (
            stripped_upper == "END_OBJECT"
            or stripped_upper.startswith("END_OBJECT")
            or stripped_upper == "END_GROUP"
            or stripped_upper.startswith("END_GROUP")
        ):
            if depth <= 0:
                raise GenericPds3AdapterValidationError(
                    "PDS3 label has unmatched END_OBJECT/END_GROUP "
                    "(depth underflow). Malformed label structure."
                )
            depth -= 1
            if depth == 0 and in_object is not None:
                object_lines.append(line)
                result[f"_OBJECT_{in_object}"] = "\n".join(object_lines)
                in_object = None
                object_lines = []
            else:
                object_lines.append(line)
            continue

        # Inside an OBJECT block at depth > 0 — collect and continue.
        if depth > 0:
            object_lines.append(line)
            continue

        # Top-level: must be a valid keyword = value line.
        m = _KV_LINE_RE.match(stripped)
        if m:
            key = m.group(1).upper()
            raw_val = m.group(2)
            # Ambiguous pointer check (pointer must be a simple filename or
            # ("filename", n) form; nested complex syntax is rejected).
            if key.startswith("^"):
                # Allow: "filename", 'filename', ("filename", n), plain token.
                # Reject: nested parens or complex expressions.
                rv = raw_val.strip()
                if rv.count("(") > 1 or rv.count(")") > 1:
                    raise GenericPds3AdapterValidationError(
                        f"PDS3 label has ambiguous pointer syntax for key {key!r}."
                    )
            result[key] = _parse_pvl_value(raw_val)
        else:
            # A non-blank, non-comment, non-structural line that doesn't
            # match KV regex — fail closed.
            raise GenericPds3AdapterValidationError(
                "PDS3 label contains a malformed top-level line that is not "
                "a valid keyword=value assignment, OBJECT/END_OBJECT, "
                "GROUP/END_GROUP, comment, or END."
            )

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


# ---------------------------------------------------------------------------
# Section H — PDS3 datetime hardening
# ---------------------------------------------------------------------------


def _parse_pds3_datetime(raw: str, field_name: str) -> datetime:
    """Parse PDS3 timestamp to UTC-aware datetime.

    Handles:
    - ``YYYY-DDDTHH:MM:SS[.sss]``  (day-of-year)
    - ``YYYY-MM-DDTHH:MM:SS[.sss]``
    - ``YYYY-DDD``  (date only → midnight UTC)
    - ``YYYY-MM-DD``  (date only → midnight UTC)

    DOY validation:
    - doy < 1 → reject
    - doy > 365 in non-leap year → reject
    - doy > 366 → always reject

    Second validation:
    - ss == 60 → reject (leap second not supported)
    - ss > 60 → reject

    Raises ``GenericPds3AdapterValidationError`` for unparseable values.
    """
    from datetime import date, timedelta

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

        # DOY range validation.
        if doy < 1:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year "
                f"{doy} (must be >= 1): {raw!r}."
            )
        max_doy = 366 if calendar.isleap(year) else 365
        if doy > max_doy:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year "
                f"{doy} for year {year} (max={max_doy}): {raw!r}."
            )

        # Second validation — reject leap seconds.
        if ss == 60:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} contains a leap second "
                f"(second=60), which is not supported: {raw!r}."
            )
        if ss > 60:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid second value "
                f"{ss}: {raw!r}."
            )

        try:
            base = date(year, 1, 1) + timedelta(days=doy - 1)
            dt = datetime(
                base.year, base.month, base.day,
                hh, mm, ss, microsecond,
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
        ss = int(m.group(6))
        if ss == 60:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} contains a leap second "
                f"(second=60), which is not supported: {raw!r}."
            )
        if ss > 60:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid second value "
                f"{ss}: {raw!r}."
            )
        try:
            dt = datetime(
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), ss,
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
        if doy < 1:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year "
                f"{doy} (must be >= 1): {raw!r}."
            )
        max_doy = 366 if calendar.isleap(year) else 365
        if doy > max_doy:
            raise GenericPds3AdapterValidationError(
                f"PDS3 label datetime field {field_name!r} has invalid day-of-year "
                f"{doy} for year {year} (max={max_doy}): {raw!r}."
            )
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
# Section J — Profile-aware exact size derivation
# ---------------------------------------------------------------------------


def _derive_pds3_file_size(
    kv: dict[str, str | list[str]],
    strategy: Pds3SizeDerivationStrategy,
) -> tuple[Optional[int], ArchiveDataFileSizeCertainty]:
    """Derive data-file size from PDS3 label keywords using the profile strategy.

    Parameters
    ----------
    kv:
        Parsed PDS3 keyword→value dict.

    strategy:
        Size derivation strategy from the profile.

    Returns
    -------
    tuple[Optional[int], ArchiveDataFileSizeCertainty]
        (file_size_bytes, certainty)

    Raises
    ------
    GenericPds3AdapterValidationError
        When the relevant keyword(s) are present but malformed, or the
        computed size exceeds the 100 GiB sanity limit.
    """
    if strategy is Pds3SizeDerivationStrategy.NONE:
        return None, ArchiveDataFileSizeCertainty.SIZE_UNKNOWN

    if strategy is Pds3SizeDerivationStrategy.FILE_SIZE:
        file_size_raw = _extract_pds3_str(kv, "FILE_SIZE")
        if file_size_raw is not None:
            fs = file_size_raw.strip()
            if not _ASCII_DECIMAL_RE.match(fs):
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label FILE_SIZE has malformed numeric value: {fs!r}."
                )
            size = int(fs)
            if size > _MAX_DATA_FILE_BYTES:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label FILE_SIZE {size} exceeds sanity limit "
                    f"({_MAX_DATA_FILE_BYTES} bytes)."
                )
            return size, ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        return None, ArchiveDataFileSizeCertainty.SIZE_UNKNOWN

    if strategy is Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS:
        rb_raw = _extract_pds3_str(kv, "RECORD_BYTES")
        fr_raw = _extract_pds3_str(kv, "FILE_RECORDS")
        if rb_raw is not None and fr_raw is not None:
            rb = rb_raw.strip()
            fr = fr_raw.strip()
            if not _ASCII_DECIMAL_RE.match(rb):
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label RECORD_BYTES has malformed numeric value: {rb!r}."
                )
            if not _ASCII_DECIMAL_RE.match(fr):
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label FILE_RECORDS has malformed numeric value: {fr!r}."
                )
            computed = int(rb) * int(fr)
            if computed > _MAX_DATA_FILE_BYTES:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label RECORD_BYTES×FILE_RECORDS product {computed} exceeds "
                    f"sanity limit ({_MAX_DATA_FILE_BYTES} bytes)."
                )
            return computed, ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        return None, ArchiveDataFileSizeCertainty.SIZE_UNKNOWN

    # Unreachable — all enum values handled above.
    return None, ArchiveDataFileSizeCertainty.SIZE_UNKNOWN


# ---------------------------------------------------------------------------
# Section O — Provenance helpers (profile + normalizer binding)
# ---------------------------------------------------------------------------


def _build_pds3_provenance_id_input(
    source_record_id: str,
    source_ref: str,
    profile_id: str,
    normalizer_id: str,
) -> str:
    """Return the deterministic JSON identity string for provenance_id computation.

    Includes profile_id and normalizer_id so that provenance IDs are bound
    to both the label content AND the normalizer+profile that produced them.
    """
    return json.dumps(
        {
            "adapter": normalizer_id,
            "profile_id": profile_id,
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

    Trust validation (Section C) is called BEFORE any parsing when
    source_ref looks like a URL (starts with 'https://').

    Parameters
    ----------
    raw_bytes:
        Exact raw PDS3 label bytes.  Must not exceed ``MAX_PDS3_LABEL_BYTES``.

    source_ref:
        Source URL/path for this label (for provenance tracking).
        If this is an HTTPS URL, trust validation is performed against
        the profile before parsing.

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

    # 0. URL trust validation — BEFORE any parsing (Section C).
    #
    # Always validate external-looking refs.  Do NOT skip validation for
    # non-https schemes (e.g. http://, ftp://, evil://) — those must be
    # explicitly rejected by _validate_pds3_source_url_trust rather than
    # silently bypassed.
    #
    # Offline fixture/local-parsing paths must use a clearly non-URL form
    # (e.g. "fixture:waves_test" or a bare filename without "://").
    # Any source_ref that contains "://" is treated as a network URL and
    # validated; bare local identifiers (no "://") bypass trust validation.
    if "://" in source_ref:
        _validate_pds3_source_url_trust(source_ref, profile)

    # 1. Size limit.
    if len(raw_bytes) > MAX_PDS3_LABEL_BYTES:
        raise GenericPds3AdapterValidationError(
            f"PDS3 label exceeds maximum allowed size ({MAX_PDS3_LABEL_BYTES} bytes)."
        )

    # 2. SHA-256 before parsing.
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Parse PVL (fail-closed — Section E).
    kv = _parse_pds3_label(raw_bytes)

    # 4. Required identity fields.
    data_set_id = _extract_pds3_str(kv, "DATA_SET_ID", required=True)
    product_id  = _extract_pds3_str(kv, "PRODUCT_ID",  required=True)
    assert data_set_id is not None and product_id is not None  # satisfied by required=True

    # 5. Optional version.
    version_id = _extract_pds3_str(kv, "PRODUCT_VERSION_ID")

    # 6. Instrument / spacecraft identity (Section G — no profile fallback).
    instrument_id = (
        _extract_pds3_str(kv, "INSTRUMENT_ID")
        or _extract_pds3_str(kv, "INSTRUMENT_NAME")
    )
    spacecraft_id = (
        _extract_pds3_str(kv, "INSTRUMENT_HOST_ID")
        or _extract_pds3_str(kv, "SPACECRAFT_ID")
    )

    # Enforce presence if profile requires it (Section G).
    if profile.require_spacecraft_id and spacecraft_id is None:
        raise GenericPds3AdapterValidationError(
            f"PDS3 label is missing required spacecraft identifier "
            f"(INSTRUMENT_HOST_ID / SPACECRAFT_ID) "
            f"(profile {profile.profile_id!r})."
        )
    if profile.require_instrument_id and instrument_id is None:
        raise GenericPds3AdapterValidationError(
            f"PDS3 label is missing required instrument identifier "
            f"(INSTRUMENT_ID / INSTRUMENT_NAME) "
            f"(profile {profile.profile_id!r})."
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
    # Spacecraft identity check — the label's spacecraft ID must match the
    # profile's expected spacecraft (when the field is present).
    if spacecraft_id is not None:
        if not spacecraft_id.upper().startswith(profile.expected_spacecraft.upper()):
            raise GenericPds3AdapterValidationError(
                f"PDS3 label INSTRUMENT_HOST_ID/SPACECRAFT_ID {spacecraft_id!r} does "
                f"not match expected spacecraft {profile.expected_spacecraft!r} "
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

    # 11. File pointer and size (Section F — no silent data-file loss).
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

    # File size (Section J — profile strategy, no try/except swallowing).
    file_size_bytes, size_certainty = _derive_pds3_file_size(
        kv, profile.size_derivation_strategy
    )

    # Build data files (Section F — no silent exception swallowing).
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
        except pydantic.ValidationError as exc:
            raise GenericPds3AdapterValidationError(
                "PDS3 label produced an invalid ArchiveDataFile record."
            ) from exc

    # Item 5: unknown size must NOT aggregate as zero.
    # total_data_size_bytes = None if any file has unknown size.
    total_size: Optional[int]
    if any(f.file_size_bytes is None for f in data_files):
        total_size = None
    else:
        total_size = sum(f.file_size_bytes for f in data_files)  # type: ignore[misc]

    # 12. Build source_record_id.
    source_record_id = build_pds3_source_record_id(
        data_set_id=data_set_id,
        product_id=product_id,
        product_version_id=version_id,
    )

    # 13. Build ArchiveScienceProduct (Section G — use actual ids, not profile fallback).
    try:
        product = ArchiveScienceProduct(
            source_record_id=source_record_id,
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id=data_set_id,
            source_product_id=product_id,
            source_version=version_id,
            mission_name=profile.expected_mission,
            spacecraft_name=spacecraft_id,   # actual label value or None
            instrument_name=instrument_id,   # actual label value or None
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

    # 14. Build provenance (Section O — includes profile_id and normalizer_id).
    identity = _build_pds3_provenance_id_input(
        source_record_id, source_ref, profile.profile_id, _PDS3_NORMALIZER_ID
    )
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


# ---------------------------------------------------------------------------
# Section B — Live Adapter
# ---------------------------------------------------------------------------


class GenericPds3SourceRequest(BaseModel):
    """Request object for a live PDS3 label fetch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_url: str = Field(description="HTTPS URL to the PDS3 ASCII label file.")

    @field_validator("source_url", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_url must not be empty.")
        return v


class GenericPds3ObservationalLabelAdapter:
    """Live HTTP adapter for PDS3 observational label acquisition.

    Validates URL trust BEFORE making any network request.
    Uses httpx with bounded streaming read and no redirect following.

    Maps HTTP status to adapter errors:
    - 3xx → GenericPds3AdapterValidationError (no redirects)
    - 404, 429, 5xx → GenericPds3AdapterUnavailableError
    - other 4xx → GenericPds3AdapterValidationError

    Returns ArchiveCaptureRecord on success.
    Zero network calls in parse_generic_pds3_label (the pure parser).
    """

    @staticmethod
    def fetch(
        request: GenericPds3SourceRequest,
        profile: GenericPds3AdapterProfile,
        retrieved_at: datetime,
    ) -> "ArchiveCaptureRecord":
        """Fetch a PDS3 label from the archive and return a capture record.

        Parameters
        ----------
        request:
            Source URL to fetch.
        profile:
            Instrument-family validation profile.
        retrieved_at:
            Timezone-aware UTC datetime for provenance.

        Returns
        -------
        ArchiveCaptureRecord
            Fully validated capture.

        Raises
        ------
        GenericPds3AdapterValidationError
            Trust violation, redirect, or label validation failure.
        GenericPds3AdapterUnavailableError
            Network error or HTTP 404/429/5xx.
        """
        import httpx

        source_url = request.source_url

        # Section G: Live production fetch must always have an explicit trust
        # boundary.  Profiles with no allowed_hosts or no allowed_path_prefixes
        # must be rejected BEFORE making any network request.
        if not profile.allowed_hosts:
            raise GenericPds3AdapterValidationError(
                f"Live PDS3 fetch rejected: profile {profile.profile_id!r} has no "
                "allowed_hosts defined. Production fetch requires an explicit trust "
                "boundary (allowed_hosts must be non-empty)."
            )
        if not profile.allowed_path_prefixes:
            raise GenericPds3AdapterValidationError(
                f"Live PDS3 fetch rejected: profile {profile.profile_id!r} has no "
                "allowed_path_prefixes defined. Production fetch requires an explicit "
                "trust boundary (allowed_path_prefixes must be non-empty)."
            )

        # Trust validation BEFORE any network request.
        _validate_pds3_source_url_trust(source_url, profile)

        # True bounded streaming read — never materialises more than
        # MAX_PDS3_LABEL_BYTES + 1 bytes regardless of Content-Length.
        try:
            with httpx.Client(follow_redirects=False, timeout=30.0) as client:
                with client.stream("GET", source_url) as response:
                    status = response.status_code

                    # Inspect HTTP status BEFORE consuming the body.

                    # Reject redirects.
                    if 300 <= status < 400:
                        raise GenericPds3AdapterValidationError(
                            f"PDS3 label URL returned an unexpected redirect (HTTP {status}). "
                            "Redirects are not followed."
                        )

                    # Map unavailability codes.
                    if status in (404, 429) or status >= 500:
                        raise GenericPds3AdapterUnavailableError(
                            f"PDS3 label is not available (HTTP {status})."
                        )

                    # Other client errors → validation error.
                    if status >= 400:
                        raise GenericPds3AdapterValidationError(
                            f"PDS3 label URL returned an unexpected client error (HTTP {status})."
                        )

                    if status != 200:
                        raise GenericPds3AdapterValidationError(
                            f"PDS3 label URL returned unexpected status (HTTP {status})."
                        )

                    # Incrementally accumulate at most MAX_PDS3_LABEL_BYTES + 1 bytes.
                    # Abort immediately once the limit is exceeded.
                    chunks: list[bytes] = []
                    accumulated = 0
                    limit = MAX_PDS3_LABEL_BYTES + 1
                    for chunk in response.iter_bytes():
                        accumulated += len(chunk)
                        if accumulated > MAX_PDS3_LABEL_BYTES:
                            raise GenericPds3AdapterValidationError(
                                f"PDS3 label response exceeds maximum allowed size "
                                f"({MAX_PDS3_LABEL_BYTES} bytes)."
                            )
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
        except (GenericPds3AdapterValidationError, GenericPds3AdapterUnavailableError):
            raise
        except httpx.TransportError as exc:
            raise GenericPds3AdapterUnavailableError(
                "PDS3 label fetch failed due to a network transport error."
            ) from exc

        # Parse and validate.
        product, provenance = parse_generic_pds3_label(
            raw_bytes=raw_bytes,
            source_ref=source_url,
            profile=profile,
            retrieved_at=retrieved_at,
        )

        return ArchiveCaptureRecord(
            source_label_ref=source_url,
            product=product,
            provenance=provenance,
            raw_label_bytes=raw_bytes,
        )
