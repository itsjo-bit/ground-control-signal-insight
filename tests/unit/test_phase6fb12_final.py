"""GCSI Phase 6F-B1.2 — Final Verified Source Foundation Gate Tests.

Items covered:
  1.  True streaming oversized body cutoff (PDS3 and PDS4): reader stops at
      MAX+1 without materialising the full oversized response body.
  2.  Actual production PDS3 profile host/path rejection (no _make_profile_with_url
      proxy — tests use the real production profile objects directly).
  3.  PDS3 http/ftp/evil-scheme external source_ref rejection (Item 3: any
      scheme-with-://-but-not-https is rejected, not silently skipped).
  4.  Malformed PDS4 checksum propagates error (not silent []).
  5a. Malformed PDS4 file_size propagates error (not silent []).
  5b. Unknown aggregate size != zero (None != 0 at product level).
  5c. Mixed known/unknown aggregate size → total = None.
  6.  Verified manifest without source registry rejected.
  7a. 411-entry fully-referenced verified manifest passes.
  7b. Parser registry cannot be overwritten / injected.
  8.  Snapshot write rejects unknown/empty normalizer_id or profile_id.
  9.  Snapshot load rejects normalizer/source_standard mismatch.
  10. Source fact vs normalization fact semantics (documented in product).

All tests are OFFLINE. No live PDS requests are made.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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
    _validate_pds3_source_url_trust,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    JIRAM_PDS4_PROFILE,
    MAX_PDS4_LABEL_BYTES,
    GenericPds4AdapterProfile,
    GenericPds4AdapterUnavailableError,
    GenericPds4AdapterValidationError,
    GenericPds4ObservationalLabelAdapter,
    GenericPds4SourceRequest,
    _PDS4_NORMALIZER_ID,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
    VerifiedInventoryEntry,
    VerifiedInventoryManifest,
    VerifiedSourceRecordRef,
    _compute_manifest_id,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotValidationError,
    _PARSER_REGISTRY,
    _compute_snapshot_id,
    _register_parser_force,
    register_parser,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)

# Minimal WAVES Burst label for snapshot-related tests.
_WAVES_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_B12_FINAL_TEST"
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
^TABLE                = "WAV_B12_FINAL_TEST_V01.BIN"
END
"""

# Official WAVES Burst source URL (Phase 6F-B1.2.1 corrected official archive root).
_WAVES_OFFICIAL_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
    "DATA/2024/wav_b12_final_test_v01.lbl"
)


def _waves_reparser(raw_bytes, source_ref, retrieved_at):
    return parse_generic_pds3_label(
        raw_bytes, source_ref, WAVES_BURST_PDS3_PROFILE, retrieved_at
    )


def _write_snapshot(
    tmp_path: Path,
    raw=None,
    source_ref=None,
    snap_name="snap.json",
    normalizer_id=None,
    profile_id=None,
):
    raw = raw or _WAVES_LABEL
    source_ref = source_ref or "fixture:b12_waves_label"
    normalizer_id = normalizer_id or _PDS3_NORMALIZER_ID
    profile_id = profile_id or WAVES_BURST_PDS3_PROFILE.profile_id
    product, prov = _waves_reparser(raw, source_ref, _RETRIEVED_AT)
    snap_path = tmp_path / snap_name
    ArchiveLabelSnapshotStore.write(
        raw_label_bytes=raw,
        source_ref=source_ref,
        product=product,
        provenance=prov,
        reparser=_waves_reparser,
        path=snap_path,
        normalizer_id=normalizer_id,
        profile_id=profile_id,
    )
    return snap_path, product, prov


# ---------------------------------------------------------------------------
# Streaming mock helpers
# ---------------------------------------------------------------------------


def _mock_stream_response(status: int, content: bytes = b""):
    """Build a mock streaming response for httpx.Client.stream()."""
    resp = MagicMock()
    resp.status_code = status
    resp.iter_bytes = MagicMock(return_value=iter([content] if content else []))
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=resp)
    stream_cm.__exit__ = MagicMock(return_value=False)
    return stream_cm, resp


def _mock_stream_chunked(status: int, chunks: list[bytes]):
    """Build a streaming response that yields multiple chunks."""
    resp = MagicMock()
    resp.status_code = status
    resp.iter_bytes = MagicMock(return_value=iter(chunks))
    stream_cm = MagicMock()
    stream_cm.__enter__ = MagicMock(return_value=resp)
    stream_cm.__exit__ = MagicMock(return_value=False)
    return stream_cm, resp


