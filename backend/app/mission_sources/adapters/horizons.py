"""GCSI Phase 6D-A — JPL Horizons Geometry Source Adapter.

This module implements the ``HorizonsAdapter``: a lower-level source adapter
that fetches and validates spacecraft-to-Earth geometric state vectors from
the NASA/JPL Horizons API.

Architecture position
---------------------
::

    HistoricalReplayProvider          [future]
              |
         ReplayAssembler              [future]
          /          \\
 HorizonsGeometry   PDS Products
    Adapter          Adapter [future]
       |
       ↓
validated external facts

This adapter is NOT a ``BaseMissionSourceProvider`` subclass.  It does NOT:

- Create a Scenario
- Modify runtime state (state.py)
- Affect RF, BER, SNR, goodput, scheduling, evaluation, AI, or simulation

It ONLY:

1. Constructs a minimal well-formed Horizons VECTORS request.
2. Fetches it via HTTPS (one request per call).
3. Validates HTTP transport semantics.
4. Validates Horizons payload semantics.
5. Parses the $$SOE/$$EOE vector table.
6. Normalizes output into :class:`HorizonsGeometry`.
7. Produces an ``EXTERNAL_AUTHORITATIVE`` :class:`ProvenanceRecord`.
8. Returns a :class:`HorizonsGeometryResult`.

Security notes
--------------
- The JPL endpoint URL is fixed; callers cannot supply an arbitrary base URL
  (prevents SSRF).
- Only numeric SPK target IDs are accepted; no Horizons command syntax.
- Earth geocenter CENTER is fixed at ``'500@399'`` for Phase 6D-A.
- Protocol parameters are owned entirely by this adapter.
- Raw response content, request URLs, and raw Horizons error text are NOT
  exposed in public exception messages.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from math import isfinite, isnan
from typing import Callable, Optional

import httpx

from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)

from .horizons_models import (
    HorizonsGeometry,
    HorizonsGeometryCapture,
    HorizonsGeometryRequest,
    HorizonsGeometryResult,
)


# ---------------------------------------------------------------------------
# Adapter-specific typed errors
# ---------------------------------------------------------------------------


class HorizonsAdapterError(Exception):
    """Base class for all JPL Horizons adapter failures.

    Catch this class to handle any Horizons-specific error.
    Catch the subclasses to distinguish availability from validation.
    """


class HorizonsUnavailableError(HorizonsAdapterError, MissionSourceUnavailableError):
    """Horizons service is unreachable or returned an availability error.

    Raised for:
    - Network timeouts and connection failures (httpx.TimeoutException,
      httpx.ConnectError, httpx.RequestError)
    - HTTP 5xx server errors
    - HTTP 429 rate-limiting
    """


class HorizonsValidationError(HorizonsAdapterError, MissionSourceValidationError):
    """Horizons returned a well-formed response that fails domain validation.

    Raised for:
    - HTTP 4xx client errors
    - Horizons ``error`` field present in JSON response
    - Wrong API signature source or version
    - Missing or malformed JSON payload
    - Oversized response body
    - Malformed $$SOE/$$EOE table
    - Invalid or non-finite geometry values
    - Returned ephemeris epoch does not match requested epoch
    - Returned target body ID does not match requested target
    - Returned center body ID does not match expected Earth geocenter (399)
    - Missing, duplicate, or malformed identity header lines
    """


# ---------------------------------------------------------------------------
# Protocol constants  (adapter-owned — NOT configurable by callers)
# ---------------------------------------------------------------------------

_HORIZONS_ENDPOINT: str = "https://ssd.jpl.nasa.gov/api/horizons.api"

_EXPECTED_SIGNATURE_SOURCE: str = "NASA/JPL Horizons API"
_EXPECTED_SIGNATURE_VERSION: str = "1.3"

# Earth geocenter CENTER code — fixed for Phase 6D-A.
_EARTH_CENTER: str = "500@399"

# Maximum accepted raw response body size (1 MiB).
_MAX_RESPONSE_BYTES: int = 1 * 1024 * 1024  # 1 MiB

# HTTP request timeout (seconds).
_HTTP_TIMEOUT: float = 30.0

# VEC_TABLE=6 CSV column indices for the semantic data row.
# When CSV_FORMAT=YES, VEC_TABLE=6, VEC_DELTA_T=NO, TIME_DIGITS=FRACSEC,
# the semantic row is:
#   JDTDB, Calendar Date (UT), LT, RG, RR[, <trailing empty from Horizons comma>]
# Semantic indices (0-based, after stripping trailing empty cell):
#   0 = JDTDB  (Julian Date, TDB timescale — finite positive float)
#   1 = Calendar Date (UT, TIME_TYPE=UT output)
#   2 = LT  (one-way light-time, seconds)
#   3 = RG  (range, km)
#   4 = RR  (range-rate, km/s)
_IDX_JDTDB: int = 0
_IDX_CAL_DATE: int = 1
_IDX_LIGHT_TIME: int = 2
_IDX_RANGE: int = 3
_IDX_RANGE_RATE: int = 4
_EXPECTED_SEMANTIC_COLUMNS: int = 5

# VEC_TABLE=3 has 11 columns (JDTDB, Date, X, Y, Z, VX, VY, VZ, LT, RG, RR).
# We use this count to detect and reject table-3-shaped rows.
_VEC_TABLE_3_COLUMN_COUNT: int = 11

# Expected center body ID for CENTER='500@399' (Earth geocenter).
# The numeric ID after the '@' in the CENTER code.
_EXPECTED_CENTER_BODY_ID: int = 399

# Pattern to extract the FINAL parenthesized signed-integer body ID from a
# Horizons identity header value.
# Examples:
#   "Juno (spacecraft) (-61)"              -> -61
#   "Juno (spacecraft) (-61) {source: x}"  -> -61
#   "Mars (499)"                           -> 499
#   "Earth (399)"                          -> 399
# Strategy: find ALL (...) tokens that contain only a signed integer, then
# take the LAST one.  This handles names that contain non-numeric parentheses
# (e.g. "(spacecraft)") and optional trailing content such as "{source: ...}".
_BODY_ID_IN_PARENS_RE = re.compile(r"\((-?\d+)\)")

# Horizons A.D. calendar-date pattern produced under:
#   TIME_TYPE=UT, TLIST_TYPE=CAL, CAL_TYPE=GREGORIAN, TIME_DIGITS=FRACSEC
# Expected form: "A.D. YYYY-Mon-DD HH:MM:SS.ffffff"
# Microseconds: 6 fractional digits from FRACSEC.
_HORIZONS_CAL_DATE_RE = re.compile(
    r"^A\.D\.\s+"
    r"(\d{4})-([A-Za-z]{3})-(\d{2})\s+"
    r"(\d{2}):(\d{2}):(\d{2})\.(\d+)$"
)

_MONTH_ABBR_TO_NUM: dict[str, int] = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# ---------------------------------------------------------------------------
# Production clock (injectable for tests)
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Provenance identity helpers
# ---------------------------------------------------------------------------


def _build_canonical_query_identity(
    request: HorizonsGeometryRequest,
) -> str:
    """Return a deterministic JSON string representing canonical query identity.

    The identity is determined by: target SPK ID, normalized UTC epoch,
    Earth center, and the fixed protocol settings that affect output.
    All fixed adapter-owned protocol parameters are included so that any
    protocol change is reflected in the provenance_id.
    """
    epoch_str = request.epoch_utc.strftime("%Y-%b-%d %H:%M:%S.%f")
    identity: dict = {
        "cal_type": "GREGORIAN",
        "center": _EARTH_CENTER,
        "csv_format": "YES",
        "ephem_type": "VECTORS",
        "out_units": "KM-S",
        "ref_plane": "FRAME",
        "ref_system": "ICRF",
        "target_spk_id": request.target_spk_id,
        "time_digits": "FRACSEC",
        "time_type": "UT",
        "tlist_epoch_utc": epoch_str,
        "tlist_type": "CAL",
        "vec_corr": "NONE",
        "vec_delta_t": "NO",
        "vec_table": "6",
    }
    # Sorted keys for determinism.
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _compute_provenance_id(canonical_identity: str, content_sha256: str) -> str:
    """Compute a deterministic provenance_id from query identity + response hash.

    Formula:
        SHA-256(canonical_identity + "|" + content_sha256)

    Returns the hex digest string.
    """
    combined = canonical_identity + "|" + content_sha256
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Epoch → Horizons TLIST timestamp
# ---------------------------------------------------------------------------

_MONTH_ABBR = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _epoch_to_tlist(epoch: datetime) -> str:
    """Format a UTC-normalized datetime as a Horizons TLIST calendar string.

    Uses the pattern: ``YYYY-Mon-DD HH:MM:SS.ffffff``
    Example: ``2016-Jul-04 00:00:00.000000``

    The epoch must be UTC-normalized before calling this helper.
    """
    month_abbr = _MONTH_ABBR[epoch.month]
    return (
        f"{epoch.year}-{month_abbr}-{epoch.day:02d} "
        f"{epoch.hour:02d}:{epoch.minute:02d}:{epoch.second:02d}.{epoch.microsecond:06d}"
    )


# ---------------------------------------------------------------------------
# Calendar-date parsing and epoch verification
# ---------------------------------------------------------------------------


def _parse_horizons_cal_date(cal_date_str: str) -> datetime:
    """Parse a Horizons A.D. calendar-date string to an aware UTC datetime.

    Expected format under TIME_TYPE=UT, TIME_DIGITS=FRACSEC:
        ``A.D. YYYY-Mon-DD HH:MM:SS.ffffff``

    The returned datetime is timezone-aware UTC with microsecond precision.
    If Horizons returns more than 6 fractional digits, only the first 6 are
    used (deterministic truncation to microsecond precision).

    Raises HorizonsValidationError if the string does not match the expected
    pattern or contains an unrecognised month abbreviation.
    """
    m = _HORIZONS_CAL_DATE_RE.match(cal_date_str.strip())
    if m is None:
        raise HorizonsValidationError(
            "Horizons calendar-date field does not match expected "
            "A.D. YYYY-Mon-DD HH:MM:SS.f+ format."
        )
    year_s, mon_s, day_s, hr_s, min_s, sec_s, frac_s = m.groups()

    month_num = _MONTH_ABBR_TO_NUM.get(mon_s.capitalize())
    if month_num is None:
        raise HorizonsValidationError(
            "Horizons calendar-date field contains unrecognised month abbreviation."
        )

    # Normalise fractional seconds to exactly 6 digits (microseconds).
    # Pad or truncate deterministically.
    frac_6 = (frac_s + "000000")[:6]
    microsecond = int(frac_6)

    try:
        return datetime(
            int(year_s), month_num, int(day_s),
            int(hr_s), int(min_s), int(sec_s), microsecond,
            tzinfo=timezone.utc,
        )
    except ValueError as exc:
        raise HorizonsValidationError(
            "Horizons calendar-date field contains an invalid date/time value."
        ) from exc


def _verify_returned_epoch(
    returned_epoch: datetime,
    requested_epoch: datetime,
) -> None:
    """Verify the returned Horizons epoch matches the requested UTC epoch.

    Both datetimes are compared at microsecond precision after normalising to
    UTC.  A mismatch is rejected with HorizonsValidationError.
    """
    # Both must be UTC-normalised before comparison.
    ret_utc = returned_epoch.astimezone(timezone.utc)
    req_utc = requested_epoch.astimezone(timezone.utc)

    if ret_utc != req_utc:
        raise HorizonsValidationError(
            "Horizons returned epoch does not match the requested UTC epoch."
        )


# ---------------------------------------------------------------------------
# $$SOE/$$EOE parser
# ---------------------------------------------------------------------------


def _parse_vector_table(
    result_text: str,
    request: HorizonsGeometryRequest,
) -> tuple[float, float, float]:
    """Parse the $$SOE/$$EOE vector table from a Horizons result string.

    Validates the Julian-time field (column 0) and the calendar-date field
    (column 1) against the requested epoch, then returns the geometry values.

    Returns:
        (one_way_light_time_s, range_km, range_rate_km_s)

    Raises:
        HorizonsValidationError: for all structural, numeric, or epoch
            mismatch failures.
    """
    # --- Require exactly ONE $$SOE and ONE $$EOE ---
    soe_count = result_text.count("$$SOE")
    eoe_count = result_text.count("$$EOE")

    if soe_count == 0:
        raise HorizonsValidationError(
            "Horizons response is missing the $$SOE table marker."
        )
    if eoe_count == 0:
        raise HorizonsValidationError(
            "Horizons response is missing the $$EOE table marker."
        )
    if soe_count > 1:
        raise HorizonsValidationError(
            "Horizons response contains duplicate $$SOE markers; "
            "expected exactly one ephemeris section."
        )
    if eoe_count > 1:
        raise HorizonsValidationError(
            "Horizons response contains duplicate $$EOE markers; "
            "expected exactly one ephemeris section."
        )

    soe_idx = result_text.index("$$SOE")
    eoe_idx = result_text.index("$$EOE")

    if eoe_idx <= soe_idx:
        raise HorizonsValidationError(
            "Horizons $$EOE appears before or at same position as $$SOE."
        )

    # Extract only the block between the markers (exclusive).
    table_block = result_text[soe_idx + len("$$SOE"):eoe_idx]

    # Use csv reader to robustly parse rows.
    reader = csv.reader(io.StringIO(table_block))
    data_rows: list[list[str]] = []
    for raw_row in reader:
        # Strip whitespace from all cells.
        stripped = [cell.strip() for cell in raw_row]
        # Remove a single trailing empty cell caused by Horizons' trailing comma.
        if stripped and stripped[-1] == "":
            stripped = stripped[:-1]
        # A valid data row must have a non-empty first cell.
        if stripped and stripped[0]:
            data_rows.append(stripped)

    if len(data_rows) == 0:
        raise HorizonsValidationError(
            "Horizons $$SOE/$$EOE block contains no data rows "
            "(expected exactly one for a single TLIST request)."
        )
    if len(data_rows) > 1:
        raise HorizonsValidationError(
            f"Horizons $$SOE/$$EOE block contains {len(data_rows)} data rows; "
            "expected exactly one for a single TLIST request."
        )

    row = data_rows[0]

    # Reject VEC_TABLE=3-shaped rows.
    if len(row) == _VEC_TABLE_3_COLUMN_COUNT:
        raise HorizonsValidationError(
            "Horizons data row has 11 columns matching VEC_TABLE=3 layout "
            "(JDTDB, Date, X, Y, Z, VX, VY, VZ, LT, RG, RR); "
            "this adapter requires VEC_TABLE=6 (JDTDB, Date, LT, RG, RR)."
        )

    # Require exactly 5 semantic columns.
    if len(row) != _EXPECTED_SEMANTIC_COLUMNS:
        raise HorizonsValidationError(
            f"Horizons VEC_TABLE=6 data row has {len(row)} semantic columns; "
            f"expected exactly {_EXPECTED_SEMANTIC_COLUMNS} "
            "(JDTDB, Calendar Date, LT, RG, RR)."
        )

    # --- Column 0: Julian-time value must be a finite positive float ---
    jd_value = _parse_finite_float(row[_IDX_JDTDB], "julian_date")
    if jd_value <= 0.0:
        raise HorizonsValidationError(
            "Horizons JDTDB value must be a positive Julian date."
        )

    # --- Column 1: Calendar Date — parse and verify against requested epoch ---
    returned_epoch = _parse_horizons_cal_date(row[_IDX_CAL_DATE])
    _verify_returned_epoch(returned_epoch, request.epoch_utc)

    # --- Columns 2-4: LT, RG, RR ---
    light_time_s = _parse_finite_float(row[_IDX_LIGHT_TIME], "one_way_light_time_s")
    range_km = _parse_finite_float(row[_IDX_RANGE], "range_km")
    range_rate = _parse_finite_float(row[_IDX_RANGE_RATE], "range_rate_km_s")

    return light_time_s, range_km, range_rate


def _parse_finite_float(cell: str, field_name: str) -> float:
    """Parse a CSV cell as a finite float; raise HorizonsValidationError if invalid."""
    try:
        value = float(cell)
    except (ValueError, TypeError):
        raise HorizonsValidationError(
            f"Horizons response field '{field_name}' could not be parsed as a number."
        )
    if isnan(value):
        raise HorizonsValidationError(
            f"Horizons response field '{field_name}' is NaN."
        )
    if not isfinite(value):
        raise HorizonsValidationError(
            f"Horizons response field '{field_name}' is infinite."
        )
    return value


# ---------------------------------------------------------------------------
# Response identity validation (target + center body IDs)
# ---------------------------------------------------------------------------


def _extract_header_body_id(
    result_text_before_soe: str,
    header_prefix: str,
) -> int:
    """Extract the final numeric body ID from a Horizons identity header line.

    Searches ``result_text_before_soe`` (the portion of the Horizons result
    text BEFORE ``$$SOE``) for lines starting with ``header_prefix``.

    Requirements:
    - Exactly one such line must be present.
    - The line must end with a parenthesized signed integer, e.g. ``(-61)``
      or ``(399)``, optionally followed by whitespace.
    - Names that themselves contain parentheses are handled correctly because
      only the FINAL ``(<integer>)`` token is matched.

    Parameters
    ----------
    result_text_before_soe:
        The header portion of the Horizons result text (before ``$$SOE``).

    header_prefix:
        The line-start label to search for, e.g. ``"Target body name:"``
        or ``"Center body name:"``.

    Returns
    -------
    int
        The extracted numeric body ID (may be negative for spacecraft).

    Raises
    ------
    HorizonsValidationError
        If the header line is missing, appears more than once, or the
        numeric body ID cannot be extracted.
    """
    matching_lines: list[str] = []
    for line in result_text_before_soe.splitlines():
        stripped = line.strip()
        if stripped.startswith(header_prefix):
            matching_lines.append(stripped)

    if len(matching_lines) == 0:
        raise HorizonsValidationError(
            f"Horizons response is missing the '{header_prefix}' identity header."
        )
    if len(matching_lines) > 1:
        raise HorizonsValidationError(
            f"Horizons response contains duplicate '{header_prefix}' identity headers; "
            "expected exactly one."
        )

    line_value = matching_lines[0][len(header_prefix):].strip()

    # Find ALL parenthesized signed-integer tokens; take the LAST one.
    all_matches = _BODY_ID_IN_PARENS_RE.findall(line_value)
    if not all_matches:
        raise HorizonsValidationError(
            f"Horizons '{header_prefix}' header does not contain a "
            "parenthesized numeric body identifier."
        )
    try:
        return int(all_matches[-1])
    except ValueError:
        raise HorizonsValidationError(
            f"Horizons '{header_prefix}' header contains a malformed numeric body identifier."
        )


def _verify_response_identity(
    result_text: str,
    request: HorizonsGeometryRequest,
) -> None:
    """Verify the returned Horizons target and center body IDs.

    Searches only the header portion of ``result_text`` (before ``$$SOE``)
    so that identity lines appearing only in the ephemeris data block are
    not mistakenly trusted.

    Verifies:
    - ``Target body name:`` → final numeric ID == ``int(request.target_spk_id)``
    - ``Center body name:`` → final numeric ID == ``_EXPECTED_CENTER_BODY_ID`` (399)

    Raises HorizonsValidationError on any mismatch, missing header, or
    duplicate header.
    """
    # Use only the pre-$$SOE portion for identity extraction.
    soe_idx = result_text.find("$$SOE")
    if soe_idx == -1:
        # $$SOE absence will be caught by the table parser; use full text here
        # so identity checks produce their own clear error if that path is hit.
        header_text = result_text
    else:
        header_text = result_text[:soe_idx]

    # --- Target body ID ---
    returned_target_id = _extract_header_body_id(header_text, "Target body name:")
    try:
        requested_target_id = int(request.target_spk_id)
    except ValueError:
        # The request model enforces numeric-only SPK IDs, so this is unreachable
        # in normal operation, but guard it anyway.
        raise HorizonsValidationError(
            "Horizons response target identity could not be compared: "
            "request target_spk_id is not a plain integer."
        )
    if returned_target_id != requested_target_id:
        raise HorizonsValidationError(
            "Horizons response target identity does not match the requested target."
        )

    # --- Center body ID ---
    returned_center_id = _extract_header_body_id(header_text, "Center body name:")
    if returned_center_id != _EXPECTED_CENTER_BODY_ID:
        raise HorizonsValidationError(
            "Horizons response center identity does not match the expected "
            "Earth geocenter (399)."
        )


# ---------------------------------------------------------------------------
# Pure shared raw-response validator
# ---------------------------------------------------------------------------


def _validate_horizons_raw_response(
    request: HorizonsGeometryRequest,
    raw_bytes: bytes,
    retrieved_at: datetime,
) -> HorizonsGeometryResult:
    """Validate raw Horizons response bytes and produce a HorizonsGeometryResult.

    This is the single authoritative validation boundary shared by:
    - the live HTTP fetch path (HorizonsAdapter.fetch_capture)
    - the offline snapshot reload path (HorizonsSnapshotStore.load)

    It performs NO HTTP requests.  The caller supplies the exact raw bytes
    and the acquisition timestamp.

    Parameters
    ----------
    request:
        The original :class:`HorizonsGeometryRequest` (target + epoch).

    raw_bytes:
        Exact raw HTTP response body bytes.  Must not exceed
        ``_MAX_RESPONSE_BYTES`` (1 MiB).

    retrieved_at:
        Timezone-aware datetime representing when the response was acquired.
        Must be a ``datetime`` instance with ``tzinfo`` set.
        Normalised to UTC for canonical provenance storage.

    Returns
    -------
    HorizonsGeometryResult
        Fully validated and normalised geometry result.

    Raises
    ------
    HorizonsValidationError
        For any validation failure: oversized body, malformed JSON, wrong
        signature, Horizons error field, malformed table, epoch mismatch,
        invalid geometry values, or invalid ``retrieved_at``.
    """
    # 0. Validate retrieved_at before doing any parsing work.
    if not isinstance(retrieved_at, datetime):
        raise HorizonsValidationError(
            "retrieved_at did not receive a datetime object."
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise HorizonsValidationError(
            "retrieved_at must be timezone-aware."
        )
    # Normalise to UTC for canonical provenance storage.
    retrieved_at_utc = retrieved_at.astimezone(timezone.utc)

    # 1. Enforce raw response size limit.
    if len(raw_bytes) > _MAX_RESPONSE_BYTES:
        raise HorizonsValidationError(
            f"JPL Horizons response body exceeds maximum allowed size "
            f"({_MAX_RESPONSE_BYTES} bytes)."
        )

    # 2. Hash the raw bytes (before any decoding can fail).
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Parse JSON — normalise UnicodeDecodeError as well as JSONDecodeError.
    try:
        payload = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HorizonsValidationError(
            "JPL Horizons response could not be decoded as valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise HorizonsValidationError(
            "JPL Horizons response JSON is not an object."
        )

    # 4. Validate signature.
    signature = payload.get("signature")
    if not isinstance(signature, dict):
        raise HorizonsValidationError(
            "JPL Horizons response is missing a valid 'signature' object."
        )

    sig_source = signature.get("source")
    if sig_source != _EXPECTED_SIGNATURE_SOURCE:
        raise HorizonsValidationError(
            "JPL Horizons response has unexpected API source in signature."
        )

    sig_version = signature.get("version")
    if sig_version != _EXPECTED_SIGNATURE_VERSION:
        raise HorizonsValidationError(
            "JPL Horizons response has unexpected API version in signature."
        )

    # 5. Fail closed if Horizons error field present.
    if "error" in payload:
        raise HorizonsValidationError(
            "JPL Horizons returned an error in the response payload."
        )

    # 6. Validate result text is present.
    result_text = payload.get("result")
    if not isinstance(result_text, str) or not result_text.strip():
        raise HorizonsValidationError(
            "JPL Horizons response is missing the 'result' text field."
        )

    # 6a. Verify target and center body identity from the response header.
    #     Must pass before any ephemeris data is accepted.
    _verify_response_identity(result_text, request)

    # 7. Parse vector table — validates JDTDB, calendar date epoch, LT/RG/RR.
    one_way_light_time_s, range_km, range_rate_km_s = _parse_vector_table(
        result_text, request
    )

    # 8. Domain validation of geometry values.
    if range_km <= 0.0:
        raise HorizonsValidationError(
            "Horizons range_km must be > 0."
        )
    if one_way_light_time_s <= 0.0:
        raise HorizonsValidationError(
            "Horizons one_way_light_time_s must be > 0."
        )

    # 9. Build HorizonsGeometry (model validators enforce finite/positive).
    geometry = HorizonsGeometry(
        target_spk_id=request.target_spk_id,
        center=_EARTH_CENTER,
        epoch_utc=request.epoch_utc,
        range_km=range_km,
        range_rate_km_s=range_rate_km_s,
        one_way_light_time_s=one_way_light_time_s,
        api_source=sig_source,
        api_version=sig_version,
    )

    # 10. Build deterministic provenance_id.
    canonical_identity = _build_canonical_query_identity(request)
    provenance_id = _compute_provenance_id(canonical_identity, content_sha256)

    # 11. Build ProvenanceRecord.
    provenance = ProvenanceRecord(
        provenance_id=provenance_id,
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system=_EXPECTED_SIGNATURE_SOURCE,
        source_version=sig_version,
        source_uri=_HORIZONS_ENDPOINT,
        observed_at=request.epoch_utc,
        retrieved_at=retrieved_at_utc,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
    )

    return HorizonsGeometryResult(
        request=request,
        geometry=geometry,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# HorizonsAdapter
# ---------------------------------------------------------------------------


class HorizonsAdapter:
    """Lower-level source adapter that fetches validated spacecraft-to-Earth
    geometry from the NASA/JPL Horizons API.

    Parameters
    ----------
    client:
        Optional injected ``httpx.Client``.  If ``None``, the adapter creates
        and owns its own client (and closes it when used as a context manager).
        If a client is injected, the adapter does NOT close it.

    clock:
        Optional callable returning an aware UTC ``datetime`` used as
        ``retrieved_at``.  Defaults to :func:`_utc_now`.  Tests must inject a
        fixed aware timestamp.  The adapter raises :class:`HorizonsValidationError`
        if the clock returns a naive datetime or a non-datetime value.

    Usage
    -----
    As a context manager (recommended for owned client)::

        with HorizonsAdapter() as adapter:
            result = adapter.fetch(request)

    With an injected client (caller manages client lifetime)::

        with httpx.Client(transport=mock_transport) as client:
            adapter = HorizonsAdapter(client=client)
            result = adapter.fetch(request)

    Notes
    -----
    - One :meth:`fetch` call = one HTTP GET request.  No retries, no
      concurrent requests.
    - The production endpoint URL is fixed and not configurable by callers.
    - Only numeric SPK target IDs are accepted.
    - Earth geocenter (CENTER=``'500@399'``) is fixed for Phase 6D-A.
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._injected_client = client is not None
        self._client: httpx.Client = client if client is not None else httpx.Client(
            timeout=_HTTP_TIMEOUT
        )
        self._clock: Callable[[], datetime] = clock if clock is not None else _utc_now

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "HorizonsAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally-owned HTTP client (if any).

        Does NOT close a caller-injected client.
        """
        if not self._injected_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch(self, request: HorizonsGeometryRequest) -> HorizonsGeometryResult:
        """Fetch and validate one geometry epoch from JPL Horizons.

        Parameters
        ----------
        request:
            Validated :class:`HorizonsGeometryRequest` specifying target and epoch.

        Returns
        -------
        HorizonsGeometryResult
            Normalized geometry fact with EXTERNAL_AUTHORITATIVE provenance.

        Raises
        ------
        HorizonsUnavailableError
            Network/transport failure or HTTP 5xx/429.

        HorizonsValidationError
            HTTP 4xx, malformed/invalid Horizons payload, geometry
            validation failure, or returned epoch mismatch.
        """
        return self.fetch_capture(request).result

    def fetch_capture(
        self, request: HorizonsGeometryRequest
    ) -> HorizonsGeometryCapture:
        """Fetch and validate one geometry epoch, returning raw bytes alongside the result.

        Performs exactly one HTTP request.  The returned
        :class:`HorizonsGeometryCapture` bundles the validated
        :class:`HorizonsGeometryResult` with the exact raw response bytes,
        enabling downstream snapshot creation without a second request.

        Parameters
        ----------
        request:
            Validated :class:`HorizonsGeometryRequest` specifying target and epoch.

        Returns
        -------
        HorizonsGeometryCapture
            Immutable container holding both the validated result and the
            exact raw HTTP response bytes.

        Raises
        ------
        HorizonsUnavailableError
            Network/transport failure or HTTP 5xx/429.

        HorizonsValidationError
            HTTP 4xx, malformed/invalid Horizons payload, geometry
            validation failure, or returned epoch mismatch.
        """
        params = self._build_params(request)
        raw_bytes = self._execute_request(params)
        retrieved_at = self._clock()
        result = _validate_horizons_raw_response(request, raw_bytes, retrieved_at)
        return HorizonsGeometryCapture(result=result, raw_response=raw_bytes)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_params(self, request: HorizonsGeometryRequest) -> dict[str, str]:
        """Build the fixed Horizons API query parameters for this request.

        Callers cannot override these parameters — the adapter owns the
        protocol settings.
        """
        tlist_value = _epoch_to_tlist(request.epoch_utc)
        return {
            "format": "json",
            "COMMAND": f"'{request.target_spk_id}'",
            "OBJ_DATA": "NO",
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "VECTORS",
            "CENTER": f"'{_EARTH_CENTER}'",
            "TLIST": f"'{tlist_value}'",
            "TLIST_TYPE": "CAL",
            "TIME_TYPE": "UT",
            "TIME_DIGITS": "FRACSEC",
            "OUT_UNITS": "KM-S",
            "VEC_TABLE": "6",
            "VEC_CORR": "NONE",
            "VEC_DELTA_T": "NO",
            "CSV_FORMAT": "YES",
            "REF_SYSTEM": "ICRF",
            "REF_PLANE": "FRAME",
            "CAL_TYPE": "GREGORIAN",
        }

    def _execute_request(self, params: dict[str, str]) -> bytes:
        """Perform exactly one HTTP GET to the fixed Horizons endpoint.

        Returns the raw response body bytes.

        Raises HorizonsUnavailableError or HorizonsValidationError on failure.
        """
        try:
            response = self._client.get(_HORIZONS_ENDPOINT, params=params)
        except httpx.TimeoutException as exc:
            raise HorizonsUnavailableError(
                "Request to JPL Horizons timed out."
            ) from exc
        except httpx.RequestError as exc:
            raise HorizonsUnavailableError(
                "Network error while contacting JPL Horizons."
            ) from exc

        # HTTP transport validation.
        status = response.status_code
        if status == 200:
            return response.content
        # ALL HTTP 5xx are availability failures.
        if status == 429 or (500 <= status < 600):
            raise HorizonsUnavailableError(
                f"JPL Horizons returned HTTP {status}."
            )
        if 400 <= status < 500:
            raise HorizonsValidationError(
                f"JPL Horizons returned HTTP {status}."
            )
        # Any other unexpected non-200 status — fail closed.
        raise HorizonsValidationError(
            f"JPL Horizons returned unexpected HTTP status {status}."
        )
