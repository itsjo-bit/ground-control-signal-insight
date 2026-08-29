"""GCSI Phase 6F-B1.2.2 — Production Snapshot Write + PDS4 Fail-Closed Tests.

All tests are OFFLINE. No network activity.

Coverage (12 required items):
  1.  Production snapshot write has no arbitrary reparser parameter.
  2.  Unknown production normalizer/profile pair rejected.
  3.  Caller cannot overwrite production parser mapping (_PRODUCTION_RESOLVER).
  4.  Production load uses immutable production resolver (not mutable registry).
  5.  source_standard mismatch rejected before write.
  6.  Empty PDS4 file_size text rejected.
  7.  Missing file_size unit rejected.
  8.  Empty file_size unit rejected.
  9.  Absent file_size remains SIZE_UNKNOWN.
 10.  Multiple File_Area elements are all normalized (OPTION A).
 11.  Profile requiring payload rejects missing File_Area.
 12.  Metadata-only profile explicitly permits missing File_Area.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent
from types import MappingProxyType
from typing import Optional

import pytest

from backend.app.mission_sources.adapters.pds3_adapter import (
    WAVES_BURST_PDS3_PROFILE,
    parse_generic_pds3_label,
    _PDS3_NORMALIZER_ID,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    JIRAM_PDS4_PROFILE,
    GenericPds4AdapterProfile,
    GenericPds4AdapterValidationError,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveDataFileSizeCertainty,
    ArchiveSourceStandard,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotValidationError,
    _FROZEN_PRODUCTION_PROFILE_MAP,
    _PRODUCTION_RESOLVER,
    _compute_snapshot_id,
    _register_parser_force,
    register_parser,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)

_WAVES_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_B122_TEST"
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
^TABLE                = "WAV_B122_TEST_V01.BIN"
END
"""

_WAVES_OFFICIAL_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
    "DATA/2024/wav_b122_test_v01.lbl"
)


def _waves_reparser(raw_bytes, source_ref, retrieved_at):
    return parse_generic_pds3_label(
        raw_bytes, source_ref, WAVES_BURST_PDS3_PROFILE, retrieved_at
    )


def _build_waves_capture(source_ref: Optional[str] = None) -> ArchiveCaptureRecord:
    """Build a valid ArchiveCaptureRecord from _WAVES_LABEL."""
    ref = source_ref or "fixture:b122_waves"
    product, prov = _waves_reparser(_WAVES_LABEL, ref, _RETRIEVED_AT)
    return ArchiveCaptureRecord(
        source_label_ref=ref,
        product=product,
        provenance=prov,
        raw_label_bytes=_WAVES_LABEL,
    )


# ---------------------------------------------------------------------------
# PDS4 label builder for file_size / File_Area multiplicity tests
# ---------------------------------------------------------------------------

_JIRAM_VALID_URL = (
    "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/"
    "data_calibrated/jir_b122_test.xml"
)

_JIRAM_METADATA_ONLY_PROFILE = GenericPds4AdapterProfile(
    profile_id="jiram_b122_meta_only",
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
    require_data_file=False,
)

_JIRAM_PAYLOAD_REQUIRED_PROFILE = GenericPds4AdapterProfile(
    profile_id="jiram_b122_payload_req",
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
    require_data_file=True,
)


def _jiram_label_with_file_areas(file_areas_xml: str) -> bytes:
    """Build a minimal valid JIRAM PDS4 label with the given File_Area_Observational XML."""
    return dedent(f"""\
<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_b122_multi</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B122 Multi-FileArea Test</title>
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
    <Target_Identification><name>Jupiter</name></Target_Identification>
  </Observation_Area>
{file_areas_xml}
</Product_Observational>
""").encode("utf-8")


def _jiram_no_file_area_label() -> bytes:
    """Minimal JIRAM label with NO File_Area_Observational."""
    return _jiram_label_with_file_areas("")


def _jiram_single_file_area_label(file_size_xml: str = "", file_name: str = "jir_b122.img") -> bytes:
    """Minimal JIRAM label with one File_Area containing the given file_size XML."""
    return _jiram_label_with_file_areas(f"""\
  <File_Area_Observational>
    <File>
      <file_name>{file_name}</file_name>
{file_size_xml}    </File>
  </File_Area_Observational>""")


