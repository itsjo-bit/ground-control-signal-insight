"""GCSI Phase 6E-D0 — PDS Verified Snapshot Tests.

All tests are COMPLETELY OFFLINE.

A network guard patches socket.socket, socket.create_connection, and
socket.getaddrinfo to fail immediately if any network call is attempted.

Test coverage:
  A. Happy path             (1–10)
  B. Determinism            (11–13)
  C. Raw-byte tampering     (14–16)
  D. Stored normalized-value tampering  (17–23)
  E. Request/identity tampering         (24–26)
  F. Snapshot identity      (27–30)
  G. Encoding/structure     (31–37)
  H. Size / filesystem      (38–42)
  I. Network isolation      (43–45)
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import socket
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from unittest.mock import patch, MagicMock

import httpx
import pytest
from pydantic import ValidationError

from backend.app.mission_sources.adapters.pds import (
    PdsRegistryAdapter,
    PdsValidationError,
)
from backend.app.mission_sources.adapters.pds_models import (
    PdsProductRequest,
    PdsScienceProduct,
    PdsScienceProductCapture,
)
from backend.app.mission_sources.snapshots.pds_snapshot import (
    PdsSnapshotError,
    PdsSnapshotStore,
    PdsSnapshotUnavailableError,
    PdsSnapshotValidationError,
    _MAX_SNAPSHOT_BYTES,
    _compute_snapshot_id,
    _canonical_retrieved_at,
)
from backend.app.mission_sources.snapshots.pds_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    PdsSnapshotEnvelope,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_VALID_LIDVID = "urn:nasa:pds:test_gcsi_bundle:data_raw:test_obs_001::1.0"
_VALID_LID = "urn:nasa:pds:test_gcsi_bundle:data_raw:test_obs_001"
_VALID_VERSION = "1.0"
_FIXED_CLOCK_UTC = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


class _NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail immediately on any socket network call in this test module."""

    def _no_socket(*args, **kwargs):
        raise _NetworkBlockedError(
            "Network access is prohibited in PDS snapshot tests."
        )

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)
    yield


# ---------------------------------------------------------------------------
# Synthetic PDS response builder
# ---------------------------------------------------------------------------


def _make_valid_kvp_payload(
    lidvid: str = _VALID_LIDVID,
    lid: str = _VALID_LID,
    version_id: str = _VALID_VERSION,
    title: str = "Test MWR Calibrated Science Product",
    processing_level: str = "Calibrated",
) -> dict:
    data_item: dict = {
        "lid": lid,
        "lidvid": lidvid,
        "product_class": "Product_Observational",
        "title": title,
        "pds:Identification_Area.pds:logical_identifier": lid,
        "pds:Identification_Area.pds:version_id": version_id,
        "pds:Identification_Area.pds:title": title,
        "pds:Identification_Area.pds:product_class": "Product_Observational",
        "pds:Time_Coordinates.pds:start_date_time": "2024-06-13T14:00:00Z",
        "pds:Time_Coordinates.pds:stop_date_time": "2024-06-13T15:00:00Z",
        "pds:Primary_Result_Summary.pds:processing_level": processing_level,
        "ref_lid_instrument": ["urn:nasa:pds:context:instrument:mwr.jno"],
        "ref_lid_instrument_host": ["urn:nasa:pds:context:instrument_host:spacecraft.jno"],
        "ref_lid_investigation": ["urn:nasa:pds:context:investigation:mission.juno"],
        "ref_lid_target": ["urn:nasa:pds:context:target:planet.jupiter"],
        "ops:Data_File_Info.ops:file_name": ["jno_mwr_rdr_2024165t140000_v01.csv"],
        "ops:Data_File_Info.ops:file_ref": ["https://pds.nasa.gov/data/jno_mwr_rdr_2024165t140000_v01.csv"],
        "ops:Data_File_Info.ops:file_size": ["2097152"],
        "ops:Data_File_Info.ops:md5_checksum": ["d41d8cd98f00b204e9800998ecf8427e"],
        "ops:Data_File_Info.ops:mime_type": ["text/csv"],
        "ops:Harvest_Info.ops:node_name": "PDS_ATM",
        "ops:Harvest_Info.ops:harvest_date_time": "2026-08-01T12:00:00Z",
    }
    return {
        "summary": {"hits": 1, "q": "*", "start": 0, "limit": 1},
        "data": [data_item],
    }


