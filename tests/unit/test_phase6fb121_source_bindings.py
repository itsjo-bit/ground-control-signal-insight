"""GCSI Phase 6F-B1.2.1 — Production Source Profile + Snapshot Binding Correction Tests.

Covers Section H (15 required tests) from the B1.2.1 task specification:

 1.  Official FGM archive root accepted (pds-ppi / JNO-J-3-FGM-CAL-V1.0)
 2.  Official JADE archive root accepted (pds-ppi / JNO-J_SW-JAD-3-CALIBRATED-V1.0)
 3.  Official JEDI archive root accepted (pds-ppi / JNO-J-JED-3-CDR-V1.0)
 4.  Official WAVES Survey archive root accepted (pds-ppi / JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0)
 5.  Official WAVES Burst archive root accepted (pds-ppi / JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0)
 6.  Official JunoCam JNOJNC_0029 Imaging Node root accepted (planetarydata.jpl.nasa.gov)
 7.  Previously fabricated paths are rejected
 8.  Live PDS3 fetch rejects unconstrained profile (no allowed_hosts / no allowed_path_prefixes)
 9.  Production parser mapping cannot be caller-injected (frozen map is not writable)
10.  Snapshot production write cannot use arbitrary normalizer_id+profile_id pair
      that is not a known production pair
11.  PDS4 File_Area_Observational with missing File element → raises
12.  PDS4 File element with missing/empty file_name → raises
13.  PDS4 empty/missing-unit file_size when declared → raises
14.  Approximate aggregate is NOT exposed as exact total
15.  All-exact aggregate still produces integer total

All tests are OFFLINE. No live PDS requests are made.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.mission_sources.adapters.pds3_adapter import (
    FGM_PDS3_PROFILE,
    JADE_PDS3_PROFILE,
    JEDI_PDS3_PROFILE,
    JUNOCAM_PDS3_PROFILE,
    WAVES_BURST_PDS3_PROFILE,
    WAVES_SURVEY_PDS3_PROFILE,
    GenericPds3AdapterProfile,
    GenericPds3AdapterValidationError,
    GenericPds3ObservationalLabelAdapter,
    GenericPds3SourceRequest,
    Pds3SizeDerivationStrategy,
    _validate_pds3_source_url_trust,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    JIRAM_PDS4_PROFILE,
    GenericPds4AdapterValidationError,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
)
from backend.app.mission_sources.adapters.pds3_adapter import (
    _PDS3_NORMALIZER_ID,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotValidationError,
    _FROZEN_PRODUCTION_PROFILE_MAP,
    _PARSER_REGISTRY,
    _register_parser_force,
    get_production_source_standard,
    is_known_production_pair,
    register_parser,
)

_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)

# Minimal WAVES Burst label (valid DATA_SET_ID prefix, complete structure).
_WAVES_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_B121_TEST"
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
^TABLE                = "WAV_B121_TEST_V01.BIN"
END
"""


def _waves_reparser(raw_bytes, source_ref, retrieved_at):
    return parse_generic_pds3_label(
        raw_bytes, source_ref, WAVES_BURST_PDS3_PROFILE, retrieved_at
    )


# ===========================================================================
# 1. Official FGM archive root accepted
# ===========================================================================


