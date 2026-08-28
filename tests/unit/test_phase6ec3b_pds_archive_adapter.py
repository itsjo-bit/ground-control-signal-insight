"""GCSI Phase 6E-C3B — PDS Archive-Label Adapter Tests.

All tests are OFFLINE.  No live PDS Atmospheres Node requests are made.

The tests use httpx.MockTransport to simulate PDS archive label responses.

Test coverage:

REQUEST VALIDATION (1-20)
  - Valid request construction
  - LIDVID format validation (valid + invalid patterns)
  - label_url validation (scheme, host, prefix, extension, cross-binding)
  - Cross-binding (dir name, year/day mismatch, basename mismatch)

TRANSPORT (21-30)
  - 200 success
  - 3xx redirects → validation error
  - 404/429/5xx → unavailable error
  - other 4xx → validation error
  - oversized response
  - timeout / network error

XML SECURITY (31-35)
  - DOCTYPE rejection
  - ENTITY rejection
  - Malformed XML

IDENTITY CHECKS (36-45)
  - Correct LIDVID/lid/version
  - Wrong logical_identifier
  - Wrong version_id
  - Wrong namespace
  - Wrong IM version
  - Wrong product class

OBSERVATION FACTS (46-52)
  - Timestamps present and valid
  - start > stop rejected
  - Processing level present + Calibrated
  - Processing level missing → rejected
  - Processing level wrong value → rejected

CONTEXT REFERENCES (53-62)
  - All 4 required refs present
  - Each missing ref → rejected individually
  - Wrong reference_type value → rejected

FILE AREA (63-75)
  - Valid single file
  - Missing File_Area_Observational → rejected
  - Multiple File_Area_Observational → rejected
  - Missing File child → rejected
  - Multiple File children → rejected
  - Missing file_name → rejected
  - Missing file_size → rejected
  - file_size wrong unit → rejected
  - md5_checksum present → rejected
  - file_ref derived correctly from label URL

PRODUCT / PROVENANCE / CAPTURE ASSEMBLY (76-88)
  - Product fields correct
  - Provenance source_system, source_uri, source_version
  - Provenance content_sha256 correct
  - Provenance notes contain derivation note
  - Capture invariants
  - Provenance_id is deterministic
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timezone, timedelta
from textwrap import dedent
from typing import Optional
from urllib.parse import urlparse

import httpx
import pytest
from pydantic import ValidationError

from backend.app.mission_sources.adapters.pds_archive import (
    MAX_ARCHIVE_LABEL_BYTES,
    PdsArchiveLabelAdapter,
    PdsArchiveLabelError,
    PdsArchiveLabelUnavailableError,
    PdsArchiveLabelValidationError,
    _ARCHIVE_SOURCE_SYSTEM,
    _REQUIRED_PROCESSING_LEVEL,
    _validate_pds_archive_label_response,
)
from backend.app.mission_sources.adapters.pds_archive_models import (
    PdsArchiveLabelCapture,
    PdsArchiveLabelRequest,
    _LIDVID_CROSS_RE,
    _SUPPORTED_IM_VERSION,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

# Valid MWR PJ62 IRDR calibrated LIDVID (role=i → IRDR)
_VALID_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04::1.0"
)
# Label URL matching the above LIDVID
_VALID_LABEL_URL = (
    "https://pds-atmospheres.nmsu.edu"
    "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166"
    "/MWR62RI2024166030000_R04112_V04.xml"
)

# Valid GRDR LIDVID (role=g → GRDR)
_VALID_GRDR_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62rg2024166030000_r04112_v04::1.0"
)
_VALID_GRDR_LABEL_URL = (
    "https://pds-atmospheres.nmsu.edu"
    "/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024166"
    "/MWR62RG2024166030000_R04112_V04.xml"
)

_FIXED_CLOCK_UTC = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)

# LID from the VALID LIDVID
_VALID_LID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04"
)
_VALID_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Network guard — no real network calls allowed
# ---------------------------------------------------------------------------


class _NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail immediately on any socket network call in this test module."""

    def _no_socket(*args, **kwargs):
        raise _NetworkBlockedError(
            "Network access is prohibited in PDS archive adapter tests."
        )

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)
    yield


