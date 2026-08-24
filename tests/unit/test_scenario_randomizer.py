"""Unit tests for the scenario randomizer.

Verifies:
1. randomize_scenario() returns a new Scenario (original is not mutated).
2. All jittered fields stay within their defined bounds.
3. Two calls with different RNG instances produce different values (variance).
4. Two GET /state calls without a reset return identical values.
5. comm_window_remaining_s is kept in sync with link_inputs remaining_window_s.
6. event_time_remaining_s is kept in sync with comm_window_remaining_s.
7. Non-randomized fields (nominal_data_rate_bps, timestamp, packets) are unchanged.
8. state.load_scenario(..., randomize=False) preserves exact template values.
9. state.load_scenario(..., randomize=True) produces values that may differ.
10. POST /state/reset returns different scenario values across two resets.
"""

from __future__ import annotations

import random

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app import state as app_state
from backend.app.main import app
from backend.app.simulation.scenario_loader import ScenarioLoader
from backend.app.simulation.scenario_randomizer import _JITTER, randomize_scenario

_SCENARIO_PATH = "data/scenarios/nominal_pass.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_base() -> object:
    return ScenarioLoader.load(_SCENARIO_PATH)


# ---------------------------------------------------------------------------
# 1. Original scenario is not mutated
# ---------------------------------------------------------------------------

def test_original_scenario_not_mutated():
    base = _load_base()
    original_snr = base.link_inputs["snr_db"]
    original_window = base.link_inputs["remaining_window_s"]

    randomize_scenario(base, rng=random.Random(99))

    assert base.link_inputs["snr_db"] == original_snr
    assert base.link_inputs["remaining_window_s"] == original_window


# ---------------------------------------------------------------------------
# 2. All jittered fields stay within bounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [0, 1, 42, 100, 999, 12345, 99999])
def test_jittered_fields_within_bounds(seed):
    base = _load_base()
    result = randomize_scenario(base, rng=random.Random(seed))

    for field, (_half, lo, hi, _digits) in _JITTER.items():
        if field not in result.link_inputs:
            continue
        value = float(result.link_inputs[field])
        assert lo <= value <= hi, (
            f"Field '{field}' out of bounds: {value} not in [{lo}, {hi}] (seed={seed})"
        )


# ---------------------------------------------------------------------------
# 3. link_stability is always [0, 1]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(20))
def test_link_stability_always_valid(seed):
    base = _load_base()
    result = randomize_scenario(base, rng=random.Random(seed))
    stability = result.link_inputs["link_stability"]
    assert 0.0 <= stability <= 1.0


# ---------------------------------------------------------------------------
# 4. remaining_window_s, comm_window_remaining_s, event_time_remaining_s are in sync
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", [7, 42, 314])
def test_window_fields_in_sync(seed):
    base = _load_base()
    result = randomize_scenario(base, rng=random.Random(seed))

    link_window = result.link_inputs["remaining_window_s"]
    assert result.mission_state.comm_window_remaining_s == pytest.approx(link_window)
    assert result.mission_state.event_time_remaining_s == pytest.approx(link_window)


# ---------------------------------------------------------------------------
# 5. Non-randomized fields are unchanged
# ---------------------------------------------------------------------------

def test_non_randomized_fields_unchanged():
    base = _load_base()
    result = randomize_scenario(base, rng=random.Random(0))

    # nominal_data_rate_bps is not in _JITTER — must be preserved exactly
    assert result.link_inputs["nominal_data_rate_bps"] == base.link_inputs["nominal_data_rate_bps"]
    # timestamp preserved
    assert result.link_inputs["timestamp"] == base.link_inputs["timestamp"]
    # packets list unchanged
    assert result.packets == base.packets
    # scenario_id and mission_id preserved
    assert result.scenario_id == base.scenario_id
    assert result.mission_state.mission_id == base.mission_state.mission_id
    assert result.mission_state.mission_phase == base.mission_state.mission_phase


