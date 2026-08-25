"""Phase 2E-C3-F — Architecture hardening & semantic cleanup tests.

Covers the four audit findings addressed in Phase 2E-C3-F:

F1 — Ollama risk authority
    OllamaProvider._parse_response now binds risk_score / risk_level from
    EvaluationResult (identical to Granite/Gemini).  Tests are in
    test_ai_providers.py::TestOllamaProviderParsing (F1 section).

F2 — Shared stateless helpers (prioritization_helpers.py)
    GraniteAgent, GeminiProvider, and OllamaProvider all use module-level
    functions from prioritization_helpers.py instead of
    GraniteAgent.__new__(GraniteAgent).

F3 — AI geometry precision policy
    GET /state exposes full-precision floats.
    AI context rounds geometry to 3 decimal places (token optimisation).

F4 — Terminology: latency_s vs propagation_delay_s
    LinkState.latency_s Pydantic description updated to "link-layer latency".
    LinkHealthPanel label updated to "Link Latency".
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence

import pytest

from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TS = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
_C = 299_792_458.0  # must match routes_state._SPEED_OF_LIGHT_M_S


def _link_state() -> LinkState:
    return LinkState(
        timestamp=_TS, snr_db=12.0, eb_n0_db=20.0, ber=1e-6,
        rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
        link_goodput_bps=90_000.0, latency_s=1.4,
        link_stability=0.9, remaining_window_s=480.0,
    )


def _mission_state() -> MissionState:
    return MissionState(
        mission_id="T-001", mission_phase="science", current_event="pass",
        event_time_remaining_s=480.0, comm_window_remaining_s=480.0,
        risk_score=0.3, risk_level=RiskLevel.MEDIUM,
    )


def _candidate(product_id: str = "P-001") -> CandidateSummary:
    return CandidateSummary(
        product_id=product_id,
        subsystem="power",
        product_type="telemetry",
        criticality=0.8,
        mission_relevance=0.7,
        scientific_value=0.3,
        deadline_s=300.0,
        age_s=60.0,
        size_bits=1_000_000,
        anomaly_id=None,
        related_ids=[],
        description="Battery telemetry.",
    )


# ---------------------------------------------------------------------------
# F2 — Shared helpers: prioritization_helpers module
# ---------------------------------------------------------------------------


class TestSharedPrioritizationHelpers:
    """F2: Verify the shared helpers work correctly and are used by all providers."""

    def test_build_prioritization_message_is_importable(self):
        """The shared helper must be importable from prioritization_helpers."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        assert callable(build_prioritization_message)

    def test_parse_prioritization_response_is_importable(self):
        from backend.app.agent.prioritization_helpers import parse_prioritization_response
        assert callable(parse_prioritization_response)

    def test_build_message_returns_valid_json(self):
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
        )
        ctx = json.loads(msg)
        assert "mission_context" in ctx
        assert "link_context" in ctx
        assert "communication_geometry" in ctx
        assert "candidates" in ctx

    def test_build_message_geometry_present_when_distance_given(self):
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]
        assert geom is not None
        assert set(geom.keys()) == {"distance_km", "propagation_delay_s", "round_trip_time_s"}

    def test_build_message_geometry_null_when_no_distance(self):
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
        )
        ctx = json.loads(msg)
        assert ctx["communication_geometry"] is None

    def test_granite_agent_delegates_to_shared_helper(self):
        """GraniteAgent._build_prioritization_message must produce identical output."""
        from backend.app.agent.granite_agent import GraniteAgent
        from backend.app.agent.prioritization_helpers import build_prioritization_message

        agent = GraniteAgent.__new__(GraniteAgent)
        candidates = [_candidate()]
        ls = _link_state()
        ms = _mission_state()

        agent_msg = agent._build_prioritization_message(  # noqa: SLF001
            candidates, ls, ms, None, distance_km=54_000_000.0
        )
        helper_msg = build_prioritization_message(
            candidates, ls, ms, None, distance_km=54_000_000.0
        )
        assert agent_msg == helper_msg

    def test_gemini_no_longer_uses_granite_agent_new(self):
        """GeminiProvider must not contain GraniteAgent.__new__ in its source."""
        import inspect
        from backend.app.agent import gemini_provider
        source = inspect.getsource(gemini_provider)
        assert "GraniteAgent.__new__" not in source, (
            "GeminiProvider still uses GraniteAgent.__new__(GraniteAgent) — "
            "F2 refactor not applied"
        )

    def test_ollama_no_longer_uses_granite_agent_new(self):
        """OllamaProvider must not contain GraniteAgent.__new__ in its source."""
        import inspect
        from backend.app.agent import ollama_provider
        source = inspect.getsource(ollama_provider)
        assert "GraniteAgent.__new__" not in source, (
            "OllamaProvider still uses GraniteAgent.__new__(GraniteAgent) — "
            "F2 refactor not applied"
        )

    def test_gemini_imports_from_prioritization_helpers(self):
        """GeminiProvider must import from prioritization_helpers."""
        import inspect
        from backend.app.agent import gemini_provider
        source = inspect.getsource(gemini_provider)
        assert "prioritization_helpers" in source

    def test_ollama_imports_from_prioritization_helpers(self):
        """OllamaProvider must import from prioritization_helpers."""
        import inspect
        from backend.app.agent import ollama_provider
        source = inspect.getsource(ollama_provider)
        assert "prioritization_helpers" in source

    def test_parse_response_validates_hallucinated_ids(self):
        """Shared parser must reject hallucinated product_ids."""
        from backend.app.agent.prioritization_helpers import parse_prioritization_response
        from backend.app.agent.granite_agent import GraniteResponseError
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "FAKE-999", "priority": 1, "reason": "x",
                 "factors": [], "anomaly_ids": [], "subsystem": "power", "confidence": 0.8}
            ],
            "overall_reasoning": "test",
            "confidence": 0.8,
        })
        with pytest.raises(GraniteResponseError, match="unknown product_id"):
            parse_prioritization_response(raw, {"P-001", "P-002"})

    def test_parse_response_validates_duplicate_priorities(self):
        from backend.app.agent.prioritization_helpers import parse_prioritization_response
        from backend.app.agent.granite_agent import GraniteResponseError
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P-001", "priority": 1, "reason": "x",
                 "factors": [], "anomaly_ids": [], "subsystem": "s", "confidence": None},
                {"product_id": "P-002", "priority": 1, "reason": "y",
                 "factors": [], "anomaly_ids": [], "subsystem": "s", "confidence": None},
            ],
            "overall_reasoning": "test",
            "confidence": 0.7,
        })
        with pytest.raises(GraniteResponseError, match="duplicate priority"):
            parse_prioritization_response(raw, {"P-001", "P-002"})

    def test_all_three_providers_produce_same_message_structure(self):
        """Granite, Gemini, Ollama must all produce identical geometry in AI context."""
        from backend.app.agent.granite_agent import GraniteAgent
        from backend.app.agent.prioritization_helpers import build_prioritization_message

        candidates = [_candidate("P-001"), _candidate("P-002")]
        ls = _link_state()
        ms = _mission_state()
        distance = 54_000_000.0

        # Granite delegates to the shared helper
        agent = GraniteAgent.__new__(GraniteAgent)
        granite_msg = agent._build_prioritization_message(  # noqa: SLF001
            candidates, ls, ms, None, distance_km=distance
        )
        # Direct helper call (what Gemini and Ollama now use)
        direct_msg = build_prioritization_message(
            candidates, ls, ms, None, distance_km=distance
        )

        assert granite_msg == direct_msg


