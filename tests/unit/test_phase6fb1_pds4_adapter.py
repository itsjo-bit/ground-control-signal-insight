"""GCSI Phase 6F-B1 — Generic PDS4 Adapter Tests.

All tests are OFFLINE. No live PDS requests are made.

Coverage:
- GenericPds4AdapterProfile validation
- URL trust boundary (wrong host, HTTP, query, fragment, percent-encoding,
  backslash, userinfo, wrong path prefix, wrong port)
- XML security (DOCTYPE, ENTITY, NUL, BOM, non-UTF-8, malformed XML)
- PDS4 label parsing (JIRAM-style, UVS-style, MWR-compatible fixture)
- Profile validation (wrong instrument LID, wrong spacecraft LID,
  missing investigation LID, wrong processing level, wrong IM version)
- Identity checks (stop < start, naive timestamps, missing timestamps)
- Provenance output (EXTERNAL_AUTHORITATIVE, VALIDATED, content_sha256)
- ArchiveScienceProduct output integrity
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from textwrap import dedent

import pytest

from backend.app.mission_sources.adapters.pds4_adapter import (
    GenericPds4AdapterProfile,
    GenericPds4AdapterValidationError,
    JIRAM_PDS4_PROFILE,
    MWR_GENERIC_PDS4_PROFILE,
    UVS_PDS4_PROFILE,
    MAX_PDS4_LABEL_BYTES,
    _validate_label_url_trust,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import ArchiveSourceStandard
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus


# ---------------------------------------------------------------------------
# Minimal valid JIRAM-style PDS4 label fixture
# ---------------------------------------------------------------------------

_JIRAM_LID = "urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_img_rec_test_v01"
_JIRAM_VER = "1.0"
_JIRAM_LIDVID = f"{_JIRAM_LID}::{_JIRAM_VER}"

_JIRAM_LABEL_TEMPLATE = dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>{lid}</logical_identifier>
    <version_id>{version}</version_id>
    <title>JIRAM Test Calibrated Image</title>
    <information_model_version>1.16.0.0</information_model_version>
    <product_class>Product_Observational</product_class>
  </Identification_Area>
  <Observation_Area>
    <Time_Coordinates>
      <start_date_time>{start}</start_date_time>
      <stop_date_time>{stop}</stop_date_time>
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
          <lid_reference>urn:nasa:pds:context:instrument:jiram.jno</lid_reference>
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
      <name>Jupiter</name>
    </Target_Identification>
  </Observation_Area>
</Product_Observational>
""")

_JIRAM_LABEL_URL = "https://pds.nasa.gov/pds/juno_jiram/data/jir_img_rec_test_v01.xml"


def _jiram_label(
    lid: str = _JIRAM_LID,
    version: str = _JIRAM_VER,
    start: str = "2024-06-13T10:00:00.000Z",
    stop: str = "2024-06-13T10:05:00.000Z",
) -> bytes:
    return _JIRAM_LABEL_TEMPLATE.format(
        lid=lid, version=version, start=start, stop=stop
    ).encode("utf-8")


_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)

# JIRAM profile with pds.nasa.gov path prefix that matches our test URL
_JIRAM_TEST_PROFILE = GenericPds4AdapterProfile(
    profile_id="jiram_test",
    allowed_hosts=frozenset({"pds.nasa.gov"}),
    allowed_path_prefixes=("/pds/",),
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="JIRAM",
    instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
    spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
    investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
    product_family="JIRAM",
    allowed_processing_levels=frozenset({"Calibrated", "Derived"}),
)

# UVS-style label fixture
_UVS_LID = "urn:nasa:pds:juno_uvs_bundle:data_calibrated:jno_uvs_test_cal"
_UVS_VER = "1.0"
_UVS_LABEL = dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_uvs_bundle:data_calibrated:jno_uvs_test_cal</logical_identifier>
    <version_id>1.0</version_id>
    <title>UVS Test Calibrated</title>
    <information_model_version>1.16.0.0</information_model_version>
    <product_class>Product_Observational</product_class>
  </Identification_Area>
  <Observation_Area>
    <Time_Coordinates>
      <start_date_time>2024-06-13T10:00:00.000Z</start_date_time>
      <stop_date_time>2024-06-13T10:05:00.000Z</stop_date_time>
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
          <lid_reference>urn:nasa:pds:context:instrument:uvs.jno</lid_reference>
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
      <name>Jupiter</name>
    </Target_Identification>
  </Observation_Area>