def _jiram_two_file_areas_label() -> bytes:
    """Minimal JIRAM label with TWO File_Area_Observational elements."""
    return _jiram_label_with_file_areas("""\
  <File_Area_Observational>
    <File>
      <file_name>jir_b122_img.img</file_name>
      <file_size unit="byte">131072</file_size>
    </File>
  </File_Area_Observational>
  <File_Area_Observational>
    <File>
      <file_name>jir_b122_spe.tab</file_name>
      <file_size unit="byte">65536</file_size>
    </File>
  </File_Area_Observational>""")


# ===========================================================================
# Test 1: Production write has no reparser parameter
# ===========================================================================


class TestProductionWriteNoReparserParam:
    """Item 1: ArchiveLabelSnapshotStore.write() must not accept a reparser parameter."""

    def test_write_signature_has_no_reparser_param(self):
        """Inspect write() signature — must not include a 'reparser' parameter."""
        sig = inspect.signature(ArchiveLabelSnapshotStore.write)
        assert "reparser" not in sig.parameters, (
            "Production write() must not accept a reparser parameter. "
            f"Got parameters: {list(sig.parameters.keys())}"
        )

    def test_write_signature_accepts_capture(self):
        """write() must accept 'capture' as first positional parameter."""
        sig = inspect.signature(ArchiveLabelSnapshotStore.write)
        params = list(sig.parameters.keys())
        assert "capture" in params, (
            f"Production write() must have 'capture' parameter. Got: {params}"
        )
        assert "normalizer_id" in params
        assert "profile_id" in params

    def test_production_write_succeeds_with_capture_record(self, tmp_path):
        """Production write() must accept an ArchiveCaptureRecord and succeed."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_prod_write.json"
        ArchiveLabelSnapshotStore.write(
            capture=capture,
            path=snap_path,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )
        assert snap_path.exists()
        data = json.loads(snap_path.read_text())
        assert data["normalizer_id"] == _PDS3_NORMALIZER_ID
        assert data["profile_id"] == "waves_burst_pds3"

    def test_write_with_explicit_reparser_for_test_still_works(self, tmp_path):
        """The test-only path must still function for backward compat."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b122_explicit_test", _RETRIEVED_AT)
        snap_path = tmp_path / "b122_explicit_test.json"
        ArchiveLabelSnapshotStore._write_with_explicit_reparser_for_test(
            raw_label_bytes=raw,
            source_ref="fixture:b122_explicit_test",
            product=product,
            provenance=prov,
            reparser=_waves_reparser,
            path=snap_path,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )
        assert snap_path.exists()


# ===========================================================================
# Test 2: Unknown production normalizer/profile pair rejected
# ===========================================================================


class TestUnknownProductionPairRejected:
    """Item 2: write() must reject unknown (normalizer_id, profile_id) pairs."""

    def test_unknown_normalizer_id_rejected(self, tmp_path):
        """write() with unknown normalizer_id must raise ArchiveSnapshotValidationError."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_unknown_nid.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="[Uu]nknown|[Pp]air"):
            ArchiveLabelSnapshotStore.write(
                capture=capture,
                path=snap_path,
                normalizer_id="gcsi.nonexistent_normalizer.v1",
                profile_id="waves_burst_pds3",
            )
        assert not snap_path.exists()

    def test_unknown_profile_id_rejected(self, tmp_path):
        """write() with unknown profile_id must raise ArchiveSnapshotValidationError."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_unknown_pid.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="[Uu]nknown|[Pp]air"):
            ArchiveLabelSnapshotStore.write(
                capture=capture,
                path=snap_path,
                normalizer_id=_PDS3_NORMALIZER_ID,
                profile_id="nonexistent_profile_xyz",
            )
        assert not snap_path.exists()

    def test_empty_normalizer_id_rejected(self, tmp_path):
        """write() with empty normalizer_id must raise ArchiveSnapshotValidationError."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_empty_nid.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="normalizer"):
            ArchiveLabelSnapshotStore.write(
                capture=capture,
                path=snap_path,
                normalizer_id="",
                profile_id="waves_burst_pds3",
            )
        assert not snap_path.exists()

    def test_empty_profile_id_rejected(self, tmp_path):
        """write() with empty profile_id must raise ArchiveSnapshotValidationError."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_empty_pid.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="profile"):
            ArchiveLabelSnapshotStore.write(
                capture=capture,
                path=snap_path,
                normalizer_id=_PDS3_NORMALIZER_ID,
                profile_id="",
            )
        assert not snap_path.exists()