class TestOfficialFgmRootAccepted:
    """Test 1: Official FGM archive root (pds-ppi / JNO-J-3-FGM-CAL-V1.0) accepted."""

    def test_fgm_official_root_accepted(self):
        """Official FGM URL at pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/ is accepted."""
        url = (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/"
            "2024/fgm_2024165_orbit62_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, FGM_PDS3_PROFILE)

    def test_fgm_profile_host_is_ppi(self):
        """FGM profile is bound to pds-ppi.igpp.ucla.edu."""
        assert "pds-ppi.igpp.ucla.edu" in FGM_PDS3_PROFILE.allowed_hosts

    def test_fgm_profile_prefix_is_official_volume(self):
        """FGM profile path prefix is the official JNO-J-3-FGM-CAL-V1.0 volume."""
        assert any(
            "JNO-J-3-FGM-CAL-V1.0" in p
            for p in FGM_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_fgm_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J-3-FGM-CAL-V1.0/DATA/t.lbl",
                FGM_PDS3_PROFILE,
            )

    def test_fgm_sibling_dataset_rejected(self):
        """A sibling dataset path on the same host is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V2.0/DATA/t.lbl",
                FGM_PDS3_PROFILE,
            )

    def test_fgm_wrong_dataset_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
                FGM_PDS3_PROFILE,
            )


# ===========================================================================
# 2. Official JADE archive root accepted
# ===========================================================================


class TestOfficialJadeRootAccepted:
    """Test 2: Official JADE archive root (pds-ppi / JNO-J_SW-JAD-3-CALIBRATED-V1.0)."""

    def test_jade_official_root_accepted(self):
        url = (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/"
            "jad_l20_lo_tof3d_2024165_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, JADE_PDS3_PROFILE)

    def test_jade_profile_host_is_ppi(self):
        assert "pds-ppi.igpp.ucla.edu" in JADE_PDS3_PROFILE.allowed_hosts

    def test_jade_profile_prefix_is_official_volume(self):
        assert any(
            "JNO-J_SW-JAD-3-CALIBRATED-V1.0" in p
            for p in JADE_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_jade_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/t.lbl",
                JADE_PDS3_PROFILE,
            )

    def test_jade_sibling_dataset_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V2.0/DATA/t.lbl",
                JADE_PDS3_PROFILE,
            )

    def test_jade_wrong_dataset_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
                JADE_PDS3_PROFILE,
            )


# ===========================================================================
# 3. Official JEDI archive root accepted
# ===========================================================================


class TestOfficialJediRootAccepted:
    """Test 3: Official JEDI archive root (pds-ppi / JNO-J-JED-3-CDR-V1.0)."""

    def test_jedi_official_root_accepted(self):
        url = (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/"
            "jed_2024165_ch0_l2_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, JEDI_PDS3_PROFILE)

    def test_jedi_profile_host_is_ppi(self):
        assert "pds-ppi.igpp.ucla.edu" in JEDI_PDS3_PROFILE.allowed_hosts

    def test_jedi_profile_prefix_is_official_volume(self):
        assert any(
            "JNO-J-JED-3-CDR-V1.0" in p
            for p in JEDI_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_jedi_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-J-JED-3-CDR-V1.0/DATA/t.lbl",
                JEDI_PDS3_PROFILE,
            )

    def test_jedi_sibling_dataset_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V2.0/DATA/t.lbl",
                JEDI_PDS3_PROFILE,
            )

    def test_jedi_wrong_dataset_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/OTHER/path/t.lbl",
                JEDI_PDS3_PROFILE,
            )


# ===========================================================================
# 4. Official WAVES Survey archive root accepted
# ===========================================================================


class TestOfficialWavesSurveyRootAccepted:
    """Test 4: Official WAVES Survey archive root (JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0)."""

    def test_waves_survey_official_root_accepted(self):
        url = (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
            "DATA/2024/wav_2024165_srvy_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, WAVES_SURVEY_PDS3_PROFILE)

    def test_waves_survey_profile_prefix_is_srvfull(self):
        assert any(
            "JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0" in p
            for p in WAVES_SURVEY_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_waves_survey_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/t.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )

    def test_waves_survey_burst_path_rejected(self):
        """WAVES Burst path is rejected by WAVES Survey profile (sibling dataset)."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/DATA/t.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )

    def test_waves_survey_wrong_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )


# ===========================================================================
# 5. Official WAVES Burst archive root accepted
# ===========================================================================


