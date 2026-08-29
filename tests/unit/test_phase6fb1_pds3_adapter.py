"""GCSI Phase 6F-B1 — Generic PDS3 Adapter Tests.

All tests are OFFLINE. No live PDS requests are made.

Coverage:
- Bounded PVL subset parser
- GenericPds3AdapterProfile
- parse_generic_pds3_label:
  * JunoCam-style label
  * FGM-style label
  * JADE/JEDI-style tabular label
  * WAVES Burst label
- Timestamp formats (DOY and ISO, date-only)
- Missing required keywords
- Stop < start rejected
- Profile data_set_id prefix validation
- Profile instrument validation
- File size derivation (FILE_SIZE, RECORD_BYTES×FILE_RECORDS, neither)
- NUL byte rejection
- Oversized label rejection
- Provenance output
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

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
    GenericPds3AdapterValidationError,
    _parse_pds3_label,
    _parse_pds3_datetime,
    _derive_pds3_file_size,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.archive_models import (
    ArchiveDataFileSizeCertainty,
    ArchiveSourceStandard,
)
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus


_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Minimal PDS3 label fixtures
# ---------------------------------------------------------------------------

_WAVES_BURST_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_2024165T055551_B_BIN"
PRODUCT_VERSION_ID    = "V01"
RECORD_TYPE           = FIXED_LENGTH
RECORD_BYTES          = 1024
FILE_RECORDS          = 50
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "WAV"
PROCESSING_LEVEL_ID   = "3"
START_TIME            = 2024-165T05:55:51.259
STOP_TIME             = 2024-165T05:59:02.709
TARGET_NAME           = {"JUPITER", "SOLAR_SYSTEM"}
^TABLE                = "WAV_2024165T055551_B_BIN_V01.BIN"
END
"""

_JUNOCAM_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J-JNC-2-EDR-L1A-V1.0"
PRODUCT_ID            = "JNCR_2024165_01M01280_V01"
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "JNC"
START_TIME            = 2024-06-13T05:55:51.000
STOP_TIME             = 2024-06-13T06:00:00.000
TARGET_NAME           = "JUPITER"
FILE_SIZE             = 2048576
^IMAGE                = "JNCR_2024165_01M01280_V01.IMG"
END
"""

_FGM_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-SS/J-FGM-3-RDR-FLUXGATE-V1.0"
PRODUCT_ID            = "FGM_2024165_ORBIT62"
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "FGM"
START_TIME            = 2024-165T00:00:00.000
STOP_TIME             = 2024-165T23:59:59.999
TARGET_NAME           = "JUPITER"
RECORD_BYTES          = 64
FILE_RECORDS          = 86400
^DATA_FILE            = "FGM_2024165_ORBIT62.DAT"
END
"""

_JADE_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-J-JAD-3-CDR-V1.0"
PRODUCT_ID            = "JAD_L20_LO_TOF3D_2024165T055551"
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "JAD"
START_TIME            = 2024-165T05:55:51.000
STOP_TIME             = 2024-165T06:00:00.000
TARGET_NAME           = "JUPITER"
^TABLE                = "JAD_L20_LO_TOF3D_2024165T055551.DAT"
END
"""

_JEDI_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-J-JED-3-CDR-V1.0"
PRODUCT_ID            = "JED_2024165_CH0_L2"
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "JED"
START_TIME            = 2024-165T05:55:51.000
STOP_TIME             = 2024-165T06:00:00.000
TARGET_NAME           = "JUPITER"
^SPREADSHEET          = "JED_2024165_CH0_L2.CSV"
END
"""


# ===========================================================================
# Bounded PVL parser tests
# ===========================================================================


class TestPvlParser:
    def test_basic_keywords(self):
        kv = _parse_pds3_label(_WAVES_BURST_LABEL)
        assert kv["DATA_SET_ID"] == "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
        assert kv["PRODUCT_ID"] == "WAV_2024165T055551_B_BIN"

    def test_set_value_parsed_as_list(self):
        kv = _parse_pds3_label(_WAVES_BURST_LABEL)
        target = kv["TARGET_NAME"]
        assert isinstance(target, list)
        assert "JUPITER" in target

    def test_pointer_keyword_preserved(self):
        kv = _parse_pds3_label(_WAVES_BURST_LABEL)
        assert "^TABLE" in kv

    def test_nul_byte_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError, match="NUL"):
            _parse_pds3_label(b"PDS_VERSION_ID = PDS3\x00\nEND\n")

    def test_end_stops_parsing(self):
        raw = b"DATA_SET_ID = DS\nEND\nDATA_SET_ID = AFTER_END\n"
        kv = _parse_pds3_label(raw)
        assert kv["DATA_SET_ID"] == "DS"

    def test_integer_value(self):
        kv = _parse_pds3_label(b"RECORD_BYTES = 1024\nEND\n")
        assert kv.get("RECORD_BYTES") == "1024"