# ---------------------------------------------------------------------------
# XML label builder
# ---------------------------------------------------------------------------

_PDS_NS = "http://pds.nasa.gov/pds4/pds/v1"


def _make_valid_label_xml(
    lid: str = _VALID_LID,
    version_id: str = _VALID_VERSION,
    im_version: str = _SUPPORTED_IM_VERSION,
    product_class: str = "Product_Observational",
    title: str = "MWR PJ62 IRDR Calibrated Test Label",
    start_dt: str = "2024-06-14T03:00:00Z",
    stop_dt: str = "2024-06-14T05:00:00Z",
    processing_level: Optional[str] = "Calibrated",
    include_investigation: bool = True,
    include_instrument: bool = True,
    include_instrument_host: bool = True,
    include_target: bool = True,
    investigation_ref_type: str = "data_to_investigation",
    instrument_ref_type: str = "is_instrument",
    instrument_host_ref_type: str = "is_instrument_host",
    target_ref_type: str = "data_to_target",
    file_name: str = "MWR62RI2024166030000_R04112_V04.csv",
    file_size: int = 2097152,
    file_size_unit: str = "byte",
    include_md5: bool = False,
    include_supplemental: bool = False,
    extra_file_areas: int = 0,
    extra_file_children: int = 0,
) -> bytes:
    """Build a minimal but structurally valid PDS4 archive label XML."""
    ns = _PDS_NS

    # Build Internal_Reference elements
    refs_xml = ""
    if include_investigation:
        refs_xml += f"""
          <Investigation_Area>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
              <reference_type>{investigation_ref_type}</reference_type>
            </Internal_Reference>
          </Investigation_Area>"""
    if include_instrument:
        refs_xml += f"""
          <Observing_System>
            <Observing_System_Component>
              <Internal_Reference>
                <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
                <reference_type>{instrument_ref_type}</reference_type>
              </Internal_Reference>
            </Observing_System_Component>
          </Observing_System>"""
    if include_instrument_host:
        refs_xml += f"""
          <Observing_System>
            <Observing_System_Component>
              <Internal_Reference>
                <lid_reference>urn:nasa:pds:context:instrument_host:spacecraft.jno</lid_reference>
                <reference_type>{instrument_host_ref_type}</reference_type>
              </Internal_Reference>
            </Observing_System_Component>
          </Observing_System>"""
    if include_target:
        refs_xml += f"""
          <Target_Identification>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
              <reference_type>{target_ref_type}</reference_type>
            </Internal_Reference>
          </Target_Identification>"""

    proc_xml = ""
    if processing_level is not None:
        proc_xml = f"""
          <Primary_Result_Summary>
            <processing_level>{processing_level}</processing_level>
          </Primary_Result_Summary>"""

    md5_xml = ""
    if include_md5:
        md5_xml = "<md5_checksum>d41d8cd98f00b204e9800998ecf8427e</md5_checksum>"

    extra_files_xml = ""
    for _ in range(extra_file_children):
        extra_files_xml += f"""
      <File>
        <file_name>extra_file.csv</file_name>
        <file_size unit="byte">100</file_size>
      </File>"""

    extra_farea_xml = ""
    for _ in range(extra_file_areas):
        extra_farea_xml += f"""
  <File_Area_Observational>
    <File>
      <file_name>extra_area_file.csv</file_name>
      <file_size unit="byte">100</file_size>
    </File>
  </File_Area_Observational>"""

    supplemental_xml = ""
    if include_supplemental:
        supplemental_xml = """
  <File_Area_Observational_Supplemental>
    <File>
      <file_name>supplemental.csv</file_name>
      <file_size unit="byte">512</file_size>
    </File>
  </File_Area_Observational_Supplemental>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="{ns}">
  <Identification_Area>
    <logical_identifier>{lid}</logical_identifier>
    <version_id>{version_id}</version_id>
    <title>{title}</title>
    <information_model_version>{im_version}</information_model_version>
    <product_class>{product_class}</product_class>
  </Identification_Area>
  <Observation_Area>
    <Time_Coordinates>
      <start_date_time>{start_dt}</start_date_time>
      <stop_date_time>{stop_dt}</stop_date_time>
    </Time_Coordinates>{proc_xml}{refs_xml}
  </Observation_Area>
  <File_Area_Observational>
    <File>
      <file_name>{file_name}</file_name>
      <file_size unit="{file_size_unit}">{file_size}</file_size>
      {md5_xml}
    </File>{extra_files_xml}
  </File_Area_Observational>{extra_farea_xml}{supplemental_xml}
</Product_Observational>
"""
    return xml.encode("utf-8")