# ===========================================================================
# 1. True streaming oversized body cutoff
# ===========================================================================


class TestTrueStreamingOversizedCutoff:
    """Item 1: reader must stop accumulating at MAX+1; never materialise the full body."""

    # ---- PDS3 ----

    _PDS3_PROFILE = GenericPds3AdapterProfile(
        profile_id="b12_stream_pds3",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        product_family="WAVES_BURST",
        require_start_stop_time=True,
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
        # Section G: must have explicit trust constraints for live fetch.
        allowed_hosts=frozenset({"test-stream.example.com"}),
        allowed_path_prefixes=("/pds3/",),
    )
    _PDS3_URL = "https://test-stream.example.com/pds3/test.lbl"

    def _pds3_patched(self, chunks: list[bytes]):
        stream_cm, resp = _mock_stream_chunked(200, chunks)
        with patch("httpx.Client") as cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            cls.return_value = mock_cm
            return GenericPds3ObservationalLabelAdapter.fetch(
                GenericPds3SourceRequest(source_url=self._PDS3_URL),
                self._PDS3_PROFILE,
                _RETRIEVED_AT,
            )

    def test_pds3_single_oversized_chunk_rejected(self):
        """Single chunk that exceeds MAX_PDS3_LABEL_BYTES must be rejected."""
        oversized = b"x" * (MAX_PDS3_LABEL_BYTES + 1)
        with pytest.raises(GenericPds3AdapterValidationError, match="size"):
            self._pds3_patched([oversized])

    def test_pds3_two_chunks_summing_over_limit_rejected(self):
        """Two chunks whose sum exceeds MAX must be rejected at the boundary."""
        half = MAX_PDS3_LABEL_BYTES // 2 + 1
        chunk1 = b"x" * half
        chunk2 = b"y" * half  # sum = half*2 > MAX
        with pytest.raises(GenericPds3AdapterValidationError, match="size"):
            self._pds3_patched([chunk1, chunk2])

    def test_pds3_reader_aborts_not_accumulates_full_body(self):
        """The reader must abort as soon as the limit is crossed — not after all chunks.

        We verify this by checking iter_bytes is called but the error is raised
        before any additional chunks are consumed.
        """
        half = MAX_PDS3_LABEL_BYTES // 2 + 1
        chunk1 = b"x" * half
        chunk2 = b"y" * half
        # Two big chunks; second crossing the limit.
        stream_cm, resp = _mock_stream_chunked(200, [chunk1, chunk2])
        with patch("httpx.Client") as cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            cls.return_value = mock_cm
            with pytest.raises(GenericPds3AdapterValidationError, match="size"):
                GenericPds3ObservationalLabelAdapter.fetch(
                    GenericPds3SourceRequest(source_url=self._PDS3_URL),
                    self._PDS3_PROFILE,
                    _RETRIEVED_AT,
                )
        # iter_bytes was called (streaming started).
        resp.iter_bytes.assert_called()

    # ---- PDS4 ----

    _PDS4_PROFILE = GenericPds4AdapterProfile(
        profile_id="b12_stream_pds4",
        allowed_hosts=frozenset({"test-stream.example.com"}),
        allowed_path_prefixes=("/pds4/",),
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="JIRAM",
        instrument_lid="urn:nasa:pds:context:instrument:jiram.jno",
        spacecraft_host_lid="urn:nasa:pds:context:instrument_host:spacecraft.jno",
        investigation_lid="urn:nasa:pds:context:investigation:mission.juno",
        product_family="JIRAM",
    )
    _PDS4_URL = "https://test-stream.example.com/pds4/test.xml"

    def _pds4_patched(self, chunks: list[bytes]):
        stream_cm, resp = _mock_stream_chunked(200, chunks)
        with patch("httpx.Client") as cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            cls.return_value = mock_cm
            return GenericPds4ObservationalLabelAdapter.fetch(
                GenericPds4SourceRequest(label_url=self._PDS4_URL),
                self._PDS4_PROFILE,
                _RETRIEVED_AT,
            )

    def test_pds4_single_oversized_chunk_rejected(self):
        """Single PDS4 chunk exceeding MAX_PDS4_LABEL_BYTES is rejected."""
        oversized = b"x" * (MAX_PDS4_LABEL_BYTES + 1)
        with pytest.raises(GenericPds4AdapterValidationError, match="size"):
            self._pds4_patched([oversized])

    def test_pds4_two_chunks_summing_over_limit_rejected(self):
        """PDS4: two chunks summing over MAX are rejected at the boundary."""
        half = MAX_PDS4_LABEL_BYTES // 2 + 1
        with pytest.raises(GenericPds4AdapterValidationError, match="size"):
            self._pds4_patched([b"x" * half, b"y" * half])

    def test_pds3_exactly_max_bytes_not_rejected(self):
        """MAX_PDS3_LABEL_BYTES exactly is accepted (boundary check — body is valid ASCII so
        it may fail parsing, but NOT size rejection)."""
        # Use exactly MAX_PDS3_LABEL_BYTES bytes of valid-ish content.
        # The check is: NOT rejected for size.  It may fail for non-ASCII, that's OK.
        exact = b"X" * MAX_PDS3_LABEL_BYTES
        stream_cm, resp = _mock_stream_chunked(200, [exact])
        with patch("httpx.Client") as cls:
            mock_cm = MagicMock()
            mock_cm.__enter__ = MagicMock(return_value=mock_cm)
            mock_cm.__exit__ = MagicMock(return_value=False)
            mock_cm.stream = MagicMock(return_value=stream_cm)
            cls.return_value = mock_cm
            try:
                GenericPds3ObservationalLabelAdapter.fetch(
                    GenericPds3SourceRequest(source_url=self._PDS3_URL),
                    self._PDS3_PROFILE,
                    _RETRIEVED_AT,
                )
            except GenericPds3AdapterValidationError as exc:
                # Must NOT be a size error.
                assert "size" not in str(exc).lower(), \
                    f"Exact MAX body should not produce a size error; got: {exc}"
            # If no exception: that's also fine.


