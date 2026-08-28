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
- Archive label XML must be non-empty, NUL-free, and strict UTF-8 before any
  parsing.  A NUL byte anywhere in the raw bytes is rejected immediately —
  this closes UTF-16 / UTF-32 NUL-interleaving bypass.
- The XML declaration ``encoding`` attribute, if present, must be ``UTF-8``
  (case-insensitive).  Non-UTF-8 declarations are rejected; the bytes are
  never transcoded.
- ``<!DOCTYPE`` and ``<!ENTITY`` (case-insensitive) are scanned on the
  already-validated UTF-8 decoded text.
- ElementTree receives the decoded Unicode string (``ET.fromstring(xml_text)``)
  so that the scan and the parse operate on the same representation.  Raw bytes
  are never re-encoded; SHA-256 is computed from the original ``raw_bytes``.
- BOM (UTF-8 BOM ``\\xef\\xbb\\xbf``) is **rejected** — an unexpected BOM on a
  UTF-8 stream signals a potentially non-canonical encoding.
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
from urllib.parse import urlsplit

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

# Explicit streaming chunk size for bounded body reads (64 KiB).
# Using a finite chunk size ensures that a single oversized transport chunk
# cannot be appended to memory before the limit check fires.
_STREAM_CHUNK_BYTES: int = 64 * 1024

# HTTP request timeout (seconds).
_HTTP_TIMEOUT: float = 30.0

# Required PDS4 XML namespace.
_PDS_NS: str = "http://pds.nasa.gov/pds4/pds/v1"
_PDS_NS_BRACED: str = f"{{{_PDS_NS}}}"

# Required root element (without namespace braces).
_PRODUCT_OBSERVATIONAL: str = "Product_Observational"

# Required processing level for MWR calibrated products.
_REQUIRED_PROCESSING_LEVEL: str = "Calibrated"

# Notes text template for provenance.
_FILE_REF_DERIVATION_NOTE: str = (
    "file_ref derived from label URI directory + label-reported file_name; "
    "not source-reported from XML. "
    "Archive snapshot authenticates XML label bytes only; "
    "CSV science payload bytes are NOT authenticated."
)

# Strict ASCII decimal integer pattern for file_size values.
# Rejects: +1, -1, 1.0, 1e3, empty, whitespace, non-ASCII numerals.
_ASCII_DECIMAL_RE = re.compile(r"^[0-9]+$")