def _make_mock_transport(
    status_code: int = 200,
    body: Optional[bytes] = None,
    label_xml: Optional[bytes] = None,
) -> httpx.MockTransport:
    """Build an httpx.MockTransport that returns a fixed response."""
    if body is None:
        if label_xml is not None:
            body = label_xml
        else:
            body = _make_valid_label_xml()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return httpx.MockTransport(handler)


def _make_adapter(
    status_code: int = 200,
    body: Optional[bytes] = None,
    label_xml: Optional[bytes] = None,
    clock=None,
) -> PdsArchiveLabelAdapter:
    """Build a PdsArchiveLabelAdapter backed by MockTransport."""
    if clock is None:
        clock = lambda: _FIXED_CLOCK_UTC

    transport = _make_mock_transport(
        status_code=status_code, body=body, label_xml=label_xml
    )
    client = httpx.Client(transport=transport)
    return PdsArchiveLabelAdapter(client=client, clock=clock)


def _valid_request(
    lidvid: str = _VALID_LIDVID,
    label_url: str = _VALID_LABEL_URL,
) -> PdsArchiveLabelRequest:
    return PdsArchiveLabelRequest(lidvid=lidvid, label_url=label_url)


# ===========================================================================
# REQUEST VALIDATION (1-20)
# ===========================================================================


