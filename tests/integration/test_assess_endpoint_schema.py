"""Backend contract regression test for POST /plans/assess.

Phase 5.1C: Verifies that the serialized response from POST /plans/assess
contains anomaly_coverage_by_id as a JSON array, not a JSON object/dict.

This test uses the actual FastAPI/TestClient path to serialize the response,
so it detects Pydantic model shape regressions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state

_V3_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json")
_ASTERIA_SCENARIO = str(
    Path(__file__).parents[2]
    / "data"
    / "scenarios"
    / "asteria7_thermal_priority_contact_v1.json"
)


@pytest.fixture(autouse=True)
def reset_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


@pytest.fixture
def loaded_v3():
    app_state.load_scenario(_V3_SCENARIO)


@pytest.fixture
def loaded_asteria():
    app_state.load_scenario(_ASTERIA_SCENARIO)


class TestAssessEndpointSchema:
    """POST /plans/assess — serialized response shape regression tests."""

    @pytest.mark.asyncio
    async def test_anomaly_coverage_by_id_is_json_array_v3(self, loaded_v3):
        """anomaly_coverage_by_id must be a JSON array in the serialized response.

        The backend Pydantic model uses list[AnomalyCoverageDetail].
        Pydantic serializes this as a JSON array.
        The frontend TypeScript type must use AnomalyCoverageDetail[], not Record.
        """
        # Get a valid product ID from the scenario
        scenario = app_state.active_scenario
        assert scenario is not None
        assert len(scenario.data_products) > 0
        product_id = scenario.data_products[0].product_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": [product_id]})

        assert resp.status_code == 200
        body = resp.json()
        assert "mission_outcome" in body

        mission_outcome = body["mission_outcome"]
        assert mission_outcome is not None, "v3 scenario must produce a mission_outcome"

        # THE CRITICAL ASSERTION: anomaly_coverage_by_id must be a JSON array
        assert "anomaly_coverage_by_id" in mission_outcome
        coverage = mission_outcome["anomaly_coverage_by_id"]
        assert isinstance(coverage, list), (
            f"anomaly_coverage_by_id must be a JSON array (list), "
            f"got {type(coverage).__name__}: {coverage!r}"
        )

    @pytest.mark.asyncio
    async def test_anomaly_coverage_by_id_elements_have_required_fields(self, loaded_v3):
        """Each element of anomaly_coverage_by_id must have all required fields."""
        scenario = app_state.active_scenario
        assert scenario is not None
        # Use all product IDs to maximise the chance of anomaly coverage
        product_ids = [dp.product_id for dp in scenario.data_products[:20]]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": product_ids})

        assert resp.status_code == 200
        body = resp.json()
        mission_outcome = body["mission_outcome"]
        assert mission_outcome is not None
        coverage = mission_outcome["anomaly_coverage_by_id"]
        assert isinstance(coverage, list)

        for element in coverage:
            assert "anomaly_id" in element, f"element missing anomaly_id: {element}"
            assert "severity" in element, f"element missing severity: {element}"
            assert "total_linked_products" in element, f"element missing total_linked_products: {element}"
            assert "delivered_linked_products" in element, f"element missing delivered_linked_products: {element}"
            assert "coverage_rate" in element, f"element missing coverage_rate: {element}"

    @pytest.mark.asyncio
    async def test_assess_returns_503_when_no_scenario(self):
        """Returns 503 before any scenario is loaded."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": ["FAKE-001"]})
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_assess_complete_response_structure(self, loaded_v3):
        """Full response structure from /plans/assess matches expected schema."""
        scenario = app_state.active_scenario
        assert scenario is not None
        product_id = scenario.data_products[0].product_id

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json={"product_ids": [product_id]})

        assert resp.status_code == 200
        body = resp.json()

        # Top-level fields
        assert "plan" in body
        assert "evaluation" in body
        assert "mission_outcome" in body
        assert "capacity_summary" in body

        # Plan structure
        plan = body["plan"]
        assert plan["plan_id"] == "operator-manual-assess"
        # strategy is rewritten by the authoritative reconstruction path
        assert isinstance(plan["strategy"], str)

        # Evaluation structure
        eval_result = body["evaluation"]
        assert "risk_score" in eval_result
        assert "risk_level" in eval_result

        # Capacity summary
        cap = body["capacity_summary"]
        assert "available_capacity_bits" in cap
        assert "selected_bits" in cap
        assert "selected_count" in cap
        assert "exceeds_capacity" in cap

        # Mission outcome (v3 must have one)
        mo = body["mission_outcome"]
        assert mo is not None
        assert "total_products" in mo
        assert "delivered_products" in mo
        assert "anomaly_coverage_by_id" in mo
        assert isinstance(mo["anomaly_coverage_by_id"], list)