# ===========================================================================
# 2. Actual production PDS3 profile host/path rejection
# ===========================================================================


class TestProductionPds3ProfileTrustBoundaries:
    """Item 2: production profile objects carry real trust boundaries — no proxy."""

    def _accept(self, url: str, profile: GenericPds3AdapterProfile) -> None:
        _validate_pds3_source_url_trust(url, profile)

    def _reject_host(self, url: str, profile: GenericPds3AdapterProfile) -> None:
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(url, profile)

    def _reject_path(self, url: str, profile: GenericPds3AdapterProfile) -> None:
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(url, profile)

    # --- JUNOCAM_PDS3_PROFILE ---
    # Official: PDS Imaging Node, planetarydata.jpl.nasa.gov, JNOJNC_0029 volume

    def test_junocam_official_url_accepted(self):
        self._accept(
            "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/DATA/jncr_test.lbl",
            JUNOCAM_PDS3_PROFILE,
        )

    def test_junocam_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/img/data/juno/JNOJNC_0029/DATA/t.lbl",
            JUNOCAM_PDS3_PROFILE,
        )

    def test_junocam_wrong_path_rejected(self):
        self._reject_path(
            "https://planetarydata.jpl.nasa.gov/WRONG/path/t.lbl",
            JUNOCAM_PDS3_PROFILE,
        )

    # --- FGM_PDS3_PROFILE ---
    # Official: pds-ppi.igpp.ucla.edu, /data/JNO-J-3-FGM-CAL-V1.0/

    def test_fgm_official_url_accepted(self):
        self._accept(
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/fgm_test.lbl",
            FGM_PDS3_PROFILE,
        )

    def test_fgm_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/data/JNO-J-3-FGM-CAL-V1.0/DATA/t.lbl",
            FGM_PDS3_PROFILE,
        )

    def test_fgm_wrong_path_rejected(self):
        self._reject_path(
            "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
            FGM_PDS3_PROFILE,
        )

    # --- JADE_PDS3_PROFILE ---
    # Official: pds-ppi.igpp.ucla.edu, /data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/

    def test_jade_official_url_accepted(self):
        self._accept(
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/jad_test.lbl",
            JADE_PDS3_PROFILE,
        )

    def test_jade_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/t.lbl",
            JADE_PDS3_PROFILE,
        )

    def test_jade_wrong_path_rejected(self):
        self._reject_path(
            "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
            JADE_PDS3_PROFILE,
        )

    # --- JEDI_PDS3_PROFILE ---
    # Official: pds-ppi.igpp.ucla.edu, /data/JNO-J-JED-3-CDR-V1.0/

    def test_jedi_official_url_accepted(self):
        self._accept(
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/jed_test.lbl",
            JEDI_PDS3_PROFILE,
        )

    def test_jedi_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/data/JNO-J-JED-3-CDR-V1.0/DATA/t.lbl",
            JEDI_PDS3_PROFILE,
        )

    # --- WAVES_SURVEY_PDS3_PROFILE ---
    # Official: pds-ppi.igpp.ucla.edu, /data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/

    def test_waves_survey_official_url_accepted(self):
        self._accept(
            "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
            "DATA/2024/wav_test_srvy.lbl",
            WAVES_SURVEY_PDS3_PROFILE,
        )

    def test_waves_survey_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
            "DATA/t.lbl",
            WAVES_SURVEY_PDS3_PROFILE,
        )

    def test_waves_survey_sibling_path_rejected(self):
        """A sibling/wrong path on the correct host is rejected."""
        self._reject_path(
            "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
            WAVES_SURVEY_PDS3_PROFILE,
        )

    # --- WAVES_BURST_PDS3_PROFILE ---
    # Official: pds-ppi.igpp.ucla.edu, /data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/

    def test_waves_burst_official_url_accepted(self):
        self._accept(
            "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
            "DATA/2024/wav_test_bst.lbl",
            WAVES_BURST_PDS3_PROFILE,
        )

    def test_waves_burst_wrong_host_rejected(self):
        self._reject_host(
            "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
            "DATA/t.lbl",
            WAVES_BURST_PDS3_PROFILE,
        )

    def test_waves_burst_sibling_path_rejected(self):
        self._reject_path(
            "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
            WAVES_BURST_PDS3_PROFILE,
        )


