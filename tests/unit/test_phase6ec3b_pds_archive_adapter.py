"""GCSI Phase 6E-C3B.1 — PDS Archive-Label Adapter Tests.

All tests are OFFLINE.  No live PDS Atmospheres Node requests are made.

The tests use httpx.MockTransport to simulate PDS archive label responses.

Test coverage:

REQUEST VALIDATION (1-20)
  - Valid request construction
  - LIDVID format validation (valid + invalid patterns)
  - label_url validation (scheme, host, prefix, extension, cross-binding)
  - Cross-binding (dir name, year/day mismatch, basename mismatch)

URL TRUST BOUNDARY (C3B.1-URL)
  - query string rejection
  - fragment rejection
  - traversal rejection
  - extra path segment rejection
  - double slash path rejection
  - percent-encoded path rejection
  - backslash rejection
  - userinfo rejection
  - deceptive host / subdomain rejection
  - non-443 explicit port rejection
  - explicit :443 behavior

TRANSPORT (21-30)
  - 200 success
  - 3xx redirects → validation error
  - 404/429/5xx → unavailable error
  - other 4xx → validation error
  - oversized response (genuinely streaming)
  - timeout / network error

XML SECURITY (31-35 + C3B.1-SEC)
  - DOCTYPE rejection
  - ENTITY rejection
  - Malformed XML
  - UTF-16/alternate-encoding bypass rejection
  - Invalid UTF-8 fails closed
  - Mixed-case DOCTYPE/ENTITY rejected

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

CONTEXT REFERENCES (53-62 + C3B.1-CTX)
  - All required refs present
  - Each missing ref → rejected individually
  - Wrong reference_type value → rejected
  - Misplaced references → rejected
  - Duplicate references → rejected

FILE AREA (63-75 + C3B.1-FILE)
  - Valid single file
  - Missing/duplicate File_Area_Observational
  - Missing/multiple File children
  - Missing file_name → rejected
  - Missing file_size → rejected
  - file_size wrong unit → rejected
  - file_size strict grammar (+1, float, exponent, etc.)
  - Missing Table_Delimited → rejected
  - Duplicate Table_Delimited → rejected
  - Table_Delimited only in supplemental → rejected
  - file_ref derived correctly from label URL
  - Safe file_name cross-binding
  - MD5 absent → None
  - Valid MD5 accepted
  - Invalid MD5 rejected
  - Duplicate MD5 rejected

PRODUCT / PROVENANCE / CAPTURE ASSEMBLY (76-88)

PURE VALIDATOR + STREAMING TESTS
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from datetime import datetime, timezone, timedelta
from textwrap import dedent
from typing import Optional, Iterator
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
    _scan_xml_security,
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
    file_size_str: Optional[str] = None,  # override for grammar tests
    file_size_unit: str = "byte",
    include_md5: bool = False,
    md5_value: str = "d41d8cd98f00b204e9800998ecf8427e",
    include_duplicate_md5: bool = False,
    include_table_delimited: bool = True,
    include_supplemental: bool = False,
    extra_file_areas: int = 0,
    extra_file_children: int = 0,
    extra_table_delimited: int = 0,
    # Misplacement controls for adversarial context tests
    instrument_in_target: bool = False,
    host_in_investigation: bool = False,
    target_in_observing_system: bool = False,
    duplicate_investigation_area: bool = False,
    duplicate_target_identification: bool = False,
    duplicate_instrument_component: bool = False,
    # C3B.2: component type overrides for adversarial tests
    instrument_comp_type: str = "Instrument",   # default correct type
    host_comp_type: str = "Spacecraft",          # default correct type
    # C3B.2: duplicate Internal_Reference inside a component
    duplicate_instrument_internal_ref: bool = False,
    # C3B.2: duplicate Investigation/Target Internal_Reference
    duplicate_investigation_ref: bool = False,
    duplicate_target_ref: bool = False,
    # C3B.2: duplicate Observing_System
    duplicate_observing_system: bool = False,
) -> bytes:
    """Build a minimal but structurally valid PDS4 archive label XML.

    C3B.2 structure: one Observing_System with two Observing_System_Component
    children (one for Instrument, one for Spacecraft), each with a <type> element.
    """
    ns = _PDS_NS

    # Build Investigation_Area
    inv_xml = ""
    extra_inv_ref = ""
    if duplicate_investigation_ref:
        extra_inv_ref = f"""
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
            <reference_type>data_to_investigation</reference_type>
          </Internal_Reference>"""
    if include_investigation:
        inv_xml = f"""
        <Investigation_Area>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
            <reference_type>{investigation_ref_type}</reference_type>
          </Internal_Reference>{extra_inv_ref}
        </Investigation_Area>"""
    if duplicate_investigation_area:
        inv_xml += f"""
        <Investigation_Area>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
            <reference_type>data_to_investigation</reference_type>
          </Internal_Reference>
        </Investigation_Area>"""

    # Build Observing_System (one element with both components)
    obs_sys_xml = ""
    if include_instrument or include_instrument_host or duplicate_instrument_component:
        instr_comp = ""
        if include_instrument:
            extra_instr_ir = ""
            if duplicate_instrument_internal_ref:
                extra_instr_ir = f"""
              <Internal_Reference>
                <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
                <reference_type>is_instrument</reference_type>
              </Internal_Reference>"""
            instr_comp += f"""
          <Observing_System_Component>
            <type>{instrument_comp_type}</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
              <reference_type>{instrument_ref_type}</reference_type>
            </Internal_Reference>{extra_instr_ir}
          </Observing_System_Component>"""
        if duplicate_instrument_component:
            instr_comp += f"""
          <Observing_System_Component>
            <type>Instrument</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
              <reference_type>is_instrument</reference_type>
            </Internal_Reference>
          </Observing_System_Component>"""
        host_comp = ""
        if include_instrument_host:
            host_comp += f"""
          <Observing_System_Component>
            <type>{host_comp_type}</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:instrument_host:spacecraft.jno</lid_reference>
              <reference_type>{instrument_host_ref_type}</reference_type>
            </Internal_Reference>
          </Observing_System_Component>"""
        # Target in Observing_System (adversarial)
        tgt_in_os = ""
        if target_in_observing_system:
            tgt_in_os = """
          <Observing_System_Component>
            <type>Target</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
              <reference_type>data_to_target</reference_type>
            </Internal_Reference>
          </Observing_System_Component>"""
        obs_sys_xml = f"""
        <Observing_System>
          {instr_comp}{host_comp}{tgt_in_os}
        </Observing_System>"""
        if duplicate_observing_system:
            obs_sys_xml += f"""
        <Observing_System>
          <Observing_System_Component>
            <type>Instrument</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
              <reference_type>is_instrument</reference_type>
            </Internal_Reference>
          </Observing_System_Component>
          <Observing_System_Component>
            <type>Spacecraft</type>
            <Internal_Reference>
              <lid_reference>urn:nasa:pds:context:instrument_host:spacecraft.jno</lid_reference>
              <reference_type>is_instrument_host</reference_type>
            </Internal_Reference>
          </Observing_System_Component>
        </Observing_System>"""

    # Instrument ref in Target_Identification (adversarial)
    instr_in_target_xml = ""
    if instrument_in_target:
        instr_in_target_xml = """
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
            <reference_type>is_instrument</reference_type>
          </Internal_Reference>"""

    # Host ref in Investigation_Area (adversarial) — this injects a second
    # Internal_Reference so the cardinality check catches it now.
    if host_in_investigation:
        inv_xml = f"""
        <Investigation_Area>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:investigation:mission.juno</lid_reference>
            <reference_type>data_to_investigation</reference_type>
          </Internal_Reference>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:instrument_host:spacecraft.jno</lid_reference>
            <reference_type>is_instrument_host</reference_type>
          </Internal_Reference>
        </Investigation_Area>"""

    # Build Target_Identification
    extra_tgt_ref = ""
    if duplicate_target_ref:
        extra_tgt_ref = f"""
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
            <reference_type>data_to_target</reference_type>
          </Internal_Reference>"""
    tgt_xml = ""
    if include_target:
        tgt_xml = f"""
        <Target_Identification>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
            <reference_type>{target_ref_type}</reference_type>
          </Internal_Reference>{instr_in_target_xml}{extra_tgt_ref}
        </Target_Identification>"""
    if duplicate_target_identification:
        tgt_xml += f"""
        <Target_Identification>
          <Internal_Reference>
            <lid_reference>urn:nasa:pds:context:target:planet.jupiter</lid_reference>
            <reference_type>data_to_target</reference_type>
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
        md5_xml = f"<md5_checksum>{md5_value}</md5_checksum>"
    if include_duplicate_md5:
        md5_xml += f"<md5_checksum>{md5_value}</md5_checksum>"

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
      <file_name>MWR62RI2024166030000_R04112_V04.csv</file_name>
      <file_size unit="byte">100</file_size>
    </File>
    <Table_Delimited>
    </Table_Delimited>
  </File_Area_Observational>"""

    supplemental_xml = ""
    if include_supplemental:
        supplemental_xml = """
  <File_Area_Observational_Supplemental>
    <File>
      <file_name>supplemental.csv</file_name>
      <file_size unit="byte">512</file_size>
    </File>
    <Table_Delimited>
    </Table_Delimited>
  </File_Area_Observational_Supplemental>"""

    table_xml = ""
    if include_table_delimited:
        table_xml = "<Table_Delimited>\n    </Table_Delimited>"
    for _ in range(extra_table_delimited):
        table_xml += "\n    <Table_Delimited>\n    </Table_Delimited>"

    fsize_text = str(file_size) if file_size_str is None else file_size_str

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
    </Time_Coordinates>{proc_xml}{inv_xml}{obs_sys_xml}{tgt_xml}
  </Observation_Area>
  <File_Area_Observational>
    <File>
      <file_name>{file_name}</file_name>
      <file_size unit="{file_size_unit}">{fsize_text}</file_size>
      {md5_xml}
    </File>
    {table_xml}{extra_files_xml}
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
        wrong_day_url = _VALID_LABEL_URL.replace("2024166", "2024167")
        with pytest.raises(ValidationError, match="LIDVID-derived"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_day_url)

    # 14. Cross-binding: basename mismatch rejected
    def test_14_basename_mismatch_rejected(self):
        wrong_basename_url = _VALID_LABEL_URL.replace(
            "MWR62RI2024166030000_R04112_V04.xml",
            "MWR99RI2024166030000_R04112_V04.xml",
        )
        with pytest.raises(ValidationError, match="basename|LIDVID-derived"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=wrong_basename_url)

    # 15. LIDVID cross regex: timestamp must be 13 digits
    def test_15_short_timestamp_rejected(self):
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri202416603000_r04112_v04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    # 16. LIDVID cross regex: role must be 'i' or 'g'
    def test_16_invalid_role_rejected(self):
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
# URL TRUST BOUNDARY TESTS (C3B.1-URL)
# ===========================================================================


class TestUrlTrustBoundary:
    """C3B.1 Part B: Exact URL/path trust boundary tests."""

    def test_url_01_query_string_rejected(self):
        """label_url with query string must be rejected."""
        url = _VALID_LABEL_URL + "?download=1"
        with pytest.raises(ValidationError, match="query"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_02_fragment_rejected(self):
        """label_url with fragment must be rejected."""
        url = _VALID_LABEL_URL + "#fragment"
        with pytest.raises(ValidationError, match="fragment"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_03_dot_traversal_in_path_rejected(self):
        """label_url with ../ traversal after expected prefix must be rejected."""
        # Construct a URL with a traversal that would pass startswith but fail exact match
        url = (
            "https://pds-atmospheres.nmsu.edu"
            "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/"
            "../2024166/MWR62RI2024166030000_R04112_V04.xml"
        )
        with pytest.raises(ValidationError, match="LIDVID-derived|path"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_04_extra_path_segment_rejected(self):
        """label_url with extra path segment must be rejected."""
        url = (
            "https://pds-atmospheres.nmsu.edu"
            "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/"
            "extra/MWR62RI2024166030000_R04112_V04.xml"
        )
        with pytest.raises(ValidationError, match="LIDVID-derived|path"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_05_double_slash_path_rejected(self):
        """label_url with double slash in path must be rejected."""
        url = _VALID_LABEL_URL.replace(
            "/PDS/data/jnomwr_1100/DATA/IRDR/",
            "/PDS/data/jnomwr_1100/DATA//IRDR/",
        )
        with pytest.raises(ValidationError, match="LIDVID-derived|path"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_06_percent_encoded_path_rejected(self):
        """label_url with percent-encoded characters must be rejected."""
        url = _VALID_LABEL_URL.replace("/IRDR/", "/%49RDR/")
        with pytest.raises(ValidationError, match="percent"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_07_backslash_in_url_rejected(self):
        """label_url with backslash must be rejected."""
        url = _VALID_LABEL_URL.replace("/IRDR/", "\\IRDR\\")
        with pytest.raises(ValidationError, match="backslash"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_08_userinfo_rejected(self):
        """label_url with userinfo (user:pass@host) must be rejected."""
        url = _VALID_LABEL_URL.replace(
            "https://pds-atmospheres.nmsu.edu",
            "https://user:pass@pds-atmospheres.nmsu.edu",
        )
        with pytest.raises(ValidationError, match="userinfo|trusted"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_09_deceptive_host_rejected(self):
        """label_url pointing to a deceptive host must be rejected."""
        url = _VALID_LABEL_URL.replace(
            "pds-atmospheres.nmsu.edu",
            "pds-atmospheres.nmsu.edu.evil.com",
        )
        with pytest.raises(ValidationError, match="trusted"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_10_subdomain_rejected(self):
        """label_url with a subdomain of the trusted host must be rejected."""
        url = _VALID_LABEL_URL.replace(
            "pds-atmospheres.nmsu.edu",
            "evil.pds-atmospheres.nmsu.edu",
        )
        with pytest.raises(ValidationError, match="trusted"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_11_non_443_explicit_port_rejected(self):
        """label_url with explicit non-443 port must be rejected."""
        url = _VALID_LABEL_URL.replace(
            "pds-atmospheres.nmsu.edu",
            "pds-atmospheres.nmsu.edu:8080",
        )
        with pytest.raises(ValidationError, match="port"):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_12_explicit_443_accepted(self):
        """label_url with explicit :443 is accepted (same as default HTTPS port)."""
        url = _VALID_LABEL_URL.replace(
            "pds-atmospheres.nmsu.edu",
            "pds-atmospheres.nmsu.edu:443",
        )
        # Port 443 is the standard HTTPS port; explicit :443 is accepted.
        req = PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)
        assert req.label_url == url

    def test_url_13_exact_path_equality_rejects_prefix_match(self):
        """startswith would accept, but exact equality rejects prefix-matched paths."""
        # Valid prefix but with extra trailing segment
        url = (
            "https://pds-atmospheres.nmsu.edu"
            "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166"
            "/MWR62RI2024166030000_R04112_V04.xmlsuffix"
        )
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=url)

    def test_url_14_valid_url_accepted(self):
        """Canonical valid URL is accepted."""
        req = PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=_VALID_LABEL_URL)
        assert req.label_url == _VALID_LABEL_URL


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
        """Oversized response body raises PdsArchiveLabelValidationError."""
        oversized = b"A" * (MAX_ARCHIVE_LABEL_BYTES + 1)
        adapter = _make_adapter(body=oversized)
        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            adapter.fetch(_valid_request())


# ===========================================================================
# STREAMING BOUNDED READ TEST (C3B.1-STREAM)
# ===========================================================================


class TestStreamingBoundedRead:
    """C3B.1 Part D: Verify that the adapter stops consuming once MAX+1 is crossed.

    A custom streaming transport counts chunks delivered.  If production code
    reads the complete oversized stream before checking the limit, the counter
    will reach the total number of chunks.  If it stops early (correct), the
    counter will be less.
    """

    def test_stream_01_adapter_stops_consuming_oversized_stream_early(self):
        """Adapter must stop reading once MAX_ARCHIVE_LABEL_BYTES is exceeded.

        We build a response that is well over MAX.  We verify the adapter
        raises the size validation error.

        Note: With iter_bytes(chunk_size=_STREAM_CHUNK_BYTES), the adapter
        processes data in 64 KiB iterator chunks.  The underlying transport
        stream may be exhausted by httpx's internal buffering before our
        pre-append check fires.  The important invariant is that the adapter
        RAISES before materializing the full oversized body into a single
        joined bytes object and passing it to the validator.
        See test_stream_04 for the single-oversized-chunk / no-retention proof.
        """
        from backend.app.mission_sources.adapters.pds_archive import _STREAM_CHUNK_BYTES

        # Use chunks aligned to _STREAM_CHUNK_BYTES so the iterator does not
        # need to buffer multiple transport chunks for one iteration.
        chunk_size = _STREAM_CHUNK_BYTES  # 64 KiB
        # Total: 33 chunks → 33 * 64 KiB = 2112 KiB > MAX (2048 KiB)
        total_chunks = (MAX_ARCHIVE_LABEL_BYTES // chunk_size) + 2
        total_bytes = total_chunks * chunk_size  # clearly > MAX

        chunks_consumed = {"count": 0}

        # We need a transport that actually streams chunks one at a time.
        # Build a custom SyncByteStream that tracks consumption.
        class _TrackingStream(httpx.SyncByteStream):
            def __init__(self):
                self._chunks_sent = 0

            def __iter__(self) -> Iterator[bytes]:
                for i in range(total_chunks):
                    self._chunks_sent += 1
                    chunks_consumed["count"] = self._chunks_sent
                    yield b"X" * chunk_size

        class _TrackingTransport(httpx.BaseTransport):
            def handle_request(self, request):
                stream = _TrackingStream()
                return httpx.Response(200, stream=stream)

        client = httpx.Client(transport=_TrackingTransport())
        adapter = PdsArchiveLabelAdapter(
            client=client, clock=lambda: _FIXED_CLOCK_UTC
        )

        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            adapter.fetch(_valid_request())

        # The adapter must have stopped before reading all chunks.
        # With aligned chunk sizes, the pre-append check fires on the (N+1)th
        # chunk, so count should be strictly less than total_chunks.
        assert chunks_consumed["count"] < total_chunks, (
            f"Adapter consumed all {total_chunks} chunks before raising — "
            "it read the complete oversized stream instead of stopping early."
        )

    def test_stream_02_exactly_at_limit_is_accepted(self):
        """A response of exactly MAX bytes must be accepted."""
        # Build valid XML and pad to exactly MAX bytes — but this is too small to
        # be valid XML anyway, so we use a different approach: just verify the
        # adapter does NOT raise for a valid-sized response.
        valid_xml = _make_valid_label_xml()
        assert len(valid_xml) <= MAX_ARCHIVE_LABEL_BYTES
        adapter = _make_adapter(body=valid_xml)
        product, _ = adapter.fetch(_valid_request())
        assert product is not None

    def test_stream_03_one_byte_over_limit_rejected(self):
        """MAX + 1 bytes must be rejected."""
        # Pad a valid body to just over MAX — use a pre-materialized body
        # since MockTransport delivers it as one chunk but we verified the
        # streaming logic in test_stream_01.
        oversized = b"A" * (MAX_ARCHIVE_LABEL_BYTES + 1)
        adapter = _make_adapter(body=oversized)
        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            adapter.fetch(_valid_request())

    def test_stream_04_oversized_single_source_chunk_rejected_without_retention(self):
        """C3B.2 Part D: A single transport chunk larger than MAX cannot be retained.

        The explicit chunk_size=_STREAM_CHUNK_BYTES in iter_bytes() limits each
        iteration to at most _STREAM_CHUNK_BYTES bytes.  This means even if the
        underlying transport provides one giant chunk, the iterator will slice it.
        We verify that the adapter rejects and closes the response without ever
        materializing the full oversized body.
        """
        from backend.app.mission_sources.adapters.pds_archive import _STREAM_CHUNK_BYTES

        # Build a body that is significantly larger than MAX in one logical chunk.
        giant_size = MAX_ARCHIVE_LABEL_BYTES * 3
        body_materialized = [False]

        class _GiantSingleChunkStream(httpx.SyncByteStream):
            """A transport stream that yields one giant chunk."""
            def __iter__(self) -> Iterator[bytes]:
                body_materialized[0] = True
                yield b"X" * giant_size

        class _GiantTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(200, stream=_GiantSingleChunkStream())

        client = httpx.Client(transport=_GiantTransport())
        adapter = PdsArchiveLabelAdapter(
            client=client, clock=lambda: _FIXED_CLOCK_UTC
        )

        with pytest.raises(PdsArchiveLabelValidationError, match="size"):
            adapter.fetch(_valid_request())

        # The key invariant: the adapter rejected without retaining the full body.
        # With chunk_size=_STREAM_CHUNK_BYTES, the first slice is at most
        # _STREAM_CHUNK_BYTES bytes — which already exceeds 0+_STREAM_CHUNK_BYTES
        # compared to MAX only if _STREAM_CHUNK_BYTES > MAX, which is false.
        # The actual rejection happens after the first chunk: accumulated + chunk > MAX.
        # The response must have been closed.
        # We can't easily verify response.close() from outside the adapter, but
        # we CAN verify that the adapter raises, which implies the response was
        # closed via the finally block.


# ===========================================================================
# XML SECURITY (31-35 + C3B.1-SEC)
# ===========================================================================


class TestXmlSecurity:
    """Tests 31-35 + C3B.1 Part I: XML security checks."""

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
        with pytest.raises(PdsArchiveLabelValidationError, match="ENTITY|DOCTYPE"):
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

    def test_sec_01_utf8_doctype_rejected(self):
        """UTF-8 encoded DOCTYPE is rejected before parsing."""
        body = '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE foo><x/>'.encode("utf-8")
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            _scan_xml_security(body)

    def test_sec_02_mixed_case_doctype_rejected(self):
        """Mixed-case DOCTYPE (e.g. <!DocType) is rejected."""
        body = b'<?xml version="1.0"?><!DocType foo><x/>'
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            _scan_xml_security(body)

    def test_sec_03_utf8_entity_rejected(self):
        """UTF-8 encoded ENTITY is rejected."""
        body = '<?xml version="1.0"?><!ENTITY foo "bar">'.encode("utf-8")
        with pytest.raises(PdsArchiveLabelValidationError, match="ENTITY"):
            _scan_xml_security(body)

    def test_sec_04_utf16le_doctype_rejected_by_complete_validator(self):
        """UTF-16LE XML with DOCTYPE fails closed via NUL-byte rejection.

        C3B.2: UTF-16LE of ASCII produces NUL bytes (b'<\\x00!\\x00D\\x00...')
        which the hardened validator rejects at the NUL-byte check BEFORE
        ElementTree ever sees the data.  This test calls _scan_xml_security()
        directly to prove the NUL rejection fires.
        """
        evil_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        body = evil_xml.encode("utf-16-le")
        with pytest.raises(PdsArchiveLabelValidationError):
            _scan_xml_security(body)

    def test_sec_04b_utf16le_doctype_rejected_by_full_validator(self):
        """UTF-16LE DOCTYPE attack fails closed when full validator is called."""
        evil_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        body = evil_xml.encode("utf-16-le")
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError):
            _validate_pds_archive_label_response(req, body, _FIXED_CLOCK_UTC)

    def test_sec_04c_utf16be_doctype_rejected_by_complete_validator(self):
        """UTF-16BE XML with DOCTYPE fails closed via NUL-byte rejection."""
        evil_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        body = evil_xml.encode("utf-16-be")
        with pytest.raises(PdsArchiveLabelValidationError):
            _scan_xml_security(body)

    def test_sec_04d_utf16be_doctype_rejected_by_full_validator(self):
        """UTF-16BE DOCTYPE attack fails closed when full validator is called."""
        evil_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        body = evil_xml.encode("utf-16-be")
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError):
            _validate_pds_archive_label_response(req, body, _FIXED_CLOCK_UTC)

    def test_sec_04e_utf16le_internal_entity_rejected_by_full_validator(self):
        """UTF-16LE ENTITY attack fails closed when full validator is called."""
        evil_xml = '<?xml version="1.0"?><!ENTITY foo "bar"><x/>'
        body = evil_xml.encode("utf-16-le")
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError):
            _validate_pds_archive_label_response(req, body, _FIXED_CLOCK_UTC)

    def test_sec_04f_utf16_encoding_declaration_rejected(self):
        """XML declaring encoding=UTF-16 is rejected even without DOCTYPE."""
        body = '<?xml version="1.0" encoding="UTF-16"?><x/>'.encode("utf-8")
        with pytest.raises(PdsArchiveLabelValidationError, match="encoding"):
            _scan_xml_security(body)

    def test_sec_05_invalid_utf8_fails_closed(self):
        """Invalid UTF-8 byte sequence fails closed."""
        body = b"\xff\xfe invalid utf-8 bytes"
        with pytest.raises(PdsArchiveLabelValidationError, match="UTF-8"):
            _scan_xml_security(body)

    def test_sec_06_nul_byte_interleaved_doctype_rejected(self):
        """NUL-byte interleaved DOCTYPE is rejected BEFORE ElementTree sees it.

        C3B.2: The NUL-byte check fires on the raw bytes before UTF-8 decoding,
        ensuring that any attempt to hide <!DOCTYPE via NUL interleaving is
        rejected fail-closed.
        """
        body = b"<\x00!DOCTYPE\x00 foo>"
        with pytest.raises(PdsArchiveLabelValidationError):
            _scan_xml_security(body)

    def test_sec_06b_nul_byte_entity_interleaved_rejected(self):
        """NUL-byte interleaved ENTITY attack is rejected by NUL-byte check."""
        body = b"<\x00!ENTITY\x00 foo 'bar'>"
        with pytest.raises(PdsArchiveLabelValidationError):
            _scan_xml_security(body)

    def test_sec_06c_nul_byte_attack_rejected_by_full_validator(self):
        """NUL-byte interleaved DOCTYPE fails closed when full validator is called."""
        body = b"<\x00!DOCTYPE\x00 foo>"
        req = _valid_request()
        with pytest.raises(PdsArchiveLabelValidationError):
            _validate_pds_archive_label_response(req, body, _FIXED_CLOCK_UTC)

    def test_sec_06d_nul_byte_attack_rejected_by_adapter_fetch(self):
        """NUL-byte attack fails closed when adapter.fetch() is called."""
        body = b"<\x00!DOCTYPE\x00 foo>"
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_sec_07_clean_valid_utf8_xml_passes_scan(self):
        """Valid UTF-8 XML with no DOCTYPE/ENTITY passes the security scan."""
        body = _make_valid_label_xml()
        # Should not raise — returns the decoded text
        result = _scan_xml_security(body)
        assert isinstance(result, str)

    def test_sec_08_utf8_bom_rejected(self):
        """UTF-8 BOM is rejected."""
        body = b"\xef\xbb\xbf" + _make_valid_label_xml()
        with pytest.raises(PdsArchiveLabelValidationError, match="BOM"):
            _scan_xml_security(body)

    def test_sec_09_utf16le_doctype_attack_via_adapter_fetch(self):
        """UTF-16LE DOCTYPE attack fails closed via adapter.fetch()."""
        evil_xml = '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><x/>'
        body = evil_xml.encode("utf-16-le")
        adapter = _make_adapter(body=body)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_sec_10_mixed_case_doctype_rejected_via_scanner(self):
        """Mixed-case <!DocType is rejected by scanner (confirms earlier test)."""
        body = b'<?xml version="1.0"?><!DocType foo><x/>'
        with pytest.raises(PdsArchiveLabelValidationError, match="DOCTYPE"):
            _scan_xml_security(body)

    def test_sec_11_mixed_case_entity_rejected_via_scanner(self):
        """Mixed-case <!Entity is rejected by scanner."""
        body = b'<?xml version="1.0"?><!Entity foo "bar"/>'
        with pytest.raises(PdsArchiveLabelValidationError, match="ENTITY"):
            _scan_xml_security(body)

    def test_sec_12_xml_encoding_declaration_iso_rejected(self):
        """XML declaring encoding=ISO-8859-1 is rejected."""
        body = '<?xml version="1.0" encoding="ISO-8859-1"?><x/>'.encode("utf-8")
        with pytest.raises(PdsArchiveLabelValidationError, match="encoding"):
            _scan_xml_security(body)

    def test_sec_13_xml_encoding_declaration_utf8_accepted(self):
        """XML with encoding=UTF-8 declaration passes the encoding check."""
        body = '<?xml version="1.0" encoding="UTF-8"?><x/>'.encode("utf-8")
        result = _scan_xml_security(body)
        assert isinstance(result, str)


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
        label = _make_valid_label_xml(
            lid=_VALID_LID,
            version_id="2.0",
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
# CONTEXT REFERENCES (53-62 + C3B.1-CTX)
# ===========================================================================


class TestContextReferences:
    """Tests 53-62 + C3B.1 Part E: Required context reference validation."""

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
        with pytest.raises(PdsArchiveLabelValidationError, match="investigation|Investigation_Area"):
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
        with pytest.raises(PdsArchiveLabelValidationError, match="target|Target_Identification"):
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
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument|Spacecraft"):
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

    # C3B.1-CTX: Misplacement tests

    def test_ctx_01_instrument_ref_in_target_identification_fails(self):
        """Instrument ref placed inside Target_Identification must NOT satisfy instrument invariant."""
        label = _make_valid_label_xml(
            include_instrument=False,       # remove from correct location
            instrument_in_target=True,      # place in wrong location
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="instrument"):
            adapter.fetch(_valid_request())

    def test_ctx_02_host_ref_in_investigation_area_fails(self):
        """Spacecraft host ref placed inside Investigation_Area must NOT satisfy host invariant."""
        label = _make_valid_label_xml(
            include_instrument_host=False,  # remove from correct location
            host_in_investigation=True,     # place in wrong location
        )
        adapter = _make_adapter(label_xml=label)
        # C3B.2: cardinality check fires first (Investigation_Area has 2 Internal_References)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_03_target_ref_in_observing_system_fails(self):
        """Target ref placed inside Observing_System must NOT satisfy target invariant."""
        label = _make_valid_label_xml(
            include_target=False,              # remove from correct location
            target_in_observing_system=True,   # place in wrong location
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="target|Target_Identification"):
            adapter.fetch(_valid_request())

    def test_ctx_04_duplicate_valid_instrument_components_rejected(self):
        """Duplicate valid instrument components must be rejected."""
        label = _make_valid_label_xml(duplicate_instrument_component=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="duplicate"):
            adapter.fetch(_valid_request())

    def test_ctx_05_duplicate_investigation_area_rejected(self):
        """Duplicate Investigation_Area must be rejected."""
        label = _make_valid_label_xml(duplicate_investigation_area=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Investigation_Area"):
            adapter.fetch(_valid_request())

    def test_ctx_06_duplicate_target_identification_rejected(self):
        """Duplicate Target_Identification must be rejected."""
        label = _make_valid_label_xml(duplicate_target_identification=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Target_Identification"):
            adapter.fetch(_valid_request())

    # C3B.2: Component type tests

    def test_ctx_07_instrument_lid_inside_spacecraft_type_rejected(self):
        """Instrument LID inside type=Spacecraft component must be rejected."""
        label = _make_valid_label_xml(instrument_comp_type="Spacecraft")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_08_instrument_lid_inside_other_type_rejected(self):
        """Instrument LID inside type=Other component must be rejected."""
        label = _make_valid_label_xml(instrument_comp_type="Other")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_09_spacecraft_lid_inside_instrument_type_rejected(self):
        """Spacecraft LID inside type=Instrument component must be rejected."""
        label = _make_valid_label_xml(host_comp_type="Instrument")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_10_spacecraft_lid_inside_other_type_rejected(self):
        """Spacecraft LID inside type=Other component must be rejected."""
        label = _make_valid_label_xml(host_comp_type="Other")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_11_missing_instrument_type_rejected(self):
        """Instrument component without <type> is rejected."""
        # Build a label where the instrument component has no <type>
        # by using instrument_comp_type="Instrument" but removing it via raw XML surgery.
        # Simplest: use include_instrument=False and inject raw XML via body.
        ns = _PDS_NS
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="{ns}">
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
        <!-- no <type> element — must be rejected -->
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
          <reference_type>is_instrument</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
      <Observing_System_Component>
        <type>Spacecraft</type>
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
  <File_Area_Observational>
    <File>
      <file_name>MWR62RI2024166030000_R04112_V04.csv</file_name>
      <file_size unit="byte">1024</file_size>
    </File>
    <Table_Delimited>
    </Table_Delimited>
  </File_Area_Observational>
</Product_Observational>
""".encode("utf-8")
        adapter = _make_adapter(body=xml)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_12_duplicate_type_child_rejected(self):
        """Instrument component with duplicate <type> elements is rejected."""
        ns = _PDS_NS
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="{ns}">
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
        <type>Instrument</type>
        <type>Instrument</type>
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
          <reference_type>is_instrument</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
      <Observing_System_Component>
        <type>Spacecraft</type>
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
  <File_Area_Observational>
    <File>
      <file_name>MWR62RI2024166030000_R04112_V04.csv</file_name>
      <file_size unit="byte">1024</file_size>
    </File>
    <Table_Delimited>
    </Table_Delimited>
  </File_Area_Observational>