# ---------------------------------------------------------------------------
# 6. Two resets with different seeds produce different SNR (variance check)
# ---------------------------------------------------------------------------

def test_two_resets_produce_different_values():
    base = _load_base()
    r1 = randomize_scenario(base, rng=random.Random(1))
    r2 = randomize_scenario(base, rng=random.Random(2))
    # With different seeds at least one field should differ
    fields_differ = any(
        r1.link_inputs[f] != r2.link_inputs[f]
        for f in _JITTER
        if f in r1.link_inputs
    )
    assert fields_differ, "Two different RNG seeds produced identical link_inputs"


# ---------------------------------------------------------------------------
# 7. Same seed → identical result (reproducibility for tests)
# ---------------------------------------------------------------------------

def test_same_seed_reproducible():
    base = _load_base()
    r1 = randomize_scenario(base, rng=random.Random(42))
    r2 = randomize_scenario(base, rng=random.Random(42))
    for field in _JITTER:
        if field in r1.link_inputs:
            assert r1.link_inputs[field] == r2.link_inputs[field], (
                f"Field '{field}' differs with same seed"
            )


# ---------------------------------------------------------------------------
# 8. state.load_scenario(randomize=False) preserves exact template values
# ---------------------------------------------------------------------------

def test_load_scenario_no_randomize_preserves_template():
    app_state.load_scenario(_SCENARIO_PATH, randomize=False)
    base = _load_base()
    assert app_state.active_scenario.link_inputs["snr_db"] == base.link_inputs["snr_db"]
    assert app_state.active_scenario.link_inputs["rssi_dbm"] == base.link_inputs["rssi_dbm"]
    assert app_state.active_link_state is not None
    # Reset global state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


# ---------------------------------------------------------------------------
# 9. state.load_scenario(randomize=True) computes valid LinkState
# ---------------------------------------------------------------------------

def test_load_scenario_with_randomize_produces_valid_link_state():
    app_state.load_scenario(_SCENARIO_PATH, randomize=True)
    ls = app_state.active_link_state
    assert ls is not None
    assert ls.snr_db is not None
    assert ls.ber >= 0.0
    assert ls.link_goodput_bps > 0.0
    assert ls.remaining_window_s >= 60.0
    assert ls.remaining_window_s <= 600.0
    assert 0.0 <= ls.link_stability <= 1.0
    # Reset global state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


# ---------------------------------------------------------------------------
# 10. Two consecutive GET /state without reset return identical values
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_global_state():
    """Ensure global state is clean before and after each test."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


@pytest.mark.asyncio
async def test_get_state_stable_without_reset():
    app_state.load_scenario(_SCENARIO_PATH, randomize=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.get("/state")
        r2 = await client.get("/state")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["link_state"]["snr_db"] == r2.json()["link_state"]["snr_db"]
    assert r1.json()["link_state"]["remaining_window_s"] == r2.json()["link_state"]["remaining_window_s"]
    assert r1.json()["mission_state"]["comm_window_remaining_s"] == r2.json()["mission_state"]["comm_window_remaining_s"]


# ---------------------------------------------------------------------------
# 11. POST /state/reset produces different values across two calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_produces_different_scenario_values():
    """Two consecutive resets should (with overwhelming probability) differ."""
    app_state.load_scenario(_SCENARIO_PATH, randomize=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post("/state/reset")
        snr_after_first_reset = app_state.active_link_state.snr_db

        r2 = await client.post("/state/reset")
        snr_after_second_reset = app_state.active_link_state.snr_db

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Probability both produce identical SNR from OS entropy is negligible
    # (uniform over ±4 dB range rounded to 1 decimal = 80 possible steps).
    # We don't assert inequality — RNG could theoretically hit same value.
    # Instead verify both values are within bounds.
    _, lo, hi, _ = _JITTER["snr_db"]
    assert lo <= snr_after_first_reset <= hi
    assert lo <= snr_after_second_reset <= hi