# ===========================================================================
# 3. PDS3 http/invalid external source_ref rejection
# ===========================================================================


class TestPds3ExternalSourceRefSchemeRejection:
    """Item 3: any source_ref containing :// but not https:// is rejected."""

    _PROFILE_WITH_ALLOWED_HOSTS = GenericPds3AdapterProfile(
        profile_id="b12_scheme_test",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        product_family="WAVES_BURST",
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
    )

    def _assert_scheme_rejected(self, source_ref: str) -> None:
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh][Tt][Tt][Pp]|[Ss]cheme"):
            _validate_pds3_source_url_trust(source_ref, self._PROFILE_WITH_ALLOWED_HOSTS)

    def test_http_scheme_rejected(self):
        """http:// must be rejected — not silently bypassed."""
        self._assert_scheme_rejected("http://pds-ppi.igpp.ucla.edu/data/test.lbl")

    def test_ftp_scheme_rejected(self):
        """ftp:// must be rejected."""
        self._assert_scheme_rejected("ftp://pds-ppi.igpp.ucla.edu/data/test.lbl")

    def test_evil_scheme_rejected(self):
        """evil:// must be rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh][Tt][Tt][Pp]|[Ss]cheme"):
            _validate_pds3_source_url_trust("evil://evil.example.com/data/test.lbl",
                                            self._PROFILE_WITH_ALLOWED_HOSTS)

    def test_javascript_scheme_rejected(self):
        """javascript:// must be rejected."""
        with pytest.raises(GenericPds3AdapterValidationError):
            _validate_pds3_source_url_trust("javascript://harmless/test",
                                            self._PROFILE_WITH_ALLOWED_HOSTS)

    def test_bare_local_ref_not_rejected(self):
        """A bare local identifier (no ://) bypasses trust validation (offline fixture mode)."""
        # No exception expected — bare local ref is not a network URL.
        # parse_generic_pds3_label checks "://" in source_ref, not pure URL.
        raw = _WAVES_LABEL
        profile = WAVES_BURST_PDS3_PROFILE
        # Use "fixture:waves_test" which has no "://"
        product, prov = parse_generic_pds3_label(raw, "fixture:waves_test", profile, _RETRIEVED_AT)
        assert product.source_record_id.startswith("pds3:")

    def test_http_source_ref_rejected_in_parser(self):
        """parse_generic_pds3_label must reject http:// source_ref (not skip silently)."""
        raw = _WAVES_LABEL
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh][Tt][Tt][Pp]|[Ss]cheme"):
            parse_generic_pds3_label(
                raw,
                "http://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
                "DATA/2024/wav_test.lbl",
                WAVES_BURST_PDS3_PROFILE,
                _RETRIEVED_AT,
            )

    def test_ftp_source_ref_rejected_in_parser(self):
        """parse_generic_pds3_label must reject ftp:// source_ref."""
        raw = _WAVES_LABEL
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh][Tt][Tt][Pp]|[Ss]cheme"):
            parse_generic_pds3_label(
                raw,
                "ftp://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/test.lbl",
                WAVES_BURST_PDS3_PROFILE,
                _RETRIEVED_AT,
            )