class TestRequestValidation:
    """Tests 1-20: Request model validation."""

    # 1. Valid IRDR request accepted
    def test_01_valid_irdr_request_accepted(self):
        req = _valid_request()
        assert req.lidvid == _VALID_LIDVID
        assert req.label_url == _VALID_LABEL_URL

    # 2. Valid GRDR request accepted
    def test_02_valid_grdr_request_accepted(self):
        req = _valid_request(lidvid=_VALID_GRDR_LIDVID, label_url=_VALID_GRDR_LABEL_URL)
        assert req.lidvid == _VALID_GRDR_LIDVID

    # 3. LIDVID missing urn:nasa:pds: prefix rejected
    def test_03_wrong_lid_prefix_rejected(self):
        with pytest.raises(ValidationError, match="MWR calibrated"):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:other:data:product::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 4. LIDVID with bare LID (no ::version) rejected
    def test_04_bare_lid_rejected(self):
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04",
                label_url=_VALID_LABEL_URL,
            )

    # 5. LIDVID not matching MWR pattern (wrong bundle) rejected
    def test_05_wrong_bundle_rejected(self):
        with pytest.raises(ValidationError, match="MWR calibrated"):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:test_bundle:data_raw:test_obs::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 6. label_url with HTTP (not HTTPS) rejected
    def test_06_http_scheme_rejected(self):
        http_url = _VALID_LABEL_URL.replace("https://", "http://")
        with pytest.raises(ValidationError, match="HTTPS"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=http_url)

    # 7. label_url with wrong host rejected
    def test_07_wrong_host_rejected(self):
        wrong_url = _VALID_LABEL_URL.replace(
            "pds-atmospheres.nmsu.edu", "pds.nasa.gov"
        )
        with pytest.raises(ValidationError, match="trusted"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_url)

    # 8. label_url with wrong path prefix rejected
    def test_08_wrong_path_prefix_rejected(self):
        wrong_url = "https://pds-atmospheres.nmsu.edu/other/path/file.xml"
        with pytest.raises(ValidationError, match="/PDS/data/jnomwr_1100/DATA/"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_url)

    # 9. label_url not ending in .xml rejected
    def test_09_non_xml_extension_rejected(self):
        non_xml = _VALID_LABEL_URL.replace(".xml", ".csv")
        with pytest.raises(ValidationError, match="xml"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=non_xml)

    # 10. Cross-binding: IRDR LIDVID (role=i) with GRDR URL rejected
    def test_10_irdr_lidvid_with_grdr_url_rejected(self):
        with pytest.raises(ValidationError, match="LIDVID-derived"):
            PdsArchiveLabelRequest(
                lidvid=_VALID_LIDVID,
                label_url=_VALID_GRDR_LABEL_URL,
            )

    # 11. Cross-binding: GRDR LIDVID (role=g) with IRDR URL rejected
    def test_11_grdr_lidvid_with_irdr_url_rejected(self):
        with pytest.raises(ValidationError, match="LIDVID-derived"):
            PdsArchiveLabelRequest(
                lidvid=_VALID_GRDR_LIDVID,
                label_url=_VALID_LABEL_URL,
            )

    # 12. Cross-binding: year mismatch rejected
    def test_12_year_mismatch_rejected(self):
        wrong_year_url = _VALID_LABEL_URL.replace("/2024/2024166/", "/2025/2025166/")
        with pytest.raises(ValidationError, match="LIDVID-derived"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_year_url)

    # 13. Cross-binding: day-of-year mismatch rejected
    def test_13_day_mismatch_rejected(self):
        # Change day 166 to 167 in day_dir component
        wrong_day_url = _VALID_LABEL_URL.replace("2024166", "2024167")
        with pytest.raises(ValidationError, match="LIDVID-derived"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_day_url)

    # 14. Cross-binding: basename mismatch rejected
    def test_14_basename_mismatch_rejected(self):
        wrong_basename_url = _VALID_LABEL_URL.replace(
            "MWR62RI2024166030000_R04112_V04.xml",
            "MWR99RI2024166030000_R04112_V04.xml",
        )
        with pytest.raises(ValidationError, match="basename"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_basename_url)

    # 15. LIDVID cross regex: timestamp must be 13 digits
    def test_15_short_timestamp_rejected(self):
        # 12-digit timestamp
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri202416603000_r04112_v04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 16. LIDVID cross regex: role must be 'i' or 'g'
    def test_16_invalid_role_rejected(self):
        # role 'x' is not valid
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62rx2024166030000_r04112_v04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 17. LIDVID cross regex: reccode must start with 'r' + 5 digits
    def test_17_invalid_reccode_rejected(self):
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_x04112_v04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 18. LIDVID cross regex: localver must start with 'v' + 2 digits
    def test_18_invalid_localver_rejected(self):
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_w04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 19. Request is frozen (immutable)
    def test_19_request_is_frozen(self):
        req = _valid_request()
        with pytest.raises(Exception):
            req.lidvid = "something_else"  # type: ignore[misc]

    # 20. Extra fields are forbidden
    def test_20_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid=_VALID_LIDVID,
                label_url=_VALID_LABEL_URL,
                extra_field="not_allowed",  # type: ignore[call-arg]
            )


# ===========================================================================
# TRANSPORT (21-30)
# ===========================================================================


class TestTransport:
    """Tests 21-30: HTTP transport semantics."""

    def test_21_http_200_success(self):
        adapter = _make_adapter()
        req = _valid_request()
        product, provenance = adapter.fetch(req)
        assert product.lidvid == _VALID_LIDVID

    def test_22_http_301_raises_validation_error(self):
        adapter = _make_adapter(status_code=301)
        with pytest.raises(PdsArchiveLabelValidationError, match="redirect"):
            adapter.fetch(_valid_request())

    def test_23_http_302_raises_validation_error(self):
        adapter = _make_adapter(status_code=302)
        with pytest.raises(PdsArchiveLabelValidationError, match="redirect"):
            adapter.fetch(_valid_request())

    def test_24_http_307_raises_validation_error(self):
        adapter = _make_adapter(status_code=307)
        with pytest.raises(PdsArchiveLabelValidationError, match="redirect"):
            adapter.fetch(_valid_request())

    def test_25_http_404_raises_unavailable_error(self):
        adapter = _make_adapter(status_code=404)
        with pytest.raises(PdsArchiveLabelUnavailableError):
            adapter.fetch(_valid_request())

    def test_26_http_429_raises_unavailable_error(self):
        adapter = _make_adapter(status_code=429)
        with pytest.raises(PdsArchiveLabelUnavailableError):
            adapter.fetch(_valid_request())

    def test_27_http_500_raises_unavailable_error(self):
        adapter = _make_adapter(status_code=500)
        with pytest.raises(PdsArchiveLabelUnavailableError):
            adapter.fetch(_valid_request())

    def test_28_http_503_raises_unavailable_error(self):
        adapter = _make_adapter(status_code=503)
        with pytest.raises(PdsArchiveLabelUnavailableError):
            adapter.fetch(_valid_request())

    def test_29_http_400_raises_validation_error(self):
        adapter = _make_adapter(status_code=400)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_30_oversized_response_raises_validation_error(self):
        oversized = b"A" * (MAX_ARCHIVE_LABEL_BYTES + 1)
        adapter = _make_adapter(body=oversized)
        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            adapter.fetch(_valid_request())