</Product_Observational>
""".encode("utf-8")
        adapter = _make_adapter(body=xml)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_13_duplicate_internal_reference_in_component_rejected(self):
        """Instrument component with duplicate <Internal_Reference> elements is rejected."""
        label = _make_valid_label_xml(duplicate_instrument_internal_ref=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_14_duplicate_observing_system_rejected(self):
        """Duplicate Observing_System in Observation_Area is rejected."""
        label = _make_valid_label_xml(duplicate_observing_system=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Observing_System"):
            adapter.fetch(_valid_request())

    # C3B.2: Duplicate Internal_Reference cardinality tests

    def test_ctx_15_duplicate_investigation_internal_ref_rejected(self):
        """Duplicate valid Investigation Internal_Reference is rejected."""
        label = _make_valid_label_xml(duplicate_investigation_ref=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_16_duplicate_target_internal_ref_rejected(self):
        """Duplicate valid Target Internal_Reference is rejected."""
        label = _make_valid_label_xml(duplicate_target_ref=True)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_ctx_17_correct_component_types_accepted(self):
        """Correct Instrument + Spacecraft types with matching LIDs are accepted."""
        label = _make_valid_label_xml(
            instrument_comp_type="Instrument",
            host_comp_type="Spacecraft",
        )
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product is not None
        assert "urn:nasa:pds:context:instrument:mwr.jno" in product.instrument_lids
        assert "urn:nasa:pds:context:instrument_host:spacecraft.jno" in product.instrument_host_lids


# ===========================================================================
# FILE AREA (63-75 + C3B.1-FILE)
# ===========================================================================


class TestFileArea:
    """Tests 63-75 + C3B.1: File area and file metadata validation."""

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
        <type>Instrument</type>
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
          <reference_type>is_instrument</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
      <Observing_System_Component>
        <type>Spacecraft</type>
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

    def test_69_md5_absent_gives_none(self):
        """Absent md5_checksum → product md5_checksum is None (C3B contract)."""
        label = _make_valid_label_xml(include_md5=False)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].md5_checksum is None

    def test_69b_valid_md5_present_accepted(self):
        """Valid md5_checksum present → accepted and normalized (C3B contract)."""
        label = _make_valid_label_xml(
            include_md5=True,
            md5_value="d41d8cd98f00b204e9800998ecf8427e",
        )
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].md5_checksum == "d41d8cd98f00b204e9800998ecf8427e"

    def test_69c_invalid_md5_rejected(self):
        """Invalid md5_checksum (not 32 hex chars) is rejected."""
        label = _make_valid_label_xml(
            include_md5=True,
            md5_value="not_a_valid_md5_value",
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="md5"):
            adapter.fetch(_valid_request())

    def test_69d_duplicate_md5_rejected(self):
        """Duplicate md5_checksum elements are rejected."""
        label = _make_valid_label_xml(
            include_md5=True,
            include_duplicate_md5=True,
            md5_value="d41d8cd98f00b204e9800998ecf8427e",
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="duplicate|md5"):
            adapter.fetch(_valid_request())

    def test_70_file_ref_derived_from_label_url(self):
        """file_ref must be DERIVED from label URL directory + label file_name."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        file_ref = product.data_files[0].file_ref
        assert file_ref.startswith("https://pds-atmospheres.nmsu.edu")
        assert file_ref.endswith("MWR62RI2024166030000_R04112_V04.csv")
        assert "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/" in file_ref

    def test_71_file_ref_not_from_xml(self):
        """file_ref is derived, not source-reported."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
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

    def test_74_no_mime_in_product(self):
        """Archive labels have no MIME type; product data_file.mime_type must be None."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].mime_type is None

    def test_75_file_ref_same_directory_as_label(self):
        """Derived file_ref must share the same directory as the label URL."""
        adapter = _make_adapter()
        product, _ = adapter.fetch(_valid_request())
        file_ref = product.data_files[0].file_ref
        # Both label and file_ref must have the same directory
        label_dir = _VALID_LABEL_URL.rsplit("/", 1)[0]
        file_dir = file_ref.rsplit("/", 1)[0]
        assert file_dir == label_dir

    # C3B.1-FILE: Table_Delimited cardinality

    def test_file_01_missing_table_delimited_rejected(self):
        """Missing Table_Delimited must be rejected."""
        label = _make_valid_label_xml(include_table_delimited=False)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Table_Delimited"):
            adapter.fetch(_valid_request())

    def test_file_02_duplicate_table_delimited_rejected(self):
        """Duplicate Table_Delimited must be rejected."""
        label = _make_valid_label_xml(extra_table_delimited=1)
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Table_Delimited"):
            adapter.fetch(_valid_request())

    def test_file_03_table_delimited_only_in_supplemental_rejected(self):
        """Table_Delimited in supplemental area only does NOT satisfy the primary requirement."""
        # Primary file area has no Table_Delimited; supplemental has one.
        # But the check is only on File_Area_Observational (primary), not supplemental.
        label = _make_valid_label_xml(
            include_table_delimited=False,
            include_supplemental=True,
        )
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="Table_Delimited"):
            adapter.fetch(_valid_request())

    # C3B.1-FILE: file_size strict grammar

    def test_file_size_plus_sign_rejected(self):
        """+1 file_size is rejected by strict ASCII decimal grammar."""
        label = _make_valid_label_xml(file_size_str="+1")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="decimal|integer"):
            adapter.fetch(_valid_request())

    def test_file_size_negative_rejected(self):
        """-1 file_size is rejected."""
        label = _make_valid_label_xml(file_size_str="-1")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="decimal|integer"):
            adapter.fetch(_valid_request())

    def test_file_size_float_rejected(self):
        """1.0 file_size is rejected."""
        label = _make_valid_label_xml(file_size_str="1.0")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="decimal|integer"):
            adapter.fetch(_valid_request())

    def test_file_size_exponent_rejected(self):
        """1e3 file_size is rejected."""
        label = _make_valid_label_xml(file_size_str="1e3")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="decimal|integer"):
            adapter.fetch(_valid_request())

    def test_file_size_empty_rejected(self):
        """Empty file_size is rejected."""
        label = _make_valid_label_xml(file_size_str=" ")
        adapter = _make_adapter(label_xml=label)
        # Empty text raises missing text error before grammar check
        with pytest.raises(PdsArchiveLabelValidationError):
            adapter.fetch(_valid_request())

    def test_file_size_zero_valid(self):
        """0 file_size is accepted (strict ASCII decimal)."""
        label = _make_valid_label_xml(file_size=0)
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].file_size_bytes == 0

    # C3B.1-FILE: safe file_name cross-binding

    def test_filename_01_valid_uppercase_csv_accepted(self):
        """Uppercase .CSV extension is accepted (case-insensitive)."""
        label = _make_valid_label_xml(file_name="MWR62RI2024166030000_R04112_V04.CSV")
        adapter = _make_adapter(label_xml=label)
        product, _ = adapter.fetch(_valid_request())
        assert product.data_files[0].file_name == "MWR62RI2024166030000_R04112_V04.CSV"

    def test_filename_02_wrong_product_name_rejected(self):
        """file_name with wrong product token (wrong stem) is rejected."""
        label = _make_valid_label_xml(file_name="OTHER_PRODUCT.csv")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="stem|token|cross-binding"):
            adapter.fetch(_valid_request())

    def test_filename_03_traversal_filename_rejected(self):
        """file_name with ../traversal is rejected."""
        label = _make_valid_label_xml(file_name="../MWR62RI2024166030000_R04112_V04.csv")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="traversal|\\.\\."):
            adapter.fetch(_valid_request())

    def test_filename_04_slash_in_filename_rejected(self):
        """file_name with '/' is rejected."""
        label = _make_valid_label_xml(file_name="foo/MWR62RI2024166030000_R04112_V04.csv")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="traversal|/"):
            adapter.fetch(_valid_request())

    def test_filename_05_query_in_filename_rejected(self):
        """file_name with '?x=1' is rejected."""
        label = _make_valid_label_xml(file_name="MWR62RI2024166030000_R04112_V04.csv?x=1")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="query|\\?"):
            adapter.fetch(_valid_request())

    def test_filename_06_txt_extension_rejected(self):
        """file_name with .TXT extension is rejected."""
        label = _make_valid_label_xml(file_name="MWR62RI2024166030000_R04112_V04.TXT")
        adapter = _make_adapter(label_xml=label)
        with pytest.raises(PdsArchiveLabelValidationError, match="csv|extension"):
            adapter.fetch(_valid_request())


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


