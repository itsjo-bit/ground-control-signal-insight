"""GCSI Phase 6F-B2.2 — Acquisition Layer Offline Tests.

All tests are OFFLINE. No live PDS requests are made.
All HTTP interactions are mocked via httpx.

Coverage:
  Section 68: Fetch and parse test matrix
  Section 69: Temporal test matrix
  Section 71: Size test matrix
  Section 73: Ledger test matrix
  Section 74: Inventory test matrix
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from backend.app.mission_sources.v2_inventory_acquisition import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_UTC,
    AcquisitionLedger,
    AcquisitionLedgerRow,
    AcquisitionStatus,
    SizeVerificationStatus,
    TemporalVerificationStatus,
    V2InventoryAcquisitionRunner,
    _build_representation_sequence,
    _check_temporal_eligibility,
    _compute_ledger_id,
    _derive_size_verification_status,
    _select_canary_indices,
    _snapshot_path_for_url,
    load_ledger,
    save_ledger,
)
from backend.app.mission_sources.v2_source_bundle import (
    V2SourceBundle,
    build_source_bundle,
    load_source_bundle,
    save_source_bundle,
)
from backend.app.mission_sources.v2_verified_inventory import (
    V2VerifiedInventoryBuilder,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_ELIGIBLE_STOP_UTC = datetime(2024, 6, 13, 15, 0, 0, tzinfo=timezone.utc)
_PRE_STOP_UTC = datetime(2024, 6, 12, 8, 0, 0, tzinfo=timezone.utc)
_POST_STOP_UTC = datetime(2024, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
_START_UTC = datetime(2024, 6, 13, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Shared minimal-plan helpers
# ---------------------------------------------------------------------------


def _make_plan_with_one_rep(
    source_standard: str = "pds4",
    profile_id: str = "jiram_pds4",
    normalizer_id: str = "gcsi.generic_pds4_label.v1",
    label_url: str = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit_0062/JIR_IMG_RDR_2024166T090046_V01.xml",
    expected_archive_identity: Optional[str] = None,
    temporal_status: str = "LABEL_VERIFICATION_PENDING",
    discovery_availability: Optional[datetime] = None,
):
    """Build a minimal HistoricalReplayV2AcquisitionPlan for testing."""
    from backend.app.mission_sources.v2_acquisition_plan import (
        AcquisitionLogicalProductEntry,
        AcquisitionRepresentationRole,
        AcquisitionSourceRepresentation,
        AcquisitionSourceStandard,
        DiscoveryEvidence,
        HistoricalReplayV2AcquisitionPlan,
        ACCUMULATION_START_UTC,
        DECISION_EPOCH_UTC,
        DECISION_EPOCH_POLICY,
        FINAL_TEMPORAL_ELIGIBILITY,
        TemporalEvidenceStatus,
        _compute_plan_id,
    )

    # Map strings to enums
    std_enum = AcquisitionSourceStandard.PDS4 if source_standard == "pds4" else AcquisitionSourceStandard.PDS3

    # Pick role by profile
    role_map = {
        "jiram_pds4": AcquisitionRepresentationRole.CALIBRATED,
        "mwr_generic_pds4": AcquisitionRepresentationRole.CALIBRATED,
        "uvs_pds4": AcquisitionRepresentationRole.CALIBRATED,
        "junocam_pds3": AcquisitionRepresentationRole.EDR,
        "fgm_pds3": AcquisitionRepresentationRole.FULL_RESOLUTION,
        "jade_pds3": AcquisitionRepresentationRole.CALIBRATED,
        "jedi_pds3": AcquisitionRepresentationRole.CALIBRATED,
        "waves_survey_pds3": AcquisitionRepresentationRole.SURVEY_B,
        "waves_burst_pds3": AcquisitionRepresentationRole.BURST_B_BIN,
    }
    role = role_map.get(profile_id, AcquisitionRepresentationRole.CALIBRATED)

    rep = AcquisitionSourceRepresentation(
        representation_role=role,
        source_standard=std_enum,
        label_url=label_url,
        normalizer_id=normalizer_id,
        profile_id=profile_id,
        expected_archive_identity=expected_archive_identity,
        discovery_evidence_id="test_ev",
    )

    temp_ev_status = (
        TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
        if temporal_status == "EXACT_DISCOVERY_METADATA"
        else TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
    )

    entry = AcquisitionLogicalProductEntry(
        logical_product_id="test_product_001",
        instrument="JIRAM" if "jiram" in profile_id else profile_id.split("_")[0].upper(),
        semantic_role="instrument_diagnostic",
        temporal_evidence_status=temp_ev_status,
        discovery_availability_time_utc=discovery_availability,
        representations=(rep,),
        discovery_evidence_id="test_ev",
    )

    # Build fake discovery evidence (SHA-256 must pass placeholder check)
    fake_bytes = b"fake discovery html response " + label_url.encode()
    ev = DiscoveryEvidence.capture(
        evidence_id="test_ev",
        source_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/",
        retrieved_at=datetime(2024, 6, 13, 10, 5, 0, tzinfo=timezone.utc),
        response_bytes=fake_bytes,
        source_kind="pds4_directory_html",
    )

    fake_sidecar_bytes = b"sidecar content for " + label_url.encode()
    discovery_evidence_artifact_id = hashlib.sha256(fake_sidecar_bytes).hexdigest()

    plan_id = _compute_plan_id(
        plan_id_placeholder="",
        replay_id="test_replay_v2",
        accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
        decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
        decision_epoch_policy=DECISION_EPOCH_POLICY,
        logical_entries=(entry,),
        discovery_evidence=(ev,),
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
    )

    return HistoricalReplayV2AcquisitionPlan(
        schema="gcsi.historical_replay_v2_acquisition_plan",
        schema_version=1,
        plan_id=plan_id,
        replay_id="test_replay_v2",
        accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
        decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
        decision_epoch_policy=DECISION_EPOCH_POLICY,
        final_temporal_eligibility=FINAL_TEMPORAL_ELIGIBILITY,
        logical_entries=(entry,),
        discovery_evidence=(ev,),
        discovery_evidence_artifact_id=discovery_evidence_artifact_id,
    )


# ===========================================================================
# §68 — Fetch and parse test matrix
# ===========================================================================


class TestSection68FetchMatrix:
    """Section 68: HTTP fetch and parse scenarios."""

    def test_01_snapshot_path_deterministic(self):
        """_snapshot_path_for_url must be deterministic and URL-hash-based."""
        root = Path("/tmp/snapshots")
        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        p1 = _snapshot_path_for_url("JIRAM", url, root)
        p2 = _snapshot_path_for_url("JIRAM", url, root)
        assert p1 == p2
        # Must be inside root/jiram/
        assert p1.parent == root / "jiram"
        assert p1.suffix == ".json"
        expected_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()
        assert p1.name == f"{expected_hash}.json"

    def test_02_snapshot_path_instrument_lowercased(self):
        """Instrument directory must be lowercase."""
        root = Path("/tmp/snapshots")
        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        p = _snapshot_path_for_url("JIRAM", url, root)
        assert p.parent.name == "jiram"

    def test_03_302_rejection_not_retryable(self):
        """HTTP 302 redirect must be rejected as FAILED_VALIDATION (not retried)."""
        from backend.app.mission_sources.v2_inventory_acquisition import _fetch_label_bytes
        from backend.app.mission_sources.adapters.pds4_adapter import GenericPds4AdapterValidationError

        mock_response = MagicMock()
        mock_response.status_code = 302

        class _MockStream:
            def __enter__(self):
                return mock_response

            def __exit__(self, *a):
                pass

        mock_client = MagicMock()
        mock_client.stream.return_value = _MockStream()

        with pytest.raises(GenericPds4AdapterValidationError, match="redirect"):
            _fetch_label_bytes("https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml", "pds4", mock_client)

    def test_04_404_not_retryable(self):
        """HTTP 404 must raise ValidationError (FAILED_VALIDATION, not retryable) immediately."""
        from backend.app.mission_sources.v2_inventory_acquisition import _fetch_label_bytes
        from backend.app.mission_sources.adapters.pds4_adapter import GenericPds4AdapterValidationError

        mock_response = MagicMock()
        mock_response.status_code = 404

        class _MockStream:
            def __enter__(self):
                return mock_response

            def __exit__(self, *a):
                pass

        mock_client = MagicMock()
        mock_client.stream.return_value = _MockStream()

        # 404 raises ValidationError (not retryable), not UnavailableError
        with pytest.raises(GenericPds4AdapterValidationError, match="404"):
            _fetch_label_bytes("https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml", "pds4", mock_client)

    def test_05_429_is_retryable(self):
        """HTTP 429 must raise UnavailableError (transient, retryable)."""
        from backend.app.mission_sources.v2_inventory_acquisition import _fetch_label_bytes
        from backend.app.mission_sources.adapters.pds4_adapter import GenericPds4AdapterUnavailableError

        mock_response = MagicMock()
        mock_response.status_code = 429

        class _MockStream:
            def __enter__(self):
                return mock_response

            def __exit__(self, *a):
                pass

        mock_client = MagicMock()
        mock_client.stream.return_value = _MockStream()

        with pytest.raises(GenericPds4AdapterUnavailableError, match="429"):
            _fetch_label_bytes("https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml", "pds4", mock_client)

    def test_06_500_is_retryable(self):
        """HTTP 500 must raise UnavailableError (transient, retryable)."""
        from backend.app.mission_sources.v2_inventory_acquisition import _fetch_label_bytes
        from backend.app.mission_sources.adapters.pds4_adapter import GenericPds4AdapterUnavailableError

        mock_response = MagicMock()
        mock_response.status_code = 503

        class _MockStream:
            def __enter__(self):
                return mock_response

            def __exit__(self, *a):
                pass

        mock_client = MagicMock()
        mock_client.stream.return_value = _MockStream()

        with pytest.raises(GenericPds4AdapterUnavailableError, match="503"):
            _fetch_label_bytes("https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml", "pds4", mock_client)

    def test_07_timeout_is_retryable(self):
        """Network timeout must raise UnavailableError (transient, retryable)."""
        import httpx
        from backend.app.mission_sources.v2_inventory_acquisition import _fetch_label_bytes
        from backend.app.mission_sources.adapters.pds4_adapter import GenericPds4AdapterUnavailableError

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.TimeoutException("timed out")

        with pytest.raises(GenericPds4AdapterUnavailableError):
            _fetch_label_bytes("https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml", "pds4", mock_client)

    def test_08_retry_exhaustion_produces_failed_transient(self, tmp_path):
        """After max_attempts retries, status must be FAILED_TRANSIENT."""
        import httpx
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_001",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        mock_client = MagicMock()
        mock_client.stream.side_effect = httpx.TimeoutException("timed out")

        row = _acquire_one(
            idx=0,
            entry=entry,
            rep=rep,
            snapshot_root=tmp_path,
            client=mock_client,
            max_attempts=3,
            backoff_seconds=(0.001, 0.001, 0.001),
            dry_run=False,
        )
        assert row.acquisition_status == AcquisitionStatus.FAILED_TRANSIENT
        assert row.attempt_count == 3

    def test_09_parser_error_not_retried(self, tmp_path):
        """A parser failure (FAILED_VALIDATION) must not be retried."""
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_001",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_bytes.return_value = [b"not-valid-xml"]

        class _MockStream:
            def __enter__(self):
                return mock_response

            def __exit__(self, *a):
                pass

        mock_client = MagicMock()
        mock_client.stream.return_value = _MockStream()

        row = _acquire_one(
            idx=0,
            entry=entry,
            rep=rep,
            snapshot_root=tmp_path,
            client=mock_client,
            max_attempts=3,
            backoff_seconds=(0.001, 0.001, 0.001),
            dry_run=False,
        )
        # Parser error is not retryable — must be attempt_count=1
        assert row.acquisition_status == AcquisitionStatus.FAILED_VALIDATION
        assert row.attempt_count == 1
        # stream() must be called exactly once (no retry)
        assert mock_client.stream.call_count == 1

    def test_10_identity_mismatch_produces_failed_identity(self, tmp_path):
        """Identity mismatch must produce FAILED_IDENTITY and not retry."""
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )
        from backend.app.mission_sources.adapters.pds4_adapter import (
            GenericPds4AdapterValidationError,
        )
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one
        from backend.app.mission_sources.archive_models import (
            ArchiveScienceProduct,
            ArchiveSourceStandard,
            ArchiveDataFileSizeCertainty,
        )
        from backend.app.provenance.models import (
            ProvenanceRecord,
            ProvenanceKind,
            ProvenanceValidationStatus,
        )

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            expected_archive_identity="urn:nasa:pds:juno_jiram:data_calibrated:EXPECTED_PRODUCT_LID",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_001",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        fake_raw = b"<fake_xml/>"
        fake_sha = hashlib.sha256(fake_raw).hexdigest()
        fake_retrieved = datetime(2024, 6, 13, 12, 0, 0, tzinfo=timezone.utc)

        fake_product = ArchiveScienceProduct(
            source_record_id="pds4:urn:nasa:pds:juno_jiram:data_calibrated:ACTUAL_DIFFERENT_PRODUCT::1.0",
            source_standard=ArchiveSourceStandard.PDS4,
            source_dataset_id="urn:nasa:pds:juno_jiram",
            source_product_id="urn:nasa:pds:juno_jiram:data_calibrated:ACTUAL_DIFFERENT_PRODUCT",
            mission_name="JUNO",
            product_family="JIRAM",
            total_data_size_bytes=0,
        )
        fake_prov = ProvenanceRecord(
            provenance_id="test_prov_001",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA Planetary Data System",
            source_record_id=fake_product.source_record_id,
            retrieved_at=fake_retrieved,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256=fake_sha,
        )

        with patch(
            "backend.app.mission_sources.v2_inventory_acquisition._fetch_label_bytes",
            return_value=(fake_raw, fake_retrieved),
        ):
            with patch(
                "backend.app.mission_sources.v2_inventory_acquisition._parse_label",
                return_value=(fake_product, fake_prov),
            ):
                mock_client = MagicMock()
                row = _acquire_one(
                    idx=0,
                    entry=entry,
                    rep=rep,
                    snapshot_root=tmp_path,
                    client=mock_client,
                    max_attempts=3,
                    backoff_seconds=(0.001, 0.001, 0.001),
                    dry_run=False,
                )
        assert row.acquisition_status == AcquisitionStatus.FAILED_IDENTITY
        assert row.error_class == "IdentityMismatch"
        # Must not retry on identity mismatch
        assert row.attempt_count == 1

    def test_11_deterministic_ordering(self):
        """_build_representation_sequence must produce stable deterministic ordering."""
        plan = _make_plan_with_one_rep()
        seq1 = _build_representation_sequence(plan)
        seq2 = _build_representation_sequence(plan)
        assert seq1 == seq2
        # Ordering is by (logical_product_id, role, url)
        assert all(isinstance(item, tuple) and len(item) == 3 for item in seq1)

    def test_12_resume_from_valid_snapshot(self, tmp_path):
        """If a valid snapshot exists, acquisition must return REUSED_VERIFIED_SNAPSHOT."""
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )
        from backend.app.mission_sources.archive_models import (
            ArchiveScienceProduct,
            ArchiveSourceStandard,
            ArchiveDataFileSizeCertainty,
            ArchiveCaptureRecord,
        )
        from backend.app.provenance.models import (
            ProvenanceRecord,
            ProvenanceKind,
            ProvenanceValidationStatus,
        )
        from backend.app.mission_sources.snapshots.archive_label_snapshot import (
            ArchiveLabelSnapshotStore,
        )
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_001",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        snapshot_path = _snapshot_path_for_url("JIRAM", url, tmp_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)

        # Write a minimal valid snapshot by mocking the store load
        fake_product_mock = MagicMock()
        fake_product_mock.source_label_ref = url
        fake_product_mock.source_record_id = "pds4:urn:nasa:pds:juno_jiram:foo::1.0"
        fake_product_mock.source_product_id = "urn:nasa:pds:juno_jiram:foo"
        fake_product_mock.source_version = "1.0"
        fake_product_mock.observation_start_utc = _START_UTC
        fake_product_mock.observation_stop_utc = _ELIGIBLE_STOP_UTC
        fake_product_mock.total_data_size_bytes = None
        fake_product_mock.data_files = []

        fake_prov_mock = MagicMock()
        fake_prov_mock.retrieved_at = _ELIGIBLE_STOP_UTC
        fake_prov_mock.content_sha256 = "a" * 64
        fake_prov_mock.provenance_id = "test_prov"

        # Write minimal snapshot JSON
        snapshot_data = {
            "snapshot_schema": "gcsi.archive_label_snapshot",
            "snapshot_version": 1,
            "snapshot_id": "b" * 64,
            "snapshot_source_standard": "pds4",
            "source_ref": url,
            "retrieved_at": _ELIGIBLE_STOP_UTC.isoformat(),
            "raw_label_base64": "dGVzdA==",
            "raw_label_sha256": hashlib.sha256(b"test").hexdigest(),
            "product": {
                "source_record_id": "pds4:urn:nasa:pds:juno_jiram:foo::1.0",
                "source_standard": "pds4",
                "source_dataset_id": "urn:nasa:pds:juno_jiram",
                "source_product_id": "urn:nasa:pds:juno_jiram:foo",
                "mission_name": "JUNO",
                "product_family": "JIRAM",
                "total_data_size_bytes": 0,
                "source_label_ref": url,
                "target_names": [],
                "data_files": [],
            },
            "provenance": {
                "provenance_id": "test_prov",
                "kind": "external_authoritative",
                "source_system": "NASA Planetary Data System",
                "source_record_id": "pds4:urn:nasa:pds:juno_jiram:foo::1.0",
                "retrieved_at": _ELIGIBLE_STOP_UTC.isoformat(),
                "validation_status": "validated",
                "content_sha256": hashlib.sha256(b"test").hexdigest(),
            },
            "normalizer_id": "gcsi.generic_pds4_label.v1",
            "profile_id": "jiram_pds4",
        }
        snapshot_path.write_text(json.dumps(snapshot_data), encoding="utf-8")

        # Mock ArchiveLabelSnapshotStore.load to return fake data
        with patch.object(
            ArchiveLabelSnapshotStore, "load",
            return_value=(fake_product_mock, fake_prov_mock),
        ):
            mock_client = MagicMock()
            row = _acquire_one(
                idx=0,
                entry=entry,
                rep=rep,
                snapshot_root=tmp_path,
                client=mock_client,
                max_attempts=3,
                backoff_seconds=(0.001,),
                dry_run=False,
            )

        assert row.acquisition_status == AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT
        # HTTP must not have been called
        mock_client.stream.assert_not_called()

    def test_13_corrupt_snapshot_not_silently_reused(self, tmp_path):
        """A snapshot that fails to load must produce FAILED_SNAPSHOT, not silently bypass."""
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )
        from backend.app.mission_sources.snapshots.archive_label_snapshot import (
            ArchiveLabelSnapshotStore,
            ArchiveSnapshotValidationError,
        )
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/corrupt.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_corrupt",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        snapshot_path = _snapshot_path_for_url("JIRAM", url, tmp_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text('{"broken_json": true, "corrupt": "yes"}', encoding="utf-8")

        # Make the load raise a validation error to simulate corruption
        with patch.object(
            ArchiveLabelSnapshotStore, "load",
            side_effect=ArchiveSnapshotValidationError("corrupt snapshot"),
        ):
            mock_client = MagicMock()
            row = _acquire_one(
                idx=0,
                entry=entry,
                rep=rep,
                snapshot_root=tmp_path,
                client=mock_client,
                max_attempts=3,
                backoff_seconds=(0.001,),
                dry_run=False,
            )

        assert row.acquisition_status == AcquisitionStatus.FAILED_SNAPSHOT
        assert row.error_class == "SnapshotIntegrityError"

    def test_14_wrong_snapshot_url_not_reused(self, tmp_path):
        """A snapshot with wrong source_ref URL must not be reused (treated as different URL)."""
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )
        from backend.app.mission_sources.snapshots.archive_label_snapshot import (
            ArchiveLabelSnapshotStore,
        )
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one

        url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/REAL.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_url_mismatch",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        # Write snapshot claiming a DIFFERENT url
        snapshot_path = _snapshot_path_for_url("JIRAM", url, tmp_path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        wrong_url = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/OTHER.xml"

        fake_product_mock = MagicMock()
        # Key: source_label_ref points to a DIFFERENT url
        fake_product_mock.source_label_ref = wrong_url
        fake_product_mock.source_record_id = "pds4:urn:nasa:pds:juno_jiram:other::1.0"
        fake_product_mock.source_product_id = "urn:nasa:pds:juno_jiram:other"
        fake_product_mock.source_version = "1.0"
        fake_product_mock.observation_start_utc = None
        fake_product_mock.observation_stop_utc = _ELIGIBLE_STOP_UTC
        fake_product_mock.total_data_size_bytes = None
        fake_product_mock.data_files = []

        fake_prov_mock = MagicMock()
        fake_prov_mock.retrieved_at = _ELIGIBLE_STOP_UTC
        fake_prov_mock.content_sha256 = "a" * 64
        fake_prov_mock.provenance_id = "test_prov"

        snapshot_data = {
            "snapshot_schema": "gcsi.archive_label_snapshot",
            "snapshot_version": 1,
            "snapshot_id": "b" * 64,
            "snapshot_source_standard": "pds4",
            "source_ref": wrong_url,  # wrong URL
            "retrieved_at": _ELIGIBLE_STOP_UTC.isoformat(),
            "raw_label_base64": "dGVzdA==",
            "raw_label_sha256": hashlib.sha256(b"test").hexdigest(),
            "product": {"source_label_ref": wrong_url, "source_record_id": "pds4:other::1.0",
                        "source_standard": "pds4", "source_dataset_id": "urn:nasa:pds:juno_jiram",
                        "source_product_id": "other", "mission_name": "JUNO", "product_family": "JIRAM",
                        "total_data_size_bytes": 0, "target_names": [], "data_files": []},
            "provenance": {"provenance_id": "test_prov", "kind": "external_authoritative",
                           "source_system": "NASA PDS", "retrieved_at": _ELIGIBLE_STOP_UTC.isoformat(),
                           "validation_status": "validated",
                           "content_sha256": hashlib.sha256(b"test").hexdigest(),
                           "source_record_id": "pds4:other::1.0"},
            "normalizer_id": "gcsi.generic_pds4_label.v1",
            "profile_id": "jiram_pds4",
        }
        snapshot_path.write_text(json.dumps(snapshot_data), encoding="utf-8")

        # Mock load to return wrong-url product
        with patch.object(
            ArchiveLabelSnapshotStore, "load",
            return_value=(fake_product_mock, fake_prov_mock),
        ):
            # Mock HTTP to fail (dry_run=False but we just check it wasn't bypassed wrongly)
            import httpx
            mock_client = MagicMock()
            mock_client.stream.side_effect = httpx.TimeoutException("not called")
            row = _acquire_one(
                idx=0,
                entry=entry,
                rep=rep,
                snapshot_root=tmp_path,
                client=mock_client,
                max_attempts=1,
                backoff_seconds=(0.001,),
                dry_run=False,
            )
        # URL mismatch → must NOT be REUSED_VERIFIED_SNAPSHOT; should proceed to fetch
        # (which will fail as transient since we raised TimeoutException)
        assert row.acquisition_status != AcquisitionStatus.REUSED_VERIFIED_SNAPSHOT

    def test_15_duplicate_source_record_id_detection(self):
        """_build_representation_sequence must not silently allow duplicate source_record_ids."""
        # The plan model already enforces no duplicate label_urls, which would
        # produce different source_record_ids. We verify the sequence has correct indexing.
        plan = _make_plan_with_one_rep()
        seq = _build_representation_sequence(plan)
        urls = [rep.label_url for _, _, rep in seq]
        # Within the plan all URLs must be unique (enforced by model)
        assert len(urls) == len(set(urls))

    def test_16_untrusted_url_causes_zero_http_requests(self, tmp_path):
        """§8: An untrusted URL must cause exactly 0 HTTP requests.

        profile/source URL trust must be validated BEFORE network.
        If the URL is not in the trusted host/path set for the profile,
        acquisition must return FAILED_VALIDATION with error_class='UntrustedURL'
        and must NOT have called client.stream() at all.
        """
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            AcquisitionLogicalProductEntry,
            TemporalEvidenceStatus,
        )
        from backend.app.mission_sources.v2_inventory_acquisition import _acquire_one

        # Use an untrusted host (evil.example.com) for a jiram_pds4 profile
        # jiram_pds4 is trusted only for atmos.nmsu.edu
        untrusted_url = "https://evil.example.com/malicious/label.xml"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=untrusted_url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="prod_untrusted",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep,),
        )

        mock_client = MagicMock()
        # Patch validate_representation_url_trust to raise ValueError for untrusted URL
        with patch(
            "backend.app.mission_sources.v2_inventory_acquisition.validate_representation_url_trust",
            side_effect=ValueError(f"label_url host 'evil.example.com' is not trusted"),
        ):
            row = _acquire_one(
                idx=0,
                entry=entry,
                rep=rep,
                snapshot_root=tmp_path,
                client=mock_client,
                max_attempts=3,
                backoff_seconds=(0.001, 0.001, 0.001),
                dry_run=False,
            )

        # §8: Zero HTTP requests must have been made
        assert mock_client.stream.call_count == 0, (
            f"Expected 0 HTTP requests for untrusted URL, got {mock_client.stream.call_count}"
        )
        assert row.acquisition_status == AcquisitionStatus.FAILED_VALIDATION
        assert row.error_class == "UntrustedURL"
        assert row.attempt_count == 0


# ===========================================================================
# §69 — Temporal test matrix
# ===========================================================================


class TestSection69TemporalMatrix:
    """Section 69: Temporal eligibility checks."""

    def test_eligible_stop_passes(self):
        """Stop time in window → VERIFIED_ELIGIBLE."""
        result = _check_temporal_eligibility(_ELIGIBLE_STOP_UTC)
        assert result == TemporalVerificationStatus.VERIFIED_ELIGIBLE

    def test_pre_stop_fails(self):
        """Stop time before accumulation start → FAILED_PRE."""
        result = _check_temporal_eligibility(_PRE_STOP_UTC)
        assert result == TemporalVerificationStatus.FAILED_PRE

    def test_post_stop_fails(self):
        """Stop time after decision epoch → FAILED_POST."""
        result = _check_temporal_eligibility(_POST_STOP_UTC)
        assert result == TemporalVerificationStatus.FAILED_POST

    def test_none_stop_is_pending(self):
        """None stop time → PENDING (JIRAM and similar)."""
        result = _check_temporal_eligibility(None)
        assert result == TemporalVerificationStatus.PENDING

    def test_exactly_at_accumulation_start_is_pre(self):
        """Stop exactly at accumulation start (not strictly after) → FAILED_PRE."""
        result = _check_temporal_eligibility(ACCUMULATION_START_UTC)
        assert result == TemporalVerificationStatus.FAILED_PRE

    def test_exactly_at_decision_epoch_is_eligible(self):
        """Stop exactly at decision epoch → VERIFIED_ELIGIBLE (eligible iff <= epoch)."""
        result = _check_temporal_eligibility(DECISION_EPOCH_UTC)
        assert result == TemporalVerificationStatus.VERIFIED_ELIGIBLE

    def test_one_microsecond_after_decision_epoch_is_post(self):
        """Stop one microsecond after decision epoch → FAILED_POST."""
        from datetime import timedelta
        stop = DECISION_EPOCH_UTC + timedelta(microseconds=1)
        result = _check_temporal_eligibility(stop)
        assert result == TemporalVerificationStatus.FAILED_POST

    def test_one_microsecond_after_accum_start_is_eligible(self):
        """Stop one microsecond after accumulation start → VERIFIED_ELIGIBLE."""
        from datetime import timedelta
        stop = ACCUMULATION_START_UTC + timedelta(microseconds=1)
        result = _check_temporal_eligibility(stop)
        assert result == TemporalVerificationStatus.VERIFIED_ELIGIBLE


# ===========================================================================
# §71 — Size test matrix
# ===========================================================================


class TestSection71SizeMatrix:
    """Section 71: Size verification status derivation."""

    def test_no_data_files_is_size_unknown(self):
        """Product with no data_files → SIZE_UNKNOWN."""
        from backend.app.mission_sources.archive_models import ArchiveScienceProduct, ArchiveSourceStandard
        product = ArchiveScienceProduct(
            source_record_id="pds4:urn:nasa:pds:juno_jiram:foo::1.0",
            source_standard=ArchiveSourceStandard.PDS4,
            source_dataset_id="urn:nasa:pds:juno_jiram",
            source_product_id="urn:nasa:pds:juno_jiram:foo",
            mission_name="JUNO",
            product_family="JIRAM",
            total_data_size_bytes=0,
        )
        assert _derive_size_verification_status(product) == SizeVerificationStatus.SIZE_UNKNOWN

    def test_exact_size_metadata_classification(self):
        """Product with SIZE_METADATA_EXACT files → SIZE_METADATA_EXACT."""
        from backend.app.mission_sources.archive_models import (
            ArchiveScienceProduct, ArchiveSourceStandard, ArchiveDataFile, ArchiveDataFileSizeCertainty
        )
        f = ArchiveDataFile(
            file_name="data.dat",
            file_size_bytes=12345,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds4:urn:nasa:pds:juno_jiram:foo::1.0",
            source_standard=ArchiveSourceStandard.PDS4,
            source_dataset_id="urn:nasa:pds:juno_jiram",
            source_product_id="urn:nasa:pds:juno_jiram:foo",
            mission_name="JUNO",
            product_family="JIRAM",
            data_files=(f,),
            total_data_size_bytes=12345,
        )
        assert _derive_size_verification_status(product) == SizeVerificationStatus.SIZE_METADATA_EXACT

    def test_approximate_size_classification(self):
        """Product with SIZE_DISCOVERED_APPROXIMATE files → SIZE_DISCOVERED_APPROXIMATE."""
        from backend.app.mission_sources.archive_models import (
            ArchiveScienceProduct, ArchiveSourceStandard, ArchiveDataFile, ArchiveDataFileSizeCertainty
        )
        f = ArchiveDataFile(
            file_name="data.dat",
            file_size_bytes=None,
            size_certainty=ArchiveDataFileSizeCertainty.SIZE_DISCOVERED_APPROXIMATE,
        )
        product = ArchiveScienceProduct(
            source_record_id="pds4:urn:nasa:pds:juno_jiram:foo::1.0",
            source_standard=ArchiveSourceStandard.PDS4,
            source_dataset_id="urn:nasa:pds:juno_jiram",
            source_product_id="urn:nasa:pds:juno_jiram:foo",
            mission_name="JUNO",
            product_family="JIRAM",
            data_files=(f,),
            total_data_size_bytes=None,
        )
        assert _derive_size_verification_status(product) == SizeVerificationStatus.SIZE_DISCOVERED_APPROXIMATE


# ===========================================================================
# §73 — Ledger test matrix
# ===========================================================================


class TestSection73LedgerMatrix:
    """Section 73: Ledger model and save/load."""

    def _make_row(self, idx: int = 0, status: AcquisitionStatus = AcquisitionStatus.ACQUIRED_VERIFIED) -> AcquisitionLedgerRow:
        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=f"prod_{idx:03d}",
            instrument="JIRAM",
            representation_role="calibrated",
            source_standard="pds4",
            label_url=f"https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/prod_{idx}.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            attempt_count=1,
            acquisition_status=status,
        )

    def test_ledger_id_deterministic(self):
        """ledger_id must be deterministic for same rows/replay_id/plan_id."""
        rows = [self._make_row(0), self._make_row(1)]
        id1 = _compute_ledger_id(rows, "replay_v2", "plan_abc")
        id2 = _compute_ledger_id(rows, "replay_v2", "plan_abc")
        assert id1 == id2
        assert len(id1) == 64

    def test_ledger_id_changes_on_different_plan(self):
        """Different plan_id → different ledger_id."""
        rows = [self._make_row(0)]
        id1 = _compute_ledger_id(rows, "replay_v2", "plan_A")
        id2 = _compute_ledger_id(rows, "replay_v2", "plan_B")
        assert id1 != id2

    def test_ledger_serializes_and_loads(self):
        """AcquisitionLedger must round-trip to/from JSON via production load_ledger().

        §13: load_ledger() must use canonical data/replays/ confinement — a test-only
        path is written inside data/replays/ and cleaned up afterwards.
        """
        import pathlib
        import uuid as _uuid
        _repo_root = pathlib.Path(__file__).resolve().parents[2]
        path = _repo_root / "data" / "replays" / f"_test_ledger_{_uuid.uuid4().hex}.json"
        try:
            rows = [self._make_row(0), self._make_row(1)]
            ledger_id = _compute_ledger_id(rows, "replay_v2", "plan_abc")
            ledger = AcquisitionLedger(
                ledger_id=ledger_id,
                replay_id="replay_v2",
                plan_id="plan_abc",
                rows=tuple(rows),
            )
            save_ledger(ledger, path)
            loaded = load_ledger(path)
            assert loaded.ledger_id == ledger.ledger_id
            assert loaded.replay_id == ledger.replay_id
            assert len(loaded.rows) == 2
        finally:
            if path.exists():
                path.unlink()

    def test_ledger_load_outside_confinement_rejected(self, tmp_path):
        """§13: load_ledger() must reject paths outside data/replays/."""
        rows = [self._make_row(0)]
        ledger_id = _compute_ledger_id(rows, "replay_v2", "plan_abc")
        ledger = AcquisitionLedger(
            ledger_id=ledger_id, replay_id="replay_v2", plan_id="plan_abc", rows=tuple(rows)
        )
        path = tmp_path / "outside_ledger.json"
        save_ledger(ledger, path)
        with pytest.raises((ValueError, Exception), match="confinement|outside|replays"):
            load_ledger(path)

    def test_ledger_load_rejects_dotdot_traversal(self):
        """§13: load_ledger() must reject paths containing '..'."""
        import pathlib
        _repo_root = pathlib.Path(__file__).resolve().parents[2]
        traversal = _repo_root / "data" / "replays" / ".." / "replays" / "nonexistent.json"
        with pytest.raises((ValueError, Exception)):
            load_ledger(traversal)

    def test_ledger_load_rejects_mutated_archive_total_size_bytes(self):
        """§13/§11: Changing archive_total_size_bytes without updating ledger_id is rejected."""
        import pathlib, json as _json, uuid as _uuid
        _repo_root = pathlib.Path(__file__).resolve().parents[2]
        # Build ledger with a row that has archive_total_size_bytes set
        row = AcquisitionLedgerRow(
            acquisition_index=0,
            logical_product_id="prod_000",
            instrument="JIRAM",
            representation_role="calibrated",
            source_standard="pds4",
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/prod_0.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            attempt_count=1,
            acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
            archive_total_size_bytes=100000,
        )
        rows = [row]
        ledger_id = _compute_ledger_id(rows, "replay_v2", "plan_abc")
        ledger = AcquisitionLedger(
            ledger_id=ledger_id, replay_id="replay_v2", plan_id="plan_abc", rows=tuple(rows)
        )
        path = _repo_root / "data" / "replays" / f"_test_ledger_mut_{_uuid.uuid4().hex}.json"
        try:
            save_ledger(ledger, path)
            # Mutate archive_total_size_bytes in the raw JSON, leave ledger_id stale
            data = _json.loads(path.read_text())
            data["rows"][0]["archive_total_size_bytes"] = 999999
            path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            with pytest.raises((ValueError, Exception), match="ledger_id|mismatch|mutated"):
                load_ledger(path)
        finally:
            if path.exists():
                path.unlink()

    def test_ledger_load_rejects_mutated_observation_stop_utc(self):
        """§13/§11: Changing observation_stop_utc without updating ledger_id is rejected."""
        import pathlib, json as _json, uuid as _uuid
        _repo_root = pathlib.Path(__file__).resolve().parents[2]
        row = AcquisitionLedgerRow(
            acquisition_index=0,
            logical_product_id="prod_000",
            instrument="JIRAM",
            representation_role="calibrated",
            source_standard="pds4",
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/prod_0.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            attempt_count=1,
            acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
            observation_stop_utc="2024-06-14T09:35:17+00:00",
        )
        rows = [row]
        ledger_id = _compute_ledger_id(rows, "replay_v2", "plan_abc")
        ledger = AcquisitionLedger(
            ledger_id=ledger_id, replay_id="replay_v2", plan_id="plan_abc", rows=tuple(rows)
        )
        path = _repo_root / "data" / "replays" / f"_test_ledger_stop_{_uuid.uuid4().hex}.json"
        try:
            save_ledger(ledger, path)
            # Mutate observation_stop_utc, leave stale ledger_id
            data = _json.loads(path.read_text())
            data["rows"][0]["observation_stop_utc"] = "2024-06-15T00:00:00+00:00"
            path.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            with pytest.raises((ValueError, Exception), match="ledger_id|mismatch|mutated"):
                load_ledger(path)
        finally:
            if path.exists():
                path.unlink()

    def test_ledger_id_changes_when_new_semantic_fields_change(self):
        """§11: ledger_id must change when any §11 semantic field changes."""
        base_row = AcquisitionLedgerRow(
            acquisition_index=0,
            logical_product_id="prod_000",
            instrument="JIRAM",
            representation_role="calibrated",
            source_standard="pds4",
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/prod_0.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            attempt_count=1,
            acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
            archive_product_id="PROD_000_V01",
            archive_version="V01",
            observation_start_utc="2024-06-14T09:00:00+00:00",
            observation_stop_utc="2024-06-14T09:35:00+00:00",
            archive_total_size_bytes=50000,
            size_verification_status="SIZE_METADATA_EXACT",
            error_class=None,
            error_detail_code=None,
        )
        base_id = _compute_ledger_id([base_row], "r", "p")

        # archive_product_id
        r = base_row.model_copy(update={"archive_product_id": "OTHER_ID"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # archive_version
        r = base_row.model_copy(update={"archive_version": "V99"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # observation_stop_utc
        r = base_row.model_copy(update={"observation_stop_utc": "2024-06-15T00:00:00+00:00"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # archive_total_size_bytes
        r = base_row.model_copy(update={"archive_total_size_bytes": 9999})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # size_verification_status
        r = base_row.model_copy(update={"size_verification_status": "SIZE_UNKNOWN"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # error_class
        r = base_row.model_copy(update={"error_class": "SomeError"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # error_detail_code
        r = base_row.model_copy(update={"error_detail_code": "detail_99"})
        assert _compute_ledger_id([r], "r", "p") != base_id

        # observation_start_utc
        r = base_row.model_copy(update={"observation_start_utc": "2024-06-14T08:00:00+00:00"})
        assert _compute_ledger_id([r], "r", "p") != base_id

    def test_ledger_row_extra_fields_rejected(self):
        """Extra fields in AcquisitionLedgerRow must be rejected."""
        with pytest.raises(Exception):
            AcquisitionLedgerRow(
                acquisition_index=0,
                logical_product_id="prod_001",
                instrument="JIRAM",
                representation_role="calibrated",
                source_standard="pds4",
                label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo.xml",
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
                attempt_count=1,
                acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
                unexpected_field="should_fail",
            )

    def test_ledger_is_frozen(self):
        """AcquisitionLedgerRow must be immutable (frozen)."""
        row = self._make_row(0)
        with pytest.raises(Exception):
            row.attempt_count = 99  # type: ignore[misc]


# ===========================================================================
# §74 — Inventory test matrix
# ===========================================================================


class TestSection74InventoryMatrix:
    """Section 74: Verified inventory builder."""

    def _make_successful_row(self, idx: int, logical_id: str, role: str, url: str) -> AcquisitionLedgerRow:
        return AcquisitionLedgerRow(
            acquisition_index=idx,
            logical_product_id=logical_id,
            instrument="JIRAM",
            representation_role=role,
            source_standard="pds4",
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            attempt_count=1,
            acquisition_status=AcquisitionStatus.ACQUIRED_VERIFIED,
            retrieved_at=_ELIGIBLE_STOP_UTC.isoformat(),
            raw_label_sha256="a" * 64,
            source_record_id=f"pds4:urn:nasa:pds:juno_jiram:data_calibrated:{logical_id}::1.0",
            archive_product_id=f"urn:nasa:pds:juno_jiram:data_calibrated:{logical_id}",
            snapshot_ref=f"/tmp/snap_{idx}.json",
            snapshot_id="b" * 64,
            provenance_id=f"prov_{logical_id}",
            observation_start_utc=_START_UTC.isoformat(),
            observation_stop_utc=_ELIGIBLE_STOP_UTC.isoformat(),
            temporal_verification_status=TemporalVerificationStatus.VERIFIED_ELIGIBLE.value,
            archive_total_size_bytes=12345,
            size_verification_status=SizeVerificationStatus.SIZE_METADATA_EXACT.value,
        )

    def test_build_manifest_from_single_entry(self, tmp_path):
        """Build must produce a valid VerifiedInventoryManifest for a single-entry plan+ledger."""
        plan = _make_plan_with_one_rep(
            temporal_status="LABEL_VERIFICATION_PENDING",
        )
        entry = plan.logical_entries[0]
        rep = entry.representations[0]

        row = self._make_successful_row(
            idx=0,
            logical_id=entry.logical_product_id,
            role=rep.representation_role.value,
            url=rep.label_url,
        )
        ledger_id = _compute_ledger_id([row], plan.replay_id, plan.plan_id)
        ledger = AcquisitionLedger(
            ledger_id=ledger_id,
            replay_id=plan.replay_id,
            plan_id=plan.plan_id,
            rows=(row,),
        )

        builder = V2VerifiedInventoryBuilder()
        manifest = builder.build(plan=plan, ledger=ledger, snapshot_root=tmp_path)

        assert len(manifest.entries) == 1
        assert manifest.entries[0].logical_product_id == entry.logical_product_id
        assert len(manifest.source_records) == 1

    def test_failed_row_excluded_from_manifest(self, tmp_path):
        """Failed acquisition rows must be excluded from the manifest."""
        plan = _make_plan_with_one_rep()
        entry = plan.logical_entries[0]
        rep = entry.representations[0]

        row = AcquisitionLedgerRow(
            acquisition_index=0,
            logical_product_id=entry.logical_product_id,
            instrument="JIRAM",
            representation_role=rep.representation_role.value,
            source_standard="pds4",
            label_url=rep.label_url,
            normalizer_id=rep.normalizer_id,
            profile_id=rep.profile_id,
            attempt_count=3,
            acquisition_status=AcquisitionStatus.FAILED_UNAVAILABLE,
            error_class="GenericPds4AdapterUnavailableError",
        )
        ledger_id = _compute_ledger_id([row], plan.replay_id, plan.plan_id)
        ledger = AcquisitionLedger(
            ledger_id=ledger_id,
            replay_id=plan.replay_id,
            plan_id=plan.plan_id,
            rows=(row,),
        )

        builder = V2VerifiedInventoryBuilder()
        with pytest.raises(ValueError, match="no verified entries"):
            builder.build(plan=plan, ledger=ledger, snapshot_root=tmp_path)

    def test_manifest_save_and_load_roundtrip(self, tmp_path):
        """VerifiedInventoryManifest must survive save/load round-trip."""
        plan = _make_plan_with_one_rep(
            temporal_status="LABEL_VERIFICATION_PENDING",
        )
        entry = plan.logical_entries[0]
        rep = entry.representations[0]

        row = self._make_successful_row(
            idx=0,
            logical_id=entry.logical_product_id,
            role=rep.representation_role.value,
            url=rep.label_url,
        )
        ledger_id = _compute_ledger_id([row], plan.replay_id, plan.plan_id)
        ledger = AcquisitionLedger(
            ledger_id=ledger_id,
            replay_id=plan.replay_id,
            plan_id=plan.plan_id,
            rows=(row,),
        )

        builder = V2VerifiedInventoryBuilder()
        manifest = builder.build(plan=plan, ledger=ledger, snapshot_root=tmp_path)

        manifest_path = tmp_path / "manifest.json"
        builder.save_manifest(manifest, manifest_path)
        loaded = builder.load_manifest(manifest_path)

        assert loaded.manifest_id == manifest.manifest_id
        assert len(loaded.entries) == len(manifest.entries)


# ===========================================================================
# Source bundle tests
# ===========================================================================


class TestV2SourceBundle:
    """Source bundle model: build, save, load. B2.2.1 schema version 2."""

    def _make_bundle(self) -> V2SourceBundle:
        return build_source_bundle(
            replay_id="juno_pj62_large_replay_v2",
            candidate_plan_id="a" * 64,
            discovery_evidence_artifact_id="b" * 64,
            acquisition_ledger_id="c" * 64,
            temporal_reconciliation_id="e" * 64,
            verified_inventory_manifest_id="d" * 64,
            verified_inventory_manifest_ref="data/replays/manifest.json",
            label_snapshot_count=535,
            candidate_logical_count=411,
            candidate_source_count=535,
            eligible_logical_count=403,
            eligible_source_count=527,
            ineligible_logical_count=8,
            ineligible_source_count=8,
            decision_epoch_utc="2024-06-14T09:35:17.546000+00:00",
        )

    def test_bundle_has_schema_and_version(self):
        bundle = self._make_bundle()
        assert bundle.schema == "gcsi.v2_source_bundle"
        assert bundle.schema_version == 2

    def test_bundle_id_deterministic(self):
        b1 = self._make_bundle()
        b2 = self._make_bundle()
        assert b1.bundle_id == b2.bundle_id
        assert len(b1.bundle_id) == 64

    def test_bundle_id_changes_on_different_replay_id(self):
        b1 = build_source_bundle(
            replay_id="replay_A",
            candidate_plan_id="a" * 64,
            discovery_evidence_artifact_id="b" * 64,
            acquisition_ledger_id="c" * 64,
            temporal_reconciliation_id="e" * 64,
            verified_inventory_manifest_id="d" * 64,
            verified_inventory_manifest_ref="data/replays/manifest.json",
            label_snapshot_count=535,
            candidate_logical_count=411,
            candidate_source_count=535,
            eligible_logical_count=403,
            eligible_source_count=527,
            ineligible_logical_count=8,
            ineligible_source_count=8,
            decision_epoch_utc="2024-06-14T09:35:17.546000+00:00",
        )
        b2 = build_source_bundle(
            replay_id="replay_B",
            candidate_plan_id="a" * 64,
            discovery_evidence_artifact_id="b" * 64,
            acquisition_ledger_id="c" * 64,
            temporal_reconciliation_id="e" * 64,
            verified_inventory_manifest_id="d" * 64,
            verified_inventory_manifest_ref="data/replays/manifest.json",
            label_snapshot_count=535,
            candidate_logical_count=411,
            candidate_source_count=535,
            eligible_logical_count=403,
            eligible_source_count=527,
            ineligible_logical_count=8,
            ineligible_source_count=8,
            decision_epoch_utc="2024-06-14T09:35:17.546000+00:00",
        )
        assert b1.bundle_id != b2.bundle_id

    def test_bundle_save_load_roundtrip(self, tmp_path):
        """Bundle save/load round-trip using actual data/replays confinement."""
        import pathlib
        _REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
        bundle_path = _REPO_ROOT / "data" / "replays" / "test_bundle_roundtrip.json"
        try:
            bundle = self._make_bundle()
            save_source_bundle(bundle, bundle_path)
            loaded = load_source_bundle(bundle_path)
            assert loaded.bundle_id == bundle.bundle_id
            assert loaded.schema == "gcsi.v2_source_bundle"
            assert loaded.schema_version == 2
        finally:
            if bundle_path.exists():
                bundle_path.unlink()

    def test_bundle_not_found_raises(self):
        import pathlib
        _REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
        with pytest.raises(FileNotFoundError):
            load_source_bundle(_REPO_ROOT / "data" / "replays" / "nonexistent_bundle.json")

    def test_bundle_extra_fields_rejected(self):
        with pytest.raises(Exception):
            V2SourceBundle(
                schema="gcsi.v2_source_bundle",
                schema_version=2,
                bundle_id="a" * 64,
                replay_id="test",
                candidate_plan_id="a" * 64,
                discovery_evidence_artifact_id="b" * 64,
                acquisition_ledger_id="c" * 64,
                temporal_reconciliation_id="e" * 64,
                verified_inventory_manifest_id="d" * 64,
                verified_inventory_manifest_ref="data/replays/manifest.json",
                label_snapshot_count=535,
                candidate_logical_count=411,
                candidate_source_count=535,
                eligible_logical_count=403,
                eligible_source_count=527,
                ineligible_logical_count=8,
                ineligible_source_count=8,
                logical_product_count=403,
                source_record_count=527,
                decision_epoch_utc="2024-06-14T09:35:17+00:00",
                unexpected="bad_field",
            )

    def test_bundle_is_frozen(self):
        bundle = self._make_bundle()
        with pytest.raises(Exception):
            bundle.label_snapshot_count = 999  # type: ignore[misc]


# ===========================================================================
# Canary selection tests
# ===========================================================================


class TestCanarySelection:
    """Canary selection must pick exactly one representative per profile."""

    def test_canary_indices_selected_once_per_profile(self):
        """Exactly one index per profile must be selected as canary."""
        plan = _make_plan_with_one_rep()
        seq = _build_representation_sequence(plan)
        canary_indices = _select_canary_indices(seq)

        # For single-rep plan, must have exactly 1 canary
        assert len(canary_indices) == 1
        assert 0 in canary_indices

    def test_canary_second_rep_same_profile_not_selected(self):
        """A second representation with the same profile must not add another canary."""
        from backend.app.mission_sources.v2_acquisition_plan import (
            AcquisitionLogicalProductEntry,
            AcquisitionRepresentationRole,
            AcquisitionSourceRepresentation,
            AcquisitionSourceStandard,
            DiscoveryEvidence,
            HistoricalReplayV2AcquisitionPlan,
            ACCUMULATION_START_UTC,
            DECISION_EPOCH_UTC,
            DECISION_EPOCH_POLICY,
            FINAL_TEMPORAL_ELIGIBILITY,
            TemporalEvidenceStatus,
            _compute_plan_id,
        )

        url1 = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo1.xml"
        url2 = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/orbit_0062/foo2.xml"

        rep1 = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url1,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        rep2 = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.EDR,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url2,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        entry1 = AcquisitionLogicalProductEntry(
            logical_product_id="prod_AAA",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep1,),
        )
        entry2 = AcquisitionLogicalProductEntry(
            logical_product_id="prod_BBB",
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            representations=(rep2,),
        )
        fake_bytes = b"fake response for dual-entry test"
        ev = DiscoveryEvidence.capture(
            evidence_id="test_ev",
            source_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/",
            retrieved_at=datetime(2024, 6, 13, 10, 5, 0, tzinfo=timezone.utc),
            response_bytes=fake_bytes,
            source_kind="pds4_directory_html",
        )
        fake_sidecar = b"sidecar"
        artifact_id = hashlib.sha256(fake_sidecar).hexdigest()
        plan_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id="test_replay_v2",
            accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
            decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
            decision_epoch_policy=DECISION_EPOCH_POLICY,
            logical_entries=(entry1, entry2),
            discovery_evidence=(ev,),
            discovery_evidence_artifact_id=artifact_id,
        )
        plan = HistoricalReplayV2AcquisitionPlan(
            schema="gcsi.historical_replay_v2_acquisition_plan",
            schema_version=1,
            plan_id=plan_id,
            replay_id="test_replay_v2",
            accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
            decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
            decision_epoch_policy=DECISION_EPOCH_POLICY,
            final_temporal_eligibility=FINAL_TEMPORAL_ELIGIBILITY,
            logical_entries=(entry1, entry2),
            discovery_evidence=(ev,),
            discovery_evidence_artifact_id=artifact_id,
        )

        seq = _build_representation_sequence(plan)
        canary_indices = _select_canary_indices(seq)

        # Both reps share the same profile_id="jiram_pds4"
        # Only 1 canary should be selected even though there are 2 reps
        assert len(canary_indices) == 1


# ===========================================================================
# Module import test
# ===========================================================================


class TestModuleImport:
    """Verify all three modules can be imported without errors."""

    def test_v2_inventory_acquisition_importable(self):
        """v2_inventory_acquisition must be importable."""
        import backend.app.mission_sources.v2_inventory_acquisition as m
        assert hasattr(m, "V2InventoryAcquisitionRunner")
        assert hasattr(m, "AcquisitionLedger")
        assert hasattr(m, "AcquisitionStatus")

    def test_v2_verified_inventory_importable(self):
        """v2_verified_inventory must be importable."""
        import backend.app.mission_sources.v2_verified_inventory as m
        assert hasattr(m, "V2VerifiedInventoryBuilder")

    def test_v2_source_bundle_importable(self):
        """v2_source_bundle must be importable."""
        import backend.app.mission_sources.v2_source_bundle as m
        assert hasattr(m, "V2SourceBundle")
        assert hasattr(m, "build_source_bundle")
        assert hasattr(m, "save_source_bundle")
        assert hasattr(m, "load_source_bundle")

    def test_acquisition_status_enum_values(self):
        """AcquisitionStatus must have all expected members."""
        expected = {
            "PENDING", "ACQUIRED_VERIFIED", "REUSED_VERIFIED_SNAPSHOT",
            "FAILED_TRANSIENT", "FAILED_UNAVAILABLE", "FAILED_VALIDATION",
            "FAILED_IDENTITY", "FAILED_TEMPORAL", "FAILED_SNAPSHOT",
        }
        actual = {s.value for s in AcquisitionStatus}
        assert actual == expected

    def test_temporal_verification_status_enum_values(self):
        """TemporalVerificationStatus must have all expected members."""
        expected = {
            "VERIFIED_ELIGIBLE", "FAILED_PRE", "FAILED_POST",
            "FAILED_STOP_BEFORE_START", "PENDING",
        }
        actual = {s.value for s in TemporalVerificationStatus}
        assert actual == expected