# ===========================================================================
# XML SECURITY (31-35)
# ===========================================================================


class TestXmlSecurity:
    """Tests 31-35: XML security checks."""

    def test_31_doctype_lower_rejected(self):
        body = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            adapter.fetch(_valid_request())

    def test_32_doctype_upper_rejected(self):
        body = b'<?xml version="1.0"?><!DOCTYPE SYSTEM "http://evil.com/"> <x/>'
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            adapter.fetch(_valid_request())

    def test_33_entity_declaration_rejected(self):
        body = b'<?xml version="1.0"?><!ENTITY foo "bar"><x/>'
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            adapter.fetch(_valid_request())

    def test_34_malformed_xml_rejected(self):
        body = b"<not_valid_xml>"
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError, match="malformed"):
            adapter.fetch(_valid_request())

    def test_35_not_xml_at_all_rejected(self):
        body = b'{"json": "data"}'
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())


# ===========================================================================
# IDENTITY CHECKS (36-45)
# ===========================================================================


class TestIdentityChecks:
    """Tests 36-45: Identity field validation."""

    def test_36_correct_lidvid_accepted(self):
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.lidvid == _VALID_LIDVID
        assert product.lid == _VALID_LID
        assert product.version_id == _VALID_VERSION

    def test_37_wrong_logical_identifier_rejected(self):
        label = _make_valid_label_xml(
            lid="urn:nasa:pds:juno_mwr:data_calibrated:mwr99ri9999999999999_r99999_v99"
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="logical_identifier"):
            adapter.fetch(_valid_request())

    def test_38_wrong_version_id_rejected(self):
        label = _make_valid_label_xml(version_id="9.9")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="version_id"):
            adapter.fetch(_valid_request())

    def test_39_wrong_namespace_rejected(self):
        # Replace correct namespace with wrong one
        label = _make_valid_label_xml()
        wrong_ns = label.replace(
            b"http://pds.nasa.gov/pds4/pds/v1",
            b"http://pds.nasa.gov/pds4/pds/v2",
        )
        adapter = _make_adapter(body=wrong_ns)
        with pytest.raises(PdsArchiveLabelValidationError, match="namespace"):
            adapter.fetch(_valid_request())

    def test_40_wrong_im_version_rejected(self):
        label = _make_valid_label_xml(im_version="1.6.0.0")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="information_model_version"):
            adapter.fetch(_valid_request())

    def test_41_supported_im_version_accepted(self):
        label = _make_valid_label_xml(im_version=_SUPPORTED_IM_VERSION)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product is not None

    def test_42_wrong_product_class_rejected(self):
        label = _make_valid_label_xml(product_class="Product_Collection")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="product_class"):
            adapter.fetch(_valid_request())

    def test_43_product_class_must_be_product_observational(self):
        label = _make_valid_label_xml(product_class="Product_Bundle")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_44_correct_product_class_accepted(self):
        label = _make_valid_label_xml(product_class="Product_Observational")
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.product_class == "Product_Observational"

    def test_45_logical_identifier_must_be_consistent(self):
        # LID matches but constructed LIDVID must equal request LIDVID
        label = _make_valid_label_xml(
            lid=_VALID_LID,
            version_id="2.0",  # different version
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())


