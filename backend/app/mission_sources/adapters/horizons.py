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

# VEC_TABLE=6 column indices in the CSV data row.
# When CSV_FORMAT=YES and VEC_TABLE=6, the row format is:
#   JDTDB, Calendar Date (TDB), X, Y, Z, VX, VY, VZ, LT, RG, RR
# Indices (0-based):
#   0  = JDTDB
#   1  = Calendar Date (TDB)
#   2  = X
#   3  = Y
#   4  = Z
#   5  = VX
#   6  = VY
#   7  = VZ
#   8  = LT  (one-way light-time, seconds)
#   9  = RG  (range, km)
#   10 = RR  (range-rate, km/s)
_IDX_LIGHT_TIME: int = 8
_IDX_RANGE: int = 9
_IDX_RANGE_RATE: int = 10
_MIN_COLUMNS: int = 11


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
    """
    epoch_str = request.epoch_utc.strftime("%Y-%b-%d %H:%M:%S.%f")
    identity: dict = {
        "center": _EARTH_CENTER,
        "ephem_type": "VECTORS",
        "out_units": "KM-S",
        "target_spk_id": request.target_spk_id,
        "tlist_epoch_utc": epoch_str,
        "vec_corr": "NONE",
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
    Example: ``2016-07-04 00:00:00.000000``

    The epoch must be UTC-normalized before calling this helper.
    """
    month_abbr = _MONTH_ABBR[epoch.month]
    return (
        f"{epoch.year}-{month_abbr}-{epoch.day:02d} "
        f"{epoch.hour:02d}:{epoch.minute:02d}:{epoch.second:02d}.{epoch.microsecond:06d}"
    )


# ---------------------------------------------------------------------------
# $$SOE/$$EOE parser
# ---------------------------------------------------------------------------


def _parse_vector_table(result_text: str) -> tuple[float, float, float]:
    """Parse the $$SOE/$$EOE vector table from a Horizons result string.

    Returns:
        (one_way_light_time_s, range_km, range_rate_km_s)

    Raises:
        HorizonsValidationError: if markers are missing/wrong order, zero or
            multiple data rows, or numeric values are invalid.
    """
    soe_idx = result_text.find("$$SOE")
    eoe_idx = result_text.find("$$EOE")

    if soe_idx == -1:
        raise HorizonsValidationError(
            "Horizons response is missing the $$SOE table marker."
        )
    if eoe_idx == -1:
        raise HorizonsValidationError(
            "Horizons response is missing the $$EOE table marker."
        )
    if eoe_idx <= soe_idx:
        raise HorizonsValidationError(
            "Horizons $$EOE appears before or at same position as $$SOE."
        )

    # Extract only the block between the markers (exclusive).
    table_block = result_text[soe_idx + len("$$SOE"):eoe_idx]

    # Use csv reader to robustly parse rows.
    reader = csv.reader(io.StringIO(table_block))
    data_rows: list[list[str]] = []
    for row in reader:
        # Strip whitespace from all cells.
        stripped = [cell.strip() for cell in row]
        # A valid data row must have enough columns and a non-empty first cell.
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
    if len(row) < _MIN_COLUMNS:
        raise HorizonsValidationError(
            f"Horizons VEC_TABLE=6 data row has {len(row)} columns; "
            f"expected at least {_MIN_COLUMNS}."
        )

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
        if the clock returns a naive datetime.

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
            HTTP 4xx, malformed/invalid Horizons payload, or geometry
            validation failure.
        """
        params = self._build_params(request)
        raw_bytes = self._execute_request(params)
        return self._process_response(request, raw_bytes)

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
            "OUT_UNITS": "KM-S",
            "VEC_TABLE": "6",
            "VEC_CORR": "NONE",
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

        # Check response body size BEFORE decoding.
        raw_bytes = response.content
        if len(raw_bytes) > _MAX_RESPONSE_BYTES:
            raise HorizonsValidationError(
                f"JPL Horizons response body exceeds maximum allowed size "
                f"({_MAX_RESPONSE_BYTES} bytes)."
            )

        # HTTP transport validation.
        status = response.status_code
        if status == 200:
            return raw_bytes
        if status in (429, 500, 502, 503, 504):
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

    def _process_response(
        self,
        request: HorizonsGeometryRequest,
        raw_bytes: bytes,
    ) -> HorizonsGeometryResult:
        """Parse and validate the raw Horizons response bytes.

        Returns a fully assembled HorizonsGeometryResult.
        """
        # 1. Hash the raw bytes first (before any decoding can fail).
        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

        # 2. Get retrieved_at from the injected clock.
        retrieved_at = self._clock()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise HorizonsValidationError(
                "Injected clock returned a naive datetime; retrieved_at must be "
                "timezone-aware."
            )

        # 3. Parse JSON.
        try:
            payload = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            raise HorizonsValidationError(
                "JPL Horizons response is not valid JSON."
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

        # 7. Parse vector table.
        one_way_light_time_s, range_km, range_rate_km_s = _parse_vector_table(
            result_text
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
            retrieved_at=retrieved_at,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256=content_sha256,
        )

        return HorizonsGeometryResult(
            request=request,
            geometry=geometry,
            provenance=provenance,
        )