# ===========================================================================
# Test 3: Caller cannot overwrite production parser mapping
# ===========================================================================


class TestProductionParserMappingImmutable:
    """Item 3: _PRODUCTION_RESOLVER must be a MappingProxyType — cannot be mutated."""

    def test_production_resolver_is_mapping_proxy(self):
        """_PRODUCTION_RESOLVER must be a MappingProxyType."""
        assert isinstance(_PRODUCTION_RESOLVER, MappingProxyType), (
            f"_PRODUCTION_RESOLVER must be MappingProxyType; got {type(_PRODUCTION_RESOLVER)}"
        )

    def test_production_resolver_cannot_be_assigned_new_key(self):
        """Attempting to add a new key to _PRODUCTION_RESOLVER must raise TypeError."""
        with pytest.raises(TypeError):
            _PRODUCTION_RESOLVER[("gcsi.evil.v1", "evil_profile")] = object()  # type: ignore[index]

    def test_production_resolver_cannot_delete_key(self):
        """Attempting to delete a key from _PRODUCTION_RESOLVER must raise TypeError."""
        some_key = next(iter(_PRODUCTION_RESOLVER))
        with pytest.raises(TypeError):
            del _PRODUCTION_RESOLVER[some_key]  # type: ignore[attr-defined]

    def test_frozen_production_profile_map_is_mapping_proxy(self):
        """_FROZEN_PRODUCTION_PROFILE_MAP must also be a MappingProxyType."""
        assert isinstance(_FROZEN_PRODUCTION_PROFILE_MAP, MappingProxyType)

    def test_frozen_production_profile_map_consistent_with_resolver(self):
        """_FROZEN_PRODUCTION_PROFILE_MAP keys must equal _PRODUCTION_RESOLVER keys."""
        assert set(_FROZEN_PRODUCTION_PROFILE_MAP.keys()) == set(_PRODUCTION_RESOLVER.keys())


# ===========================================================================
# Test 4: Production load uses immutable production resolver
# ===========================================================================


class TestProductionLoadUsesImmutableResolver:
    """Item 4: load() resolves via _PRODUCTION_RESOLVER, not the mutable registry."""

    def test_load_succeeds_with_production_pair(self, tmp_path):
        """load() must succeed when the snapshot uses a known production pair."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_load_prod.json"
        ArchiveLabelSnapshotStore.write(
            capture=capture,
            path=snap_path,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )
        product, prov = ArchiveLabelSnapshotStore.load(snap_path)
        assert product.source_standard == ArchiveSourceStandard.PDS3
        assert product.instrument_name == "WAV"
        assert prov.kind.value == "external_authoritative"

    def test_load_rejects_snapshot_with_unknown_pair(self, tmp_path):
        """load() must reject a tampered snapshot claiming an unknown production pair."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_load_unknown.json"
        ArchiveLabelSnapshotStore.write(
            capture=capture,
            path=snap_path,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )
        data = json.loads(snap_path.read_text())
        data["normalizer_id"] = "gcsi.nonexistent.v1"
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError, match="[Uu]nknown|[Pp]air"):
            ArchiveLabelSnapshotStore.load(snap_path)


# ===========================================================================
# Test 5: source_standard mismatch rejected before write
# ===========================================================================


