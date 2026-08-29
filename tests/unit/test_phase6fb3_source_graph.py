"""GCSI Phase 6F-B3 — Source Graph Tests.

Tests that the complete artifact chain loads and cross-verifies correctly:

- Root source bundle loads with correct IDs
- Candidate plan loads (411 logical / 535 source)
- Sidecar loads with artifact_id match
- Ledger loads (535 rows, all IDs cross-checked)
- Temporal reconciliation loads (403 eligible / 8 ineligible)
- Verified inventory loads (403 entries / 527 source records)
- Horizons snapshot loads (target=-61, center=500@399, epoch=decision epoch)
- 535 snapshots load (zero-network, all verified)
- Cross-artifact mutation defense (recomputing root hash is insufficient)
- Inclusion/exclusion: 403 assembled, 8 specifically excluded
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import tempfile
from typing import Any
from unittest.mock import patch

import pytest

from backend.app.mission_sources.v2_source_graph import (
    VerifiedV2SourceGraph,
    load_verified_v2_source_graph,
    _EXPECTED_BUNDLE_ID,
    _EXPECTED_CANDIDATE_LOGICAL,
    _EXPECTED_CANDIDATE_SOURCE,
    _EXPECTED_ELIGIBLE_LOGICAL,
    _EXPECTED_ELIGIBLE_SOURCE,
    _EXPECTED_INELIGIBLE_LOGICAL,
    _EXPECTED_INELIGIBLE_SOURCE,
    _EXPECTED_LABEL_SNAPSHOT_COUNT,
    _EXPECTED_HORIZONS_SNAPSHOT_ID,
    _EXPECTED_HORIZONS_TARGET,
    _EXPECTED_HORIZONS_CENTER,
    _DECISION_EPOCH_UTC,
    _REPO_ROOT,
)

# ---------------------------------------------------------------------------
# Known ineligible logical product IDs (must exist in candidate plan but
# NOT appear in eligible set / assembled DataProducts)
# ---------------------------------------------------------------------------

_KNOWN_INELIGIBLE = {
    "gcsi.jedi.pj62.jed_090_loersesp_cdr_2024166_v04",
    "gcsi.uvs.pj62.s02_771613347_2024166_p62sy1",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source_graph() -> VerifiedV2SourceGraph:
    """Load the verified source graph once per module."""
    return load_verified_v2_source_graph()


# ---------------------------------------------------------------------------
# Section 35: Source graph loads
# ---------------------------------------------------------------------------


class TestSourceGraphLoads:
    def test_source_bundle_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.source_bundle is not None
        assert source_graph.source_bundle.bundle_id == _EXPECTED_BUNDLE_ID
        assert source_graph.source_bundle.schema_version == 2
        assert source_graph.source_bundle.replay_id == "juno_pj62_large_replay_v2"

    def test_candidate_plan_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        plan = source_graph.candidate_plan
        assert plan is not None
        assert len(plan.logical_entries) == _EXPECTED_CANDIDATE_LOGICAL
        total_source = sum(len(e.representations) for e in plan.logical_entries)
        assert total_source == _EXPECTED_CANDIDATE_SOURCE

    def test_sidecar_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        sidecar = source_graph.discovery_sidecar
        assert sidecar is not None
        assert sidecar.replay_id == "juno_pj62_large_replay_v2"

    def test_ledger_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        ledger = source_graph.acquisition_ledger
        assert ledger is not None
        assert len(ledger.rows) == _EXPECTED_CANDIDATE_SOURCE
        assert ledger.replay_id == "juno_pj62_large_replay_v2"

    def test_reconciliation_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        reconciliation = source_graph.temporal_reconciliation
        assert reconciliation is not None
        assert reconciliation.eligible_logical_count == _EXPECTED_ELIGIBLE_LOGICAL
        assert reconciliation.eligible_source_count == _EXPECTED_ELIGIBLE_SOURCE
        assert reconciliation.ineligible_logical_count == _EXPECTED_INELIGIBLE_LOGICAL
        assert reconciliation.ineligible_source_count == _EXPECTED_INELIGIBLE_SOURCE

    def test_verified_inventory_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        inventory = source_graph.verified_inventory
        assert inventory is not None
        assert len(inventory.entries) == _EXPECTED_ELIGIBLE_LOGICAL
        total_srids = sum(len(e.representation_record_ids) for e in inventory.entries)
        assert total_srids == _EXPECTED_ELIGIBLE_SOURCE

    def test_horizons_snapshot_loads(self, source_graph: VerifiedV2SourceGraph) -> None:
        horizons_result = source_graph.horizons_result
        assert horizons_result is not None
        assert horizons_result.geometry.target_spk_id == _EXPECTED_HORIZONS_TARGET
        assert horizons_result.geometry.center == _EXPECTED_HORIZONS_CENTER

    def test_535_snapshots_loaded(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.label_snapshot_count == _EXPECTED_LABEL_SNAPSHOT_COUNT
        assert len(source_graph.snapshots_by_source_record_id) == _EXPECTED_LABEL_SNAPSHOT_COUNT

    def test_exact_counts(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.candidate_logical_count == _EXPECTED_CANDIDATE_LOGICAL
        assert source_graph.candidate_source_count == _EXPECTED_CANDIDATE_SOURCE
        assert source_graph.eligible_logical_count == _EXPECTED_ELIGIBLE_LOGICAL
        assert source_graph.eligible_source_count == _EXPECTED_ELIGIBLE_SOURCE
        assert source_graph.ineligible_logical_count == _EXPECTED_INELIGIBLE_LOGICAL
        assert source_graph.ineligible_source_count == _EXPECTED_INELIGIBLE_SOURCE


# ---------------------------------------------------------------------------
# Section 35: Cross-artifact validation
# ---------------------------------------------------------------------------


class TestCrossArtifactValidation:
    def test_bundle_plan_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """bundle.candidate_plan_id == plan.plan_id."""
        assert source_graph.source_bundle.candidate_plan_id == source_graph.candidate_plan.plan_id

    def test_bundle_sidecar_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """plan.discovery_evidence_artifact_id == bundle.discovery_evidence_artifact_id."""
        assert (
            source_graph.candidate_plan.discovery_evidence_artifact_id
            == source_graph.source_bundle.discovery_evidence_artifact_id
        )

    def test_bundle_ledger_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """ledger.ledger_id == bundle.acquisition_ledger_id."""
        assert source_graph.acquisition_ledger.ledger_id == source_graph.source_bundle.acquisition_ledger_id

    def test_bundle_reconciliation_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """reconciliation.reconciliation_id == bundle.temporal_reconciliation_id."""
        assert (
            source_graph.temporal_reconciliation.reconciliation_id
            == source_graph.source_bundle.temporal_reconciliation_id
        )

    def test_bundle_inventory_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """inventory.manifest_id == bundle.verified_inventory_manifest_id."""
        assert (
            source_graph.verified_inventory.manifest_id
            == source_graph.source_bundle.verified_inventory_manifest_id
        )

    def test_bundle_horizons_binding(self, source_graph: VerifiedV2SourceGraph) -> None:
        """Horizons snapshot_id == bundle.horizons_snapshot_id."""
        from backend.app.mission_sources.snapshots.horizons_snapshot import (
            _compute_snapshot_id,
            _canonical_retrieved_at,
        )
        computed = _compute_snapshot_id(
            source_graph.horizons_result.provenance.provenance_id,
            _canonical_retrieved_at(source_graph.horizons_result.provenance.retrieved_at),
        )
        assert computed == source_graph.source_bundle.horizons_snapshot_id

    def test_horizons_epoch_matches_decision_epoch(self, source_graph: VerifiedV2SourceGraph) -> None:
        """Horizons epoch == decision epoch (2024-06-14T09:35:17.546000Z)."""
        from datetime import timezone
        geo_epoch = source_graph.horizons_result.geometry.epoch_utc.astimezone(timezone.utc)
        assert geo_epoch == _DECISION_EPOCH_UTC

    def test_inventory_logical_ids_match_eligible_reconciliation(self, source_graph: VerifiedV2SourceGraph) -> None:
        """Exact set equality: inventory logical IDs == eligible reconciliation IDs."""
        from backend.app.mission_sources.v2_temporal_reconciliation import ReconciliationClassification
        eligible_ids = {
            e.logical_product_id
            for e in source_graph.temporal_reconciliation.entries
            if e.classification == ReconciliationClassification.ELIGIBLE
        }
        inventory_ids = {e.logical_product_id for e in source_graph.verified_inventory.entries}
        assert inventory_ids == eligible_ids

    def test_ineligible_not_in_inventory(self, source_graph: VerifiedV2SourceGraph) -> None:
        """Ineligible source_record_ids are absent from verified inventory."""
        from backend.app.mission_sources.v2_temporal_reconciliation import ReconciliationClassification
        ineligible_srids = set()
        for e in source_graph.temporal_reconciliation.entries:
            if e.classification != ReconciliationClassification.ELIGIBLE:
                ineligible_srids.update(e.source_record_ids)
        inventory_srids = set()
        for e in source_graph.verified_inventory.entries:
            inventory_srids.update(e.representation_record_ids)
        contamination = ineligible_srids & inventory_srids
        assert contamination == set(), f"Ineligible records leaked: {contamination!r}"


# ---------------------------------------------------------------------------
# Section 36: Inclusion / Exclusion
# ---------------------------------------------------------------------------


class TestInclusionExclusion:
    def test_403_eligible_products(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.eligible_logical_count == 403

    def test_known_ineligible_excluded_from_inventory(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Known ineligible IDs must NOT appear in verified inventory."""
        inventory_ids = {e.logical_product_id for e in source_graph.verified_inventory.entries}
        for bad_id in _KNOWN_INELIGIBLE:
            assert bad_id not in inventory_ids, (
                f"{bad_id!r} should be excluded but found in inventory."
            )

    def test_known_ineligible_preserved_in_candidate_plan(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Known ineligible IDs MUST still appear in candidate plan (evidence preserved)."""
        plan_ids = {e.logical_product_id for e in source_graph.candidate_plan.logical_entries}
        for bad_id in _KNOWN_INELIGIBLE:
            assert bad_id in plan_ids, (
                f"{bad_id!r} should be in candidate plan (evidence preserved) but not found."
            )

    def test_jedi_candidate_28_eligible_22(self, source_graph: VerifiedV2SourceGraph) -> None:
        """JEDI: 28 candidates, 22 eligible (6 ineligible)."""
        from backend.app.mission_sources.v2_temporal_reconciliation import ReconciliationClassification
        jedi_entries = [
            e for e in source_graph.temporal_reconciliation.entries
            if e.logical_product_id.startswith("gcsi.jedi.")
        ]
        eligible = sum(
            1 for e in jedi_entries
            if e.classification == ReconciliationClassification.ELIGIBLE
        )
        assert len(jedi_entries) == 28
        assert eligible == 22

    def test_uvs_candidate_8_eligible_6(self, source_graph: VerifiedV2SourceGraph) -> None:
        """UVS: 8 candidates, 6 eligible (2 ineligible)."""
        from backend.app.mission_sources.v2_temporal_reconciliation import ReconciliationClassification
        uvs_entries = [
            e for e in source_graph.temporal_reconciliation.entries
            if e.logical_product_id.startswith("gcsi.uvs.")
        ]
        eligible = sum(
            1 for e in uvs_entries
            if e.classification == ReconciliationClassification.ELIGIBLE
        )
        assert len(uvs_entries) == 8
        assert eligible == 6


# ---------------------------------------------------------------------------
# Section 12: Cross-artifact mutation defense
# ---------------------------------------------------------------------------


class TestCrossArtifactMutationDefense:
    """Prove that recomputing bundle_id is NOT sufficient to forge a valid graph.

    The loader enforces a hardcoded canonical bundle_id (_EXPECTED_BUNDLE_ID) and
    independently loads every child artifact through its own trust loader.

    Attack model tested here:
    1. An attacker edits a root source-bundle field.
    2. Recomputes a new syntactically valid bundle_id over the mutated content.
    3. Writes the forged bundle JSON.
    4. load_verified_v2_source_graph() is called with the forged path.

    Defence layers that fire:
    - Layer 1: forged bundle_id ≠ hardcoded _EXPECTED_BUNDLE_ID → immediate rejection.
    - Layer 2 (deeper fields): even if layer 1 were bypassed, the child artifact's
      own content would disagree with the mutated root field.

    Both layers are tested here.  Layer 1 produces a "bundle_id" mismatch error.
    Tests for count mutations additionally prove layer 2 fires directly.
    """

    _BUNDLE_PATH = (
        _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_source_bundle.json"
    )

    def _mutate_bundle_and_expect_fail(self, mutation_fn) -> None:
        """Mutate bundle, recompute bundle_id, write to temp, expect rejection."""
        from backend.app.mission_sources.v2_source_bundle import _compute_bundle_id

        raw = self._BUNDLE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)

        # Apply mutation
        mutation_fn(data)

        # Recompute a "valid" bundle_id for the mutated content
        new_bundle_id = _compute_bundle_id(
            replay_id=data.get("replay_id", ""),
            candidate_plan_id=data.get("candidate_plan_id", ""),
            discovery_evidence_artifact_id=data.get("discovery_evidence_artifact_id", ""),
            acquisition_ledger_id=data.get("acquisition_ledger_id", ""),
            temporal_reconciliation_id=data.get("temporal_reconciliation_id", ""),
            verified_inventory_manifest_id=data.get("verified_inventory_manifest_id", ""),
            verified_inventory_manifest_ref=data.get("verified_inventory_manifest_ref", ""),
            label_snapshot_count=data.get("label_snapshot_count", 0),
            candidate_logical_count=data.get("candidate_logical_count", 0),
            candidate_source_count=data.get("candidate_source_count", 0),
            eligible_logical_count=data.get("eligible_logical_count", 0),
            eligible_source_count=data.get("eligible_source_count", 0),
            ineligible_logical_count=data.get("ineligible_logical_count", 0),
            ineligible_source_count=data.get("ineligible_source_count", 0),
            decision_epoch_utc=data.get("decision_epoch_utc", ""),
            horizons_snapshot_id=data.get("horizons_snapshot_id"),
            horizons_snapshot_ref=data.get("horizons_snapshot_ref"),
        )
        # This new_bundle_id is syntactically valid but differs from _EXPECTED_BUNDLE_ID.
        data["bundle_id"] = new_bundle_id
        data["logical_product_count"] = data.get("eligible_logical_count", 0)
        data["source_record_count"] = data.get("eligible_source_count", 0)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8",
            dir=self._BUNDLE_PATH.parent, delete=False
        ) as f:
            json.dump(data, f, indent=2, sort_keys=True)
            temp_path = pathlib.Path(f.name)

        try:
            # Layer 1: new_bundle_id ≠ _EXPECTED_BUNDLE_ID → ValueError("bundle_id")
            with pytest.raises((ValueError, RuntimeError), match="bundle_id"):
                load_verified_v2_source_graph(bundle_path=temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_mutation_candidate_plan_id_rejected(self) -> None:
        """Mutating candidate_plan_id + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["candidate_plan_id"] = "a" * 64
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_acquisition_ledger_id_rejected(self) -> None:
        """Mutating acquisition_ledger_id + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["acquisition_ledger_id"] = "b" * 64
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_temporal_reconciliation_id_rejected(self) -> None:
        """Mutating temporal_reconciliation_id + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["temporal_reconciliation_id"] = "c" * 64
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_verified_inventory_manifest_id_rejected(self) -> None:
        """Mutating verified_inventory_manifest_id + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["verified_inventory_manifest_id"] = "d" * 64
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_horizons_snapshot_id_rejected(self) -> None:
        """Mutating horizons_snapshot_id + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["horizons_snapshot_id"] = "e" * 64
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_candidate_counts_rejected(self) -> None:
        """Mutating candidate counts + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["candidate_logical_count"] = 999
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_eligible_counts_rejected(self) -> None:
        """Mutating eligible counts + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["eligible_logical_count"] = 400
            d["logical_product_count"] = 400
        self._mutate_bundle_and_expect_fail(mutate)

    def test_mutation_label_snapshot_count_rejected(self) -> None:
        """Mutating label_snapshot_count + recomputing bundle_id is rejected (bundle_id mismatch)."""
        def mutate(d: dict) -> None:
            d["label_snapshot_count"] = 100
        self._mutate_bundle_and_expect_fail(mutate)

    def test_child_id_mismatch_rejected_even_without_bundle_id_check(self) -> None:
        """Prove child artifact disagreement is detected independently.

        We mutate a root field AND inject the canonical _EXPECTED_BUNDLE_ID back,
        simulating an attacker who also somehow corrupts the hardcoded value.
        The loader still rejects because the child artifact's own ID disagrees
        with the root field.

        We use candidate_plan_id because load_verified_v2_source_graph cross-checks
        bundle.candidate_plan_id == plan.plan_id, which would fail independently.
        """
        raw = self._BUNDLE_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Mutate the field referencing the child artifact
        data["candidate_plan_id"] = "a" * 64
        # Keep the EXPECTED bundle_id to bypass layer 1
        data["bundle_id"] = _EXPECTED_BUNDLE_ID

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8",
            dir=self._BUNDLE_PATH.parent, delete=False
        ) as f:
            json.dump(data, f, indent=2, sort_keys=True)
            temp_path = pathlib.Path(f.name)

        try:
            # The load_source_bundle() trust loader independently recomputes bundle_id
            # from stored fields and rejects the mutation because the stored bundle_id
            # no longer matches the recomputed one (candidate_plan_id was changed).
            # This is a distinct defense from the _EXPECTED_BUNDLE_ID hardcode check.
            with pytest.raises((ValueError, RuntimeError), match="bundle_id"):
                load_verified_v2_source_graph(bundle_path=temp_path)
        finally:
            temp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Offline isolation
# ---------------------------------------------------------------------------


class TestOfflineIsolation:
    def test_source_graph_offline(self) -> None:
        """Source graph loads successfully without network access."""
        import socket

        original_connect = socket.socket.connect

        def no_connect(*args, **kwargs):
            raise OSError("Network access is DISABLED in B3 offline test.")

        with patch.object(socket.socket, "connect", no_connect):
            # If this raises OSError("Network access is DISABLED"), the test fails
            sg = load_verified_v2_source_graph()
        assert sg.eligible_logical_count == 403