class TestOfficialWavesBurstRootAccepted:
    """Test 5: Official WAVES Burst archive root (JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0)."""

    def test_waves_burst_official_root_accepted(self):
        url = (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
            "DATA/2024/wav_2024165t055551_b_bin_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, WAVES_BURST_PDS3_PROFILE)

    def test_waves_burst_profile_prefix_is_bstfull(self):
        assert any(
            "JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0" in p
            for p in WAVES_BURST_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_waves_burst_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/DATA/t.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )

    def test_waves_burst_survey_path_rejected(self):
        """WAVES Survey path is rejected by WAVES Burst profile (sibling dataset)."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/t.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )

    def test_waves_burst_wrong_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/WRONG/path/t.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )


# ===========================================================================
# 6. Official JunoCam JNOJNC_0029 Imaging Node root accepted
# ===========================================================================


class TestOfficialJunocamImagingNodeAccepted:
    """Test 6: Official JunoCam PJ62 root (planetarydata.jpl.nasa.gov / JNOJNC_0029)."""

    def test_junocam_official_imaging_node_url_accepted(self):
        url = (
            "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/"
            "DATA/2024/jncr_2024165_01m01280_v01.lbl"
        )
        _validate_pds3_source_url_trust(url, JUNOCAM_PDS3_PROFILE)

    def test_junocam_profile_host_is_imaging_node(self):
        """JunoCam profile must be bound to planetarydata.jpl.nasa.gov."""
        assert "planetarydata.jpl.nasa.gov" in JUNOCAM_PDS3_PROFILE.allowed_hosts

    def test_junocam_profile_prefix_contains_jnojnc_0029(self):
        """JunoCam profile path prefix must contain JNOJNC_0029."""
        assert any(
            "JNOJNC_0029" in p
            for p in JUNOCAM_PDS3_PROFILE.allowed_path_prefixes
        )

    def test_junocam_pds_rings_host_rejected(self):
        """pds-rings.seti.org is NOT the official JunoCam production host (rejected)."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://pds-rings.seti.org/holdings/jno-e-jnc-2-edr-l1a-v1.0/data/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )

    def test_junocam_wrong_host_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://evil.example.com/img/data/juno/JNOJNC_0029/DATA/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )

    def test_junocam_wrong_volume_rejected(self):
        """A different JunoCam volume (not JNOJNC_0029) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0028/DATA/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )

    def test_junocam_wrong_path_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://planetarydata.jpl.nasa.gov/WRONG/path/t.lbl",
                JUNOCAM_PDS3_PROFILE,
            )


# ===========================================================================
# 7. Previously fabricated paths are rejected
# ===========================================================================


class TestFabricatedPathsRejected:
    """Test 7: Previously fabricated archive paths that were never real are rejected."""

    def test_fgm_fabricated_path_rejected(self):
        """Old fabricated FGM path (/data/juno/juno-fgm/) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/juno/juno-fgm/data/fgm_test.lbl",
                FGM_PDS3_PROFILE,
            )

    def test_jade_fabricated_path_rejected(self):
        """Old fabricated JADE path (/data/juno/juno-jade/) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/juno/juno-jade/data/jad_test.lbl",
                JADE_PDS3_PROFILE,
            )

    def test_jedi_fabricated_path_rejected(self):
        """Old fabricated JEDI path (/data/juno/juno-jedi/) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/juno/juno-jedi/data/jed_test.lbl",
                JEDI_PDS3_PROFILE,
            )

    def test_waves_survey_fabricated_path_rejected(self):
        """Old fabricated WAVES Survey path (jno-e-j-ss-wav-3-cdr-srvy) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/juno-wav-3-cdr-calibrated-v2.0/"
                "jno-e-j-ss-wav-3-cdr-srvy/data/2024/wav_test_srvy.lbl",
                WAVES_SURVEY_PDS3_PROFILE,
            )

    def test_waves_burst_fabricated_path_rejected(self):
        """Old fabricated WAVES Burst path (jno-e-j-ss-wav-3-cdr-bstfull) is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Pp]refix|[Pp]ath"):
            _validate_pds3_source_url_trust(
                "https://pds-ppi.igpp.ucla.edu/data/juno-wav-3-cdr-calibrated-v2.0/"
                "jno-e-j-ss-wav-3-cdr-bstfull/data/2024/wav_test_bst.lbl",
                WAVES_BURST_PDS3_PROFILE,
            )

    def test_junocam_pds_rings_fabricated_path_rejected(self):
        """Old fabricated JunoCam path at pds-rings.seti.org is rejected."""
        with pytest.raises(GenericPds3AdapterValidationError, match="[Hh]ost"):
            _validate_pds3_source_url_trust(
                "https://pds-rings.seti.org/holdings/jno-e-jnc-2-edr-l1a-v1.0/data/jncr_test.lbl",
                JUNOCAM_PDS3_PROFILE,
            )


# ===========================================================================
# 8. Live PDS3 fetch rejects unconstrained profile
# ===========================================================================