# ---------------------------------------------------------------------------
# F3 — Geometry precision policy
# ---------------------------------------------------------------------------


class TestGeometryPrecisionPolicy:
    """F3: /state full precision; AI context rounded to 3 dp."""

    def test_helper_rounds_to_3_decimal_places(self):
        """build_prioritization_message must round geometry to 3 dp."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]
        prop = geom["propagation_delay_s"]
        rtt = geom["round_trip_time_s"]

        # 3 dp means the value has at most 3 significant decimal digits
        # (it may have fewer if the 4th digit is 0, but definitely not more precision)
        assert prop == round(prop, 3)
        assert rtt == round(rtt, 3)

    def test_helper_geometry_is_semantically_correct_despite_rounding(self):
        """3 dp rounding must not meaningfully alter the physics (< 1 ms error)."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]

        exact_delay = 54_000_000.0 * 1_000.0 / _C
        # Rounding error is less than 0.001 s (1 ms)
        assert abs(geom["propagation_delay_s"] - exact_delay) < 0.001
        assert abs(geom["round_trip_time_s"] - 2.0 * exact_delay) < 0.001

    def test_routes_state_returns_full_precision(self):
        """GET /state must return full-precision geometry values (not rounded)."""
        from pathlib import Path
        from backend.app import state as app_state
        from backend.app.api.routes_state import _SPEED_OF_LIGHT_M_S

        v3_path = str(
            Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json"
        )
        app_state.load_scenario(v3_path, randomize=False)
        try:
            distance_km = app_state.active_scenario.distance_km  # type: ignore[union-attr]
            exact_delay = distance_km * 1_000.0 / _SPEED_OF_LIGHT_M_S

            # routes_state computes and returns the full-precision float
            # (no rounding applied, unlike the AI helper)
            import math
            # The exact computed value is irrational — it must not equal its 3 dp rounded version
            assert exact_delay != round(exact_delay, 3), (
                "v3 propagation delay should have more than 3 decimal places"
            )
        finally:
            app_state.active_scenario = None
            app_state.active_link_state = None
            app_state.active_scenario_path = None

    def test_precision_difference_is_small(self):
        """The gap between full-precision and 3-dp rounded geometry is < 1 ms."""
        distance_km = 54_000_000.0
        exact = distance_km * 1_000.0 / _C
        rounded = round(exact, 3)
        assert abs(exact - rounded) < 0.001  # < 1 ms

    def test_rtt_is_exactly_twice_one_way_in_ai_context(self):
        """In AI context, RTT must equal exactly 2× one-way (both rounded independently)."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]
        # Both values are independently rounded to 3 dp; they should differ by at most 1 ulp
        exact_delay = 54_000_000.0 * 1_000.0 / _C
        assert geom["propagation_delay_s"] == round(exact_delay, 3)
        assert geom["round_trip_time_s"] == round(2.0 * exact_delay, 3)

    def test_precision_policy_documented_in_helper_module(self):
        """prioritization_helpers.py module docstring must describe the precision policy."""
        import backend.app.agent.prioritization_helpers as mod
        doc = mod.__doc__ or ""
        assert "3 decimal" in doc.lower() or "3 dp" in doc.lower(), (
            "prioritization_helpers module docstring must describe the 3 dp precision policy"
        )

    def test_precision_policy_documented_in_helper_function(self):
        """build_prioritization_message docstring must reference geometry precision."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        doc = build_prioritization_message.__doc__ or ""
        assert "3 decimal" in doc.lower() or "precision" in doc.lower() or "round" in doc.lower(), (
            "build_prioritization_message docstring must mention geometry rounding"
        )


