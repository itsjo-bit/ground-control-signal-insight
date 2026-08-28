"""GCSI Phase 6E-C3B — PDS Archive-Label Snapshot Tests.

All tests are COMPLETELY OFFLINE.

A network guard patches socket.socket, socket.create_connection, and
socket.getaddrinfo to fail immediately if any network call is attempted.

Test coverage:
  A. Happy path             (1-10)
  B. Determinism            (11-13)
  C. Raw-byte tampering     (14-16)
  D. Stored normalized-value tampering  (17-23)
  E. Request/identity tampering         (24-26)
  F. Snapshot identity      (27-30)
  G. Encoding/structure     (31-37)
  H. Size / filesystem      (38-40)
  I. Network isolation      (41-43)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx
import pytest
from pydantic import ValidationError

from backend.app.mission_sources.adapters.pds_archive import (
    PdsArchiveLabelAdapter,
    PdsArchiveLabelValidationError,
)
from backend.app.mission_sources.adapters.pds_archive_models import (
    PdsArchiveLabelCapture,
    PdsArchiveLabelRequest,
)
from backend.app.mission_sources.snapshots.pds_archive_snapshot import (
    PdsArchiveSnapshotError,
    PdsArchiveSnapshotStore,
    PdsArchiveSnapshotUnavailableError,
    PdsArchiveSnapshotValidationError,
    _MAX_SNAPSHOT_BYTES,
    _compute_archive_snapshot_id,
    _canonical_retrieved_at,
)
from backend.app.mission_sources.snapshots.pds_archive_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    PdsArchiveSnapshotEnvelope,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_VALID_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04::1.0"
)
_VALID_LABEL_URL = (
    "https://pds-atmospheres.nmsu.edu"
    "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166"
    "/MWR62RI2024166030000_R04112_V04.xml"
)
_VALID_LID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04"
)
_VALID_VERSION = "1.0"
_FIXED_CLOCK_UTC = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
_PDS_NS = "http://pds.nasa.gov/pds4/pds/v1"
_IM_VERSION = "1.7.0.0"


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


class _NetworkBlockedError(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Fail immediately on any socket network call."""

    def _no_socket(*args, **kwargs):
        raise _NetworkBlockedError(
            "Network access is prohibited in PDS archive snapshot tests."
        )

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_socket)
    yield


# ---------------------------------------------------------------------------
# XML label builder (minimal valid label)
# ---------------------------------------------------------------------------


def _make_valid_label_xml(
    lid: str = _VALID_LID,
    version_id: str = _VALID_VERSION,
    title: str = "MWR PJ62 IRDR Calibrated Snapshot Test",
    file_name: str = "MWR62RI2024166030000_R04112_V04.csv",
    file_size: int = 2097152,
) -> bytes:
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Product_Observational xmlns="{_PDS_NS}">
  <Identification_Area>
    <logical_identifier>{lid}</logical_identifier>
    <version_id>{version_id}</version_id>
    <title>{title}</title>
    <information_model_version>{_IM_VERSION}</information_model_version>
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
        <Internal_Reference>
          <lid_reference>urn:nasa:pds:context:instrument:mwr.jno</lid_reference>
          <reference_type>is_instrument</reference_type>
        </Internal_Reference>
      </Observing_System_Component>
      <Observing_System_Component>
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
      <file_name>{file_name}</file_name>
      <file_size unit="byte">{file_size}</file_size>
    </File>
    <Table_Delimited>
    </Table_Delimited>
  </File_Area_Observational>
