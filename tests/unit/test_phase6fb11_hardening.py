"""GCSI Phase 6F-B1.1 — Verified Inventory Foundation Hardening Tests.

This module covers the hardening acceptance criteria that go beyond the base
B1 test suite:

Section Q — Public-path security tests
    - PDS3 live adapter: trust enforcement through actual adapter entry point
    - PDS4 live adapter: trust enforcement through actual adapter entry point
    - Transport semantics: 3xx rejected, 404/429/5xx unavailable, 4xx validation error,
      oversized body, maximum one GET per fetch

Section R — PDS3 fail-closed tests (parser)
    - non-ASCII body, nested OBJECT/GROUP, unmatched END_OBJECT/END_GROUP
    - unterminated OBJECT at END, malformed assignment, unterminated quote
    - unterminated set/sequence, invalid pointer, invalid DOY 000 / 366 non-leap
    - leap-year DOY 366 accepted, second=60 rejected (no silent clamp)
    - invalid FILE_SIZE / RECORD_BYTES / FILE_RECORDS, huge size value
    - missing spacecraft / instrument identity (require=True)
    - payload normalization error is propagated

Section S — Size + snapshot tests
    - unknown size != zero size
    - approximate size contains an actual approximate value
    - exact metadata stays exact metadata
    - snapshot verification tracked independently from source size metadata
    - writing/loading a snapshot does not require mutating ArchiveScienceProduct

Section T — Manifest tests
    - dangling source_record_id rejected
    - dangling provenance reference rejected
    - duplicate source record rejected
    - manifest ID changes when availability / representation / provenance /
      source snapshot reference changes
    - canonical reordering produces same manifest_id

Section N — Snapshot normalizer/profile binding
    - normalizer_id + profile_id stored in envelope
    - controlled registry resolution
    - unknown normalizer/profile rejected by load()

Section P — Snapshot cross-checks
    - snapshot_source_standard matches re-derived product.source_standard

Section V — Real profile smoke fixtures (no network)
    - JIRAM official Atmospheres Node PDS4 path
    - UVS official archive path
    - MWR existing path
    - JunoCam PDS3 path
    - FGM PDS3 path
    - JADE PDS3 path
    - JEDI PDS3 path
    - WAVES Survey PDS3 path
    - WAVES Burst PDS3 path

All tests are OFFLINE. No live PDS requests are made.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Imports from production modules
# ---------------------------------------------------------------------------

from backend.app.mission_sources.adapters.pds3_adapter import (
    FGM_PDS3_PROFILE,
    JADE_PDS3_PROFILE,
    JEDI_PDS3_PROFILE,
    JUNOCAM_PDS3_PROFILE,
    MAX_PDS3_LABEL_BYTES,
    WAVES_BURST_PDS3_PROFILE,
    WAVES_SURVEY_PDS3_PROFILE,
    GenericPds3AdapterProfile,
    GenericPds3AdapterUnavailableError,
    GenericPds3AdapterValidationError,
    GenericPds3ObservationalLabelAdapter,
    GenericPds3SourceRequest,
    Pds3SizeDerivationStrategy,
    _PDS3_NORMALIZER_ID,
    _parse_pds3_datetime,
    _parse_pds3_label,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    JIRAM_PDS4_PROFILE,
    MAX_PDS4_LABEL_BYTES,
    MWR_GENERIC_PDS4_PROFILE,
    UVS_PDS4_PROFILE,
    GenericPds4AdapterProfile,
    GenericPds4AdapterUnavailableError,
    GenericPds4AdapterValidationError,
    GenericPds4ObservationalLabelAdapter,
    GenericPds4SourceRequest,
    _PDS4_NORMALIZER_ID,
    _validate_label_url_trust,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveSnapshotVerificationStatus,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
    VerifiedInventoryEntry,
    VerifiedInventoryManifest,
    VerifiedSourceRecordRef,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotValidationError,
    _PARSER_REGISTRY,
    _register_parser_force,
    register_parser,
    _compute_snapshot_id,
)
from backend.app.provenance.models import ProvenanceRecord

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)
_NOW_UTC = _RETRIEVED_AT

# Minimal WAVES Burst label for snapshot tests.
_WAVES_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_B11_HARD_TEST"
PRODUCT_VERSION_ID    = "V01"
RECORD_TYPE           = FIXED_LENGTH
RECORD_BYTES          = 512
FILE_RECORDS          = 20
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "WAV"
PROCESSING_LEVEL_ID   = "3"
START_TIME            = 2024-165T05:55:51.259
STOP_TIME             = 2024-165T05:59:02.709
TARGET_NAME           = "JUPITER"
^TABLE                = "WAV_B11_HARD_TEST_V01.BIN"
END
"""


def _waves_reparser(raw_bytes, source_ref, retrieved_at):
    return parse_generic_pds3_label(
        raw_bytes, source_ref, WAVES_BURST_PDS3_PROFILE, retrieved_at
    )


def _write_snapshot(tmp_path: Path, raw=None, source_ref="src", snap_name="snap.json"):
    raw = raw or _WAVES_LABEL
    product, prov = _waves_reparser(raw, source_ref, _RETRIEVED_AT)
    snap_path = tmp_path / snap_name
    ArchiveLabelSnapshotStore._write_with_explicit_reparser_for_test(
        raw_label_bytes=raw,
        source_ref=source_ref,
        product=product,
        provenance=prov,
        reparser=_waves_reparser,
        path=snap_path,
        normalizer_id=_PDS3_NORMALIZER_ID,
        profile_id=WAVES_BURST_PDS3_PROFILE.profile_id,
    )
    return snap_path, product, prov


