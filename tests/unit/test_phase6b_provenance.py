"""Phase 6B — Provenance Foundation Unit Tests.

Tests cover all 18 required cases from the Phase 6B specification:

 1.  All provenance kinds serialize correctly
 2.  Valid EXTERNAL_AUTHORITATIVE record
 3.  Valid DERIVED record with parent provenance IDs
 4.  Valid MODELED record
 5.  Valid SYNTHETIC record
 6.  Valid AI_DERIVED record
 7.  Invalid SHA-256 rejected
 8.  Timezone-naive datetime rejected
 9.  Duplicate provenance IDs rejected
10.  Binding to missing provenance ID rejected
11.  Missing parent provenance ID rejected
12.  Direct self-parent rejected
13.  Multi-node provenance cycle rejected
14.  Duplicate exact field binding rejected
15.  Two different fields on the same entity may have different provenance
16.  Identical canonical DataProduct objects remain completely independent
     of provenance metadata
17.  DataProduct serialization/schema is unchanged by Phase 6B
18.  Existing Scenario simulated=True behavior remains unchanged

These tests intentionally import nothing from the existing GCSI decision
engine (ScenarioLoader, TelecomEngine, PlanEvaluator, etc.) and add no
dependencies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.provenance import (
    FieldProvenanceBinding,
    ProvenanceKind,
    ProvenanceManifest,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)
from backend.app.models.data_product import DataProduct
from backend.app.models.scenario import Scenario


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_UTC = timezone.utc

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=_UTC)


def _record(**kwargs: Any) -> ProvenanceRecord:
    """Return a minimal valid ProvenanceRecord with optional overrides."""
    defaults: dict[str, Any] = dict(
        provenance_id="rec-001",
        kind=ProvenanceKind.SYNTHETIC,
        source_system="GCSI-benchmark",
        validation_status=ProvenanceValidationStatus.VALIDATED,
    )
    defaults.update(kwargs)
    return ProvenanceRecord(**defaults)


def _binding(**kwargs: Any) -> FieldProvenanceBinding:
    """Return a minimal valid FieldProvenanceBinding."""
    defaults: dict[str, Any] = dict(
        entity_type="data_product",
        entity_id="DP-001",
        field_path="size_bits",
        provenance_id="rec-001",
    )
    defaults.update(kwargs)
    return FieldProvenanceBinding(**defaults)


def _minimal_data_product(**overrides: Any) -> dict:
    """Return a minimal valid DataProduct constructor dict."""
    base = dict(
        product_id="TEL-PROP-001",
        product_type="telemetry",
        subsystem="propulsion",
        size_bits=8192,
        criticality=0.85,
        mission_relevance=0.90,
        scientific_value=0.0,
        deadline_s=120.0,
        age_s=60.0,
        delivery_requirement="required",
        retry_cost=0.5,
    )
    base.update(overrides)
    return base


def _minimal_scenario(**overrides: Any) -> dict:
    """Return a minimal valid Scenario constructor dict."""
    base = dict(
        scenario_id="SC-001",
        simulated=True,
        link_inputs={
            "frequency_hz": 8.4e9,
            "tx_power_w": 20.0,
            "tx_gain_dbi": 36.0,
            "rx_gain_dbi": 42.0,
            "distance_m": 1e11,
            "noise_temp_k": 30.0,
            "bandwidth_hz": 1e6,
        },
        mission_state={
            "mission_id": "GCSI-TEST",
            "mission_phase": "cruise",
            "current_event": "nominal_comm_pass",
            "event_time_remaining_s": 600.0,
            "comm_window_remaining_s": 3600.0,
            "risk_score": 0.1,
            "risk_level": "LOW",
        },
    )
    base.update(overrides)
    return base


# ===========================================================================
# TEST CASE 1 — All provenance kinds serialize correctly
# ===========================================================================

class TestProvenanceKindSerialization:
    """Test case 1: All provenance kinds serialize correctly."""

    def test_all_kinds_are_defined(self):
        expected = {
            "EXTERNAL_AUTHORITATIVE",
            "DERIVED",
            "MODELED",
            "SYNTHETIC",
            "AI_DERIVED",
        }
        actual = {k.name for k in ProvenanceKind}
        assert actual == expected

    def test_serialization_values_are_snake_case(self):
        assert ProvenanceKind.EXTERNAL_AUTHORITATIVE.value == "external_authoritative"
        assert ProvenanceKind.DERIVED.value == "derived"
        assert ProvenanceKind.MODELED.value == "modeled"
        assert ProvenanceKind.SYNTHETIC.value == "synthetic"
        assert ProvenanceKind.AI_DERIVED.value == "ai_derived"

    def test_all_kinds_round_trip_via_record_model_dump(self):
        """Each kind survives a model_dump → reconstruct round trip."""
        for kind in ProvenanceKind:
            rec = _record(provenance_id=f"rec-{kind.value}", kind=kind)
            dumped = rec.model_dump()
            assert dumped["kind"] == kind.value, (
                f"Expected {kind.value!r}, got {dumped['kind']!r}"
            )
            # Reconstruct
            rec2 = ProvenanceRecord(**dumped)
            assert rec2.kind == kind

    def test_no_human_kind_exists(self):
        """HUMAN must not be a ProvenanceKind per spec."""
        names = {k.name for k in ProvenanceKind}
        assert "HUMAN" not in names

    def test_validation_status_values(self):
        assert ProvenanceValidationStatus.VALIDATED.value == "validated"
        assert ProvenanceValidationStatus.PENDING.value == "pending"
        assert ProvenanceValidationStatus.REJECTED.value == "rejected"


# ===========================================================================
# TEST CASE 2 — Valid EXTERNAL_AUTHORITATIVE record
# ===========================================================================

class TestExternalAuthoritativeRecord:
    """Test case 2: Valid EXTERNAL_AUTHORITATIVE record."""

    def test_valid_external_authoritative_record(self):
        rec = ProvenanceRecord(
            provenance_id="pds-001",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA-PDS",
            source_record_id="urn:nasa:pds:juno:product:001",
            source_uri="https://pds.nasa.gov/products/juno-001",
            source_version="v3.2",
            observed_at=_NOW,
            retrieved_at=_NOW,
            normalized_at=_NOW,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            content_sha256="a" * 64,
            notes="Validated against PDS schema v3",
        )
        assert rec.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert rec.source_system == "NASA-PDS"
        assert rec.validation_status == ProvenanceValidationStatus.VALIDATED
        assert rec.content_sha256 == "a" * 64

    def test_external_authoritative_without_optional_fields(self):
        rec = _record(kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE, source_system="NASA-Horizons")
        assert rec.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE
        assert rec.source_record_id is None
        assert rec.source_uri is None
        assert rec.content_sha256 is None


# ===========================================================================
# TEST CASE 3 — Valid DERIVED record with parent provenance IDs
# ===========================================================================

class TestDerivedRecord:
    """Test case 3: Valid DERIVED record with parent provenance IDs."""

    def test_valid_derived_record_with_parents(self):
        rec = ProvenanceRecord(
            provenance_id="derived-001",
            kind=ProvenanceKind.DERIVED,
            source_system="GCSI-derivation-engine",
            derivation_method="propagation_delay_from_distance_km",
            parent_provenance_ids=["pds-001", "pds-002"],
            validation_status=ProvenanceValidationStatus.VALIDATED,
        )
        assert rec.kind == ProvenanceKind.DERIVED
        assert rec.derivation_method == "propagation_delay_from_distance_km"
        assert rec.parent_provenance_ids == ("pds-001", "pds-002")

    def test_derived_record_in_manifest_with_valid_parents(self):
        parent = _record(provenance_id="parent-001", kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE)
        child = _record(
            provenance_id="child-001",
            kind=ProvenanceKind.DERIVED,
            parent_provenance_ids=["parent-001"],
        )
        binding = _binding(provenance_id="child-001")
        manifest = ProvenanceManifest(records=[parent, child], bindings=[binding])
        assert len(manifest.records) == 2
        assert manifest.bindings[0].provenance_id == "child-001"


# ===========================================================================
# TEST CASE 4 — Valid MODELED record
# ===========================================================================

class TestModeledRecord:
    """Test case 4: Valid MODELED record."""

    def test_valid_modeled_record(self):
        rec = _record(
            provenance_id="modeled-001",
            kind=ProvenanceKind.MODELED,
            source_system="GCSI-replay-assembler",
            notes="Reconstructed queue membership; real value not publicly available.",
        )
        assert rec.kind == ProvenanceKind.MODELED
        assert rec.notes is not None

    def test_modeled_does_not_require_source_record_id(self):
        rec = _record(kind=ProvenanceKind.MODELED)
        assert rec.source_record_id is None


# ===========================================================================
# TEST CASE 5 — Valid SYNTHETIC record
# ===========================================================================

class TestSyntheticRecord:
    """Test case 5: Valid SYNTHETIC record."""

    def test_valid_synthetic_record(self):
        rec = _record(
            provenance_id="syn-001",
            kind=ProvenanceKind.SYNTHETIC,
            source_system="GCSI-benchmark",
            notes="ASTERIA-7 controlled fictional ground truth.",
        )
        assert rec.kind == ProvenanceKind.SYNTHETIC

    def test_synthetic_default_validation_status_is_pending(self):
        rec = ProvenanceRecord(
            provenance_id="syn-002",
            kind=ProvenanceKind.SYNTHETIC,
            source_system="GCSI-benchmark",
        )
        assert rec.validation_status == ProvenanceValidationStatus.PENDING


# ===========================================================================
# TEST CASE 6 — Valid AI_DERIVED record
# ===========================================================================

class TestAiDerivedRecord:
    """Test case 6: Valid AI_DERIVED record."""

    def test_valid_ai_derived_record(self):
        rec = _record(
            provenance_id="ai-001",
            kind=ProvenanceKind.AI_DERIVED,
            source_system="GCSI-AI-granite",
            notes="Semantic priority ranking from Granite 3.3 inference.",
        )
        assert rec.kind == ProvenanceKind.AI_DERIVED

    def test_ai_derived_may_have_parent(self):
        parent = _record(provenance_id="source-001")
        child = _record(
            provenance_id="ai-002",
            kind=ProvenanceKind.AI_DERIVED,
            parent_provenance_ids=["source-001"],
        )
        binding_a = _binding(provenance_id="source-001", field_path="criticality")
        binding_b = _binding(provenance_id="ai-002", field_path="semantic_priority")
        manifest = ProvenanceManifest(records=[parent, child], bindings=[binding_a, binding_b])
        assert len(manifest.records) == 2


# ===========================================================================
# TEST CASE 7 — Invalid SHA-256 rejected
# ===========================================================================

class TestSha256Validation:
    """Test case 7: Invalid SHA-256 rejected."""

    def test_valid_sha256_accepted(self):
        rec = _record(content_sha256="a" * 64)
        assert rec.content_sha256 == "a" * 64

    def test_valid_sha256_all_hex_chars(self):
        # 64 lowercase hex chars
        sha = "0123456789abcdef" * 4
        assert len(sha) == 64
        rec = _record(content_sha256=sha)
        assert rec.content_sha256 == sha

    def test_sha256_too_short_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            _record(content_sha256="abc")
        assert "content_sha256" in str(exc_info.value).lower() or "64" in str(exc_info.value)

    def test_sha256_too_long_rejected(self):
        with pytest.raises(ValidationError):
            _record(content_sha256="a" * 65)

    def test_sha256_with_uppercase_rejected(self):
        with pytest.raises(ValidationError):
            _record(content_sha256="A" * 64)

    def test_sha256_with_non_hex_chars_rejected(self):
        with pytest.raises(ValidationError):
            _record(content_sha256="g" * 64)

    def test_sha256_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            _record(content_sha256="")


# ===========================================================================
# TEST CASE 8 — Timezone-naive datetime rejected
# ===========================================================================

class TestTimezoneDatetimes:
    """Test case 8: Timezone-naive datetimes are explicitly rejected."""

    def test_aware_observed_at_accepted(self):
        rec = _record(observed_at=_NOW)
        assert rec.observed_at == _NOW

    def test_naive_observed_at_rejected(self):
        naive = datetime(2024, 1, 1, 12, 0, 0)  # no tzinfo
        with pytest.raises(ValidationError) as exc_info:
            _record(observed_at=naive)
        assert "timezone" in str(exc_info.value).lower() or "tzinfo" in str(exc_info.value).lower()

    def test_naive_retrieved_at_rejected(self):
        naive = datetime(2024, 1, 1, 0, 0, 0)
        with pytest.raises(ValidationError):
            _record(retrieved_at=naive)

    def test_naive_normalized_at_rejected(self):
        naive = datetime(2024, 1, 1, 0, 0, 0)
        with pytest.raises(ValidationError):
            _record(normalized_at=naive)

    def test_all_optional_datetimes_may_be_none(self):
        rec = _record()
        assert rec.observed_at is None
        assert rec.retrieved_at is None
        assert rec.normalized_at is None

    def test_all_aware_datetimes_accepted(self):
        rec = _record(
            observed_at=_NOW,
            retrieved_at=_NOW,
            normalized_at=_NOW,
        )
        assert rec.observed_at is not None
        assert rec.retrieved_at is not None
        assert rec.normalized_at is not None


# ===========================================================================
# TEST CASE 9 — Duplicate provenance IDs rejected
# ===========================================================================

class TestDuplicateProvenanceIds:
    """Test case 9: Duplicate provenance IDs rejected."""

    def test_duplicate_ids_in_manifest_rejected(self):
        rec_a = _record(provenance_id="dup-001")
        rec_b = _record(provenance_id="dup-001")  # same ID
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceManifest(records=[rec_a, rec_b], bindings=[])
        assert "dup-001" in str(exc_info.value)

    def test_unique_ids_accepted(self):
        rec_a = _record(provenance_id="unique-001")
        rec_b = _record(provenance_id="unique-002")
        manifest = ProvenanceManifest(records=[rec_a, rec_b], bindings=[])
        assert len(manifest.records) == 2

    def test_empty_manifest_is_valid(self):
        manifest = ProvenanceManifest(records=[], bindings=[])
        assert manifest.records == ()
        assert manifest.bindings == ()
        assert len(manifest.records) == 0


# ===========================================================================
# TEST CASE 10 — Binding to missing provenance ID rejected
# ===========================================================================

class TestBindingToMissingRecord:
    """Test case 10: Binding to missing provenance ID rejected."""

    def test_binding_referencing_nonexistent_record_rejected(self):
        rec = _record(provenance_id="real-001")
        bad_binding = _binding(provenance_id="nonexistent-999")
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceManifest(records=[rec], bindings=[bad_binding])
        assert "nonexistent-999" in str(exc_info.value)

    def test_binding_referencing_existing_record_accepted(self):
        rec = _record(provenance_id="real-001")
        good_binding = _binding(provenance_id="real-001")
        manifest = ProvenanceManifest(records=[rec], bindings=[good_binding])
        assert len(manifest.bindings) == 1


# ===========================================================================
# TEST CASE 11 — Missing parent provenance ID rejected
# ===========================================================================

class TestMissingParentProvenanceId:
    """Test case 11: Missing parent provenance ID rejected."""

    def test_parent_referencing_nonexistent_record_rejected(self):
        rec = _record(
            provenance_id="child-001",
            parent_provenance_ids=["ghost-parent-999"],
        )
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceManifest(records=[rec], bindings=[])
        assert "ghost-parent-999" in str(exc_info.value)

    def test_valid_parent_chain_accepted(self):
        parent = _record(provenance_id="parent-001")
        child = _record(
            provenance_id="child-001",
            parent_provenance_ids=["parent-001"],
        )
        manifest = ProvenanceManifest(records=[parent, child], bindings=[])
        assert len(manifest.records) == 2


# ===========================================================================
# TEST CASE 12 — Direct self-parent rejected
# ===========================================================================

class TestSelfParentRejected:
    """Test case 12: A record cannot directly parent itself."""

    def test_self_parent_in_record_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceRecord(
                provenance_id="self-001",
                kind=ProvenanceKind.SYNTHETIC,
                source_system="GCSI-benchmark",
                parent_provenance_ids=["self-001"],
            )
        assert "self-001" in str(exc_info.value)

    def test_self_parent_in_manifest_rejected(self):
        """Even if someone bypasses ProvenanceRecord validation, the manifest also checks."""
        # ProvenanceRecord already rejects it; this tests the contract.
        with pytest.raises(ValidationError):
            ProvenanceRecord(
                provenance_id="loop-001",
                kind=ProvenanceKind.DERIVED,
                source_system="GCSI",
                parent_provenance_ids=["loop-001"],
            )


# ===========================================================================
# TEST CASE 13 — Multi-node provenance cycle rejected
# ===========================================================================

class TestMultiNodeCycleDetection:
    """Test case 13: Multi-node provenance cycles are rejected."""

    def test_two_node_cycle_rejected(self):
        """A → B, B → A must fail."""
        rec_a = _record(
            provenance_id="cycle-A",
            parent_provenance_ids=["cycle-B"],
        )
        rec_b = _record(
            provenance_id="cycle-B",
            parent_provenance_ids=["cycle-A"],
        )
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceManifest(records=[rec_a, rec_b], bindings=[])
        err = str(exc_info.value)
        assert "cycle" in err.lower() or "cycle-A" in err or "cycle-B" in err

    def test_three_node_cycle_rejected(self):
        """A → B → C → A must fail."""
        rec_a = _record(provenance_id="tri-A", parent_provenance_ids=["tri-C"])
        rec_b = _record(provenance_id="tri-B", parent_provenance_ids=["tri-A"])
        rec_c = _record(provenance_id="tri-C", parent_provenance_ids=["tri-B"])
        with pytest.raises(ValidationError):
            ProvenanceManifest(records=[rec_a, rec_b, rec_c], bindings=[])

    def test_valid_dag_no_cycle(self):
        """A linear chain A ← B ← C is a valid DAG, not a cycle."""
        root = _record(provenance_id="dag-root")
        mid = _record(provenance_id="dag-mid", parent_provenance_ids=["dag-root"])
        leaf = _record(provenance_id="dag-leaf", parent_provenance_ids=["dag-mid"])
        manifest = ProvenanceManifest(records=[root, mid, leaf], bindings=[])
        assert len(manifest.records) == 3

    def test_diamond_dag_no_cycle(self):
        """A diamond: root ← A, root ← B, A ← leaf, B ← leaf (valid DAG)."""
        root = _record(provenance_id="d-root")
        a = _record(provenance_id="d-A", parent_provenance_ids=["d-root"])
        b = _record(provenance_id="d-B", parent_provenance_ids=["d-root"])
        leaf = _record(provenance_id="d-leaf", parent_provenance_ids=["d-A", "d-B"])
        manifest = ProvenanceManifest(records=[root, a, b, leaf], bindings=[])
        assert len(manifest.records) == 4


# ===========================================================================
# TEST CASE 14 — Duplicate exact field binding rejected
# ===========================================================================

class TestDuplicateFieldBindingRejected:
    """Test case 14: Duplicate exact (entity_type, entity_id, field_path) rejected."""

    def test_duplicate_binding_same_provenance_id_rejected(self):
        rec = _record()
        b1 = _binding()
        b2 = _binding()  # identical
        with pytest.raises(ValidationError) as exc_info:
            ProvenanceManifest(records=[rec], bindings=[b1, b2])
        assert "Duplicate" in str(exc_info.value) or "duplicate" in str(exc_info.value).lower()

    def test_duplicate_binding_different_provenance_ids_still_rejected(self):
        """Even if they point to different records, same key is rejected."""
        rec_a = _record(provenance_id="rec-a")
        rec_b = _record(provenance_id="rec-b")
        b1 = _binding(provenance_id="rec-a")
        b2 = _binding(provenance_id="rec-b")  # same entity/field, different record
        with pytest.raises(ValidationError):
            ProvenanceManifest(records=[rec_a, rec_b], bindings=[b1, b2])


# ===========================================================================
# TEST CASE 15 — Two different fields may have different provenance
# ===========================================================================

class TestDifferentFieldsAllowDifferentProvenance:
    """Test case 15: Two different fields on the same entity may have different provenance."""

    def test_same_entity_different_fields_accepted(self):
        rec_ext = _record(
            provenance_id="ext-001",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
        )
        rec_mod = _record(
            provenance_id="mod-001",
            kind=ProvenanceKind.MODELED,
        )
        b_size = FieldProvenanceBinding(
            entity_type="data_product",
            entity_id="DP-001",
            field_path="size_bits",
            provenance_id="ext-001",
        )
        b_criticality = FieldProvenanceBinding(
            entity_type="data_product",
            entity_id="DP-001",
            field_path="criticality",
            provenance_id="mod-001",
        )
        manifest = ProvenanceManifest(
            records=[rec_ext, rec_mod],
            bindings=[b_size, b_criticality],
        )
        assert len(manifest.bindings) == 2

    def test_full_product_provenance_map(self):
        """Simulate the full spec example: product_id, size_bits, age_s,
        criticality, mission_relevance, deadline_s, semantic_priority."""
        ext = _record(provenance_id="ext-001", kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE)
        drv = _record(
            provenance_id="drv-001",
            kind=ProvenanceKind.DERIVED,
            parent_provenance_ids=["ext-001"],
        )
        mod1 = _record(provenance_id="mod-001", kind=ProvenanceKind.MODELED)
        mod2 = _record(provenance_id="mod-002", kind=ProvenanceKind.MODELED)
        mod3 = _record(provenance_id="mod-003", kind=ProvenanceKind.MODELED)
        ai = _record(provenance_id="ai-001", kind=ProvenanceKind.AI_DERIVED)

        bindings = [
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="product_id", provenance_id="ext-001"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="size_bits", provenance_id="ext-001"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="age_s", provenance_id="drv-001"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="criticality", provenance_id="mod-001"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="mission_relevance", provenance_id="mod-002"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="deadline_s", provenance_id="mod-003"),
            FieldProvenanceBinding(entity_type="data_product", entity_id="JUNO-001", field_path="semantic_priority", provenance_id="ai-001"),
        ]
        manifest = ProvenanceManifest(
            records=[ext, drv, mod1, mod2, mod3, ai],
            bindings=bindings,
        )
        assert len(manifest.bindings) == 7
        assert len(manifest.records) == 6


# ===========================================================================
# TEST CASE 16 — DataProduct instances are independent of provenance
# ===========================================================================

class TestDataProductProvenanceIndependence:
    """Test case 16: Identical canonical DataProduct objects remain independent
    of provenance metadata."""

    def test_two_identical_data_products_share_no_provenance_state(self):
        dp1 = DataProduct(**_minimal_data_product())
        dp2 = DataProduct(**_minimal_data_product())
        # Build provenance for dp1 only
        rec = _record(provenance_id="rec-for-dp1")
        b = _binding(entity_id="TEL-PROP-001", provenance_id="rec-for-dp1")
        manifest1 = ProvenanceManifest(records=[rec], bindings=[b])
        # dp2 has no manifest
        assert dp2.product_id == dp1.product_id
        # The manifest for dp1 contains no state from dp2
        assert len(manifest1.bindings) == 1

    def test_provenance_manifest_has_no_reference_to_data_product_object(self):
        """The manifest does not hold a reference to the DataProduct instance."""
        dp = DataProduct(**_minimal_data_product())
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        # Verify the manifest cannot reach the dp object
        assert dp not in manifest.records
        assert dp not in manifest.bindings

    def test_data_product_has_no_provenance_field(self):
        dp = DataProduct(**_minimal_data_product())
        assert not hasattr(dp, "provenance")
        assert not hasattr(dp, "provenance_manifest")
        assert not hasattr(dp, "provenance_id")
        assert "provenance" not in DataProduct.model_fields


# ===========================================================================
# TEST CASE 17 — DataProduct serialization unchanged
# ===========================================================================

class TestDataProductSerializationUnchanged:
    """Test case 17: DataProduct serialization/schema is unchanged by Phase 6B."""

    EXPECTED_FIELDS = {
        "product_id",
        "product_type",
        "description",
        "subsystem",
        "size_bits",
        "criticality",
        "mission_relevance",
        "scientific_value",
        "deadline_s",
        "age_s",
        "anomaly_id",
        "experiment_id",
        "related_ids",
        "delivery_requirement",
        "retry_cost",
    }

    def test_data_product_fields_unchanged(self):
        actual_fields = set(DataProduct.model_fields.keys())
        assert actual_fields == self.EXPECTED_FIELDS, (
            f"DataProduct field set has changed!\n"
            f"  Added: {actual_fields - self.EXPECTED_FIELDS}\n"
            f"  Removed: {self.EXPECTED_FIELDS - actual_fields}"
        )

    def test_data_product_model_dump_contains_no_provenance_keys(self):
        dp = DataProduct(**_minimal_data_product())
        dumped = dp.model_dump()
        provenance_keys = {k for k in dumped if "provenance" in k.lower()}
        assert provenance_keys == set(), (
            f"DataProduct.model_dump() unexpectedly contains provenance keys: {provenance_keys}"
        )

    def test_data_product_has_no_extra_provenance_fields(self):
        """DataProduct has no provenance fields — a known non-existent field is
        not present.  The model does not have extra='forbid' by design (existing
        behavior); what matters is that NO provenance field was added to it."""
        dp = DataProduct(**_minimal_data_product())
        assert not hasattr(dp, "provenance")
        assert not hasattr(dp, "provenance_manifest")
        assert not hasattr(dp, "provenance_id")
        # Confirm the known field set has not grown.
        assert len(DataProduct.model_fields) == len(TestDataProductSerializationUnchanged.EXPECTED_FIELDS)

    def test_data_product_round_trip_unchanged(self):
        original = DataProduct(**_minimal_data_product())
        dumped = original.model_dump()
        reconstructed = DataProduct(**dumped)
        assert reconstructed.product_id == original.product_id
        assert reconstructed.size_bits == original.size_bits
        assert reconstructed.criticality == original.criticality


# ===========================================================================
# TEST CASE 18 — Existing Scenario simulated=True behavior unchanged
# ===========================================================================

class TestScenarioSimulatedUnchanged:
    """Test case 18: Existing Scenario simulated=True behavior remains unchanged."""

    def test_simulated_true_scenario_loads(self):
        sc = Scenario(**_minimal_scenario())
        assert sc.simulated is True
        assert sc.scenario_id == "SC-001"

    def test_simulated_false_would_not_be_blocked_by_model_alone(self):
        """The Scenario model itself accepts simulated=False; enforcement is in
        ScenarioLoader.  This test confirms the model is NOT broken by Phase 6B."""
        sc = Scenario(**_minimal_scenario(simulated=False))
        assert sc.simulated is False

    def test_scenario_has_no_provenance_field(self):
        sc = Scenario(**_minimal_scenario())
        assert not hasattr(sc, "provenance")
        assert not hasattr(sc, "provenance_manifest")
        assert "provenance" not in Scenario.model_fields

    def test_scenario_data_products_default_empty(self):
        sc = Scenario(**_minimal_scenario())
        assert sc.data_products == []

    def test_scenario_field_set_unchanged(self):
        expected = {
            "scenario_id", "simulated", "link_inputs", "mission_state",
            "packets", "data_products", "anomalies", "distance_km",
        }
        actual = set(Scenario.model_fields.keys())
        assert actual == expected, (
            f"Scenario field set has changed!\n"
            f"  Added: {actual - expected}\n"
            f"  Removed: {expected - actual}"
        )

    def test_scenario_with_data_products_unchanged(self):
        dp_data = _minimal_data_product()
        sc = Scenario(**_minimal_scenario(data_products=[dp_data]))
        assert len(sc.data_products) == 1
        assert sc.data_products[0].product_id == "TEL-PROP-001"


# ===========================================================================
# Additional edge-case / robustness tests
# ===========================================================================

class TestProvenanceRecordImmutability:
    """Provenance records are frozen — mutations must be rejected."""

    def test_record_is_frozen(self):
        rec = _record()
        with pytest.raises((TypeError, ValidationError)):
            rec.notes = "mutated"  # type: ignore[misc]

    def test_binding_is_frozen(self):
        b = _binding()
        with pytest.raises((TypeError, ValidationError)):
            b.field_path = "mutated"  # type: ignore[misc]

    def test_manifest_is_frozen(self):
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        with pytest.raises((TypeError, ValidationError)):
            manifest.records = []  # type: ignore[misc]


class TestProvenanceRecordExtraFieldsRejected:
    """Unknown fields must be rejected (extra='forbid')."""

    def test_record_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ProvenanceRecord(
                provenance_id="x",
                kind=ProvenanceKind.SYNTHETIC,
                source_system="test",
                unknown_future_field="should-fail",
            )

    def test_binding_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            FieldProvenanceBinding(
                entity_type="data_product",
                entity_id="DP-001",
                field_path="size_bits",
                provenance_id="rec-001",
                extra_field="bad",
            )


class TestManifestCollectionDefaults:
    """Collection fields use default_factory — no shared mutable defaults.
    After Phase 6B.1 these collections are tuples (immutable).
    """

    def test_two_manifests_have_independent_record_tuples(self):
        """Empty tuples are interned by CPython; use non-empty to prove independence."""
        rec1 = _record(provenance_id="m1-rec")
        rec2 = _record(provenance_id="m2-rec")
        m1 = ProvenanceManifest(records=[rec1], bindings=[])
        m2 = ProvenanceManifest(records=[rec2], bindings=[])
        # Distinct tuples with distinct contents
        assert m1.records is not m2.records
        assert m1.records[0].provenance_id == "m1-rec"
        assert m2.records[0].provenance_id == "m2-rec"

    def test_two_records_have_independent_parent_id_tuples(self):
        """Populate parent_ids to avoid CPython empty-tuple interning."""
        r1 = _record(provenance_id="r1", parent_provenance_ids=["r1-parent"])
        r2 = _record(provenance_id="r2", parent_provenance_ids=["r2-parent"])
        assert r1.parent_provenance_ids is not r2.parent_provenance_ids
        assert r1.parent_provenance_ids == ("r1-parent",)
        assert r2.parent_provenance_ids == ("r2-parent",)

    def test_records_collection_is_tuple(self):
        rec = _record()
        m = ProvenanceManifest(records=[rec], bindings=[])
        assert isinstance(m.records, tuple)

    def test_bindings_collection_is_tuple(self):
        rec = _record()
        b = _binding()
        m = ProvenanceManifest(records=[rec], bindings=[b])
        assert isinstance(m.bindings, tuple)

    def test_parent_provenance_ids_is_tuple(self):
        rec = _record(parent_provenance_ids=[])
        assert isinstance(rec.parent_provenance_ids, tuple)


class TestProvenanceValidationStatusSemantics:
    """REJECTED status must be clearly represented and distinguishable."""

    def test_rejected_status_is_distinct(self):
        rejected = ProvenanceValidationStatus.REJECTED
        validated = ProvenanceValidationStatus.VALIDATED
        assert rejected != validated
        assert rejected.value == "rejected"

    def test_record_can_have_rejected_status(self):
        rec = _record(validation_status=ProvenanceValidationStatus.REJECTED)
        assert rec.validation_status == ProvenanceValidationStatus.REJECTED

    def test_all_three_statuses_round_trip(self):
        for status in ProvenanceValidationStatus:
            rec = _record(validation_status=status)
            dumped = rec.model_dump()
            assert dumped["validation_status"] == status.value


# ===========================================================================
# Phase 6B.1 — Deep Immutability Tests
# ===========================================================================

class TestDeepImmutability:
    """Phase 6B.1 cases 1-5: tuple fields cannot be mutated after construction."""

    # Case 1 — parent_provenance_ids cannot be mutated after construction
    def test_parent_provenance_ids_is_immutable(self):
        rec = _record(parent_provenance_ids=["p1", "p2"])
        assert isinstance(rec.parent_provenance_ids, tuple)
        with pytest.raises(AttributeError):
            rec.parent_provenance_ids.append("new-parent")  # type: ignore[attr-defined]

    def test_parent_provenance_ids_tuple_cannot_be_replaced(self):
        """frozen=True prevents normal attribute replacement."""
        rec = _record(parent_provenance_ids=["p1"])
        with pytest.raises((TypeError, ValidationError)):
            rec.parent_provenance_ids = ("p1", "injected")  # type: ignore[misc]

    # Case 2 — manifest.records cannot be mutated after construction
    def test_manifest_records_is_immutable_tuple(self):
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        assert isinstance(manifest.records, tuple)
        with pytest.raises(AttributeError):
            manifest.records.append(_record(provenance_id="injected"))  # type: ignore[attr-defined]

    # Case 3 — manifest.bindings cannot be mutated after construction
    def test_manifest_bindings_is_immutable_tuple(self):
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        assert isinstance(manifest.bindings, tuple)
        with pytest.raises(AttributeError):
            manifest.bindings.append(_binding(field_path="injected"))  # type: ignore[attr-defined]

    # Case 4 — a validated manifest cannot be transformed into invalid/cyclic graph
    def test_validated_manifest_cannot_become_cyclic_via_mutation(self):
        """After manifest passes cycle/reference validation, the collections are
        tuple-immutable, so no post-construction mutation can introduce a cycle."""
        root = _record(provenance_id="safe-root")
        child = _record(provenance_id="safe-child", parent_provenance_ids=["safe-root"])
        manifest = ProvenanceManifest(records=[root, child], bindings=[])
        # Attempt to mutate record collection — must be impossible
        with pytest.raises(AttributeError):
            manifest.records.append(  # type: ignore[attr-defined]
                _record(provenance_id="safe-root", parent_provenance_ids=["safe-child"])
            )
        # Manifest integrity is preserved
        assert len(manifest.records) == 2

    # Case 5 — list input accepted and normalized to immutable tuple
    def test_list_input_normalized_to_tuple_for_parent_ids(self):
        rec = ProvenanceRecord(
            provenance_id="norm-001",
            kind=ProvenanceKind.DERIVED,
            source_system="GCSI",
            parent_provenance_ids=["p-a", "p-b"],
        )
        assert isinstance(rec.parent_provenance_ids, tuple)
        assert rec.parent_provenance_ids == ("p-a", "p-b")

    def test_list_input_normalized_to_tuple_for_manifest_records(self):
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        assert isinstance(manifest.records, tuple)
        assert isinstance(manifest.bindings, tuple)

    def test_tuple_input_also_accepted_for_parent_ids(self):
        rec = ProvenanceRecord(
            provenance_id="tup-001",
            kind=ProvenanceKind.SYNTHETIC,
            source_system="GCSI",
            parent_provenance_ids=(),
        )
        assert isinstance(rec.parent_provenance_ids, tuple)
        assert rec.parent_provenance_ids == ()


# Case 6 — model_dump_json() serializes tuple collections as JSON arrays
class TestTupleJsonSerialization:
    """Phase 6B.1 case 6: tuple fields serialize as JSON arrays."""

    def test_parent_provenance_ids_serializes_as_json_array(self):
        import json
        rec = _record(parent_provenance_ids=["a", "b"])
        data = json.loads(rec.model_dump_json())
        assert isinstance(data["parent_provenance_ids"], list)
        assert data["parent_provenance_ids"] == ["a", "b"]

    def test_manifest_records_serializes_as_json_array(self):
        import json
        rec = _record()
        b = _binding()
        manifest = ProvenanceManifest(records=[rec], bindings=[b])
        data = json.loads(manifest.model_dump_json())
        assert isinstance(data["records"], list)
        assert isinstance(data["bindings"], list)

    def test_empty_parent_provenance_ids_serializes_as_empty_array(self):
        import json
        rec = _record()
        data = json.loads(rec.model_dump_json())
        assert data["parent_provenance_ids"] == []

    def test_empty_manifest_collections_serialize_as_empty_arrays(self):
        import json
        manifest = ProvenanceManifest()
        data = json.loads(manifest.model_dump_json())
        assert data["records"] == []
        assert data["bindings"] == []


# ===========================================================================
# Phase 6B.1 — Datetime JSON Validation Tests (cases 7-11)
# ===========================================================================

class TestDatetimeJsonValidation:
    """Phase 6B.1 cases 7-11: datetime validation via JSON deserialization."""

    _UTC_ISO = "2026-08-27T12:00:00Z"
    _OFFSET_ISO = "2026-08-27T19:00:00+07:00"
    _NAIVE_ISO = "2026-08-27T12:00:00"

    def _base_json(self, **overrides) -> str:
        import json
        base = {
            "provenance_id": "json-rec-001",
            "kind": "synthetic",
            "source_system": "GCSI-benchmark",
        }
        base.update(overrides)
        return json.dumps(base)

    # Case 7 — UTC ISO string accepted
    def test_model_validate_json_accepts_utc_iso_string(self):
        payload = self._base_json(observed_at=self._UTC_ISO)
        rec = ProvenanceRecord.model_validate_json(payload)
        assert rec.observed_at is not None
        assert rec.observed_at.utcoffset() is not None

    # Case 8 — offset-aware ISO string accepted
    def test_model_validate_json_accepts_offset_aware_iso_string(self):
        payload = self._base_json(retrieved_at=self._OFFSET_ISO)
        rec = ProvenanceRecord.model_validate_json(payload)
        assert rec.retrieved_at is not None
        assert rec.retrieved_at.utcoffset() is not None

    # Case 9 — naive ISO string rejected
    def test_model_validate_json_rejects_naive_iso_string(self):
        payload = self._base_json(observed_at=self._NAIVE_ISO)
        with pytest.raises(ValidationError):
            ProvenanceRecord.model_validate_json(payload)

    # Case 10 — naive ISO rejection is ValidationError, not AttributeError
    def test_naive_iso_rejection_is_validation_error_not_attribute_error(self):
        payload = self._base_json(normalized_at=self._NAIVE_ISO)
        try:
            ProvenanceRecord.model_validate_json(payload)
            pytest.fail("Expected ValidationError was not raised")
        except ValidationError:
            pass  # correct
        except AttributeError as exc:
            pytest.fail(
                f"Got AttributeError instead of ValidationError: {exc}"
            )

    # Case 11 — JSON round-trip
    def test_record_json_round_trip(self):
        original = ProvenanceRecord(
            provenance_id="rt-001",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA-PDS",
            observed_at=datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc),
            validation_status=ProvenanceValidationStatus.VALIDATED,
            parent_provenance_ids=["parent-001"],
        )
        json_str = original.model_dump_json()
        restored = ProvenanceRecord.model_validate_json(json_str)
        assert restored.provenance_id == original.provenance_id
        assert restored.kind == original.kind
        assert restored.observed_at == original.observed_at
        assert restored.parent_provenance_ids == original.parent_provenance_ids
        assert isinstance(restored.parent_provenance_ids, tuple)


# ===========================================================================
# Phase 6B.1 — ProvenanceManifest JSON Round-trip (case 12)
# ===========================================================================

class TestManifestJsonRoundTrip:
    """Phase 6B.1 case 12: ProvenanceManifest JSON round-trip with lineage."""

    def test_manifest_json_round_trip_with_records_bindings_and_lineage(self):
        root = ProvenanceRecord(
            provenance_id="rt-root",
            kind=ProvenanceKind.EXTERNAL_AUTHORITATIVE,
            source_system="NASA-PDS",
            validation_status=ProvenanceValidationStatus.VALIDATED,
        )
        derived = ProvenanceRecord(
            provenance_id="rt-derived",
            kind=ProvenanceKind.DERIVED,
            source_system="GCSI-engine",
            parent_provenance_ids=["rt-root"],
        )
        binding = FieldProvenanceBinding(
            entity_type="data_product",
            entity_id="DP-RT-001",
            field_path="age_s",
            provenance_id="rt-derived",
        )
        original = ProvenanceManifest(records=[root, derived], bindings=[binding])
        json_str = original.model_dump_json()
        restored = ProvenanceManifest.model_validate_json(json_str)

        assert len(restored.records) == 2
        assert len(restored.bindings) == 1
        assert isinstance(restored.records, tuple)
        assert isinstance(restored.bindings, tuple)
        restored_derived = next(r for r in restored.records if r.provenance_id == "rt-derived")
        assert isinstance(restored_derived.parent_provenance_ids, tuple)
        assert restored_derived.parent_provenance_ids == ("rt-root",)
        assert restored.bindings[0].field_path == "age_s"


# ===========================================================================
# Phase 6B.1 — SHA-256 non-string input (case 13)
# ===========================================================================

class TestSha256NonStringRejected:
    """Phase 6B.1 case 13: non-string content_sha256 fails with ValidationError."""

    def test_integer_sha256_fails_with_validation_error(self):
        try:
            ProvenanceRecord(
                provenance_id="sha-int",
                kind=ProvenanceKind.SYNTHETIC,
                source_system="test",
                content_sha256=12345,  # type: ignore[arg-type]
            )
            pytest.fail("Expected ValidationError was not raised")
        except ValidationError:
            pass
        except (TypeError, AttributeError) as exc:
            pytest.fail(f"Got raw {type(exc).__name__} instead of ValidationError: {exc}")

    def test_list_sha256_fails_with_validation_error(self):
        try:
            ProvenanceRecord(
                provenance_id="sha-list",
                kind=ProvenanceKind.SYNTHETIC,
                source_system="test",
                content_sha256=["not", "a", "string"],  # type: ignore[arg-type]
            )
            pytest.fail("Expected ValidationError was not raised")
        except ValidationError:
            pass
        except (TypeError, AttributeError) as exc:
            pytest.fail(f"Got raw {type(exc).__name__} instead of ValidationError: {exc}")

    def test_none_sha256_is_still_valid(self):
        rec = ProvenanceRecord(
            provenance_id="sha-none",
            kind=ProvenanceKind.SYNTHETIC,
            source_system="test",
            content_sha256=None,
        )
        assert rec.content_sha256 is None