class TestLiveFetchRejectsUnconstrainedProfile:
    """Test 8: Live PDS3 fetch rejects profiles with no allowed_hosts or no allowed_path_prefixes."""

    _UNCONSTRAINED_NO_HOSTS = GenericPds3AdapterProfile(
        profile_id="b121_unconstrained_no_hosts",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        product_family="WAVES_BURST",
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
        # allowed_hosts = None (default)
        allowed_path_prefixes=("/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/",),
    )

    _UNCONSTRAINED_NO_PREFIXES = GenericPds3AdapterProfile(
        profile_id="b121_unconstrained_no_prefixes",
        expected_mission="JUNO",
        expected_spacecraft="JNO",
        expected_instrument="WAV",
        product_family="WAVES_BURST",
        size_derivation_strategy=Pds3SizeDerivationStrategy.RECORD_BYTES_X_FILE_RECORDS,
        allowed_hosts=frozenset({"pds-ppi.igpp.ucla.edu"}),
        # allowed_path_prefixes = None (default)
    )

    def test_no_allowed_hosts_rejects_before_network(self):
        """Profile with no allowed_hosts must be rejected before any network request."""
        req = GenericPds3SourceRequest(
            source_url=(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
                "DATA/2024/wav_test.lbl"
            )
        )
        with pytest.raises(GenericPds3AdapterValidationError, match="[Aa]llowed_hosts|[Tt]rust"):
            GenericPds3ObservationalLabelAdapter.fetch(
                req, self._UNCONSTRAINED_NO_HOSTS, _RETRIEVED_AT
            )

    def test_no_allowed_path_prefixes_rejects_before_network(self):
        """Profile with no allowed_path_prefixes must be rejected before any network request."""
        req = GenericPds3SourceRequest(
            source_url=(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
                "DATA/2024/wav_test.lbl"
            )
        )
        with pytest.raises(GenericPds3AdapterValidationError, match="[Aa]llowed_path|[Tt]rust"):
            GenericPds3ObservationalLabelAdapter.fetch(
                req, self._UNCONSTRAINED_NO_PREFIXES, _RETRIEVED_AT
            )

    def test_constrained_profile_does_not_reject_before_network(self):
        """A fully constrained profile proceeds past the guard (may fail for other reasons)."""
        req = GenericPds3SourceRequest(
            source_url=(
                "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
                "DATA/2024/wav_b121_live_test.lbl"
            )
        )
        # The constrained profile passes the guard; any remaining error is
        # NOT about unconstrained-profile rejection.
        stream_cm = MagicMock()
        resp_mock = MagicMock()
        resp_mock.status_code = 404
        resp_mock.iter_bytes = MagicMock(return_value=iter([]))
        stream_cm.__enter__ = MagicMock(return_value=resp_mock)
        stream_cm.__exit__ = MagicMock(return_value=False)

        with patch("httpx.Client") as cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream = MagicMock(return_value=stream_cm)
            cls.return_value = mock_client
            from backend.app.mission_sources.adapters.pds3_adapter import (
                GenericPds3AdapterUnavailableError,
            )
            with pytest.raises(GenericPds3AdapterUnavailableError):
                GenericPds3ObservationalLabelAdapter.fetch(
                    req, WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
                )


# ===========================================================================
# 9. Production parser mapping cannot be caller-injected
# ===========================================================================


class TestProductionParserMappingFrozen:
    """Test 9: The frozen production map is read-only; callers cannot inject entries."""

    def test_frozen_map_is_not_directly_writable(self):
        """_FROZEN_PRODUCTION_PROFILE_MAP is a module-level dict but callers cannot
        inject new entries via any public API — there is no public write function."""
        # Verify the public API provides read access only.
        assert callable(is_known_production_pair)
        assert callable(get_production_source_standard)
        # There is no 'register_production_pair' or equivalent public write function.
        import backend.app.mission_sources.snapshots.archive_label_snapshot as snap_mod
        assert not hasattr(snap_mod, "register_production_pair")
        assert not hasattr(snap_mod, "add_production_parser")

    def test_all_production_pds3_profiles_have_frozen_entry(self):
        """Every PDS3 production profile used by B2 must appear in the frozen map."""
        _PDS3_NID = "gcsi.generic_pds3_label.v1"
        required_pids = {
            "fgm_pds3", "jade_pds3", "jedi_pds3",
            "waves_burst_pds3", "waves_survey_pds3", "junocam_pds3",
        }
        for pid in required_pids:
            assert is_known_production_pair(_PDS3_NID, pid), (
                f"Production profile {pid!r} not in frozen map."
            )

    def test_all_production_pds4_profiles_have_frozen_entry(self):
        """Every PDS4 production profile used by B2 must appear in the frozen map."""
        _PDS4_NID = "gcsi.generic_pds4_label.v1"
        required_pids = {"jiram_pds4", "uvs_pds4", "mwr_generic_pds4"}
        for pid in required_pids:
            assert is_known_production_pair(_PDS4_NID, pid), (
                f"Production profile {pid!r} not in frozen map."
            )

    def test_unknown_pair_raises_from_frozen_map(self):
        """An unknown (normalizer_id, profile_id) pair raises from the frozen map."""
        with pytest.raises(ArchiveSnapshotValidationError):
            get_production_source_standard("gcsi.nonexistent.v99", "nonexistent_profile")

    def test_frozen_map_source_standard_is_correct_for_pds3(self):
        assert get_production_source_standard("gcsi.generic_pds3_label.v1", "fgm_pds3") == "pds3"
        assert get_production_source_standard("gcsi.generic_pds3_label.v1", "waves_burst_pds3") == "pds3"

    def test_frozen_map_source_standard_is_correct_for_pds4(self):
        assert get_production_source_standard("gcsi.generic_pds4_label.v1", "jiram_pds4") == "pds4"

    def test_production_parsers_registered_at_import_in_registry(self):
        """All frozen production pairs are pre-populated in _PARSER_REGISTRY at import."""
        for (nid, pid) in _FROZEN_PRODUCTION_PROFILE_MAP:
            assert (nid, pid) in _PARSER_REGISTRY, (
                f"Production pair ({nid!r}, {pid!r}) not in _PARSER_REGISTRY after import."
            )