# ===========================================================================
# Section Q — Public-path security tests
# ===========================================================================


class TestPds3PublicPathSecurityViaAdapter:
    """Trust enforcement through the actual public PDS3 live adapter entry point."""

    # Profile with strict trust constraints.
    _STRICT_PROFILE = GenericPds3AdapterProfile(
        profile_id="test_strict_pds3",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        expected_data_set_id_prefix="JNO-E/J/SS-WAV",
        product_family="WAVES_BURST",
        allowed_processing_levels=frozenset({"3"}),
        require_start_stop_time=True,
        allowed_hosts=frozenset({"pds.nasa.gov"}),
        allowed_path_prefixes=("/data/waves/",),
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
    )

    def _make_request(self, url: str) -> GenericPds3SourceRequest:
        return GenericPds3SourceRequest(source_url=url)

    def test_http_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="http://pds.nasa.gov/data/waves/t.lbl")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh][Tt][Tt][Pp][Ss]"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_wrong_host_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://evil.example.com/data/waves/t.lbl")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_userinfo_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://user@pds.nasa.gov/data/waves/t.lbl")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Uu]serinfo"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_query_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://pds.nasa.gov/data/waves/t.lbl?x=1")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Qq]uery"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_fragment_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://pds.nasa.gov/data/waves/t.lbl#sec")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Ff]ragment"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_percent_encoding_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://pds.nasa.gov/data/waves/t%2Elbl")
        with pytest.raises(GenericPds3AdapterValidationError, match="percent"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)

    def test_non_443_port_rejected_before_network(self):
        req = GenericPds3SourceRequest(source_url="https://pds.nasa.gov:8080/data/waves/t.lbl")
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]ort"):
            GenericPds3ObservationalLabelAdapter.fetch(req, self._STRICT_PROFILE, _RETRIEVED_AT)


class TestPds3TransportSemantics:
    """Transport semantics tests via mocked httpx."""

    _PROFILE = GenericPds3AdapterProfile(
        profile_id="test_transport_pds3",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        expected_data_set_id_prefix="JNO-E/J/SS-WAV",
        product_family="WAVES_BURST",
        allowed_processing_levels=frozenset({"3"}),
        require_start_stop_time=True,
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
        # Section G: must have explicit trust constraints for live fetch.
        allowed_hosts=frozenset({"test-archive.example.com"}),
        allowed_path_prefixes=("/pds/",),
    )
    _URL = "https://test-archive.example.com/pds/waves_test.lbl"

    def _req(self):
        return GenericPds3SourceRequest(source_url=self._URL)

    def _mock_stream_response(self, status: int, content: bytes = b""):
        """Build a mock streaming response for httpx.Client.stream()."""
        resp = MagicMock()
        resp.status_code = status
        resp.iter_bytes = MagicMock(return_value=iter([content] if content else []))
        stream_cm = MagicMock()
        stream_cm.__enter__ = MagicMock(return_value=resp)
        stream_cm.__exit__ = MagicMock(return_value=False)
        return stream_cm, resp

    def _patched_fetch(self, status: int, content: bytes = b""):
        stream_cm, _resp = self._mock_stream_response(status, content)
        with patch("httpx.Client") as mock_client_cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            mock_client_cls.return_value = mock_cm
            return GenericPds3ObservationalLabelAdapter.fetch(
                self._req(), self._PROFILE, _RETRIEVED_AT
            )

    def test_redirect_301_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="redirect|3"):
            self._patched_fetch(301)

    def test_redirect_302_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="redirect|3"):
            self._patched_fetch(302)

    def test_404_is_unavailable(self):
        with pytest.raises(GenericPds3AdapterUnavailableError):
            self._patched_fetch(404)

    def test_429_is_unavailable(self):
        with pytest.raises(GenericPds3AdapterUnavailableError):
            self._patched_fetch(429)

    def test_500_is_unavailable(self):
        with pytest.raises(GenericPds3AdapterUnavailableError):
            self._patched_fetch(500)

    def test_503_is_unavailable(self):
        with pytest.raises(GenericPds3AdapterUnavailableError):
            self._patched_fetch(503)

    def test_400_is_validation_error(self):
        with pytest.raises(GenericPds3AdapterValidationError):
            self._patched_fetch(400)

    def test_403_is_validation_error(self):
        with pytest.raises(GenericPds3AdapterValidationError):
            self._patched_fetch(403)

    def test_oversized_body_rejected(self):
        oversized = b"x" * (MAX_PDS3_LABEL_BYTES + 1)
        with pytest.raises(GenericPds3AdapterValidationError, match="size"):
            self._patched_fetch(200, oversized)

    def test_at_most_one_stream_per_fetch(self):
        """The adapter must issue exactly one streaming GET per fetch call."""
        stream_cm, _resp = self._mock_stream_response(404, b"")
        with patch("httpx.Client") as mock_client_cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            mock_client_cls.return_value = mock_cm
            with pytest.raises(GenericPds3AdapterUnavailableError):
                GenericPds3ObservationalLabelAdapter.fetch(
                    self._req(), self._PROFILE, _RETRIEVED_AT
                )
            assert mock_cm.stream.call_count == 1


