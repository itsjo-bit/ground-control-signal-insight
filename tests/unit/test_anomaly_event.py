"""Unit tests for AnomalyEvent — Phase 2A domain model.

Covers:
- Valid construction with all fields
- Required field enforcement
- severity bounds [0, 1]
- detected_at_s >= 0
- related_product_ids defaults to empty list (no mutable-default bug)
- Free-form status string
- Rejection of invalid values
- Round-trip serialization
"""

import pytest
from pydantic import ValidationError

from backend.app.models.anomaly_event import AnomalyEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_anomaly_event(**overrides) -> dict:
    """Return a minimal valid AnomalyEvent dict."""
    base = dict(
        anomaly_id="ANOM-017",
        subsystem="propulsion",
        severity=0.85,
        detected_at_s=480.0,
        description="Unexpected thrust oscillation detected in primary propulsion assembly.",
        status="active",
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Valid construction
# ---------------------------------------------------------------------------

class TestAnomalyEventValidConstruction:
    def test_minimal_required_fields(self):
        ae = AnomalyEvent(**make_anomaly_event())
        assert ae.anomaly_id == "ANOM-017"
        assert ae.subsystem == "propulsion"
        assert ae.severity == 0.85
        assert ae.detected_at_s == 480.0
        assert ae.description == "Unexpected thrust oscillation detected in primary propulsion assembly."
        assert ae.status == "active"

    def test_related_product_ids_defaults_to_empty_list(self):
        ae = AnomalyEvent(**make_anomaly_event())
        assert ae.related_product_ids == []

    def test_related_product_ids_can_be_set(self):
        ids = ["TEL-PROP-001", "DIAG-PROP-002", "FAU-PROP-001"]
        ae = AnomalyEvent(**make_anomaly_event(related_product_ids=ids))
        assert ae.related_product_ids == ids

    def test_boundary_severity_zero(self):
        ae = AnomalyEvent(**make_anomaly_event(severity=0.0))
        assert ae.severity == 0.0

    def test_boundary_severity_one(self):
        ae = AnomalyEvent(**make_anomaly_event(severity=1.0))
        assert ae.severity == 1.0

    def test_zero_detected_at_s_is_valid(self):
        ae = AnomalyEvent(**make_anomaly_event(detected_at_s=0.0))
        assert ae.detected_at_s == 0.0

    def test_various_statuses_accepted(self):
        """status is a free-form string — no enum restriction."""
        for status in ["active", "resolved", "monitoring", "investigating", "deferred"]:
            ae = AnomalyEvent(**make_anomaly_event(status=status))
            assert ae.status == status

    def test_various_subsystems_accepted(self):
        for sub in ["propulsion", "power", "thermal", "communications", "navigation"]:
            ae = AnomalyEvent(**make_anomaly_event(subsystem=sub))
            assert ae.subsystem == sub

    def test_long_description_accepted(self):
        long_desc = "A" * 1000
        ae = AnomalyEvent(**make_anomaly_event(description=long_desc))
        assert len(ae.description) == 1000


# ---------------------------------------------------------------------------
# Required field enforcement
# ---------------------------------------------------------------------------

class TestAnomalyEventRequiredFields:
    def test_missing_anomaly_id_raises(self):
        data = make_anomaly_event()
        del data["anomaly_id"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)

    def test_missing_subsystem_raises(self):
        data = make_anomaly_event()
        del data["subsystem"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)

    def test_missing_severity_raises(self):
        data = make_anomaly_event()
        del data["severity"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)

    def test_missing_detected_at_s_raises(self):
        data = make_anomaly_event()
        del data["detected_at_s"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)

    def test_missing_description_raises(self):
        data = make_anomaly_event()
        del data["description"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)

    def test_missing_status_raises(self):
        data = make_anomaly_event()
        del data["status"]
        with pytest.raises(ValidationError):
            AnomalyEvent(**data)


# ---------------------------------------------------------------------------
# severity bounds
# ---------------------------------------------------------------------------

class TestAnomalyEventSeverity:
    def test_above_one_raises(self):
        with pytest.raises(ValidationError):
            AnomalyEvent(**make_anomaly_event(severity=1.001))

    def test_below_zero_raises(self):
        with pytest.raises(ValidationError):
            AnomalyEvent(**make_anomaly_event(severity=-0.001))

    def test_exact_zero_and_one_accepted(self):
        AnomalyEvent(**make_anomaly_event(severity=0.0))
        AnomalyEvent(**make_anomaly_event(severity=1.0))


# ---------------------------------------------------------------------------
# detected_at_s constraint
# ---------------------------------------------------------------------------

class TestAnomalyEventDetectedAtS:
    def test_negative_detected_at_raises(self):
        with pytest.raises(ValidationError):
            AnomalyEvent(**make_anomaly_event(detected_at_s=-1.0))

    def test_zero_is_valid(self):
        ae = AnomalyEvent(**make_anomaly_event(detected_at_s=0.0))
        assert ae.detected_at_s == 0.0


# ---------------------------------------------------------------------------
# related_product_ids — default factory (no mutable default bug)
# ---------------------------------------------------------------------------

class TestAnomalyEventRelatedProductIds:
    def test_default_is_empty_list(self):
        ae1 = AnomalyEvent(**make_anomaly_event())
        ae2 = AnomalyEvent(**make_anomaly_event())
        assert ae1.related_product_ids == []
        assert ae2.related_product_ids == []

    def test_instances_are_independent(self):
        """Two separate AnomalyEvent instances must not share the same list object."""
        ae1 = AnomalyEvent(**make_anomaly_event())
        ae2 = AnomalyEvent(**make_anomaly_event())
        ae1.related_product_ids.append("FAKE-001")
        assert ae2.related_product_ids == [], (
            "related_product_ids instances must be independent — mutable default bug"
        )

    def test_multiple_ids_accepted(self):
        ids = ["DIAG-PROP-001", "TEL-PROP-001", "HEALTH-PROP-001"]
        ae = AnomalyEvent(**make_anomaly_event(related_product_ids=ids))
        assert ae.related_product_ids == ids


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

class TestAnomalyEventRoundTrip:
    def test_model_dump_and_reconstruct(self):
        original = AnomalyEvent(**make_anomaly_event(
            related_product_ids=["TEL-PROP-001", "DIAG-PROP-001"],
        ))
        dumped = original.model_dump()
        reconstructed = AnomalyEvent(**dumped)
        assert reconstructed.anomaly_id == original.anomaly_id
        assert reconstructed.severity == original.severity
        assert reconstructed.related_product_ids == ["TEL-PROP-001", "DIAG-PROP-001"]

    def test_model_dump_mode_json(self):
        ae = AnomalyEvent(**make_anomaly_event())
        dumped = ae.model_dump(mode="json")
        assert isinstance(dumped, dict)
        assert dumped["anomaly_id"] == "ANOM-017"