def _make_response_bytes(payload: Optional[dict] = None) -> bytes:
    if payload is None:
        payload = _make_valid_kvp_payload()
    return json.dumps(payload).encode("utf-8")


def _make_adapter(
    body: Optional[bytes] = None,
    clock=None,
) -> PdsRegistryAdapter:
    if body is None:
        body = _make_response_bytes()
    if clock is None:
        clock = lambda: _FIXED_CLOCK_UTC

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return PdsRegistryAdapter(client=client, clock=clock)


def _make_capture(
    body: Optional[bytes] = None,
    clock=None,
) -> PdsScienceProductCapture:
    """Build a valid PdsScienceProductCapture using MockTransport."""
    adapter = _make_adapter(body=body, clock=clock)
    req = PdsProductRequest(lidvid=_VALID_LIDVID)
    return adapter.fetch_capture(req)


# ---------------------------------------------------------------------------
# A. Happy path (tests 1-10)
# ---------------------------------------------------------------------------


class TestHappyPath:
    """A.1–A.10: round-trip write → load succeeds."""

    def test_01_valid_capture_writes_snapshot(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snapshot.json"
        PdsSnapshotStore.write(capture, dest)
        assert dest.exists()

    def test_02_written_snapshot_exists(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        assert dest.is_file()

    def test_03_load_returns_product_equal_to_capture_product(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        product, _ = PdsSnapshotStore.load(dest)
        assert product == capture.product

    def test_04_load_returns_provenance_equal_to_capture_provenance(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        _, provenance = PdsSnapshotStore.load(dest)
        assert provenance == capture.provenance

    def test_05_raw_bytes_survive_write_load_verification(self, tmp_path):
        body = _make_response_bytes()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        # After loading, the re-derived product/provenance must match the originals.
        product, provenance = PdsSnapshotStore.load(dest)
        # Verify both hash properties
        computed = hashlib.sha256(capture.raw_response).hexdigest()
        assert computed == provenance.content_sha256
        assert computed == capture.provenance.content_sha256

    def test_06_snapshot_schema_is_correct(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_schema"] == "gcsi.pds_science_product_snapshot"

    def test_07_snapshot_version_is_1(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_version"] == 1

    def test_08_snapshot_id_format_valid(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        sid = raw["snapshot_id"]
        import re
        assert re.match(r"^[0-9a-f]{64}$", sid)

    def test_09_raw_response_sha256_equals_independent_hash(self, tmp_path):
        body = _make_response_bytes()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        raw = json.loads(dest.read_text("utf-8"))
        stored_hash = raw["raw_response_sha256"]
        assert stored_hash == hashlib.sha256(body).hexdigest()

    def test_10_provenance_content_sha256_equals_independent_hash(self, tmp_path):
        body = _make_response_bytes()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        raw = json.loads(dest.read_text("utf-8"))
        prov_sha = raw["provenance"]["content_sha256"]
        assert prov_sha == hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# B. Determinism (tests 11-13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """B.11–B.13: snapshot IDs are deterministic and stable."""

    def test_11_same_capture_same_retrieved_at_same_snapshot_id(self, tmp_path):
        body = _make_response_bytes()
        clock = lambda: _FIXED_CLOCK_UTC
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock), dest_a)
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock), dest_b)
        raw_a = json.loads(dest_a.read_text())
        raw_b = json.loads(dest_b.read_text())
        assert raw_a["snapshot_id"] == raw_b["snapshot_id"]

    def test_12_different_retrieved_at_produces_different_snapshot_id(self, tmp_path):
        body = _make_response_bytes()
        clock_a = lambda: datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
        clock_b = lambda: datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock_a), dest_a)
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock_b), dest_b)
        raw_a = json.loads(dest_a.read_text())
        raw_b = json.loads(dest_b.read_text())
        assert raw_a["snapshot_id"] != raw_b["snapshot_id"]

    def test_13_deterministic_json_serialization_stable(self, tmp_path):
        body = _make_response_bytes()
        clock = lambda: _FIXED_CLOCK_UTC
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock), dest_a)
        PdsSnapshotStore.write(_make_capture(body=body, clock=clock), dest_b)
        assert dest_a.read_bytes() == dest_b.read_bytes()


# ---------------------------------------------------------------------------
# C. Raw-byte tampering (tests 14-16)
# ---------------------------------------------------------------------------


