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
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
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

from .pds_models import (
    PdsDataFile,
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
# KVP value normalization helpers
# ---------------------------------------------------------------------------


def _as_str_or_none(value: object) -> Optional[str]:
    """Extract a scalar string from a KVP field value (scalar or single-item list)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        if len(value) == 0:
            return None
        if len(value) == 1:
            v = value[0]
            if isinstance(v, str):
                return v or None
            return str(v) if v is not None else None
    return str(value) if value else None


def _as_str_required(value: object, field_name: str) -> str:
    """Extract a required non-empty scalar string from a KVP field value."""
    result = _as_str_or_none(value)
    if not result:
        raise PdsValidationError(
            f"PDS response missing required field: {field_name!r}."
        )
    return result


def _as_str_list(value: object) -> list[str]:
    """Extract a list of strings from a KVP field (scalar, list, or None)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    return [str(value)] if value else []


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
    - Parallel array cardinalities do not match
    - file_size is non-integer or negative
    - MD5 is present but malformed (caught by PdsDataFile validator)
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

    # If no names, check if other required fields also absent → no data files.
    if n == 0 and len(refs) == 0 and len(sizes) == 0:
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
        name = str(names[i]) if names[i] is not None else ""
        ref = str(refs[i]) if refs[i] is not None else ""
        raw_size = sizes[i]

        # Validate size: must be integer-valued and non-negative.
        try:
            size_val = int(str(raw_size))
        except (ValueError, TypeError):
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_size at index {i} is not an integer: "
                f"got {raw_size!r}."
            )
        if size_val < 0:
            raise PdsValidationError(
                f"PDS Data_File_Info.ops:file_size at index {i} is negative: {size_val}."
            )

        md5 = str(md5s[i]) if md5s and md5s[i] is not None else None
        mime = str(mimes[i]) if mimes and mimes[i] is not None else None

        try:
            pds_file = PdsDataFile(
                file_name=name,
                file_ref=ref,
                file_size_bytes=size_val,
                md5_checksum=md5 if md5 else None,
                mime_type=mime if mime else None,
            )
        except Exception as exc:
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
    3. Identification_Area.logical_identifier == returned lid
    4. Identification_Area.version_id == version portion of request.lidvid
    5. product_class and Identification_Area.product_class are both present,
       consistent with each other, and equal to Product_Observational.

    Returns (lid, version_id) on success.
    Raises PdsValidationError on any mismatch or missing field.
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

    # Field: Identification_Area.logical_identifier must match returned lid.
    logical_id = _as_str_or_none(
        data_item.get("pds:Identification_Area.pds:logical_identifier")
    )
    if logical_id is not None and logical_id != returned_lid:
        raise PdsValidationError(
            "PDS response Identification_Area.logical_identifier does not match "
            "the returned lid. Identity fields are inconsistent."
        )

    # Field: Identification_Area.version_id must match LIDVID version.
    version_id = _as_str_or_none(
        data_item.get("pds:Identification_Area.pds:version_id")
    )
    if version_id is not None and version_id != req_version:
        raise PdsValidationError(
            "PDS response Identification_Area.version_id does not match "
            "the version component of the requested LIDVID."
        )

    # Use Identification_Area.logical_identifier if available, else returned_lid.
    effective_lid = logical_id if logical_id is not None else returned_lid
    effective_version = version_id if version_id is not None else req_version

    # Product class validation.
    top_class = _as_str_or_none(data_item.get("product_class"))
    ia_class = _as_str_or_none(
        data_item.get("pds:Identification_Area.pds:product_class")
    )

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

    # Must be Product_Observational.
    if effective_class != _PRODUCT_OBSERVATIONAL:
        raise PdsValidationError(
            f"PDS product_class is '{effective_class}'; only '{_PRODUCT_OBSERVATIONAL}' "
            "is accepted by this adapter. Bundles, collections, and documents require "
            "separate adapters."
        )

    return effective_lid, effective_version


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
        For any validation failure.
    PdsUnavailableError
        (not raised from this function; raised by the HTTP transport layer)
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
    hits = summary.get("hits")
    if hits is not None:
        try:
            hits_int = int(hits)
        except (ValueError, TypeError):
            raise PdsValidationError(
                "PDS Search API response summary.hits is not a valid integer."
            )
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
    effective_lid, effective_version = _validate_identity(request, data_item)

    # 7. Required scalar fields.
    title = _as_str_required(
        data_item.get("pds:Identification_Area.pds:title") or data_item.get("title"),
        "title",
    )

    # 8. Optional observation times.
    obs_start_utc: Optional[datetime] = None
    obs_stop_utc: Optional[datetime] = None

    raw_start = _as_str_or_none(
        data_item.get("pds:Time_Coordinates.pds:start_date_time")
    )
    if raw_start:
        obs_start_utc = _parse_pds_datetime(
            raw_start, "pds:Time_Coordinates.pds:start_date_time"
        )

    raw_stop = _as_str_or_none(
        data_item.get("pds:Time_Coordinates.pds:stop_date_time")
    )
    if raw_stop:
        obs_stop_utc = _parse_pds_datetime(
            raw_stop, "pds:Time_Coordinates.pds:stop_date_time"
        )

    if obs_start_utc is not None and obs_stop_utc is not None:
        if obs_start_utc > obs_stop_utc:
            raise PdsValidationError(
                "PDS response observation start_date_time is after stop_date_time."
            )

    # 9. Optional processing level.
    processing_level = _as_str_or_none(
        data_item.get("pds:Primary_Result_Summary.pds:processing_level")
    )

    # 10. Reference LID lists.
    instrument_lids = tuple(_as_str_list(data_item.get("ref_lid_instrument")))
    instrument_host_lids = tuple(_as_str_list(data_item.get("ref_lid_instrument_host")))
    investigation_lids = tuple(_as_str_list(data_item.get("ref_lid_investigation")))
    target_lids = tuple(_as_str_list(data_item.get("ref_lid_target")))

    # 11. Data-file normalization (ops:Data_File_Info only — not label file).
    data_files = _normalize_data_files(data_item)

    # 12. Total data size (sum of data-file sizes).
    total_data_size_bytes = sum(f.file_size_bytes for f in data_files)

    # 13. Registry harvest info.
    registry_node = _as_str_or_none(data_item.get("ops:Harvest_Info.ops:node_name"))
    registry_harvested_at: Optional[datetime] = None
    raw_harvest_time = _as_str_or_none(
        data_item.get("ops:Harvest_Info.ops:harvest_date_time")
    )
    if raw_harvest_time:
        registry_harvested_at = _parse_pds_datetime(
            raw_harvest_time, "ops:Harvest_Info.ops:harvest_date_time"
        )

    # 14. Build normalized product.
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
    except Exception as exc:
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
            product class, invalid metadata, oversized response.
        """
        raw_bytes = self._execute_request(request)
        retrieved_at = self._clock()
        return _validate_pds_raw_response(request, raw_bytes, retrieved_at)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute_request(self, request: PdsProductRequest) -> bytes:
        """Perform exactly one HTTP GET to the fixed PDS Search API endpoint.

        Returns the raw response body bytes.

        The endpoint URL is constructed entirely by this adapter from the
        fixed base plus the validated LIDVID.  Callers cannot supply or
        override the URL.

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
            response = self._client.get(url, params=params, headers=headers)
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
        raise PdsValidationError(
            f"NASA PDS Search API returned unexpected HTTP status {status}."
        )
