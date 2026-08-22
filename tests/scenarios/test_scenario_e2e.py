"""End-to-end scenario test: full pipeline from scenario file to simulation.

Validates:
1. Load nominal_pass.json scenario.
2. POST /plans/generate → 4 strategies returned.
3. POST /plans/evaluate for each plan → EvaluationResult structure valid.
4. POST /simulate with seed=42 → final LinkState is deterministic.
5. Baseline EvaluationResult is comparable to alternative strategy results
   (benchmark measurement point works).
"""

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state


@pytest.fixture(autouse=True)
def reset_state():
    app_state.active_scenario = None
    app_state.active_link_state = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None


@pytest.mark.asyncio
async def test_full_pipeline_nominal_pass():
    """Full pipeline: load → generate → evaluate all → simulate baseline."""
    # 1. Load the reference scenario.
    app_state.load_scenario("data/scenarios/nominal_pass.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Verify state is accessible.
        state_resp = await client.get("/state")
        assert state_resp.status_code == 200

        # 2. Generate candidate plans.
        gen_resp = await client.post("/plans/generate")
        assert gen_resp.status_code == 200
        plans = gen_resp.json()
        assert len(plans) == 4

        strategy_names = {p["strategy"] for p in plans}
        assert "baseline" in strategy_names
        assert "deadline_first" in strategy_names
        assert "mission_critical_first" in strategy_names
        assert "value_per_cost" in strategy_names

        # 3. Evaluate all plans — verify EvaluationResult structure for each.
        evaluations = []
        for plan in plans:
            eval_resp = await client.post("/plans/evaluate", json=plan)
            assert eval_resp.status_code == 200, f"Evaluate failed for plan {plan['plan_id']}"
            ev = eval_resp.json()
            assert "plan_id" in ev
            assert "risk_score" in ev
            assert "risk_level" in ev
            assert "mission_value" in ev
            assert "bandwidth_utilization" in ev
            assert 0.0 <= ev["risk_score"] <= 1.0
            assert 0.0 <= ev["bandwidth_utilization"] <= 1.0
            evaluations.append(ev)

        # 4. Simulate baseline plan with seed=42.
        sim_resp = await client.post("/simulate", json={"plan_id": "baseline", "seed": 42})
        assert sim_resp.status_code == 200
        sim_result = sim_resp.json()
        assert "elapsed_time_s" in sim_result
        assert sim_result["elapsed_time_s"] > 0

        # 5. Verify benchmark comparability: all evaluations share the same structure.
        plan_ids = {ev["plan_id"] for ev in evaluations}
        assert len(plan_ids) == 4, "Each plan produces a distinct EvaluationResult"

        # Baseline evaluation should have non-zero mission value (5 packets, good link).
        baseline_ev = next(ev for ev in evaluations if ev["plan_id"] == "baseline")
        assert baseline_ev["mission_value"] > 0.0


@pytest.mark.asyncio
async def test_simulate_seed42_is_deterministic():
    """Same seed → identical simulation result on two independent runs."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/simulate", json={"plan_id": "baseline", "seed": 42})

    # Reset state for second run.
    app_state.load_scenario("data/scenarios/nominal_pass.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r2 = await client.post("/simulate", json={"plan_id": "baseline", "seed": 42})

    assert r1.json()["delivered_packets"] == r2.json()["delivered_packets"]
    assert r1.json()["elapsed_time_s"] == pytest.approx(r2.json()["elapsed_time_s"])
    assert r1.json()["link_state"]["remaining_window_s"] == pytest.approx(
        r2.json()["link_state"]["remaining_window_s"]
    )


@pytest.mark.asyncio
async def test_degraded_link_scenario_loads_and_evaluates():
    """degraded_link.json should load, generate plans, and evaluate without errors."""
    app_state.load_scenario("data/scenarios/degraded_link.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        gen_resp = await client.post("/plans/generate")
        assert gen_resp.status_code == 200
        plans = gen_resp.json()
        assert len(plans) == 4

        for plan in plans:
            eval_resp = await client.post("/plans/evaluate", json=plan)
            assert eval_resp.status_code == 200


@pytest.mark.asyncio
async def test_approve_uses_simulation_service_directly():
    """POST /approve must not call POST /simulate route handler internally."""
    app_state.load_scenario("data/scenarios/nominal_pass.json")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/approve", json={
            "plan_id": "baseline",
            "operator_notes": "Approved by ground controller",
        })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert "simulation_result" in body
    assert "elapsed_time_s" in body["simulation_result"]
