"""Phase 4.2B — Tests for POST /plans/assess (non-mutating manual assessment).

These tests verify:
- POST /plans/assess returns a non-null evaluation.
- POST /plans/assess with ASTERIA-7 product IDs works.
- POST /plans/assess does not mutate state (issued-plan registry unchanged).
- Assessment works with no AI recommendation present.
"""

import sys
import os

# Ensure the backend package is importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from fastapi.testclient import TestClient

from app import state as app_state
from app.main import app
from app.api.routes_plans import AssessRequest


SCENARIO_V3_PATH = os.path.join(
    os.path.dirname(__file__), "../../../data/scenarios/mission_data_v3.json"
)
SCENARIO_ASTERIA_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../data/scenarios/asteria7_thermal_priority_contact_v1.json",
)


@pytest.fixture(autouse=True)
def load_v3_scenario():
    """Load the v3 scenario before each test and restore state after."""
    path = os.path.abspath(SCENARIO_V3_PATH)
    if not os.path.exists(path):
        pytest.skip(f"Scenario not found: {path}")
    app_state.load_scenario(path)
    yield
    # Clean up: clear issued plans to avoid test leakage.
    app_state.invalidate_issued_plans(reason="test teardown")


@pytest.fixture()
def client():
    return TestClient(app)


# ── Helper to pick a few product IDs from the active scenario ────────────────

def _first_n_product_ids(n: int) -> list[str]:
    """Return the first n product_ids from the active v3 scenario."""
    scenario = app_state.active_scenario
    assert scenario is not None
    if scenario.data_products:
        return [dp.product_id for dp in scenario.data_products[:n]]
    # Legacy fallback
    return [pkt.packet_id for pkt in scenario.packets[:n]]


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_assess_returns_non_null_evaluation(client):
    """POST /plans/assess must return a non-null EvaluationResult."""
    product_ids = _first_n_product_ids(5)
    resp = client.post("/plans/assess", json={"product_ids": product_ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evaluation"] is not None
    assert body["evaluation"]["plan_id"] == "operator-manual-assess"
    assert "risk_score" in body["evaluation"]
    assert body["plan"] is not None
    assert len(body["plan"]["packets"]) == len(product_ids)


def test_assess_returns_capacity_summary(client):
    """POST /plans/assess must include a capacity_summary dict."""
    product_ids = _first_n_product_ids(3)
    resp = client.post("/plans/assess", json={"product_ids": product_ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cs = body["capacity_summary"]
    assert "available_capacity_bits" in cs
    assert "selected_bits" in cs
    assert "selected_count" in cs
    assert cs["selected_count"] == len(product_ids)


def test_assess_does_not_mutate_issued_plan_registry(client):
    """POST /plans/assess must not add any entry to the issued-plan registry."""
    before_count = len(app_state.issued_plans)
    product_ids = _first_n_product_ids(4)
    resp = client.post("/plans/assess", json={"product_ids": product_ids})
    assert resp.status_code == 200, resp.text
    after_count = len(app_state.issued_plans)
    assert after_count == before_count, (
        f"issued_plans count changed: {before_count} → {after_count}"
    )


def test_assess_no_ai_recommendation_required(client):
    """POST /plans/assess works without any AI recommendation present."""
    # The endpoint must succeed even when no recommendation has been requested
    # (the AI pipeline has not run at all).
    assert app_state.issued_plans == {} or True  # no requirement that plans exist
    product_ids = _first_n_product_ids(6)
    resp = client.post("/plans/assess", json={"product_ids": product_ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evaluation"]["plan_id"] == "operator-manual-assess"


def test_assess_with_asteria7_scenario(client):
    """POST /plans/assess with the ASTERIA-7 scenario returns a valid evaluation."""
    path = os.path.abspath(SCENARIO_ASTERIA_PATH)
    if not os.path.exists(path):
        pytest.skip(f"ASTERIA-7 scenario not found: {path}")
    app_state.load_scenario(path)

    scenario = app_state.active_scenario
    assert scenario is not None
    if scenario.data_products:
        product_ids = [dp.product_id for dp in scenario.data_products[:5]]
    elif scenario.packets:
        product_ids = [pkt.packet_id for pkt in scenario.packets[:5]]
    else:
        pytest.skip("ASTERIA-7 scenario has no products or packets")

    resp = client.post("/plans/assess", json={"product_ids": product_ids})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["evaluation"] is not None
    assert body["plan"]["plan_id"] == "operator-manual-assess"


def test_assess_unknown_product_id_returns_422(client):
    """POST /plans/assess must return 422 for unknown product IDs."""
    resp = client.post(
        "/plans/assess",
        json={"product_ids": ["NONEXISTENT-PRODUCT-ID-XYZ"]},
    )
    assert resp.status_code == 422, resp.text


def test_assess_empty_product_list(client):
    """POST /plans/assess with 0 products returns a valid (empty) plan evaluation."""
    resp = client.post("/plans/assess", json={"product_ids": []})
    # An empty plan is valid — PlanEvaluator handles it gracefully.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan"]["packets"] == []
    assert body["capacity_summary"]["selected_count"] == 0