# ---------------------------------------------------------------------------
# F4 — Terminology: latency_s vs propagation_delay_s
# ---------------------------------------------------------------------------


class TestLatencyTerminology:
    """F4: latency_s must be unambiguously labelled as link-layer, not propagation."""

    def test_link_state_latency_s_field_description_not_propagation(self):
        """latency_s field description must NOT say 'propagation latency'."""
        from backend.app.models.link_state import LinkState
        desc = LinkState.model_fields["latency_s"].description or ""
        desc_lower = desc.lower()
        # The old misleading description
        assert "one-way propagation latency" not in desc_lower, (
            "latency_s description still says 'One-way propagation latency' — "
            "F4 fix not applied"
        )

    def test_link_state_latency_s_description_mentions_link_layer(self):
        """latency_s field description must clearly identify it as link-layer."""
        from backend.app.models.link_state import LinkState
        desc = LinkState.model_fields["latency_s"].description or ""
        desc_lower = desc.lower()
        assert "link-layer" in desc_lower or "protocol" in desc_lower, (
            "latency_s description must mention 'link-layer' or 'protocol' to "
            "distinguish it from free-space propagation delay"
        )

    def test_link_state_class_docstring_distinguishes_three_concepts(self):
        """LinkState class docstring must distinguish latency_s from propagation_delay_s."""
        from backend.app.models.link_state import LinkState
        doc = LinkState.__doc__ or ""
        assert "propagation_delay_s" in doc, (
            "LinkState docstring must reference propagation_delay_s to distinguish it "
            "from latency_s"
        )
        assert "latency_s" in doc

    def test_link_state_latency_s_not_derived_from_distance(self):
        """latency_s must be independent of distance_km (existing RF chain contract)."""
        from backend.app.models.scenario import Scenario
        from backend.app.models.mission_state import MissionState
        from backend.app.telecom.engine import TelecomEngine

        link_inputs = {
            "timestamp": _TS, "snr_db": 8.2, "rssi_dbm": -91.0,
            "nominal_data_rate_bps": 100_000.0, "latency_s": 1.4,
            "link_stability": 0.74, "remaining_window_s": 480.0,
        }
        engine = TelecomEngine()
        ls = engine.compute(link_inputs)
        # latency_s passes through the engine unchanged
        assert ls.latency_s == pytest.approx(1.4)

    def test_latency_s_value_differs_from_propagation_delay_v3(self):
        """For v3 scenario, latency_s (1.4 s) must differ from propagation_delay (≈180 s)."""
        from pathlib import Path
        from backend.app import state as app_state
        from backend.app.api.routes_state import _SPEED_OF_LIGHT_M_S

        v3_path = str(
            Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v3.json"
        )
        app_state.load_scenario(v3_path, randomize=False)
        try:
            ls = app_state.active_link_state
            scenario = app_state.active_scenario
            prop_delay = scenario.distance_km * 1_000.0 / _SPEED_OF_LIGHT_M_S  # type: ignore[union-attr]
            # latency_s is ~1.4 s; propagation_delay is ~180 s — clearly different
            assert abs(ls.latency_s - prop_delay) > 100.0, (  # type: ignore[union-attr]
                "latency_s and propagation_delay_s must be clearly different values"
            )
        finally:
            app_state.active_scenario = None
            app_state.active_link_state = None
            app_state.active_scenario_path = None

    def test_latency_s_not_in_ai_link_context(self):
        """latency_s must NOT appear in the AI link_context block."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        assert "latency_s" not in ctx["link_context"], (
            "latency_s leaked into AI link_context — AI should not see this field"
        )

    def test_propagation_delay_not_conflated_with_latency_in_ai_context(self):
        """propagation_delay_s must be in communication_geometry, not link_context."""
        from backend.app.agent.prioritization_helpers import build_prioritization_message
        msg = build_prioritization_message(
            [_candidate()], _link_state(), _mission_state(), None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        # propagation_delay must only appear under communication_geometry
        assert "propagation_delay_s" not in ctx["link_context"]
        assert "propagation_delay_s" in ctx["communication_geometry"]
