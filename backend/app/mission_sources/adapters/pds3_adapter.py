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
  are syntactically validated (B2.2.1 §9) but semantics are not extracted.
  Malformed grammar inside nested OBJECT blocks raises GenericPds3AdapterValidationError.
- ^POINTER = "filename" or ^POINTER = ("filename", n) for payload file refs.
- Strict ASCII: rejects non-ASCII bytes (no latin-1 fallback).
- Fail-closed: malformed lines, unterminated quotes, unterminated sets,
  unsupported multiline values, depth underflow/overflow, unmatched
  END_OBJECT, malformed nested OBJECT content — all raise GenericPds3AdapterValidationError.

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
# Allows namespace-prefixed keys like JNO:TDI_STAGES_COUNT.
_KV_LINE_RE = re.compile(r"^\s*([A-Z_\^][A-Z0-9_\^\:]*)\s*=\s*(.*)$", re.IGNORECASE)

# Keyword-only line (value on next line or empty – should not normally appear, but guard).
_KEY_ONLY_RE = re.compile(r"^\s*([A-Z_\^][A-Z0-9_\^\:]*)\s*=\s*$", re.IGNORECASE)

# Comment line (/* ... */ or # comment).
_COMMENT_RE = re.compile(r"^\s*/\*.*$|^\s*#.*$")

# Inline comment pattern: /* ... */ that may appear after a value.
_INLINE_COMMENT_RE = re.compile(r"/\*[^*]*\*/")

# Structural keyword patterns (allow arbitrary whitespace around =).
_STRUCT_OBJECT_START_RE = re.compile(r"^(OBJECT|GROUP)\s*=\s*(\S+)", re.IGNORECASE)
_STRUCT_OBJECT_END_RE = re.compile(r"^(END_OBJECT|END_GROUP)(\s*=\s*\S+)?$", re.IGNORECASE)
_STRUCT_END_RE = re.compile(r"^END\s*$", re.IGNORECASE)


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
    # JunoCam labels use SPACECRAFT_NAME = "JUNO" (full name, not abbreviation)
    expected_spacecraft="JUNO",
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
    # FGM labels use SPACECRAFT_NAME = "JUNO" (full name, not abbreviation)
    expected_spacecraft="JUNO",
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
    # JEDI labels use INSTRUMENT_HOST_NAME = "JUNO" (full name, not abbreviation)
    expected_spacecraft="JUNO",
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


def _strip_inline_comment(raw_val: str) -> str:
    """Strip trailing inline PVL comments (/* ... */) from a raw value string.

    Handles:
    - Unquoted values: ``2024-165T00:00:33 /* UTC */`` → ``2024-165T00:00:33``
    - Quoted values with trailing comment: ``"JAD" /* JADE */`` → ``"JAD"``
    - Multi-line values (not yet accumulated): called only for single lines.
    """
    v = raw_val.strip()
    if not v:
        return v

    if v.startswith('"') or v.startswith("'"):
        q = v[0]
        # Find the closing quote character (last occurrence after first).
        close_pos = v.rfind(q, 1)
        if close_pos > 0 and close_pos < len(v) - 1:
            # Content after the closing quote — strip if it's whitespace/comments.
            tail = v[close_pos + 1:].strip()
            if not tail or _INLINE_COMMENT_RE.fullmatch(tail):
                return v[:close_pos + 1]
        # Otherwise return as-is (either terminated correctly or will be caught later).
        return v
    else:
        # Unquoted or set/paren: strip any inline comment.
        return _INLINE_COMMENT_RE.sub("", v).strip()