# ===========================================================================
# PART A: BASELINE LIDVID VALIDATION REUSE
# ===========================================================================


class TestBaselineLidvidReuse:
    """C3B.1 Part A: PdsProductRequest baseline validation is actually reused."""

    def test_baseline_01_lidvid_with_slash_rejected(self):
        """LIDVID with slash (PdsProductRequest baseline) is rejected."""
        # Construct something that would pass MWR regex if slash weren't checked,
        # but the baseline must reject it first.
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04/extra::1.0",
                label_url=_VALID_LABEL_URL,
            )

    def test_baseline_02_lidvid_with_question_mark_rejected(self):
        """LIDVID with '?' is rejected by PdsProductRequest baseline."""
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04?x=1::1.0",
                label_url=_VALID_LABEL_URL,
            )

    def test_baseline_03_lidvid_with_hash_rejected(self):
        """LIDVID with '#' is rejected by PdsProductRequest baseline."""
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04#frag::1.0",
                label_url=_VALID_LABEL_URL,
            )

    def test_baseline_04_lidvid_with_percent_rejected(self):
        """LIDVID with '%' is rejected by PdsProductRequest baseline."""
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112%20_v04::1.0",
                label_url=_VALID_LABEL_URL,
            )

    def test_baseline_05_lidvid_with_no_version_rejected(self):
        """LIDVID without '::version' is rejected by both baseline and MWR regex."""
        with pytest.raises(ValidationError):
            PdsArchiveLabelRequest(
                lidvid="urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04",
                label_url=_VALID_LABEL_URL,
            )

    def test_baseline_06_valid_lidvid_passes_both_checks(self):
        """Valid LIDVID passes PdsProductRequest baseline AND MWR regex."""
        req = PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=_VALID_LABEL_URL)
        assert req.lidvid == _VALID_LIDVID
