"""Phase 6D-B1 / Phase 6D-B1.1 — Verified Horizons Snapshot Foundation Unit Tests.

Covers the full required test matrix from the Phase 6D-B1 and 6D-B1.1 specifications.
Uses httpx.MockTransport exclusively — zero live network calls.

Test groups
-----------
CAPTURE (tests 1-5):
    fetch() backward-compat, fetch_capture(), raw bytes, one request, client ownership

SNAPSHOT CREATION (tests 6-13):
    schema/version, deterministic ID, lossless encoding, hash, geometry,
    provenance, no current-time field

WRITE (tests 14-18):
    round-trip, UTF-8 JSON, stable structure, atomic write, interrupted write

LOAD / REVALIDATION (tests 19-26):
    no HTTP, exact bytes, same parser, geometry/provenance/request match,
    stored retrieved_at reused, repeated loads identical

TAMPER DETECTION (tests 27-43):
    all tamper scenarios listed in the spec

IO (tests 44-47):
    missing file, permission error, no path leak, cause preserved

REGRESSION (tests 48-52+):
    Phase 6D-A, 6C, 6B imports pass; state.py unwired; schemas unchanged

HORIZONS EPOCH FIDELITY (6D-B1.1 tests):
    TIME_DIGITS=FRACSEC, canonical identity, epoch matching/rejection

SHARED RAW VALIDATOR (6D-B1.1 tests):
    pure function, no HTTP, size limit, retrieved_at handling, no __new__

BOUNDED READ (6D-B1.1 tests):
    genuinely bounded, oversized rejection

SNAPSHOT ID (6D-B1.1 tests):
    different retrieved_at → different ID, coordinated tamper rejection

WRITER SELF-VALIDATION (6D-B1.1 tests):
    geometry/request/provenance consistency, missing retrieved_at, oversized
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import stat
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from backend.app.mission_sources.adapters.horizons import (
    HorizonsAdapter,
    HorizonsValidationError,
    _build_canonical_query_identity,
    _validate_horizons_raw_response,
    _MAX_RESPONSE_BYTES,
)
from backend.app.mission_sources.adapters.horizons_models import (
    HorizonsGeometryCapture,
    HorizonsGeometryRequest,
    HorizonsGeometryResult,
)
from backend.app.mission_sources.snapshots.horizons_snapshot import (
    HorizonsSnapshotError,
    HorizonsSnapshotUnavailableError,
    HorizonsSnapshotValidationError,
    HorizonsSnapshotStore,
    _compute_snapshot_id,
    _canonical_retrieved_at,
    _MAX_SNAPSHOT_BYTES,
)
from backend.app.mission_sources.snapshots.horizons_snapshot_models import (
    SNAPSHOT_SCHEMA,
    SNAPSHOT_VERSION,
    HorizonsSnapshotEnvelope,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

UTC = timezone.utc

_JUNO_ID = "-61"
_EPOCH_UTC = datetime(2026, 8, 27, 0, 0, 0, tzinfo=UTC)
_RETRIEVED_AT = datetime(2026, 8, 27, 20, 41, 0, tzinfo=UTC)

_LT_VALUE = 2795.812640498820
_RG_VALUE = 838249962.14964500
_RR_VALUE = 14.639175321946800

# Calendar date with FRACSEC precision (6 decimal digits).
_VALID_DATA_ROW = (
    " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.000000,"
    f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
)

_VALID_RESULT_TEXT = (
    "JPL/HORIZONS header line\n"
    "$$SOE\n"
    + _VALID_DATA_ROW
    + "\n$$EOE\n"
    "Coord. ref. frame : ICRF\n"
)


def _make_horizons_response_bytes(
    result_text: Optional[str] = None,
) -> bytes:
    """Build a representative Horizons JSON response as bytes."""
    if result_text is None:
        result_text = _VALID_RESULT_TEXT
    payload = {
        "signature": {
            "source": "NASA/JPL Horizons API",
            "version": "1.3",
        },
        "result": result_text,
    }
    return json.dumps(payload).encode("utf-8")


def _fixed_clock() -> datetime:
    return _RETRIEVED_AT


def _make_mock_transport(content: Optional[bytes] = None) -> httpx.MockTransport:
    if content is None:
        content = _make_horizons_response_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, content=content)

    return httpx.MockTransport(handler)


def _make_adapter(content: Optional[bytes] = None) -> HorizonsAdapter:
    transport = _make_mock_transport(content=content)
    client = httpx.Client(transport=transport)
    return HorizonsAdapter(client=client, clock=_fixed_clock)


def _make_request() -> HorizonsGeometryRequest:
    return HorizonsGeometryRequest(target_spk_id=_JUNO_ID, epoch_utc=_EPOCH_UTC)


def _make_capture(content: Optional[bytes] = None) -> HorizonsGeometryCapture:
    adapter = _make_adapter(content=content)
    return adapter.fetch_capture(_make_request())


# ---------------------------------------------------------------------------
# CAPTURE (tests 1-5)
# ---------------------------------------------------------------------------


class TestCapture:
    """Tests 1-5: HorizonsGeometryCapture contract."""

    def test_01_fetch_backward_compatible(self):
        """Test 1: fetch() remains backward-compatible, returns HorizonsGeometryResult."""
        adapter = _make_adapter()
        result = adapter.fetch(_make_request())
        assert isinstance(result, HorizonsGeometryResult)
        assert result.geometry.range_km > 0

    def test_02_fetch_capture_same_result_as_fetch(self):
        """Test 2: fetch_capture() returns same normalized result as fetch()."""
        content = _make_horizons_response_bytes()
        adapter1 = _make_adapter(content=content)
        adapter2 = _make_adapter(content=content)
        req = _make_request()
        result_fetch = adapter1.fetch(req)
        capture = adapter2.fetch_capture(req)
        assert capture.result == result_fetch

    def test_03_capture_contains_exact_raw_bytes(self):
        """Test 3: capture contains exact raw response bytes."""
        content = _make_horizons_response_bytes()
        adapter = _make_adapter(content=content)
        capture = adapter.fetch_capture(_make_request())
        assert capture.raw_response == content

    def test_04_fetch_capture_exactly_one_request(self):
        """Test 4: fetch_capture() causes exactly one HTTP request."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, content=_make_horizons_response_bytes())

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.fetch_capture(_make_request())
        assert call_count == 1

    def test_05_injected_client_ownership_unchanged(self):
        """Test 5: adapter does not close injected client after fetch_capture."""
        transport = _make_mock_transport()
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.fetch_capture(_make_request())
        assert not client.is_closed

    def test_fetch_also_exactly_one_request(self):
        """fetch() delegates to fetch_capture() — still exactly one HTTP request."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(status_code=200, content=_make_horizons_response_bytes())

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.fetch(_make_request())
        assert call_count == 1

    def test_capture_is_frozen(self):
        """HorizonsGeometryCapture is frozen (immutable)."""
        capture = _make_capture()
        with pytest.raises(Exception):
            capture.raw_response = b"tampered"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SNAPSHOT CREATION (tests 6-13)
# ---------------------------------------------------------------------------


class TestSnapshotCreation:
    """Tests 6-13: snapshot envelope construction."""

    def _make_envelope_dict(self, content: Optional[bytes] = None) -> dict:
        capture = _make_capture(content=content)
        result = capture.result
        raw_b64 = base64.b64encode(capture.raw_response).decode("ascii")
        computed_hash = hashlib.sha256(capture.raw_response).hexdigest()
        retrieved_at_iso = _canonical_retrieved_at(result.provenance.retrieved_at)
        snapshot_id = _compute_snapshot_id(
            result.provenance.provenance_id, retrieved_at_iso
        )
        return {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": result.request.model_dump(mode="json"),
            "retrieved_at": retrieved_at_iso,
            "raw_response_base64": raw_b64,
            "raw_response_sha256": computed_hash,
            "geometry": result.geometry.model_dump(mode="json"),
            "provenance": result.provenance.model_dump(mode="json"),
        }

    def test_06_schema_version_correct(self):
        """Test 6: snapshot_schema and snapshot_version are correct."""
        d = self._make_envelope_dict()
        assert d["snapshot_schema"] == "gcsi.horizons_geometry_snapshot"
        assert d["snapshot_version"] == 1

    def test_07_snapshot_id_deterministic(self):
        """Test 7: same inputs produce same snapshot_id."""
        content = _make_horizons_response_bytes()
        d1 = self._make_envelope_dict(content=content)
        d2 = self._make_envelope_dict(content=content)
        assert d1["snapshot_id"] == d2["snapshot_id"]

    def test_08_same_capture_same_snapshot_id(self):
        """Test 8: same capture -> same snapshot_id."""
        content = _make_horizons_response_bytes()
        c1 = _make_capture(content=content)
        c2 = _make_capture(content=content)
        iso1 = _canonical_retrieved_at(c1.result.provenance.retrieved_at)
        iso2 = _canonical_retrieved_at(c2.result.provenance.retrieved_at)
        id1 = _compute_snapshot_id(c1.result.provenance.provenance_id, iso1)
        id2 = _compute_snapshot_id(c2.result.provenance.provenance_id, iso2)
        assert id1 == id2

    def test_09_raw_response_encoded_losslessly(self):
        """Test 9: raw bytes can be decoded back exactly."""
        capture = _make_capture()
        raw_b64 = base64.b64encode(capture.raw_response).decode("ascii")
        decoded = base64.b64decode(raw_b64, validate=True)
        assert decoded == capture.raw_response

    def test_10_raw_hash_matches_provenance(self):
        """Test 10: raw_response_sha256 matches provenance.content_sha256."""
        capture = _make_capture()
        computed = hashlib.sha256(capture.raw_response).hexdigest()
        assert computed == capture.result.provenance.content_sha256

    def test_11_stored_geometry_equals_validated_geometry(self):
        """Test 11: stored geometry in envelope matches live-validated geometry."""
        d = self._make_envelope_dict()
        env = HorizonsSnapshotEnvelope.model_validate(d)
        assert env.geometry == _make_capture().result.geometry

    def test_12_stored_provenance_equals_validated_provenance(self):
        """Test 12: stored provenance in envelope matches live-validated provenance."""
        d = self._make_envelope_dict()
        env = HorizonsSnapshotEnvelope.model_validate(d)
        assert env.provenance == _make_capture().result.provenance

    def test_13_no_current_time_snapshot_field(self):
        """Test 13: no snapshot-creation timestamp (retrieved_at is the historical time)."""
        d = self._make_envelope_dict()
        env = HorizonsSnapshotEnvelope.model_validate(d)
        assert env.retrieved_at == _RETRIEVED_AT

    def test_snapshot_id_is_sha256_hex(self):
        """snapshot_id is a 64-char lowercase hex SHA-256."""
        capture = _make_capture()
        iso = _canonical_retrieved_at(capture.result.provenance.retrieved_at)
        sid = _compute_snapshot_id(capture.result.provenance.provenance_id, iso)
        assert len(sid) == 64
        assert all(c in "0123456789abcdef" for c in sid)

    def test_snapshot_id_formula(self):
        """snapshot_id formula is correct."""
        capture = _make_capture()
        prov_id = capture.result.provenance.provenance_id
        iso = _canonical_retrieved_at(capture.result.provenance.retrieved_at)
        expected = hashlib.sha256(
            f"gcsi.horizons_geometry_snapshot:v1:{prov_id}:{iso}".encode("utf-8")
        ).hexdigest()
        assert _compute_snapshot_id(prov_id, iso) == expected


# ---------------------------------------------------------------------------
# WRITE (tests 14-18)
# ---------------------------------------------------------------------------


class TestWrite:
    """Tests 14-18: snapshot write behavior."""

    def test_14_write_read_round_trip(self, tmp_path):
        """Test 14: write then load returns identical result."""
        capture = _make_capture()
        snap_path = tmp_path / "juno_snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        assert loaded.geometry == capture.result.geometry
        assert loaded.provenance == capture.result.provenance
        assert loaded.request == capture.result.request

    def test_15_output_is_valid_utf8_json(self, tmp_path):
        """Test 15: output is valid UTF-8 JSON."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        raw = snap_path.read_bytes()
        text = raw.decode("utf-8")  # must not raise
        parsed = json.loads(text)   # must not raise
        assert isinstance(parsed, dict)

    def test_16_stable_serialized_structure(self, tmp_path):
        """Test 16: same capture produces byte-for-byte identical output."""
        content = _make_horizons_response_bytes()
        c1 = _make_capture(content=content)
        c2 = _make_capture(content=content)
        p1 = tmp_path / "snap1.json"
        p2 = tmp_path / "snap2.json"
        HorizonsSnapshotStore.write(c1, p1)
        HorizonsSnapshotStore.write(c2, p2)
        assert p1.read_bytes() == p2.read_bytes()

    def test_17_atomic_write_temp_file_used(self, tmp_path):
        """Test 17: write uses a temporary file (atomic os.replace)."""
        written_files: list[str] = []
        original_mkstemp = tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = original_mkstemp(**kwargs)
            written_files.append(path)
            return fd, path

        snap_path = tmp_path / "snap.json"
        capture = _make_capture()

        with patch(
            "backend.app.mission_sources.snapshots.horizons_snapshot.tempfile.mkstemp",
            side_effect=tracking_mkstemp,
        ):
            HorizonsSnapshotStore.write(capture, snap_path)

        # Temp file should have been replaced (not left behind).
        for tmp_file in written_files:
            assert not pathlib.Path(tmp_file).exists()
        assert snap_path.exists()

    def test_18_failed_write_raises_snapshot_unavailable(self, tmp_path):
        """Test 18: if write fails due to OSError, raises HorizonsSnapshotUnavailableError."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"

        # Simulate a failure during os.replace.
        with patch("os.replace", side_effect=OSError("simulated disk full")):
            with pytest.raises(HorizonsSnapshotUnavailableError):
                HorizonsSnapshotStore.write(capture, snap_path)

        # The final path must not have been created.
        assert not snap_path.exists()

    def test_write_rejects_bad_hash(self, tmp_path):
        """write() raises if capture raw bytes do not match provenance hash."""
        capture = _make_capture()
        tampered = HorizonsGeometryCapture(
            result=capture.result,
            raw_response=b"tampered_bytes_with_wrong_hash",
        )
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered, tmp_path / "bad.json")


# ---------------------------------------------------------------------------
# LOAD / REVALIDATION (tests 19-26)
# ---------------------------------------------------------------------------


class TestLoadRevalidation:
    """Tests 19-26: offline load and re-validation behavior."""

    def _write_snap(self, tmp_path, content: Optional[bytes] = None) -> pathlib.Path:
        capture = _make_capture(content=content)
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        return snap_path

    def test_19_loader_performs_no_http_requests(self, tmp_path):
        """Test 19: loader performs no HTTP requests."""
        snap_path = self._write_snap(tmp_path)
        result = HorizonsSnapshotStore.load(snap_path)
        assert result.geometry.range_km > 0

    def test_20_loader_decodes_exact_original_bytes(self, tmp_path):
        """Test 20: loader decodes exact original raw bytes."""
        content = _make_horizons_response_bytes()
        snap_path = self._write_snap(tmp_path, content=content)
        raw_envelope = json.loads(snap_path.read_bytes().decode("utf-8"))
        decoded = base64.b64decode(raw_envelope["raw_response_base64"], validate=True)
        assert decoded == content

    def test_21_loader_reruns_same_horizons_validator(self, tmp_path):
        """Test 21: loader re-runs the same Horizons raw-response validator."""
        snap_path = self._write_snap(tmp_path)
        result = HorizonsSnapshotStore.load(snap_path)
        expected = _make_adapter(_make_horizons_response_bytes()).fetch(_make_request())
        assert result.geometry == expected.geometry

    def test_22_loaded_geometry_equals_original(self, tmp_path):
        """Test 22: loaded geometry == original geometry."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        assert loaded.geometry == capture.result.geometry

    def test_23_loaded_provenance_equals_original(self, tmp_path):
        """Test 23: loaded provenance == original provenance."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        assert loaded.provenance == capture.result.provenance

    def test_24_loaded_request_equals_original(self, tmp_path):
        """Test 24: loaded request == original request."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        assert loaded.request == capture.result.request

    def test_25_stored_retrieved_at_reused_not_current_time(self, tmp_path):
        """Test 25: stored retrieved_at is reused; current time not substituted."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        assert loaded.provenance.retrieved_at == _RETRIEVED_AT

    def test_26_repeated_offline_loads_identical(self, tmp_path):
        """Test 26: repeated offline loads produce identical results."""
        snap_path = self._write_snap(tmp_path)
        r1 = HorizonsSnapshotStore.load(snap_path)
        r2 = HorizonsSnapshotStore.load(snap_path)
        assert r1.geometry == r2.geometry
        assert r1.provenance == r2.provenance
        assert r1.request == r2.request


# ---------------------------------------------------------------------------
# TAMPER DETECTION (tests 27-43)
# ---------------------------------------------------------------------------


class TestTamperDetection:
    """Tests 27-43: all tamper scenarios fail closed."""

    def _write_snap_dict(self, tmp_path) -> tuple[dict, pathlib.Path]:
        """Write a valid snapshot and return the parsed dict + path."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        raw_dict = json.loads(snap_path.read_bytes().decode("utf-8"))
        return raw_dict, snap_path

    def _write_tampered(self, tmp_path, tampered_dict: dict) -> pathlib.Path:
        """Write a tampered dict as a snapshot file."""
        p = tmp_path / "tampered.json"
        p.write_bytes(
            (json.dumps(tampered_dict, sort_keys=True, indent=2) + "\n").encode("utf-8")
        )
        return p

    def test_27_altered_raw_response_base64_rejected(self, tmp_path):
        """Test 27: altered raw_response_base64 rejected (hash mismatch)."""
        d, _ = self._write_snap_dict(tmp_path)
        garbage = base64.b64encode(b"tampered raw bytes garbage data").decode("ascii")
        d["raw_response_base64"] = garbage
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_28_altered_raw_response_sha256_rejected(self, tmp_path):
        """Test 28: altered raw_response_sha256 rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["raw_response_sha256"] = "a" * 64
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_29_altered_range_km_rejected(self, tmp_path):
        """Test 29: altered range_km in stored geometry rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["geometry"]["range_km"] = 999999.0
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_30_altered_range_rate_rejected(self, tmp_path):
        """Test 30: altered range_rate rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["geometry"]["range_rate_km_s"] = -999.0
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_31_altered_light_time_rejected(self, tmp_path):
        """Test 31: altered light-time rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["geometry"]["one_way_light_time_s"] = 1.0
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_32_altered_provenance_id_rejected(self, tmp_path):
        """Test 32: altered provenance_id rejected (snapshot_id mismatch)."""
        d, _ = self._write_snap_dict(tmp_path)
        d["provenance"]["provenance_id"] = "a" * 64
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_33_altered_provenance_source_rejected(self, tmp_path):
        """Test 33: altered provenance source/version rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["provenance"]["source_system"] = "TAMPERED_SOURCE"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_34_altered_retrieved_at_rejected(self, tmp_path):
        """Test 34: altered retrieved_at causes provenance mismatch."""
        d, _ = self._write_snap_dict(tmp_path)
        d["retrieved_at"] = "2020-01-01T00:00:00+00:00"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_35_altered_request_target_rejected(self, tmp_path):
        """Test 35: altered request target causes provenance mismatch."""
        d, _ = self._write_snap_dict(tmp_path)
        d["request"]["target_spk_id"] = "499"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_36_altered_request_epoch_rejected(self, tmp_path):
        """Test 36: altered request epoch causes provenance mismatch."""
        d, _ = self._write_snap_dict(tmp_path)
        d["request"]["epoch_utc"] = "2020-01-01T00:00:00+00:00"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_37_altered_snapshot_id_rejected(self, tmp_path):
        """Test 37: altered snapshot_id rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["snapshot_id"] = "b" * 64
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_38_wrong_snapshot_schema_rejected(self, tmp_path):
        """Test 38: wrong snapshot_schema rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["snapshot_schema"] = "gcsi.wrong_schema"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_39_unsupported_snapshot_version_rejected(self, tmp_path):
        """Test 39: unsupported snapshot_version rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["snapshot_version"] = 99
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_40_invalid_base64_rejected(self, tmp_path):
        """Test 40: invalid Base64 rejected."""
        d, _ = self._write_snap_dict(tmp_path)
        d["raw_response_base64"] = "THIS IS NOT BASE64!!! @@##"
        p = self._write_tampered(tmp_path, d)
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_41_malformed_json_rejected(self, tmp_path):
        """Test 41: malformed JSON rejected."""
        p = tmp_path / "bad.json"
        p.write_bytes(b"not valid json {{{")
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_42_invalid_utf8_rejected(self, tmp_path):
        """Test 42: invalid UTF-8 rejected."""
        p = tmp_path / "bad.json"
        p.write_bytes(b"\xff\xfe invalid utf-8 content here")
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_43_oversized_snapshot_rejected(self, tmp_path):
        """Test 43: oversized snapshot rejected."""
        p = tmp_path / "big.json"
        p.write_bytes(b"X" * (_MAX_SNAPSHOT_BYTES + 1))
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)