</Product_Observational>
"""
    return xml.encode("utf-8")


def _make_adapter(
    body: Optional[bytes] = None,
    clock=None,
) -> PdsArchiveLabelAdapter:
    if body is None:
        body = _make_valid_label_xml()
    if clock is None:
        clock = lambda: _FIXED_CLOCK_UTC

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return PdsArchiveLabelAdapter(client=client, clock=clock)


def _make_capture(
    body: Optional[bytes] = None,
    clock=None,
) -> PdsArchiveLabelCapture:
    """Build a valid PdsArchiveLabelCapture using MockTransport."""
    adapter = _make_adapter(body=body, clock=clock)
    req = PdsArchiveLabelRequest(lidvid=_VALID_LIDVID, label_url=_VALID_LABEL_URL)
    return adapter.fetch_capture(req)


# ---------------------------------------------------------------------------
# A. Happy path (tests 1-10)
# ---------------------------------------------------------------------------


class TestHappyPath:
    """A.1–A.10: round-trip write → load succeeds."""

    def test_01_valid_capture_writes_snapshot(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snapshot.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        assert dest.exists()

    def test_02_written_snapshot_exists(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        assert dest.is_file()

    def test_03_load_returns_product_equal_to_capture_product(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        product, _ = PdsArchiveSnapshotStore.load(dest)
        assert product == capture.product

    def test_04_load_returns_provenance_equal_to_capture_provenance(self, tmp_path):
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        _, provenance = PdsArchiveSnapshotStore.load(dest)
        assert provenance == capture.provenance

    def test_05_raw_bytes_survive_write_load_verification(self, tmp_path):
        body = _make_valid_label_xml()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        product, provenance = PdsArchiveSnapshotStore.load(dest)
        computed = hashlib.sha256(capture.raw_label).hexdigest()
        assert computed == provenance.content_sha256
        assert computed == capture.provenance.content_sha256

    def test_06_snapshot_schema_is_correct(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_schema"] == SNAPSHOT_SCHEMA

    def test_07_snapshot_version_is_1(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_version"] == 1

    def test_08_snapshot_id_format_valid(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        import re
        assert re.match(r"^[0-9a-f]{64}$", raw["snapshot_id"])

    def test_09_raw_label_sha256_equals_independent_hash(self, tmp_path):
        body = _make_valid_label_xml()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        raw = json.loads(dest.read_text("utf-8"))
        stored_hash = raw["raw_label_sha256"]
        assert stored_hash == hashlib.sha256(body).hexdigest()

    def test_10_provenance_content_sha256_equals_independent_hash(self, tmp_path):
        body = _make_valid_label_xml()
        capture = _make_capture(body=body)
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        raw = json.loads(dest.read_text("utf-8"))
        prov_sha = raw["provenance"]["content_sha256"]
        assert prov_sha == hashlib.sha256(body).hexdigest()


# ---------------------------------------------------------------------------
# B. Determinism (tests 11-13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """B.11–B.13: snapshot IDs are deterministic and stable."""

    def test_11_same_capture_same_retrieved_at_same_snapshot_id(self, tmp_path):
        body = _make_valid_label_xml()
        clock = lambda: _FIXED_CLOCK_UTC
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock), dest_a)
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock), dest_b)
        raw_a = json.loads(dest_a.read_text())
        raw_b = json.loads(dest_b.read_text())
        assert raw_a["snapshot_id"] == raw_b["snapshot_id"]

    def test_12_different_retrieved_at_produces_different_snapshot_id(self, tmp_path):
        body = _make_valid_label_xml()
        clock_a = lambda: datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
        clock_b = lambda: datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock_a), dest_a)
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock_b), dest_b)
        raw_a = json.loads(dest_a.read_text())
        raw_b = json.loads(dest_b.read_text())
        assert raw_a["snapshot_id"] != raw_b["snapshot_id"]

    def test_13_deterministic_json_serialization_stable(self, tmp_path):
        body = _make_valid_label_xml()
        clock = lambda: _FIXED_CLOCK_UTC
        dest_a = tmp_path / "a.json"
        dest_b = tmp_path / "b.json"
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock), dest_a)
        PdsArchiveSnapshotStore.write(_make_capture(body=body, clock=clock), dest_b)
        assert dest_a.read_bytes() == dest_b.read_bytes()


# ---------------------------------------------------------------------------
# C. Raw-byte tampering (tests 14-16)
# ---------------------------------------------------------------------------


class TestRawByteTampering:
    """C.14–C.16: tampering with raw label content is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_14_changed_raw_content_without_updating_hash_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        tampered = base64.b64decode(raw["raw_label_base64"]) + b"\xff"
        raw["raw_label_base64"] = base64.b64encode(tampered).decode("ascii")
        # raw_label_sha256 still points to original — hash mismatch
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_15_changed_base64_and_envelope_hash_but_not_provenance_hash_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        tampered = base64.b64decode(raw["raw_label_base64"]) + b"\xff"
        tampered_b64 = base64.b64encode(tampered).decode("ascii")
        new_hash = hashlib.sha256(tampered).hexdigest()
        raw["raw_label_base64"] = tampered_b64
        raw["raw_label_sha256"] = new_hash
        # provenance.content_sha256 still has the original hash
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_16_changed_raw_bytes_both_hashes_but_stale_product_rejected(self, tmp_path):
        """Proves hashes alone do NOT create authority; re-validation catches stale data."""
        dest, raw = self._write_and_read_dict(tmp_path)
        # Build a different valid raw payload to get a different hash
        other_body = _make_valid_label_xml(title="TAMPERED Title Different")
        tampered_b64 = base64.b64encode(other_body).decode("ascii")
        new_hash = hashlib.sha256(other_body).hexdigest()
        raw["raw_label_base64"] = tampered_b64
        raw["raw_label_sha256"] = new_hash
        raw["provenance"]["content_sha256"] = new_hash
        # Leave stored product stale
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# D. Stored normalized-value tampering (tests 17-23)
# ---------------------------------------------------------------------------


