"""GCSI Phase 6E-C4A — Offline integration test for the exact-epoch PJ62 Horizons snapshot.

This test is COMPLETELY OFFLINE.  It loads only the committed snapshot artifact
captured in Phase 6E-C4A.

No live HTTP request is made.  A network guard blocks any socket access.

Captured artifact:
    data/verified_snapshots/horizons/juno/juno_spk_-61_2024-06-14T035955.483000Z.json

Phase 6E-C4A froze the replay decision epoch as:
    2024-06-14T03:59:55.483000Z

This is exactly the MWR observation_stop_utc shared by both:
    IRDR: urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04::3.0
    GRDR: urn:nasa:pds:juno_mwr:data_calibrated:mwr62rg2024166030000_r04112_v04::3.0

Cross-source temporal alignment verified: EXACT
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

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
    / "juno_spk_-61_2024-06-14T035955.483000Z.json"
)
_IRDR_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "pds_archive"
    / "juno_mwr"
    / "pj62"
    / "mwr62ri2024166030000_r04112_v04_3.0.json"
)
_GRDR_PATH = (
    _ROOT
    / "data"
    / "verified_snapshots"
    / "pds_archive"
    / "juno_mwr"
    / "pj62"
    / "mwr62rg2024166030000_r04112_v04_3.0.json"
)

# ---------------------------------------------------------------------------
# Import GCSI production modules
# ---------------------------------------------------------------------------

sys.path.insert(0, str(_ROOT))

from backend.app.mission_sources.snapshots.horizons_snapshot import HorizonsSnapshotStore
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus

# ---------------------------------------------------------------------------
# Exact constants locked from the C4A live capture
# (Must not be modified without a new authoritative live capture.)
# ---------------------------------------------------------------------------

_DECISION_EPOCH = datetime(2024, 6, 14, 3, 59, 55, 483000, tzinfo=timezone.utc)

_EXPECTED_RANGE_KM: float = 893345396.8038701
_EXPECTED_RANGE_RATE_KM_S: float = -10.47349415704751
_EXPECTED_ONE_WAY_LIGHT_TIME_S: float = 2979.879489843171

_EXPECTED_PROVENANCE_ID: str = (
    "6b5b55bc68159e9b53216455f86604f5615258a12644d2a3414e99913b01c324"
)
_EXPECTED_CONTENT_SHA256: str = (
    "89efa5118ca00795e68644c9a700f841e1b31c31830dfdb6454da7e7a3a2ec34"
)
_EXPECTED_SNAPSHOT_ID: str = (
    "34aad3778efe9e9d481ac9fa2173dcd10f700db1e6e6406182b7b68ff1970097"
)
_EXPECTED_API_SOURCE: str = "NASA/JPL Horizons API"
_EXPECTED_API_VERSION: str = "1.2"


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


def _load():
    """Load and fully re-validate the committed C4A Juno snapshot."""
    return HorizonsSnapshotStore.load(_SNAPSHOT_PATH)


# ---------------------------------------------------------------------------
# Tests: snapshot file presence
# ---------------------------------------------------------------------------


class TestSnapshotFilePresence:
    """Verify the committed snapshot file exists at the expected path."""

    def test_snapshot_file_exists(self):
        assert _SNAPSHOT_PATH.exists(), (
            f"C4A snapshot not found at expected path: {_SNAPSHOT_PATH}"
        )


# ---------------------------------------------------------------------------
# Tests: schema and identity fields
# ---------------------------------------------------------------------------


class TestSnapshotSchemaAndIdentity:
    """Verify schema identity fields in the raw JSON envelope."""

    def test_snapshot_schema(self):
        with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
            envelope = json.load(f)
        assert envelope["snapshot_schema"] == "gcsi.horizons_geometry_snapshot"

    def test_snapshot_version(self):
        with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
            envelope = json.load(f)
        assert envelope["snapshot_version"] == 1

    def test_snapshot_id_locked(self):
        """Lock exact snapshot_id from the C4A live capture."""
        with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
            envelope = json.load(f)
        assert envelope["snapshot_id"] == _EXPECTED_SNAPSHOT_ID


# ---------------------------------------------------------------------------
# Tests: request fields
# ---------------------------------------------------------------------------


class TestRequestFields:
    """Verify the request section of the loaded snapshot."""

    def test_target_spk_id(self):
        """SPK ID must be Juno (-61)."""
        result = _load()
        assert result.request.target_spk_id == "-61"

    def test_epoch_utc_exact(self):
        """Epoch must be exactly the replay decision epoch — no rounding, no tolerance."""
        result = _load()
        assert result.request.epoch_utc == _DECISION_EPOCH, (
            f"Epoch mismatch: expected {_DECISION_EPOCH.isoformat()}, "
            f"got {result.request.epoch_utc.isoformat()}"
        )


# ---------------------------------------------------------------------------
# Tests: geometry fields
# ---------------------------------------------------------------------------


class TestGeometryFields:
    """Verify geometry field values exactly match the locked C4A capture."""

    def test_target_spk_id_in_geometry(self):
        result = _load()
        assert result.geometry.target_spk_id == "-61"

    def test_center(self):
        """Center must be Earth geocenter."""
        result = _load()
        assert result.geometry.center == "500@399"

    def test_epoch_utc_exact(self):
        """Geometry epoch must be exactly the decision epoch — no tolerance."""
        result = _load()
        assert result.geometry.epoch_utc == _DECISION_EPOCH

    def test_range_km_positive(self):
        result = _load()
        assert result.geometry.range_km > 0

    def test_one_way_light_time_s_positive(self):
        result = _load()
        assert result.geometry.one_way_light_time_s > 0

    def test_range_rate_km_s_finite(self):
        result = _load()
        assert math.isfinite(result.geometry.range_rate_km_s)

    def test_range_km_locked(self):
        """Lock exact range_km from C4A capture."""
        result = _load()
        assert result.geometry.range_km == _EXPECTED_RANGE_KM

    def test_range_rate_km_s_locked(self):
        """Lock exact range_rate_km_s from C4A capture."""
        result = _load()
        assert result.geometry.range_rate_km_s == _EXPECTED_RANGE_RATE_KM_S

    def test_one_way_light_time_s_locked(self):
        """Lock exact one_way_light_time_s from C4A capture."""
        result = _load()
        assert result.geometry.one_way_light_time_s == _EXPECTED_ONE_WAY_LIGHT_TIME_S

    def test_api_source(self):
        result = _load()
        assert result.geometry.api_source == _EXPECTED_API_SOURCE

    def test_api_version_locked(self):
        """Lock exact API version from C4A capture."""
        result = _load()
        assert result.geometry.api_version == _EXPECTED_API_VERSION


# ---------------------------------------------------------------------------
# Tests: provenance fields
# ---------------------------------------------------------------------------


class TestProvenanceFields:
    """Verify provenance record values exactly match the C4A locked capture."""

    def test_kind(self):
        result = _load()
        assert result.provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_validation_status(self):
        result = _load()
        assert result.provenance.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_source_system(self):
        result = _load()
        assert result.provenance.source_system == _EXPECTED_API_SOURCE

    def test_source_version_locked(self):
        result = _load()
        assert result.provenance.source_version == _EXPECTED_API_VERSION

    def test_observed_at_is_decision_epoch(self):
        """observed_at must be exactly the replay decision epoch."""
        result = _load()
        assert result.provenance.observed_at == _DECISION_EPOCH

    def test_retrieved_at_is_timezone_aware(self):
        result = _load()
        assert result.provenance.retrieved_at is not None
        assert result.provenance.retrieved_at.tzinfo is not None
        assert result.provenance.retrieved_at.utcoffset() is not None

    def test_provenance_id_locked(self):
        """Lock exact provenance_id from C4A capture."""
        result = _load()
        assert result.provenance.provenance_id == _EXPECTED_PROVENANCE_ID

    def test_content_sha256_locked(self):
        """Lock exact content_sha256 from C4A capture."""
        result = _load()
        assert result.provenance.content_sha256 == _EXPECTED_CONTENT_SHA256


# ---------------------------------------------------------------------------
# Tests: independent hash verification
# ---------------------------------------------------------------------------


class TestIndependentHashVerification:
    """Independent raw-byte hash verification — does not rely on HorizonsSnapshotStore."""

    def test_raw_bytes_sha256_matches_stored(self):
        """Independently decode raw_response_base64 and verify SHA-256."""
        with open(_SNAPSHOT_PATH, encoding="utf-8") as f:
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


# ---------------------------------------------------------------------------
# Tests: double-load equality
# ---------------------------------------------------------------------------


class TestDoubleLoadEquality:
    """ZERO-NETWORK: load snapshot twice and verify identical results."""

    def test_double_load_identical(self):
        """Both loads must succeed and return identical results."""
        result_a = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)
        result_b = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)

        assert result_a.request == result_b.request
        assert result_a.geometry == result_b.geometry
        assert result_a.provenance == result_b.provenance


# ---------------------------------------------------------------------------
# Tests: cross-source temporal alignment
# ---------------------------------------------------------------------------


class TestCrossSourceTemporalAlignment:
    """Verify exact temporal alignment across the three authoritative C4A artifacts.

    The Horizons geometry epoch, the IRDR observation_stop_utc, and the GRDR
    observation_stop_utc must all be exactly equal to the replay decision epoch.
    This freezes the temporal alignment across all authoritative replay inputs.

    TEMPORAL_ALIGNMENT = EXACT is asserted; any deviation fails the test.
    """

    def test_horizons_epoch_equals_decision_epoch(self):
        """Horizons epoch must be exactly the replay decision epoch."""
        result = _load()
        assert result.geometry.epoch_utc == _DECISION_EPOCH

    def test_irdr_stop_equals_decision_epoch(self):
        """IRDR observation_stop_utc must equal the replay decision epoch."""
        irdr_data = json.loads(_IRDR_PATH.read_text(encoding="utf-8"))
        stop_str = irdr_data["product"]["observation_stop_utc"]
        # Parse: ends in Z
        irdr_stop = datetime.fromisoformat(stop_str.replace("Z", "+00:00"))
        assert irdr_stop == _DECISION_EPOCH, (
            f"IRDR stop {irdr_stop.isoformat()} != decision epoch {_DECISION_EPOCH.isoformat()}"
        )

    def test_grdr_stop_equals_decision_epoch(self):
        """GRDR observation_stop_utc must equal the replay decision epoch."""
        grdr_data = json.loads(_GRDR_PATH.read_text(encoding="utf-8"))
        stop_str = grdr_data["product"]["observation_stop_utc"]
        grdr_stop = datetime.fromisoformat(stop_str.replace("Z", "+00:00"))
        assert grdr_stop == _DECISION_EPOCH, (
            f"GRDR stop {grdr_stop.isoformat()} != decision epoch {_DECISION_EPOCH.isoformat()}"
        )

    def test_all_three_exactly_equal(self):
        """All three authoritative sources must share exactly the same epoch.

        TEMPORAL_ALIGNMENT = EXACT — any mismatch fails closed.
        """
        result = _load()
        horizons_epoch = result.geometry.epoch_utc

        irdr_data = json.loads(_IRDR_PATH.read_text(encoding="utf-8"))
        irdr_stop = datetime.fromisoformat(
            irdr_data["product"]["observation_stop_utc"].replace("Z", "+00:00")
        )

        grdr_data = json.loads(_GRDR_PATH.read_text(encoding="utf-8"))
        grdr_stop = datetime.fromisoformat(
            grdr_data["product"]["observation_stop_utc"].replace("Z", "+00:00")
        )

        assert horizons_epoch == irdr_stop == grdr_stop == _DECISION_EPOCH, (
            f"TEMPORAL_ALIGNMENT_MISMATCH: "
            f"Horizons={horizons_epoch.isoformat()}, "
            f"IRDR_stop={irdr_stop.isoformat()}, "
            f"GRDR_stop={grdr_stop.isoformat()}, "
            f"expected={_DECISION_EPOCH.isoformat()}"
        )
