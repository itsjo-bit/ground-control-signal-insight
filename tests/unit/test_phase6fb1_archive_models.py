"""GCSI Phase 6F-B1 — Generic Archive Source Domain Model Tests.

Tests for:
  - ArchiveSourceStandard
  - ArchiveDataFileSizeCertainty
  - ArchiveDataFile
  - ArchiveScienceProduct (including source_record_id helpers)
  - ArchiveCaptureRecord
  - ProductRepresentationKind / ProductRepresentationRelationship
  - VerifiedInventoryEntry
  - VerifiedInventoryManifest (including scale test with 411 entries)

All tests are offline. No network activity.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional

import pytest
import pydantic

from backend.app.mission_sources.archive_models import (
    ArchiveCaptureRecord,
    ArchiveDataFile,
    ArchiveDataFileSizeCertainty,
    ArchiveScienceProduct,
    ArchiveSourceStandard,
    ProductRepresentationKind,
    ProductRepresentationRelationship,
    VerifiedInventoryEntry,
    VerifiedInventoryManifest,
    build_pds3_source_record_id,
    build_pds4_source_record_id,
    _compute_manifest_id,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_NOW_UTC = datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc)
_RAW_BYTES = b"<label>content</label>"
_SHA256 = hashlib.sha256(_RAW_BYTES).hexdigest()


def _make_data_file(
    file_name: str = "data.csv",
    file_size_bytes: int = 1024,
    size_certainty: ArchiveDataFileSizeCertainty = ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
) -> ArchiveDataFile:
    return ArchiveDataFile(
        file_name=file_name,
        file_size_bytes=file_size_bytes,
        size_certainty=size_certainty,
    )


def _make_product(
    source_record_id: str = "pds4:urn:nasa:pds:test:data:product_a::1.0",
    product_family: str = "TEST_FAMILY",
    data_files: tuple[ArchiveDataFile, ...] = (),
    obs_start: Optional[datetime] = None,
    obs_stop: Optional[datetime] = None,
) -> ArchiveScienceProduct:
    total = sum(f.file_size_bytes for f in data_files)
    return ArchiveScienceProduct(
        source_record_id=source_record_id,
        source_standard=ArchiveSourceStandard.PDS4,
        source_dataset_id="urn:nasa:pds:test:data",
        source_product_id="urn:nasa:pds:test:data:product_a",
        source_version="1.0",
        mission_name="TEST_MISSION",
        spacecraft_name="TEST_SC",
        instrument_name="TEST_INST",
        product_family=product_family,
        observation_start_utc=obs_start,
        observation_stop_utc=obs_stop,
        data_files=data_files,
        total_data_size_bytes=total,
    )


def _make_provenance(
    source_record_id: str = "pds4:urn:nasa:pds:test:data:product_a::1.0",
    content_sha256: str = _SHA256,
) -> ProvenanceRecord:
    return ProvenanceRecord(
        provenance_id="test-provenance-id-001",
        kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        source_system="NASA-PDS-Test",
        source_record_id=source_record_id,
        retrieved_at=_NOW_UTC,
        validation_status=ProvenanceValidationStatus.VALIDATED,
        content_sha256=content_sha256,
    )


def _make_entry(
    logical_product_id: str = "LP-001",
    record_ids: tuple[str, ...] = ("pds4:urn:nasa:pds:test:data:product_a::1.0",),
    availability: datetime = _NOW_UTC,
) -> VerifiedInventoryEntry:
    return VerifiedInventoryEntry(
        logical_product_id=logical_product_id,
        representation_record_ids=record_ids,
        availability_time_utc=availability,
    )


# ===========================================================================
# A. ArchiveSourceStandard
# ===========================================================================


class TestArchiveSourceStandard:
    def test_pds3_value(self):
        assert ArchiveSourceStandard.PDS3.value == "pds3"

    def test_pds4_value(self):
        assert ArchiveSourceStandard.PDS4.value == "pds4"


# ===========================================================================
# B. ArchiveDataFileSizeCertainty
# ===========================================================================


class TestArchiveDataFileSizeCertainty:
    def test_approximate_value(self):
        assert ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE.value == "size_discovered_approximate"

    def test_exact_value(self):
        assert ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT.value == "size_metadata_exact"

    def test_verified_value(self):
        assert ArchiveDataFileSizeCertainty.SIZE_SNAPSHOT_VERIFIED.value == "size_snapshot_verified"


# ===========================================================================
# C. ArchiveDataFile
# ===========================================================================


class TestArchiveDataFile:
    def test_valid_file(self):
        f = _make_data_file()
        assert f.file_name == "data.csv"
        assert f.file_size_bytes == 1024
        assert f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        assert f.checksum_algorithm is None
        assert f.checksum_value is None

    def test_empty_file_name_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="file_name"):
            ArchiveDataFile(
                file_name="   ",
                file_size_bytes=0,
                size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
            )

    def test_negative_size_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="file_size_bytes"):
            ArchiveDataFile(
                file_name="data.csv",
                file_size_bytes=-1,
                size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
            )

    def test_checksum_without_algorithm_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="checksum"):
            ArchiveDataFile(
                file_name="data.csv",
                file_size_bytes=0,
                size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
                checksum_value="abc123",
            )

    def test_algorithm_without_value_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="checksum"):
            ArchiveDataFile(
                file_name="data.csv",
                file_size_bytes=0,
                size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
                checksum_algorithm="MD5",
            )

    def test_checksum_pair_accepted(self):
        f = ArchiveDataFile(
            file_name="data.csv",
            file_size_bytes=512,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
            checksum_algorithm="MD5",
            checksum_value="d41d8cd98f00b204e9800998ecf8427e",
        )
        assert f.checksum_algorithm == "MD5"
        assert f.checksum_value == "d41d8cd98f00b204e9800998ecf8427e"

    def test_zero_size_accepted(self):
        f = _make_data_file(file_size_bytes=0)
        assert f.file_size_bytes == 0

    def test_frozen_model(self):
        f = _make_data_file()
        with pytest.raises(Exception):
            f.file_name = "other.csv"  # type: ignore[misc]

    def test_extra_fields_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ArchiveDataFile(
                file_name="data.csv",
                file_size_bytes=0,
                size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
                extra_field="oops",
            )


# ===========================================================================
# D. source_record_id helpers
# ===========================================================================


class TestSourceRecordIdHelpers:
    def test_pds4_formula(self):
        rid = build_pds4_source_record_id("urn:nasa:pds:test:data:product::1.0")
        assert rid == "pds4:urn:nasa:pds:test:data:product::1.0"

    def test_pds4_empty_rejected(self):
        with pytest.raises(ValueError):
            build_pds4_source_record_id("")

    def test_pds3_no_version(self):
        rid = build_pds3_source_record_id("JNO-WAV-3-BST", "WAV_2024165T055551_B_BIN")
        assert rid == "pds3:JNO-WAV-3-BST:WAV_2024165T055551_B_BIN"

    def test_pds3_with_version(self):
        rid = build_pds3_source_record_id("JNO-WAV-3-BST", "WAV_2024165T055551_B_BIN", "01")
        assert rid == "pds3:JNO-WAV-3-BST:WAV_2024165T055551_B_BIN:v01"

    def test_pds3_empty_dataset_rejected(self):
        with pytest.raises(ValueError, match="data_set_id"):
            build_pds3_source_record_id("", "PROD_ID")

    def test_pds3_empty_product_rejected(self):
        with pytest.raises(ValueError, match="product_id"):
            build_pds3_source_record_id("DS_ID", "")

    def test_pds3_empty_version_rejected(self):
        with pytest.raises(ValueError, match="product_version_id"):
            build_pds3_source_record_id("DS_ID", "PROD_ID", "   ")


# ===========================================================================
# E. ArchiveScienceProduct
# ===========================================================================


class TestArchiveScienceProduct:
    def test_valid_product_no_files(self):
        p = _make_product()
        assert p.total_data_size_bytes == 0
        assert p.data_files == ()

    def test_valid_product_with_file(self):
        f = _make_data_file(file_size_bytes=5000)
        p = _make_product(data_files=(f,))
        assert p.total_data_size_bytes == 5000

    def test_empty_source_record_id_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="source_record_id"):
            ArchiveScienceProduct(
                source_record_id="   ",
                source_standard=ArchiveSourceStandard.PDS4,
                source_dataset_id="ds",
                source_product_id="prod",
                mission_name="M",
                spacecraft_name="S",
                instrument_name="I",
                product_family="F",
                data_files=(),
                total_data_size_bytes=0,
            )

    def test_wrong_total_size_rejected(self):
        f = _make_data_file(file_size_bytes=100)
        with pytest.raises(pydantic.ValidationError, match="total_data_size_bytes"):
            ArchiveScienceProduct(
                source_record_id="pds4:test::1.0",
                source_standard=ArchiveSourceStandard.PDS4,
                source_dataset_id="ds",
                source_product_id="prod",
                mission_name="M",
                spacecraft_name="S",
                instrument_name="I",
                product_family="F",
                data_files=(f,),
                total_data_size_bytes=999,  # wrong
            )

    def test_stop_before_start_rejected(self):
        start = datetime(2024, 6, 14, 10, 0, tzinfo=timezone.utc)
        stop  = datetime(2024, 6, 14, 9, 0, tzinfo=timezone.utc)
        with pytest.raises(pydantic.ValidationError, match="start.*stop|stop.*start"):
            _make_product(obs_start=start, obs_stop=stop)

    def test_naive_start_rejected(self):
        naive = datetime(2024, 6, 14, 10, 0)
        with pytest.raises(pydantic.ValidationError):
            _make_product(obs_start=naive)

    def test_naive_stop_rejected(self):
        naive = datetime(2024, 6, 14, 10, 0)
        with pytest.raises(pydantic.ValidationError):
            _make_product(obs_stop=naive)

    def test_duplicate_file_names_rejected(self):
        f1 = _make_data_file("data.csv", 100)
        f2 = _make_data_file("data.csv", 200)
        with pytest.raises(pydantic.ValidationError, match="[Dd]uplicate"):
            ArchiveScienceProduct(
                source_record_id="pds4:test::1.0",
                source_standard=ArchiveSourceStandard.PDS4,
                source_dataset_id="ds",
                source_product_id="prod",
                mission_name="M",
                spacecraft_name="S",
                instrument_name="I",
                product_family="F",
                data_files=(f1, f2),
                total_data_size_bytes=300,
            )

    def test_start_equals_stop_accepted(self):
        t = datetime(2024, 6, 14, 10, 0, tzinfo=timezone.utc)
        p = _make_product(obs_start=t, obs_stop=t)
        assert p.observation_start_utc == t
        assert p.observation_stop_utc == t

    def test_extra_fields_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ArchiveScienceProduct(
                source_record_id="pds4:test::1.0",
                source_standard=ArchiveSourceStandard.PDS4,
                source_dataset_id="ds",
                source_product_id="prod",
                mission_name="M",
                spacecraft_name="S",
                instrument_name="I",
                product_family="F",
                data_files=(),
                total_data_size_bytes=0,
                unknown_field="oops",
            )

    def test_frozen_model(self):
        p = _make_product()
        with pytest.raises(Exception):
            p.mission_name = "OTHER"  # type: ignore[misc]

    def test_pds3_product(self):
        f = _make_data_file(
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
        )
        p = ArchiveScienceProduct(
            source_record_id="pds3:JNO-WAV-3-BST:WAV_B_BIN:v01",
            source_standard=ArchiveSourceStandard.PDS3,
            source_dataset_id="JNO-WAV-3-BST",
            source_product_id="WAV_B_BIN",
            source_version="01",
            mission_name="JUNO",
            spacecraft_name="JNO",
            instrument_name="WAV",
            product_family="WAVES_BURST",
            data_files=(f,),
            total_data_size_bytes=f.file_size_bytes,
        )
        assert p.source_standard == ArchiveSourceStandard.PDS3


# ===========================================================================
# F. ArchiveCaptureRecord
# ===========================================================================


class TestArchiveCaptureRecord:
    def test_valid_capture(self):
        product = _make_product()
        prov = _make_provenance()
        cap = ArchiveCaptureRecord(
            source_label_ref="https://pds.nasa.gov/test.xml",
            product=product,
            provenance=prov,
            raw_label_bytes=_RAW_BYTES,
        )
        assert cap.product == product

    def test_source_record_id_mismatch_rejected(self):
        product = _make_product(source_record_id="pds4:correct::1.0")
        prov = _make_provenance(source_record_id="pds4:wrong::1.0")
        with pytest.raises(pydantic.ValidationError, match="source_record_id"):
            ArchiveCaptureRecord(
                source_label_ref=None,
                product=product,
                provenance=prov,
                raw_label_bytes=_RAW_BYTES,
            )

    def test_wrong_provenance_kind_rejected(self):
        product = _make_product()
        prov = ProvenanceRecord(
            provenance_id="test-prov",
            kind=ProvenanceKind.MODELED,  # wrong
            source_system="NASA-PDS-Test",
            source_record_id=product.source_record_id,
            retrieved_at=_NOW_UTC,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256=_SHA256,
        )
        with pytest.raises(pydantic.ValidationError, match="EXTERNAL_AUTHORITATIVE"):
            ArchiveCaptureRecord(
                source_label_ref=None,
                product=product,
                provenance=prov,
                raw_label_bytes=_RAW_BYTES,
            )

    def test_wrong_validation_status_rejected(self):
        product = _make_product()
        prov = ProvenanceRecord(
            provenance_id="test-prov",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA-PDS-Test",
            source_record_id=product.source_record_id,
            retrieved_at=_NOW_UTC,
            validation_status=ProvenanceValidationStatus.PENDING,  # wrong
            content_sha256=_SHA256,
        )
        with pytest.raises(pydantic.ValidationError, match="VALIDATED"):
            ArchiveCaptureRecord(
                source_label_ref=None,
                product=product,
                provenance=prov,
                raw_label_bytes=_RAW_BYTES,
            )

    def test_hash_mismatch_rejected(self):
        product = _make_product()
        bad_sha = "a" * 64  # wrong hash
        prov = ProvenanceRecord(
            provenance_id="test-prov",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA-PDS-Test",
            source_record_id=product.source_record_id,
            retrieved_at=_NOW_UTC,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256=bad_sha,
        )
        with pytest.raises(pydantic.ValidationError, match="SHA-256"):
            ArchiveCaptureRecord(
                source_label_ref=None,
                product=product,
                provenance=prov,
                raw_label_bytes=_RAW_BYTES,
            )

    def test_naive_retrieved_at_rejected(self):
        product = _make_product()
        naive_dt = datetime(2024, 6, 14, 10, 0)  # naive
        # ProvenanceRecord itself will reject naive retrieved_at
        with pytest.raises(pydantic.ValidationError):
            ProvenanceRecord(
                provenance_id="test-prov",
                kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
                source_system="NASA-PDS-Test",
                source_record_id=product.source_record_id,
                retrieved_at=naive_dt,
                validation_status=ProvenanceValidationStatus.VALIDATED,
                content_sha256=_SHA256,
            )

    def test_label_ref_mismatch_rejected(self):
        product = _make_product()
        product_with_ref = ArchiveScienceProduct(
            **{**product.model_dump(), "source_label_ref": "https://pds.nasa.gov/real.xml"}
        )
        prov = _make_provenance()
        with pytest.raises(pydantic.ValidationError, match="source_label_ref"):
            ArchiveCaptureRecord(
                source_label_ref="https://pds.nasa.gov/OTHER.xml",  # mismatch
                product=product_with_ref,
                provenance=prov,
                raw_label_bytes=_RAW_BYTES,
            )


# ===========================================================================
# G. ProductRepresentationRelationship
# ===========================================================================


class TestProductRepresentationRelationship:
    def test_valid_relationship(self):
        r = ProductRepresentationRelationship(
            from_record_id="pds4:A::1.0",
            to_record_id="pds4:B::1.0",
            kind=ProductRepresentationKind.SAME_OBSERVATION_ALTERNATE_PROCESSING,
        )
        assert r.kind == ProductRepresentationKind.SAME_OBSERVATION_ALTERNATE_PROCESSING

    def test_self_relation_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="self-relation"):
            ProductRepresentationRelationship(
                from_record_id="pds4:same::1.0",
                to_record_id="pds4:same::1.0",
                kind=ProductRepresentationKind.COMPONENT_RELATION,
            )

    def test_empty_from_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="empty"):
            ProductRepresentationRelationship(
                from_record_id="  ",
                to_record_id="pds4:B::1.0",
                kind=ProductRepresentationKind.DERIVED_REPRESENTATION,
            )

    def test_all_kinds(self):
        for kind in ProductRepresentationKind:
            r = ProductRepresentationRelationship(
                from_record_id="pds4:A::1.0",
                to_record_id="pds4:B::1.0",
                kind=kind,
            )
            assert r.kind == kind

    def test_junocam_edr_rdr(self):
        r = ProductRepresentationRelationship(
            from_record_id="pds4:urn:nasa:pds:junocam:edr:img::1.0",
            to_record_id="pds4:urn:nasa:pds:junocam:rdr:img::1.0",
            kind=ProductRepresentationKind.SAME_OBSERVATION_ALTERNATE_PROCESSING,
            notes="JunoCam EDR ↔ RDR alternate processing",
        )
        assert r.notes is not None

    def test_waves_survey_vs_burst(self):
        r = ProductRepresentationRelationship(
            from_record_id="pds3:JNO-WAV-SURVEY:WAV_SRV_01",
            to_record_id="pds3:JNO-WAV-BST:WAV_2024165T055551_B_BIN",
            kind=ProductRepresentationKind.INDEPENDENT_ACQUISITION,
        )
        assert r.kind == ProductRepresentationKind.INDEPENDENT_ACQUISITION


# ===========================================================================
# H. VerifiedInventoryEntry
# ===========================================================================


class TestVerifiedInventoryEntry:
    def test_valid_entry(self):
        e = _make_entry()
        assert e.logical_product_id == "LP-001"
        assert len(e.representation_record_ids) == 1

    def test_empty_logical_id_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="logical_product_id"):
            VerifiedInventoryEntry(
                logical_product_id="  ",
                representation_record_ids=("rid1",),
                availability_time_utc=_NOW_UTC,
            )

    def test_empty_representation_ids_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            VerifiedInventoryEntry(
                logical_product_id="LP-001",
                representation_record_ids=(),
                availability_time_utc=_NOW_UTC,
            )

    def test_duplicate_representation_ids_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="duplicate"):
            VerifiedInventoryEntry(
                logical_product_id="LP-001",
                representation_record_ids=("rid1", "rid1"),
                availability_time_utc=_NOW_UTC,
            )

    def test_naive_availability_rejected(self):
        naive = datetime(2024, 6, 14, 9, 35)
        with pytest.raises(pydantic.ValidationError):
            VerifiedInventoryEntry(
                logical_product_id="LP-001",
                representation_record_ids=("rid1",),
                availability_time_utc=naive,
            )

    def test_multiple_representation_ids_accepted(self):
        e = VerifiedInventoryEntry(
            logical_product_id="LP-CAM-001",
            representation_record_ids=("pds4:edr::1.0", "pds4:rdr::1.0"),
            availability_time_utc=_NOW_UTC,
        )
        assert len(e.representation_record_ids) == 2

    def test_availability_normalized_to_utc(self):
        aware_plus7 = datetime(2024, 6, 14, 16, 35, 17, 546000,
                               tzinfo=timezone(timedelta(hours=7)))
        e = VerifiedInventoryEntry(
            logical_product_id="LP-001",
            representation_record_ids=("rid1",),
            availability_time_utc=aware_plus7,
        )
        # Should be normalized to UTC
        assert e.availability_time_utc.utcoffset().total_seconds() == 0

    def test_frozen_model(self):
        e = _make_entry()
        with pytest.raises(Exception):
            e.logical_product_id = "other"  # type: ignore[misc]


# ===========================================================================
# I. VerifiedInventoryManifest
# ===========================================================================


class TestVerifiedInventoryManifest:
    def _make_411_entries(self) -> list[VerifiedInventoryEntry]:
        """Generate 411 synthetic entries for scale test."""
        entries = []
        for i in range(411):
            entries.append(
                VerifiedInventoryEntry(
                    logical_product_id=f"LP-{i:04d}",
                    representation_record_ids=(f"pds3:DS:PROD_{i:04d}",),
                    availability_time_utc=_NOW_UTC,
                )
            )
        return entries

    def test_valid_single_entry(self):
        m = VerifiedInventoryManifest.build([_make_entry()])
        assert len(m.entries) == 1

    def test_411_entries_scale(self):
        """411 entries must not create any special-case code path."""
        entries = self._make_411_entries()
        m = VerifiedInventoryManifest.build(entries)
        assert len(m.entries) == 411
        assert len(m.manifest_id) == 64  # SHA-256 hex

    def test_greater_than_411_entries(self):
        """Manifest accepts > 411 entries — 411 is not a hard limit."""
        entries = [
            VerifiedInventoryEntry(
                logical_product_id=f"LP-{i:05d}",
                representation_record_ids=(f"pds3:DS:PROD_{i:05d}",),
                availability_time_utc=_NOW_UTC,
            )
            for i in range(500)
        ]
        m = VerifiedInventoryManifest.build(entries)
        assert len(m.entries) == 500

    def test_empty_entries_rejected(self):
        with pytest.raises(pydantic.ValidationError, match="[Ee]mpty|at least one"):
            VerifiedInventoryManifest.build([])

    def test_duplicate_logical_ids_rejected(self):
        e1 = _make_entry("LP-001", ("rid1",))
        e2 = _make_entry("LP-001", ("rid2",))  # duplicate logical ID
        with pytest.raises(pydantic.ValidationError, match="[Dd]uplicate"):
            VerifiedInventoryManifest.build([e1, e2])

    def test_duplicate_representation_ids_across_entries_rejected(self):
        e1 = _make_entry("LP-001", ("shared-rid",))
        e2 = _make_entry("LP-002", ("shared-rid",))  # same repr ID in different entry
        with pytest.raises(pydantic.ValidationError, match="shared-rid"):
            VerifiedInventoryManifest.build([e1, e2])

    def test_manifest_id_deterministic(self):
        entries = [_make_entry("LP-001"), _make_entry("LP-002", ("rid2",))]
        m1 = VerifiedInventoryManifest.build(entries)
        m2 = VerifiedInventoryManifest.build(entries)
        assert m1.manifest_id == m2.manifest_id

    def test_manifest_id_wrong_rejected(self):
        e = _make_entry()
        correct_id = _compute_manifest_id((e,))
        wrong_id = "a" * 64
        assert wrong_id != correct_id
        with pytest.raises(pydantic.ValidationError, match="manifest_id"):
            VerifiedInventoryManifest(manifest_id=wrong_id, entries=(e,))

    def test_immutable(self):
        m = VerifiedInventoryManifest.build([_make_entry()])
        with pytest.raises(Exception):
            m.entries = ()  # type: ignore[misc]

    def test_canonical_serialization_stable(self):
        entries = self._make_411_entries()
        m = VerifiedInventoryManifest.build(entries)
        json1 = m.model_dump_json()
        json2 = m.model_dump_json()
        assert json1 == json2
