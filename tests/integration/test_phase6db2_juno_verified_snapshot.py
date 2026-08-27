"""GCSI Phase 6D-B2 — Offline regression test for the verified Juno Horizons snapshot.

This test is COMPLETELY OFFLINE.  It loads only the committed snapshot
artifact that was captured in Phase 6D-B2 Attempt #2.

No live HTTP request is made.  A network guard is installed to fail
immediately if any attempted HTTP transport/network access occurs.

Captured artifact:
    data/verified_snapshots/horizons/juno/juno_spk_-61_2024-06-13T000000Z.json

Attempt history:
    Attempt #1  : REJECTED — API version 1.2 was not yet supported; no snapshot committed.
    Phase 6D-B2A: compatibility review — 1.2 and 1.3 added to allow-list; no live request.
    Attempt #2  : SUCCESS — API version 1.2 returned; snapshot committed here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOT_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "horizons"
    / "juno"
    / "juno_spk_-61_2024-06-13T000000Z.json"
)

# ---------------------------------------------------------------------------
# Import GCSI production modules
# ---------------------------------------------------------------------------

sys.path.insert(0, str(_ROOT))

from backend.app.mission_sources.snapshots.horizons_snapshot import (
    HorizonsSnapshotStore,
)
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus

# ---------------------------------------------------------------------------
# Exact constants locked from Attempt #2 capture
# (Must not be modified without a new authoritative live capture.)
# ---------------------------------------------------------------------------

_EXPECTED_RANGE_KM: float = 893315711.6078479
_EXPECTED_RANGE_RATE_KM_S: float = -2.452294661456788
_EXPECTED_ONE_WAY_LIGHT_TIME_S: float = 2979.780470687651
_EXPECTED_PROVENANCE_ID: str = (
    "6ba3b6a4878275b2cdb12051d96f44d38f19ddf36ae0899508107915fa472e7c"
)
_EXPECTED_CONTENT_SHA256: str = (
    "5300316d60a15e5954148192672edd8d325252a688676e6e772811c5fea28fc7"
)
_EXPECTED_SNAPSHOT_ID: str = (
    "794a6d257d5ae49e172e6dfc888aaaaa2d6ddcd2a7ea1ad492d8561ade39c3e2"
)
_EXPECTED_API_VERSION: str = "1.2"  # Exact version returned by Attempt #2


# ---------------------------------------------------------------------------
# Zero-network guard fixture
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    """Immediately fail if any socket connection is attempted."""
    raise RuntimeError(
        "GCSI offline test guard: network access is forbidden in this test. "
        "Any attempt to open a socket would violate the zero-network guarantee."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Block all network access for every test in this module."""
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Helper: load snapshot (used in multiple tests)
# ---------------------------------------------------------------------------


def _load() -> object:
    """Load and fully re-validate the committed Juno snapshot."""
    return HorizonsSnapshotStore.load(_SNAPSHOT_PATH)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotSchemaAndIdentity:
    """Verify schema identity fields in the raw envelope."""

    def test_snapshot_schema(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_schema"] == "gcsi.horizons_geometry_snapshot"

    def test_snapshot_version(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_version"] == 1

    def test_snapshot_id_locked(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_id"] == _EXPECTED_SNAPSHOT_ID


class TestRequestFields:
    """Verify the request section of the loaded snapshot."""

    def test_target_spk_id(self):
        result = _load()
        assert result.request.target_spk_id == "-61"

    def test_epoch_utc(self):
        result = _load()
        expected = datetime(2024, 6, 13, 0, 0, 0, tzinfo=timezone.utc)
        assert result.request.epoch_utc == expected


class TestGeometryFields:
    """Verify geometry field values exactly match the committed capture."""

    def test_center(self):
        result = _load()
        assert result.geometry.center == "500@399"

    def test_api_source(self):
        result = _load()
        assert result.geometry.api_source == "NASA/JPL Horizons API"

    def test_api_version_locked(self):
        """Lock the EXACT version returned by Attempt #2 (1.2)."""
        result = _load()
        assert result.geometry.api_version == _EXPECTED_API_VERSION

    def test_range_km_positive(self):
        result = _load()
        assert result.geometry.range_km > 0

    def test_one_way_light_time_s_positive(self):
        result = _load()
        assert result.geometry.one_way_light_time_s > 0

    def test_range_rate_km_s_finite(self):
        import math
        result = _load()
        assert math.isfinite(result.geometry.range_rate_km_s)

    def test_range_km_exact(self):
        """Lock exact range_km from Attempt #2 capture."""
        result = _load()
        assert result.geometry.range_km == _EXPECTED_RANGE_KM

    def test_range_rate_km_s_exact(self):
        """Lock exact range_rate_km_s from Attempt #2 capture."""
        result = _load()
        assert result.geometry.range_rate_km_s == _EXPECTED_RANGE_RATE_KM_S

    def test_one_way_light_time_s_exact(self):
        """Lock exact one_way_light_time_s from Attempt #2 capture."""
        result = _load()
        assert result.geometry.one_way_light_time_s == _EXPECTED_ONE_WAY_LIGHT_TIME_S


class TestProvenanceFields:
    """Verify provenance record values exactly match the committed capture."""

    def test_kind(self):
        result = _load()
        assert result.provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_validation_status(self):
        result = _load()
        assert result.provenance.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_source_version_locked(self):
        """Lock source_version to exact captured API version."""
        result = _load()
        assert result.provenance.source_version == _EXPECTED_API_VERSION

    def test_observed_at_is_requested_epoch(self):
        result = _load()
        expected = datetime(2024, 6, 13, 0, 0, 0, tzinfo=timezone.utc)
        assert result.provenance.observed_at == expected

    def test_retrieved_at_is_timezone_aware(self):
        result = _load()
        assert result.provenance.retrieved_at is not None
        assert result.provenance.retrieved_at.tzinfo is not None
        assert result.provenance.retrieved_at.utcoffset() is not None

    def test_provenance_id_locked(self):
        """Lock exact provenance_id from Attempt #2 capture."""
        result = _load()
        assert result.provenance.provenance_id == _EXPECTED_PROVENANCE_ID

    def test_content_sha256_locked(self):
        """Lock exact content_sha256 from Attempt #2 capture."""
        result = _load()
        assert result.provenance.content_sha256 == _EXPECTED_CONTENT_SHA256


class TestIndependentHashVerification:
    """Independent raw-byte hash verification — does not rely on HorizonsSnapshotStore."""

    def test_raw_bytes_sha256_matches_stored(self):
        """Independently decode raw_response_base64 and verify SHA-256."""
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)

        # Strict Base64 decode (validate=True rejects non-Base64 chars).
        raw_bytes = base64.b64decode(envelope["raw_response_base64"], validate=True)

        # Independently compute SHA-256.
        computed = hashlib.sha256(raw_bytes).hexdigest()

        # Assert computed == raw_response_sha256 in envelope.
        assert computed == envelope["raw_response_sha256"]

        # Assert computed == provenance.content_sha256 in envelope.
        assert computed == envelope["provenance"]["content_sha256"]

        # Assert both match the locked expected value.
        assert computed == _EXPECTED_CONTENT_SHA256


class TestDoubleLoadEquality:
    """ZERO-NETWORK: load snapshot twice and verify identical results."""

    def test_double_load_identical(self):
        """Both loads must succeed and return identical results."""
        result_a = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)
        result_b = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)

        assert result_a.request == result_b.request
        assert result_a.geometry == result_b.geometry
        assert result_a.provenance == result_b.provenance