class TestRawByteTampering:
    """C.14–C.16: tampering with raw response content is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_14_changed_raw_content_without_updating_hash_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Replace base64 with tampered bytes, keep hashes unchanged
        tampered = base64.b64decode(raw["raw_response_base64"]) + b"\xff"
        raw["raw_response_base64"] = base64.b64encode(tampered).decode("ascii")
        # raw_response_sha256 still points to original — hash mismatch
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_15_changed_base64_and_envelope_hash_but_not_provenance_hash_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        tampered = base64.b64decode(raw["raw_response_base64"]) + b"\xff"
        tampered_b64 = base64.b64encode(tampered).decode("ascii")
        new_hash = hashlib.sha256(tampered).hexdigest()
        raw["raw_response_base64"] = tampered_b64
        raw["raw_response_sha256"] = new_hash
        # provenance.content_sha256 still has the original hash
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_16_changed_raw_bytes_both_hashes_but_stale_product_rejected(self, tmp_path):
        """Proves hashes alone do NOT create authority; re-validation catches stale data."""
        dest, raw = self._write_and_read_dict(tmp_path)
        # Build a different valid raw payload to get a different hash
        other_body = _make_response_bytes(
            _make_valid_kvp_payload(title="TAMPERED Title Different")
        )
        tampered_b64 = base64.b64encode(other_body).decode("ascii")
        new_hash = hashlib.sha256(other_body).hexdigest()
        raw["raw_response_base64"] = tampered_b64
        raw["raw_response_sha256"] = new_hash
        # Also update provenance.content_sha256 to match new hash
        raw["provenance"]["content_sha256"] = new_hash
        # Leave stored product and provenance_id stale
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# D. Stored normalized-value tampering (tests 17-23)
# ---------------------------------------------------------------------------


class TestStoredNormalizedValueTampering:
    """D.17–D.23: tampering with stored product/provenance metadata is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_17_altered_product_title_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["title"] = "TAMPERED TITLE"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_18_altered_processing_level_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["processing_level"] = "Raw"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_19_altered_target_lid_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["target_lids"] = ["urn:nasa:pds:context:target:planet.saturn"]
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_20_altered_data_file_metadata_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        if raw["product"]["data_files"]:
            raw["product"]["data_files"][0]["file_name"] = "tampered.csv"
        else:
            pytest.skip("no data_files in test payload")
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_21_altered_provenance_source_record_id_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["source_record_id"] = "urn:nasa:pds:tampered::9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_22_altered_provenance_validation_status_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["validation_status"] = "pending"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_23_altered_provenance_retrieved_at_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["retrieved_at"] = "2020-01-01T00:00:00+00:00"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# E. Request/identity tampering (tests 24-26)
# ---------------------------------------------------------------------------


class TestRequestIdentityTampering:
    """E.24–E.26: tampering with stored request identity is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_24_altered_request_lidvid_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["request"]["lidvid"] = "urn:nasa:pds:other:data:other::9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_25_product_lidvid_inconsistent_with_request_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Change stored product lidvid without changing request
        raw["product"]["lidvid"] = "urn:nasa:pds:other:data:other::9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_26_version_mismatch_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Alter version_id in product — will fail stored vs re-derived comparison
        raw["product"]["version_id"] = "9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# F. Snapshot identity (tests 27-30)
# ---------------------------------------------------------------------------


class TestSnapshotIdentity:
    """F.27–F.30: snapshot_id and schema fields are validated."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_27_altered_snapshot_id_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_id"] = "a" * 64
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_28_wrong_snapshot_schema_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_schema"] = "gcsi.wrong_schema"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_29_unsupported_snapshot_version_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_version"] = 99
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_30_malformed_snapshot_id_format_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Replace snapshot_id with non-hex content
        raw["snapshot_id"] = "not-a-sha256-value!!"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# G. Encoding/structure (tests 31-37)
# ---------------------------------------------------------------------------


class TestEncodingAndStructure:
    """G.31–G.37: structural and encoding validation."""

    def test_31_malformed_utf8_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_bytes(b"\xff\xfe\x00invalid")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_32_malformed_json_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_33_top_level_non_object_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_34_malformed_base64_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        raw["raw_response_base64"] = "!!!not-valid-base64!!!"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_35_base64_with_invalid_characters_rejected(self, tmp_path):
        """validate=True in b64decode rejects whitespace/garbage."""
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        # Valid Base64 with embedded whitespace — validate=True must reject this
        b64_with_space = raw["raw_response_base64"][:10] + " " + raw["raw_response_base64"][10:]
        raw["raw_response_base64"] = b64_with_space
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_36_missing_required_envelope_field_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        del raw["raw_response_base64"]
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_37_unknown_extra_envelope_field_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        raw["unknown_injected_field"] = "evil"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# H. Size / filesystem (tests 38-42)
# ---------------------------------------------------------------------------


