"""Scenario randomizer — applies bounded jitter to a loaded Scenario.

Called once per scenario reset to produce a slightly different but realistic
mission/link condition.  The JSON template file is never modified; jitter is
applied only to the in-memory copy returned by ScenarioLoader.

Design principles
-----------------
* Pure function: no global state, no side effects.
* Bounded: every jittered value stays inside physically and operationally
  reasonable limits that are deliberately wider than the template range.
* Consistent: called exactly once at reset time; the resulting Scenario is
  stored in ``app.state.active_scenario`` and remains unchanged until the
  next reset.
* Deterministic for tests: callers can pass an explicit ``rng`` instance
  (``random.Random(seed)``) so results are reproducible in tests.

Randomized fields
-----------------
Link inputs (in link_inputs dict):
  snr_db             ±4 dB      clamped to [-10, 25] dB
  rssi_dbm           ±5 dBm     clamped to [-115, -60] dBm
  latency_s          ±0.15 s    clamped to [0.05, 2.5] s
  link_stability     ±0.08      clamped to [0.30, 1.00]
  remaining_window_s ±50 s      clamped to [60, 600] s
  nominal_data_rate_bps — NOT randomized (hardware constant)
  timestamp          — NOT randomized (irrelevant for demo)

Mission state:
  comm_window_remaining_s  — kept equal to link_inputs.remaining_window_s
  event_time_remaining_s   — kept equal to comm_window_remaining_s
  risk_score / risk_level  — NOT changed here; evaluator is the sole authority
"""

from __future__ import annotations

import random
from typing import Any

from ..models.scenario import Scenario


# ---------------------------------------------------------------------------
# Jitter configuration
# ---------------------------------------------------------------------------

_JITTER: dict[str, tuple[float, float, float, float]] = {
    # field: (half_range, min_value, max_value, round_digits)
    "snr_db":              (4.0,  -10.0,  25.0,  1),
    "rssi_dbm":            (5.0, -115.0, -60.0,  1),
    "latency_s":           (0.15,  0.05,   2.5,  3),
    "link_stability":      (0.08,  0.30,   1.0,  2),
    "remaining_window_s":  (50.0, 60.0,  600.0,  0),
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def randomize_scenario(
    scenario: Scenario,
    rng: random.Random | None = None,
) -> Scenario:
    """Return a new Scenario with bounded-random link/mission values.

    The original *scenario* object is never mutated.  A deep copy of
    ``link_inputs`` and ``mission_state`` is made and the jittered values are
    applied to the copy.

    Args:
        scenario: The loaded baseline scenario (from ScenarioLoader).
        rng:      Optional seeded Random instance for reproducible tests.
                  Defaults to ``random.Random()`` (uses OS entropy).

    Returns:
        A new :class:`Scenario` instance with jittered runtime values.
    """
    if rng is None:
        rng = random.Random()

    # ── Jitter link_inputs ────────────────────────────────────────────────
    new_inputs: dict[str, Any] = dict(scenario.link_inputs)  # shallow copy

    for field, (half, lo, hi, digits) in _JITTER.items():
        if field not in new_inputs:
            continue  # field absent in this scenario template — skip safely
        base: float = float(new_inputs[field])
        delta: float = rng.uniform(-half, half)
        jittered: float = _clamp(round(base + delta, digits), lo, hi)
        new_inputs[field] = jittered

    # ── Keep mission_state window in sync ─────────────────────────────────
    new_window: float = float(new_inputs["remaining_window_s"])
    new_mission = scenario.mission_state.model_copy(update={
        "comm_window_remaining_s": new_window,
        "event_time_remaining_s": new_window,
    })

    # ── Return new Scenario (original is not mutated) ─────────────────────
    return scenario.model_copy(update={
        "link_inputs": new_inputs,
        "mission_state": new_mission,
    })