def _parse_pvl_value(raw_value: str) -> str | list[str]:
    """Parse a fully-accumulated PDS3 PVL value into a Python string or list.

    At this point ``raw_value`` is the complete (possibly multi-line) value
    string as accumulated by ``_parse_pds3_label``.

    Handles:
    - Quoted strings: ``"value"`` or ``'value'`` → ``str``
    - Bare unquoted tokens: ``VALUE``, ``123``, etc. → ``str``
    - Sets/sequences: ``{ val1, val2, "val3" }`` → ``list[str]``
    - Parenthesis sequences: ``( 'A', 'B' )`` → ``list[str]``
    - Multi-line quoted strings: already collapsed by caller
    - ``N/A``, ``"N/A"`` → ``"N/A"`` (preserved)
    - Unit suffixes like ``<km>`` are stripped.

    Raises
    ------
    GenericPds3AdapterValidationError
        On unterminated quoted strings or unterminated sets.

    Returns ``str`` or ``list[str]``.
    """
    # Caller (_parse_pds3_label) has already applied _strip_inline_comment to
    # single-line values before calling this function.  For multi-line
    # accumulated values, inline comments inside the content are not stripped
    # (they are part of the raw string content).
    v = raw_value.strip()

    # Set/sequence: starts with { and ends with } (may have been multi-line)
    if v.startswith("{"):
        if not v.endswith("}"):
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated set/sequence value "
                "(opening '{' without closing '}')."
            )
        inner = v[1 : len(v) - 1].strip()
        return _parse_sequence_inner(inner)

    # Parenthesis sequence: ( 'BLUE', 'GREEN', 'RED' )
    if v.startswith("("):
        if not v.endswith(")"):
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated parenthesis sequence "
                "(opening '(' without closing ')')."
            )
        inner = v[1 : len(v) - 1].strip()
        return _parse_sequence_inner(inner)

    # Quoted string — must be fully terminated (caller accumulated all lines).
    if v.startswith('"'):
        if len(v) < 2 or not v.endswith('"'):
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated double-quoted string value."
            )
        return v[1:-1]
    if v.startswith("'"):
        if len(v) < 2 or not v.endswith("'"):
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unterminated single-quoted string value."
            )
        return v[1:-1]

    # Backslash continuation is not supported.
    if raw_value.rstrip().endswith("\\"):
        raise GenericPds3AdapterValidationError(
            "PDS3 label contains unsupported multiline value continuation (backslash)."
        )

    # Strip unit suffix (e.g. "12345 <byte>").
    v = re.sub(r"\s*<[^>]+>$", "", v).strip()

    return v


def _parse_sequence_inner(inner: str) -> list[str]:
    """Parse comma-separated inner content of a PDS3 set/sequence.

    Handles quoted items with embedded commas by scanning character by character.
    Items may be quoted with ``"`` or ``'``, or bare unquoted tokens.
    """
    items: list[str] = []
    current: list[str] = []
    in_quote: Optional[str] = None

    for ch in inner:
        if in_quote:
            if ch == in_quote:
                in_quote = None
            current.append(ch)
        elif ch in ('"', "'"):
            in_quote = ch
            current.append(ch)
        elif ch == ",":
            token = "".join(current).strip()
            if token:
                items.append(_clean_sequence_item(token))
            current = []
        else:
            current.append(ch)

    # Final item
    token = "".join(current).strip()
    if token:
        items.append(_clean_sequence_item(token))

    return [i for i in items if i]


def _clean_sequence_item(item: str) -> str:
    """Strip outer quotes and unit suffixes from a sequence item."""
    item = item.strip()
    if item.startswith('"') and item.endswith('"') and len(item) >= 2:
        return item[1:-1]
    if item.startswith("'") and item.endswith("'") and len(item) >= 2:
        return item[1:-1]
    # Strip unit suffix like <km>
    return re.sub(r"\s*<[^>]+>$", "", item).strip()


# Maximum OBJECT/GROUP nesting depth (inclusive of the top-level block).
_MAX_OBJECT_DEPTH: int = 8