# ===========================================================================
# PDS3 datetime parser
# ===========================================================================


class TestPds3DatetimeParsing:
    def test_doy_datetime(self):
        dt = _parse_pds3_datetime("2024-165T05:55:51.259", "START_TIME")
        assert dt.tzinfo is not None
        assert dt.year == 2024

    def test_iso_datetime(self):
        dt = _parse_pds3_datetime("2024-06-13T10:00:00.000", "START_TIME")
        assert dt.year == 2024
        assert dt.month == 6
        assert dt.day == 13

    def test_doy_date_only(self):
        dt = _parse_pds3_datetime("2024-165", "START_TIME")
        assert dt.year == 2024

    def test_iso_date_only(self):
        dt = _parse_pds3_datetime("2024-06-13", "START_TIME")
        assert dt.day == 13

    def test_invalid_format_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError):
            _parse_pds3_datetime("not-a-date", "START_TIME")

    def test_na_value_rejected(self):
        with pytest.raises(GenericPds3AdapterValidationError):
            _parse_pds3_datetime("N/A", "START_TIME")

    def test_quoted_datetime_accepted(self):
        dt = _parse_pds3_datetime('"2024-165T05:55:51.259"', "START_TIME")
        assert dt.year == 2024


# ===========================================================================
# File size derivation
# ===========================================================================


class TestPds3FileSizeDerivation:
    def test_file_size_keyword(self):
        kv = _parse_pds3_label(b"FILE_SIZE = 2048576\nEND\n")
        size, certainty = _derive_pds3_file_size(kv)
        assert size == 2048576
        assert certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    def test_record_formula(self):
        kv = _parse_pds3_label(b"RECORD_BYTES = 1024\nFILE_RECORDS = 50\nEND\n")
        size, certainty = _derive_pds3_file_size(kv)
        assert size == 51200
        assert certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT

    def test_no_size_info(self):
        kv = _parse_pds3_label(b"DATA_SET_ID = DS\nEND\n")
        size, certainty = _derive_pds3_file_size(kv)
        assert size == 0
        assert certainty == ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE

    def test_file_size_takes_priority_over_formula(self):
        kv = _parse_pds3_label(
            b"FILE_SIZE = 9999\nRECORD_BYTES = 1024\nFILE_RECORDS = 50\nEND\n"
        )
        size, certainty = _derive_pds3_file_size(kv)
        assert size == 9999
        assert certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT


# ===========================================================================
# Full parse — WAVES Burst
# ===========================================================================


