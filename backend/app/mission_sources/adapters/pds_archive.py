"""GCSI Phase 6E-C3B — PDS Atmospheres Node Archive-Label Adapter.

This module implements the ``PdsArchiveLabelAdapter``: a lower-level source
adapter that fetches and validates the PDS4 XML label for one exact versioned
MWR calibrated product directly from the PDS Atmospheres Node file server.

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
                 PdsArchiveLabelAdapter  ← this module (C3B)
                 PdsRegistryAdapter      (pds.py — Search API, D2)

This adapter is NOT a ``BaseMissionSourceProvider`` subclass.  It does NOT:

- Create a Scenario
- Modify runtime state (state.py)
- Affect RF, BER, SNR, goodput, scheduling, evaluation, AI, or simulation

It ONLY:

1. Validates the exact LIDVID request + label_url cross-binding.
2. Fetches the XML label via a single HTTPS GET (no redirects).
3. Validates HTTP transport semantics.
4. Validates XML security (DOCTYPE/ENTITY rejection, bounded parsing).
5. Validates PDS4 namespace, product class, IM version, and identity.
6. Validates required observation facts and context references.
7. Normalizes output into :class:`PdsScienceProduct`.
8. Produces an ``EXTERNAL_AUTHORITATIVE`` :class:`ProvenanceRecord`.
9. Returns the normalized product + provenance.

Transport semantics (Phase 6E-C3B)
------------------------------------
EXACTLY ONE HTTP GET is performed per fetch.  ``follow_redirects=False``
is enforced at the ``send()`` level.

- 3xx responses → :class:`PdsArchiveLabelValidationError` (never followed).
- 404/429/5xx   → :class:`PdsArchiveLabelUnavailableError`.
- other 4xx     → :class:`PdsArchiveLabelValidationError`.
- timeout/network → :class:`PdsArchiveLabelUnavailableError`.
- Maximum HTTP GETs per fetch: **1**.

XML security
------------
- ``<!DOCTYPE`` and ``<!ENTITY`` (case-insensitive) are rejected before
  parsing — prevents XXE, Billion-Laughs, and DTD-based SSRF.
- ``xml.etree.ElementTree`` is used with a bounded in-memory parse.
- No entity resolution, no XInclude, no schemaLocation fetch, no network.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

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

from .pds_archive_models import (
    PdsArchiveLabelCapture,
    PdsArchiveLabelRequest,
    _ARCHIVE_HOST,
    _ARCHIVE_PATH_PREFIX,
    _ARCHIVE_SCHEME,
    _LIDVID_CROSS_RE,
    _SUPPORTED_IM_VERSION,
)
from .pds_models import (
    PdsDataFile,
    PdsScienceProduct,
)


# ---------------------------------------------------------------------------
# Adapter-specific typed errors
# ---------------------------------------------------------------------------


class PdsArchiveLabelError(Exception):
    """Base class for all PDS archive-label adapter failures.

    Catch this class to handle any archive-label-specific error.
    Catch the subclasses to distinguish availability from validation.
    """


class PdsArchiveLabelUnavailableError(PdsArchiveLabelError, MissionSourceUnavailableError):
    """PDS Atmospheres Node archive is unreachable or the label is unavailable.

    Raised for:
    - Network timeouts and connection failures
    - HTTP 5xx server errors
    - HTTP 429 rate-limiting
    - HTTP 404: label not found at this URL
    """


class PdsArchiveLabelValidationError(PdsArchiveLabelError, MissionSourceValidationError):
    """PDS returned a response that fails domain validation.

    Raised for:
    - HTTP 3xx redirects (never followed)
    - HTTP 4xx client errors (except 404/429)
    - Oversized response body
    - XML security violation (DOCTYPE / ENTITY detected)
    - Malformed XML
    - Wrong PDS4 namespace or information model version
    - Wrong product class
    - Identity mismatch (LIDVID, logical_identifier, version_id)
    - Missing or invalid observation timestamps
    - Missing or wrong processing level
    - Missing required context references
    - Invalid file area structure
    """


# ---------------------------------------------------------------------------
# Protocol constants — adapter-owned, NOT configurable by callers
# ---------------------------------------------------------------------------

# HTTP Accept header for XML label responses.
_ACCEPT_XML: str = "application/xml, text/xml"

# Source system string for provenance.
_ARCHIVE_SOURCE_SYSTEM: str = "NASA Planetary Data System Atmospheres Node"

# Maximum raw response body size (2 MiB).
MAX_ARCHIVE_LABEL_BYTES: int = 2 * 1024 * 1024

# HTTP request timeout (seconds).
_HTTP_TIMEOUT: float = 30.0

# Required PDS4 XML namespace.
_PDS_NS: str = "http://pds.nasa.gov/pds4/pds/v1"
_PDS_NS_BRACED: str = f"{{{_PDS_NS}}}"

# Required root element (without namespace braces).
_PRODUCT_OBSERVATIONAL: str = "Product_Observational"

# Required processing level for MWR calibrated products.
_REQUIRED_PROCESSING_LEVEL: str = "Calibrated"

# Required context URNs and reference_type values.
_REQUIRED_REFS: tuple[tuple[str, str, str], ...] = (
    # (reference_type, lid, human-name)
    (
        "data_to_investigation",
        "urn:nasa:pds:context:investigation:mission.juno",
        "Juno investigation",
    ),
    (
        "is_instrument",
        "urn:nasa:pds:context:instrument:mwr.jno",
        "MWR instrument",
    ),
    (
        "is_instrument_host",
        "urn:nasa:pds:context:instrument_host:spacecraft.jno",
        "Juno spacecraft",
    ),
    (
        "data_to_target",
        "urn:nasa:pds:context:target:planet.jupiter",
        "Jupiter target",
    ),
)

# Regex to detect <!DOCTYPE and <!ENTITY (case-insensitive).
_XML_DOCTYPE_ENTITY_RE = re.compile(
    rb"<!(?:DOCTYPE|ENTITY)\b",
    re.IGNORECASE,
)

# Notes text template for provenance.
_FILE_REF_DERIVATION_NOTE: str = (
    "file_ref derived from label URI directory + label-reported file_name; "
    "not source-reported from XML. "
    "Archive snapshot authenticates XML label bytes only; "
    "CSV science payload bytes are NOT authenticated."
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


def _build_archive_provenance_id_input(
    request: PdsArchiveLabelRequest,
) -> str:
    """Return the deterministic JSON identity string for provenance_id computation."""
    identity = json.dumps(
        {
            "adapter": "gcsi:pds_archive_label:v1",
            "lidvid": request.lidvid,
            "label_url": request.label_url,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return identity


def _compute_archive_provenance_id(
    identity: str,
    content_sha256: str,
) -> str:
    """Compute a deterministic provenance_id from request identity + content hash.

    Formula::

        SHA-256(identity + "|" + content_sha256)

    Returns the hex digest string.
    """
    combined = identity + "|" + content_sha256
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# XML helper utilities
# ---------------------------------------------------------------------------


def _find_one(
    parent: ET.Element,
    tag: str,
    ns: str = _PDS_NS,
    required: bool = True,
    context: str = "",
) -> Optional[ET.Element]:
    """Find exactly one child element with a given tag in the PDS namespace.

    Raises PdsArchiveLabelValidationError if required and not found, or if
    more than one is found.
    """
    found = parent.findall(f"{{{ns}}}{tag}")
    if len(found) == 0:
        if required:
            raise PdsArchiveLabelValidationError(
                f"PDS4 label is missing required element <{tag}>{context}."
            )
        return None
    if len(found) > 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label has {len(found)} <{tag}> elements; "
            f"expected exactly 1{context}."
        )
    return found[0]


def _text_required(elem: ET.Element, context: str = "") -> str:
    """Extract non-empty text content from an element.

    Raises PdsArchiveLabelValidationError if text is missing or empty.
    """
    raw = (elem.text or "").strip()
    if not raw:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label element {context!r} has empty or missing text content."
        )
    return raw


def _parse_archive_datetime(raw: str, field_name: str) -> datetime:
    """Parse a PDS4 datetime string into a timezone-aware UTC datetime.

    Raises PdsArchiveLabelValidationError for malformed or naive values.
    """
    raw = raw.strip()
    if not raw:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label datetime field {field_name!r} is empty."
        )
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label datetime field {field_name!r} could not be parsed as ISO 8601."
        ) from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label datetime field {field_name!r} is naive; "
            "timezone-aware value required."
        )
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Core XML validator — shared by live adapter and snapshot reload
# ---------------------------------------------------------------------------


def _validate_pds_archive_label_response(
    request: PdsArchiveLabelRequest,
    raw_bytes: bytes,
    retrieved_at: datetime,
) -> tuple[PdsScienceProduct, ProvenanceRecord]:
    """Validate raw PDS4 archive-label XML bytes and produce normalized output.

    This is a pure function: it performs NO HTTP requests.  It is the single
    authoritative validation boundary for both the live adapter and the
    offline archive snapshot reload path.

    Parameters
    ----------
    request:
        The original :class:`PdsArchiveLabelRequest` (LIDVID + label_url).

    raw_bytes:
        Exact raw HTTP response body bytes.  Must not exceed
        ``MAX_ARCHIVE_LABEL_BYTES`` (2 MiB).  Provenance SHA-256 is computed
        from these exact bytes — they are never re-serialized or altered.

    retrieved_at:
        Timezone-aware datetime representing when the response was acquired.
        Normalized to UTC.

    Returns
    -------
    tuple[PdsScienceProduct, ProvenanceRecord]
        Fully validated normalized product and EXTERNAL_AUTHORITATIVE
        provenance record.

    Raises
    ------
    PdsArchiveLabelValidationError
        For structural or semantic validation failures.

    PdsArchiveLabelUnavailableError
        Not raised by this function — transport errors come from the adapter.
    """
    # 0. Validate retrieved_at before any parsing work.
    if not isinstance(retrieved_at, datetime):
        raise PdsArchiveLabelValidationError(
            "retrieved_at did not receive a datetime object."
        )
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise PdsArchiveLabelValidationError(
            "retrieved_at must be timezone-aware."
        )
    retrieved_at_utc = retrieved_at.astimezone(timezone.utc)

    # 1. Enforce raw response size limit.
    if len(raw_bytes) > MAX_ARCHIVE_LABEL_BYTES:
        raise PdsArchiveLabelValidationError(
            f"PDS4 archive label response body exceeds maximum allowed size "
            f"({MAX_ARCHIVE_LABEL_BYTES} bytes)."
        )

    # 2. Hash the raw bytes before any decoding.
    content_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    # 3. XML security: reject DOCTYPE and ENTITY before parsing.
    if _XML_DOCTYPE_ENTITY_RE.search(raw_bytes):
        raise PdsArchiveLabelValidationError(
            "PDS4 label rejected: DOCTYPE or ENTITY declaration detected. "
            "External entity expansion is not permitted."
        )

    # 4. Parse XML.
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        raise PdsArchiveLabelValidationError(
            "PDS4 label XML is malformed and could not be parsed."
        ) from exc

    # 5. Validate root element namespace and tag.
    expected_root_tag = f"{_PDS_NS_BRACED}{_PRODUCT_OBSERVATIONAL}"
    if root.tag != expected_root_tag:
        raise PdsArchiveLabelValidationError(
            "PDS4 label root element is not Product_Observational in the "
            "expected PDS namespace. "
            f"Expected namespace: {_PDS_NS!r}."
        )

    # 6. Validate Identification_Area.
    id_area = _find_one(root, "Identification_Area")

    # 6a. logical_identifier
    lid_elem = _find_one(id_area, "logical_identifier")
    label_lid = _text_required(lid_elem, "logical_identifier")

    # 6b. version_id
    ver_elem = _find_one(id_area, "version_id")
    label_version = _text_required(ver_elem, "version_id")

    # 6c. information_model_version
    imv_elem = _find_one(id_area, "information_model_version")
    im_version = _text_required(imv_elem, "information_model_version")
    if im_version != _SUPPORTED_IM_VERSION:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label information_model_version {im_version!r} is not supported. "
            f"Only {_SUPPORTED_IM_VERSION!r} is accepted."
        )

    # 6d. product_class
    pc_elem = _find_one(id_area, "product_class")
    label_product_class = _text_required(pc_elem, "product_class")
    if label_product_class != _PRODUCT_OBSERVATIONAL:
        raise PdsArchiveLabelValidationError(
            "PDS4 label product_class is not 'Product_Observational'. "
            "Only observational products are supported by this adapter."
        )

    # 6e. title
    title_elem = _find_one(id_area, "title")
    label_title = _text_required(title_elem, "title")

    # 7. Cross-validate identity against request LIDVID.
    req_m = _LIDVID_CROSS_RE.match(request.lidvid)
    # Already validated by PdsArchiveLabelRequest, but guard defensively.
    if req_m is None:
        raise PdsArchiveLabelValidationError(
            "Request LIDVID failed cross-binding (internal error)."
        )
    req_pdsver = req_m.group(6)  # version component after ::

    # Compose expected LID and LIDVID from label fields.
    # The request LIDVID encodes the LID, which must match the label's
    # logical_identifier.
    expected_lid, _ = request.lidvid.rsplit("::", 1)

    if label_lid != expected_lid:
        raise PdsArchiveLabelValidationError(
            "PDS4 label logical_identifier does not match the request LIDVID "
            "LID component. Identity cross-check failed."
        )
    if label_version != req_pdsver:
        raise PdsArchiveLabelValidationError(
            "PDS4 label version_id does not match the request LIDVID version "
            "component. Identity cross-check failed."
        )

    # Compose LIDVID from label-reported fields (must equal request.lidvid).
    label_lidvid = f"{label_lid}::{label_version}"
    if label_lidvid != request.lidvid:
        raise PdsArchiveLabelValidationError(
            "PDS4 label LIDVID (derived from logical_identifier::version_id) "
            "does not match the requested LIDVID."
        )

    # 8. Observation_Area.
    obs_area = _find_one(root, "Observation_Area")

    # 8a. Time_Coordinates — required (start and stop must be present).
    time_coords = _find_one(obs_area, "Time_Coordinates")
    start_elem = _find_one(time_coords, "start_date_time")
    stop_elem = _find_one(time_coords, "stop_date_time")

    start_raw = _text_required(start_elem, "start_date_time")
    stop_raw = _text_required(stop_elem, "stop_date_time")

    obs_start_utc = _parse_archive_datetime(start_raw, "start_date_time")
    obs_stop_utc = _parse_archive_datetime(stop_raw, "stop_date_time")

    if obs_start_utc > obs_stop_utc:
        raise PdsArchiveLabelValidationError(
            "PDS4 label start_date_time is after stop_date_time."
        )

    # 8b. Primary_Result_Summary.processing_level — required, must be Calibrated.
    prs_elem = _find_one(obs_area, "Primary_Result_Summary")
    proc_elem = _find_one(prs_elem, "processing_level")
    processing_level = _text_required(proc_elem, "processing_level")
    if processing_level != _REQUIRED_PROCESSING_LEVEL:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label processing_level is {processing_level!r}; "
            f"only {_REQUIRED_PROCESSING_LEVEL!r} is accepted."
        )

    # 8c. Investigation_Area / Observing_System / Target_Identification.
    # Collect all Internal_Reference elements within Observation_Area.
    # Required refs are checked by (reference_type, lid) pair.
    all_internal_refs: list[ET.Element] = list(
        obs_area.iter(f"{_PDS_NS_BRACED}Internal_Reference")
    )
    # Build set of (reference_type, lid) found.
    found_refs: set[tuple[str, str]] = set()
    for ir in all_internal_refs:
        lid_ref_elem = ir.find(f"{_PDS_NS_BRACED}lid_reference")
        ref_type_elem = ir.find(f"{_PDS_NS_BRACED}reference_type")
        if lid_ref_elem is not None and ref_type_elem is not None:
            lid_ref = (lid_ref_elem.text or "").strip()
            ref_type = (ref_type_elem.text or "").strip()
            if lid_ref and ref_type:
                found_refs.add((ref_type, lid_ref))

    for req_ref_type, req_ref_lid, req_ref_name in _REQUIRED_REFS:
        if (req_ref_type, req_ref_lid) not in found_refs:
            raise PdsArchiveLabelValidationError(
                f"PDS4 label is missing required context reference: "
                f"{req_ref_name} "
                f"(reference_type={req_ref_type!r}, lid={req_ref_lid!r})."
            )

    # 9. File_Area_Observational — exactly one, with exactly one File child.
    file_area = _find_one(root, "File_Area_Observational")
    file_elem = _find_one(file_area, "File")

    # 9a. File/file_name — required.
    fname_elem = _find_one(file_elem, "file_name")
    label_file_name = _text_required(fname_elem, "file_name")

    # 9b. File/file_size — required, unit must be byte.
    fsize_elem = _find_one(file_elem, "file_size")
    fsize_raw = _text_required(fsize_elem, "file_size")
    # Check unit attribute is "byte".
    fsize_unit = (fsize_elem.get("unit") or "").strip().lower()
    if fsize_unit != "byte":
        raise PdsArchiveLabelValidationError(
            f"PDS4 label file_size unit must be 'byte'; got {fsize_unit!r}."
        )
    try:
        file_size_bytes = int(fsize_raw)
    except (ValueError, TypeError) as exc:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_size is not a valid integer."
        ) from exc
    if file_size_bytes < 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_size must be non-negative."
        )

    # 9c. md5_checksum — NOT required per C3A observations; reject if present
    #     (the C3A spec notes there is no md5_checksum in the XML).
    md5_elem = file_elem.find(f"{_PDS_NS_BRACED}md5_checksum")
    if md5_elem is not None:
        raise PdsArchiveLabelValidationError(
            "PDS4 label contains an unexpected md5_checksum element. "
            "MWR calibrated labels do not include md5_checksum."
        )

    # 9d. Derive file_ref from label URL directory + label-reported file_name.
    # This is NOT source-reported — it is deterministically derived.
    parsed_url = urlparse(request.label_url)
    label_dir = parsed_url.path.rsplit("/", 1)[0] + "/"
    file_ref = f"https://{_ARCHIVE_HOST}{label_dir}{label_file_name}"

    # 10. Build normalized data file (one file, no md5, no mime in archive labels).
    try:
        data_file = PdsDataFile(
            file_name=label_file_name,
            file_ref=file_ref,
            file_size_bytes=file_size_bytes,
            md5_checksum=None,
            mime_type=None,
        )
    except pydantic.ValidationError as exc:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label file metadata failed validation."
        ) from exc

    data_files = (data_file,)
    total_data_size_bytes = file_size_bytes

    # 11. Build normalized PdsScienceProduct.
    try:
        product = PdsScienceProduct(
            lid=label_lid,
            lidvid=label_lidvid,
            logical_identifier=label_lid,
            version_id=label_version,
            product_class=_PRODUCT_OBSERVATIONAL,
            title=label_title,
            observation_start_utc=obs_start_utc,
            observation_stop_utc=obs_stop_utc,
            processing_level=processing_level,
            instrument_lids=(
                "urn:nasa:pds:context:instrument:mwr.jno",
            ),
            instrument_host_lids=(
                "urn:nasa:pds:context:instrument_host:spacecraft.jno",
            ),
            investigation_lids=(
                "urn:nasa:pds:context:investigation:mission.juno",
            ),
            target_lids=(
                "urn:nasa:pds:context:target:planet.jupiter",
            ),
            data_files=data_files,
            total_data_size_bytes=total_data_size_bytes,
            registry_node=None,
            registry_harvested_at=None,
        )
    except pydantic.ValidationError as exc:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label normalized product failed internal validation."
        ) from exc

    # 12. Build provenance_id.
    identity = _build_archive_provenance_id_input(request)
    provenance_id = _compute_archive_provenance_id(identity, content_sha256)

    # 13. Build ProvenanceRecord.
    provenance = ProvenanceRecord(
        provenance_id=provenance_id,
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system=_ARCHIVE_SOURCE_SYSTEM,
        source_version=im_version,  # information_model_version from label
        source_record_id=request.lidvid,
        source_uri=request.label_url,
        observed_at=None,
        retrieved_at=retrieved_at_utc,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
        notes=_FILE_REF_DERIVATION_NOTE,
    )

    return product, provenance


# ---------------------------------------------------------------------------
# PdsArchiveLabelAdapter
# ---------------------------------------------------------------------------


class PdsArchiveLabelAdapter:
    """Lower-level source adapter that fetches validated PDS4 XML archive labels
    from the NASA PDS Atmospheres Node file server.

    Parameters
    ----------
    client:
        Optional injected ``httpx.Client``.  If ``None``, the adapter creates
        and owns its own client (and closes it when used as a context manager).
        If a client is injected, the adapter does NOT close it.

    clock:
        Optional callable returning an aware UTC ``datetime`` used as
        ``retrieved_at``.  Defaults to :func:`_utc_now`.

    Notes
    -----
    - Exactly ONE HTTP GET per fetch.  Redirects are never followed.
    - ``follow_redirects=False`` is enforced at the ``send()`` level.
    - Maximum response body: 2 MiB.
    - XML DOCTYPE/ENTITY is rejected before parsing.
    """

    def __init__(
        self,
        client: Optional[httpx.Client] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._injected_client = client is not None
        self._client: httpx.Client = (
            client
            if client is not None
            else httpx.Client(
                timeout=_HTTP_TIMEOUT,
                follow_redirects=False,
            )
        )
        self._clock: Callable[[], datetime] = clock if clock is not None else _utc_now

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "PdsArchiveLabelAdapter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the internally-owned HTTP client (if any)."""
        if not self._injected_client:
            self._client.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch(
        self, request: PdsArchiveLabelRequest
    ) -> tuple[PdsScienceProduct, ProvenanceRecord]:
        """Fetch and validate an archive label for one exact PDS LIDVID.

        Returns
        -------
        tuple[PdsScienceProduct, ProvenanceRecord]
            Normalized science product metadata and EXTERNAL_AUTHORITATIVE
            provenance record.

        Raises
        ------
        PdsArchiveLabelUnavailableError
            Network/transport failure, HTTP 5xx, HTTP 429, HTTP 404.

        PdsArchiveLabelValidationError
            HTTP 3xx, HTTP 4xx (non-404/429), oversized response,
            XML security violation, identity mismatch, etc.
        """
        capture = self.fetch_capture(request)
        return capture.product, capture.provenance

    def fetch_capture(
        self, request: PdsArchiveLabelRequest
    ) -> PdsArchiveLabelCapture:
        """Fetch, validate, and capture an archive label for one exact LIDVID.

        Returns a :class:`PdsArchiveLabelCapture` that bundles the validated
        product, provenance, and the exact raw XML label bytes.

        Parameters
        ----------
        request:
            Validated :class:`PdsArchiveLabelRequest` specifying the LIDVID
            and the exact label URL.

        Returns
        -------
        PdsArchiveLabelCapture
            Immutable capture object.

        Raises
        ------
        PdsArchiveLabelUnavailableError
            Network/transport failure, HTTP 5xx, HTTP 429, HTTP 404.

        PdsArchiveLabelValidationError
            HTTP 3xx, other HTTP 4xx, oversized response, XML issues.
        """
        # Record retrieved_at BEFORE the request.
        retrieved_at = self._clock()
        if not isinstance(retrieved_at, datetime):
            raise PdsArchiveLabelValidationError(
                "Archive adapter clock did not return a datetime."
            )
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise PdsArchiveLabelValidationError(
                "Archive adapter clock returned a naive datetime; "
                "timezone-aware value required."
            )

        # Build the HTTP request.
        http_request = httpx.Request(
            "GET",
            request.label_url,
            headers={"Accept": _ACCEPT_XML},
        )

        # Perform EXACTLY ONE HTTP GET — redirects are never followed.
        try:
            response = self._client.send(http_request, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label request timed out."
            ) from exc
        except httpx.NetworkError as exc:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label request failed due to a "
                "network error."
            ) from exc
        except httpx.HTTPError as exc:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label request failed."
            ) from exc

        status = response.status_code

        # Handle 3xx — never follow redirects.
        if 300 <= status <= 399:
            raise PdsArchiveLabelValidationError(
                "PDS Atmospheres Node archive label request returned a redirect. "
                "Redirects are not followed by this adapter."
            )

        # Handle 404 / 429 / 5xx as unavailable.
        if status == 404:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label was not found (HTTP 404)."
            )
        if status == 429:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label request was rate-limited (HTTP 429)."
            )
        if 500 <= status <= 599:
            raise PdsArchiveLabelUnavailableError(
                "PDS Atmospheres Node archive label request returned a server error."
            )

        # Handle other 4xx as validation errors.
        if 400 <= status <= 499:
            raise PdsArchiveLabelValidationError(
                f"PDS Atmospheres Node archive label request returned an "
                f"unexpected client error."
            )

        # 200 expected for success; any other status is a validation error.
        if status != 200:
            raise PdsArchiveLabelValidationError(
                "PDS Atmospheres Node archive label request returned an "
                "unexpected HTTP status."
            )

        # Bounded body read: read at most MAX + 1 bytes.
        # response.content is already the full body for httpx.
        raw_bytes = response.content
        if len(raw_bytes) > MAX_ARCHIVE_LABEL_BYTES:
            raise PdsArchiveLabelValidationError(
                f"PDS4 archive label response body exceeds maximum allowed size "
                f"({MAX_ARCHIVE_LABEL_BYTES} bytes)."
            )

        # Validate and normalize.
        product, provenance = _validate_pds_archive_label_response(
            request=request,
            raw_bytes=raw_bytes,
            retrieved_at=retrieved_at,
        )

        # Build and return capture.
        try:
            capture = PdsArchiveLabelCapture(
                request=request,
                product=product,
                provenance=provenance,
                raw_label=raw_bytes,
            )
        except pydantic.ValidationError as exc:
            raise PdsArchiveLabelValidationError(
                "PDS archive label capture failed self-consistency validation."
            ) from exc

        return capture