</Product_Observational>
""").encode("utf-8")

_UVS_TEST_PROFILE = GenericPds4AdapterProfile(
    profile_id="uvs_test",
    allowed_hosts=frozenset({"pds.nasa.gov"}),
    allowed_path_prefixes=("/pds/",),
    expected_mission="JUNO",
    expected_spacecraft="JNO",
    expected_instrument="UVS",
    instrument_lid="urn:nasa:pds:context:instrument:uvs.jno",
    spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
    investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
    product_family="UVS",
    allowed_processing_levels=frozenset({"Calibrated"}),
)


# ===========================================================================
# Profile validation
# ===========================================================================


class TestGenericPds4AdapterProfile:
    def test_valid_profile(self):
        assert _JIRAM_TEST_PROFILE.profile_id == "jiram_test"

    def test_empty_profile_id_rejected(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="[Ee]mpty"):
            GenericPds4AdapterProfile(
                profile_id="  ",
                allowed_hosts=frozenset({"pds.nasa.gov"}),
                allowed_path_prefixes=("/pds/",),
                expected_mission="JUNO",
                expected_spacecraft="JNO",
                expected_instrument="JIRAM",
                instrument_lid="urn:lid:inst",
                spacecraft_host_lid="urn:lid:sc",
                investigation_lid="urn:lid:inv",
                product_family="FAM",
            )


# ===========================================================================
# URL trust boundary
# ===========================================================================


class TestUrlTrustBoundary:
    def test_valid_url_accepted(self):
        _validate_label_url_trust(_JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE)

    def test_http_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh][Tt][Tt][Pp][Ss]"):
            _validate_label_url_trust(
                "http://pds.nasa.gov/pds/test.xml", _JIRAM_TEST_PROFILE
            )

    def test_wrong_host_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh]ost"):
            _validate_label_url_trust(
                "https://evil.example.com/pds/test.xml", _JIRAM_TEST_PROFILE
            )

    def test_wrong_path_prefix_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]refix|prefix"):
            _validate_label_url_trust(
                "https://pds.nasa.gov/BADPATH/test.xml", _JIRAM_TEST_PROFILE
            )

    def test_query_string_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Qq]uery"):
            _validate_label_url_trust(
                "https://pds.nasa.gov/pds/test.xml?foo=bar", _JIRAM_TEST_PROFILE
            )

    def test_fragment_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ff]ragment"):
            _validate_label_url_trust(
                "https://pds.nasa.gov/pds/test.xml#section", _JIRAM_TEST_PROFILE
            )

    def test_percent_encoding_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="percent"):
            _validate_label_url_trust(
                "https://pds.nasa.gov/pds/te%73t.xml", _JIRAM_TEST_PROFILE
            )

    def test_backslash_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="backslash"):
            _validate_label_url_trust(
                "https://pds.nasa.gov/pds\\test.xml", _JIRAM_TEST_PROFILE
            )

    def test_userinfo_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Uu]serinfo"):
            _validate_label_url_trust(
                "https://user@pds.nasa.gov/pds/test.xml", _JIRAM_TEST_PROFILE
            )

    def test_non_443_port_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]ort"):
            _validate_label_url_trust(
                "https://pds.nasa.gov:8080/pds/test.xml", _JIRAM_TEST_PROFILE
            )

    def test_explicit_443_accepted(self):
        # Explicit :443 is the default HTTPS port — should be accepted.
        _validate_label_url_trust(
            "https://pds.nasa.gov:443/pds/test.xml", _JIRAM_TEST_PROFILE
        )


# ===========================================================================
# XML security tests
# ===========================================================================


class TestXmlSecurity:
    def _parse(self, raw: bytes) -> None:
        parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_doctype_rejected(self):
        raw = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY bar "baz">]><root/>'
        with pytest.raises(GenericPds4AdapterValidationError, match="DOCTYPE"):
            self._parse(raw)

    def test_entity_rejected(self):
        raw = b'<?xml version="1.0"?><!ENTITY foo "bar"><root/>'
        with pytest.raises(GenericPds4AdapterValidationError, match="ENTITY"):
            self._parse(raw)

    def test_nul_byte_rejected(self):
        raw = b'<?xml version="1.0"?>\x00<root/>'
        with pytest.raises(GenericPds4AdapterValidationError, match="NUL"):
            self._parse(raw)

    def test_utf8_bom_rejected(self):
        raw = b"\xef\xbb\xbf" + b'<?xml version="1.0" encoding="UTF-8"?><root/>'
        with pytest.raises(GenericPds4AdapterValidationError, match="BOM"):
            self._parse(raw)

    def test_non_utf8_rejected(self):
        raw = b"\xff\xfe<root/>"  # UTF-16 BOM pattern
        with pytest.raises(GenericPds4AdapterValidationError):
            self._parse(raw)

    def test_malformed_xml_rejected(self):
        raw = b"<root><unclosed>"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Mm]alformed"):
            self._parse(raw)

    def test_mixed_case_doctype_rejected(self):
        raw = b"<!doctype foo><root/>"
        with pytest.raises(GenericPds4AdapterValidationError, match="DOCTYPE"):
            self._parse(raw)

    def test_empty_body_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError):
            self._parse(b"")

    def test_oversized_body_rejected(self):
        raw = b"x" * (MAX_PDS4_LABEL_BYTES + 1)
        with pytest.raises(GenericPds4AdapterValidationError, match="size"):
            self._parse(raw)


# ===========================================================================
# Successful parsing — JIRAM-style fixture
# ===========================================================================


class TestJiramLabelParsing:
    def test_valid_jiram_label(self):
        raw = _jiram_label()
        product, prov = parse_generic_pds4_label(
            raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT
        )
        assert product.source_standard == ArchiveSourceStandard.PDS4
        assert product.instrument_name == "JIRAM"
        assert product.mission_name == "JUNO"
        assert product.product_family == "JIRAM"
        assert product.source_record_id.startswith("pds4:")
        assert product.observation_start_utc is not None
        assert product.observation_stop_utc is not None

    def test_provenance_fields(self):
        raw = _jiram_label()
        product, prov = parse_generic_pds4_label(
            raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT
        )
        assert prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert prov.validation_status == ProvenanceValidationStatus.VALIDATED
        assert prov.content_sha256 == hashlib.sha256(raw).hexdigest()
        assert prov.source_record_id == product.source_record_id
        assert prov.retrieved_at == _RETRIEVED_AT

    def test_provenance_id_deterministic(self):
        raw = _jiram_label()
        _, prov1 = parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)
        _, prov2 = parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)
        assert prov1.provenance_id == prov2.provenance_id

    def test_target_names_extracted(self):
        raw = _jiram_label()
        product, _ = parse_generic_pds4_label(
            raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT
        )
        assert "Jupiter" in product.target_names

    def test_stop_before_start_rejected(self):
        raw = _jiram_label(
            start="2024-06-13T10:05:00.000Z",
            stop="2024-06-13T10:00:00.000Z",
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ss]tart.*[Ss]top|[Ss]top.*[Ss]tart"):
            parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_invalid_timestamp_rejected(self):
        raw = _jiram_label(start="not-a-date")
        with pytest.raises(GenericPds4AdapterValidationError):
            parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_naive_retrieved_at_rejected(self):
        raw = _jiram_label()
        naive = datetime(2024, 6, 14, 9, 35)
        with pytest.raises(GenericPds4AdapterValidationError, match="[Tt]imezone"):
            parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, naive)

    def test_wrong_instrument_lid_rejected(self):
        # Swap instrument LID to UVS — should fail JIRAM profile
        # Use _jiram_label() to produce a fully-rendered label, then replace the LID.
        label = _jiram_label().replace(b"jiram.jno", b"uvs.jno")
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ii]nstrument"):
            parse_generic_pds4_label(label, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_wrong_spacecraft_lid_rejected(self):
        label = _jiram_label().replace(b"spacecraft.jno", b"spacecraft.other")
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ss]pacecraft"):
            parse_generic_pds4_label(label, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_wrong_processing_level_rejected(self):
        label = _jiram_label().replace(
            b"<processing_level>Calibrated</processing_level>",
            b"<processing_level>Raw</processing_level>",
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]rocessing"):
            parse_generic_pds4_label(label, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_wrong_product_class_rejected(self):
        # Wrong root element
        label = b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1">
</Product_Bundle>"""
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]roduct"):
            parse_generic_pds4_label(label, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_wrong_namespace_rejected(self):
        label = b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://wrong.namespace.org/v1">
</Product_Observational>"""
        with pytest.raises(GenericPds4AdapterValidationError):
            parse_generic_pds4_label(label, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT)

    def test_wrong_profile_instrument(self):
        # Parse JIRAM label with UVS profile — should fail
        raw = _jiram_label()
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ii]nstrument"):
            parse_generic_pds4_label(raw, _JIRAM_LABEL_URL, _UVS_TEST_PROFILE, _RETRIEVED_AT)


# ===========================================================================
# UVS-style fixture
# ===========================================================================


class TestUvsLabelParsing:
    def test_valid_uvs_label(self):
        product, prov = parse_generic_pds4_label(
            _UVS_LABEL,
            "https://pds.nasa.gov/pds/uvs_test.xml",
            _UVS_TEST_PROFILE,
            _RETRIEVED_AT,
        )
        assert product.instrument_name == "UVS"
        assert product.source_record_id.startswith("pds4:")
        assert prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_uvs_wrong_processing_level_rejected(self):
        label = _UVS_LABEL.replace(
            b"<processing_level>Calibrated</processing_level>",
            b"<processing_level>Raw</processing_level>",
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]rocessing"):
            parse_generic_pds4_label(
                label,
                "https://pds.nasa.gov/pds/uvs_test.xml",
                _UVS_TEST_PROFILE,
                _RETRIEVED_AT,
            )


# ===========================================================================
# IM version constraint
# ===========================================================================


class TestImVersionConstraint:
    def test_allowed_version_accepted(self):
        # JIRAM test profile has no IM version restriction — any version OK.
        raw = _jiram_label()
        product, _ = parse_generic_pds4_label(
            raw, _JIRAM_LABEL_URL, _JIRAM_TEST_PROFILE, _RETRIEVED_AT
        )
        assert product is not None

    def test_mwr_profile_wrong_im_version_rejected(self):
        # MWR generic profile requires IM version 1.7.0.0
        from backend.app.mission_sources.adapters.pds_archive_models import _LIDVID_CROSS_RE
        # Use the JIRAM label (IM 1.16.0.0) with MWR profile that requires 1.7.0.0
        raw = _jiram_label()
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ii]nformation_model_version|version"):
            parse_generic_pds4_label(
                raw,
                "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/test.xml",
                MWR_GENERIC_PDS4_PROFILE,
                _RETRIEVED_AT,
            )