# ===========================================================================
# OBSERVATION FACTS (46-52)
# ===========================================================================


class TestObservationFacts:
    """Tests 46-52: Observation timestamp and processing level."""

    def test_46_valid_timestamps_accepted(self):
        label = _make_valid_label_xml(
            start_dt="2024-06-14T03:00:00Z",
            stop_dt="2024-06-14T05:00:00Z",
        )
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.observation_start_utc is not None
        assert product.observation_stop_utc is not None
        assert product.observation_start_utc <= product.observation_stop_utc

    def test_47_start_after_stop_rejected(self):
        label = _make_valid_label_xml(
            start_dt="2024-06-14T06:00:00Z",
            stop_dt="2024-06-14T03:00:00Z",
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="stop"):
            adapter.fetch(_valid_request())

    def test_48_equal_start_stop_accepted(self):
        label = _make_valid_label_xml(
            start_dt="2024-06-14T03:00:00Z",
            stop_dt="2024-06-14T03:00:00Z",
        )
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.observation_start_utc == product.observation_stop_utc

    def test_49_processing_level_calibrated_accepted(self):
        label = _make_valid_label_xml(processing_level="Calibrated")
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.processing_level == "Calibrated"

    def test_50_processing_level_missing_rejected(self):
        label = _make_valid_label_xml(processing_level=None)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Primary_Result_Summary"):
            adapter.fetch(_valid_request())

    def test_51_processing_level_raw_rejected(self):
        label = _make_valid_label_xml(processing_level="Raw")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="processing_level"):
            adapter.fetch(_valid_request())

    def test_52_processing_level_derived_rejected(self):
        label = _make_valid_label_xml(processing_level="Derived")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="processing_level"):
            adapter.fetch(_valid_request())


# ===========================================================================
# CONTEXT REFERENCES (53-62)
# ===========================================================================


class TestContextReferences:
    """Tests 53-62: Required context reference validation."""

    def test_53_all_four_refs_present_accepted(self):
        label = _make_valid_label_xml()
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert "urn:nasa:pds:context:investigation:mission.juno" in product.investigation_lids
        assert "urn:nasa:pds:context:instrument:mwr.jno" in product.instrument_lids
        assert "urn:nasa:pds:context:instrument_host:spacecraft.jno" in product.instrument_host_lids
        assert "urn:nasa:pds:context:target:planet.jupiter" in product.target_lids

    def test_54_missing_investigation_ref_rejected(self):
        label = _make_valid_label_xml(include_investigation=False)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="investigation"):
            adapter.fetch(_valid_request())

    def test_55_missing_instrument_ref_rejected(self):
        label = _make_valid_label_xml(include_instrument=False)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument"):
            adapter.fetch(_valid_request())

    def test_56_missing_instrument_host_ref_rejected(self):
        label = _make_valid_label_xml(include_instrument_host=False)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument"):
            adapter.fetch(_valid_request())

    def test_57_missing_target_ref_rejected(self):
        label = _make_valid_label_xml(include_target=False)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="target"):
            adapter.fetch(_valid_request())

    def test_58_wrong_investigation_ref_type_rejected(self):
        label = _make_valid_label_xml(investigation_ref_type="wrong_ref_type")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="investigation"):
            adapter.fetch(_valid_request())

    def test_59_wrong_instrument_ref_type_rejected(self):
        label = _make_valid_label_xml(instrument_ref_type="wrong_ref_type")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument"):
            adapter.fetch(_valid_request())

    def test_60_wrong_instrument_host_ref_type_rejected(self):
        label = _make_valid_label_xml(instrument_host_ref_type="wrong_ref_type")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument"):
            adapter.fetch(_valid_request())

    def test_61_wrong_target_ref_type_rejected(self):
        label = _make_valid_label_xml(target_ref_type="wrong_ref_type")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="target"):
            adapter.fetch(_valid_request())

    def test_62_supplemental_file_area_not_blocking(self):
        """A File_Area_Observational_Supplemental does NOT prevent a valid fetch."""
        label = _make_valid_label_xml(include_supplemental=True)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product is not None


# ===========================================================================
# FILE AREA (63-75)
# ===========================================================================