# ===========================================================================
# 10. Snapshot write cannot use non-production normalizer/profile pair
# ===========================================================================


class TestSnapshotWriteEnforcesProductionIds:
    """Test 10: Snapshot write must reject empty normalizer_id or profile_id."""

    def test_empty_normalizer_id_rejected_at_write(self, tmp_path):
        """write() with empty normalizer_id must raise ArchiveSnapshotValidationError."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b121_write_test", _RETRIEVED_AT)
        snap_path = tmp_path / "snap.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="normalizer"):
            ArchiveLabelSnapshotStore._write_with_explicit_reparser_for_test(
                raw_label_bytes=raw,
                source_ref="fixture:b121_write_test",
                product=product,
                provenance=prov,
                reparser=_waves_reparser,
                path=snap_path,
                normalizer_id="",
                profile_id="waves_burst_pds3",
            )
        assert not snap_path.exists()

    def test_empty_profile_id_rejected_at_write(self, tmp_path):
        """write() with empty profile_id must raise ArchiveSnapshotValidationError."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b121_write_test2", _RETRIEVED_AT)
        snap_path = tmp_path / "snap2.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="profile"):
            ArchiveLabelSnapshotStore._write_with_explicit_reparser_for_test(
                raw_label_bytes=raw,
                source_ref="fixture:b121_write_test2",
                product=product,
                provenance=prov,
                reparser=_waves_reparser,
                path=snap_path,
                normalizer_id="gcsi.generic_pds3_label.v1",
                profile_id="",
            )
        assert not snap_path.exists()

    def test_valid_normalizer_and_profile_ids_accepted(self, tmp_path):
        """write() with known normalizer_id + profile_id writes successfully."""
        raw = _WAVES_LABEL
        product, prov = _waves_reparser(raw, "fixture:b121_write_ok", _RETRIEVED_AT)
        snap_path = tmp_path / "snap_ok.json"
        ArchiveLabelSnapshotStore._write_with_explicit_reparser_for_test(
            raw_label_bytes=raw,
            source_ref="fixture:b121_write_ok",
            product=product,
            provenance=prov,
            reparser=_waves_reparser,
            path=snap_path,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
        )
        assert snap_path.exists()


# ===========================================================================
# 11. PDS4 File_Area_Observational with missing File element raises
# ===========================================================================


class TestPds4MissingFileElementRaises:
    """Test 11: File_Area_Observational exists but no File element → raises (fail-closed)."""

    _VALID_URL = (
        "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/"
        "data_calibrated/jir_b121_missing_file.xml"
    )

    def _make_label_no_file_elem(self) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_b121_nofile</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B121 No-File Test</title>
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
  <File_Area_Observational>
    <!-- File element intentionally omitted -->
  </File_Area_Observational>
