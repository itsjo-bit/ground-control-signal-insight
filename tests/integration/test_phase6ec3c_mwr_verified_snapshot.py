"""GCSI Phase 6E-C3C — Offline regression test for the verified PJ62 MWR IRDR snapshot.

This test is COMPLETELY OFFLINE.  It loads only the committed snapshot
artifact that was captured in Phase 6E-C3C.

No live HTTP request is made.  A network guard (autouse fixture) is installed
to fail immediately if any socket access is attempted.

Captured artifact:
    data/verified_snapshots/pds_archive/juno_mwr/pj62/
    mwr62ri2024166030000_r04112_v04_3.0.json

C3C capture details (locked):
    LIDVID   : urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04::3.0
    label URL: https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/
               MWR62RI2024166030000_R04112_V04.xml
    HTTP GET : 1 (exactly)
    status   : 200
    raw XML  : 91253 bytes
    SHA-256  : add832f0e14d90d73daf6b4d9e1a02eeac94811dc51847aa802ec2d36b1074b0
"""

from __future__ import annotations

import base64
import hashlib
import json
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
    / "pds_archive"
    / "juno_mwr"
    / "pj62"
    / "mwr62ri2024166030000_r04112_v04_3.0.json"
)

# ---------------------------------------------------------------------------
# Import GCSI production modules
# ---------------------------------------------------------------------------

sys.path.insert(0, str(_ROOT))

from backend.app.mission_sources.snapshots import PdsArchiveSnapshotStore
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus

# ---------------------------------------------------------------------------
# Locked constants from the C3C authoritative live capture
# (Must not be modified without a new authoritative live capture.)
# ---------------------------------------------------------------------------

_EXPECTED_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04::3.0"
)
_EXPECTED_LID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04"
)
_EXPECTED_VERSION_ID = "3.0"
_EXPECTED_PRODUCT_CLASS = "Product_Observational"
_EXPECTED_PROCESSING_LEVEL = "Calibrated"
_EXPECTED_SOURCE_SYSTEM = "NASA Planetary Data System Atmospheres Node"
_EXPECTED_SOURCE_VERSION = "1.7.0.0"
_EXPECTED_CONTENT_SHA256 = (
    "add832f0e14d90d73daf6b4d9e1a02eeac94811dc51847aa802ec2d36b1074b0"
)
_EXPECTED_SNAPSHOT_SCHEMA = "gcsi.pds_archive_label_snapshot"
_EXPECTED_SNAPSHOT_VERSION = 1
_EXPECTED_SNAPSHOT_ID = (
    "ac877fd11a0c97561cb7019cba71ab3159e00b59d8a3a7618a2f35c506e4097a"
)
_EXPECTED_RAW_LABEL_SHA256 = (
    "add832f0e14d90d73daf6b4d9e1a02eeac94811dc51847aa802ec2d36b1074b0"
)
_EXPECTED_PROVENANCE_ID = (
    "880009ffe96c0f3d4cce0b9c77680b563615ad029a3028341bf8825a5bacdad8"
)

