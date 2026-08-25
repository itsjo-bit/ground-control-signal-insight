"""Unit tests for DataProduct — Phase 2A domain model.

Covers:
- Valid construction with all fields
- Required field enforcement
- size_bits must be > 0
- criticality bounds [0, 1]
- mission_relevance bounds [0, 1]
- scientific_value bounds [0, 1]
- deadline_s >= 0
- age_s >= 0
- retry_cost >= 0
- related_ids defaults to empty list (no mutable-default bug)
- optional anomaly_id and experiment_id (None by default)
- No priority field exists
- Field descriptions are correct types
"""

import pytest
from pydantic import ValidationError

from backend.app.models.data_product import DataProduct


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_data_product(**overrides) -> dict:
    """Return a minimal valid DataProduct dict."""
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


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------

class TestDataProductValidConstruction:
    def test_minimal_required_fields(self):
        dp = DataProduct(**make_data_product())
        assert dp.product_id == "TEL-PROP-001"
        assert dp.product_type == "telemetry"
        assert dp.subsystem == "propulsion"
        assert dp.size_bits == 8192
        assert dp.criticality == 0.85
        assert dp.mission_relevance == 0.90
        assert dp.scientific_value == 0.0
        assert dp.deadline_s == 120.0
        assert dp.age_s == 60.0
        assert dp.delivery_requirement == "required"
        assert dp.retry_cost == 0.5

    def test_optional_fields_default_to_none_and_empty_list(self):
        dp = DataProduct(**make_data_product())
        assert dp.anomaly_id is None
        assert dp.experiment_id is None
        assert dp.related_ids == []

    def test_optional_anomaly_id_can_be_set(self):
        dp = DataProduct(**make_data_product(anomaly_id="ANOM-017"))
        assert dp.anomaly_id == "ANOM-017"

    def test_optional_experiment_id_can_be_set(self):
        dp = DataProduct(**make_data_product(experiment_id="EXP-MARS-004"))
        assert dp.experiment_id == "EXP-MARS-004"

    def test_related_ids_can_be_populated(self):
        dp = DataProduct(**make_data_product(related_ids=["TEL-PROP-002", "DIAG-PROP-001"]))
        assert dp.related_ids == ["TEL-PROP-002", "DIAG-PROP-001"]

    def test_boundary_values_criticality(self):
        low = DataProduct(**make_data_product(criticality=0.0))
        high = DataProduct(**make_data_product(criticality=1.0))
        assert low.criticality == 0.0
        assert high.criticality == 1.0

    def test_boundary_values_mission_relevance(self):
        low = DataProduct(**make_data_product(mission_relevance=0.0))
        high = DataProduct(**make_data_product(mission_relevance=1.0))
        assert low.mission_relevance == 0.0
        assert high.mission_relevance == 1.0

    def test_boundary_values_scientific_value(self):
        low = DataProduct(**make_data_product(scientific_value=0.0))
        high = DataProduct(**make_data_product(scientific_value=1.0))
        assert low.scientific_value == 0.0
        assert high.scientific_value == 1.0

    def test_zero_deadline_is_valid(self):
        dp = DataProduct(**make_data_product(deadline_s=0.0))
        assert dp.deadline_s == 0.0

    def test_zero_age_is_valid(self):
        dp = DataProduct(**make_data_product(age_s=0.0))
        assert dp.age_s == 0.0

    def test_zero_retry_cost_is_valid(self):
        dp = DataProduct(**make_data_product(retry_cost=0.0))
        assert dp.retry_cost == 0.0

    def test_various_product_types_accepted(self):
        for ptype in [
            "telemetry", "housekeeping", "science", "image", "diagnostic",
            "experiment", "command_ack", "navigation", "health",
        ]:
            dp = DataProduct(**make_data_product(product_type=ptype))
            assert dp.product_type == ptype

    def test_various_subsystems_accepted(self):
        for subsystem in [
            "propulsion", "power", "thermal", "communications",
            "navigation", "attitude_control", "payload", "flight_computer",
        ]:
            dp = DataProduct(**make_data_product(subsystem=subsystem))
            assert dp.subsystem == subsystem

    def test_various_delivery_requirements_accepted(self):
        for req in ["required", "best_effort", "redundant", "latest_only"]:
            dp = DataProduct(**make_data_product(delivery_requirement=req))
            assert dp.delivery_requirement == req


# ---------------------------------------------------------------------------
# No priority field
# ---------------------------------------------------------------------------

class TestDataProductNoPriorityField:
    def test_no_priority_field(self):
        dp = DataProduct(**make_data_product())
        assert not hasattr(dp, "priority"), "DataProduct must not expose a 'priority' field"
        assert "priority" not in DataProduct.model_fields

    def test_no_priority_score_field(self):
        dp = DataProduct(**make_data_product())
        assert not hasattr(dp, "priority_score")
        assert "priority_score" not in DataProduct.model_fields

    def test_no_ai_priority_field(self):
        dp = DataProduct(**make_data_product())
        assert not hasattr(dp, "ai_priority")
        assert "ai_priority" not in DataProduct.model_fields

    def test_no_rank_field(self):
        dp = DataProduct(**make_data_product())
        assert not hasattr(dp, "rank")
        assert "rank" not in DataProduct.model_fields


# ---------------------------------------------------------------------------
# Required field enforcement
# ---------------------------------------------------------------------------

