"""GCSI Phase 6E-C3D — Offline regression test for the verified PJ62 MWR GRDR snapshot
and the authoritative IRDR/GRDR pair freeze.

This test is COMPLETELY OFFLINE.  It loads both committed snapshot artifacts
through PdsArchiveSnapshotStore.load() — no live HTTP request is made.

An autouse socket-block fixture fails immediately if any network access is
attempted.

Captured artifacts:
    data/verified_snapshots/pds_archive/juno_mwr/pj62/
        mwr62ri2024166030000_r04112_v04_3.0.json  (C3C — IRDR)
        mwr62rg2024166030000_r04112_v04_3.0.json  (C3D — GRDR)

C3D capture details (locked):
    GRDR LIDVID  : urn:nasa:pds:juno_mwr:data_calibrated:mwr62rg2024166030000_r04112_v04::3.0
    GRDR URL     : https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024166/
                   MWR62RG2024166030000_R04112_V04.xml
    HTTP GET     : 1 (exactly)
    status       : 200
    raw XML      : 84015 bytes
    SHA-256      : 7e0fec3de320f6ebaf3228104658b7f0badfe32e260bd769d29c2940000d654b
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]
_IRDR_SNAPSHOT_PATH = (
    _ROOT
    / "data" / "verified_snapshots" / "pds_archive" / "juno_mwr" / "pj62"
    / "mwr62ri2024166030000_r04112_v04_3.0.json"
)
_GRDR_SNAPSHOT_PATH = (
    _ROOT
    / "data" / "verified_snapshots" / "pds_archive" / "juno_mwr" / "pj62"
    / "mwr62rg2024166030000_r04112_v04_3.0.json"
)

sys.path.insert(0, str(_ROOT))

from backend.app.mission_sources.snapshots import PdsArchiveSnapshotStore
from backend.app.provenance.models import ProvenanceKind, ProvenanceValidationStatus

# ---------------------------------------------------------------------------
# Locked constants from C3D authoritative live capture
# (Must not be modified without a new authoritative live capture.)
# ---------------------------------------------------------------------------

_GRDR_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62rg2024166030000_r04112_v04::3.0"
)
_GRDR_LID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62rg2024166030000_r04112_v04"
)
_IRDR_LIDVID = (
    "urn:nasa:pds:juno_mwr:data_calibrated:"
    "mwr62ri2024166030000_r04112_v04::3.0"
)
_GRDR_VERSION_ID = "3.0"
_GRDR_PRODUCT_CLASS = "Product_Observational"
_GRDR_PROCESSING_LEVEL = "Calibrated"
_SOURCE_SYSTEM = "NASA Planetary Data System Atmospheres Node"
_GRDR_SOURCE_VERSION = "1.7.0.0"
_GRDR_CONTENT_SHA256 = (
    "7e0fec3de320f6ebaf3228104658b7f0badfe32e260bd769d29c2940000d654b"
)
_GRDR_PROVENANCE_ID = (
    "c431a798ae09c7d91701aefd8be8fd954f5066a3c09801668d997965ae67808d"
)
_GRDR_SNAPSHOT_SCHEMA = "gcsi.pds_archive_label_snapshot"
_GRDR_SNAPSHOT_VERSION = 1
_GRDR_SNAPSHOT_ID = (
    "4f55efcfb7cddac48c2b245ed0613a4839467fe6b73b029b98bd34fc89a6e88f"
)
_GRDR_RAW_LABEL_SHA256 = (
    "7e0fec3de320f6ebaf3228104658b7f0badfe32e260bd769d29c2940000d654b"
)

# PJ62 observation anchor
_PJ62_ANCHOR = datetime(2024, 6, 14, 3, 33, 9, tzinfo=timezone.utc)

# LIDVID parse regex
_LIDVID_RE = re.compile(
    r"^urn:nasa:pds:juno_mwr:data_calibrated:"
    r"mwr([0-9]{2})r([ig])([0-9]{13})_(r[0-9]{5})_(v[0-9]{2})"
    r"::([A-Za-z0-9._-]+)$"
)


# ---------------------------------------------------------------------------
# Zero-network guard fixture (autouse)
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError(
        "GCSI offline test guard: network access is forbidden in this test. "
        "Any socket call violates the zero-network guarantee."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Block all socket access for every test in this module."""
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_grdr():
    """Load and re-validate the committed GRDR snapshot."""
    return PdsArchiveSnapshotStore.load(_GRDR_SNAPSHOT_PATH)