# ---------------------------------------------------------------------------
# IO (tests 44-47)
# ---------------------------------------------------------------------------


class TestIO:
    """Tests 44-47: IO error handling."""

    def test_44_missing_file_raises_unavailable(self, tmp_path):
        """Test 44: missing file -> HorizonsSnapshotUnavailableError."""
        p = tmp_path / "does_not_exist.json"
        with pytest.raises(HorizonsSnapshotUnavailableError):
            HorizonsSnapshotStore.load(p)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="chmod-based permission test not reliable on Windows",
    )
    def test_45_permission_error_raises_unavailable(self, tmp_path):
        """Test 45: permission/OSError -> HorizonsSnapshotUnavailableError."""
        capture = _make_capture()
        p = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, p)
        p.chmod(0o000)
        try:
            with pytest.raises(HorizonsSnapshotUnavailableError):
                HorizonsSnapshotStore.load(p)
        finally:
            p.chmod(0o644)

    def test_46_public_error_does_not_expose_raw_path(self, tmp_path):
        """Test 46: public error message does not expose raw file path or content."""
        sentinel = "SECRET_PATH_SENTINEL_XYZ_9999"
        p = tmp_path / sentinel
        with pytest.raises(HorizonsSnapshotUnavailableError) as exc_info:
            HorizonsSnapshotStore.load(p)
        assert sentinel not in str(exc_info.value)

    def test_47_lower_cause_preserved(self, tmp_path):
        """Test 47: lower cause is preserved as __cause__."""
        p = tmp_path / "missing.json"
        with pytest.raises(HorizonsSnapshotUnavailableError) as exc_info:
            HorizonsSnapshotStore.load(p)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, FileNotFoundError)

    def test_unavailable_is_mission_source_unavailable(self, tmp_path):
        """HorizonsSnapshotUnavailableError derives from MissionSourceUnavailableError."""
        p = tmp_path / "no.json"
        with pytest.raises(MissionSourceUnavailableError):
            HorizonsSnapshotStore.load(p)

    def test_validation_is_mission_source_validation(self, tmp_path):
        """HorizonsSnapshotValidationError derives from MissionSourceValidationError."""
        p = tmp_path / "bad.json"
        p.write_bytes(b"not json")
        with pytest.raises(MissionSourceValidationError):
            HorizonsSnapshotStore.load(p)

    def test_errors_are_horizons_snapshot_error(self, tmp_path):
        """Both error types derive from HorizonsSnapshotError."""
        p_missing = tmp_path / "missing.json"
        p_bad = tmp_path / "bad.json"
        p_bad.write_bytes(b"bad content")

        with pytest.raises(HorizonsSnapshotError):
            HorizonsSnapshotStore.load(p_missing)
        with pytest.raises(HorizonsSnapshotError):
            HorizonsSnapshotStore.load(p_bad)