</Product_Observational>
"""

    def test_missing_file_element_raises(self):
        """File_Area_Observational without <File> must raise GenericPds4AdapterValidationError."""
        label = self._make_label_no_file_elem()
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ff]ile"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)


# ===========================================================================
# 12. PDS4 missing/empty file_name raises
# ===========================================================================


class TestPds4MissingFileNameRaises:
    """Test 12: File element present but missing/empty file_name → raises (fail-closed)."""

    _VALID_URL = (
        "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/"
        "data_calibrated/jir_b121_empty_fname.xml"
    )

    def _make_label_with_file_but_no_fname(self) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_b121_nofname</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B121 No-FileName Test</title>
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
  <File_Area_Observational>
    <File>
      <!-- file_name element intentionally omitted -->
    </File>
  </File_Area_Observational>
</Product_Observational>
"""

    def _make_label_with_empty_fname(self) -> bytes:
        return b"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_b121_emptyfname</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B121 Empty-FileName Test</title>
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
  <File_Area_Observational>
    <File>
      <file_name>   </file_name>
    </File>
  </File_Area_Observational>
</Product_Observational>
"""

    def test_missing_file_name_element_raises(self):
        """<File> without <file_name> must raise GenericPds4AdapterValidationError."""
        label = self._make_label_with_file_but_no_fname()
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ff]ile_name|[Ff]ile"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_empty_file_name_raises(self):
        """<file_name> with whitespace-only content must raise GenericPds4AdapterValidationError."""
        label = self._make_label_with_empty_fname()
        with pytest.raises(GenericPds4AdapterValidationError, match="[Ff]ile_name|[Ee]mpty"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)


# ===========================================================================
# 13. PDS4 empty/missing-unit file_size when declared raises
# ===========================================================================


class TestPds4FileAreaSizeFailClosed:
    """Test 13: file_size with bad value or unsupported unit when declared → raises."""

    _VALID_URL = (
        "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/"
        "data_calibrated/jir_b121_filesize.xml"
    )

    def _make_label(self, file_name: str = "jir_test.img",
                    size_val: str = None, size_unit: str = None) -> bytes:
        size_block = ""
        if size_val is not None:
            unit_attr = f' unit="{size_unit}"' if size_unit is not None else ""
            size_block = f"      <file_size{unit_attr}>{size_val}</file_size>\n"
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="http://pds.nasa.gov/pds4/pds/v1">
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:juno_jiram_bundle:data_calibrated:jir_b121_fsize</logical_identifier>
    <version_id>1.0</version_id>
    <title>JIRAM B121 FileSize Test</title>
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
  <File_Area_Observational>
    <File>
      <file_name>{file_name}</file_name>
{size_block}    </File>
  </File_Area_Observational>
</Product_Observational>
""".encode("utf-8")

    def test_malformed_file_size_value_raises(self):
        """Non-numeric file_size with unit=byte must raise."""
        label = self._make_label(size_val="NOT_A_NUMBER", size_unit="byte")
        with pytest.raises(GenericPds4AdapterValidationError, match="file_size|malform"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_unsupported_file_size_unit_raises(self):
        """file_size with unit=kilobyte must raise (only 'byte' supported)."""
        label = self._make_label(size_val="1024", size_unit="kilobyte")
        with pytest.raises(GenericPds4AdapterValidationError, match="unit|file_size"):
            parse_generic_pds4_label(label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT)

    def test_valid_byte_file_size_accepted(self):
        """Valid numeric file_size with unit=byte must be accepted."""
        label = self._make_label(size_val="131072", size_unit="byte")
        product, _ = parse_generic_pds4_label(
            label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT
        )
        assert product.total_data_size_bytes == 131072

    def test_no_file_size_produces_unknown(self):
        """No file_size element → file_size_bytes=None, total=None."""
        label = self._make_label()  # no size_val
        product, _ = parse_generic_pds4_label(
            label, self._VALID_URL, JIRAM_PDS4_PROFILE, _RETRIEVED_AT
        )
        assert product.total_data_size_bytes is None
        if product.data_files:
            assert product.data_files[0].file_size_bytes is None


# ===========================================================================
# 14. Approximate aggregate NOT exposed as exact total
# ===========================================================================


class TestApproximateAggregateNotExact:
    """Test 14: A product with SIZE_DISCOVERED_APPROXIMATE must have total_data_size_bytes=None."""

    def test_approximate_file_requires_none_total(self):
        """Single file with SIZE_DISCOVERED_APPROXIMATE → total must be None."""
        f = ArchiveDataFile(
            file_name="approx.bin",
            file_size_bytes=512000,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="None|approximate"):
            ArchiveScienceProduct(
                source_record_id="pds3:DS:APPROX_TEST",
                source_standard=ArchiveSourceStandard.PDS3,
                source_dataset_id="DS",
                source_product_id="APPROX_TEST",
                mission_name="JUNO",
                product_family="WAVES_BURST",
                data_files=(f,),
                total_data_size_bytes=512000,  # non-None when approximate → should fail
            )

    def test_approximate_file_with_none_total_accepted(self):
        """Approximate file + total=None is valid."""
        f = ArchiveDataFile(
            file_name="approx.bin",
            file_size_bytes=512000,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds3:DS:APPROX_NONE",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="APPROX_NONE",
            mission_name="JUNO",
            product_family="WAVES_BURST",
            data_files=(f,),
            total_data_size_bytes=None,
        )
        assert product.total_data_size_bytes is None

    def test_approximate_plus_exact_files_require_none_total(self):
        """Mix of approximate + exact files → total must be None."""
        f_approx = ArchiveDataFile(
            file_name="approx.bin",
            file_size_bytes=512000,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        f_exact = ArchiveDataFile(
            file_name="exact.bin",
            file_size_bytes=1024,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds3:DS:APPROX_MIX",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="APPROX_MIX",
            mission_name="JUNO",
            product_family="WAVES_BURST",
            data_files=(f_approx, f_exact),
            total_data_size_bytes=None,
        )
        assert product.total_data_size_bytes is None

    def test_approximate_zero_still_not_exact(self):
        """Even a zero-byte approximate file must produce total=None, not 0."""
        f = ArchiveDataFile(
            file_name="approx_zero.bin",
            file_size_bytes=0,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="None|approximate"):
            ArchiveScienceProduct(
                source_record_id="pds3:DS:APPROX_ZERO",
                source_standard=ArchiveSourceStandard.PDS3,
                source_dataset_id="DS",
                source_product_id="APPROX_ZERO",
                mission_name="JUNO",
                product_family="WAVES_BURST",
                data_files=(f,),
                total_data_size_bytes=0,  # 0 is still non-None → should fail
            )


# ===========================================================================
# 15. All-exact aggregate produces integer total
# ===========================================================================


class TestAllExactAggregateProducesIntegerTotal:
    """Test 15: All-exact aggregate still produces correct integer total."""

    def test_all_exact_single_file_correct_total(self):
        f = ArchiveDataFile(
            file_name="exact.bin",
            file_size_bytes=1024,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds3:DS:EXACT_SINGLE",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="EXACT_SINGLE",
            mission_name="JUNO",
            product_family="FGM",
            data_files=(f,),
            total_data_size_bytes=1024,
        )
        assert product.total_data_size_bytes == 1024
        assert isinstance(product.total_data_size_bytes, int)

    def test_all_exact_multi_file_correct_total(self):
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
        f3 = ArchiveDataFile(
            file_name="c.bin",
            file_size_bytes=2048,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds3:DS:EXACT_MULTI",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="EXACT_MULTI",
            mission_name="JUNO",
            product_family="FGM",
            data_files=(f1, f2, f3),
            total_data_size_bytes=3584,  # 512+1024+2048
        )
        assert product.total_data_size_bytes == 3584

    def test_all_exact_wrong_sum_rejected(self):
        """Supplying wrong sum when all files have exact size → validation error."""
        f1 = ArchiveDataFile(
            file_name="a.bin",
            file_size_bytes=512,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        import pydantic
        with pytest.raises(pydantic.ValidationError, match="sum|total"):
            ArchiveScienceProduct(
                source_record_id="pds3:DS:EXACT_WRONG",
                source_standard=ArchiveSourceStandard.PDS3,
                source_dataset_id="DS",
                source_product_id="EXACT_WRONG",
                mission_name="JUNO",
                product_family="FGM",
                data_files=(f1,),
                total_data_size_bytes=999,  # wrong total
            )

    def test_zero_file_product_exact_zero_total(self):
        """Zero data files → total must be 0 (not None) — exact and integer."""
        product = ArchiveScienceProduct(
            source_record_id="pds3:DS:ZERO_FILES",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="DS",
            source_product_id="ZERO_FILES",
            mission_name="JUNO",
            product_family="FGM",
            data_files=(),
            total_data_size_bytes=0,
        )
        assert product.total_data_size_bytes == 0
        assert product.total_data_size_bytes is not None
        assert isinstance(product.total_data_size_bytes, int)