class TestSourceStandardMismatchRejected:
    """Item 5: write() must reject source_standard mismatch before writing."""

    def test_pds3_capture_with_pds4_pair_rejected(self, tmp_path):
        """A PDS3 capture with a PDS4 normalizer/profile pair must be rejected."""
        capture = _build_waves_capture(_WAVES_OFFICIAL_URL)
        snap_path = tmp_path / "b122_std_mismatch.json"
        # PDS3 capture with PDS4 pair — source_standard mismatch.
        with pytest.raises(ArchiveSnapshotValidationError, match="[Ss]tandard|mismatch"):
            ArchiveLabelSnapshotStore.write(
                capture=capture,
                path=snap_path,
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
            )
        assert not snap_path.exists()


# ===========================================================================
# Test 6: Empty PDS4 file_size text rejected
# ===========================================================================


class TestPds4FileSizeEmptyTextRejected:
    """Item 6: <file_size unit="byte"></file_size> (empty text) must be rejected."""

    def test_empty_file_size_text_rejected(self):
        """<file_size unit="byte"> with whitespace-only text must raise."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="byte">   </file_size>\n'
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="file_size|[Ee]mpty"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )

    def test_file_size_empty_element_rejected(self):
        """<file_size unit="byte"/> (self-closing, no text) must be rejected."""
        # ElementTree treats self-closing tags as None text — same as empty.
        from io import BytesIO
        import xml.etree.ElementTree as ET
        # Build a label with <file_size unit="byte"/> by inserting via XML manipulation.
        label_str = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="byte"></file_size>\n'
        ).decode("utf-8")
        label = label_str.encode("utf-8")
        with pytest.raises(GenericPds4AdapterValidationError, match="file_size|[Ee]mpty"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )


# ===========================================================================
# Test 7: Missing file_size unit rejected
# ===========================================================================


class TestPds4FileSizeMissingUnitRejected:
    """Item 7: <file_size>123</file_size> (no unit attribute) must be rejected."""

    def test_file_size_without_unit_attribute_rejected(self):
        """<file_size>123</file_size> with no unit= attribute must raise."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size>123</file_size>\n'
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )


# ===========================================================================
# Test 8: Empty file_size unit rejected
# ===========================================================================