def _load_irdr():
    """Load and re-validate the committed IRDR snapshot."""
    return PdsArchiveSnapshotStore.load(_IRDR_SNAPSHOT_PATH)


def _parse_lidvid(lidvid: str):
    """Return (pj, role, timestamp, reccode, localver, pdsver) from a LIDVID."""
    m = _LIDVID_RE.match(lidvid)
    assert m is not None, f"LIDVID failed regex: {lidvid!r}"
    return m.groups()


# ---------------------------------------------------------------------------
# GRDR — schema / identity
# ---------------------------------------------------------------------------


class TestGrdrSnapshotSchemaAndIdentity:
    """Verify GRDR snapshot envelope fields."""

    def test_snapshot_schema(self):
        with open(_GRDR_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_schema"] == _GRDR_SNAPSHOT_SCHEMA

    def test_snapshot_version(self):
        with open(_GRDR_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_version"] == _GRDR_SNAPSHOT_VERSION

    def test_snapshot_id_locked(self):
        with open(_GRDR_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["snapshot_id"] == _GRDR_SNAPSHOT_ID

    def test_raw_label_sha256_locked(self):
        with open(_GRDR_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        assert envelope["raw_label_sha256"] == _GRDR_RAW_LABEL_SHA256


# ---------------------------------------------------------------------------
# GRDR — product identity
# ---------------------------------------------------------------------------


class TestGrdrProductIdentity:
    """Verify exact GRDR LIDVID, LID, version, class, processing level."""

    def test_lidvid_exact(self):
        product, _ = _load_grdr()
        assert product.lidvid == _GRDR_LIDVID

    def test_lid_exact(self):
        product, _ = _load_grdr()
        assert product.lid == _GRDR_LID

    def test_version_id(self):
        product, _ = _load_grdr()
        assert product.version_id == _GRDR_VERSION_ID

    def test_product_class(self):
        product, _ = _load_grdr()
        assert product.product_class == _GRDR_PRODUCT_CLASS

    def test_processing_level_calibrated(self):
        product, _ = _load_grdr()
        assert product.processing_level == _GRDR_PROCESSING_LEVEL


# ---------------------------------------------------------------------------
# GRDR — context references
# ---------------------------------------------------------------------------


class TestGrdrContextReferences:
    """Verify GRDR instrument, host, investigation, target refs."""

    def test_instrument_mwr(self):
        product, _ = _load_grdr()
        assert "urn:nasa:pds:context:instrument:mwr.jno" in product.instrument_lids

    def test_instrument_host_juno(self):
        product, _ = _load_grdr()
        assert (
            "urn:nasa:pds:context:instrument_host:spacecraft.jno"
            in product.instrument_host_lids
        )

    def test_investigation_juno(self):
        product, _ = _load_grdr()
        assert (
            "urn:nasa:pds:context:investigation:mission.juno"
            in product.investigation_lids
        )

    def test_target_jupiter(self):
        product, _ = _load_grdr()
        assert "urn:nasa:pds:context:target:planet.jupiter" in product.target_lids


# ---------------------------------------------------------------------------
# GRDR — data file
# ---------------------------------------------------------------------------


class TestGrdrDataFile:
    """Verify GRDR data file structure and constraints."""

    def test_exactly_one_data_file(self):
        product, _ = _load_grdr()
        assert len(product.data_files) == 1

    def test_total_size_matches_file(self):
        product, _ = _load_grdr()
        df = product.data_files[0]
        assert product.total_data_size_bytes == df.file_size_bytes

    def test_file_name_grdr_csv(self):
        product, _ = _load_grdr()
        df = product.data_files[0]
        assert df.file_name.lower() == "mwr62rg2024166030000_r04112_v04.csv"

    def test_file_size_non_negative(self):
        product, _ = _load_grdr()
        df = product.data_files[0]
        assert df.file_size_bytes >= 0

    def test_mime_type_none(self):
        product, _ = _load_grdr()
        df = product.data_files[0]
        assert df.mime_type is None

    def test_registry_node_none(self):
        product, _ = _load_grdr()
        assert product.registry_node is None

    def test_registry_harvested_at_none(self):
        product, _ = _load_grdr()
        assert product.registry_harvested_at is None


# ---------------------------------------------------------------------------
# GRDR — observation interval
# ---------------------------------------------------------------------------


class TestGrdrObservationInterval:
    """Verify GRDR observation start/stop and PJ62 anchor containment."""

    def test_start_before_stop(self):
        product, _ = _load_grdr()
        assert product.observation_start_utc <= product.observation_stop_utc

    def test_pj62_anchor_inside_interval(self):
        product, _ = _load_grdr()
        assert product.observation_start_utc <= _PJ62_ANCHOR <= product.observation_stop_utc

    def test_start_timezone_aware(self):
        product, _ = _load_grdr()
        assert product.observation_start_utc.tzinfo is not None

    def test_stop_timezone_aware(self):
        product, _ = _load_grdr()
        assert product.observation_stop_utc.tzinfo is not None


# ---------------------------------------------------------------------------
# GRDR — provenance
# ---------------------------------------------------------------------------


class TestGrdrProvenance:
    """Verify GRDR provenance record fields."""

    def test_kind_external_authoritative(self):
        _, provenance = _load_grdr()
        assert provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_source_system(self):
        _, provenance = _load_grdr()
        assert provenance.source_system == _SOURCE_SYSTEM

    def test_source_record_id_exact(self):
        _, provenance = _load_grdr()
        assert provenance.source_record_id == _GRDR_LIDVID

    def test_validation_status_validated(self):
        _, provenance = _load_grdr()
        assert provenance.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_content_sha256_locked(self):
        _, provenance = _load_grdr()
        assert provenance.content_sha256 == _GRDR_CONTENT_SHA256

    def test_provenance_id_locked(self):
        _, provenance = _load_grdr()
        assert provenance.provenance_id == _GRDR_PROVENANCE_ID

    def test_source_version_im(self):
        _, provenance = _load_grdr()
        assert provenance.source_version == _GRDR_SOURCE_VERSION

    def test_retrieved_at_timezone_aware(self):
        _, provenance = _load_grdr()
        assert provenance.retrieved_at is not None
        assert provenance.retrieved_at.tzinfo is not None
        assert provenance.retrieved_at.utcoffset() is not None

    def test_notes_mention_derived_file_ref(self):
        _, provenance = _load_grdr()
        assert provenance.notes is not None
        assert "derived" in provenance.notes.lower()

    def test_notes_csv_not_authenticated(self):
        _, provenance = _load_grdr()
        assert provenance.notes is not None
        assert "not authenticated" in provenance.notes.lower()


# ---------------------------------------------------------------------------
# GRDR — independent hash verification
# ---------------------------------------------------------------------------


class TestGrdrIndependentHashVerification:
    """Independent raw-byte hash — does not rely on PdsArchiveSnapshotStore."""

    def test_raw_bytes_sha256_matches_stored(self):
        with open(_GRDR_SNAPSHOT_PATH) as f:
            envelope = json.load(f)
        raw_bytes = base64.b64decode(envelope["raw_label_base64"], validate=True)
        computed = hashlib.sha256(raw_bytes).hexdigest()
        assert computed == envelope["raw_label_sha256"]
        assert computed == envelope["provenance"]["content_sha256"]
        assert computed == _GRDR_CONTENT_SHA256


# ---------------------------------------------------------------------------
# Pair invariants — IRDR and GRDR together
# ---------------------------------------------------------------------------


class TestIrdrGrdrPairIdentity:
    """Verify LIDVID family fields are identical except for role (ri vs rg)."""

    def test_same_pj(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        ipj = _parse_lidvid(irdr_p.lidvid)[0]
        gpj = _parse_lidvid(grdr_p.lidvid)[0]
        assert ipj == gpj == "62"

    def test_same_timestamp(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert _parse_lidvid(irdr_p.lidvid)[2] == _parse_lidvid(grdr_p.lidvid)[2]

    def test_same_record_code(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert _parse_lidvid(irdr_p.lidvid)[3] == _parse_lidvid(grdr_p.lidvid)[3]

    def test_same_local_version(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert _parse_lidvid(irdr_p.lidvid)[4] == _parse_lidvid(grdr_p.lidvid)[4]

    def test_same_pds_version(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert _parse_lidvid(irdr_p.lidvid)[5] == _parse_lidvid(grdr_p.lidvid)[5]

    def test_irdr_role_is_i(self):
        irdr_p, _ = _load_irdr()
        assert _parse_lidvid(irdr_p.lidvid)[1] == "i"

    def test_grdr_role_is_g(self):
        grdr_p, _ = _load_grdr()
        assert _parse_lidvid(grdr_p.lidvid)[1] == "g"


class TestIrdrGrdrObservationInterval:
    """Verify identical observation windows."""

    def test_observation_starts_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert irdr_p.observation_start_utc == grdr_p.observation_start_utc

    def test_observation_stops_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert irdr_p.observation_stop_utc == grdr_p.observation_stop_utc

    def test_pj62_inside_irdr(self):
        irdr_p, _ = _load_irdr()
        assert irdr_p.observation_start_utc <= _PJ62_ANCHOR <= irdr_p.observation_stop_utc

    def test_pj62_inside_grdr(self):
        grdr_p, _ = _load_grdr()
        assert grdr_p.observation_start_utc <= _PJ62_ANCHOR <= grdr_p.observation_stop_utc


class TestIrdrGrdrContextConsistency:
    """Verify context references are identical across the pair."""

    def test_instrument_lids_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert tuple(irdr_p.instrument_lids) == tuple(grdr_p.instrument_lids)

    def test_instrument_host_lids_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert tuple(irdr_p.instrument_host_lids) == tuple(grdr_p.instrument_host_lids)

    def test_investigation_lids_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert tuple(irdr_p.investigation_lids) == tuple(grdr_p.investigation_lids)

    def test_target_lids_equal(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert tuple(irdr_p.target_lids) == tuple(grdr_p.target_lids)


class TestIrdrGrdrProcessingConsistency:
    """Verify both products are Product_Observational / Calibrated."""

    def test_both_product_observational(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert irdr_p.product_class == "Product_Observational"
        assert grdr_p.product_class == "Product_Observational"

    def test_both_calibrated(self):
        irdr_p, _ = _load_irdr()
        grdr_p, _ = _load_grdr()
        assert irdr_p.processing_level == "Calibrated"
        assert grdr_p.processing_level == "Calibrated"


class TestIrdrGrdrProvenanceConsistency:
    """Verify pair provenance consistency and distinction."""

    def test_both_external_authoritative(self):
        _, irdr_prov = _load_irdr()
        _, grdr_prov = _load_grdr()
        assert irdr_prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert grdr_prov.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_both_same_source_system(self):
        _, irdr_prov = _load_irdr()
        _, grdr_prov = _load_grdr()
        assert irdr_prov.source_system == _SOURCE_SYSTEM
        assert grdr_prov.source_system == _SOURCE_SYSTEM

    def test_both_validated(self):
        _, irdr_prov = _load_irdr()
        _, grdr_prov = _load_grdr()
        assert irdr_prov.validation_status == ProvenanceValidationStatus.VALIDATED
        assert grdr_prov.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_source_record_ids_distinct(self):
        _, irdr_prov = _load_irdr()
        _, grdr_prov = _load_grdr()
        assert irdr_prov.source_record_id != grdr_prov.source_record_id

    def test_source_uris_distinct(self):
        _, irdr_prov = _load_irdr()
        _, grdr_prov = _load_grdr()
        assert irdr_prov.source_uri != grdr_prov.source_uri

    def test_irdr_source_record_id_exact(self):
        _, irdr_prov = _load_irdr()
        assert irdr_prov.source_record_id == _IRDR_LIDVID

    def test_grdr_source_record_id_exact(self):
        _, grdr_prov = _load_grdr()
        assert grdr_prov.source_record_id == _GRDR_LIDVID


class TestDoubleLoadEquality:
    """ZERO-NETWORK: double load produces identical results."""

    def test_grdr_double_load_identical(self):
        product_a, prov_a = PdsArchiveSnapshotStore.load(_GRDR_SNAPSHOT_PATH)
        product_b, prov_b = PdsArchiveSnapshotStore.load(_GRDR_SNAPSHOT_PATH)
        assert product_a == product_b
        assert prov_a == prov_b