class TestDataProductRequiredFields:
    def test_missing_product_id_raises(self):
        data = make_data_product()
        del data["product_id"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_product_type_raises(self):
        data = make_data_product()
        del data["product_type"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_subsystem_raises(self):
        data = make_data_product()
        del data["subsystem"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_size_bits_raises(self):
        data = make_data_product()
        del data["size_bits"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_criticality_raises(self):
        data = make_data_product()
        del data["criticality"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_mission_relevance_raises(self):
        data = make_data_product()
        del data["mission_relevance"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_scientific_value_raises(self):
        data = make_data_product()
        del data["scientific_value"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_deadline_s_raises(self):
        data = make_data_product()
        del data["deadline_s"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_age_s_raises(self):
        data = make_data_product()
        del data["age_s"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_delivery_requirement_raises(self):
        data = make_data_product()
        del data["delivery_requirement"]
        with pytest.raises(ValidationError):
            DataProduct(**data)

    def test_missing_retry_cost_raises(self):
        data = make_data_product()
        del data["retry_cost"]
        with pytest.raises(ValidationError):
            DataProduct(**data)


# ---------------------------------------------------------------------------
# size_bits constraint
# ---------------------------------------------------------------------------

class TestDataProductSizeBits:
    def test_size_bits_must_be_positive(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(size_bits=0))

    def test_negative_size_bits_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(size_bits=-1024))

    def test_size_bits_one_is_valid(self):
        dp = DataProduct(**make_data_product(size_bits=1))
        assert dp.size_bits == 1

    def test_large_size_bits_accepted(self):
        dp = DataProduct(**make_data_product(size_bits=2_097_152))  # 2 Mb
        assert dp.size_bits == 2_097_152


# ---------------------------------------------------------------------------
# criticality bounds
# ---------------------------------------------------------------------------

class TestDataProductCriticality:
    def test_above_one_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(criticality=1.001))

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(criticality=-0.001))

    def test_exact_zero_and_one_accepted(self):
        DataProduct(**make_data_product(criticality=0.0))
        DataProduct(**make_data_product(criticality=1.0))


# ---------------------------------------------------------------------------
# mission_relevance bounds
# ---------------------------------------------------------------------------

class TestDataProductMissionRelevance:
    def test_above_one_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(mission_relevance=1.1))

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(mission_relevance=-0.1))


# ---------------------------------------------------------------------------
# scientific_value bounds
# ---------------------------------------------------------------------------

class TestDataProductScientificValue:
    def test_above_one_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(scientific_value=1.1))

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(scientific_value=-0.1))


# ---------------------------------------------------------------------------
# deadline_s constraint
# ---------------------------------------------------------------------------

class TestDataProductDeadline:
    def test_negative_deadline_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(deadline_s=-1.0))

    def test_zero_deadline_is_valid(self):
        dp = DataProduct(**make_data_product(deadline_s=0.0))
        assert dp.deadline_s == 0.0


# ---------------------------------------------------------------------------
# age_s constraint
# ---------------------------------------------------------------------------

class TestDataProductAge:
    def test_negative_age_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(age_s=-0.1))

    def test_zero_age_is_valid(self):
        dp = DataProduct(**make_data_product(age_s=0.0))
        assert dp.age_s == 0.0


# ---------------------------------------------------------------------------
# retry_cost constraint
# ---------------------------------------------------------------------------

class TestDataProductRetryCost:
    def test_negative_retry_cost_raises(self):
        with pytest.raises(ValidationError):
            DataProduct(**make_data_product(retry_cost=-0.01))

    def test_zero_retry_cost_is_valid(self):
        dp = DataProduct(**make_data_product(retry_cost=0.0))
        assert dp.retry_cost == 0.0


# ---------------------------------------------------------------------------
# related_ids — default factory (no mutable default bug)
# ---------------------------------------------------------------------------

class TestDataProductRelatedIds:
    def test_related_ids_default_is_empty_list(self):
        dp1 = DataProduct(**make_data_product())
        dp2 = DataProduct(**make_data_product())
        assert dp1.related_ids == []
        assert dp2.related_ids == []

    def test_related_ids_instances_are_independent(self):
        """Two separate DataProduct instances must not share the same list object."""
        dp1 = DataProduct(**make_data_product())
        dp2 = DataProduct(**make_data_product())
        dp1.related_ids.append("FAKE-001")
        assert dp2.related_ids == [], (
            "related_ids instances must be independent — mutable default bug"
        )

    def test_related_ids_can_hold_multiple_ids(self):
        ids = ["TEL-PROP-001", "TEL-PROP-002", "DIAG-PROP-001"]
        dp = DataProduct(**make_data_product(related_ids=ids))
        assert dp.related_ids == ids


# ---------------------------------------------------------------------------
# Full field round-trip
# ---------------------------------------------------------------------------

class TestDataProductRoundTrip:
    def test_model_dump_and_reconstruct(self):
        original = DataProduct(**make_data_product(
            anomaly_id="ANOM-017",
            experiment_id="EXP-MARS-004",
            related_ids=["TEL-PROP-002"],
        ))
        dumped = original.model_dump()
        reconstructed = DataProduct(**dumped)
        assert reconstructed.product_id == original.product_id
        assert reconstructed.anomaly_id == "ANOM-017"
        assert reconstructed.experiment_id == "EXP-MARS-004"
        assert reconstructed.related_ids == ["TEL-PROP-002"]