class TestPds4FileSizeEmptyUnitRejected:
    """Item 8: <file_size unit="">123</file_size> (empty unit) must be rejected."""

    def test_file_size_empty_unit_rejected(self):
        """<file_size unit="">123</file_size> must raise."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="">123</file_size>\n'
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )

    def test_file_size_kilobyte_unit_rejected(self):
        """<file_size unit="kilobyte">123</file_size> must raise."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="kilobyte">123</file_size>\n'
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )

    def test_file_size_byte_non_integer_rejected(self):
        """<file_size unit="byte">abc</file_size> must raise."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="byte">abc</file_size>\n'
        )
        with pytest.raises(GenericPds4AdapterValidationError, match="file_size|malform"):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )


# ===========================================================================
# Test 9: Absent file_size remains SIZE_UNKNOWN
# ===========================================================================


class TestPds4AbsentFileSizeRemainsSizeUnknown:
    """Item 9: No <file_size> element → file_size_bytes=None, size_certainty=SIZE_UNKNOWN."""

    def test_absent_file_size_is_size_unknown(self):
        """Label with File element but no file_size → SIZE_UNKNOWN, total=None."""
        label = _jiram_single_file_area_label(file_size_xml="")
        product, _ = parse_generic_pds4_label(
            label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
        )
        assert len(product.data_files) == 1
        f = product.data_files[0]
        assert f.file_size_bytes is None
        assert f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_UNKNOWN
        assert product.total_data_size_bytes is None

    def test_valid_byte_file_size_accepted(self):
        """<file_size unit="byte">131072</file_size> must be parsed as SIZE_METADATA_EXACT."""
        label = _jiram_single_file_area_label(
            file_size_xml='      <file_size unit="byte">131072</file_size>\n'
        )
        product, _ = parse_generic_pds4_label(
            label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
        )
        assert len(product.data_files) == 1
        f = product.data_files[0]
        assert f.file_size_bytes == 131072
        assert f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        assert product.total_data_size_bytes == 131072


# ===========================================================================
# Test 10: Multiple File_Area elements are all normalized
# ===========================================================================


class TestPds4MultipleFileAreasNormalized:
    """Item 10: All File_Area_Observational elements must be normalized (Option A)."""

    def test_two_file_areas_produce_two_data_files(self):
        """Label with two File_Area_Observational elements must produce two ArchiveDataFiles."""
        label = _jiram_two_file_areas_label()
        product, _ = parse_generic_pds4_label(
            label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
        )
        assert len(product.data_files) == 2
        file_names = {f.file_name for f in product.data_files}
        assert "jir_b122_img.img" in file_names
        assert "jir_b122_spe.tab" in file_names

    def test_two_file_areas_sum_is_correct(self):
        """Two file areas with known sizes must have correct total_data_size_bytes."""
        label = _jiram_two_file_areas_label()
        product, _ = parse_generic_pds4_label(
            label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
        )
        # 131072 + 65536 = 196608
        assert product.total_data_size_bytes == 196608

    def test_second_file_area_with_invalid_size_raises(self):
        """A bad file_size in the second File_Area must raise, not be silently ignored."""
        two_areas_bad_second = _jiram_label_with_file_areas("""\
  <File_Area_Observational>
    <File>
      <file_name>jir_good.img</file_name>
      <file_size unit="byte">1024</file_size>
    </File>
  </File_Area_Observational>
  <File_Area_Observational>
    <File>
      <file_name>jir_bad.img</file_name>
      <file_size unit="kilobyte">1024</file_size>
    </File>
  </File_Area_Observational>""")
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(
                two_areas_bad_second,
                _JIRAM_VALID_URL,
                _JIRAM_PAYLOAD_REQUIRED_PROFILE,
                _RETRIEVED_AT,
            )


# ===========================================================================
# Test 11: Profile requiring payload rejects missing File_Area
# ===========================================================================


class TestProfileRequiringPayloadRejectsMissingFileArea:
    """Item 11: require_data_file=True must reject labels without File_Area_Observational."""

    def test_require_data_file_true_rejects_missing_file_area(self):
        """Profile with require_data_file=True must reject label with no File_Area."""
        label = _jiram_no_file_area_label()
        with pytest.raises(
            GenericPds4AdapterValidationError,
            match="[Ff]ile_Area|[Pp]ayload|require_data_file",
        ):
            parse_generic_pds4_label(
                label, _JIRAM_VALID_URL, _JIRAM_PAYLOAD_REQUIRED_PROFILE, _RETRIEVED_AT
            )

    def test_require_data_file_is_true_by_default_in_profile(self):
        """GenericPds4AdapterProfile must default require_data_file=True."""
        profile = GenericPds4AdapterProfile(
            profile_id="b122_default_rdf",
            allowed_hosts=frozenset({"atmos.nmsu.edu"}),
            allowed_path_prefixes=("/PDS/",),
            expected_mission="JUNO",
            expected_spacecraft="JNO",
            expected_instrument="JIRAM",
            instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
            spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
            investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
            product_family="JIRAM",
        )
        assert profile.require_data_file is True

    def test_production_jiram_profile_requires_data_file(self):
        """Built-in JIRAM_PDS4_PROFILE must require a data file payload."""
        assert JIRAM_PDS4_PROFILE.require_data_file is True


# ===========================================================================
# Test 12: Metadata-only profile explicitly permits missing File_Area
# ===========================================================================


class TestMetadataOnlyProfilePermitsMissingFileArea:
    """Item 12: require_data_file=False must permit labels without File_Area."""

    def test_require_data_file_false_accepts_missing_file_area(self):
        """Profile with require_data_file=False must accept label with no File_Area."""
        label = _jiram_no_file_area_label()
        product, _ = parse_generic_pds4_label(
            label, _JIRAM_VALID_URL, _JIRAM_METADATA_ONLY_PROFILE, _RETRIEVED_AT
        )
        assert product.data_files == ()
        assert product.total_data_size_bytes == 0

    def test_metadata_only_profile_attribute_is_false(self):
        """_JIRAM_METADATA_ONLY_PROFILE.require_data_file must be False."""
        assert _JIRAM_METADATA_ONLY_PROFILE.require_data_file is False