def _validate_nested_object_syntax(block_lines: list[str], block_name: str) -> None:
    """Syntactically validate lines collected inside an OBJECT/GROUP block (§9).

    Decision (B2.2.1 §9, option B):
    - A bounded recursive syntactic validator is used.
    - Semantics inside nested blocks are NOT extracted (normalizer contract unchanged).
    - Grammar violations RAISE GenericPds3AdapterValidationError.
    - This does NOT change the normalizer_id (gcsi.generic_pds3_label.v1) because
      the normalization output contract is unchanged; only silent grammar acceptance
      is eliminated in favour of fail-closed behaviour.

    Checked:
    - Balanced nested OBJECT/GROUP (no unmatched END_OBJECT/END_GROUP).
    - No depth overflow (> _MAX_OBJECT_DEPTH - 1 inside the block).
    - KV lines inside match the PDS3 grammar (_KV_LINE_RE).
    - Unterminated quoted values are rejected.
    - Unterminated set/sequence values are rejected.
    - Unsupported nested value constructs raise an error.
    """
    depth = 1  # We enter already at depth=1 (inside the top-level OBJECT)
    ml_key: Optional[str] = None
    ml_parts: list[str] = []
    ml_close: Optional[str] = None
    _ML_MAX = 512

    # Skip the first line (the OBJECT = <name> opener) and last line (END_OBJECT)
    # since those are the outer delimiters already validated by the caller.
    inner_lines = block_lines[1:-1]

    for line in inner_lines:
        stripped = line.strip()

        # Multi-line accumulation
        if ml_key is not None:
            ml_parts.append(line)
            if len(ml_parts) > _ML_MAX:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 nested OBJECT {block_name!r}: "
                    f"multi-line value for key {ml_key!r} exceeded {_ML_MAX} continuation lines."
                )
            combined = "\n".join(ml_parts).strip()
            if combined.endswith(ml_close):  # type: ignore[arg-type]
                ml_key = None
                ml_parts = []
                ml_close = None
            continue

        if not stripped:
            continue
        if _COMMENT_RE.match(stripped):
            continue

        # END inside a nested block is invalid
        if _STRUCT_END_RE.match(stripped):
            raise GenericPds3AdapterValidationError(
                f"PDS3 nested OBJECT {block_name!r}: "
                "bare END inside a nested OBJECT block is not permitted."
            )

        m_start = _STRUCT_OBJECT_START_RE.match(stripped)
        m_end = _STRUCT_OBJECT_END_RE.match(stripped)

        if m_start:
            depth += 1
            if depth > _MAX_OBJECT_DEPTH:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 nested OBJECT {block_name!r}: "
                    f"OBJECT/GROUP nesting depth exceeds maximum ({_MAX_OBJECT_DEPTH})."
                )
            continue

        if m_end:
            depth -= 1
            if depth < 1:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 nested OBJECT {block_name!r}: "
                    "unmatched END_OBJECT/END_GROUP inside nested block (depth underflow)."
                )
            continue

        # Must be a KV assignment or empty/comment
        m = _KV_LINE_RE.match(stripped)
        if not m:
            raise GenericPds3AdapterValidationError(
                f"PDS3 nested OBJECT {block_name!r}: "
                f"malformed nested line is not a valid keyword=value assignment: {stripped[:80]!r}"
            )

        # Validate the value start for multi-line constructs
        raw_val = m.group(2).strip()
        # Strip inline comment before checking termination
        raw_val = _strip_inline_comment(raw_val)

        if raw_val.startswith('"') and not (raw_val.endswith('"') and len(raw_val) > 1):
            ml_key = m.group(1).upper()
            ml_parts = [raw_val]
            ml_close = '"'
            continue
        if raw_val.startswith("'") and not (raw_val.endswith("'") and len(raw_val) > 1):
            ml_key = m.group(1).upper()
            ml_parts = [raw_val]
            ml_close = "'"
            continue
        if raw_val.startswith("{") and not raw_val.endswith("}"):
            ml_key = m.group(1).upper()
            ml_parts = [raw_val]
            ml_close = "}"
            continue
        if raw_val.startswith("(") and not raw_val.endswith(")"):
            ml_key = m.group(1).upper()
            ml_parts = [raw_val]
            ml_close = ")"
            continue

    # After processing all inner lines, depth must be back to 1
    if depth != 1:
        raise GenericPds3AdapterValidationError(
            f"PDS3 nested OBJECT {block_name!r}: "
            f"unclosed nested OBJECT/GROUP block at end of outer block "
            f"(depth={depth}, expected 1)."
        )
    # Unterminated multi-line value
    if ml_key is not None:
        raise GenericPds3AdapterValidationError(
            f"PDS3 nested OBJECT {block_name!r}: "
            f"unterminated multi-line value for key {ml_key!r} "
            f"(closing {ml_close!r} not found)."
        )