class TestWavesBurstParsing:
    def test_valid_waves_burst(self):
        product, prov = parse_generic_pds3_label(
            _WAVES_BURST_LABEL,
            "https://pds-example.nasa.gov/WAV_2024165T055551_B_BIN.LBL",
            WAVES_BURST_PDS3_PROFILE,
            _RETRIEVED_AT,
        )
        assert product.source_standard == ArchiveSourceStandard.PDS3
        assert product.instrument_name == "WAV"
        assert product.product_family == "WAVES_BURST"
        assert product.source_record_id.startswith("pds3:")
        assert "WAV_2024165T055551_B_BIN" in product.source_record_id
        assert product.source_version is not None  # V01

    def test_waves_provenance(self):
        product, prov = parse_generic_pds3_label(
            _WAVES_BURST_LABEL,
            "https://pds.nasa.gov/wav_test.lbl",
            WAVES_BURST_PDS3_PROFILE,
            _RETRIEVED_AT,
        )
        assert prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert prov.validation_status == ProvenanceValidationStatus.VALIDATED
        assert prov.content_sha256 == hashlib.sha256(_WAVES_BURST_LABEL).hexdigest()

    def test_waves_file_size_from_formula(self):
        product, _ = parse_generic_pds3_label(
            _WAVES_BURST_LABEL,
            "src",
            WAVES_BURST_PDS3_PROFILE,
            _RETRIEVED_AT,
        )
        # RECORD_BYTES=1024 × FILE_RECORDS=50 = 51200
        assert product.total_data_size_bytes == 51200

    def test_waves_target_names(self):
        product, _ = parse_generic_pds3_label(
            _WAVES_BURST_LABEL, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert "JUPITER" in product.target_names

    def test_wrong_dataset_prefix_rejected(self):
        label = _WAVES_BURST_LABEL.replace(
            b"DATA_SET_ID           = \"JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0\"",
            b"DATA_SET_ID           = \"WRONG-DATASET\"",
        )
        with pytest.raises(GenericPds3AdapterValidationError, match="DATA_SET_ID"):
            parse_generic_pds3_label(label, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_missing_start_time_rejected(self):
        label = b"""\
PDS_VERSION_ID = PDS3
DATA_SET_ID    = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID     = "WAV_TEST"
INSTRUMENT_HOST_ID = "JNO"
INSTRUMENT_ID  = "WAV"
STOP_TIME      = 2024-165T06:00:00.000
END
"""
        with pytest.raises(GenericPds3AdapterValidationError, match="START_TIME"):
            parse_generic_pds3_label(label, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_stop_before_start_rejected(self):
        label = _WAVES_BURST_LABEL.replace(
            b"START_TIME            = 2024-165T05:55:51.259",
            b"START_TIME            = 2024-165T10:00:00.000",
        )
        with pytest.raises(GenericPds3AdapterValidationError, match="START_TIME|STOP_TIME"):
            parse_generic_pds3_label(label, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)


# ===========================================================================
# JunoCam-style label
# ===========================================================================


class TestJunoCamParsing:
    def test_valid_junocam(self):
        product, prov = parse_generic_pds3_label(
            _JUNOCAM_LABEL, "src", JUNOCAM_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.product_family == "JUNOCAM"
        assert product.instrument_name == "JNC"
        # FILE_SIZE = 2048576
        assert product.total_data_size_bytes == 2048576


# ===========================================================================
# FGM-style label
# ===========================================================================


class TestFgmParsing:
    def test_valid_fgm(self):
        product, prov = parse_generic_pds3_label(
            _FGM_LABEL, "src", FGM_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.product_family == "FGM"
        # RECORD_BYTES=64 × FILE_RECORDS=86400 = 5529600
        assert product.total_data_size_bytes == 64 * 86400


# ===========================================================================
# JADE/JEDI tabular label
# ===========================================================================


class TestJadeJediParsing:
    def test_valid_jade(self):
        product, _ = parse_generic_pds3_label(
            _JADE_LABEL, "src", JADE_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.product_family == "JADE"

    def test_valid_jedi(self):
        product, _ = parse_generic_pds3_label(
            _JEDI_LABEL, "src", JEDI_PDS3_PROFILE, _RETRIEVED_AT
        )
        assert product.product_family == "JEDI"


# ===========================================================================
# Security and error cases
# ===========================================================================


class TestPds3SecurityAndErrors:
    def test_nul_byte_rejected(self):
        raw = b"DATA_SET_ID = DS\x00\nPRODUCT_ID = P\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="NUL"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_oversized_label_rejected(self):
        raw = b"x" * (MAX_PDS3_LABEL_BYTES + 1)
        with pytest.raises(GenericPds3AdapterValidationError, match="size"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_missing_data_set_id_rejected(self):
        raw = b"PRODUCT_ID = PROD\nINSTRUMENT_HOST_ID = JNO\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="DATA_SET_ID"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_missing_product_id_rejected(self):
        raw = b"DATA_SET_ID = \"JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0\"\nINSTRUMENT_HOST_ID = JNO\nEND\n"
        with pytest.raises(GenericPds3AdapterValidationError, match="PRODUCT_ID"):
            parse_generic_pds3_label(raw, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)

    def test_naive_retrieved_at_rejected(self):
        naive = datetime(2024, 6, 14, 9, 35)
        with pytest.raises(GenericPds3AdapterValidationError, match="[Tt]imezone"):
            parse_generic_pds3_label(_WAVES_BURST_LABEL, "src", WAVES_BURST_PDS3_PROFILE, naive)

    def test_provenance_id_deterministic(self):
        _, prov1 = parse_generic_pds3_label(_WAVES_BURST_LABEL, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)
        _, prov2 = parse_generic_pds3_label(_WAVES_BURST_LABEL, "src", WAVES_BURST_PDS3_PROFILE, _RETRIEVED_AT)
        assert prov1.provenance_id == prov2.provenance_id
