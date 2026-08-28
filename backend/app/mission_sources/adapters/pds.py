"""GCSI Phase 6E-A — NASA PDS Registry Search API Adapter.

This module implements the ``PdsRegistryAdapter``: a lower-level source adapter
that fetches and validates metadata for one exact versioned PDS4 product from
the NASA Planetary Data System Search API.

Architecture position
---------------------
::

    HistoricalReplayProvider          [future]
              |
         ReplayAssembler              [future]
          /          \\
 HorizonsGeometry   PDS Science Products
    Adapter              Adapter
                            ↑
                   PdsRegistryAdapter  ← this module

This adapter is NOT a ``BaseMissionSourceProvider`` subclass.  It does NOT:

- Create a Scenario
- Modify runtime state (state.py)
- Affect RF, BER, SNR, goodput, scheduling, evaluation, AI, or simulation

It ONLY:

1. Validates the exact LIDVID request.
2. Constructs a fixed well-formed PDS Search API GET request.
3. Fetches it via HTTPS (one request per call).
4. Validates HTTP transport semantics.
5. Validates PDS KVP JSON envelope and identity fields.
6. Normalizes output into :class:`PdsScienceProduct`.
7. Produces an ``EXTERNAL_AUTHORITATIVE`` :class:`ProvenanceRecord`.
8. Returns the normalized product + provenance.

PDS Search API Completeness Warning
------------------------------------
NASA PDS explicitly warns that the Search API servers are not necessarily
fully populated with every PDS dataset.

Therefore HTTP 404, zero hits, or empty data MUST NOT be interpreted as
"this PDS product does not exist in the archive."

The correct semantics are: "this product's metadata is not available from
this PDS Search API request/service."

This distinction is reflected in errors, error messages, and tests.

Security notes
--------------
- The PDS Search API endpoint is fixed; callers cannot supply an arbitrary
  base URL (prevents SSRF).
- Only exact versioned LIDVIDs are accepted; bare LIDs are rejected.
- Request fields are a fixed immutable list owned by this adapter.
- Raw response content, request URLs, and raw metadata strings are NOT
  exposed in public exception messages.
- Redirect following is always disabled at the request level, regardless of
  the injected client's follow_redirects default.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Callable, Optional

import httpx
import pydantic

from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)

from .pds_models import (
    PdsDataFile,
    PdsScienceProductCapture,
    PdsProductRequest,
    PdsScienceProduct,
)


# ---------------------------------------------------------------------------
# Adapter-specific typed errors
# ---------------------------------------------------------------------------


class PdsAdapterError(Exception):
    """Base class for all PDS adapter failures.

    Catch this class to handle any PDS-specific error.
    Catch the subclasses to distinguish availability from validation.
    """


class PdsUnavailableError(PdsAdapterError, MissionSourceUnavailableError):
    """PDS Search API is unreachable or the product's metadata is unavailable.

    Raised for:
    - Network timeouts and connection failures
    - HTTP 5xx server errors
    - HTTP 429 rate-limiting
    - HTTP 404: product metadata not available from this PDS Search API
      request/service (does NOT mean the product does not exist in the archive)
    - Zero hits or empty data: metadata unavailable from this service

    IMPORTANT: HTTP 404, zero hits, or empty data do NOT imply the product
    does not exist in the PDS archive — the Search API is not guaranteed to
    be fully populated with every PDS dataset.
    """


class PdsValidationError(PdsAdapterError, MissionSourceValidationError):
    """PDS returned a response that fails domain validation.

    Raised for:
    - HTTP 4xx client errors (except 404)
    - Malformed JSON response
    - Invalid or inconsistent metadata (identity mismatch, wrong product class,
      mismatched data-file arrays, invalid timestamps, etc.)
    - Oversized response body
    - Unexpected redirect or other non-200/404/429/5xx status
    """


# ---------------------------------------------------------------------------
# Protocol constants  (adapter-owned — NOT configurable by callers)
# ---------------------------------------------------------------------------

# Fixed NASA PDS Search API base — HTTPS only.
# Endpoint: GET /products/{identifier}
# Full URL constructed as: _PDS_PRODUCTS_ENDPOINT + url_encoded_lidvid
_PDS_PRODUCTS_ENDPOINT: str = "https://pds.nasa.gov/api/search/1/products/"

# Accept header for the KVP JSON representation.
_ACCEPT_KVP_JSON: str = "application/kvp+json"

# Source system string for provenance.
_PDS_SOURCE_SYSTEM: str = "NASA Planetary Data System Search API"

# Maximum raw response body size (2 MiB).
_MAX_RESPONSE_BYTES: int = 2 * 1024 * 1024

# HTTP request timeout (seconds).
_HTTP_TIMEOUT: float = 30.0

# Authoritative product class for science/observational products.
_PRODUCT_OBSERVATIONAL: str = "Product_Observational"

# Fixed immutable field list requested from the PDS Search API.
# This list is owned entirely by this adapter.  Callers cannot add or remove
# fields.  Do not use wildcard retrieval.
_REQUESTED_FIELDS: tuple[str, ...] = (
    # Core identity
    "lid",
    "lidvid",
    "product_class",
    "title",
    "pds:Identification_Area.pds:logical_identifier",
    "pds:Identification_Area.pds:version_id",
    "pds:Identification_Area.pds:title",
    "pds:Identification_Area.pds:product_class",
    # Observation time
    "pds:Time_Coordinates.pds:start_date_time",
    "pds:Time_Coordinates.pds:stop_date_time",
    # Science classification
    "pds:Primary_Result_Summary.pds:processing_level",
    # References
    "ref_lid_instrument",
    "ref_lid_instrument_host",
    "ref_lid_investigation",
    "ref_lid_target",
    # Data file information (science payload — NOT label file)
    "ops:Data_File_Info.ops:file_name",
    "ops:Data_File_Info.ops:file_ref",
    "ops:Data_File_Info.ops:file_size",
    "ops:Data_File_Info.ops:md5_checksum",
    "ops:Data_File_Info.ops:mime_type",
    # Registry harvest traceability
    "ops:Harvest_Info.ops:node_name",
    "ops:Harvest_Info.ops:harvest_date_time",
)


# ---------------------------------------------------------------------------
# Production clock (injectable for tests)
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Provenance helpers
# ---------------------------------------------------------------------------


def _build_canonical_request_identity(request: PdsProductRequest) -> str:
    """Return a deterministic JSON string representing canonical request identity.

    Binds: fixed endpoint, exact LIDVID, media type, exact immutable field list.
    All fixed adapter-owned protocol parameters are included so that any
    protocol change is reflected in the provenance_id.
    """
    identity: dict = {
        "endpoint": _PDS_PRODUCTS_ENDPOINT,
        "lidvid": request.lidvid,
        "media_type": _ACCEPT_KVP_JSON,
        "requested_fields": sorted(_REQUESTED_FIELDS),
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _compute_provenance_id(canonical_identity: str, content_sha256: str) -> str:
    """Compute a deterministic provenance_id from request identity + response hash.

    Formula:
        SHA-256(canonical_identity + "|" + content_sha256)

    Returns the hex digest string.  retrieved_at does NOT affect provenance_id.
    """
    combined = canonical_identity + "|" + content_sha256
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# KVP value normalization helpers — strict narrow contracts
# ---------------------------------------------------------------------------
# These helpers enforce an explicit narrow contract over external KVP values.
# They do NOT stringify arbitrary types.  If the source sends a non-string
# value where a string is required, a PdsValidationError is raised.


def _as_str_or_none(value: object) -> Optional[str]:
    """Extract a scalar string from a KVP field value (strict).

    Accepted shapes:
        "value"             → "value" (non-empty string)
        ""                  → None
        None                → None
        ["value"]           → "value" (single-element list of str)
        []                  → None
        [None]              → None

    Rejected (raises PdsValidationError):
        123, 1.5, True, False, {}, {"x": "y"}
        ["a", "b"]  (multi-element list — use _as_str_list for that)
        [123], [True]  (list containing non-string)
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        if len(value) == 0:
            return None
        if len(value) == 1:
            v = value[0]
            if v is None:
                return None
            if isinstance(v, str):
                return v or None
            raise PdsValidationError(
                "PDS KVP field contains a list with a non-string element where "
                "a scalar string value is required."
            )
        # Multi-element list where a scalar is expected.
        raise PdsValidationError(
            f"PDS KVP field contains a list with {len(value)} elements where "
            "a single scalar string value is required."
        )
    # Non-string, non-list, non-None scalar (int, float, bool, dict, etc.)
    raise PdsValidationError(
        "PDS KVP field has an unexpected non-string type where a string value "
        "is required."
    )