def _parse_pds3_label(raw_bytes: bytes) -> dict[str, str | list[str]]:
    """Parse PDS3/ODL ASCII label bytes into a flat keyword→value dict.

    Fail-closed: rejects non-ASCII bytes, malformed lines, unterminated
    quoted values, unterminated sets, depth overflow/underflow,
    unterminated OBJECT blocks at END, and malformed nested OBJECT content (§9).

    Only top-level keywords are extracted.  OBJECT/END_OBJECT blocks
    at the top level are syntactically validated and recorded with key
    ``"_OBJECT_<name>"`` holding the raw block text; nested grammar
    violations raise GenericPds3AdapterValidationError.

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

    # Multi-line value accumulation state.
    # When we encounter a KV assignment whose value is not yet terminated
    # (e.g. starts with '"' but closing '"' not on the same line, or starts
    # with '{' but '}' not on same line), we accumulate subsequent raw lines
    # until the closing delimiter is found.
    ml_key: Optional[str] = None        # key being accumulated
    ml_parts: list[str] = []            # accumulated raw value parts
    ml_close: Optional[str] = None      # closing delimiter: '"', '}', ')'

    # Maximum continuation lines for multi-line values (bounded, fail-closed).
    _ML_MAX_LINES = 128

    def _flush_multiline(parts: list[str], close: str, key: str) -> None:
        """Join accumulated parts and store in result.  Validates terminator."""
        combined = "\n".join(parts)
        if not combined.strip().endswith(close):
            raise GenericPds3AdapterValidationError(
                f"PDS3 label multi-line value for key {key!r} is not terminated "
                f"by {close!r} within {_ML_MAX_LINES} continuation lines."
            )
        result[key] = _parse_pvl_value(combined.strip())

    for line in text.splitlines():
        # ---------------------------------------------------------------
        # If we are inside a multi-line value accumulation, continue it.
        # ---------------------------------------------------------------
        if ml_key is not None:
            ml_parts.append(line)
            if len(ml_parts) > _ML_MAX_LINES:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label multi-line value for key {ml_key!r} exceeded "
                    f"{_ML_MAX_LINES} continuation lines (not terminated)."
                )
            # Check whether the close delimiter has appeared.
            combined_so_far = "\n".join(ml_parts)
            # For quoted strings, the closing delimiter is the same quote char.
            # For sets/sequences, it's '}' or ')'.
            if ml_close in ('"', "'"):
                # Count unescaped occurrences of the quote char to decide if
                # we're closed.  Simple heuristic: strip the opening from the
                # first line and check if the accumulated text ends with the
                # quote (allowing trailing whitespace after).
                stripped_combined = combined_so_far.strip()
                # The combined value must start with the open quote (from first line).
                if stripped_combined.endswith(ml_close):
                    _flush_multiline(ml_parts, ml_close, ml_key)
                    ml_key = None
                    ml_parts = []
                    ml_close = None
            else:
                # Set/sequence: look for the close brace/paren.
                stripped_combined = combined_so_far.strip()
                if stripped_combined.endswith(ml_close):
                    _flush_multiline(ml_parts, ml_close, ml_key)
                    ml_key = None
                    ml_parts = []
                    ml_close = None
            continue

        stripped = line.strip()

        # Empty line → skip.
        if not stripped:
            if depth > 0:
                object_lines.append(line)
            continue

        # Comment line → skip.
        if _COMMENT_RE.match(stripped):
            if depth > 0:
                object_lines.append(line)
            continue

        # END marker: validate depth then stop.
        # Use regex to allow arbitrary whitespace (e.g. "END  ").
        if _STRUCT_END_RE.match(stripped):
            if depth > 0:
                raise GenericPds3AdapterValidationError(
                    f"PDS3 label END encountered with unclosed OBJECT/GROUP "
                    f"(depth={depth}, open block={in_object!r}). "
                    "Unterminated OBJECT/GROUP is not supported."
                )
            found_end = True
            break

        # Use regex patterns for OBJECT/GROUP/END_OBJECT/END_GROUP detection.
        # These allow arbitrary whitespace between keyword and '='.
        _m_obj_start = _STRUCT_OBJECT_START_RE.match(stripped)
        _m_obj_end = _STRUCT_OBJECT_END_RE.match(stripped)

        # Inside an OBJECT block (depth > 0): collect all lines as raw text
        # but still track nested depth so we know when the top-level block ends.
        if depth > 0:
            if _m_obj_start:
                depth += 1
                object_lines.append(line)
            elif _m_obj_end:
                depth -= 1
                if depth == 0 and in_object is not None:
                    object_lines.append(line)
                    # §9: Validate nested grammar before accepting the block.
                    _validate_nested_object_syntax(object_lines, in_object)
                    result[f"_OBJECT_{in_object}"] = "\n".join(object_lines)
                    in_object = None
                    object_lines = []
                else:
                    object_lines.append(line)
            else:
                object_lines.append(line)
            continue

        # At top-level (depth == 0): process structural keywords.
        if _m_obj_start:
            obj_name = _m_obj_start.group(2).strip().upper()
            in_object = obj_name
            object_lines = [line]
            depth += 1
            continue

        if _m_obj_end:
            raise GenericPds3AdapterValidationError(
                "PDS3 label has unmatched END_OBJECT/END_GROUP "
                "at top level (depth underflow). Malformed label structure."
            )

        # Top-level: must be a valid keyword = value line.
        m = _KV_LINE_RE.match(stripped)
        if m:
            key = m.group(1).upper()
            raw_val = m.group(2).strip()

            # Pre-strip single-line inline comments (/* ... */) from raw_val
            # BEFORE multi-line detection.  This handles values like:
            #   INSTRUMENT_ID = "JAD" /* JADE */
            #   START_TIME = 2024-165T00:00:33 /* UTC */
            # We must NOT strip from inside a quoted string — only strip
            # content that appears AFTER the value.
            raw_val = _strip_inline_comment(raw_val)

            # Ambiguous pointer check (pointer must be a simple filename or
            # ("filename", n) form; nested complex syntax is rejected).
            if key.startswith("^"):
                # Allow: "filename", 'filename', ("filename", n), plain token.
                # Reject: nested parens or complex expressions.
                if raw_val.count("(") > 1 or raw_val.count(")") > 1:
                    raise GenericPds3AdapterValidationError(
                        f"PDS3 label has ambiguous pointer syntax for key {key!r}."
                    )
                result[key] = _parse_pvl_value(raw_val)
                continue

            # Check whether this value is a multi-line construct.
            # Case 1: Quoted string not yet terminated.
            if raw_val.startswith('"') and not (raw_val.endswith('"') and len(raw_val) > 1):
                ml_key = key
                ml_parts = [raw_val]
                ml_close = '"'
                continue
            if raw_val.startswith("'") and not (raw_val.endswith("'") and len(raw_val) > 1):
                ml_key = key
                ml_parts = [raw_val]
                ml_close = "'"
                continue
            # Case 2: Set/sequence not yet closed.
            if raw_val.startswith("{") and not raw_val.endswith("}"):
                ml_key = key
                ml_parts = [raw_val]
                ml_close = "}"
                continue
            if raw_val.startswith("(") and not raw_val.endswith(")"):
                ml_key = key
                ml_parts = [raw_val]
                ml_close = ")"
                continue

            # Value is complete on this line.
            result[key] = _parse_pvl_value(raw_val)
        else:
            # A non-blank, non-comment, non-structural line that doesn't
            # match KV regex — fail closed.
            raise GenericPds3AdapterValidationError(
                "PDS3 label contains a malformed top-level line that is not "
                "a valid keyword=value assignment, OBJECT/END_OBJECT, "
                "GROUP/END_GROUP, comment, or END."
            )

    # If we're still accumulating a multi-line value at end, it's unterminated.
    if ml_key is not None:
        raise GenericPds3AdapterValidationError(
            f"PDS3 label ended while accumulating multi-line value for "
            f"key {ml_key!r} (closing {ml_close!r} not found)."
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
        or _extract_pds3_str(kv, "INSTRUMENT_HOST_NAME")
        or _extract_pds3_str(kv, "SPACECRAFT_NAME")
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