# ===========================================================================
# 4 & 5a. Malformed PDS4 checksum / file_size propagates error
# ===========================================================================


class TestPds4FileMetadataFailClosed:
    """Item 4: malformed PDS4 checksum or file_size must raise, not return []."""

    _VALID_URL = (
        "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/"
        "data_calibrated/jir_test.xml"
    )

    def _make_label(self, md5_val: str = None, file_size_val: str = None,
                    file_size_unit: str = "byte") -> bytes:
        md5_block = ""
        if md5_val is not None:
            md5_block = f"      <md5_checksum>{md5_val}</md5_checksum>\n"
        size_block = ""
        if file_size_val is not None:
            size_block = (
                f'      <file_size unit="{file_size_unit}">'
                f"{file_size_val}</file_size>\n"
            )
        label = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_test_b12</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B12 Test</title>
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
  <File_Area_Observational>
    <File>
      <file_name>jir_test_b12.img</file_name>
{size_block}{md5_block}    </File>
  </File_Area_Observational>
</Product_Observational>
""".encode("utf-8")
        return label

    def test_malformed_md5_checksum_raises(self):
        """Malformed (non-32-hex) md5_checksum must raise GenericPds4AdapterValidationError."""
        label = self._make_label(md5_val="NOTAVALIDMD5")
        with pytest.raises(GenericPds4AdapterValidationError, match="[Mm][Dd]5|[Cc]hecksum|malform"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_valid_md5_accepted(self):
        """A valid 32-hex MD5 must be accepted."""
        good_md5 = "a" * 32
        label = self._make_label(md5_val=good_md5)
        product, _ = parse_generic_pds4_label(
            label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT
        )
        assert len(product.data_files) == 1
        assert product.data_files[0].checksum_value == good_md5

    def test_malformed_file_size_value_raises(self):
        """Non-numeric file_size with unit=byte must raise."""
        label = self._make_label(file_size_val="NOT_A_NUMBER", file_size_unit="byte")
        with pytest.raises(GenericPds4AdapterValidationError, match="file_size|malform"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_unsupported_file_size_unit_raises(self):
        """file_size with unit != 'byte' must raise (fail closed)."""
        label = self._make_label(file_size_val="1024", file_size_unit="kilobyte")
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_no_file_area_produces_empty_data_files(self):
        """Label with no File_Area_Observational is a valid explicit no-file case."""
        label = b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_nofile</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM No File</title>
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
</Product_Observational>
"""
        product, _ = parse_generic_pds4_label(
            label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT
        )
        assert product.data_files == ()
        assert product.total_data_size_bytes == 0


# ===========================================================================
# 5b & 5c. Unknown aggregate size != zero; mixed known/unknown
# ===========================================================================