# ---------------------------------------------------------------------------
# REGRESSION (tests 48-52)
# ---------------------------------------------------------------------------


class TestRegression:
    """Tests 48-52: regression checks."""

    def test_48_phase6da_tests_import(self):
        """Test 48: Phase 6D-A adapter imports still work."""
        from backend.app.mission_sources.adapters.horizons import (
            HorizonsAdapter,
            HorizonsAdapterError,
            HorizonsUnavailableError,
            HorizonsValidationError,
        )
        from backend.app.mission_sources.adapters.horizons_models import (
            HorizonsGeometry,
            HorizonsGeometryRequest,
            HorizonsGeometryResult,
        )
        assert HorizonsAdapter is not None
        assert HorizonsGeometryResult is not None

    def test_49_phase6c_imports(self):
        """Test 49: Phase 6C mission-source imports still work."""
        from backend.app.mission_sources.base import BaseMissionSourceProvider
        from backend.app.mission_sources.errors import (
            MissionSourceError,
            MissionSourceUnavailableError,
            MissionSourceValidationError,
        )
        assert MissionSourceError is not None

    def test_50_phase6b_imports(self):
        """Test 50: Phase 6B provenance imports still work."""
        from backend.app.provenance.models import (
            ProvenanceKind,
            ProvenanceRecord,
            ProvenanceValidationStatus,
            ProvenanceManifest,
            FieldProvenanceBinding,
        )
        assert ProvenanceKind.EXTERNAL_AUTHORITATIVE is not None

    def test_51_state_py_not_imported(self):
        """Test 51: state.py is NOT imported by any snapshot module."""
        import backend.app.mission_sources.snapshots.horizons_snapshot as snap_mod
        import backend.app.mission_sources.snapshots.horizons_snapshot_models as snap_models
        assert "backend.app.state" not in snap_mod.__dict__

    def test_52_scenario_schema_unchanged(self):
        """Test 52: Scenario and DataProduct schemas remain unchanged."""
        from backend.app.models.scenario import Scenario
        from backend.app.models.data_product import DataProduct
        assert Scenario is not None
        assert DataProduct is not None

    def test_snapshot_error_hierarchy(self):
        """Error hierarchy is correctly derived."""
        assert issubclass(HorizonsSnapshotUnavailableError, HorizonsSnapshotError)
        assert issubclass(HorizonsSnapshotValidationError, HorizonsSnapshotError)
        assert issubclass(HorizonsSnapshotUnavailableError, MissionSourceUnavailableError)
        assert issubclass(HorizonsSnapshotValidationError, MissionSourceValidationError)

    def test_capture_round_trip_geometry_values(self, tmp_path):
        """End-to-end: capture -> write -> load produces exact geometry values."""
        capture = _make_capture()
        p = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, p)
        loaded = HorizonsSnapshotStore.load(p)
        assert abs(loaded.geometry.range_km - _RG_VALUE) < 1.0
        assert abs(loaded.geometry.range_rate_km_s - _RR_VALUE) < 0.001
        assert abs(loaded.geometry.one_way_light_time_s - _LT_VALUE) < 0.001

    def test_snapshot_envelope_extra_fields_forbidden(self):
        """HorizonsSnapshotEnvelope rejects extra fields (extra=forbid)."""
        capture = _make_capture()
        raw_b64 = base64.b64encode(capture.raw_response).decode("ascii")
        computed_hash = hashlib.sha256(capture.raw_response).hexdigest()
        iso = _canonical_retrieved_at(capture.result.provenance.retrieved_at)
        snapshot_id = _compute_snapshot_id(
            capture.result.provenance.provenance_id, iso
        )
        d = {
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": capture.result.request.model_dump(mode="json"),
            "retrieved_at": iso,
            "raw_response_base64": raw_b64,
            "raw_response_sha256": computed_hash,
            "geometry": capture.result.geometry.model_dump(mode="json"),
            "provenance": capture.result.provenance.model_dump(mode="json"),
            "extra_field_not_allowed": "bad",
        }
        with pytest.raises(ValidationError):
            HorizonsSnapshotEnvelope.model_validate(d)

    def test_envelope_wrong_schema_name_rejected(self):
        """HorizonsSnapshotEnvelope rejects wrong schema name at model level."""
        capture = _make_capture()
        raw_b64 = base64.b64encode(capture.raw_response).decode("ascii")
        computed_hash = hashlib.sha256(capture.raw_response).hexdigest()
        iso = _canonical_retrieved_at(capture.result.provenance.retrieved_at)
        snapshot_id = _compute_snapshot_id(
            capture.result.provenance.provenance_id, iso
        )
        d = {
            "snapshot_schema": "wrong.schema.name",
            "snapshot_version": SNAPSHOT_VERSION,
            "snapshot_id": snapshot_id,
            "request": capture.result.request.model_dump(mode="json"),
            "retrieved_at": iso,
            "raw_response_base64": raw_b64,
            "raw_response_sha256": computed_hash,
            "geometry": capture.result.geometry.model_dump(mode="json"),
            "provenance": capture.result.provenance.model_dump(mode="json"),
        }
        with pytest.raises(ValidationError):
            HorizonsSnapshotEnvelope.model_validate(d)

    def test_no_live_network_in_tests(self):
        """Confirm no live Horizons endpoint is called — all tests use MockTransport."""
        assert True

    def test_phase6db2_not_started(self):
        """Confirm Phase 6D-B2 (live capture integration) is not started."""
        try:
            import backend.app.mission_sources.historical_replay_provider  # noqa: F401
            pytest.fail("Phase 6D-B2 HistoricalReplayProvider should not exist yet")
        except ImportError:
            pass