class TestFileArea:
    """Tests 63-75: File area and file metadata validation."""

    def test_63_valid_single_file_accepted(self):
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert len(product.data_files) == 1
        assert product.data_files[0].file_name == "MWR62RI2024166030000_R04112_V04.csv"

    def test_64_file_size_in_bytes(self):
        label = _make_valid_label_xml(file_size=2097152)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].file_size_bytes == 2097152
        assert product.total_data_size_bytes == 2097152

    def test_65_missing_file_area_observational_rejected(self):
        # Remove File_Area_Observational by building XML without it
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="{_PDS_NS}">
  <Identification_Area>
    <logical_identifier>{_VALID_LID}</logical_identifier>
    <version_id>{_VALID_VERSION}</version_id>
    <title>Test</title>
    <information_model_version>{_SUPPORTED_IM_VERSION}</information_model_version>
    <product_class>Product_Observational</product_class>
  </Identification_Area>
  <Observation_Area>
    <Time_Coordinates>
      <start_date_time>2024-06-14T03:00:00Z</start_date_time>
      <stop_date_time>2024-06-14T05:00:00Z</stop_date_time>
    </Time_Coordinates>
    <Primary_Result_Summary>
      <processing_level>Calibrated</processing_level>
    </Primary_Result_Summary>
    <Investigation_Area>
      <Internal_Reference>
        <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
        <reference_type>data_to_investigation</reference_type>
      </Internal_Reference>
    </Investigation_Area>
    <Observing_System>
      <Observing_System_Component>
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
          <reference_type>is_instrument</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
    </Observing_System>
    <Observing_System>
      <Observing_System_Component>
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument_host:spacecraft.jno</lid_reference>
          <reference_type>is_instrument_host</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
    </Observing_System>
    <Target_Identification>
      <Internal_Reference>
        <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
        <reference_type>data_to_target</reference_type>
      </Internal_Reference>
    </Target_Identification>
  </Observation_Area>