class TestPds4PublicPathSecurityViaParser:
    """Trust enforcement via parse_generic_pds4_label public entry point (Section C).

    _validate_label_url_trust must be called by the production normalization path,
    not only in isolation.
    """

    _STRICT_PROFILE = GenericPds4AdapterProfile(
        profile_id="jiram_strict_test",
        allowed_hosts=frozenset({"atmos.nmsu.edu"}),
        allowed_path_prefixes=("/PDS/data/PDS4/juno_jiram_bundle/",),
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="JIRAM",
        instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
        spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
        investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
        product_family="JIRAM",
        allowed_processing_levels=frozenset({"Calibrated", "Derived"}),
    )

    _VALID_LABEL = b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_test</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM Test</title>
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
"""

    _VALID_URL = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_test.xml"

    def test_evil_host_rejected_by_parser(self):
        """evil.example/label.xml must be rejected before normalization records it as EXTERNAL_AUTHORITATIVE."""
        evil_url = "https://evil.example/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh]ost"):
            parse_generic_pds4_label(
                self._VALID_LABEL, evil_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_http_rejected_by_parser(self):
        http_url = self._VALID_URL.replace("https://", "http://")
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh][Tt][Tt][Pp][Ss]"):
            parse_generic_pds4_label(
                self._VALID_LABEL, http_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_wrong_path_rejected_by_parser(self):
        bad_url = "https://atmos.nmsu.edu/WRONG/path/label.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            parse_generic_pds4_label(
                self._VALID_LABEL, bad_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_query_rejected_by_parser(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Qq]uery"):
            parse_generic_pds4_label(
                self._VALID_LABEL, self._VALID_URL + "?foo=1", self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_fragment_rejected_by_parser(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ff]ragment"):
            parse_generic_pds4_label(
                self._VALID_LABEL, self._VALID_URL + "#sec", self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_percent_encoding_rejected_by_parser(self):
        pct_url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_te%73t.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="percent"):
            parse_generic_pds4_label(
                self._VALID_LABEL, pct_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_non_443_port_rejected_by_parser(self):
        port_url = "https://atmos.nmsu.edu:8080/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Pp]ort"):
            parse_generic_pds4_label(
                self._VALID_LABEL, port_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )

    def test_userinfo_rejected_by_parser(self):
        user_url = "https://user@atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Uu]serinfo"):
            parse_generic_pds4_label(
                self._VALID_LABEL, user_url, self._STRICT_PROFILE, _RETRIEVED_AT
            )


class TestPds4TransportSemantics:
    """Transport semantics tests via mocked httpx for PDS4 live adapter."""

    _PROFILE = GenericPds4AdapterProfile(
        profile_id="test_transport_pds4",
        allowed_hosts=frozenset({"test-archive.example.com"}),
        allowed_path_prefixes=("/pds4/data/",),
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="JIRAM",
        instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
        spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
        investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
        product_family="JIRAM",
        allowed_processing_levels=frozenset({"Calibrated", "Derived"}),
    )
    _URL = "https://test-archive.example.com/pds4/data/jir_test.xml"

    def _req(self):
        return GenericPds4SourceRequest(label_url=self._URL)

    def _mock_stream_response(self, status: int, content: bytes = b""):
        resp = MagicMock()
        resp.status_code = status
        resp.iter_bytes = MagicMock(return_value=iter([content] if content else []))
        stream_cm = MagicMock()
        stream_cm.__enter__ = MagicMock(return_value=resp)
        stream_cm.__exit__ = MagicMock(return_value=False)
        return stream_cm, resp

    def _patched_fetch(self, status: int, content: bytes = b""):
        stream_cm, _resp = self._mock_stream_response(status, content)
        with patch("httpx.Client") as mock_client_cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            mock_client_cls.return_value = mock_cm
            return GenericPds4ObservationalLabelAdapter.fetch(
                self._req(), self._PROFILE, _RETRIEVED_AT
            )

    def test_redirect_301_rejected(self):
        with pytest.raises(GenericPds4AdapterValidationError, match="redirect"):
            self._patched_fetch(301)

    def test_404_is_unavailable(self):
        with pytest.raises(GenericPds4AdapterUnavailableError):
            self._patched_fetch(404)

    def test_429_is_unavailable(self):
        with pytest.raises(GenericPds4AdapterUnavailableError):
            self._patched_fetch(429)

    def test_500_is_unavailable(self):
        with pytest.raises(GenericPds4AdapterUnavailableError):
            self._patched_fetch(500)

    def test_400_is_validation_error(self):
        with pytest.raises(GenericPds4AdapterValidationError):
            self._patched_fetch(400)

    def test_oversized_body_rejected(self):
        oversized = b"x" * (MAX_PDS4_LABEL_BYTES + 1)
        with pytest.raises(GenericPds4AdapterValidationError, match="size"):
            self._patched_fetch(200, oversized)

    def test_at_most_one_stream_per_fetch(self):
        """The adapter must issue exactly one streaming GET per fetch call."""
        stream_cm, _resp = self._mock_stream_response(404, b"")
        with patch("httpx.Client") as mock_client_cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            mock_client_cls.return_value = mock_cm
            with pytest.raises(GenericPds4AdapterUnavailableError):
                GenericPds4ObservationalLabelAdapter.fetch(
                    self._req(), self._PROFILE, _RETRIEVED_AT
                )
            assert mock_cm.stream.call_count == 1


# ===========================================================================
# Section R — PDS3 fail-closed tests (comprehensive)
# ===========================================================================


class TestPds3FailClosedComplete:
    """Comprehensive fail-closed tests per Section R requirements."""

    def test_non_ascii_body(self):
        """Non-ASCII bytes must be rejected."""
        raw = b"DATA_SET_ID = DS\nPRODUCT_ID = PROD_\xc3\xa9\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="[Nn]on-ASCII|[Aa]SCII"):
            _parse_pds3_label(raw)

    def test_nested_object_collected_as_raw_text(self):
        # B2.2 parser update: real WAVES Burst labels contain nested OBJECT blocks
        # (FILE > HEADER_TABLE > COLUMN etc.).  The parser now collects them as raw
        # text under "_OBJECT_<NAME>" rather than raising.  A standalone OBJECT = A
        # with a nested OBJECT = B inside is a valid PDS3 construct; the outer block
        # is stored and the nested content is preserved verbatim.
        raw = b"OBJECT = A\nOBJECT = B\nEND_OBJECT = B\nEND_OBJECT = A\nEND\n"
        result = _parse_pds3_label(raw)
        assert "_OBJECT_A" in result

    def test_nested_group_collected_as_raw_text(self):
        # Identical policy for GROUP blocks.
        raw = b"GROUP = A\nGROUP = B\nEND_GROUP = B\nEND_GROUP = A\nEND\n"
        result = _parse_pds3_label(raw)
        assert "_OBJECT_A" in result

    def test_unmatched_end_object(self):
        raw = b"DATA_SET_ID = DS\nEND_OBJECT = THING\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="[Uu]nmatched|[Dd]epth|underflow"):
            _parse_pds3_label(raw)

    def test_unmatched_end_group(self):
        raw = b"DATA_SET_ID = DS\nEND_GROUP = THING\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="[Uu]nmatched|[Dd]epth|underflow"):
            _parse_pds3_label(raw)

    def test_unterminated_object_at_end(self):
        raw = b"OBJECT = TABLE\nKEY = VALUE\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="[Uu]nterminated|[Uu]nclosed"):
            _parse_pds3_label(raw)

    def test_malformed_assignment(self):
        raw = b"DATA_SET_ID = DS\nTHIS IS NOT VALID ASSIGNMENT\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="[Mm]alformed|[Uu]nrecognized"):
            _parse_pds3_label(raw)

    def test_unterminated_quote(self):
        raw = b'DATA_SET_ID = "not closed\nEND\n'
        # The multi-line accumulator raises when the label ends without a closing quote.
        # Error message: "ended while accumulating multi-line value ... closing '"' not found"
        with pytest.raises(GenericPds3AdapterValidationError, match="accumulating|closing|not found"):
            _parse_pds3_label(raw)

    def test_unterminated_set(self):
        raw = b"TARGET_NAME = { JUPITER, SOLAR_SYSTEM\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError):
            _parse_pds3_label(raw)

    def test_invalid_doy_000(self):
        with pytest.raises(GenericPds3AdapterValidationError):
            _parse_pds3_datetime("2024-000T12:00:00.000", "START_TIME")

    def test_invalid_doy_366_non_leap(self):
        """2023 is not a leap year — DOY 366 must be rejected."""
        with pytest.raises(GenericPds3AdapterValidationError):
            _parse_pds3_datetime("2023-366T00:00:00.000", "START_TIME")

    def test_valid_doy_366_leap_year(self):
        """2024 is a leap year — DOY 366 must be accepted."""
        dt = _parse_pds3_datetime("2024-366T00:00:00.000", "START_TIME")
        assert dt.year == 2024
        assert dt.month == 12
        assert dt.day == 31

    def test_leap_second_60_rejected(self):
        """second=60 must be rejected — not silently clamped to 59."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Ll]eap|second"):
            _parse_pds3_datetime("2024-165T23:59:60.000", "STOP_TIME")

    def test_invalid_file_size(self):
        """Malformed FILE_SIZE must be rejected when FILE_SIZE strategy is active."""
        from backend.app.mission_sources.adapters.pds3_adapter import _derive_pds3_file_size
        kv = _parse_pds3_label(b"FILE_SIZE = NOT_A_NUMBER\nEND\n")
        with pytest.raises(GenericPds3AdapterValidationError, match="FILE_SIZE"):
            _derive_pds3_file_size(kv, Pds3SizeDerivationStrategy.FILE_SIZE)

    def test_invalid_record_bytes(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _derive_pds3_file_size
        kv = _parse_pds3_label(b"RECORD_BYTES = BAD\nFILE_RECORDS = 10\nEND\n")
        with pytest.raises(GenericPds3AdapterValidationError, match="RECORD_BYTES"):
            _derive_pds3_file_size(kv, Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS)

    def test_invalid_file_records(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _derive_pds3_file_size
        kv = _parse_pds3_label(b"RECORD_BYTES = 1024\nFILE_RECORDS = BAD\nEND\n")
        with pytest.raises(GenericPds3AdapterValidationError, match="FILE_RECORDS"):
            _derive_pds3_file_size(kv, Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS)

    def test_huge_size_value(self):
        """Size values > 100 GiB sanity limit must be rejected."""
        from backend.app.mission_sources.adapters.pds3_adapter import _derive_pds3_file_size
        huge = str(200 * 1024 * 1024 * 1024)
        kv = _parse_pds3_label(f"FILE_SIZE = {huge}\nEND\n".encode())
        with pytest.raises(GenericPds3AdapterValidationError, match="sanity|limit"):
            _derive_pds3_file_size(kv, Pds3SizeDerivationStrategy.FILE_SIZE)

    def test_missing_required_spacecraft_identity(self):
        """require_spacecraft_id=True: missing INSTRUMENT_HOST_ID raises error."""
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID = "WAV_NOSC"
INSTRUMENT_ID = "WAV"
PROCESSING_LEVEL_ID = "3"
START_TIME = 2024-165T05:55:51.259
STOP_TIME = 2024-165T05:59:02.709
RECORD_BYTES = 512
FILE_RECORDS = 10
^TABLE = "WAV_NOSC.BIN"
END
"""
        with pytest.raises(GenericPds3AdapterValidationError, match="spacecraft|INSTRUMENT_HOST"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_missing_required_instrument_identity(self):
        """require_instrument_id=True: missing INSTRUMENT_ID raises error."""
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID = "WAV_NOINST"
INSTRUMENT_HOST_ID = "JNO"
PROCESSING_LEVEL_ID = "3"
START_TIME = 2024-165T05:55:51.259
STOP_TIME = 2024-165T05:59:02.709
RECORD_BYTES = 512
FILE_RECORDS = 10
^TABLE = "WAV_NOINST.BIN"
END
"""
        with pytest.raises(GenericPds3AdapterValidationError, match="instrument|INSTRUMENT_ID"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_wrong_spacecraft_identity(self):
        """Wrong spacecraft ID (VGR instead of JNO) must be rejected."""
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID = "WAV_WRSC"
INSTRUMENT_HOST_ID = "VGR"
INSTRUMENT_ID = "WAV"
PROCESSING_LEVEL_ID = "3"
START_TIME = 2024-165T05:55:51.259
STOP_TIME = 2024-165T05:59:02.709
RECORD_BYTES = 512
FILE_RECORDS = 10
^TABLE = "WAV_WRSC.BIN"
END
"""
        with pytest.raises(GenericPds3AdapterValidationError):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_payload_normalization_error_propagated(self):
        """If ArchiveDataFile construction fails, parse_generic_pds3_label must fail."""
        # This tests Section F: no silent data-file loss.
        # JunoCam profile uses FILE_SIZE strategy. Supply a malformed FILE_SIZE.
        # Real JunoCam labels use SPACECRAFT_NAME = "JUNO" (not INSTRUMENT_HOST_ID = "JNO").
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-E/J-JNC-2-EDR-L1A-V1.0"
PRODUCT_ID = "JNCR_BADSIZE"
SPACECRAFT_NAME = "JUNO"
INSTRUMENT_ID = "JNC"
START_TIME = 2024-06-13T05:55:51.000
STOP_TIME = 2024-06-13T06:00:00.000
TARGET_NAME = "JUPITER"
FILE_SIZE = NOTANUMBER
^IMAGE = "JNCR_BADSIZE.IMG"
END
"""
        with pytest.raises(GenericPds3AdapterValidationError, match="FILE_SIZE"):
            parse_generic_pds3_label(raw, "src", JUNOCAM_PDS3_PROFILE, _RETRIEVED_AT)


# ===========================================================================
# Section S — Size + snapshot tests
# ===========================================================================


class TestSizeKnowledgeSemantics:
    """Prove size semantics per Section I / Section S requirements."""

    def test_unknown_size_is_none_not_zero(self):
        """SIZE_UNKNOWN: file_size_bytes must be None, not 0."""
        f = ArchiveDataFile(
            file_name="data.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        assert f.file_size_bytes is None
        assert f.file_size_bytes != 0

    def test_zero_size_is_distinct_from_unknown(self):
        """A zero-byte file and an unknown-size file are semantically distinct."""
        zero = ArchiveDataFile(
            file_name="empty.bin",
            file_size_bytes=0,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        unknown = ArchiveDataFile(
            file_name="empty.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        assert zero.file_size_bytes == 0
        assert unknown.file_size_bytes is None
        assert zero != unknown

    def test_approximate_size_has_value(self):
        """Approximate size must contain an actual approximate integer value."""
        f = ArchiveDataFile(
            file_name="data.csv",
            file_size_bytes=512000,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        assert f.file_size_bytes == 512000
        assert f.file_size_bytes is not None

    def test_exact_size_metadata_stays_exact(self):
        """SIZE_METADATA_EXACT must not be downgraded silently."""
        f = ArchiveDataFile(
            file_name="data.csv",
            file_size_bytes=98765,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        assert f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    def test_snapshot_verification_tracked_independently(self):
        """ArchiveSnapshotVerificationStatus is independent of ArchiveDataFileSizeCertainty."""
        # A product with unknown size can still have its snapshot verified.
        v = ArchiveSnapshotVerificationStatus.SNAPSHOT_VERIFIED
        assert v.value == "snapshot_verified"
        # This enum has no file_size_bytes; it's a separate state tracker.
        assert not hasattr(ArchiveDataFileSizeCertainty, "SIZE_SNAPSHOT_VERIFIED")

    def test_writing_loading_snapshot_does_not_mutate_product(self, tmp_path):
        """Writing and loading a snapshot must NOT mutate the ArchiveScienceProduct."""
        snap_path, original_product, _ = _write_snapshot(tmp_path)
        loaded_product, _ = ArchiveLabelSnapshotStore.load_from_explicit_reparser(
            snap_path, _waves_reparser
        )
        # The loaded product must equal the original (no mutation).
        assert loaded_product == original_product
        # Specifically: no size field changed.
        if original_product.data_files:
            orig_f = original_product.data_files[0]
            load_f = loaded_product.data_files[0]
            assert orig_f.file_size_bytes == load_f.file_size_bytes
            assert orig_f.size_certainty == load_f.size_certainty

    def test_pds3_unknown_size_with_none_strategy(self):
        """When strategy=NONE, file_size_bytes is None (SIZE_UNKNOWN)."""
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-J-JAD-3-CDR-V1.0"
PRODUCT_ID = "JAD_L20_LO_TEST"
INSTRUMENT_HOST_ID = "JNO"
INSTRUMENT_ID = "JAD"
START_TIME = 2024-165T05:55:51.000
STOP_TIME = 2024-165T06:00:00.000
TARGET_NAME = "JUPITER"
^TABLE = "JAD_L20_LO_TEST.DAT"
END
"""
        product, _ = parse_generic_pds3_label(raw, "src", JADE_PDS3_PROFILE, _RETRIEVED_AT)
        if product.data_files:
            assert product.data_files[0].file_size_bytes is None
            assert product.data_files[0].size_certainty == ArchiveDataFileSizeCertainty.SIZE_UNKNOWN


# ===========================================================================
# Section T — Manifest tests
# ===========================================================================


def _make_entry(lid, rids, avail=_NOW_UTC, prov_ids=()):
    return VerifiedInventoryEntry(
        logical_product_id=lid,
        representation_record_ids=rids,
        availability_time_utc=avail,
        source_fact_provenance_ids=prov_ids,
    )


def _make_ref(rid, prov_id, profile_id="waves_burst_pds3"):
    return VerifiedSourceRecordRef(
        source_record_id=rid,
        source_standard=ArchiveSourceStandard.PDS3,
        provenance_id=prov_id,
        normalizer_id=_PDS3_NORMALIZER_ID,
        profile_id=profile_id,
    )


class TestManifestSourceRegistry:
    """Section L + T: manifest source-record registry and referential integrity."""

    def test_valid_manifest_with_source_records(self):
        entry = _make_entry("LP-001", ("pds3:DS:P001",))
        ref = _make_ref("pds3:DS:P001", "prov-001")
        m = VerifiedInventoryManifest.build([entry], source_records=[ref])
        assert len(m.source_records) == 1
        assert len(m.entries) == 1

    def test_dangling_source_record_rejected(self):
        """representation_record_id not in source_records is rejected."""
        entry = _make_entry("LP-001", ("pds3:DS:DANGLING",))
        ref = _make_ref("pds3:DS:DIFFERENT", "prov-001")
        with pytest.raises(Exception, match="[Dd]angling|DANGLING"):
            VerifiedInventoryManifest.build([entry], source_records=[ref])

    def test_dangling_provenance_ref_rejected(self):
        """source_fact_provenance_id with no matching source_records.provenance_id is rejected."""
        entry = _make_entry("LP-001", ("pds3:DS:P001",), prov_ids=("ghost-prov",))
        ref = _make_ref("pds3:DS:P001", "prov-001")
        with pytest.raises(Exception, match="[Dd]angling|provenance|ghost-prov"):
            VerifiedInventoryManifest.build([entry], source_records=[ref])

    def test_duplicate_source_record_id_rejected(self):
        entry = _make_entry("LP-001", ("pds3:DS:P001",))
        ref1 = _make_ref("pds3:DS:P001", "prov-001")
        ref2 = _make_ref("pds3:DS:P001", "prov-002")  # same source_record_id
        with pytest.raises(Exception, match="[Dd]uplicate|source_record_id"):
            VerifiedInventoryManifest.build([entry], source_records=[ref1, ref2])

    def test_411_scale_with_source_records(self):
        """411+ entries with source_records all validate and pass."""
        entries = []
        refs = []
        for i in range(411):
            rid = f"pds3:DS:PROD_{i:04d}"
            pid = f"prov-{i:04d}"
            entries.append(_make_entry(f"LP-{i:04d}", (rid,), prov_ids=(pid,)))
            refs.append(_make_ref(rid, pid))
        m = VerifiedInventoryManifest.build(entries, source_records=refs)
        assert len(m.entries) == 411
        assert len(m.source_records) == 411
        assert len(m.manifest_id) == 64


class TestManifestIdSemanticMutations:
    """Section M: manifest_id must cover all semantic content."""

    def _make_ref_for_rid(self, rid: str, prov_id: str = "prov-000") -> VerifiedSourceRecordRef:
        return VerifiedSourceRecordRef(
            source_record_id=rid,
            source_standard=ArchiveSourceStandard.PDS3,
            provenance_id=prov_id,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )

    def _build_with_auto_refs(self, entries):
        """Build manifest with auto-generated source_records for each rid."""
        all_rids = list({rid for e in entries for rid in e.representation_record_ids})
        all_prov_ids = list({pid for e in entries for pid in e.source_fact_provenance_ids})
        # Build one ref per rid; if there are provenance ids, use the first as prov_id.
        refs = []
        for i, rid in enumerate(all_rids):
            prov_id = all_prov_ids[i] if i < len(all_prov_ids) else f"auto-prov-{i:04d}"
            refs.append(self._make_ref_for_rid(rid, prov_id))
        return VerifiedInventoryManifest.build(entries, source_records=refs)

    def test_id_changes_when_availability_changes(self):
        e1 = _make_entry("LP-001", ("rid1",), avail=datetime(2024, 1, 1, tzinfo=timezone.utc))
        m1 = self._build_with_auto_refs([e1])
        e2 = _make_entry("LP-001", ("rid1",), avail=datetime(2025, 1, 1, tzinfo=timezone.utc))
        m2 = self._build_with_auto_refs([e2])
        assert m1.manifest_id != m2.manifest_id

    def test_id_changes_when_representation_changes(self):
        e1 = _make_entry("LP-001", ("rid-A",))
        m1 = self._build_with_auto_refs([e1])
        e2 = _make_entry("LP-001", ("rid-B",))
        m2 = self._build_with_auto_refs([e2])
        assert m1.manifest_id != m2.manifest_id

    def test_id_changes_when_provenance_ref_changes(self):
        e1 = _make_entry("LP-001", ("rid1",), prov_ids=("prov-A",))
        ref1 = self._make_ref_for_rid("rid1", "prov-A")
        m1 = VerifiedInventoryManifest.build([e1], source_records=[ref1])
        e2 = _make_entry("LP-001", ("rid1",), prov_ids=("prov-B",))
        ref2 = self._make_ref_for_rid("rid1", "prov-B")
        m2 = VerifiedInventoryManifest.build([e2], source_records=[ref2])
        assert m1.manifest_id != m2.manifest_id

    def test_id_changes_when_source_record_changes(self):
        entry = _make_entry("LP-001", ("pds3:DS:P001",))
        ref1 = _make_ref("pds3:DS:P001", "prov-001", profile_id="waves_burst_pds3.v1")
        ref2 = _make_ref("pds3:DS:P001", "prov-001", profile_id="waves_burst_pds3.v2")
        m1 = VerifiedInventoryManifest.build([entry], source_records=[ref1])
        m2 = VerifiedInventoryManifest.build([entry], source_records=[ref2])
        assert m1.manifest_id != m2.manifest_id

    def test_canonical_reorder_same_id(self):
        e1 = _make_entry("LP-AAA", ("rid-a",))
        e2 = _make_entry("LP-ZZZ", ("rid-z",))
        refs = [self._make_ref_for_rid("rid-a"), self._make_ref_for_rid("rid-z", "prov-001")]
        m1 = VerifiedInventoryManifest.build([e1, e2], source_records=refs)
        m2 = VerifiedInventoryManifest.build([e2, e1], source_records=refs)  # reversed order
        assert m1.manifest_id == m2.manifest_id


# ===========================================================================
# Section N — Snapshot normalizer/profile binding
# ===========================================================================


class TestSnapshotNormalizerProfileBinding:
    def test_normalizer_and_profile_stored_in_envelope(self, tmp_path):
        """Snapshot envelope must carry normalizer_id and profile_id."""
        snap_path, _, _ = _write_snapshot(tmp_path)
        data = json.loads(snap_path.read_text())
        assert data.get("normalizer_id") == _PDS3_NORMALIZER_ID
        assert data.get("profile_id") == WAVES_BURST_PDS3_PROFILE.profile_id

    def test_registry_load_after_register(self, tmp_path):
        """load() resolves the parser from the registry (pre-registered at import)."""
        snap_path, _, _ = _write_snapshot(tmp_path)
        # Production parsers are pre-registered at import; use _register_parser_force
        # to ensure the test-local parser is in place (idempotent for the production pair).
        _register_parser_force(
            _PDS3_NORMALIZER_ID, WAVES_BURST_PDS3_PROFILE.profile_id, _waves_reparser
        )
        # load() should succeed via the registry.
        product, prov = ArchiveLabelSnapshotStore.load(snap_path)
        assert product.source_record_id.startswith("pds3:")
        assert "WAV_B11_HARD_TEST" in product.source_record_id

    def test_unknown_normalizer_id_rejected(self, tmp_path):
        """load() must reject snapshots with unknown normalizer_id/profile_id."""
        snap_path, _, _ = _write_snapshot(tmp_path)
        # Write a snapshot with a normalizer_id NOT in the registry.
        data = json.loads(snap_path.read_text())
        data["normalizer_id"] = "gcsi.nonexistent_normalizer.v999"
        # Recompute snapshot_id to make envelope consistent.
        new_snap_id = _compute_snapshot_id(
            data["snapshot_source_standard"],
            data["provenance"]["provenance_id"],
            data["retrieved_at"],
        )
        data["snapshot_id"] = new_snap_id
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError, match="[Uu]nknown|[Nn]ormalizer"):
            ArchiveLabelSnapshotStore.load(snap_path)


# ===========================================================================
# Section P — Snapshot cross-checks
# ===========================================================================


class TestSnapshotCrossChecks:
    def test_snapshot_source_standard_matches_product(self, tmp_path):
        """snapshot_source_standard must match the re-derived product's source_standard."""
        snap_path, product, _ = _write_snapshot(tmp_path)
        # Verify the stored snapshot_source_standard is consistent with the product.
        data = json.loads(snap_path.read_text())
        assert data["snapshot_source_standard"] == product.source_standard.value

    def test_tampered_snapshot_source_standard_rejected(self, tmp_path):
        """If snapshot_source_standard is changed to mismatch, load must reject it."""
        snap_path, _, _ = _write_snapshot(tmp_path)
        data = json.loads(snap_path.read_text())
        # Tamper: change source_standard from pds3 to pds4.
        data["snapshot_source_standard"] = "pds4"
        # Also update snapshot_id to make envelope structurally valid.
        new_snap_id = _compute_snapshot_id(
            "pds4",
            data["provenance"]["provenance_id"],
            data["retrieved_at"],
        )
        data["snapshot_id"] = new_snap_id
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)


# ===========================================================================
# Section V — Real profile smoke fixtures (no network)
# ===========================================================================


class TestRealProfileUrls:
    """Smoke tests proving production profiles use official archive URLs.

    No network activity. Tests validate URL against profile trust constraints.
    """

    # --- PDS4 Profiles ---

    def test_jiram_official_atmospheres_path_accepted(self):
        """JIRAM production profile: atmos.nmsu.edu Atmospheres Node path."""
        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/jir_img_rec_pj062_v01.xml"
        _validate_label_url_trust(url, JIRAM_PDS4_PROFILE)

    def test_jiram_wrong_host_rejected(self):
        url = "https://pds.nasa.gov/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh]ost"):
            _validate_label_url_trust(url, JIRAM_PDS4_PROFILE)

    def test_uvs_official_archive_path_accepted(self):
        """UVS production profile: atmos.nmsu.edu Atmospheres Node path."""
        url = "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/data/jno_uvs_3001_cal_20240613_v01.xml"
        _validate_label_url_trust(url, UVS_PDS4_PROFILE)

    def test_uvs_wrong_host_rejected(self):
        url = "https://pds.nasa.gov/PDS/data/jnouvs_3001/data/test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh]ost"):
            _validate_label_url_trust(url, UVS_PDS4_PROFILE)

    def test_mwr_existing_path_accepted(self):
        """MWR production profile: pds-atmospheres.nmsu.edu existing path."""
        url = "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/mwr62ri2024165030000_r04112_v04.xml"
        _validate_label_url_trust(url, MWR_GENERIC_PDS4_PROFILE)

    def test_mwr_wrong_host_rejected(self):
        url = "https://atmos.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/test.xml"
        with pytest.raises(GenericPds4AdapterValidationError, match="[Hh]ost"):
            _validate_label_url_trust(url, MWR_GENERIC_PDS4_PROFILE)

    # --- PDS3 Profile URL patterns — ACTUAL PRODUCTION PROFILE OBJECTS ---
    # These tests prove the REAL production profile trust boundaries, not copies.

    def test_junocam_pds3_official_url_accepted(self):
        """JUNOCAM_PDS3_PROFILE: planetarydata.jpl.nasa.gov PDS Imaging Node path accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        # Official: PDS Imaging Node, JNOJNC_0029 volume (Phase 6F-B1.2.1 corrected)
        url = "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/DATA/2024/jncr_2024165_01m01280_v01.lbl"
        _validate_pds3_source_url_trust(url, JUNOCAM_PDS3_PROFILE)

    def test_junocam_pds3_wrong_host_rejected(self):
        """JUNOCAM_PDS3_PROFILE: wrong host rejected."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/img/data/juno/JNOJNC_0029/DATA/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )

    def test_junocam_pds3_wrong_path_rejected(self):
        """JUNOCAM_PDS3_PROFILE: sibling/wrong path rejected."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://planetarydata.jpl.nasa.gov/WRONG/path/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )

    def test_fgm_pds3_official_url_accepted(self):
        """FGM_PDS3_PROFILE: pds-ppi.igpp.ucla.edu official JNO-J-3-FGM-CAL-V1.0 root accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        url = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/2024/fgm_2024165_orbit62.lbl"
        _validate_pds3_source_url_trust(url, FGM_PDS3_PROFILE)

    def test_fgm_pds3_wrong_host_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J-3-FGM-CAL-V1.0/DATA/t.lbl",
                FGM_PDS3_PROFILE,
            )

    def test_fgm_pds3_wrong_path_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
                FGM_PDS3_PROFILE,
            )

    def test_jade_pds3_official_url_accepted(self):
        """JADE_PDS3_PROFILE: official JNO-J_SW-JAD-3-CALIBRATED-V1.0 root accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        url = "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/jad_l20_lo_tof3d_2024165.lbl"
        _validate_pds3_source_url_trust(url, JADE_PDS3_PROFILE)

    def test_jade_pds3_wrong_host_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/t.lbl",
                JADE_PDS3_PROFILE,
            )

    def test_jedi_pds3_official_url_accepted(self):
        """JEDI_PDS3_PROFILE: official JNO-J-JED-3-CDR-V1.0 root accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        url = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/jed_2024165_ch0_l2.lbl"
        _validate_pds3_source_url_trust(url, JEDI_PDS3_PROFILE)

    def test_jedi_pds3_wrong_host_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J-JED-3-CDR-V1.0/DATA/t.lbl",
                JEDI_PDS3_PROFILE,
            )

    def test_waves_survey_pds3_official_url_accepted(self):
        """WAVES_SURVEY_PDS3_PROFILE: official JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0 root accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        url = "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/2024/wav_2024165_srvy.lbl"
        _validate_pds3_source_url_trust(url, WAVES_SURVEY_PDS3_PROFILE)

    def test_waves_survey_pds3_wrong_host_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/t.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )

    def test_waves_survey_pds3_wrong_path_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )

    def test_waves_burst_pds3_official_url_accepted(self):
        """WAVES_BURST_PDS3_PROFILE: official JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0 root accepted."""
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        url = "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/DATA/2024/wav_2024165t055551_b_bin.lbl"
        _validate_pds3_source_url_trust(url, WAVES_BURST_PDS3_PROFILE)

    def test_waves_burst_pds3_wrong_host_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/DATA/t.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )

    def test_waves_burst_pds3_wrong_path_rejected(self):
        from backend.app.mission_sources.adapters.pds3_adapter import _validate_pds3_source_url_trust
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )

    # Profile identity checks
    def test_jiram_profile_id(self):
        assert JIRAM_PDS4_PROFILE.profile_id == "jiram_pds4"

    def test_uvs_profile_id(self):
        assert UVS_PDS4_PROFILE.profile_id == "uvs_pds4"

    def test_mwr_profile_id(self):
        assert MWR_GENERIC_PDS4_PROFILE.profile_id == "mwr_generic_pds4"

    def test_waves_burst_profile_id(self):
        assert WAVES_BURST_PDS3_PROFILE.profile_id == "waves_burst_pds3"

    def test_waves_survey_profile_id(self):
        assert WAVES_SURVEY_PDS3_PROFILE.profile_id == "waves_survey_pds3"

    def test_junocam_profile_id(self):
        assert JUNOCAM_PDS3_PROFILE.profile_id == "junocam_pds3"

    def test_fgm_profile_id(self):
        assert FGM_PDS3_PROFILE.profile_id == "fgm_pds3"

    def test_jade_profile_id(self):
        assert JADE_PDS3_PROFILE.profile_id == "jade_pds3"

    def test_jedi_profile_id(self):
        assert JEDI_PDS3_PROFILE.profile_id == "jedi_pds3"