def _as_str_required(value: object, field_name: str) -> str:
    """Extract a required non-empty scalar string from a KVP field value.

    Raises PdsValidationError if the value is absent, empty, or of a wrong type.
    """
    try:
        result = _as_str_or_none(value)
    except PdsValidationError as exc:
        raise PdsValidationError(
            f"PDS response field {field_name!r} has invalid type; "
            "expected a scalar string."
        ) from exc
    if not result:
        raise PdsValidationError(
            f"PDS response missing required field: {field_name!r}."
        )
    return result


def _as_str_list(value: object) -> list[str]:
    """Extract a list of strings from a KVP reference field (strict).

    Accepted shapes:
        None          → []
        ""            → []
        "value"       → ["value"]
        []            → []
        ["a", "b"]    → ["a", "b"]

    Rejected (raises PdsValidationError):
        123, 1.5, True, False, {}
        [None]                  — list containing None
        ["a", None]             — list with any None element
        [""]                    — list containing empty string
        ["a", ""]               — list with any empty string
        [123], [True]           — list containing non-string items
        mixed lists             — e.g. ["a", 123]

    Once a list is present, EVERY element must be a non-empty string.
    Null and empty-string elements are never silently dropped.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        if len(value) == 0:
            return []
        result: list[str] = []
        for i, item in enumerate(value):
            if item is None:
                raise PdsValidationError(
                    f"PDS KVP reference field contains a null element at "
                    f"index {i}; null reference LIDs are not accepted."
                )
            if not isinstance(item, str):
                raise PdsValidationError(
                    f"PDS KVP reference field contains a non-string element at "
                    f"index {i}; only string reference LIDs are accepted."
                )
            if not item:
                raise PdsValidationError(
                    f"PDS KVP reference field contains an empty string at "
                    f"index {i}; empty reference LIDs are not accepted."
                )
            result.append(item)
        return result
    # Non-string, non-list, non-None (int, float, bool, dict, etc.)
    raise PdsValidationError(
        "PDS KVP reference field has an unexpected non-string type; "
        "only strings or lists of strings are accepted."
    )


def _parse_pds_datetime(raw: str, field_name: str) -> datetime:
    """Parse a PDS datetime string into a timezone-aware UTC datetime.

    PDS datetime values typically follow ISO 8601 with 'Z' suffix or an
    offset.  Naive timestamps are rejected.

    Raises PdsValidationError for malformed or naive values.
    """
    if not raw or not raw.strip():
        raise PdsValidationError(
            f"PDS datetime field {field_name!r} is empty."
        )
    raw = raw.strip()

    # Python's fromisoformat in 3.11+ handles 'Z' as UTC.
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise PdsValidationError(
            f"PDS datetime field {field_name!r} could not be parsed as ISO 8601."
        ) from exc

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise PdsValidationError(
            f"PDS datetime field {field_name!r} is naive; timezone-aware value required."
        )

    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Data-file normalization
# ---------------------------------------------------------------------------

# Pattern accepting only non-negative decimal integer strings (no scientific
# notation, no floats, no signs other than implicit positive).
import re as _re
_DECIMAL_INT_RE = _re.compile(r"^[0-9]+$")


def _parse_file_size(raw: object, index: int) -> int:
    """Parse a single ops:file_size value into a non-negative int.

    Accepted inputs:
        non-negative JSON integer (int, not bool)
        decimal integer string such as "1024"

    Rejected inputs (raises PdsValidationError — does NOT echo raw value):
        bool
        float (including 1024.0)
        scientific notation strings
        negative values
        non-decimal strings
        dicts/lists
    """
    # Reject bool (bool is a subclass of int in Python).
    if isinstance(raw, bool):
        raise PdsValidationError(
            f"PDS Data_File_Info.ops:file_size at index {index} is not a valid "
            "file size integer."
        )
    # Accept plain int.
    if isinstance(raw, int):
        if raw < 0:
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_size at index {index} is negative."
            )
        return raw
    # Accept decimal integer string only.
    if isinstance(raw, str):
        if not _DECIMAL_INT_RE.match(raw):
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_size at index {index} is not a "
                "valid non-negative integer string."
            )
        val = int(raw)
        # int() of a decimal string is always non-negative here given the regex,
        # but be explicit.
        if val < 0:
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_size at index {index} is negative."
            )
        return val
    # float, dict, list, None, etc. — all rejected without echoing the raw value.
    raise PdsValidationError(
        f"PDS Data_File_Info.ops:file_size at index {index} is not a valid "
        "file size value."
    )


def _normalize_data_files(data_item: dict) -> tuple[PdsDataFile, ...]:
    """Extract and normalize ops:Data_File_Info fields into PdsDataFile instances.

    Handles the case where Data_File_Info fields may be:
    - Absent (no data files)
    - Scalar (one file)
    - Parallel arrays (multiple files)

    IMPORTANT: Does NOT include ops:Label_File_Info.  The XML label is
    metadata, not the science data payload.

    Raises PdsValidationError if:
    - Required fields (file_name, file_ref, file_size) are missing for any file
    - Any file_name or file_ref is not a string
    - Parallel array cardinalities do not match
    - file_size is invalid (non-integer, negative, bool, float, etc.)
    - MD5 or MIME is present but non-string
    - Optional metadata (md5/mime) is present while required identity fields
      (file_name, file_ref, file_size) are absent (partial presence → fail closed)
    """
    raw_names = data_item.get("ops:Data_File_Info.ops:file_name")
    raw_refs = data_item.get("ops:Data_File_Info.ops:file_ref")
    raw_sizes = data_item.get("ops:Data_File_Info.ops:file_size")
    raw_md5s = data_item.get("ops:Data_File_Info.ops:md5_checksum")
    raw_mimes = data_item.get("ops:Data_File_Info.ops:mime_type")

    # Normalize all fields to lists for uniform processing.
    names = _normalize_to_list(raw_names)
    refs = _normalize_to_list(raw_refs)
    sizes = _normalize_to_list(raw_sizes)
    md5s = _normalize_to_list_optional(raw_md5s)
    mimes = _normalize_to_list_optional(raw_mimes)

    n = len(names)

    # If no required fields are present at all, this is a genuinely absent
    # Data_File_Info representation.  But if optional fields (md5/mime) carry
    # actual values while the required identity triple is absent, fail closed —
    # we must not silently discard partial metadata.
    if n == 0 and len(refs) == 0 and len(sizes) == 0:
        optional_present = any(
            x for x in (md5s, mimes) if x  # non-empty list means something was supplied
        )
        if optional_present:
            raise PdsValidationError(
                "PDS Data_File_Info contains optional metadata (md5/mime) but "
                "required identity fields (file_name, file_ref, file_size) are "
                "all absent. Failing closed to avoid silent data loss."
            )
        return ()

    # With data: all required fields must be present.
    if n == 0:
        raise PdsValidationError(
            "PDS Data_File_Info is missing required field: ops:file_name."
        )
    if len(refs) == 0:
        raise PdsValidationError(
            "PDS Data_File_Info is missing required field: ops:file_ref."
        )
    if len(sizes) == 0:
        raise PdsValidationError(
            "PDS Data_File_Info is missing required field: ops:file_size."
        )

    # Enforce cardinality consistency for required fields.
    if len(refs) != n:
        raise PdsValidationError(
            f"PDS Data_File_Info parallel arrays have mismatched cardinality: "
            f"file_name has {n} items but file_ref has {len(refs)} items."
        )
    if len(sizes) != n:
        raise PdsValidationError(
            f"PDS Data_File_Info parallel arrays have mismatched cardinality: "
            f"file_name has {n} items but file_size has {len(sizes)} items."
        )

    # Optional fields: if present must match cardinality or be empty (→ all None).
    if md5s and len(md5s) != n:
        raise PdsValidationError(
            f"PDS Data_File_Info parallel arrays have mismatched cardinality: "
            f"file_name has {n} items but md5_checksum has {len(md5s)} items."
        )
    if mimes and len(mimes) != n:
        raise PdsValidationError(
            f"PDS Data_File_Info parallel arrays have mismatched cardinality: "
            f"file_name has {n} items but mime_type has {len(mimes)} items."
        )

    files: list[PdsDataFile] = []
    for i in range(n):
        # Strict string check for file_name — do NOT coerce.
        name_raw = names[i]
        if not isinstance(name_raw, str):
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_name at index {i} is not a string."
            )
        name = name_raw

        # Strict string check for file_ref — do NOT coerce.
        ref_raw = refs[i]
        if not isinstance(ref_raw, str):
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_ref at index {i} is not a string."
            )
        ref = ref_raw

        # Parse file_size with the strict helper.
        size_val = _parse_file_size(sizes[i], i)

        # Optional md5: must be string or None — do NOT coerce.
        md5: Optional[str] = None
        if md5s and md5s[i] is not None:
            md5_raw = md5s[i]
            if not isinstance(md5_raw, str):
                raise PdsValidationError(
                    f"PDS Data_File_Info.ops:md5_checksum at index {i} is not a string."
                )
            md5 = md5_raw if md5_raw else None

        # Optional mime: must be string or None — do NOT coerce.
        mime: Optional[str] = None
        if mimes and mimes[i] is not None:
            mime_raw = mimes[i]
            if not isinstance(mime_raw, str):
                raise PdsValidationError(
                    f"PDS Data_File_Info.ops:mime_type at index {i} is not a string."
                )
            mime = mime_raw if mime_raw else None

        try:
            pds_file = PdsDataFile(
                file_name=name,
                file_ref=ref,
                file_size_bytes=size_val,
                md5_checksum=md5 if md5 else None,
                mime_type=mime if mime else None,
            )
        except pydantic.ValidationError as exc:
            raise PdsValidationError(
                f"PDS Data_File_Info at index {i} failed validation."
            ) from exc

        files.append(pds_file)

    return tuple(files)


def _normalize_to_list(value: object) -> list:
    """Normalize a KVP scalar or list to a list.  Returns [] if absent."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_to_list_optional(value: object) -> list:
    """Normalize an optional KVP field to a list.  Returns [] if absent."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# Identity validation
# ---------------------------------------------------------------------------


def _validate_identity(request: PdsProductRequest, data_item: dict) -> tuple[str, str]:
    """Validate LIDVID and LID identity consistency in the PDS response.

    Checks:
    1. Returned lidvid == request.lidvid
    2. Returned lid == LID portion of request.lidvid
    3. Identification_Area.logical_identifier is present and == returned lid
    4. Identification_Area.version_id is present and == version portion of request.lidvid
    5. product_class and Identification_Area.product_class are both present,
       consistent with each other, and equal to Product_Observational.

    Returns (logical_identifier, version_id) from the actual response fields.
    Raises PdsValidationError on any mismatch or missing field.

    IMPORTANT: Both logical_identifier and version_id MUST be present in the
    source response — they are not derived from request parameters.
    """
    # Decompose request LIDVID into LID + version.
    req_lid, req_version = request.lidvid.rsplit("::", 1)

    # Field: returned lidvid must match exactly.
    returned_lidvid = _as_str_required(data_item.get("lidvid"), "lidvid")
    if returned_lidvid != request.lidvid:
        raise PdsValidationError(
            "PDS response LIDVID does not match the requested LIDVID. "
            "(Identity independently affirmed by response.)"
        )

    # Field: returned lid must match LID portion.
    returned_lid = _as_str_required(data_item.get("lid"), "lid")
    if returned_lid != req_lid:
        raise PdsValidationError(
            "PDS response lid does not match the LID portion of the requested LIDVID."
        )

    # Field: Identification_Area.logical_identifier is REQUIRED to be present.
    # It is NOT optional — a Product_Observational response MUST supply it.
    ia_lid_raw = data_item.get("pds:Identification_Area.pds:logical_identifier")
    # Strict parse — rejects non-string types.
    ia_lid = _as_str_required(ia_lid_raw, "pds:Identification_Area.pds:logical_identifier")
    if ia_lid != returned_lid:
        raise PdsValidationError(
            "PDS response Identification_Area.logical_identifier does not match "
            "the returned lid. Identity fields are inconsistent."
        )

    # Field: Identification_Area.version_id is REQUIRED to be present.
    # It is NOT optional — a Product_Observational response MUST supply it.
    ia_ver_raw = data_item.get("pds:Identification_Area.pds:version_id")
    # Strict parse — rejects non-string types.
    ia_version = _as_str_required(ia_ver_raw, "pds:Identification_Area.pds:version_id")
    if ia_version != req_version:
        raise PdsValidationError(
            "PDS response Identification_Area.version_id does not match "
            "the version component of the requested LIDVID."
        )

    # Product class validation — strict scalar string parsing.
    top_class_raw = data_item.get("product_class")
    ia_class_raw = data_item.get("pds:Identification_Area.pds:product_class")

    top_class: Optional[str] = None
    if top_class_raw is not None:
        top_class = _as_str_required(top_class_raw, "product_class")

    ia_class: Optional[str] = None
    if ia_class_raw is not None:
        ia_class = _as_str_required(ia_class_raw, "pds:Identification_Area.pds:product_class")

    # At least one must be present.
    if top_class is None and ia_class is None:
        raise PdsValidationError(
            "PDS response does not supply a product_class field. "
            "Cannot confirm Product_Observational."
        )

    # If both present they must agree.
    if top_class is not None and ia_class is not None:
        if top_class != ia_class:
            raise PdsValidationError(
                "PDS response product_class and Identification_Area.product_class "
                "are inconsistent. Failing closed."
            )

    effective_class = top_class if top_class is not None else ia_class

    # Must be Product_Observational — do NOT expose the raw class value.
    if effective_class != _PRODUCT_OBSERVATIONAL:
        raise PdsValidationError(
            "PDS product class is not supported by this observational-product adapter."
        )

    # Return actual IA-sourced values (not derived from request).
    return ia_lid, ia_version


# ---------------------------------------------------------------------------
# Title validation
# ---------------------------------------------------------------------------


def _extract_title(data_item: dict) -> str:
    """Extract and validate the product title from KVP fields.

    Protocol:
    - If both top-level title and pds:Identification_Area.pds:title are present,
      they must be equal.
    - If only one is present, accept it.
    - If neither is present, raise PdsValidationError.

    Raises PdsValidationError for missing title or conflicting values.
    """
    raw_ia_title = data_item.get("pds:Identification_Area.pds:title")
    raw_top_title = data_item.get("title")

    # Parse each: accept str/None; reject wrong types.
    ia_title: Optional[str] = None
    if raw_ia_title is not None:
        ia_title = _as_str_required(raw_ia_title, "pds:Identification_Area.pds:title")

    top_title: Optional[str] = None
    if raw_top_title is not None:
        top_title = _as_str_required(raw_top_title, "title")

    if ia_title is not None and top_title is not None:
        if ia_title != top_title:
            raise PdsValidationError(
                "PDS response title and Identification_Area.title are present "
                "but do not match. Failing closed."
            )
        return ia_title

    if ia_title is not None:
        return ia_title

    if top_title is not None:
        return top_title

    raise PdsValidationError(
        "PDS response does not supply a title field."
    )


# ---------------------------------------------------------------------------
# Full response validation
# ---------------------------------------------------------------------------


def _validate_pds_raw_response(
    request: PdsProductRequest,
    raw_bytes: bytes,
    retrieved_at: datetime,
) -> tuple[PdsScienceProduct, ProvenanceRecord]:
    """Validate raw PDS Search API response bytes and produce normalized output.

    This is the single authoritative validation boundary for the live HTTP
    fetch path.  It performs NO HTTP requests.

    Parameters
    ----------
    request:
        The original :class:`PdsProductRequest` (exact LIDVID).

    raw_bytes:
        Exact raw HTTP response body bytes.  Must not exceed
        ``_MAX_RESPONSE_BYTES`` (2 MiB).

    retrieved_at:
        Timezone-aware datetime representing when the response was acquired.
        Must be a ``datetime`` instance with ``tzinfo`` set.
        Normalized to UTC.

    Returns
    -------
    tuple[PdsScienceProduct, ProvenanceRecord]
        Fully validated normalized product and EXTERNAL_AUTHORITATIVE
        provenance record.

    Raises
    ------
    PdsValidationError
        For structural or semantic validation failures, including malformed
        JSON, oversized response, identity mismatches, wrong product class,
        invalid timestamps, and cardinality errors other than zero hits.

    PdsUnavailableError
        Raised by this function (not only by the HTTP transport layer) when
        the validated envelope reports that the product's metadata is
        unavailable:

        - ``summary.hits == 0`` — PDS Search API reported zero hits for the
          LIDVID; the product's metadata is not available from this service.
        - empty ``data`` array — PDS Search API returned no data items.

        These conditions indicate the PDS Search API did not return the
        requested product, which does NOT mean the product does not exist in
        the PDS archive.
    """
    # 0. Validate retrieved_at before doing any parsing work.
    if not isinstance(retrieved_at, datetime):
        raise PdsValidationError(
            "retrieved_at did not receive a datetime object."
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise PdsValidationError(
            "retrieved_at must be timezone-aware."
        )
    retrieved_at_utc = retrieved_at.astimezone(timezone.utc)

    # 1. Enforce raw response size limit.
    if len(raw_bytes) > _MAX_RESPONSE_BYTES:
        raise PdsValidationError(
            f"PDS Search API response body exceeds maximum allowed size "
            f"({_MAX_RESPONSE_BYTES} bytes)."
        )

    # 2. Hash the raw bytes before any decoding.
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. Parse JSON.
    try:
        payload = json.loads(raw_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PdsValidationError(
            "PDS Search API response could not be decoded as valid JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise PdsValidationError(
            "PDS Search API response JSON is not an object."
        )

    # 4. Validate envelope structure: summary + data.
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise PdsValidationError(
            "PDS Search API response is missing a valid 'summary' object."
        )

    data = payload.get("data")
    if not isinstance(data, list):
        raise PdsValidationError(
            "PDS Search API response is missing a valid 'data' array."
        )

    # 5. Validate cardinality: exactly one product.
    # hits must be a strict non-boolean integer if present.
    hits = summary.get("hits")
    if hits is not None:
        if not isinstance(hits, int) or isinstance(hits, bool):
            raise PdsValidationError(
                "PDS Search API response summary.hits is not a valid integer."
            )
        hits_int: int = hits
        if hits_int == 0:
            raise PdsUnavailableError(
                "PDS Search API reported zero hits for this LIDVID. "
                "The product metadata is not available from this PDS Search API "
                "request/service. The Search API is not guaranteed to be fully "
                "populated with every PDS dataset."
            )
        if hits_int != 1:
            raise PdsValidationError(
                f"PDS Search API returned summary.hits={hits_int}; "
                "expected exactly 1 for an exact LIDVID request."
            )

    if len(data) == 0:
        raise PdsUnavailableError(
            "PDS Search API returned an empty data array for this LIDVID. "
            "The product metadata is not available from this PDS Search API "
            "request/service. The Search API is not guaranteed to be fully "
            "populated with every PDS dataset."
        )

    if len(data) > 1:
        raise PdsValidationError(
            f"PDS Search API returned {len(data)} items in data; "
            "expected exactly 1 for an exact LIDVID request."
        )

    data_item = data[0]
    if not isinstance(data_item, dict):
        raise PdsValidationError(
            "PDS Search API data[0] is not an object."
        )

    # 6. Identity + product-class validation.
    # Returns the actual IA-sourced logical_identifier and version_id.
    effective_lid, effective_version = _validate_identity(request, data_item)

    # 7. Title validation (strict — checks both fields if present, requires agreement).
    title = _extract_title(data_item)

    # 8. Optional observation times.
    obs_start_utc: Optional[datetime] = None
    obs_stop_utc: Optional[datetime] = None

    raw_start = data_item.get("pds:Time_Coordinates.pds:start_date_time")
    if raw_start is not None:
        start_str = _as_str_required(raw_start, "pds:Time_Coordinates.pds:start_date_time")
        obs_start_utc = _parse_pds_datetime(
            start_str, "pds:Time_Coordinates.pds:start_date_time"
        )

    raw_stop = data_item.get("pds:Time_Coordinates.pds:stop_date_time")
    if raw_stop is not None:
        stop_str = _as_str_required(raw_stop, "pds:Time_Coordinates.pds:stop_date_time")
        obs_stop_utc = _parse_pds_datetime(
            stop_str, "pds:Time_Coordinates.pds:stop_date_time"
        )

    if obs_start_utc is not None and obs_stop_utc is not None:
        if obs_start_utc > obs_stop_utc:
            raise PdsValidationError(
                "PDS response observation start_date_time is after stop_date_time."
            )

    # 9. Optional processing level.
    raw_proc = data_item.get("pds:Primary_Result_Summary.pds:processing_level")
    processing_level: Optional[str] = None
    if raw_proc is not None:
        processing_level = _as_str_or_none(raw_proc)

    # 10. Reference LID lists — strict: only string or list-of-strings accepted.
    instrument_lids = tuple(_as_str_list(data_item.get("ref_lid_instrument")))
    instrument_host_lids = tuple(_as_str_list(data_item.get("ref_lid_instrument_host")))
    investigation_lids = tuple(_as_str_list(data_item.get("ref_lid_investigation")))
    target_lids = tuple(_as_str_list(data_item.get("ref_lid_target")))

    # 11. Data-file normalization (ops:Data_File_Info only — not label file).
    data_files = _normalize_data_files(data_item)

    # 12. Total data size (sum of data-file sizes).
    total_data_size_bytes = sum(f.file_size_bytes for f in data_files)

    # 13. Registry harvest info.
    raw_harvest_node = data_item.get("ops:Harvest_Info.ops:node_name")
    registry_node: Optional[str] = None
    if raw_harvest_node is not None:
        registry_node = _as_str_or_none(raw_harvest_node)

    registry_harvested_at: Optional[datetime] = None
    raw_harvest_time = data_item.get("ops:Harvest_Info.ops:harvest_date_time")
    if raw_harvest_time is not None:
        harvest_str = _as_str_or_none(raw_harvest_time)
        if harvest_str:
            registry_harvested_at = _parse_pds_datetime(
                harvest_str, "ops:Harvest_Info.ops:harvest_date_time"
            )

    # 14. Build normalized product.
    # logical_identifier and version_id are sourced from the actual IA response
    # fields (not derived from the request).
    try:
        product = PdsScienceProduct(
            lid=effective_lid,
            lidvid=request.lidvid,
            logical_identifier=effective_lid,
            version_id=effective_version,
            product_class=_PRODUCT_OBSERVATIONAL,
            title=title,
            observation_start_utc=obs_start_utc,
            observation_stop_utc=obs_stop_utc,
            processing_level=processing_level,
            instrument_lids=instrument_lids,
            instrument_host_lids=instrument_host_lids,
            investigation_lids=investigation_lids,
            target_lids=target_lids,
            data_files=data_files,
            total_data_size_bytes=total_data_size_bytes,
            registry_node=registry_node,
            registry_harvested_at=registry_harvested_at,
        )
    except pydantic.ValidationError as exc:
        raise PdsValidationError(
            "PDS normalized product failed internal validation."
        ) from exc

    # 15. Build deterministic provenance_id.
    canonical_identity = _build_canonical_request_identity(request)
    provenance_id = _compute_provenance_id(canonical_identity, content_sha256)

    # 16. Build ProvenanceRecord.
    # source_version is None — the API route path contains '/1/' but the
    # external response in Phase 6E-A does not supply an authoritative service
    # version field analogous to Horizons signature.version.  Do NOT fabricate
    # a version string from the URL path.
    provenance = ProvenanceRecord(
        provenance_id=provenance_id,
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system=_PDS_SOURCE_SYSTEM,
        source_version=None,
        source_record_id=request.lidvid,
        source_uri=_PDS_PRODUCTS_ENDPOINT,
        observed_at=None,  # observational interval belongs in PdsScienceProduct
        retrieved_at=retrieved_at_utc,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
    )

    return product, provenance


# ---------------------------------------------------------------------------
# PdsRegistryAdapter
# ---------------------------------------------------------------------------


class PdsRegistryAdapter:
    """Lower-level source adapter that fetches validated PDS science product
    metadata from the NASA Planetary Data System Search API.

    Parameters
    ----------
    client:
        Optional injected ``httpx.Client``.  If ``None``, the adapter creates
        and owns its own client (and closes it when used as a context manager).
        If a client is injected, the adapter does NOT close it.

    clock:
        Optional callable returning an aware UTC ``datetime`` used as
        ``retrieved_at``.  Defaults to :func:`_utc_now`.  Tests must inject
        a fixed aware timestamp.  The adapter raises :class:`PdsValidationError`
        if the clock returns a naive datetime or a non-datetime value.

    Usage
    -----
    As a context manager (recommended for owned client)::

        with PdsRegistryAdapter() as adapter:
            product, provenance = adapter.fetch(request)

    With an injected client (caller manages client lifetime)::

        with httpx.Client(transport=mock_transport) as client:
            adapter = PdsRegistryAdapter(client=client)
            product, provenance = adapter.fetch(request)

    Notes
    -----
    - One :meth:`fetch` call = one HTTP GET request.  No retries, no
      concurrent requests.
    - The production endpoint URL is fixed and not configurable by callers.
    - Only exact versioned LIDVIDs are accepted; bare LIDs are rejected.
    - The adapter does NOT follow or download any data-file URLs returned
      in ops:Data_File_Info.ops:file_ref.
    - Redirect following is always disabled at the request level, regardless
      of the injected client's follow_redirects configuration.  This prevents
      the adapter from becoming an SSRF surface via client-policy override.

    PDS Search API Completeness Warning
    ------------------------------------
    HTTP 404, zero hits, or empty data are surfaced as :class:`PdsUnavailableError`
    and do NOT imply the product does not exist in the PDS archive.  The Search
    API is not guaranteed to be fully populated with every PDS dataset.
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._injected_client = client is not None
        self._client: httpx.Client = client if client is not None else httpx.Client(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=False,
        )
        self._clock: Callable[[], datetime] = clock if clock is not None else _utc_now

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "PdsRegistryAdapter":
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

    def fetch(
        self, request: PdsProductRequest
    ) -> tuple[PdsScienceProduct, ProvenanceRecord]:
        """Fetch and validate metadata for one exact PDS LIDVID.

        Performs exactly one HTTP GET to the fixed PDS Search API endpoint.
        Does NOT follow or download any data-file URLs.
        Redirects are never followed, regardless of injected client config.

        This method delegates to :meth:`fetch_capture` and returns only the
        product and provenance pair.  One call = one HTTP GET.

        Parameters
        ----------
        request:
            Validated :class:`PdsProductRequest` specifying the exact LIDVID.

        Returns
        -------
        tuple[PdsScienceProduct, ProvenanceRecord]
            Normalized science product metadata and EXTERNAL_AUTHORITATIVE
            provenance record.

        Raises
        ------
        PdsUnavailableError
            Network/transport failure, HTTP 5xx/429, HTTP 404 (metadata not
            available from this Search API service), zero hits, or empty data.
            IMPORTANT: These do NOT imply the product does not exist in the
            PDS archive.

        PdsValidationError
            HTTP 4xx (non-404), malformed JSON, identity mismatch, wrong
            product class, invalid metadata, oversized response, redirect.
        """
        capture = self.fetch_capture(request)
        return capture.product, capture.provenance

    def fetch_capture(
        self, request: PdsProductRequest
    ) -> PdsScienceProductCapture:
        """Fetch, validate, and capture metadata for one exact PDS LIDVID.

        Performs exactly one HTTP GET to the fixed PDS Search API endpoint.
        Returns a :class:`PdsScienceProductCapture` that bundles the validated
        product, provenance, and the exact raw response bytes.

        The capture can be passed to :class:`PdsSnapshotStore.write` to persist
        a content-verified offline snapshot for later replay.

        Does NOT follow or download any data-file URLs.
        Redirects are never followed, regardless of injected client config.

        Parameters
        ----------
        request:
            Validated :class:`PdsProductRequest` specifying the exact LIDVID.

        Returns
        -------
        PdsScienceProductCapture
            Immutable capture binding request, product, provenance, and raw bytes.

        Raises
        ------
        PdsUnavailableError
            Network/transport failure, HTTP 5xx/429, HTTP 404, zero hits, or
            empty data.

        PdsValidationError
            HTTP 4xx (non-404), malformed JSON, identity mismatch, wrong
            product class, invalid metadata, oversized response, redirect.
        """
        raw_bytes = self._execute_request(request)
        retrieved_at = self._clock()
        product, provenance = _validate_pds_raw_response(request, raw_bytes, retrieved_at)
        return PdsScienceProductCapture(
            request=request,
            product=product,
            provenance=provenance,
            raw_response=raw_bytes,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_request(self, request: PdsProductRequest) -> bytes:
        """Perform exactly one HTTP GET to the fixed PDS Search API endpoint.

        Returns the raw response body bytes.

        The endpoint URL is constructed entirely by this adapter from the
        fixed base plus the validated LIDVID.  Callers cannot supply or
        override the URL.

        Redirect following is always disabled at the request level via
        ``follow_redirects=False`` in the ``send()`` call, regardless of the
        injected client's ``follow_redirects`` default.  This is the adapter's
        own trust-boundary guarantee — it cannot be overridden by client config.

        Raises PdsUnavailableError or PdsValidationError on failure.
        """
        # Construct URL: fixed base + LIDVID
        # LIDVID validation ensures no path-traversal characters, so it is
        # safe to append directly.
        url = _PDS_PRODUCTS_ENDPOINT + request.lidvid

        params = {
            "fields": ",".join(_REQUESTED_FIELDS),
        }

        headers = {
            "Accept": _ACCEPT_KVP_JSON,
        }

        try:
            # Build the Request object and send it explicitly with
            # follow_redirects=False.  This overrides any follow_redirects=True
            # that may be set on the injected client, ensuring the adapter's
            # own redirect policy cannot be defeated by caller configuration.
            req_obj = self._client.build_request(
                "GET", url, params=params, headers=headers
            )
            response = self._client.send(req_obj, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise PdsUnavailableError(
                "Request to NASA PDS Search API timed out."
            ) from exc
        except httpx.RequestError as exc:
            raise PdsUnavailableError(
                "Network error while contacting NASA PDS Search API."
            ) from exc

        status = response.status_code

        if status == 200:
            return response.content

        # HTTP 404: metadata not available from this Search API service.
        # IMPORTANT: does NOT imply the product is absent from the PDS archive.
        # The PDS Search API is not guaranteed to be fully populated with every
        # PDS dataset.  A 404 from this service only indicates metadata
        # unavailability via this particular Search API request.
        if status == 404:
            raise PdsUnavailableError(
                f"NASA PDS Search API returned HTTP 404 for this LIDVID. "
                f"The product metadata is not available from this PDS Search API "
                f"request/service. The Search API is not guaranteed to be fully "
                f"populated with every PDS dataset."
            )

        # HTTP 429 and all 5xx: service availability failures.
        if status == 429 or (500 <= status < 600):
            raise PdsUnavailableError(
                f"NASA PDS Search API returned HTTP {status}."
            )

        # Other 4xx: client validation failures.
        if 400 <= status < 500:
            raise PdsValidationError(
                f"NASA PDS Search API returned HTTP {status}."
            )

        # Unexpected 3xx or any other status — fail closed.
        # Do NOT follow redirect.  Do NOT expose the Location URL.
        raise PdsValidationError(
            f"NASA PDS Search API returned unexpected HTTP status {status}."
        )
