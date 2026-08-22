"""Agent tool definitions callable by GraniteAgent during its reasoning turn.

Each tool is a thin wrapper that delegates to the appropriate deterministic
module.  Tools never compute RF/telecom metrics directly; they only call
the domain-layer functions already implemented.

Tool registry (matches GraniteAgent system prompt):

    get_link_state            → state.active_link_state
    get_mission_state         → state.active_scenario.mission_state
    get_transmission_queue    → BaselineScheduler.rank()
    generate_candidate_plans  → CandidateGenerator.generate()
    evaluate_plan             → PlanEvaluator.evaluate()
    simulate_what_if          → TransmissionSimulator.simulate() (no state mutation)

The ``TOOL_SCHEMAS`` list contains OpenAI-compatible function-calling JSON
schemas that can be passed directly to Granite's ``tools`` parameter.
"""

from __future__ import annotations

from typing import Any

from .. import state
from ..config import SchedulerWeights
from ..candidate_generator.generator import CandidateGenerator
from ..evaluator.plan_evaluator import PlanEvaluator
from ..models.candidate_plan import CandidatePlan
from ..models.evaluation_result import EvaluationResult
from ..models.link_state import LinkState
from ..models.mission_state import MissionState
from ..scheduler.baseline import BaselineScheduler
from ..simulation.transmission_sim import TransmissionSimulator

# ---------------------------------------------------------------------------
# Tool function implementations
# ---------------------------------------------------------------------------


def get_link_state() -> dict[str, Any]:
    """Return the current LinkState as a JSON-serialisable dict."""
    if state.active_link_state is None:
        raise RuntimeError("No active scenario loaded")
    return state.active_link_state.model_dump(mode="json")


def get_mission_state() -> dict[str, Any]:
    """Return the current MissionState as a JSON-serialisable dict."""
    if state.active_scenario is None:
        raise RuntimeError("No active scenario loaded")
    return state.active_scenario.mission_state.model_dump(mode="json")


def get_transmission_queue() -> dict[str, Any]:
    """Return the baseline-ranked transmission queue as a JSON-serialisable dict."""
    if state.active_scenario is None or state.active_link_state is None:
        raise RuntimeError("No active scenario loaded")
    weights = SchedulerWeights()
    plan = BaselineScheduler.rank(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )
    return plan.model_dump(mode="json")


def generate_candidate_plans() -> list[dict[str, Any]]:
    """Generate all four candidate plans as a list of JSON-serialisable dicts."""
    if state.active_scenario is None or state.active_link_state is None:
        raise RuntimeError("No active scenario loaded")
    weights = SchedulerWeights()
    plans = CandidateGenerator.generate(
        state.active_scenario.packets,
        state.active_link_state,
        state.active_scenario.mission_state,
        weights,
    )
    return [p.model_dump(mode="json") for p in plans]


def evaluate_plan(plan: CandidatePlan) -> dict[str, Any]:
    """Evaluate a plan analytically and return EvaluationResult as a JSON-serialisable dict."""
    if state.active_scenario is None or state.active_link_state is None:
        raise RuntimeError("No active scenario loaded")
    ev = PlanEvaluator()
    result = ev.evaluate(plan, state.active_link_state, state.active_scenario.mission_state)
    return result.model_dump(mode="json")


def simulate_what_if(plan: CandidatePlan, seed: int | None = None) -> dict[str, Any]:
    """Run a non-mutating what-if simulation and return SimulationResult as JSON dict.

    Does NOT update server state.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise RuntimeError("No active scenario loaded")
    sim = TransmissionSimulator()
    result = sim.simulate(plan, state.active_link_state, state.active_scenario.mission_state, seed=seed)
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# OpenAI-compatible tool schemas for Granite function calling
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_link_state",
            "description": "Return the current communication link state including SNR, BER, goodput, and remaining window.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mission_state",
            "description": "Return the current mission state including risk score, risk level, and remaining communication window.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_transmission_queue",
            "description": "Return the baseline-ranked transmission queue (ordered list of packets).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_candidate_plans",
            "description": "Generate four candidate transmission plans using different strategies.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_plan",
            "description": "Evaluate a candidate plan analytically. Returns expected metrics including mission value, risk score, and deadline misses.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "The plan_id of the plan to evaluate.",
                    }
                },
                "required": ["plan_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_what_if",
            "description": "Run a stochastic what-if simulation for a given plan without mutating server state. Optional seed for reproducibility.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_id": {
                        "type": "string",
                        "description": "The plan_id of the plan to simulate.",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Optional RNG seed for reproducible simulation.",
                    },
                },
                "required": ["plan_id"],
            },
        },
    },
]
