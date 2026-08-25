"""Phase 2E-D3 tests — Approval state hardening and transmission integrity.

Covers:

P0-1 — Plan identity preserved through approval
-------------------------------------------------
1.  /approve with full ``plan`` body uses the supplied plan directly (no regeneration).
2.  SimulationResult.plan_id matches the supplied plan's plan_id.
3.  Packet ordering in the simulation matches the supplied plan exactly.
4.  /approve with only ``plan_id`` (legacy form) still works correctly.
5.  /approve/custom behaviour is unchanged (regression).
6.  When ``plan`` and ``plan_id`` are both supplied, ``plan`` is authoritative.

P0-2 — Error / retry handling (backend)
----------------------------------------
7.  /approve with missing scenario returns 503 (not a stuck state — backend contract).

D3-C — RankedProduct.description
----------------------------------
8.  RankedProduct.description defaults to "" (backward compat).
9.  RankedProduct accepts and stores a non-empty description.
10. LocalRuleBasedProvider.prioritize_candidates() forwards CandidateSummary.description
    into RankedProduct.description.
11. parse_prioritization_response() populates description from candidates lookup.
12. parse_prioritization_response() without candidates leaves description as "".
13. DataProduct.description → CandidateSummary.description → RankedProduct.description
    full pipeline check.

D3-E — TransmissionSummaryPanel wiring (backend contract)
-----------------------------------------------------------
14. Regression: /approve simulation result includes correct plan_id and packet lists.
15. Regression: /approve/custom still simulates the custom plan unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app import state as app_state
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_prioritization import RankedProduct, CandidatePrioritization
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.data_product import DataProduct
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel
from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.agent.prioritization_helpers import parse_prioritization_response
from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
from backend.app.simulation.transmission_sim import TransmissionSimulator
from backend.app.telecom.engine import TelecomEngine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_V2_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json")
_NOMINAL_SCENARIO = str(Path(__file__).parents[2] / "data" / "scenarios" / "nominal_pass.json")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state():
    """Reset global state before and after each test."""
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None


@pytest.fixture
def loaded_v2():
    app_state.load_scenario(_V2_SCENARIO)


@pytest.fixture
def loaded_legacy():
    app_state.load_scenario(_NOMINAL_SCENARIO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LINK_INPUTS = {
    "timestamp": datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    "snr_db": 10.0,
    "rssi_dbm": -80.0,
    "nominal_data_rate_bps": 100_000.0,
    "latency_s": 0.25,
    "link_stability": 0.95,
    "remaining_window_s": 300.0,
}

_MISSION_STATE = MissionState(
    mission_id="TEST-D3",
    mission_phase="test",
    current_event="d3_test",
    event_time_remaining_s=300.0,
    comm_window_remaining_s=300.0,
    risk_score=0.1,
    risk_level=RiskLevel.LOW,
)


def _make_link_state() -> LinkState:
    return TelecomEngine().compute(dict(_LINK_INPUTS))


def _make_packet(packet_id: str, criticality: float = 0.5) -> Packet:
    return Packet(
        packet_id=packet_id,
        packet_type="telemetry",
        size_bits=81920,
        criticality=criticality,
        mission_relevance=0.6,
        deadline_s=300.0,
        retry_cost=0.05,
        delivery_requirement="best_effort",
    )


def _make_plan(plan_id: str, packet_ids: list[str]) -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy="test",
        packets=[_make_packet(pid) for pid in packet_ids],
        generated_by="test",
        metadata={},
    )


# ---------------------------------------------------------------------------
# P0-1: Plan identity preserved through approval
# ---------------------------------------------------------------------------


class TestApproveWithFullPlan:
    """P0-1: /approve uses the supplied plan directly, no regeneration."""

    @pytest.mark.asyncio
    async def test_approve_with_plan_body_returns_200(self, loaded_v2):
        """POST /approve with a full plan body must return 200."""
        # Build a custom plan with a fixed packet order that differs from default generation.
        plan = _make_plan("mission-critical-first", ["PKT-A", "PKT-B", "PKT-C"])
        payload = {
            "plan_id": "mission-critical-first",
            "plan": plan.model_dump(mode="json"),
            "operator_notes": "D3 test",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_approve_simulation_result_plan_id_matches_supplied(self, loaded_v2):
        """SimulationResult.plan_id must match the plan supplied in the request."""
        plan = _make_plan("ai-recommended-plan", ["DP-001", "DP-002"])
        payload = {
            "plan_id": "ai-recommended-plan",
            "plan": plan.model_dump(mode="json"),
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["simulation_result"]["plan_id"] == "ai-recommended-plan"

    @pytest.mark.asyncio
    async def test_approve_with_plan_body_does_not_regenerate(self, loaded_v2):
        """When 'plan' is supplied the backend must NOT regenerate from _effective_packets().

        Proof: supply a plan with a single packet not in the v2 scenario.
        If regeneration occurred the packet would not appear; using the plan
        directly it must appear in one of the outcome lists.
        """
        custom_packet_id = "INJECTED-BY-D3-TEST"
        plan = _make_plan("custom-d3", [custom_packet_id])
        payload = {
            "plan_id": "custom-d3",
            "plan": plan.model_dump(mode="json"),
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        sim = body["simulation_result"]
        all_packet_ids = (
            sim["delivered_packets"]
            + sim["deferred_packets"]
            + sim["failed_packets"]
        )
        assert custom_packet_id in all_packet_ids, (
            "Custom packet must appear in simulation result — "
            "if it doesn't, backend regenerated the plan and discarded the supplied one."
        )

    @pytest.mark.asyncio
    async def test_approve_packet_order_preserved(self, loaded_v2):
        """The simulation processes packets in the order supplied in the plan.

        We supply two packets; the one listed first should appear first in the
        delivered list (assuming both succeed in the small window test case).
        Use a seeded simulation for determinism by testing via unit path.
        """
        link_state = _make_link_state()
        packets = [_make_packet("FIRST-PKT", criticality=0.9), _make_packet("SECOND-PKT", criticality=0.1)]
        plan = CandidatePlan(
            plan_id="order-test",
            strategy="test",
            packets=packets,
            generated_by="test",
            metadata={},
        )
        sim = TransmissionSimulator()
        result = sim.simulate(plan, link_state, _MISSION_STATE, seed=42)
        # First packet processed first — it should appear before second in delivered list
        # (both are small enough to fit in the window at seed=42)
        all_outcomes = (
            result.delivered_packets
            + result.deferred_packets
            + result.failed_packets
        )
        # FIRST-PKT must be processed before SECOND-PKT
        if "FIRST-PKT" in all_outcomes and "SECOND-PKT" in all_outcomes:
            assert all_outcomes.index("FIRST-PKT") < all_outcomes.index("SECOND-PKT"), (
                "Packet order must match plan.packets order."
            )

    @pytest.mark.asyncio
    async def test_legacy_plan_id_only_still_works(self, loaded_v2):
        """POST /approve with only plan_id (no plan body) must still return 200."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            # /plans/generate first to know a valid plan_id
            gen_resp = await c.post("/plans/generate")
        assert gen_resp.status_code == 200
        first_plan_id = gen_resp.json()[0]["plan_id"]

        payload = {"plan_id": first_plan_id}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 200
        assert resp.json()["simulation_result"]["plan_id"] == first_plan_id

    @pytest.mark.asyncio
    async def test_plan_is_authoritative_over_plan_id(self, loaded_v2):
        """When both plan and plan_id are supplied, plan is used (not regenerated from plan_id).

        Supply a plan_id that is a valid regeneratable name, but also supply a plan
        with a different packet (injected ID).  The simulation result must contain the
        injected packet — proving plan, not plan_id lookup, was used.
        """
        injected_id = "D3-AUTHORITY-TEST"
        supplied_plan = _make_plan("baseline", [injected_id])
        payload = {
            "plan_id": "baseline",
            "plan": supplied_plan.model_dump(mode="json"),
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        sim = body["simulation_result"]
        all_ids = (
            sim["delivered_packets"]
            + sim["deferred_packets"]
            + sim["failed_packets"]
        )
        assert injected_id in all_ids, (
            "The supplied plan must be authoritative over the plan_id fallback."
        )

    @pytest.mark.asyncio
    async def test_approve_custom_unchanged(self, loaded_v2):
        """/approve/custom must behave identically to before (regression)."""
        plan = _make_plan("operator-override", ["PKT-CUSTOM-A", "PKT-CUSTOM-B"])
        payload = {
            "plan": plan.model_dump(mode="json"),
            "operator_notes": "override test",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve/custom", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["simulation_result"]["plan_id"] == "operator-override"

    @pytest.mark.asyncio
    async def test_approve_no_scenario_returns_503(self):
        """P0-2 backend contract: /approve without a loaded scenario returns 503."""
        # Ensure no scenario is loaded (autouse fixture resets state)
        assert app_state.active_scenario is None
        payload = {"plan_id": "baseline"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/approve", json=payload)
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# D3-C: RankedProduct.description backward compatibility and forwarding
# ---------------------------------------------------------------------------


class TestRankedProductDescription:
    """D3-C: RankedProduct.description is optional, defaults to "", and is forwarded."""

    def test_ranked_product_description_defaults_to_empty(self):
        """RankedProduct without description must parse successfully with default ''."""
        rp = RankedProduct(
            product_id="DP-001",
            priority=1,
            reason="Test reason",
        )
        assert rp.description == ""

    def test_ranked_product_accepts_nonempty_description(self):
        """RankedProduct stores a non-empty description correctly."""
        rp = RankedProduct(
            product_id="DP-001",
            priority=1,
            reason="Test reason",
            description="Thruster-2 chamber pressure diagnostic",
        )
        assert rp.description == "Thruster-2 chamber pressure diagnostic"

    def test_ranked_product_round_trips_with_description(self):
        """RankedProduct with description survives model_dump → model_validate."""
        rp = RankedProduct(
            product_id="DP-002",
            priority=2,
            reason="reason",
            description="Solar panel output",
        )
        dumped = rp.model_dump(mode="json")
        restored = RankedProduct.model_validate(dumped)
        assert restored.description == "Solar panel output"

    def test_old_dict_without_description_parses_successfully(self):
        """Existing serialized responses without 'description' must still parse."""
        raw = {
            "product_id": "DP-003",
            "priority": 3,
            "reason": "legacy reason",
            "factors": [],
            "anomaly_ids": [],
            "subsystem": "power",
            "confidence": None,
        }
        rp = RankedProduct.model_validate(raw)
        assert rp.description == ""

    def test_local_provider_forwards_description(self):
        """LocalRuleBasedProvider.prioritize_candidates() populates description from CandidateSummary."""
        candidates = [
            CandidateSummary(
                product_id="DP-PROP-001",
                product_type="diagnostic",
                description="Thruster-2 chamber pressure diagnostic",
                subsystem="propulsion",
                size_bits=81920,
                criticality=0.9,
                mission_relevance=0.85,
                scientific_value=0.5,
                deadline_s=120.0,
                age_s=10.0,
                anomaly_id="ANOM-001",
            ),
            CandidateSummary(
                product_id="DP-PWR-001",
                product_type="telemetry",
                description="Solar panel power output telemetry",
                subsystem="power",
                size_bits=40960,
                criticality=0.6,
                mission_relevance=0.7,
                scientific_value=0.3,
                deadline_s=300.0,
                age_s=60.0,
            ),
        ]
        link_state = _make_link_state()
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates,
            link_state,
            _MISSION_STATE,
            anomalies=None,
        )
        assert result.ranked_products, "Expected at least one ranked product"
        id_to_desc = {rp.product_id: rp.description for rp in result.ranked_products}
        assert id_to_desc["DP-PROP-001"] == "Thruster-2 chamber pressure diagnostic"
        assert id_to_desc["DP-PWR-001"] == "Solar panel power output telemetry"

    def test_local_provider_empty_description_when_not_set(self):
        """LocalRuleBasedProvider produces description='' when CandidateSummary has no description."""
        candidates = [
            CandidateSummary(
                product_id="DP-BARE",
                product_type="telemetry",
                # description defaults to ""
                subsystem="attitude_control",
                size_bits=40960,
                criticality=0.5,
                mission_relevance=0.5,
                scientific_value=0.4,
                deadline_s=400.0,
                age_s=100.0,
            ),
        ]
        link_state = _make_link_state()
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(candidates, link_state, _MISSION_STATE)
        assert len(result.ranked_products) == 1
        assert result.ranked_products[0].description == ""

    def test_parse_prioritization_response_with_candidates_populates_description(self):
        """parse_prioritization_response() populates description from candidates lookup."""
        import json as _json

        candidates = [
            CandidateSummary(
                product_id="DP-A",
                product_type="telemetry",
                description="Propulsion valve telemetry",
                subsystem="propulsion",
                size_bits=81920,
                criticality=0.8,
                mission_relevance=0.9,
                scientific_value=0.5,
                deadline_s=120.0,
                age_s=5.0,
            ),
        ]
        valid_ids = {"DP-A"}
        raw_json = _json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-A",
                    "priority": 1,
                    "reason": "High criticality anomaly product",
                    "factors": [],
                    "anomaly_ids": [],
                    "subsystem": "propulsion",
                    "confidence": 0.9,
                }
            ],
            "overall_reasoning": "Test reasoning",
            "confidence": 0.85,
            "decision_factors": [],
        })

        result = parse_prioritization_response(raw_json, valid_ids, candidates)
        assert len(result.ranked_products) == 1
        assert result.ranked_products[0].description == "Propulsion valve telemetry"

    def test_parse_prioritization_response_without_candidates_uses_empty_description(self):
        """When candidates not supplied, description defaults to ''."""
        import json as _json

        valid_ids = {"DP-B"}
        raw_json = _json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-B",
                    "priority": 1,
                    "reason": "reason",
                    "factors": [],
                    "anomaly_ids": [],
                    "subsystem": "power",
                    "confidence": None,
                }
            ],
            "overall_reasoning": "reasoning",
            "confidence": 0.7,
            "decision_factors": [],
        })

        result = parse_prioritization_response(raw_json, valid_ids)
        assert result.ranked_products[0].description == ""

    def test_data_product_description_flows_through_full_pipeline(self):
        """DataProduct.description → CandidateSummary → RankedProduct (full chain)."""
        dp = DataProduct(
            product_id="DP-CHAIN-001",
            product_type="diagnostic",
            description="Reaction wheel bearing wear diagnostic",
            subsystem="attitude_control",
            size_bits=81920,
            criticality=0.85,
            mission_relevance=0.9,
            scientific_value=0.6,
            deadline_s=200.0,
            age_s=30.0,
            delivery_requirement="required",
            retry_cost=0.1,
        )
        # CandidatePrioritizer preserves description via _summarise
        prioritizer = CandidatePrioritizer(max_candidates=5)
        link_state = _make_link_state()
        summaries = prioritizer.select([dp], remaining_window_s=300.0)
        assert len(summaries) == 1
        assert summaries[0].description == "Reaction wheel bearing wear diagnostic"

        # LocalRuleBasedProvider forwards it into RankedProduct
        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(summaries, link_state, _MISSION_STATE)
        assert len(result.ranked_products) == 1
        assert result.ranked_products[0].description == "Reaction wheel bearing wear diagnostic"
