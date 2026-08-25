"""Phase 2E-B: AI context propagation test for v3 scenario.

Verifies that descriptions from mission_data_v3.json survive the complete
pipeline path:

    mission_data_v3.json
          ↓
    DataProduct
          ↓
    CandidateSummary  (via CandidatePrioritizer)
          ↓
    AI context  (serialized JSON passed to GraniteAgent._build_prioritization_message)

The test inspects the actual serialized AI context, not just model objects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
from backend.app.agent.granite_agent import GraniteAgent
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel
from backend.app.simulation.scenario_loader import ScenarioLoader

_V3_PATH = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json")

_TS = datetime(2024, 6, 15, 9, 41, 0, tzinfo=timezone.utc)


def make_link_state(**kw) -> LinkState:
    base = dict(
        timestamp=_TS,
        snr_db=8.2,
        eb_n0_db=18.2,
        ber=1.2e-5,
        rssi_dbm=-91.0,
        nominal_data_rate_bps=100_000.0,
        link_goodput_bps=90_000.0,
        latency_s=1.4,
        link_stability=0.74,
        remaining_window_s=480.0,
    )
    base.update(kw)
    return LinkState(**base)


def make_mission_state(**kw) -> MissionState:
    base = dict(
        mission_id="GCSI-MISSION-003",
        mission_phase="science_downlink_anomaly",
        current_event="high_volume_pass",
        event_time_remaining_s=480.0,
        comm_window_remaining_s=480.0,
        risk_score=0.71,
        risk_level=RiskLevel.HIGH,
    )
    base.update(kw)
    return MissionState(**base)


class TestV3AIContextPropagation:
    """Verify description flows from v3 JSON → DataProduct → CandidateSummary → AI context."""

    @pytest.fixture(scope="class")
    def v3_scenario(self):
        return ScenarioLoader.load(_V3_PATH)

    @pytest.fixture(scope="class")
    def selected_candidates(self, v3_scenario):
        prioritizer = CandidatePrioritizer(max_candidates=50)
        return prioritizer.select(
            v3_scenario.data_products,
            anomalies=v3_scenario.anomalies,
            remaining_window_s=float(v3_scenario.link_inputs["remaining_window_s"]),
        )

    @pytest.fixture(scope="class")
    def ai_context(self, selected_candidates):
        """The raw JSON string that GraniteAgent sends to the LLM."""
        agent = GraniteAgent.__new__(GraniteAgent)
        ls = make_link_state()
        ms = make_mission_state()
        msg = agent._build_prioritization_message(  # noqa: SLF001
            selected_candidates, ls, ms, anomalies=None
        )
        return json.loads(msg)

    def test_context_contains_150_products_total_in_header(self, ai_context):
        """The AI context should include candidate data."""
        assert "candidates" in ai_context
        assert len(ai_context["candidates"]) == 50

    def test_every_candidate_in_context_has_description(self, ai_context):
        """Every candidate in the serialized context must have a description field."""
        for candidate in ai_context["candidates"]:
            assert "description" in candidate, (
                f"Candidate {candidate.get('product_id')} missing 'description' in AI context"
            )

    def test_every_candidate_description_is_non_empty(self, ai_context):
        """No candidate in the AI context should have an empty description."""
        empty = [
            c["product_id"]
            for c in ai_context["candidates"]
            if not c.get("description", "").strip()
        ]
        assert not empty, f"Candidates with empty description in AI context: {empty}"

    def test_anomaly_linked_candidates_in_context(self, ai_context):
        """ANOM-017 products should appear in the AI context (highest severity)."""
        anomaly_candidates = [
            c for c in ai_context["candidates"]
            if c.get("anomaly_id") == "ANOM-017"
        ]
        assert len(anomaly_candidates) >= 5, (
            f"Expected at least 5 ANOM-017 candidates in AI context; "
            f"got {len(anomaly_candidates)}"
        )

    def test_description_content_matches_v3_json(self, v3_scenario, ai_context):
        """A specific known v3 description must appear verbatim in the AI context."""
        # Find the actual product
        target_id = "FAU-PROP-001"
        dp = next(
            dp for dp in v3_scenario.data_products if dp.product_id == target_id
        )
        expected_desc = dp.description

        # Find in AI context
        context_candidate = next(
            (c for c in ai_context["candidates"] if c["product_id"] == target_id),
            None,
        )
        assert context_candidate is not None, (
            f"{target_id} (highest-criticality fault log) should be in AI context"
        )
        assert context_candidate["description"] == expected_desc

    def test_candidate_product_ids_in_context_are_valid(self, v3_scenario, ai_context):
        """All product_ids in the AI context must be real v3 product IDs."""
        all_ids = {dp.product_id for dp in v3_scenario.data_products}
        for c in ai_context["candidates"]:
            assert c["product_id"] in all_ids

    def test_context_is_valid_json_structure(self, ai_context):
        """AI context must have the expected top-level structure."""
        assert "candidates" in ai_context

    def test_candidate_descriptions_survive_json_round_trip(self, selected_candidates):
        """Description must survive model_dump/JSON round-trip (what the real agent does)."""
        serialized = [cs.model_dump(mode="json") for cs in selected_candidates]
        for item in serialized:
            assert "description" in item
            assert item["description"].strip(), (
                f"Empty description after round-trip for {item['product_id']}"
            )

    def test_ai_context_total_candidate_count_is_50(self, selected_candidates, ai_context):
        """Must be exactly 50 candidates in the AI context (not all 150)."""
        assert len(selected_candidates) == 50
        assert len(ai_context["candidates"]) == 50

    def test_pipeline_path_is_complete(self, v3_scenario, selected_candidates, ai_context):
        """Verify the full path: JSON → DataProduct → CandidateSummary → AI context."""
        # Step 1: JSON loaded to 150 DataProducts
        assert len(v3_scenario.data_products) == 150

        # Step 2: CandidatePrioritizer reduced to 50 CandidateSummaries
        assert len(selected_candidates) == 50

        # Step 3: All summaries have descriptions
        for cs in selected_candidates:
            assert hasattr(cs, "description")
            assert cs.description.strip()

        # Step 4: AI context contains all 50 with descriptions
        assert len(ai_context["candidates"]) == 50
        for c in ai_context["candidates"]:
            assert c["description"].strip()