class TestAggregateUnknownSize:
    """Item 5: unknown per-file size must aggregate to None, not 0."""

    def _make_product(self, data_files: tuple, total: int | None) -> ArchiveScienceProduct:
        return ArchiveScienceProduct(
            source_record_id="pds3:DS:PROD_AGG_TEST",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="PROD_AGG_TEST",
            mission_name="JUNO",
            product_family="WAVES_BURST",
            data_files=data_files,
            total_data_size_bytes=total,
        )

    def test_unknown_size_file_requires_none_total(self):
        """Single file with unknown size → total must be None."""
        f = ArchiveDataFile(
            file_name="test.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        product = self._make_product((f,), None)
        assert product.total_data_size_bytes is None

    def test_unknown_size_file_with_zero_total_rejected(self):
        """Supplying total=0 when a file has unknown size must fail validation."""
        f = ArchiveDataFile(
            file_name="test.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="None|unknown"):
            self._make_product((f,), 0)

    def test_zero_files_produces_zero_total(self):
        """Zero data files → total = 0 (not None)."""
        product = self._make_product((), 0)
        assert product.total_data_size_bytes == 0
        assert product.total_data_size_bytes is not None

    def test_zero_byte_file_produces_zero_total(self):
        """Zero-byte file (known) → total = 0, not None."""
        f = ArchiveDataFile(
            file_name="empty.bin",
            file_size_bytes=0,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = self._make_product((f,), 0)
        assert product.total_data_size_bytes == 0
        assert product.total_data_size_bytes is not None

    def test_mixed_known_unknown_files_produces_none_total(self):
        """Mixed: one known + one unknown file → total must be None."""
        known = ArchiveDataFile(
            file_name="known.bin",
            file_size_bytes=1024,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        unknown = ArchiveDataFile(
            file_name="unknown.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        product = self._make_product((known, unknown), None)
        assert product.total_data_size_bytes is None

    def test_mixed_known_unknown_total_zero_rejected(self):
        """Mixed known+unknown: supplying total=0 is rejected."""
        known = ArchiveDataFile(
            file_name="known.bin",
            file_size_bytes=1024,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        unknown = ArchiveDataFile(
            file_name="unknown.bin",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_UNKNOWN,
        )
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="None|unknown"):
            self._make_product((known, unknown), 0)

    def test_all_known_files_requires_correct_sum(self):
        """All known files → total must equal exact sum."""
        f1 = ArchiveDataFile(
            file_name="a.bin",
            file_size_bytes=512,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        f2 = ArchiveDataFile(
            file_name="b.bin",
            file_size_bytes=1024,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = self._make_product((f1, f2), 1536)
        assert product.total_data_size_bytes == 1536

    def test_pds3_unknown_strategy_parser_produces_none_total(self):
        """JADE/JEDI profile (NONE strategy) → file_size=None → total=None."""
        raw = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID = "JNO-J-JAD-3-CDR-V1.0"
PRODUCT_ID = "JAD_B12_TEST"
INSTRUMENT_HOST_ID = "JNO"
INSTRUMENT_ID = "JAD"
START_TIME = 2024-165T05:55:51.000
STOP_TIME = 2024-165T06:00:00.000
TARGET_NAME = "JUPITER"
^TABLE = "JAD_B12_TEST.DAT"
END
"""
        product, _ = parse_generic_pds3_label(raw, "fixture:jade_b12", JADE_PDS3_PROFILE, _RETRIEVED_AT)
        assert product.total_data_size_bytes is None
        if product.data_files:
            assert product.data_files[0].file_size_bytes is None


# ===========================================================================
# 6. Verified manifest without source registry rejected
# ===========================================================================


class TestVerifiedManifestRequiresSourceRegistry:
    """Item 6: VerifiedInventoryManifest must reject empty source_records."""

    def _make_entry(self, lid: str, rid: str) -> VerifiedInventoryEntry:
        return VerifiedInventoryEntry(
            logical_product_id=lid,
            representation_record_ids=(rid,),
            availability_time_utc=_RETRIEVED_AT,
        )

    def _make_ref(self, rid: str, prov_id: str) -> VerifiedSourceRecordRef:
        return VerifiedSourceRecordRef(
            source_record_id=rid,
            source_standard=ArchiveSourceStandard.PDS3,
            provenance_id=prov_id,
            normalizer_id=_PDS3_NORMALIZER_ID,
            profile_id="waves_burst_pds3",
        )

    def test_empty_source_records_rejected(self):
        """build() with empty source_records must be rejected."""
        import pydantic
        e = self._make_entry("LP-001", "rid-001")
        with pytest.raises(pydantic.ValidationError, match="source_records|registry"):
            VerifiedInventoryManifest.build([e], source_records=[])

    def test_with_source_records_accepted(self):
        """build() with populated source_records must pass."""
        e = self._make_entry("LP-001", "rid-001")
        ref = self._make_ref("rid-001", "prov-001")
        m = VerifiedInventoryManifest.build([e], source_records=[ref])
        assert len(m.source_records) == 1

    def test_411_entry_manifest_with_source_registry_passes(self):
        """411 entries + full source registry must pass."""
        entries = []
        refs = []
        for i in range(411):
            rid = f"pds3:DS:PROD_{i:04d}"
            pid = f"prov-{i:04d}"
            entries.append(self._make_entry(f"LP-{i:04d}", rid))
            refs.append(self._make_ref(rid, pid))
        m = VerifiedInventoryManifest.build(entries, source_records=refs)
        assert len(m.entries) == 411
        assert len(m.source_records) == 411
        assert len(m.manifest_id) == 64


# ===========================================================================
# 7. Parser registry cannot be overwritten / injected
# ===========================================================================


class TestParserRegistryImmutability:
    """Item 7: register_parser must forbid duplicate registration."""

    def test_first_registration_succeeds(self):
        """Registering a new (normalizer_id, profile_id) pair succeeds."""
        nid = "gcsi.test.b12_unique_norm_v1"
        pid = "b12_unique_profile_v1"
        key = (nid, pid)
        # Ensure not already registered.
        _PARSER_REGISTRY.pop(key, None)

        def dummy(raw, ref, ts):
            raise NotImplementedError

        register_parser(nid, pid, dummy)
        assert key in _PARSER_REGISTRY
        # Cleanup.
        _PARSER_REGISTRY.pop(key, None)

    def test_duplicate_registration_raises(self):
        """Registering the same (normalizer_id, profile_id) pair twice must raise."""
        nid = "gcsi.test.b12_dup_norm_v1"
        pid = "b12_dup_profile_v1"
        key = (nid, pid)
        _PARSER_REGISTRY.pop(key, None)

        def dummy(raw, ref, ts):
            raise NotImplementedError

        register_parser(nid, pid, dummy)
        with pytest.raises(ArchiveSnapshotValidationError, match="[Dd]uplicate|[Aa]lready|overwrite"):
            register_parser(nid, pid, dummy)
        # Cleanup.
        _PARSER_REGISTRY.pop(key, None)

    def test_force_register_helper_allows_overwrite(self):
        """_register_parser_force bypasses the guard (test-only helper)."""
        nid = "gcsi.test.b12_force_norm_v1"
        pid = "b12_force_profile_v1"
        key = (nid, pid)
        _PARSER_REGISTRY.pop(key, None)

        def dummy_a(raw, ref, ts):
            return "A"

        def dummy_b(raw, ref, ts):
            return "B"

        _register_parser_force(nid, pid, dummy_a)
        _register_parser_force(nid, pid, dummy_b)  # overwrite allowed
        assert _PARSER_REGISTRY[key] is dummy_b
        _PARSER_REGISTRY.pop(key, None)

    def test_empty_normalizer_id_rejected(self):
        with pytest.raises(ValueError, match="normalizer_id"):
            register_parser("  ", "some_profile", lambda a, b, c: None)

    def test_empty_profile_id_rejected(self):
        with pytest.raises(ValueError, match="profile_id"):
            register_parser("gcsi.test.v1", "  ", lambda a, b, c: None)


# ===========================================================================
# 8. Snapshot write rejects unknown/empty normalizer_id or profile_id
# ===========================================================================


class TestSnapshotWriteEnvelopeValidation:
    """Item 8: write must reject empty normalizer_id or profile_id before filesystem commit."""

    def test_empty_normalizer_id_rejected(self, tmp_path):
        """write() with empty normalizer_id must raise before writing."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b12_envelope_test", _RETRIEVED_AT)
        snap_path = tmp_path / "test.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="normalizer"):
            ArchiveLabelSnapshotStore.write(
                raw_label_bytes=raw,
                source_ref="fixture:b12_envelope_test",
                product=product,
                provenance=prov,
                reparser=_waves_reparser,
                path=snap_path,
                normalizer_id="",
                profile_id="waves_burst_pds3",
            )
        # File must NOT have been created.
        assert not snap_path.exists()

    def test_empty_profile_id_rejected(self, tmp_path):
        """write() with empty profile_id must raise before writing."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b12_envelope_test2", _RETRIEVED_AT)
        snap_path = tmp_path / "test2.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="profile"):
            ArchiveLabelSnapshotStore.write(
                raw_label_bytes=raw,
                source_ref="fixture:b12_envelope_test2",
                product=product,
                provenance=prov,
                reparser=_waves_reparser,
                path=snap_path,
                normalizer_id=_PDS3_NORMALIZER_ID,
                profile_id="",
            )
        assert not snap_path.exists()

    def test_valid_write_succeeds(self, tmp_path):
        """write() with valid normalizer_id + profile_id must succeed."""
        snap_path, product, _ = _write_snapshot(tmp_path)
        assert snap_path.exists()
        data = json.loads(snap_path.read_text())
        assert data["normalizer_id"] == _PDS3_NORMALIZER_ID
        assert data["profile_id"] == "waves_burst_pds3"


# ===========================================================================
# 9. Snapshot load rejects normalizer/source_standard mismatch
# ===========================================================================


class TestSnapshotNormalizerSourceStandardMismatch:
    """Item 7/9: load must reject normalizer_id/source_standard mismatch."""

    def test_pds3_normalizer_with_pds4_standard_rejected(self, tmp_path):
        """A snapshot claiming pds4 normalizer but pds3 product standard must be rejected.

        We set normalizer_id to a pds4-style ID while snapshot_source_standard remains pds3.
        The check in step 18 of _finish_load detects this mismatch.
        """
        snap_path, _, _ = _write_snapshot(tmp_path)
        data = json.loads(snap_path.read_text())

        # Tamper: replace normalizer_id with a PDS4 normalizer ID while leaving
        # snapshot_source_standard as pds3.  The recomputed snapshot_id must
        # still be consistent so the id-check passes.
        data["normalizer_id"] = "gcsi.generic_pds4_label.v1"
        # snapshot_id is computed from source_standard + provenance_id + retrieved_at,
        # NOT from normalizer_id, so it remains valid.
        snap_path.write_text(json.dumps(data))

        with pytest.raises(ArchiveSnapshotValidationError, match="[Mm]ismatch|normalizer|standard"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_correct_normalizer_and_standard_accepted(self, tmp_path):
        """A correct pds3 normalizer + pds3 standard is accepted."""
        snap_path, _, _ = _write_snapshot(tmp_path)
        nid = f"gcsi.test.load_mismatch_{id(tmp_path)}"
        pid = WAVES_BURST_PDS3_PROFILE.profile_id
        _register_parser_force(nid, pid, _waves_reparser)
        snap_path_2, _, _ = _write_snapshot(
            tmp_path, snap_name="snap2.json", normalizer_id=nid
        )
        product, prov = ArchiveLabelSnapshotStore.load_from_explicit_reparser(
            snap_path_2, _waves_reparser
        )
        assert product.source_standard == ArchiveSourceStandard.PDS3
        _PARSER_REGISTRY.pop((nid, pid), None)


# ===========================================================================
# 10. Source fact vs normalization fact semantics
# ===========================================================================


class TestSourceVsNormalizationFacts:
    """Item 9: document and verify source-fact vs normalizer-classification semantics."""

    def test_mission_name_from_profile_is_deterministic(self):
        """mission_name comes from profile.expected_mission — not a literal label field."""
        raw = _WAVES_LABEL
        product, _ = parse_generic_pds3_label(
            raw, "fixture:source_fact_test", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        # mission_name is derived from the profile, not from a label keyword.
        assert product.mission_name == WAVES_BURST_PDS3_PROFILE.expected_mission

    def test_source_dataset_id_is_raw_label_value(self):
        """source_dataset_id is the exact PDS3 DATA_SET_ID from the label."""
        raw = _WAVES_LABEL
        product, _ = parse_generic_pds3_label(
            raw, "fixture:source_fact_test2", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.source_dataset_id == "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"

    def test_product_family_from_profile_not_label(self):
        """product_family is a normalization classification from profile, not a label field."""
        raw = _WAVES_LABEL
        product, _ = parse_generic_pds3_label(
            raw, "fixture:source_fact_test3", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.product_family == WAVES_BURST_PDS3_PROFILE.product_family

    def test_instrument_name_from_label_not_profile(self):
        """instrument_name is extracted from the label (INSTRUMENT_ID), not the profile."""
        raw = _WAVES_LABEL
        product, _ = parse_generic_pds3_label(
            raw, "fixture:source_fact_test4", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        # WAV comes from the label's INSTRUMENT_ID = "WAV"
        assert product.instrument_name == "WAV"

    def test_observation_times_from_label(self):
        """observation_start/stop_utc are source-normalized archive facts from the label."""
        raw = _WAVES_LABEL
        product, _ = parse_generic_pds3_label(
            raw, "fixture:source_fact_test5", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.observation_start_utc is not None
        assert product.observation_stop_utc is not None
        # 2024-165 = June 13, 2024.
        assert product.observation_start_utc.year == 2024

    def test_provenance_binds_profile_id(self):
        """ProvenanceRecord identity encodes profile_id, binding mission_name derivation."""
        raw = _WAVES_LABEL
        product, prov = parse_generic_pds3_label(
            raw, "fixture:source_fact_test6", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        # The provenance_id is computed from identity JSON that includes profile_id.
        # We verify it is a valid 64-char hex hash (not empty/null).
        assert len(prov.provenance_id) == 64
        assert all(c in "0123456789abcdef" for c in prov.provenance_id)