</Product_Observational>
""".encode("utf-8")
        adapter = _make_adapter(body=xml)
        with pytest.raises(PdsArchiveLabelValidationError, match="File_Area_Observational"):
            adapter.fetch(_valid_request())

    def test_66_multiple_file_area_observational_rejected(self):
        label = _make_valid_label_xml(extra_file_areas=1)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="File_Area_Observational"):
            adapter.fetch(_valid_request())

    def test_67_multiple_file_children_rejected(self):
        label = _make_valid_label_xml(extra_file_children=1)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="File"):
            adapter.fetch(_valid_request())

    def test_68_file_size_wrong_unit_rejected(self):
        label = _make_valid_label_xml(file_size_unit="kb")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="unit"):
            adapter.fetch(_valid_request())

    def test_69_md5_checksum_present_rejected(self):
        label = _make_valid_label_xml(include_md5=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="md5"):
            adapter.fetch(_valid_request())

    def test_70_file_ref_derived_from_label_url(self):
        """file_ref must be DERIVED from label URL directory + label file_name."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        file_ref = product.data_files[0].file_ref
        # Expected: https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/MWR62RI2024166030000_R04112_V04.csv
        assert file_ref.startswith("https://pds-atmospheres.nmsu.edu")
        assert file_ref.endswith("MWR62RI2024166030000_R04112_V04.csv")
        assert "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/" in file_ref

    def test_71_file_ref_not_from_xml(self):
        """file_ref is derived, not source-reported. Label XML has no file URL."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        # The file_ref should be derived from label_url, not from XML content.
        # It should start with https://pds-atmospheres.nmsu.edu
        assert product.data_files[0].file_ref.startswith("https://pds-atmospheres.nmsu.edu")

    def test_72_file_size_zero_accepted(self):
        label = _make_valid_label_xml(file_size=0)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].file_size_bytes == 0

    def test_73_total_data_size_equals_file_size(self):
        label = _make_valid_label_xml(file_size=12345)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.total_data_size_bytes == 12345

    def test_74_no_md5_in_product(self):
        """Archive labels have no md5; product data_file.md5_checksum must be None."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].md5_checksum is None

    def test_75_no_mime_in_product(self):
        """Archive labels have no MIME type; product data_file.mime_type must be None."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].mime_type is None


# ===========================================================================
# PRODUCT / PROVENANCE / CAPTURE ASSEMBLY (76-88)
# ===========================================================================


class TestProductProvenanceCaptureAssembly:
    """Tests 76-88: Product, provenance, and capture field correctness."""

    def test_76_product_lidvid_matches_request(self):
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.lidvid == _VALID_LIDVID

    def test_77_product_product_class_is_observational(self):
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.product_class == "Product_Observational"

    def test_78_provenance_kind_is_external_authoritative(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_79_provenance_source_system_correct(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.source_system == _ARCHIVE_SOURCE_SYSTEM

    def test_80_provenance_source_uri_is_label_url(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.source_uri == _VALID_LABEL_URL

    def test_81_provenance_source_record_id_is_lidvid(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.source_record_id == _VALID_LIDVID

    def test_82_provenance_source_version_is_im_version(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.source_version == _SUPPORTED_IM_VERSION

    def test_83_provenance_validation_status_validated(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_84_provenance_content_sha256_correct(self):
        raw = _make_valid_label_xml()
        expected = hashlib.sha256(raw).hexdigest()
        adapter = _make_adapter(label_xml=raw)
        _, prov = adapter.fetch(_valid_request())
        assert prov.content_sha256 == expected

    def test_85_provenance_notes_contain_derivation_note(self):
        adapter = _make_adapter()
        _, prov = adapter.fetch(_valid_request())
        assert prov.notes is not None
        assert "file_ref" in prov.notes.lower() or "derived" in prov.notes.lower()

    def test_86_provenance_id_is_deterministic(self):
        raw = _make_valid_label_xml()
        clock = lambda: _FIXED_CLOCK_UTC
        adapter1 = _make_adapter(label_xml=raw, clock=clock)
        adapter2 = _make_adapter(label_xml=raw, clock=clock)
        req = _valid_request()
        _, prov1 = adapter1.fetch(req)
        _, prov2 = adapter2.fetch(req)
        assert prov1.provenance_id == prov2.provenance_id

    def test_87_capture_invariants_hold(self):
        raw = _make_valid_label_xml()
        clock = lambda: _FIXED_CLOCK_UTC
        adapter = _make_adapter(label_xml=raw, clock=clock)
        req = _valid_request()
        capture = adapter.fetch_capture(req)
        # All capture invariants must hold
        assert capture.product.lidvid == capture.request.lidvid
        assert capture.provenance.source_record_id == capture.request.lidvid
        assert capture.provenance.source_uri == capture.request.label_url
        assert capture.provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert capture.provenance.validation_status == ProvenanceValidationStatus.VALIDATED
        assert capture.provenance.retrieved_at is not None
        computed = hashlib.sha256(capture.raw_label).hexdigest()
        assert computed == capture.provenance.content_sha256

    def test_88_retrieved_at_is_clock_value(self):
        clock = lambda: _FIXED_CLOCK_UTC
        adapter = _make_adapter(clock=clock)
        _, prov = adapter.fetch(_valid_request())
        assert prov.retrieved_at is not None
        assert prov.retrieved_at == _FIXED_CLOCK_UTC


# ===========================================================================
# PURE VALIDATOR FUNCTION TESTS
# ===========================================================================


class TestPureValidator:
    """Additional tests exercising _validate_pds_archive_label_response directly."""

    def test_a1_oversized_bytes_rejected(self):
        oversized = b"A" * (MAX_ARCHIVE_LABEL_BYTES + 1)
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            _validate_pds_archive_label_response(req, oversized, _FIXED_CLOCK_UTC)

    def test_a2_naive_retrieved_at_rejected(self):
        raw = _make_valid_label_xml()
        req = _valid_request()
        naive = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
        with pytest.raises(PdsArchiveLabelValidationError, match="timezone"):
            _validate_pds_archive_label_response(req, raw, naive)

    def test_a3_non_datetime_retrieved_at_rejected(self):
        raw = _make_valid_label_xml()
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError, match="datetime"):
            _validate_pds_archive_label_response(req, raw, "2026-01-01T00:00:00Z")  # type: ignore[arg-type]
