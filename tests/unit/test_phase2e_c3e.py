"""Phase 2E-C3-E — Communication geometry in AI prioritization context.

Tests that spacecraft distance/geometry context flows correctly through:

  mission_data_v3.json (distance_km=54000000)
       ↓
  scenario.distance_km
       ↓
  routes_agent: distance_km extracted
       ↓
  provider.prioritize_candidates(distance_km=...)
       ↓
  _build_prioritization_message(distance_km=...)
       ↓
  JSON context["communication_geometry"] = {distance_km, propagation_delay_s, round_trip_time_s}
       ↓
  AI (or LocalRuleBasedProvider)

Constraints verified:
- Geometry present for v3 (has distance_km).
- Geometry is null for v2/nominal_pass (no distance_km).
- Formula matches routes_state._SPEED_OF_LIGHT_M_S = 299_792_458.0.
- All four providers (Granite, Gemini, Ollama, Local) accept distance_km.
- link_context is unchanged — no geometry fields leaked into it.
- LocalRuleBasedProvider appends geometry sentence when distance_km provided.
- LocalRuleBasedProvider overall_reasoning unchanged when distance_km is None.
- CandidatePrioritization is valid regardless of geometry presence.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPEED_OF_LIGHT_M_S = 299_792_458.0  # must match routes_state

_V3_PATH = (
    Path(__file__).parent.parent.parent / "data" / "scenarios" / "mission_data_v3.json"
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _make_link_state(**kwargs):
    from backend.app.models.link_state import LinkState
    from datetime import datetime, timezone

    defaults = {
        "timestamp": datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        "snr_db": 15.0,
        "eb_n0_db": 10.0,
        "ber": 1e-6,
        "rssi_dbm": -80.0,
        "nominal_data_rate_bps": 1_200_000.0,
        "link_goodput_bps": 1_000_000.0,
        "latency_s": 0.1,
        "link_stability": 0.98,
        "remaining_window_s": 600.0,
    }
    defaults.update(kwargs)
    return LinkState(**defaults)


def _make_mission_state(**kwargs):
    from backend.app.models.mission_state import MissionState
    from backend.app.models.risk_level import RiskLevel

    defaults = {
        "mission_id": "TEST-MSN-001",
        "mission_phase": "science",
        "current_event": "nominal",
        "event_time_remaining_s": 3600.0,
        "comm_window_remaining_s": 600.0,
        "risk_score": 0.3,
        "risk_level": RiskLevel.LOW,
    }
    defaults.update(kwargs)
    return MissionState(**defaults)


def _make_candidate(product_id: str = "P-001") -> "CandidateSummary":
    from backend.app.models.candidate_summary import CandidateSummary

    return CandidateSummary(
        product_id=product_id,
        subsystem="power",
        product_type="telemetry",
        criticality=0.8,
        mission_relevance=0.7,
        scientific_value=0.2,
        deadline_s=300.0,
        age_s=60.0,
        size_bits=1_000_000,
        anomaly_id=None,
        related_ids=[],
        description="Battery voltage telemetry from the primary power bus.",
    )


# ---------------------------------------------------------------------------
# Formula correctness
# ---------------------------------------------------------------------------


class TestGeometryFormula:
    """Verify that the formula matches routes_state._SPEED_OF_LIGHT_M_S."""

    @pytest.mark.parametrize(
        "distance_km, expected_delay_s",
        [
            (300_000.0, 300_000 * 1_000 / _SPEED_OF_LIGHT_M_S),   # ~1.001 s (near Moon)
            (54_000_000.0, 54_000_000 * 1_000 / _SPEED_OF_LIGHT_M_S),  # ~180.1 s (Mars)
            (1_000_000.0, 1_000_000 * 1_000 / _SPEED_OF_LIGHT_M_S),
        ],
    )
    def test_propagation_delay_formula(self, distance_km, expected_delay_s):
        from backend.app.agent.granite_agent import GraniteAgent
        from backend.app.models.anomaly_event import AnomalyEvent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=distance_km,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]
        assert geom is not None
        assert abs(geom["propagation_delay_s"] - round(expected_delay_s, 3)) < 0.001
        assert abs(geom["round_trip_time_s"] - round(2.0 * expected_delay_s, 3)) < 0.001

    def test_rtt_is_twice_one_way(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        geom = ctx["communication_geometry"]
        assert abs(geom["round_trip_time_s"] - 2.0 * geom["propagation_delay_s"]) < 0.001

    def test_distance_km_preserved_in_context(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=12_345_678.9,
        )
        ctx = json.loads(msg)
        assert ctx["communication_geometry"]["distance_km"] == 12_345_678.9


# ---------------------------------------------------------------------------
# Geometry present for v3, null for legacy scenarios
# ---------------------------------------------------------------------------


class TestGeometryPresence:
    def test_v3_geometry_present(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        assert ctx["communication_geometry"] is not None
        assert set(ctx["communication_geometry"].keys()) == {
            "distance_km",
            "propagation_delay_s",
            "round_trip_time_s",
        }

    def test_no_distance_geometry_is_null(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=None,
        )
        ctx = json.loads(msg)
        assert "communication_geometry" in ctx
        assert ctx["communication_geometry"] is None

    def test_default_distance_is_none(self):
        """Calling _build_prioritization_message without distance_km gives null geometry."""
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
        )
        ctx = json.loads(msg)
        assert ctx["communication_geometry"] is None


# ---------------------------------------------------------------------------
# link_context is UNCHANGED — no geometry fields leaked into it
# ---------------------------------------------------------------------------


class TestLinkContextUnchanged:
    def test_link_context_keys_are_exact(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        ctx = json.loads(msg)
        assert set(ctx["link_context"].keys()) == {
            "remaining_window_s",
            "ber",
            "link_goodput_bps",
        }

    def test_link_context_no_geometry_without_distance(self):
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        msg = agent._build_prioritization_message(  # noqa: SLF001
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
        )
        ctx = json.loads(msg)
        assert "distance_km" not in ctx["link_context"]
        assert "propagation_delay_s" not in ctx["link_context"]


# ---------------------------------------------------------------------------
# LocalRuleBasedProvider geometry sentence
# ---------------------------------------------------------------------------


class TestLocalProviderGeometry:
    def test_geometry_sentence_present_when_distance_given(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        assert "million km" in result.overall_reasoning
        assert "propagation" in result.overall_reasoning

    def test_geometry_sentence_absent_when_no_distance(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=None,
        )
        assert "million km" not in result.overall_reasoning
        assert "propagation" not in result.overall_reasoning

    def test_geometry_distance_value_in_reasoning(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        # 54_000_000 km / 1_000_000 = 54.0 million km
        assert "54.0 million km" in result.overall_reasoning

    def test_local_prioritization_result_is_valid(self):
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.candidate_prioritization import CandidatePrioritization

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            [_make_candidate("P-A"), _make_candidate("P-B")],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        assert isinstance(result, CandidatePrioritization)
        assert result.candidate_count == 2
        assert len(result.ranked_products) == 2


# ---------------------------------------------------------------------------
# All providers accept distance_km keyword argument
# ---------------------------------------------------------------------------


class TestProviderInterfaceAcceptance:
    """Verify all four providers accept distance_km without raising TypeError."""

    def test_granite_provider_accepts_distance_km(self):
        from backend.app.agent.granite_provider import GraniteProvider
        from backend.app.agent.granite_agent import GraniteAgent

        agent = GraniteAgent.__new__(GraniteAgent)
        # Patch _build_prioritization_message and _call_prioritization_api
        agent._build_prioritization_message = MagicMock(return_value='{"x":1}')
        agent._call_prioritization_api = MagicMock(return_value='{}')

        provider = GraniteProvider(agent=agent)
        # Should not raise TypeError for the keyword argument
        with pytest.raises(Exception):
            # Will fail on response parsing — that's fine, we just test kwarg routing
            provider.prioritize_candidates(
                [_make_candidate()],
                _make_link_state(),
                _make_mission_state(),
                None,
                distance_km=54_000_000.0,
            )
        # Verify _build_prioritization_message was called with distance_km
        call_kwargs = agent._build_prioritization_message.call_args
        assert call_kwargs.kwargs.get("distance_km") == 54_000_000.0

    def test_local_provider_accepts_distance_km(self):
        """LocalRuleBasedProvider must accept distance_km without TypeError."""
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        provider = LocalRuleBasedProvider()
        # Should not raise TypeError
        result = provider.prioritize_candidates(
            [_make_candidate()],
            _make_link_state(),
            _make_mission_state(),
            None,
            distance_km=54_000_000.0,
        )
        assert result is not None

    def test_base_provider_signature_includes_distance_km(self):
        """BaseAIProvider.prioritize_candidates signature must include distance_km."""
        import inspect
        from backend.app.agent.base_provider import BaseAIProvider

        sig = inspect.signature(BaseAIProvider.prioritize_candidates)
        assert "distance_km" in sig.parameters

    def test_gemini_prioritize_candidates_signature(self):
        import inspect
        from backend.app.agent.gemini_provider import GeminiProvider

        sig = inspect.signature(GeminiProvider.prioritize_candidates)
        assert "distance_km" in sig.parameters

    def test_ollama_prioritize_candidates_signature(self):
        import inspect
        from backend.app.agent.ollama_provider import OllamaProvider

        sig = inspect.signature(OllamaProvider.prioritize_candidates)
        assert "distance_km" in sig.parameters

    def test_granite_agent_prioritize_candidates_signature(self):
        import inspect
        from backend.app.agent.granite_agent import GraniteAgent

        sig = inspect.signature(GraniteAgent.prioritize_candidates)
        assert "distance_km" in sig.parameters

    def test_build_prioritization_message_signature(self):
        import inspect
        from backend.app.agent.granite_agent import GraniteAgent

        sig = inspect.signature(GraniteAgent._build_prioritization_message)
        assert "distance_km" in sig.parameters


# ---------------------------------------------------------------------------
# Scenario-level: v3 has distance_km, v2 does not
# ---------------------------------------------------------------------------


class TestScenarioDistanceKm:
    @pytest.mark.skipif(not _V3_PATH.exists(), reason="mission_data_v3.json not found")
    def test_v3_scenario_has_distance_km(self):
        from backend.app.simulation.scenario_loader import ScenarioLoader

        loader = ScenarioLoader()
        scenario = loader.load(str(_V3_PATH))
        assert scenario.distance_km == 54_000_000.0

    def test_v2_scenario_distance_km_is_none(self):
        """v2 scenario must not have distance_km — it defaults to None."""
        v2_path = _V3_PATH.parent / "mission_data_v2.json"
        if not v2_path.exists():
            pytest.skip("mission_data_v2.json not found")
        from backend.app.simulation.scenario_loader import ScenarioLoader

        loader = ScenarioLoader()
        scenario = loader.load(str(v2_path))
        assert scenario.distance_km is None

    def test_nominal_pass_distance_km_is_none(self):
        nominal_path = _V3_PATH.parent / "nominal_pass.json"
        if not nominal_path.exists():
            pytest.skip("nominal_pass.json not found")
        from backend.app.simulation.scenario_loader import ScenarioLoader

        loader = ScenarioLoader()
        scenario = loader.load(str(nominal_path))
        assert scenario.distance_km is None
