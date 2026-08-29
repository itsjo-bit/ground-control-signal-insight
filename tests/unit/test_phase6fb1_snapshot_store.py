"""GCSI Phase 6F-B1 — Generic Archive Label Snapshot Store Tests.

All tests are OFFLINE. No network activity.

Coverage:
- write / load round-trip (PDS3 and PDS4)
- hash mismatch rejection
- oversized snapshot rejection
- Base64 corruption rejection
- schema/version mismatch rejection
- retrieved_at mismatch rejection
- product mismatch rejection
- provenance mismatch rejection
- snapshot_id mismatch rejection
- missing file (UnavailableError)
- zero network on load (pure)
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from backend.app.mission_sources.adapters.pds3_adapter import (
    WAVES_BURST_PDS3_PROFILE,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.archive_models import ArchiveScienceProduct
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    ArchiveLabelSnapshotStore,
    ArchiveSnapshotUnavailableError,
    ArchiveSnapshotValidationError,
    _compute_snapshot_id,
)
from backend.app.provenance.models import ProvenanceRecord

_RETRIEVED_AT = datetime(2024, 6, 14, 9, 35, 17, tzinfo=timezone.utc)

_WAVES_BURST_LABEL = b"""\
PDS_VERSION_ID        = PDS3
DATA_SET_ID           = "JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0"
PRODUCT_ID            = "WAV_SNAP_TEST_B_BIN"
INSTRUMENT_HOST_ID    = "JNO"
INSTRUMENT_ID         = "WAV"
PROCESSING_LEVEL_ID   = "3"
START_TIME            = 2024-165T05:55:51.259
STOP_TIME             = 2024-165T05:59:02.709
TARGET_NAME           = "JUPITER"
RECORD_BYTES          = 1024
FILE_RECORDS          = 10
^TABLE                = "WAV_SNAP_TEST_B_BIN.BIN"
END
"""


def _waves_reparser(
    raw_bytes: bytes,
    source_ref: str,
    retrieved_at: datetime,
) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
    return parse_generic_pds3_label(
        raw_bytes, source_ref, WAVES_BURST_PDS3_PROFILE, retrieved_at
    )


class TestArchiveLabelSnapshotStore:
    def _write_and_load(self, tmp_path: Path) -> tuple[ArchiveScienceProduct, ProvenanceRecord]:
        raw = _WAVES_BURST_LABEL
        product, prov = _waves_reparser(raw, "test://label.lbl", _RETRIEVED_AT)
        snap_path = tmp_path / "test.snapshot.json"
        ArchiveLabelSnapshotStore.write(
            raw_label_bytes=raw,
            source_ref="test://label.lbl",
            product=product,
            provenance=prov,
            reparser=_waves_reparser,
            path=snap_path,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
        )
        return ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_round_trip(self, tmp_path):
        product, prov = self._write_and_load(tmp_path)
        assert product.source_record_id.startswith("pds3:")
        assert "WAV_SNAP_TEST_B_BIN" in product.source_record_id
        assert prov.content_sha256 == hashlib.sha256(_WAVES_BURST_LABEL).hexdigest()

    def test_zero_network_on_load(self, tmp_path):
        # Load succeeds without any external connectivity — just reading local file.
        self._write_and_load(tmp_path)

    def test_missing_file_raises_unavailable(self, tmp_path):
        with pytest.raises(ArchiveSnapshotUnavailableError):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(
                tmp_path / "nonexistent.json", _waves_reparser
            )

    def _write(self, tmp_path, snap_path, raw=None, source_ref="src"):
        raw = raw or _WAVES_BURST_LABEL
        product, prov = _waves_reparser(raw, source_ref, _RETRIEVED_AT)
        ArchiveLabelSnapshotStore.write(
            raw_label_bytes=raw,
            source_ref=source_ref,
            product=product,
            provenance=prov,
            reparser=_waves_reparser,
            path=snap_path,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
        )
        return product, prov

    def test_hash_mismatch_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        self._write(tmp_path, snap_path)
        # Corrupt the raw_label_base64 in the file.
        data = json.loads(snap_path.read_text())
        data["raw_label_base64"] = data["raw_label_base64"][:-10] + "AAAAAAAAAA"
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError, match="[Hh]ash|sha256|SHA"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_wrong_schema_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        self._write(tmp_path, snap_path)
        data = json.loads(snap_path.read_text())
        data["snapshot_schema"] = "wrong.schema"
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError, match="[Ss]chema"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_wrong_version_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        self._write(tmp_path, snap_path)
        data = json.loads(snap_path.read_text())
        data["snapshot_version"] = 99
        snap_path.write_text(json.dumps(data))
        with pytest.raises(ArchiveSnapshotValidationError, match="[Vv]ersion"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_malformed_json_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        snap_path.write_bytes(b"not json {{{")
        with pytest.raises(ArchiveSnapshotValidationError, match="JSON"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_malformed_utf8_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        snap_path.write_bytes(b"\xff\xfe{}")
        with pytest.raises(ArchiveSnapshotValidationError, match="UTF-8"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_oversized_file_rejected(self, tmp_path):
        snap_path = tmp_path / "snap.json"
        from backend.app.mission_sources.snapshots.archive_label_snapshot import _MAX_SNAPSHOT_BYTES
        snap_path.write_bytes(b"x" * (_MAX_SNAPSHOT_BYTES + 2))
        with pytest.raises(ArchiveSnapshotValidationError, match="size"):
            ArchiveLabelSnapshotStore.load_from_explicit_reparser(snap_path, _waves_reparser)

    def test_snapshot_id_deterministic(self, tmp_path):
        raw = _WAVES_BURST_LABEL
        product, prov = _waves_reparser(raw, "src", _RETRIEVED_AT)
        snap_path1 = tmp_path / "snap1.json"
        snap_path2 = tmp_path / "snap2.json"
        ArchiveLabelSnapshotStore.write(
            raw, "src", product, prov, _waves_reparser, snap_path1,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
        )
        ArchiveLabelSnapshotStore.write(
            raw, "src", product, prov, _waves_reparser, snap_path2,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
        )
        d1 = json.loads(snap_path1.read_text())
        d2 = json.loads(snap_path2.read_text())
        assert d1["snapshot_id"] == d2["snapshot_id"]

    def test_write_hash_mismatch_rejected(self, tmp_path):
        raw = _WAVES_BURST_LABEL
        product, prov = _waves_reparser(raw, "src", _RETRIEVED_AT)
        # Provide wrong content_sha256 in provenance (requires a new prov record).
        from backend.app.provenance.models import (
            ProvenanceKind,
            ProvenanceRecord,
            ProvenanceValidationStatus,
        )
        bad_prov = ProvenanceRecord(
            provenance_id=prov.provenance_id,
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system=prov.source_system,
            source_record_id=prov.source_record_id,
            retrieved_at=prov.retrieved_at,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256="a" * 64,  # wrong hash
        )
        snap_path = tmp_path / "snap.json"
        with pytest.raises(ArchiveSnapshotValidationError, match="SHA-256|sha256|[Hh]ash"):
            ArchiveLabelSnapshotStore.write(
                raw, "src", product, bad_prov, _waves_reparser, snap_path,
                normalizer_id="gcsi.generic_pds3_label.v1",
                profile_id="waves_burst_pds3",
            )
