"""GCSI Phase 6F-B4 — Step 0 Trust Fix Tests.

Verifies the B4 Step-0 residual trust fixes:

1. Source-graph observation_stop_utc mismatch → raises ValueError (fail closed)
2. Source-graph provenance_id not resolved → raises ValueError (fail closed)
3. Descriptor cross-field invariants (size_policy_id, product_policy_id, roles, risk)
4. Assembler uses descriptor product-policy authority (not module-level constants)
5. Size provenance DAG: exact-size MODELED has source parent_provenance_ids
6. Size provenance DAG: fallback MODELED parents evidence-pool DERIVED
7. Descriptor source_ref preserved from caller

All tests are OFFLINE. No network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESCRIPTOR_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_descriptor.json"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source_graph():
    from backend.app.mission_sources.v2_source_graph import load_verified_v2_source_graph
    return load_verified_v2_source_graph()


@pytest.fixture(scope="module")
def descriptor():
    from backend.app.mission_sources.v2_replay_descriptor import load_v2_replay_descriptor
    return load_v2_replay_descriptor(_DESCRIPTOR_PATH)


@pytest.fixture(scope="module")
def assembled_bundle(descriptor, source_graph):
    from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2
    return ReplayAssemblerV2.assemble(descriptor=descriptor, source_graph=source_graph)


# ===========================================================================
# Section 6.1 — observation_stop_utc mismatch → fail closed
# ===========================================================================


class TestSnapshotTimeMismatchFailsClosed:
    """Verify source_graph fails closed on stop-time mismatch."""

    def test_mismatch_raises_not_warns(self, source_graph):
        """Mutation: patch one snapshot product to have a wrong stop time.
        load_verified_v2_source_graph should raise ValueError."""
        from backend.app.mission_sources.v2_source_graph import load_verified_v2_source_graph
        from backend.app.mission_sources.snapshots.archive_label_snapshot import ArchiveLabelSnapshotStore

        original_load = ArchiveLabelSnapshotStore.load
        call_count = [0]

        def patched_load(path):
            product, provenance = original_load(path)
            call_count[0] += 1
            if call_count[0] == 1:
                # Tamper the first product's observation_stop_utc
                wrong_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
                product = product.model_copy(update={"observation_stop_utc": wrong_time})
            return product, provenance

        with patch.object(ArchiveLabelSnapshotStore, "load", side_effect=patched_load):
            with pytest.raises(ValueError, match="observation_stop_utc mismatch"):
                load_verified_v2_source_graph()


# ===========================================================================
# Section 6.2 — Provenance mismatch → fail closed
# ===========================================================================


class TestProvenanceMismatchFailsClosed:
    """Verify source_graph fails closed on provenance_id mismatch."""

    def test_unresolved_provenance_raises(self, source_graph):
        """Mutation: inject a reconciliation entry with a fake provenance_id."""
        from backend.app.mission_sources.v2_source_graph import load_verified_v2_source_graph
        from backend.app.mission_sources import v2_temporal_reconciliation as recon_mod

        real_reconciliation_path = (
            _REPO_ROOT / "data" / "replays"
            / "juno_pj62_large_replay_v2_temporal_reconciliation.json"
        )
        reconciliation_raw = json.loads(real_reconciliation_path.read_text(encoding="utf-8"))

        # Inject a fake provenance_id into the first eligible entry
        for entry in reconciliation_raw.get("entries", []):
            if entry.get("classification") == "ELIGIBLE":
                original_prov_ids = entry.get("provenance_ids", [])
                if original_prov_ids:
                    # Replace first with a fake
                    entry["provenance_ids"] = ["a" * 64] + original_prov_ids[1:]
                break

        def patched_load(path):
            from backend.app.mission_sources.v2_temporal_reconciliation import (
                V2TemporalReconciliationManifest,
            )
            return V2TemporalReconciliationManifest.model_validate(
                reconciliation_raw, strict=False
            )

        with patch.object(recon_mod, "load_reconciliation_manifest", side_effect=patched_load):
            with pytest.raises(ValueError, match="provenance_id.*does not resolve|trust boundary"):
                load_verified_v2_source_graph()


# ===========================================================================
# Section 7 — Descriptor cross-field invariants
# ===========================================================================


class TestDescriptorCrossFieldInvariants:
    """Verify descriptor model_validator enforces cross-field invariants."""

    def test_size_policy_id_mismatch_raises(self):
        """size_policy_id != size_policy.policy_id → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        raw["size_policy_id"] = "WRONG_POLICY_ID"
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError), match="size_policy_id|cross-field"):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_product_policy_id_mismatch_raises(self):
        """product_policy_id != product_policy.policy_id → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        raw["product_policy_id"] = "WRONG_PRODUCT_POLICY"
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError), match="product_policy_id|cross-field"):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_duplicate_semantic_role_raises(self):
        """Duplicate semantic role in product_policy.entries → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        # Duplicate first entry
        first_entry = raw["product_policy"]["entries"][0]
        raw["product_policy"]["entries"].append(copy.deepcopy(first_entry))
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError), match="[Dd]uplicate|semantic_role"):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_missing_semantic_role_raises(self):
        """Missing required semantic role → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        # Remove 'magnetic_field' entry
        raw["product_policy"]["entries"] = [
            e for e in raw["product_policy"]["entries"]
            if e["semantic_role"] != "magnetic_field"
        ]
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError), match="magnetic_field|missing.*role"):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_queue_eligible_zero_raises(self):
        """queue_membership_policy.eligible_logical_count == 0 → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        raw["queue_membership_policy"]["eligible_logical_count"] = 0
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError)):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_risk_level_inconsistency_raises(self):
        """risk_level != replay_risk_level_from_score(risk_score) → validation error."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        # risk_score=0.35 → MEDIUM; force wrong value
        raw["modeled_mission_state"]["risk_level"] = "CRITICAL"
        from pydantic import ValidationError
        with pytest.raises((ValueError, ValidationError), match="risk_level|MEDIUM"):
            from backend.app.mission_sources.v2_replay_descriptor import (
                HistoricalReplayV2Descriptor,
            )
            HistoricalReplayV2Descriptor.model_validate(raw)

    def test_valid_descriptor_loads(self, descriptor):
        """The committed descriptor satisfies all cross-field invariants."""
        assert descriptor.size_policy_id == descriptor.size_policy.policy_id
        assert descriptor.product_policy_id == descriptor.product_policy.policy_id
        assert descriptor.queue_membership_policy.eligible_logical_count == 403
        assert descriptor.modeled_mission_state.risk_level == "MEDIUM"


# ===========================================================================
# Section 8 — Assembler consumes descriptor product-policy
# ===========================================================================


class TestAssemblerDescriptorPolicyAuthority:
    """Verify assembler uses descriptor policy, not module-level constants."""

    def test_descriptor_policy_values_consumed(self, descriptor, source_graph):
        """Assemble with a descriptor that has different policy values.
        The assembled products must reflect the descriptor values."""
        import re

        from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2
        from backend.app.mission_sources.v2_replay_descriptor import (
            HistoricalReplayV2Descriptor,
            compute_descriptor_id,
        )

        # Build a modified descriptor with altered policy values for magnetic_field
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))

        # Find and modify magnetic_field entry
        for entry in raw["product_policy"]["entries"]:
            if entry["semantic_role"] == "magnetic_field":
                entry["criticality"] = 0.11  # Unusual value for easy detection
                entry["mission_relevance"] = 0.22
                break

        # Recompute descriptor_id for the modified descriptor
        raw.pop("descriptor_id")
        # Build a temporary object to compute the id
        tmp_raw = dict(raw)
        # Compute new id manually
        canonical = {
            "decision_epoch_policy": tmp_raw["decision_epoch_policy"],
            "decision_epoch_utc": tmp_raw["decision_epoch_utc"],
            "modeled_link_inputs": tmp_raw["modeled_link_inputs"],
            "modeled_mission_state": tmp_raw["modeled_mission_state"],
            "product_policy": tmp_raw["product_policy"],
            "product_policy_id": tmp_raw["product_policy_id"],
            "queue_membership_policy": tmp_raw["queue_membership_policy"],
            "replay_id": tmp_raw["replay_id"],
            "schema": tmp_raw["schema"],
            "schema_version": tmp_raw["schema_version"],
            "simulated": tmp_raw["simulated"],
            "size_policy": tmp_raw["size_policy"],
            "size_policy_id": tmp_raw["size_policy_id"],
            "source_bundle_id": tmp_raw["source_bundle_id"],
            "source_bundle_ref": tmp_raw["source_bundle_ref"],
        }
        _DESCRIPTOR_V2_ID_PREFIX = "gcsi.historical_replay_v2_descriptor:v1:"
        payload = _DESCRIPTOR_V2_ID_PREFIX + json.dumps(
            canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        )
        new_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        raw["descriptor_id"] = new_id

        modified_descriptor = HistoricalReplayV2Descriptor.model_validate(raw)

        # Assemble with modified descriptor
        bundle = ReplayAssemblerV2.assemble(
            descriptor=modified_descriptor,
            source_graph=source_graph,
        )

        # Find FGM products (magnetic_field role)
        fgm_products = [
            dp for dp in bundle.scenario.data_products
            if dp.product_type == "magnetic_field"
        ]
        assert len(fgm_products) > 0

        # Must reflect descriptor values, not module-level constants
        for dp in fgm_products:
            assert dp.criticality == pytest.approx(0.11), (
                f"Expected descriptor criticality 0.11 but got {dp.criticality!r}. "
                "Assembler must use descriptor product policy, not module-level constants."
            )
            assert dp.mission_relevance == pytest.approx(0.22), (
                f"Expected descriptor mission_relevance 0.22 but got {dp.mission_relevance!r}."
            )


# ===========================================================================
# Section 9 — Size provenance DAG
# ===========================================================================


class TestSizeProvenanceDAG:
    """Verify the size provenance lineage is correct."""

    def test_exact_size_modeled_record_has_source_parents(self, assembled_bundle):
        """Exact-size MODELED provenance records must have source parent_provenance_ids."""
        from backend.app.provenance.models import ProvenanceKind

        manifest = assembled_bundle.provenance
        records_by_id = {r.provenance_id: r for r in manifest.records}

        # Find size_bits bindings for exact-size products
        exact_size_bindings = [
            b for b in manifest.bindings
            if b.field_path == "size_bits"
        ]

        found_exact_with_parents = 0
        for binding in exact_size_bindings:
            rec = records_by_id.get(binding.provenance_id)
            if rec and rec.kind == ProvenanceKind.MODELED:
                if rec.parent_provenance_ids:
                    found_exact_with_parents += 1

        # At least some exact-size products must have source lineage
        assert found_exact_with_parents > 0, (
            "Expected at least one MODELED size_bits record to have parent_provenance_ids. "
            "Size provenance DAG lineage is broken."
        )

    def test_fallback_modeled_has_derived_evidence_pool_parent(self, assembled_bundle):
        """Fallback MODELED provenance must parent a DERIVED evidence-pool record."""
        from backend.app.provenance.models import ProvenanceKind

        manifest = assembled_bundle.provenance
        records_by_id = {r.provenance_id: r for r in manifest.records}

        # Find fallback size records (MODELED with a DERIVED parent)
        derived_ids = {
            r.provenance_id for r in manifest.records
            if r.kind == ProvenanceKind.DERIVED
        }

        # Find MODELED records that parent a DERIVED record (the evidence pool chain)
        fallback_with_derived_parent = [
            r for r in manifest.records
            if r.kind == ProvenanceKind.MODELED
            and any(pid in derived_ids for pid in r.parent_provenance_ids)
        ]

        assert len(fallback_with_derived_parent) >= 1, (
            "Expected at least one MODELED size record to parent a DERIVED evidence-pool record. "
            "Fallback size provenance DAG is broken."
        )

    def test_manifest_is_acyclic(self, assembled_bundle):
        """ProvenanceManifest must be acyclic (no circular parent references)."""
        manifest = assembled_bundle.provenance
        records_by_id = {r.provenance_id: r for r in manifest.records}

        def has_cycle(pid: str, visiting: set, visited: set) -> bool:
            if pid in visiting:
                return True
            if pid in visited:
                return False
            visiting.add(pid)
            rec = records_by_id.get(pid)
            if rec:
                for parent in rec.parent_provenance_ids:
                    if has_cycle(parent, visiting, visited):
                        return True
            visiting.discard(pid)
            visited.add(pid)
            return False

        visited: set = set()
        for rec in manifest.records:
            assert not has_cycle(rec.provenance_id, set(), visited), (
                f"Cycle detected involving provenance_id {rec.provenance_id!r}"
            )


# ===========================================================================
# Section 10 — Descriptor source_ref preserved
# ===========================================================================


class TestDescriptorSourceRefPreserved:
    """Verify source_ref in bundle is the caller-provided path, not source_bundle_ref."""

    def test_assembler_uses_caller_source_ref(self, descriptor, source_graph):
        """When source_ref is passed, it appears in bundle.source_ref."""
        from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2

        caller_ref = "data/replays/juno_pj62_large_replay_v2_descriptor.json"
        bundle = ReplayAssemblerV2.assemble(
            descriptor=descriptor,
            source_graph=source_graph,
            source_ref=caller_ref,
        )
        assert bundle.source_ref == caller_ref

    def test_assembler_fallback_when_no_source_ref(self, descriptor, source_graph):
        """When source_ref is None, bundle.source_ref falls back to source_bundle_ref."""
        from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2

        bundle = ReplayAssemblerV2.assemble(
            descriptor=descriptor,
            source_graph=source_graph,
            source_ref=None,
        )
        # Falls back to descriptor.source_bundle_ref
        assert bundle.source_ref == descriptor.source_bundle_ref
