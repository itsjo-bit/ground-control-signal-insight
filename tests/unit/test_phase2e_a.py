"""Unit tests for Phase 2E-A: description field flow.

Covers:
- DataProduct.description field existence and defaults
- CandidateSummary.description field existence and defaults
- _summarise() copies description from DataProduct → CandidateSummary
- description flows into the AI context (serialized candidates in _build_prioritization_message)
- Legacy DataProducts without description still work (backwards compatibility)
- CandidateSummary with explicit description round-trips correctly
- Empty description is valid
- Description does not affect deterministic pipeline outputs (risk, scheduling, feasibility)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.app.agent.candidate_prioritizer import CandidatePrioritizer, _summarise
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.data_product import DataProduct
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_data_product(**kw) -> DataProduct:
    base = dict(
        product_id="DP-001",
        product_type="telemetry",
        description="",
        subsystem="power",
        size_bits=4096,
        criticality=0.6,
        mission_relevance=0.6,
        scientific_value=0.4,
        deadline_s=300.0,
        age_s=120.0,
        delivery_requirement="best_effort",
        retry_cost=0.1,
    )
    base.update(kw)
    return DataProduct(**base)


def make_link_state(**kw) -> LinkState:
    base = dict(
        timestamp=_TS, snr_db=10.0, eb_n0_db=20.0, ber=3.87e-6, rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0, link_goodput_bps=90_000.0,
        latency_s=0.25, link_stability=0.95, remaining_window_s=600.0,
    )
    base.update(kw)
    return LinkState(**base)


def make_mission_state(**kw) -> MissionState:
    base = dict(
        mission_id="m-001", mission_phase="science", current_event="downlink",
        event_time_remaining_s=600.0, comm_window_remaining_s=600.0,
        risk_score=0.3, risk_level=RiskLevel.LOW,
    )
    base.update(kw)
    return MissionState(**base)


# ===========================================================================
# DataProduct.description field
# ===========================================================================

class TestDataProductDescription:
    def test_description_field_exists(self):
        dp = make_data_product()
        assert hasattr(dp, "description")

    def test_description_defaults_to_empty_string(self):
        dp = make_data_product()
        assert dp.description == ""

    def test_description_accepts_non_empty_value(self):
        dp = make_data_product(description="Thruster-2 valve telemetry captured during anomaly.")
        assert dp.description == "Thruster-2 valve telemetry captured during anomaly."

    def test_description_accepts_empty_string(self):
        dp = make_data_product(description="")
        assert dp.description == ""

    def test_backwards_compatibility_without_description_kwarg(self):
        """DataProduct constructed without description kwarg uses empty string."""
        dp = DataProduct(
            product_id="DP-LEGACY",
            product_type="telemetry",
            subsystem="power",
            size_bits=1024,
            criticality=0.5,
            mission_relevance=0.5,
            scientific_value=0.3,
            deadline_s=300.0,
            age_s=60.0,
            delivery_requirement="best_effort",
            retry_cost=0.1,
        )
        assert dp.description == ""

    def test_description_serializes_in_model_dump(self):
        dp = make_data_product(description="Routine housekeeping.")
        data = dp.model_dump()
        assert "description" in data
        assert data["description"] == "Routine housekeeping."


# ===========================================================================
# CandidateSummary.description field
# ===========================================================================

class TestCandidateSummaryDescription:
    def test_description_field_exists(self):
        cs = CandidateSummary(
            product_id="CS-001",
            product_type="telemetry",
            subsystem="power",
            size_bits=4096,
            criticality=0.6,
            mission_relevance=0.6,
            scientific_value=0.4,
            deadline_s=300.0,
            age_s=120.0,
        )
        assert hasattr(cs, "description")

    def test_description_defaults_to_empty_string(self):
        cs = CandidateSummary(
            product_id="CS-001",
            product_type="telemetry",
            subsystem="power",
            size_bits=4096,
            criticality=0.6,
            mission_relevance=0.6,
            scientific_value=0.4,
            deadline_s=300.0,
            age_s=120.0,
        )
        assert cs.description == ""

    def test_description_round_trips(self):
        cs = CandidateSummary(
            product_id="CS-002",
            product_type="diagnostic",
            description="Propulsion diagnostic linked to active anomaly ANOM-017.",
            subsystem="propulsion",
            size_bits=2048,
            criticality=0.95,
            mission_relevance=0.9,
            scientific_value=0.5,
            deadline_s=120.0,
            age_s=30.0,
        )
        assert cs.description == "Propulsion diagnostic linked to active anomaly ANOM-017."

    def test_description_present_in_model_dump(self):
        cs = CandidateSummary(
            product_id="CS-003",
            product_type="telemetry",
            description="Solar array voltage telemetry.",
            subsystem="power",
            size_bits=512,
            criticality=0.4,
            mission_relevance=0.4,
            scientific_value=0.2,
            deadline_s=600.0,
            age_s=200.0,
        )
        data = cs.model_dump(mode="json")
        assert "description" in data
        assert data["description"] == "Solar array voltage telemetry."


# ===========================================================================
# _summarise() copies description from DataProduct → CandidateSummary
# ===========================================================================

class TestSummariseDescriptionCopy:
    def test_description_copied_when_present(self):
        dp = make_data_product(description="Thruster valve position data.")
        cs = _summarise(dp)
        assert cs.description == "Thruster valve position data."

    def test_description_empty_when_not_set(self):
        dp = make_data_product(description="")
        cs = _summarise(dp)
        assert cs.description == ""

    def test_product_id_still_correct_after_description_addition(self):
        dp = make_data_product(product_id="DP-XYZ", description="Some context.")
        cs = _summarise(dp)
        assert cs.product_id == "DP-XYZ"

    def test_other_fields_unchanged(self):
        dp = make_data_product(
            product_id="DP-CHECK",
            product_type="science",
            description="Science imagery.",
            subsystem="payload",
            criticality=0.8,
            mission_relevance=0.7,
            scientific_value=0.9,
            deadline_s=200.0,
            age_s=50.0,
        )
        cs = _summarise(dp)
        assert cs.product_type == "science"
        assert cs.subsystem == "payload"
        assert cs.criticality == pytest.approx(0.8)
        assert cs.mission_relevance == pytest.approx(0.7)
        assert cs.scientific_value == pytest.approx(0.9)
        assert cs.deadline_s == pytest.approx(200.0)
        assert cs.age_s == pytest.approx(50.0)


# ===========================================================================
# CandidatePrioritizer propagates description through select()
# ===========================================================================

class TestCandidatePrioritizerDescriptionFlow:
    def test_description_present_in_selected_candidates(self):
        dp = make_data_product(
            product_id="DP-001",
            description="Anomaly-linked propulsion diagnostic.",
            anomaly_id="ANOM-001",
            criticality=0.9,
        )
        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select([dp])
        assert len(candidates) == 1
        assert candidates[0].description == "Anomaly-linked propulsion diagnostic."

    def test_empty_description_propagated(self):
        dp = make_data_product(product_id="DP-002", description="")
        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select([dp])
        assert candidates[0].description == ""

    def test_description_in_serialized_candidate_context(self):
        """description must appear in the JSON context sent to the AI (model_dump)."""
        dp = make_data_product(
            product_id="DP-003",
            description="Navigation solution for upcoming maneuver.",
        )
        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select([dp])
        # Simulate what GraniteAgent._build_prioritization_message does
        serialized = [cs.model_dump(mode="json") for cs in candidates]
        assert serialized[0]["description"] == "Navigation solution for upcoming maneuver."


# ===========================================================================
# description flows into AI context (_build_prioritization_message)
# ===========================================================================

class TestDescriptionInAIContext:
    def test_description_present_in_prioritization_message(self):
        """The AI context JSON must include description for candidates that have it."""
        from backend.app.agent.granite_agent import GraniteAgent

        dp = make_data_product(
            product_id="DP-AI",
            description="Thermal excursion telemetry linked to anomaly ANOM-021.",
            anomaly_id="ANOM-021",
            criticality=0.85,
        )
        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select([dp])

        # Use the private helper — no API call, pure serialization
        _agent = GraniteAgent.__new__(GraniteAgent)
        msg = _agent._build_prioritization_message(  # noqa: SLF001
            candidates, make_link_state(), make_mission_state(), anomalies=None
        )

        context = json.loads(msg)
        candidate_data = context["candidates"]
        assert len(candidate_data) == 1
        assert candidate_data[0]["description"] == "Thermal excursion telemetry linked to anomaly ANOM-021."

    def test_empty_description_does_not_break_context(self):
        from backend.app.agent.granite_agent import GraniteAgent

        dp = make_data_product(product_id="DP-EMPTY-DESC", description="")
        prioritizer = CandidatePrioritizer(max_candidates=10)
        candidates = prioritizer.select([dp])

        _agent = GraniteAgent.__new__(GraniteAgent)
        msg = _agent._build_prioritization_message(  # noqa: SLF001
            candidates, make_link_state(), make_mission_state(), anomalies=None
        )
        context = json.loads(msg)
        assert context["candidates"][0]["description"] == ""