class TestStoredNormalizedValueTampering:
    """D.17–D.23: tampering with stored product/provenance metadata is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_17_altered_product_title_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["title"] = "TAMPERED TITLE"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_18_altered_processing_level_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["processing_level"] = "Raw"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_19_altered_target_lid_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["target_lids"] = ["urn:nasa:pds:context:target:planet.saturn"]
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_20_altered_data_file_metadata_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        if raw["product"]["data_files"]:
            raw["product"]["data_files"][0]["file_name"] = "tampered.csv"
        else:
            pytest.skip("no data_files in test payload")
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_21_altered_provenance_source_record_id_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["source_record_id"] = "urn:nasa:pds:tampered::9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_22_altered_provenance_validation_status_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["validation_status"] = "pending"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_23_altered_provenance_retrieved_at_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["provenance"]["retrieved_at"] = "2020-01-01T00:00:00+00:00"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# E. Request/identity tampering (tests 24-26)
# ---------------------------------------------------------------------------


class TestRequestIdentityTampering:
    """E.24–E.26: tampering with stored request identity is detected."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_24_altered_request_lidvid_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Must use a structurally valid LIDVID to pass model validation
        raw["request"]["lidvid"] = (
            "urn:nasa:pds:juno_mwr:data_calibrated:"
            "mwr99ri9999999999999_r99999_v99::9.9"
        )
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_25_product_lidvid_inconsistent_with_request_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        # Change stored product lidvid to something different
        raw["product"]["lidvid"] = (
            "urn:nasa:pds:juno_mwr:data_calibrated:"
            "mwr99ri9999999999999_r99999_v99::9.9"
        )
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_26_version_mismatch_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["product"]["version_id"] = "9.9"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# F. Snapshot identity (tests 27-30)
# ---------------------------------------------------------------------------


class TestSnapshotIdentity:
    """F.27–F.30: snapshot_id and schema fields are validated."""

    def _write_and_read_dict(self, tmp_path) -> tuple[Path, dict]:
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        return dest, json.loads(dest.read_text("utf-8"))

    def test_27_altered_snapshot_id_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_id"] = "a" * 64
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_28_wrong_snapshot_schema_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_schema"] = "gcsi.wrong_schema"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_29_unsupported_snapshot_version_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_version"] = 99
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_30_malformed_snapshot_id_format_rejected(self, tmp_path):
        dest, raw = self._write_and_read_dict(tmp_path)
        raw["snapshot_id"] = "not-a-sha256-value!!"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# G. Encoding/structure (tests 31-37)
# ---------------------------------------------------------------------------