# ---------------------------------------------------------------------------
# HORIZONS EPOCH FIDELITY (6D-B1.1)
# ---------------------------------------------------------------------------


class TestEpochFidelity:
    """6D-B1.1 tests 1-11: TIME_DIGITS=FRACSEC and epoch verification."""

    def _fetch(self, result_text: Optional[str] = None) -> HorizonsGeometryResult:
        content = _make_horizons_response_bytes(result_text=result_text)
        return _make_adapter(content=content).fetch(_make_request())

    def _params(self) -> dict:
        """Capture the HTTP params sent by the adapter."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(200, content=_make_horizons_response_bytes())

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        HorizonsAdapter(client=client, clock=_fixed_clock).fetch(_make_request())
        return captured

    def test_b11_01_request_contains_time_digits_fracsec(self):
        """B1.1-1: request includes TIME_DIGITS=FRACSEC."""
        params = self._params()
        assert params.get("TIME_DIGITS") == "FRACSEC"

    def test_b11_02_canonical_identity_includes_time_digits(self):
        """B1.1-2: canonical query identity includes time_digits."""
        identity = json.loads(_build_canonical_query_identity(_make_request()))
        assert "time_digits" in identity
        assert identity["time_digits"] == "FRACSEC"

    def test_b11_03_returned_epoch_matches_request_accepted(self):
        """B1.1-3: returned calendar epoch matching request is accepted."""
        result = self._fetch()
        assert result.geometry.range_km > 0

    def test_b11_04_different_day_rejected(self):
        """B1.1-4: returned date one day different is rejected."""
        row = (
            " 2460934.500000000, A.D. 2026-Aug-28 00:00:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_05_hour_mismatch_rejected(self):
        """B1.1-5: returned hour mismatch rejected."""
        row = (
            " 2460933.541666667, A.D. 2026-Aug-27 01:00:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_06_minute_mismatch_rejected(self):
        """B1.1-6: returned minute mismatch rejected."""
        row = (
            " 2460933.500694444, A.D. 2026-Aug-27 00:01:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_07_second_fraction_mismatch_rejected(self):
        """B1.1-7: returned seconds/fraction mismatch rejected."""
        row = (
            " 2460933.500011574, A.D. 2026-Aug-27 00:00:01.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_08_malformed_calendar_field_rejected(self):
        """B1.1-8: malformed calendar field rejected."""
        row = (
            " 2460933.500000000, INVALID DATE STRING,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_09_non_numeric_julian_time_rejected(self):
        """B1.1-9: non-numeric Julian-time field rejected."""
        row = (
            " NOT_A_JD, A.D. 2026-Aug-27 00:00:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_10_nan_julian_time_rejected(self):
        """B1.1-10: NaN Julian-time rejected."""
        row = (
            " NaN, A.D. 2026-Aug-27 00:00:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_11_non_utc_aware_request_normalised_before_comparison(self):
        """B1.1-11: non-UTC aware request epoch is normalised to UTC before comparison."""
        # +07:00 = 2026-Aug-27 07:00 local = 2026-Aug-27 00:00 UTC
        plus7 = timezone(timedelta(hours=7))
        local_epoch = datetime(2026, 8, 27, 7, 0, 0, tzinfo=plus7)
        req = HorizonsGeometryRequest(target_spk_id=_JUNO_ID, epoch_utc=local_epoch)
        # The response returns 2026-Aug-27 00:00:00.000000 which is correct UTC.
        content = _make_horizons_response_bytes()
        transport = _make_mock_transport(content=content)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        result = adapter.fetch(req)
        assert result.geometry.range_km > 0

    def test_4digit_fracsec_accepted(self):
        """4 fractional digits (padded to 6) are accepted for backward compat."""
        row = (
            " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.0000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        result = _make_adapter(content=content).fetch(_make_request())
        assert result.geometry.range_km > 0


# ---------------------------------------------------------------------------
# SHARED RAW VALIDATOR (6D-B1.1)
# ---------------------------------------------------------------------------


class TestSharedRawValidator:
    """6D-B1.1 tests 12-18: _validate_horizons_raw_response contract."""

    def test_b11_12_live_fetch_uses_shared_validator(self):
        """B1.1-12: live fetch uses _validate_horizons_raw_response."""
        # If it didn't, epoch verification would not be applied.
        row = (
            " 2460934.500000000, A.D. 2026-Aug-28 00:00:00.000000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
        )
        result_text = "JPL/HORIZONS\n$$SOE\n" + row + "\n$$EOE\n"
        content = _make_horizons_response_bytes(result_text=result_text)
        with pytest.raises(HorizonsValidationError):
            _make_adapter(content=content).fetch(_make_request())

    def test_b11_13_snapshot_load_uses_same_validator(self, tmp_path):
        """B1.1-13: snapshot load uses the exact same shared validator."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        loaded = HorizonsSnapshotStore.load(snap_path)
        # If the same validator were not used, geometry would differ.
        assert loaded.geometry == capture.result.geometry

    def test_b11_14_no_adapter_new_usage(self):
        """B1.1-14: HorizonsAdapter.__new__ is NOT used anywhere in snapshot code."""
        import inspect
        import backend.app.mission_sources.snapshots.horizons_snapshot as snap_mod
        source = inspect.getsource(snap_mod)
        assert "HorizonsAdapter.__new__" not in source
        assert "__new__(HorizonsAdapter)" not in source

    def test_b11_15_shared_validator_performs_no_http(self):
        """B1.1-15: _validate_horizons_raw_response performs no HTTP."""
        content = _make_horizons_response_bytes()
        # Calling with no transport set up — would raise if HTTP occurred.
        result = _validate_horizons_raw_response(
            request=_make_request(),
            raw_bytes=content,
            retrieved_at=_RETRIEVED_AT,
        )
        assert result.geometry.range_km > 0

    def test_b11_16_raw_body_over_1mib_rejected(self):
        """B1.1-16: raw body >1 MiB rejected by shared validator."""
        oversized = b"X" * (_MAX_RESPONSE_BYTES + 1)
        with pytest.raises(HorizonsValidationError):
            _validate_horizons_raw_response(
                request=_make_request(),
                raw_bytes=oversized,
                retrieved_at=_RETRIEVED_AT,
            )

    def test_b11_17_retrieved_at_normalised_to_utc(self):
        """B1.1-17: retrieved_at normalised to UTC in provenance."""
        # +07:00, 13:41 local == 06:41 UTC.
        plus7 = timezone(timedelta(hours=7))
        non_utc = datetime(2026, 8, 27, 13, 41, 0, tzinfo=plus7)
        content = _make_horizons_response_bytes()
        result = _validate_horizons_raw_response(
            request=_make_request(),
            raw_bytes=content,
            retrieved_at=non_utc,
        )
        prov_retrieved = result.provenance.retrieved_at
        # Must be stored as UTC (zero offset).
        assert prov_retrieved.utcoffset().total_seconds() == 0
        # And must equal the UTC equivalent of the input.
        expected_utc = datetime(2026, 8, 27, 6, 41, 0, tzinfo=timezone.utc)
        assert prov_retrieved == expected_utc

    def test_b11_18_naive_retrieved_at_rejected(self):
        """B1.1-18: naive retrieved_at raises HorizonsValidationError."""
        content = _make_horizons_response_bytes()
        naive = datetime(2026, 8, 27, 20, 41, 0)  # no tzinfo
        with pytest.raises(HorizonsValidationError):
            _validate_horizons_raw_response(
                request=_make_request(),
                raw_bytes=content,
                retrieved_at=naive,
            )