# PJ62 observation anchor
_PJ62_ANCHOR = datetime(2024, 6, 14, 3, 33, 9, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Zero-network guard fixture (autouse — applies to every test in this module)
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError(
        "GCSI offline test guard: network access is forbidden in this test. "
        "Any attempt to open a socket violates the zero-network guarantee."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Block all socket access for every test in this module."""
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Helper: load snapshot once (shared across multiple tests)
# ---------------------------------------------------------------------------


def _load():
    """Load and fully re-validate the committed PJ62 MWR IRDR snapshot."""
    product, provenance = PdsArchiveSnapshotStore.load(_SNAPSHOT_PATH)
    return product, provenance


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotSchemaAndIdentity:
    """Verify schema identity fields in the raw JSON envelope."""

    def test_snapshot_schema(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_schema"] == _EXPECTED_SNAPSHOT_SCHEMA

    def test_snapshot_version(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_version"] == _EXPECTED_SNAPSHOT_VERSION

    def test_snapshot_id_locked(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_id"] == _EXPECTED_SNAPSHOT_ID

    def test_raw_label_sha256_locked(self):
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["raw_label_sha256"] == _EXPECTED_RAW_LABEL_SHA256


class TestProductIdentity:
    """Verify exact LIDVID, LID, version, class, and processing level."""

    def test_lidvid_exact(self):
        product, _ = _load()
        assert product.lidvid == _EXPECTED_LIDVID

    def test_lid_exact(self):
        product, _ = _load()
        assert product.lid == _EXPECTED_LID

    def test_version_id(self):
        product, _ = _load()
        assert product.version_id == _EXPECTED_VERSION_ID

    def test_product_class(self):
        product, _ = _load()
        assert product.product_class == _EXPECTED_PRODUCT_CLASS

    def test_processing_level_calibrated(self):
        product, _ = _load()
        assert product.processing_level == _EXPECTED_PROCESSING_LEVEL


class TestContextReferences:
    """Verify instrument, host, investigation, and target refs."""

    def test_instrument_mwr(self):
        product, _ = _load()
        assert "urn:nasa:pds:context:instrument:mwr.jno" in product.instrument_lids

    def test_instrument_host_juno(self):
        product, _ = _load()
        assert (
            "urn:nasa:pds:context:instrument_host:spacecraft.jno"
            in product.instrument_host_lids
        )

    def test_investigation_juno(self):
        product, _ = _load()
        assert (
            "urn:nasa:pds:context:investigation:mission.juno"
            in product.investigation_lids
        )

    def test_target_jupiter(self):
        product, _ = _load()
        assert "urn:nasa:pds:context:target:planet.jupiter" in product.target_lids


class TestDataFile:
    """Verify data file structure and constraints."""

    def test_exactly_one_data_file(self):
        product, _ = _load()
        assert len(product.data_files) == 1

    def test_total_data_size_matches_file(self):
        product, _ = _load()
        df = product.data_files[0]
        assert product.total_data_size_bytes == df.file_size_bytes

    def test_file_name_csv(self):
        product, _ = _load()
        df = product.data_files[0]
        assert df.file_name.lower() == "mwr62ri2024166030000_r04112_v04.csv"

    def test_file_size_bytes_non_negative(self):
        product, _ = _load()
        df = product.data_files[0]
        assert df.file_size_bytes >= 0

    def test_registry_node_none(self):
        product, _ = _load()
        assert product.registry_node is None

    def test_registry_harvested_at_none(self):
        product, _ = _load()
        assert product.registry_harvested_at is None


class TestObservationInterval:
    """Verify observation start/stop and PJ62 anchor containment."""

    def test_start_before_stop(self):
        product, _ = _load()
        assert product.observation_start_utc <= product.observation_stop_utc

    def test_pj62_anchor_inside_interval(self):
        """2024-06-14T03:33:09Z must lie within the observed interval."""
        product, _ = _load()
        assert product.observation_start_utc <= _PJ62_ANCHOR <= product.observation_stop_utc

    def test_start_timezone_aware(self):
        product, _ = _load()
        assert product.observation_start_utc.tzinfo is not None

    def test_stop_timezone_aware(self):
        product, _ = _load()
        assert product.observation_stop_utc.tzinfo is not None


class TestProvenance:
    """Verify provenance record fields from the committed capture."""

    def test_kind_external_authoritative(self):
        _, provenance = _load()
        assert provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_source_system(self):
        _, provenance = _load()
        assert provenance.source_system == _EXPECTED_SOURCE_SYSTEM

    def test_source_record_id_exact(self):
        _, provenance = _load()
        assert provenance.source_record_id == _EXPECTED_LIDVID

    def test_validation_status_validated(self):
        _, provenance = _load()
        assert provenance.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_content_sha256_locked(self):
        _, provenance = _load()
        assert provenance.content_sha256 == _EXPECTED_CONTENT_SHA256

    def test_provenance_id_locked(self):
        _, provenance = _load()
        assert provenance.provenance_id == _EXPECTED_PROVENANCE_ID

    def test_source_version_im(self):
        _, provenance = _load()
        assert provenance.source_version == _EXPECTED_SOURCE_VERSION

    def test_retrieved_at_timezone_aware(self):
        _, provenance = _load()
        assert provenance.retrieved_at is not None
        assert provenance.retrieved_at.tzinfo is not None
        assert provenance.retrieved_at.utcoffset() is not None

    def test_notes_mention_derived_file_ref(self):
        _, provenance = _load()
        assert provenance.notes is not None
        assert "derived" in provenance.notes.lower()

    def test_notes_mention_csv_not_authenticated(self):
        _, provenance = _load()
        assert provenance.notes is not None
        assert "not authenticated" in provenance.notes.lower()


class TestIndependentHashVerification:
    """Independent raw-byte hash verification — does not rely on PdsArchiveSnapshotStore."""

    def test_raw_bytes_sha256_matches_stored(self):
        """Independently decode raw_label_base64 and verify SHA-256."""
        with open(_SNAPSHOT_PATH) as f:
            envelope = json.load(f)

        # Strict Base64 decode (validate=True rejects non-Base64 chars).
        raw_bytes = base64.b64decode(envelope["raw_label_base64"], validate=True)

        # Independently compute SHA-256.
        computed = hashlib.sha256(raw_bytes).hexdigest()

        # Assert computed == raw_label_sha256 in envelope.
        assert computed == envelope["raw_label_sha256"]

        # Assert computed == provenance.content_sha256 in envelope.
        assert computed == envelope["provenance"]["content_sha256"]

        # Assert matches the locked expected value.
        assert computed == _EXPECTED_CONTENT_SHA256


class TestDoubleLoadEquality:
    """ZERO-NETWORK: load snapshot twice and verify identical results."""

    def test_double_load_identical(self):
        product_a, provenance_a = PdsArchiveSnapshotStore.load(_SNAPSHOT_PATH)
        product_b, provenance_b = PdsArchiveSnapshotStore.load(_SNAPSHOT_PATH)
        assert product_a == product_b
        assert provenance_a == provenance_b