# Pattern to inspect the XML declaration encoding attribute.
# Matches: encoding="..." or encoding='...' within the first 256 bytes of XML.
# Group 1 captures the declared encoding value.
_XML_ENCODING_DECL_RE = re.compile(
    r"""encoding\s*=\s*(?:"([^"]+)"|'([^']+)')""",
    re.IGNORECASE,
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
    """Find exactly one direct child element with a given tag in the PDS namespace.

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


def _find_all_direct(
    parent: ET.Element,
    tag: str,
    ns: str = _PDS_NS,
) -> list[ET.Element]:
    """Find all direct children with a given tag in the PDS namespace."""
    return parent.findall(f"{{{ns}}}{tag}")


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
# XML security scanner (PART I)
# ---------------------------------------------------------------------------


def _scan_xml_security(raw_bytes: bytes) -> str:
    """Validate XML encoding safety and reject DOCTYPE/ENTITY declarations.

    Strict octet contract (applied in order):

    1. Non-empty.
    2. No NUL byte (0x00) anywhere — rejects UTF-16/UTF-32 NUL-interleaving.
    3. No UTF-8 BOM (0xEF 0xBB 0xBF) — rejected as non-canonical.
    4. Strict UTF-8 decode succeeds and contains no U+0000.
    5. XML declaration encoding attribute, if present, must be ``UTF-8``
       (case-insensitive).  Any other declared encoding is rejected.
    6. No ``<!DOCTYPE`` or ``<!ENTITY`` tokens (case-insensitive) in the
       decoded text.

    ElementTree MUST receive ``xml_text`` (the return value of this function),
    NOT ``raw_bytes``, so that the scan and the parse operate on the identical
    representation.  SHA-256 is always computed from ``raw_bytes``.

    Parameters
    ----------
    raw_bytes:
        Raw XML bytes as received from the HTTP response.

    Returns
    -------
    str
        The strict UTF-8 decoded text, already validated.  Caller must use
        this string for ``ET.fromstring()``.

    Raises
    ------
    PdsArchiveLabelValidationError
        For any octet-contract or security violation.
    """
    # Step 1: Non-empty.
    if not raw_bytes:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label XML body is empty."
        )

    # Step 2: Reject NUL bytes BEFORE UTF-8 decode.
    # This closes UTF-16/UTF-32 NUL-interleaving bypass: UTF-16LE ASCII content
    # produces bytes like b'<\x00!\x00D\x00O\x00C\x00T\x00Y\x00P\x00E\x00'
    # which may survive UTF-8 decoding (NUL is a valid UTF-8 codepoint) but
    # the resulting Unicode string would not contain contiguous '<!DOCTYPE'.
    # Rejecting NUL bytes eliminates the entire class of attack.
    if b"\x00" in raw_bytes:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label XML contains NUL bytes (0x00). "
            "UTF-16/UTF-32 encoded XML is not permitted; only strict UTF-8 is accepted."
        )

    # Step 3: Reject UTF-8 BOM.
    # A BOM on a UTF-8 stream is unexpected and signals a potentially non-canonical
    # encoding.  We reject it explicitly to maintain a simple, testable contract.
    if raw_bytes[:3] == b"\xef\xbb\xbf":
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label XML contains a UTF-8 BOM (byte-order mark). "
            "BOM-prefixed UTF-8 is not accepted; use plain UTF-8 without BOM."
        )

    # Step 4: Strict UTF-8 decode.
    try:
        xml_text = raw_bytes.decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label XML must be valid UTF-8; "
            "the response body failed UTF-8 decoding."
        ) from exc

    # Sanity: decoded text must not contain U+0000 (should be guaranteed by the
    # NUL-byte check above, but we verify at the Unicode level as well).
    if "\x00" in xml_text:
        raise PdsArchiveLabelValidationError(
            "PDS4 archive label XML contains U+0000 characters after UTF-8 decoding. "
            "Rejected."
        )

    # Step 5: XML declaration encoding check.
    # Inspect only the first 256 decoded characters (the declaration is always at
    # the start).  If an encoding attribute is present, it must be 'UTF-8'.
    decl_head = xml_text[:256]
    enc_match = _XML_ENCODING_DECL_RE.search(decl_head)
    if enc_match is not None:
        declared_enc = (enc_match.group(1) or enc_match.group(2) or "").strip()
        if declared_enc.upper() != "UTF-8":
            raise PdsArchiveLabelValidationError(
                f"PDS4 archive label XML declares encoding {declared_enc!r}; "
                "only UTF-8 is accepted.  Transcoding is not performed."
            )

    # Step 6: Scan decoded text for DOCTYPE/ENTITY (case-insensitive).
    # The scan operates on the same decoded text that will be passed to
    # ET.fromstring(), eliminating any encoding-parser differential.
    upper_text = xml_text.upper()
    if "<!DOCTYPE" in upper_text:
        raise PdsArchiveLabelValidationError(
            "PDS4 label rejected: DOCTYPE declaration detected. "
            "External entity expansion is not permitted."
        )
    if "<!ENTITY" in upper_text:
        raise PdsArchiveLabelValidationError(
            "PDS4 label rejected: ENTITY declaration detected. "
            "External entity expansion is not permitted."
        )

    return xml_text


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

    # 3. XML security: validate UTF-8 encoding and reject DOCTYPE/ENTITY.
    #    Returns the decoded UTF-8 text; ET.fromstring() receives this string
    #    so that the security scan and the parse operate on the same
    #    representation — eliminating any encoding-parser differential.
    xml_text = _scan_xml_security(raw_bytes)

    # 4. Parse XML from the already-validated Unicode text (NOT raw_bytes).
    #    SHA-256 was already computed from raw_bytes above.
    try:
        root = ET.fromstring(xml_text)
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

    # 8c. PART E — Structure-aware context validation.
    #
    # Validate each required context reference from its specific reviewed
    # PDS4 structure within Observation_Area, not from a global flat set.
    #
    # Investigation_Area: exactly one, with exactly one suitable Internal_Reference.
    investigation_areas = _find_all_direct(obs_area, "Investigation_Area")
    if len(investigation_areas) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required element <Investigation_Area> "
            "in Observation_Area."
        )
    if len(investigation_areas) > 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label has {len(investigation_areas)} <Investigation_Area> elements "
            "in Observation_Area; expected exactly 1."
        )
    inv_area = investigation_areas[0]
    # Require exactly one direct Internal_Reference inside Investigation_Area.
    # No first-match/break: duplicate valid refs fail closed.
    inv_refs = _find_all_direct(inv_area, "Internal_Reference")
    if len(inv_refs) != 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label Investigation_Area must contain exactly 1 "
            f"<Internal_Reference>; found {len(inv_refs)}."
        )
    _inv_ir = inv_refs[0]
    _inv_lid_elem = _inv_ir.find(f"{_PDS_NS_BRACED}lid_reference")
    _inv_ref_type_elem = _inv_ir.find(f"{_PDS_NS_BRACED}reference_type")
    _inv_lid = (_inv_lid_elem.text or "").strip() if _inv_lid_elem is not None else ""
    _inv_ref_type = (_inv_ref_type_elem.text or "").strip() if _inv_ref_type_elem is not None else ""
    if (
        _inv_lid != "urn:nasa:pds:context:investigation:mission.juno"
        or _inv_ref_type != "data_to_investigation"
    ):
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required context reference: "
            "Juno investigation (reference_type='data_to_investigation', "
            "lid='urn:nasa:pds:context:investigation:mission.juno') "
            "inside Investigation_Area."
        )

    # Observing_System: EXACTLY ONE — C3B.2 narrows from one-or-more to exactly one.
    observing_systems = _find_all_direct(obs_area, "Observing_System")
    if len(observing_systems) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required element <Observing_System> "
            "in Observation_Area."
        )
    if len(observing_systems) > 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label has {len(observing_systems)} <Observing_System> elements "
            "in Observation_Area; expected exactly 1."
        )
    os_elem = observing_systems[0]

    # Inside the single Observing_System, find exactly one valid Instrument
    # component and exactly one valid Spacecraft component.
    #
    # A valid Instrument component requires ALL of:
    #   - Observing_System_Component/type == "Instrument"
    #   - Internal_Reference/lid_reference == urn:nasa:pds:context:instrument:mwr.jno
    #   - Internal_Reference/reference_type == is_instrument
    #
    # A valid Spacecraft component requires ALL of:
    #   - Observing_System_Component/type == "Spacecraft"
    #   - Internal_Reference/lid_reference == urn:nasa:pds:context:instrument_host:spacecraft.jno
    #   - Internal_Reference/reference_type == is_instrument_host
    #
    # Duplicate type child elements and duplicate Internal_Reference children
    # are rejected.  Wrong type/LID combinations are rejected.
    _instrument_refs: list[tuple[str, str]] = []   # (lid_ref, ref_type) for valid Instrument
    _spacecraft_refs: list[tuple[str, str]] = []   # (lid_ref, ref_type) for valid Spacecraft

    components = _find_all_direct(os_elem, "Observing_System_Component")
    for comp in components:
        # Require exactly one direct <type> child element.
        comp_type_elems = comp.findall(f"{_PDS_NS_BRACED}type")
        if len(comp_type_elems) == 0:
            raise PdsArchiveLabelValidationError(
                "PDS4 label Observing_System_Component is missing required "
                "<type> element."
            )
        if len(comp_type_elems) > 1:
            raise PdsArchiveLabelValidationError(
                f"PDS4 label Observing_System_Component has {len(comp_type_elems)} "
                "<type> elements; expected exactly 1."
            )
        comp_type = (comp_type_elems[0].text or "").strip()

        # Require exactly one direct <Internal_Reference> child element.
        ir_elems = comp.findall(f"{_PDS_NS_BRACED}Internal_Reference")
        if len(ir_elems) == 0:
            raise PdsArchiveLabelValidationError(
                "PDS4 label Observing_System_Component is missing required "
                "<Internal_Reference> element."
            )
        if len(ir_elems) > 1:
            raise PdsArchiveLabelValidationError(
                f"PDS4 label Observing_System_Component has {len(ir_elems)} "
                "<Internal_Reference> elements; expected exactly 1."
            )
        ir = ir_elems[0]
        lid_ref_elem = ir.find(f"{_PDS_NS_BRACED}lid_reference")
        ref_type_elem = ir.find(f"{_PDS_NS_BRACED}reference_type")
        lid_ref = (lid_ref_elem.text or "").strip() if lid_ref_elem is not None else ""
        ref_type = (ref_type_elem.text or "").strip() if ref_type_elem is not None else ""

        # Check Instrument component: type MUST be "Instrument".
        if (
            lid_ref == "urn:nasa:pds:context:instrument:mwr.jno"
            and ref_type == "is_instrument"
        ):
            if comp_type != "Instrument":
                raise PdsArchiveLabelValidationError(
                    "PDS4 label Observing_System_Component with MWR instrument LID "
                    f"has component type {comp_type!r}; expected 'Instrument'."
                )
            _instrument_refs.append((lid_ref, ref_type))

        # Check Spacecraft component: type MUST be "Spacecraft".
        elif (
            lid_ref == "urn:nasa:pds:context:instrument_host:spacecraft.jno"
            and ref_type == "is_instrument_host"
        ):
            if comp_type != "Spacecraft":
                raise PdsArchiveLabelValidationError(
                    "PDS4 label Observing_System_Component with Juno spacecraft LID "
                    f"has component type {comp_type!r}; expected 'Spacecraft'."
                )
            _spacecraft_refs.append((lid_ref, ref_type))

        # Any other combination: verify the type is not masquerading as a known type
        # with a wrong LID (or vice versa).
        else:
            if comp_type == "Instrument":
                raise PdsArchiveLabelValidationError(
                    "PDS4 label Observing_System_Component has type 'Instrument' "
                    "but its lid_reference/reference_type does not match the "
                    "required MWR instrument identity."
                )
            if comp_type == "Spacecraft":
                raise PdsArchiveLabelValidationError(
                    "PDS4 label Observing_System_Component has type 'Spacecraft' "
                    "but its lid_reference/reference_type does not match the "
                    "required Juno spacecraft host identity."
                )

    if len(_instrument_refs) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required context reference: "
            "MWR instrument (type='Instrument', reference_type='is_instrument', "
            "lid='urn:nasa:pds:context:instrument:mwr.jno') "
            "inside an Observing_System_Component in Observing_System."
        )
    if len(_instrument_refs) > 1:
        raise PdsArchiveLabelValidationError(
            "PDS4 label has duplicate MWR instrument context references "
            "(type='Instrument', reference_type='is_instrument', "
            "lid='urn:nasa:pds:context:instrument:mwr.jno') "
            "inside Observing_System; expected exactly 1."
        )
    if len(_spacecraft_refs) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required context reference: "
            "Juno spacecraft (type='Spacecraft', reference_type='is_instrument_host', "
            "lid='urn:nasa:pds:context:instrument_host:spacecraft.jno') "
            "inside an Observing_System_Component in Observing_System."
        )
    if len(_spacecraft_refs) > 1:
        raise PdsArchiveLabelValidationError(
            "PDS4 label has duplicate Juno spacecraft context references "
            "(type='Spacecraft', reference_type='is_instrument_host', "
            "lid='urn:nasa:pds:context:instrument_host:spacecraft.jno') "
            "inside Observing_System; expected exactly 1."
        )

    # Target_Identification: exactly one, with exactly one direct Internal_Reference.
    # No first-match/break: duplicate valid refs fail closed.
    target_ids = _find_all_direct(obs_area, "Target_Identification")
    if len(target_ids) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required element <Target_Identification> "
            "in Observation_Area."
        )
    if len(target_ids) > 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label has {len(target_ids)} <Target_Identification> elements "
            "in Observation_Area; expected exactly 1."
        )
    tgt_area = target_ids[0]
    tgt_refs = _find_all_direct(tgt_area, "Internal_Reference")
    if len(tgt_refs) != 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label Target_Identification must contain exactly 1 "
            f"<Internal_Reference>; found {len(tgt_refs)}."
        )
    _tgt_ir = tgt_refs[0]
    _tgt_lid_elem = _tgt_ir.find(f"{_PDS_NS_BRACED}lid_reference")
    _tgt_ref_type_elem = _tgt_ir.find(f"{_PDS_NS_BRACED}reference_type")
    _tgt_lid = (_tgt_lid_elem.text or "").strip() if _tgt_lid_elem is not None else ""
    _tgt_ref_type = (_tgt_ref_type_elem.text or "").strip() if _tgt_ref_type_elem is not None else ""
    if (
        _tgt_lid != "urn:nasa:pds:context:target:planet.jupiter"
        or _tgt_ref_type != "data_to_target"
    ):
        raise PdsArchiveLabelValidationError(
            "PDS4 label is missing required context reference: "
            "Jupiter target (reference_type='data_to_target', "
            "lid='urn:nasa:pds:context:target:planet.jupiter') "
            "inside Target_Identification."
        )

    # 9. File_Area_Observational — exactly one, with exactly one File child.
    file_area = _find_one(root, "File_Area_Observational")
    file_elem = _find_one(file_area, "File")

    # PART F — Require exactly one direct Table_Delimited in File_Area_Observational.
    # A direct Header may be present; other children are not examined.
    table_delimited_elems = _find_all_direct(file_area, "Table_Delimited")
    if len(table_delimited_elems) == 0:
        raise PdsArchiveLabelValidationError(
            "PDS4 label File_Area_Observational is missing required "
            "<Table_Delimited> element. "
            "Exactly one direct Table_Delimited is required per the MWR calibrated contract."
        )
    if len(table_delimited_elems) > 1:
        raise PdsArchiveLabelValidationError(
            f"PDS4 label File_Area_Observational has {len(table_delimited_elems)} "
            "<Table_Delimited> elements; expected exactly 1."
        )

    # 9a. File/file_name — required.
    fname_elem = _find_one(file_elem, "file_name")
    label_file_name = _text_required(fname_elem, "file_name")

    # PART C — Safe XML file_name cross-binding.
    #
    # Validate: non-empty, no "/" or "\", no "..", no "?" or "#",
    # extension == ".csv" (case-insensitive), stem matches local product token.
    #
    # Do NOT use filesystem resolution.
    _validate_archive_file_name(label_file_name, request)

    # 9b. File/file_size — required, unit must be byte, strict ASCII decimal.
    fsize_elem = _find_one(file_elem, "file_size")
    fsize_raw = _text_required(fsize_elem, "file_size")
    # PART G: Strict ASCII decimal integer grammar.
    fsize_unit = (fsize_elem.get("unit") or "").strip().lower()
    if fsize_unit != "byte":
        raise PdsArchiveLabelValidationError(
            f"PDS4 label file_size unit must be 'byte'; got {fsize_unit!r}."
        )
    if not _ASCII_DECIMAL_RE.match(fsize_raw):
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_size must be a non-negative ASCII decimal integer "
            "(e.g. '12345'). Rejected: signs, floats, exponents, whitespace, "
            "and non-ASCII numerals."
        )
    file_size_bytes = int(fsize_raw)

    # 9c. PART H — Optional md5_checksum: align with approved C3B contract.
    #   - absent → None
    #   - exactly one → extract text, pass through PdsDataFile validation
    #   - duplicate → reject
    md5_elems = _find_all_direct(file_elem, "md5_checksum")
    if len(md5_elems) > 1:
        raise PdsArchiveLabelValidationError(
            "PDS4 label File contains duplicate <md5_checksum> elements; "
            "expected at most one."
        )
    md5_checksum: Optional[str]
    if len(md5_elems) == 0:
        md5_checksum = None
    else:
        md5_raw = (md5_elems[0].text or "").strip()
        if not md5_raw:
            raise PdsArchiveLabelValidationError(
                "PDS4 label <md5_checksum> element is present but has empty text."
            )
        # Validate through PdsDataFile's existing MD5 constraint.
        try:
            _tmp = PdsDataFile(
                file_name="tmp.csv",
                file_ref="https://pds-atmospheres.nmsu.edu/tmp.csv",
                file_size_bytes=0,
                md5_checksum=md5_raw,
                mime_type=None,
            )
            md5_checksum = _tmp.md5_checksum  # normalized (lowercased) by PdsDataFile
        except pydantic.ValidationError as exc:
            raise PdsArchiveLabelValidationError(
                f"PDS4 label md5_checksum value {md5_raw!r} failed validation: "
                "must be exactly 32 hexadecimal characters."
            ) from exc

    # 9d. PART C — Derive file_ref from label URL directory + validated basename.
    #
    # Only after filename validation may we derive file_ref.
    # Do NOT use generic urljoin() with untrusted input.
    # Construct from label URL parsed directory + validated basename, then
    # defensively verify the resulting URL.
    parsed_url = urlsplit(request.label_url)
    label_dir_path = parsed_url.path.rsplit("/", 1)[0] + "/"
    file_ref = f"https://{_ARCHIVE_HOST}{label_dir_path}{label_file_name}"

    # Defensive: parse derived file_ref and verify it stays in the same directory.
    _verify_derived_file_ref(file_ref, label_dir_path)

    # 10. Build normalized data file.
    try:
        data_file = PdsDataFile(
            file_name=label_file_name,
            file_ref=file_ref,
            file_size_bytes=file_size_bytes,
            md5_checksum=md5_checksum,
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
# PART C helpers — file_name validation and file_ref derivation
# ---------------------------------------------------------------------------


def _validate_archive_file_name(file_name: str, request: PdsArchiveLabelRequest) -> None:
    """Validate XML-reported file_name against the C3B safe-basename contract.

    Requirements:
    - non-empty
    - no "/" or "\"
    - no ".."
    - no "?" or "#"
    - extension must be ".csv" (case-insensitive)
    - stem (case-insensitive) must equal the local product token from the LIDVID

    Raises PdsArchiveLabelValidationError on any violation.
    """
    if not file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name is empty."
        )
    if "/" in file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name must not contain '/' (path traversal rejected)."
        )
    if "\\" in file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name must not contain '\\' (path traversal rejected)."
        )
    if ".." in file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name must not contain '..' (path traversal rejected)."
        )
    if "?" in file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name must not contain '?' (query-like injection rejected)."
        )
    if "#" in file_name:
        raise PdsArchiveLabelValidationError(
            "PDS4 label file_name must not contain '#' (fragment-like injection rejected)."
        )

    # Extension must be .csv (case-insensitive).
    if not file_name.lower().endswith(".csv"):
        raise PdsArchiveLabelValidationError(
            f"PDS4 label file_name {file_name!r} must have .csv extension "
            "(case-insensitive). Only CSV science files are expected."
        )

    # Stem must match the local product token from the LIDVID.
    # Do NOT use filesystem resolution.
    m = _LIDVID_CROSS_RE.match(request.lidvid)
    if m is None:
        raise PdsArchiveLabelValidationError(
            "Request LIDVID failed cross-binding during file_name validation (internal error)."
        )
    _pj, role, timestamp, reccode, localver, _pdsver = m.groups()
    expected_local_token = f"mwr{_pj}r{role}{timestamp}_{reccode}_{localver}"

    # Extract stem: filename without the last extension.
    stem = file_name.rsplit(".", 1)[0]

    if stem.casefold() != expected_local_token.casefold():
        raise PdsArchiveLabelValidationError(
            f"PDS4 label file_name stem {stem!r} does not match the expected "
            f"local product token {expected_local_token!r} from the LIDVID "
            "(case-insensitive cross-binding failed)."
        )


def _verify_derived_file_ref(file_ref: str, expected_dir_path: str) -> None:
    """Defensive check: ensure derived file_ref stays in the expected directory.

    Verifies:
    - scheme == "https"
    - hostname == pds-atmospheres.nmsu.edu
    - directory of file_ref path == expected_dir_path
    - no query
    - no fragment
    - no username/password

    Raises PdsArchiveLabelValidationError if any check fails.
    """
    try:
        p = urlsplit(file_ref)
    except Exception as exc:
        raise PdsArchiveLabelValidationError(
            "Derived file_ref could not be parsed."
        ) from exc

    if p.scheme != _ARCHIVE_SCHEME:
        raise PdsArchiveLabelValidationError(
            f"Derived file_ref has unexpected scheme {p.scheme!r}; expected 'https'."
        )
    if p.hostname != _ARCHIVE_HOST:
        raise PdsArchiveLabelValidationError(
            f"Derived file_ref points to unexpected host {p.hostname!r}; "
            f"expected {_ARCHIVE_HOST!r}."
        )
    if p.username is not None or p.password is not None:
        raise PdsArchiveLabelValidationError(
            "Derived file_ref must not contain userinfo."
        )
    if p.query:
        raise PdsArchiveLabelValidationError(
            "Derived file_ref must not contain a query string."
        )
    if p.fragment:
        raise PdsArchiveLabelValidationError(
            "Derived file_ref must not contain a fragment."
        )
    # Verify directory of file_ref path matches expected label directory.
    actual_dir = p.path.rsplit("/", 1)[0] + "/"
    if actual_dir != expected_dir_path:
        raise PdsArchiveLabelValidationError(
            f"Derived file_ref directory {actual_dir!r} does not match "
            f"label URL directory {expected_dir_path!r}."
        )


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
    - Maximum response body: 2 MiB, acquired with genuine streaming.
    - XML must be valid UTF-8; DOCTYPE/ENTITY is rejected before parsing.
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

        PART D: True bounded HTTP response read via streaming.
        The response body is consumed chunk-by-chunk up to MAX + 1 bytes.
        Reading stops immediately once the limit is exceeded — the complete
        body is never materialized for oversized responses.

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

        # PART D: Perform EXACTLY ONE HTTP GET with streaming — redirects never followed.
        # Use stream=True so we can consume the body chunk-by-chunk with a hard limit.
        try:
            response = self._client.send(
                http_request,
                follow_redirects=False,
                stream=True,
            )
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

        # Handle status codes and then read the body, ensuring the response is
        # always closed in all exit paths.
        try:
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

            # Optional: Content-Length early rejection.
            # If a syntactically valid non-negative Content-Length header is
            # present and exceeds MAX, reject before consuming the body.
            # The streaming bound below remains the authoritative enforcement.
            cl_header = response.headers.get("content-length", "").strip()
            if cl_header and cl_header.isdigit():
                if int(cl_header) > MAX_ARCHIVE_LABEL_BYTES:
                    raise PdsArchiveLabelValidationError(
                        f"PDS4 archive label Content-Length ({cl_header}) exceeds "
                        f"maximum allowed size ({MAX_ARCHIVE_LABEL_BYTES} bytes)."
                    )

            # PART D: Genuinely bounded body read with explicit finite chunk size.
            # Using _STREAM_CHUNK_BYTES ensures that no single transport-provided
            # chunk can be arbitrarily large: the iterator yields at most
            # _STREAM_CHUNK_BYTES bytes per iteration.
            # Limit check fires BEFORE appending — retained body memory never
            # exceeds MAX_ARCHIVE_LABEL_BYTES plus one iterator-buffer chunk.
            chunks: list[bytes] = []
            accumulated = 0

            for chunk in response.iter_bytes(chunk_size=_STREAM_CHUNK_BYTES):
                if accumulated + len(chunk) > MAX_ARCHIVE_LABEL_BYTES:
                    raise PdsArchiveLabelValidationError(
                        f"PDS4 archive label response body exceeds maximum allowed size "
                        f"({MAX_ARCHIVE_LABEL_BYTES} bytes)."
                    )
                chunks.append(chunk)
                accumulated += len(chunk)

            raw_bytes = b"".join(chunks)

        finally:
            # Ensure response is closed in all exit paths.
            response.close()

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