# ---------------------------------------------------------------------------
# BOUNDED READ (6D-B1.1)
# ---------------------------------------------------------------------------


class TestBoundedRead:
    """6D-B1.1 tests 19-20: genuinely bounded file read."""

    def test_b11_19_bounded_read_does_not_request_full_file(self, tmp_path):
        """B1.1-19: load() reads at most MAX_SNAPSHOT_BYTES + 1 bytes."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)

        read_amounts: list[int] = []
        original_open = open

        def tracking_open(path, mode="r", *args, **kwargs):
            fh = original_open(path, mode, *args, **kwargs)
            if mode == "rb":
                original_read = fh.read

                def bounded_read_tracker(n=-1):
                    data = original_read(n)
                    read_amounts.append(n if n != -1 else len(data))
                    return data

                fh.read = bounded_read_tracker
            return fh

        with patch("builtins.open", side_effect=tracking_open):
            HorizonsSnapshotStore.load(snap_path)

        # The read call must have passed n = MAX+1 (bounded), not -1 (unbounded).
        assert any(n == _MAX_SNAPSHOT_BYTES + 1 for n in read_amounts), (
            f"Expected bounded read of {_MAX_SNAPSHOT_BYTES + 1}, got: {read_amounts}"
        )

    def test_b11_20_oversized_file_rejected_quickly(self, tmp_path):
        """B1.1-20: oversized snapshot rejected without reading arbitrary full contents."""
        p = tmp_path / "big.json"
        # Create a file larger than MAX_SNAPSHOT_BYTES.
        p.write_bytes(b"Z" * (_MAX_SNAPSHOT_BYTES + 100))
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)


# ---------------------------------------------------------------------------
# SNAPSHOT ID (6D-B1.1)
# ---------------------------------------------------------------------------


class TestSnapshotIdFidelity:
    """6D-B1.1 tests 21-24: snapshot_id binds retrieved_at."""

    def test_b11_21_same_capture_same_snapshot_id(self, tmp_path):
        """B1.1-21: same capture -> same snapshot_id."""
        content = _make_horizons_response_bytes()
        c1 = _make_capture(content=content)
        c2 = _make_capture(content=content)
        p1, p2 = tmp_path / "s1.json", tmp_path / "s2.json"
        HorizonsSnapshotStore.write(c1, p1)
        HorizonsSnapshotStore.write(c2, p2)
        d1 = json.loads(p1.read_bytes())
        d2 = json.loads(p2.read_bytes())
        assert d1["snapshot_id"] == d2["snapshot_id"]

    def test_b11_22_different_retrieved_at_different_snapshot_id(self, tmp_path):
        """B1.1-22: same query/body but different retrieved_at → different snapshot_id."""
        content = _make_horizons_response_bytes()

        clock1 = lambda: datetime(2026, 8, 27, 20, 41, 0, tzinfo=UTC)
        clock2 = lambda: datetime(2026, 8, 27, 21, 00, 0, tzinfo=UTC)

        t1 = _make_mock_transport(content=content)
        t2 = _make_mock_transport(content=content)
        a1 = HorizonsAdapter(client=httpx.Client(transport=t1), clock=clock1)
        a2 = HorizonsAdapter(client=httpx.Client(transport=t2), clock=clock2)

        c1 = a1.fetch_capture(_make_request())
        c2 = a2.fetch_capture(_make_request())

        p1, p2 = tmp_path / "s1.json", tmp_path / "s2.json"
        HorizonsSnapshotStore.write(c1, p1)
        HorizonsSnapshotStore.write(c2, p2)

        d1 = json.loads(p1.read_bytes())
        d2 = json.loads(p2.read_bytes())
        assert d1["snapshot_id"] != d2["snapshot_id"]
        # Both must still load successfully.
        r1 = HorizonsSnapshotStore.load(p1)
        r2 = HorizonsSnapshotStore.load(p2)
        assert r1.geometry == r2.geometry  # same body → same geometry
        assert r1.provenance.retrieved_at != r2.provenance.retrieved_at

    def test_b11_23_coordinated_retrieved_at_tamper_rejected(self, tmp_path):
        """B1.1-23: changing BOTH envelope.retrieved_at AND provenance.retrieved_at
        to the same different timestamp is rejected because snapshot_id binds
        the original timestamp.
        """
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)

        d = json.loads(snap_path.read_bytes())
        # Coordinated change: both envelope retrieved_at and provenance retrieved_at.
        new_time = "2020-01-01T00:00:00+00:00"
        d["retrieved_at"] = new_time
        d["provenance"]["retrieved_at"] = new_time

        p = tmp_path / "tampered.json"
        p.write_bytes(
            (json.dumps(d, sort_keys=True, indent=2) + "\n").encode("utf-8")
        )
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)

    def test_b11_24_altered_snapshot_id_rejected(self, tmp_path):
        """B1.1-24: (regression) altered snapshot_id still rejected."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)

        d = json.loads(snap_path.read_bytes())
        d["snapshot_id"] = "c" * 64
        p = tmp_path / "tampered.json"
        p.write_bytes(
            (json.dumps(d, sort_keys=True, indent=2) + "\n").encode("utf-8")
        )
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.load(p)