class TestSizeAndFilesystem:
    """H.38–H.42: size limits and filesystem error handling."""

    def test_38_oversized_snapshot_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        # Write a file that is exactly MAX + 1 bytes
        dest.write_bytes(b"x" * (_MAX_SNAPSHOT_BYTES + 1))
        with pytest.raises(PdsSnapshotValidationError):
            PdsSnapshotStore.load(dest)

    def test_39_missing_snapshot_path_raises_unavailable(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(PdsSnapshotUnavailableError):
            PdsSnapshotStore.load(missing)

    def test_40_filesystem_read_failure_normalized_to_unavailable(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_text("{}", encoding="utf-8")
        with patch("builtins.open", side_effect=OSError("disk error")):
            with pytest.raises(PdsSnapshotUnavailableError):
                PdsSnapshotStore.load(dest)

    def test_41_filesystem_write_failure_normalized_to_unavailable(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        with patch("tempfile.mkstemp", side_effect=OSError("no space")):
            with pytest.raises(PdsSnapshotUnavailableError):
                PdsSnapshotStore.write(capture, dest)

    def test_42_no_partial_file_after_failed_write(self, tmp_path):
        """After a write failure, the destination should not exist (atomic write)."""
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        # Simulate failure during write by patching os.replace
        with patch("os.replace", side_effect=OSError("replace failed")):
            with pytest.raises((PdsSnapshotUnavailableError, OSError)):
                PdsSnapshotStore.write(capture, dest)
        # The destination should not have been atomically promoted
        assert not dest.exists()


# ---------------------------------------------------------------------------
# I. Network isolation (tests 43-45)
# ---------------------------------------------------------------------------


class TestNetworkIsolation:
    """I.43–I.45: load() performs zero network activity."""

    def test_43_load_succeeds_under_zero_network_guard(self, tmp_path):
        """Network guard is active; load() must succeed without network."""
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        # The autouse _block_network fixture blocks all sockets.
        # load() must complete successfully without touching the network.
        product, provenance = PdsSnapshotStore.load(dest)
        assert product == capture.product
        assert provenance == capture.provenance

    def test_44_load_twice_produces_identical_results(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        result_a = PdsSnapshotStore.load(dest)
        result_b = PdsSnapshotStore.load(dest)
        assert result_a[0] == result_b[0]
        assert result_a[1] == result_b[1]

    def test_45_no_adapter_transport_method_invoked_by_load(self, tmp_path):
        """Loading a snapshot must never invoke PdsRegistryAdapter transport methods."""
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(capture, dest)
        # Confirm load works without touching any httpx transport
        with patch.object(
            PdsRegistryAdapter,
            "_execute_request",
            side_effect=AssertionError("_execute_request must not be called during load"),
        ):
            product, provenance = PdsSnapshotStore.load(dest)
        assert product == capture.product
        assert provenance == capture.provenance


# ---------------------------------------------------------------------------
# Additional snapshot model unit tests
# ---------------------------------------------------------------------------


class TestPdsSnapshotEnvelope:
    """Unit tests for PdsSnapshotEnvelope field validation."""

    def _base_envelope_dict(self) -> dict:
        """Build a minimal valid envelope dict by writing and reading back a real snapshot."""
        body = _make_response_bytes()
        adapter = _make_adapter(body=body)
        req = PdsProductRequest(lidvid=_VALID_LIDVID)
        capture = adapter.fetch_capture(req)
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "snap.json"
            PdsSnapshotStore.write(capture, dest)
            return json.loads(dest.read_text("utf-8"))

    def test_snapshot_schema_must_equal_constant(self):
        d = self._base_envelope_dict()
        d["snapshot_schema"] = "wrong"
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_snapshot_version_must_equal_constant(self):
        d = self._base_envelope_dict()
        d["snapshot_version"] = 2
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_snapshot_id_must_be_64_hex_chars(self):
        d = self._base_envelope_dict()
        d["snapshot_id"] = "tooshort"
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_raw_response_sha256_must_be_64_hex_chars(self):
        d = self._base_envelope_dict()
        d["raw_response_sha256"] = "ABCDEF"
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_retrieved_at_must_be_timezone_aware(self):
        d = self._base_envelope_dict()
        d["retrieved_at"] = "2026-08-27T12:00:00"  # naive
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_extra_fields_forbidden(self):
        d = self._base_envelope_dict()
        d["extra_injected"] = "bad"
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)

    def test_missing_product_rejected(self):
        d = self._base_envelope_dict()
        del d["product"]
        with pytest.raises(ValidationError):
            PdsSnapshotEnvelope.model_validate(d)


class TestSnapshotIdFunction:
    """Unit tests for _compute_snapshot_id and _canonical_retrieved_at."""

    def test_compute_snapshot_id_returns_64_hex(self):
        sid = _compute_snapshot_id("test_prov_id", "2026-08-27T12:00:00+00:00")
        import re
        assert re.match(r"^[0-9a-f]{64}$", sid)

    def test_compute_snapshot_id_deterministic(self):
        a = _compute_snapshot_id("pid", "2026-08-27T12:00:00+00:00")
        b = _compute_snapshot_id("pid", "2026-08-27T12:00:00+00:00")
        assert a == b

    def test_compute_snapshot_id_differs_with_different_prov_id(self):
        a = _compute_snapshot_id("pid_a", "2026-08-27T12:00:00+00:00")
        b = _compute_snapshot_id("pid_b", "2026-08-27T12:00:00+00:00")
        assert a != b

    def test_compute_snapshot_id_differs_with_different_timestamp(self):
        a = _compute_snapshot_id("pid", "2026-08-27T12:00:00+00:00")
        b = _compute_snapshot_id("pid", "2026-08-28T12:00:00+00:00")
        assert a != b

    def test_canonical_retrieved_at_utc_normalises(self):
        dt_utc = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
        dt_offset = datetime(2026, 8, 27, 19, 0, 0, tzinfo=timezone(timedelta(hours=7)))
        assert _canonical_retrieved_at(dt_utc) == _canonical_retrieved_at(dt_offset)


class TestWriteValidationEdgeCases:
    """Write-path validation edge cases."""

    def test_write_rejects_missing_retrieved_at(self, tmp_path):
        """Capture with provenance.retrieved_at=None must fail write validation.

        We build this directly via model fields rather than through fetch_capture
        because fetch_capture always supplies retrieved_at.
        """
        body = _make_response_bytes()
        # First get a valid capture, then tamper its provenance
        adapter = _make_adapter(body=body)
        capture = adapter.fetch_capture(PdsProductRequest(lidvid=_VALID_LIDVID))
        # Build a provenance record with retrieved_at=None
        prov_dict = capture.provenance.model_dump()
        prov_dict["retrieved_at"] = None
        bad_prov = ProvenanceRecord(**prov_dict)

        # The PdsScienceProductCapture model itself enforces retrieved_at,
        # so building a capture with None retrieved_at is already rejected.
        with pytest.raises(ValidationError):
            PdsScienceProductCapture(
                request=capture.request,
                product=capture.product,
                provenance=bad_prov,
                raw_response=body,
            )

    def test_write_snapshot_has_newline_at_eof(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        content = dest.read_bytes()
        assert content.endswith(b"\n")

    def test_write_snapshot_is_valid_utf8(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        content = dest.read_bytes()
        content.decode("utf-8")  # must not raise

    def test_write_snapshot_json_is_sorted_keys(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsSnapshotStore.write(_make_capture(), dest)
        text = dest.read_text("utf-8")
        data = json.loads(text)
        keys = list(data.keys())
        assert keys == sorted(keys)


class TestErrorHierarchy:
    """Verify error hierarchy is correct."""

    def test_pds_snapshot_unavailable_is_subclass_of_snapshot_error(self):
        assert issubclass(PdsSnapshotUnavailableError, PdsSnapshotError)

    def test_pds_snapshot_validation_is_subclass_of_snapshot_error(self):
        assert issubclass(PdsSnapshotValidationError, PdsSnapshotError)

    def test_pds_snapshot_unavailable_is_mission_source_unavailable(self):
        from backend.app.mission_sources.errors import MissionSourceUnavailableError
        assert issubclass(PdsSnapshotUnavailableError, MissionSourceUnavailableError)

    def test_pds_snapshot_validation_is_mission_source_validation(self):
        from backend.app.mission_sources.errors import MissionSourceValidationError
        assert issubclass(PdsSnapshotValidationError, MissionSourceValidationError)