class TestEncodingStructure:
    """G.31–G.37: malformed or structurally invalid snapshot files."""

    def test_31_invalid_base64_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        raw["raw_label_base64"] = "not_valid_base64!!@@##"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError, match="Base64"):
            PdsArchiveSnapshotStore.load(dest)

    def test_32_malformed_json_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_text("{ not valid json }", encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError, match="JSON"):
            PdsArchiveSnapshotStore.load(dest)

    def test_33_non_object_json_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError, match="object"):
            PdsArchiveSnapshotStore.load(dest)

    def test_34_wrong_schema_name_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        raw["snapshot_schema"] = "gcsi.pds_science_product_snapshot"  # wrong schema
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError, match="schema"):
            PdsArchiveSnapshotStore.load(dest)

    def test_35_missing_snapshot_schema_key_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        del raw["snapshot_schema"]
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)

    def test_36_non_utf8_file_rejected(self, tmp_path):
        dest = tmp_path / "snap.json"
        dest.write_bytes(b"\xff\xfe not utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError, match="UTF-8"):
            PdsArchiveSnapshotStore.load(dest)

    def test_37_snapshot_uses_raw_label_base64_key(self, tmp_path):
        """Archive snapshot uses raw_label_base64 key (not raw_response_base64)."""
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert "raw_label_base64" in raw
        assert "raw_label_sha256" in raw
        assert "raw_response_base64" not in raw
        assert "raw_response_sha256" not in raw


# ---------------------------------------------------------------------------
# H. Size / filesystem (tests 38-40)
# ---------------------------------------------------------------------------


class TestSizeFilesystem:
    """H.38–H.40: size and filesystem checks."""

    def test_38_missing_file_raises_unavailable_error(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(PdsArchiveSnapshotUnavailableError):
            PdsArchiveSnapshotStore.load(missing)

    def test_39_snapshot_can_be_written_and_loaded(self, tmp_path):
        dest = tmp_path / "snap.json"
        capture = _make_capture()
        PdsArchiveSnapshotStore.write(capture, dest)
        product, prov = PdsArchiveSnapshotStore.load(dest)
        assert product.lidvid == _VALID_LIDVID

    def test_40_oversized_file_raises_validation_error(self, tmp_path):
        dest = tmp_path / "snap.json"
        # Write a valid snapshot first, then overwrite with giant file
        dest.write_bytes(b"x" * (_MAX_SNAPSHOT_BYTES + 1))
        with pytest.raises(PdsArchiveSnapshotValidationError, match="size"):
            PdsArchiveSnapshotStore.load(dest)


# ---------------------------------------------------------------------------
# I. Network isolation (tests 41-43)
# ---------------------------------------------------------------------------


class TestNetworkIsolation:
    """I.41–I.43: snapshot load/write must not use the network."""

    def test_41_write_is_offline(self, tmp_path):
        """PdsArchiveSnapshotStore.write() must not make any network call."""
        # The autouse _block_network fixture will raise if any network is attempted.
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        # This should succeed — no network needed for write.
        PdsArchiveSnapshotStore.write(capture, dest)
        assert dest.exists()

    def test_42_load_is_offline(self, tmp_path):
        """PdsArchiveSnapshotStore.load() must not make any network call."""
        capture = _make_capture()
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        # Load should succeed without network.
        product, prov = PdsArchiveSnapshotStore.load(dest)
        assert product is not None

    def test_43_adapter_uses_mock_transport_not_real_network(self):
        """Verify that _make_capture() uses MockTransport (no real network call)."""
        # If MockTransport is not in place, the _block_network fixture would
        # catch the real network call and raise _NetworkBlockedError.
        # This test simply calls _make_capture() to confirm no exception.
        capture = _make_capture()
        assert capture is not None


# ---------------------------------------------------------------------------
# Additional integrity checks
# ---------------------------------------------------------------------------


class TestAdditionalIntegrity:
    """Additional integrity and round-trip checks."""

    def test_snapshot_schema_matches_constant(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_schema"] == SNAPSHOT_SCHEMA

    def test_snapshot_version_matches_constant(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["snapshot_version"] == SNAPSHOT_VERSION

    def test_snapshot_id_is_sha256_hex(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        import re
        assert re.match(r"^[0-9a-f]{64}$", raw["snapshot_id"])

    def test_request_lidvid_preserved_in_snapshot(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["request"]["lidvid"] == _VALID_LIDVID

    def test_request_label_url_preserved_in_snapshot(self, tmp_path):
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        assert raw["request"]["label_url"] == _VALID_LABEL_URL

    def test_retrieved_at_matches_clock(self, tmp_path):
        clock = lambda: _FIXED_CLOCK_UTC
        capture = _make_capture(clock=clock)
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(capture, dest)
        raw = json.loads(dest.read_text("utf-8"))
        stored_retrieved_at = raw["retrieved_at"]
        # Should be the UTC ISO representation of the fixed clock value
        assert "2026-08-27" in stored_retrieved_at

    def test_altered_retrieved_at_in_envelope_rejected(self, tmp_path):
        """retrieved_at in envelope must match provenance.retrieved_at."""
        dest = tmp_path / "snap.json"
        PdsArchiveSnapshotStore.write(_make_capture(), dest)
        raw = json.loads(dest.read_text("utf-8"))
        raw["retrieved_at"] = "2020-01-01T00:00:00+00:00"
        dest.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(PdsArchiveSnapshotValidationError):
            PdsArchiveSnapshotStore.load(dest)