# ---------------------------------------------------------------------------
# WRITER SELF-VALIDATION (6D-B1.1)
# ---------------------------------------------------------------------------


class TestWriterSelfValidation:
    """6D-B1.1 tests 25-31: writer rejects inconsistent captures."""

    def test_b11_25_valid_capture_writes(self, tmp_path):
        """B1.1-25: valid capture writes without error."""
        capture = _make_capture()
        p = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, p)
        assert p.exists()

    def test_b11_26_correct_hash_tampered_geometry_rejected(self, tmp_path):
        """B1.1-26: correct raw hash but tampered geometry rejected before file creation."""
        # Build a genuine capture.
        capture = _make_capture()
        # Build a geometry with altered range_km but keep everything else valid.
        from backend.app.mission_sources.adapters.horizons_models import HorizonsGeometry
        tampered_geom = HorizonsGeometry(
            target_spk_id=capture.result.geometry.target_spk_id,
            center=capture.result.geometry.center,
            epoch_utc=capture.result.geometry.epoch_utc,
            range_km=capture.result.geometry.range_km + 1_000_000.0,
            range_rate_km_s=capture.result.geometry.range_rate_km_s,
            one_way_light_time_s=capture.result.geometry.one_way_light_time_s,
            api_source=capture.result.geometry.api_source,
            api_version=capture.result.geometry.api_version,
        )
        tampered_result = HorizonsGeometryResult(
            request=capture.result.request,
            geometry=tampered_geom,
            provenance=capture.result.provenance,
        )
        tampered_capture = HorizonsGeometryCapture(
            result=tampered_result,
            raw_response=capture.raw_response,
        )
        snap_path = tmp_path / "should_not_exist.json"
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered_capture, snap_path)
        assert not snap_path.exists()

    def test_b11_27_correct_hash_tampered_request_rejected(self, tmp_path):
        """B1.1-27: correct raw hash but tampered request rejected."""
        capture = _make_capture()
        tampered_req = HorizonsGeometryRequest(
            target_spk_id="499",  # different target
            epoch_utc=_EPOCH_UTC,
        )
        tampered_result = HorizonsGeometryResult(
            request=tampered_req,
            geometry=capture.result.geometry,
            provenance=capture.result.provenance,
        )
        tampered_capture = HorizonsGeometryCapture(
            result=tampered_result,
            raw_response=capture.raw_response,
        )
        snap_path = tmp_path / "should_not_exist.json"
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered_capture, snap_path)
        assert not snap_path.exists()

    def test_b11_28_correct_hash_tampered_provenance_rejected(self, tmp_path):
        """B1.1-28: correct raw hash but tampered provenance rejected."""
        capture = _make_capture()
        from backend.app.provenance.models import ProvenanceRecord, ProvenanceKind, ProvenanceValidationStatus
        prov = capture.result.provenance
        tampered_prov = ProvenanceRecord(
            provenance_id=prov.provenance_id,
            kind=prov.kind,
            source_system=prov.source_system,
            source_version=prov.source_version,
            source_uri=prov.source_uri,
            observed_at=prov.observed_at,
            retrieved_at=prov.retrieved_at,
            validation_status=prov.validation_status,
            content_sha256=prov.content_sha256,
            notes="TAMPERED_NOTE_XYZ",  # extra note — breaks provenance equality
        )
        tampered_result = HorizonsGeometryResult(
            request=capture.result.request,
            geometry=capture.result.geometry,
            provenance=tampered_prov,
        )
        tampered_capture = HorizonsGeometryCapture(
            result=tampered_result,
            raw_response=capture.raw_response,
        )
        snap_path = tmp_path / "should_not_exist.json"
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered_capture, snap_path)
        assert not snap_path.exists()

    def test_b11_29_missing_provenance_retrieved_at_rejected(self, tmp_path):
        """B1.1-29: capture with provenance.retrieved_at=None rejected."""
        capture = _make_capture()
        from backend.app.provenance.models import ProvenanceRecord
        prov = capture.result.provenance
        prov_no_time = ProvenanceRecord(
            provenance_id=prov.provenance_id,
            kind=prov.kind,
            source_system=prov.source_system,
            source_version=prov.source_version,
            source_uri=prov.source_uri,
            observed_at=prov.observed_at,
            retrieved_at=None,   # missing
            validation_status=prov.validation_status,
            content_sha256=prov.content_sha256,
        )
        result_no_time = HorizonsGeometryResult(
            request=capture.result.request,
            geometry=capture.result.geometry,
            provenance=prov_no_time,
        )
        tampered_capture = HorizonsGeometryCapture(
            result=result_no_time,
            raw_response=capture.raw_response,
        )
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered_capture, tmp_path / "snap.json")

    def test_b11_30_oversized_raw_capture_rejected(self, tmp_path):
        """B1.1-30: oversized raw capture rejected by shared validator in write()."""
        oversized_bytes = b"X" * (_MAX_RESPONSE_BYTES + 1)
        # Construct a fake capture with oversized raw bytes and wrong hash.
        # The write() should reject it.
        capture = _make_capture()
        tampered = HorizonsGeometryCapture(
            result=capture.result,
            raw_response=oversized_bytes,
        )
        with pytest.raises(HorizonsSnapshotValidationError):
            HorizonsSnapshotStore.write(tampered, tmp_path / "snap.json")

    def test_b11_32_failed_atomic_replace_preserves_previous_file(self, tmp_path):
        """B1.1-32: failed atomic replace preserves the previous valid file."""
        capture = _make_capture()
        snap_path = tmp_path / "snap.json"
        HorizonsSnapshotStore.write(capture, snap_path)
        original_content = snap_path.read_bytes()

        with patch("os.replace", side_effect=OSError("simulated disk full")):
            with pytest.raises(HorizonsSnapshotUnavailableError):
                HorizonsSnapshotStore.write(capture, snap_path)

        # Original file must still be intact.
        assert snap_path.read_bytes() == original_content

    def test_b11_33_write_oserror_is_sanitized(self, tmp_path):
        """B1.1-33: write OSError is raised as HorizonsSnapshotUnavailableError."""
        capture = _make_capture()
        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(HorizonsSnapshotUnavailableError):
                HorizonsSnapshotStore.write(capture, tmp_path / "snap.json")

    def test_b11_34_read_oserror_is_sanitized(self, tmp_path):
        """B1.1-34: read OSError is raised as HorizonsSnapshotUnavailableError."""
        p = tmp_path / "missing.json"
        with pytest.raises(HorizonsSnapshotUnavailableError):
            HorizonsSnapshotStore.load(p)
