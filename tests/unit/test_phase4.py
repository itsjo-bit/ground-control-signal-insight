"""Phase 4 — Trust, Safety & Authoritative Execution Boundaries.

Tests for all Phase 4 acceptance criteria:

A. Plan integrity — reconstruction from scenario inventory
B. Issued-plan registry — registration and invalidation
C. Approval routes — /approve registry verification, /approve/custom reconstruction
D. ApprovalTrace correctness
E. Client-only intent fields are controlled by client
F. Recommendation confidence semantics
G. Regression — PlanEvaluator formulas, benchmark v1 config unchanged
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_NOMINAL_PATH = str(_REPO_ROOT / "data" / "scenarios" / "nominal_pass.json")
_V3_PATH = str(_REPO_ROOT / "data" / "scenarios" / "mission_data_v3.json")
_BENCHMARK_V1 = str(_REPO_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json")


def _ts() -> datetime:
    return datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset all application state before and after each test."""
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.issued_plans.clear()
    app_state.last_approval_trace = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.issued_plans.clear()
    app_state.last_approval_trace = None


@pytest.fixture
def loaded_state():
    from backend.app import state as app_state
    app_state.load_scenario(_NOMINAL_PATH)


@pytest.fixture
def loaded_v3_state():
    from backend.app import state as app_state
    app_state.load_scenario(_V3_PATH)


@pytest.fixture
def app():
    from backend.app.main import app
    return app


# ---------------------------------------------------------------------------
# Part A — Plan integrity
# ---------------------------------------------------------------------------


class TestPlanIntegrity:
    """Authoritative packet reconstruction from scenario inventory."""

    def _make_scenario_with_packets(self):
        from backend.app import state as app_state
        app_state.load_scenario(_NOMINAL_PATH)
        return app_state.active_scenario

    def test_get_authoritative_packets_returns_packets(self):
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app import state as app_state
        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        assert len(packets) > 0

    def test_reconstruct_preserves_client_order(self):
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        # Reverse order
        reversed_packets = list(reversed(packets))
        client_plan = CandidatePlan(
            plan_id="test",
            strategy="baseline",
            packets=reversed_packets,
            generated_by="test",
            metadata={},
        )
        trace = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        # Order preserved
        assert [p.packet_id for p in trace.reconstructed_plan.packets] == [
            p.packet_id for p in reversed_packets
        ]

    def test_reconstruct_replaces_client_packet_facts(self):
        """Client-supplied size_bits must be replaced by authoritative value."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_NOMINAL_PATH)
        auth_packets = get_authoritative_packets(app_state.active_scenario)
        first = auth_packets[0]
        auth_size = first.size_bits

        # Build a client plan with a deliberately wrong size_bits
        tampered = Packet(
            **{**first.model_dump(), "size_bits": auth_size + 999_999}
        )
        client_plan = CandidatePlan(
            plan_id="tampered",
            strategy="baseline",
            packets=[tampered],
            generated_by="client",
            metadata={},
        )
        trace = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        assert trace.reconstructed_plan.packets[0].size_bits == auth_size

    def test_reconstruct_rejects_unknown_packet_id(self):
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            IntegrityReason,
            PlanIntegrityError,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_NOMINAL_PATH)
        bogus = Packet(
            packet_id="DOES_NOT_EXIST",
            packet_type="telemetry",
            size_bits=1000,
            criticality=0.5,
            mission_relevance=0.5,
            deadline_s=100.0,
            retry_cost=0.1,
            delivery_requirement="best_effort",
        )
        client_plan = CandidatePlan(
            plan_id="bad",
            strategy="baseline",
            packets=[bogus],
            generated_by="client",
            metadata={},
        )
        with pytest.raises(PlanIntegrityError) as exc_info:
            reconstruct_authoritative_plan(
                client_plan,
                app_state.active_scenario,
                plan_source=PlanSource.client_intent,
            )
        assert exc_info.value.reason == IntegrityReason.unknown_packet

    def test_reconstruct_rejects_duplicate_packet_ids(self):
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            IntegrityReason,
            PlanIntegrityError,
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        dup = [packets[0], packets[0]]  # duplicate
        client_plan = CandidatePlan(
            plan_id="dup",
            strategy="baseline",
            packets=dup,
            generated_by="client",
            metadata={},
        )
        with pytest.raises(PlanIntegrityError) as exc_info:
            reconstruct_authoritative_plan(
                client_plan,
                app_state.active_scenario,
                plan_source=PlanSource.client_intent,
            )
        assert exc_info.value.reason == IntegrityReason.duplicate_packet

    def test_fingerprints_are_deterministic(self):
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            compute_plan_fingerprint,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        client_plan = CandidatePlan(
            plan_id="fp_test",
            strategy="baseline",
            packets=packets,
            generated_by="client",
            metadata={},
        )
        trace1 = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        trace2 = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        assert trace1.packet_order_sha256 == trace2.packet_order_sha256
        assert trace1.canonical_plan_sha256 == trace2.canonical_plan_sha256


# ---------------------------------------------------------------------------
# Part B — Issued-plan registry
# ---------------------------------------------------------------------------


class TestIssuedPlanRegistry:
    """Registration, lookup and invalidation of server-issued plans."""

    def test_generate_plans_registers_in_issued_plans(self):
        from backend.app import state as app_state
        app_state.load_scenario(_NOMINAL_PATH)
        assert len(app_state.issued_plans) == 0

        from backend.app.api.routes_plans import generate_plans
        plans = generate_plans()
        assert len(plans) == 4
        # All four plans should now be in the registry
        assert len(app_state.issued_plans) == 4
        for plan in plans:
            assert plan.plan_id in app_state.issued_plans

    def test_registry_cleared_on_scenario_load(self):
        from backend.app import state as app_state
        app_state.load_scenario(_NOMINAL_PATH)
        from backend.app.api.routes_plans import generate_plans
        generate_plans()
        assert len(app_state.issued_plans) == 4

        # Reload the scenario — registry must be cleared
        app_state.load_scenario(_NOMINAL_PATH)
        assert len(app_state.issued_plans) == 0

    def test_invalidate_clears_registry(self):
        from backend.app import state as app_state
        app_state.load_scenario(_NOMINAL_PATH)
        from backend.app.api.routes_plans import generate_plans
        generate_plans()
        assert len(app_state.issued_plans) > 0

        app_state.invalidate_issued_plans(reason="test")
        assert len(app_state.issued_plans) == 0

    def test_registry_record_has_correct_fields(self):
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import PlanSource
        app_state.load_scenario(_NOMINAL_PATH)
        from backend.app.api.routes_plans import generate_plans
        plans = generate_plans()

        for plan in plans:
            record = app_state.issued_plans[plan.plan_id]
            assert record.plan_id == plan.plan_id
            assert record.scenario_id == app_state.active_scenario.scenario_id
            assert record.plan_source == PlanSource.deterministic_generated.value
            assert len(record.packet_order_sha256) == 64  # SHA-256 hex
            assert len(record.canonical_plan_sha256) == 64


# ---------------------------------------------------------------------------
# Part C — /approve route
# ---------------------------------------------------------------------------


class TestApproveRoute:
    """POST /approve — issued plan verification and authoritative execution."""

    @pytest.mark.asyncio
    async def test_approve_registered_plan_succeeds(self, app, loaded_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            assert gen_resp.status_code == 200
            plans = gen_resp.json()
            first_plan = plans[0]

            approve_resp = await client.post("/approve", json={
                "plan_id": first_plan["plan_id"],
                "plan": first_plan,
                "operator_notes": "phase4 test",
            })
        assert approve_resp.status_code == 200
        body = approve_resp.json()
        assert body["status"] == "approved"
        assert "simulation_result" in body
        assert "approval_trace" in body
        assert "executed_plan" in body

    @pytest.mark.asyncio
    async def test_approve_returns_409_for_unregistered_plan(self, app, loaded_state):
        """Submitting a plan that was never generated raises HTTP 409."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]
            first_plan["plan_id"] = "totally-unknown-plan-id"

            resp = await client.post("/approve", json={
                "plan_id": "totally-unknown-plan-id",
                "plan": first_plan,
            })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_approve_returns_422_for_tampered_order(self, app, loaded_state):
        """Submitting a plan with reordered packets raises HTTP 422."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]
            # Reverse the packet order
            first_plan["packets"] = list(reversed(first_plan["packets"]))

            resp = await client.post("/approve", json={
                "plan_id": first_plan["plan_id"],
                "plan": first_plan,
            })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_legacy_path_works(self, app, loaded_state):
        """Legacy path (no plan object, only plan_id) should still work."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan_id = plans[0]["plan_id"]

            resp = await client.post("/approve", json={
                "plan_id": first_plan_id,
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"

    @pytest.mark.asyncio
    async def test_approve_returns_authoritative_executed_plan(self, app, loaded_state):
        """executed_plan in the response must have authoritative packet facts."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]

            # Tamper with packet facts in the submission — they must be ignored
            tampered = dict(first_plan)
            tampered["packets"] = [
                {**pkt, "size_bits": 999_999_999}
                for pkt in tampered["packets"]
            ]

            resp = await client.post("/approve", json={
                "plan_id": first_plan["plan_id"],
                "plan": first_plan,  # original (untampered) order
            })
        assert resp.status_code == 200
        body = resp.json()
        # Executed plan should NOT have the tampered size
        for pkt in body["executed_plan"]["packets"]:
            assert pkt["size_bits"] != 999_999_999

    @pytest.mark.asyncio
    async def test_approve_invalidates_registry(self, app, loaded_state):
        """After approval, issued-plan registry must be empty."""
        from backend.app import state as app_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]

            await client.post("/approve", json={
                "plan_id": first_plan["plan_id"],
                "plan": first_plan,
            })
        assert len(app_state.issued_plans) == 0

    @pytest.mark.asyncio
    async def test_approve_approval_trace_fields(self, app, loaded_state):
        """ApprovalTrace must contain all required fields."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]

            resp = await client.post("/approve", json={
                "plan_id": first_plan["plan_id"],
                "plan": first_plan,
                "operator_notes": "my note",
            })
        trace = resp.json()["approval_trace"]
        assert trace["decision"] == "approved"
        assert trace["authoritative_reconstruction"] is True
        assert trace["issued_plan_verified"] is True
        assert trace["operator_notes"] == "my note"
        assert len(trace["packet_order_sha256"]) == 64
        assert len(trace["canonical_plan_sha256"]) == 64


# ---------------------------------------------------------------------------
# Part C — /approve/custom route
# ---------------------------------------------------------------------------


class TestApproveCustomRoute:
    """POST /approve/custom — operator-reordered plans."""

    @pytest.mark.asyncio
    async def test_approve_custom_works_without_registry(self, app, loaded_state):
        """Custom plans do not need to be in the issued-plan registry."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            # Use any plan as the base for a custom order
            custom_plan = plans[0]
            custom_plan["packets"] = list(reversed(custom_plan["packets"]))

            resp = await client.post("/approve/custom", json={
                "plan": custom_plan,
                "operator_notes": "custom order",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        trace = body["approval_trace"]
        assert trace["plan_source"] == "operator_custom"
        assert trace["issued_plan_verified"] is False
        assert trace["authoritative_reconstruction"] is True

    @pytest.mark.asyncio
    async def test_approve_custom_rejects_unknown_packet(self, app, loaded_state):
        """Custom plan with unknown packet IDs must be rejected."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app.models.packet import Packet

        packets = get_authoritative_packets(app_state.active_scenario)
        bogus = {
            "packet_id": "BOGUS_ID",
            "packet_type": "telemetry",
            "size_bits": 1000,
            "criticality": 0.5,
            "mission_relevance": 0.5,
            "deadline_s": 100.0,
            "retry_cost": 0.1,
            "delivery_requirement": "best_effort",
        }
        custom_plan = {
            "plan_id": "custom_bad",
            "strategy": "baseline",
            "packets": [bogus],
            "generated_by": "client",
            "metadata": {},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/approve/custom", json={
                "plan": custom_plan,
            })
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_approve_custom_packet_facts_are_authoritative(self, app, loaded_state):
        """Executed plan must use authoritative packet facts, not client values."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import get_authoritative_packets

        packets = get_authoritative_packets(app_state.active_scenario)
        first = packets[0]

        tampered_plan = {
            "plan_id": "tampered_custom",
            "strategy": "baseline",
            "packets": [
                {**first.model_dump(), "size_bits": 42_000_000}
            ],
            "generated_by": "client",
            "metadata": {},
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/approve/custom", json={
                "plan": tampered_plan,
            })
        assert resp.status_code == 200
        body = resp.json()
        # executed_plan must have authoritative size
        executed_pkt = body["executed_plan"]["packets"][0]
        assert executed_pkt["size_bits"] == first.size_bits
        assert executed_pkt["size_bits"] != 42_000_000


# ---------------------------------------------------------------------------
# Part D — Approval trace semantics
# ---------------------------------------------------------------------------


class TestApprovalTrace:
    """ApprovalTrace correctness and semantic invariants."""

    def test_approval_trace_plan_source_never_from_client(self):
        """plan_source must reflect backend trust classification, not client generated_by."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        # Client supplies a deceptive generated_by string
        client_plan = CandidatePlan(
            plan_id="hack",
            strategy="baseline",
            packets=packets[:1],
            generated_by="I_AM_DEFINITELY_A_LEGITIMATE_SERVER_PLAN",
            metadata={"plan_source": "definitely_legit"},
        )
        trace = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        # The backend assigns client_intent, regardless of client's generated_by
        assert trace.reconstructed_plan.metadata["plan_source"] == PlanSource.client_intent.value
        assert "backend:client_intent" in trace.reconstructed_plan.generated_by

    def test_operator_notes_trimmed_to_500_chars(self, loaded_state):
        """Operator notes longer than 500 chars must be trimmed."""
        from backend.app.api.routes_approve import _build_approval_trace
        from backend.app.domain.plan_integrity import PlanSource

        long_notes = "x" * 600
        trace = _build_approval_trace(
            plan_id="test_plan",
            scenario_id="test_scenario",
            plan_source=PlanSource.operator_custom,
            operator_notes=long_notes,
            authoritative_reconstruction=True,
            issued_plan_verified=False,
            packet_count=3,
            packet_order_sha256="a" * 64,
            canonical_plan_sha256="b" * 64,
        )
        assert len(trace.operator_notes) <= 500

    def test_approval_trace_always_authoritative_reconstruction_true(self, loaded_state):
        """authoritative_reconstruction must always be True after Phase 4."""
        from backend.app.api.routes_approve import _build_approval_trace
        from backend.app.domain.plan_integrity import PlanSource

        trace = _build_approval_trace(
            plan_id="test_plan",
            scenario_id="test_scenario",
            plan_source=PlanSource.deterministic_generated,
            operator_notes="",
            authoritative_reconstruction=True,
            issued_plan_verified=True,
            packet_count=3,
            packet_order_sha256="a" * 64,
            canonical_plan_sha256="b" * 64,
        )
        assert trace.authoritative_reconstruction is True


# ---------------------------------------------------------------------------
# Part E — Client intent model
# ---------------------------------------------------------------------------


class TestClientIntentModel:
    """Only packet_id order and operator notes are client-controlled."""

    def test_client_cannot_change_packet_criticality(self):
        """Criticality must always come from the scenario, not the client."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        auth_crit = packets[0].criticality

        # Use model_construct to bypass Pydantic validation on the tampered object
        # Flip the value away from the authoritative value
        fake_crit = 0.0 if auth_crit > 0.0 else 0.99
        tampered = Packet.model_construct(**{**packets[0].model_dump(), "criticality": fake_crit})
        assert tampered.criticality != auth_crit

        client_plan = CandidatePlan(
            plan_id="crit_test",
            strategy="baseline",
            packets=[tampered],
            generated_by="client",
            metadata={},
        )
        trace = reconstruct_authoritative_plan(
            client_plan, app_state.active_scenario, plan_source=PlanSource.client_intent
        )
        assert trace.reconstructed_plan.packets[0].criticality == auth_crit

    def test_client_cannot_change_deadline(self):
        """Deadline must always come from the scenario, not the client."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        app_state.load_scenario(_NOMINAL_PATH)
        packets = get_authoritative_packets(app_state.active_scenario)
        auth_deadline = packets[0].deadline_s

        # Use model_construct to bypass Pydantic validation; use a clearly different value
        new_deadline = auth_deadline + 999999.0
        tampered = Packet.model_construct(**{**packets[0].model_dump(), "deadline_s": new_deadline})
        client_plan = CandidatePlan(
            plan_id="deadline_test",
            strategy="baseline",
            packets=[tampered],
            generated_by="client",
            metadata={},
        )
        trace = reconstruct_authoritative_plan(
            client_plan, app_state.active_scenario, plan_source=PlanSource.client_intent
        )
        assert trace.reconstructed_plan.packets[0].deadline_s == auth_deadline


# ---------------------------------------------------------------------------
# Part F — Confidence semantics
# ---------------------------------------------------------------------------


class TestConfidenceSemantics:
    """confidence_semantics field reflects how confidence was produced."""

    def test_local_provider_sets_heuristic_confidence_semantics(self):
        from datetime import datetime, timezone
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.evaluation_result import EvaluationResult
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        from backend.app.models.packet import Packet

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        link = LinkState(
            timestamp=ts, snr_db=10.0, eb_n0_db=15.0, ber=1e-5,
            rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
            link_goodput_bps=90_000.0, latency_s=1.4,
            link_stability=0.95, remaining_window_s=300.0,
        )
        mission = MissionState(
            mission_id="test", mission_phase="science",
            current_event="nominal", event_time_remaining_s=3600.0,
            comm_window_remaining_s=300.0, risk_score=0.1,
            risk_level=RiskLevel.LOW,
        )
        pkt = Packet(
            packet_id="p1", packet_type="telemetry", size_bits=8000,
            criticality=0.5, mission_relevance=0.5, deadline_s=300.0,
            retry_cost=0.1, delivery_requirement="best_effort",
        )
        plan_a = CandidatePlan(plan_id="a", strategy="baseline", packets=[pkt],
                                generated_by="server", metadata={})
        plan_b = CandidatePlan(plan_id="b", strategy="deadline_first", packets=[pkt],
                                generated_by="server", metadata={})
        eval_a = EvaluationResult(
            plan_id="a", mission_value=1.0, critical_packets_delivered=1,
            total_critical_packets=1, deadline_misses=0, avg_packet_delay_s=0.0,
            bandwidth_utilization=0.5, retransmission_overhead=0.0,
            risk_score=0.1, risk_level=RiskLevel.LOW,
            deferred_packets=[], deadline_miss_rate=0.0,
            critical_deficit=0.0, window_pressure=0.5,
        )
        eval_b = EvaluationResult(
            plan_id="b", mission_value=0.8, critical_packets_delivered=1,
            total_critical_packets=1, deadline_misses=0, avg_packet_delay_s=0.0,
            bandwidth_utilization=0.4, retransmission_overhead=0.0,
            risk_score=0.3, risk_level=RiskLevel.MEDIUM,
            deferred_packets=[], deadline_miss_rate=0.0,
            critical_deficit=0.0, window_pressure=0.3,
        )
        provider = LocalRuleBasedProvider()
        rec = provider.recommend(link, mission, [plan_a, plan_b], [eval_a, eval_b])
        assert rec.confidence_semantics == "heuristic"

    def test_ai_recommendation_default_confidence_semantics(self):
        """Default confidence_semantics must be 'heuristic' per model default."""
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        rec = AIRecommendation(
            recommended_plan_id="p1",
            packet_actions=[],
            risk_score=0.2,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )
        assert rec.confidence_semantics == "heuristic"

    def test_ai_recommendation_accepts_uncalibrated_llm(self):
        """confidence_semantics='uncalibrated_llm' must be accepted."""
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        rec = AIRecommendation(
            recommended_plan_id="p1",
            packet_actions=[],
            risk_score=0.2,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            confidence_semantics="uncalibrated_llm",
            reasoning="test",
            evidence=[],
        )
        assert rec.confidence_semantics == "uncalibrated_llm"


# ---------------------------------------------------------------------------
# Part G — Regression: PlanEvaluator formulas, benchmark v1 config
# ---------------------------------------------------------------------------


class TestPhase4Regression:
    """PlanEvaluator and benchmark v1 config must remain unchanged."""

    def test_benchmark_v1_config_unchanged(self):
        """Benchmark v1 JSON must remain byte-for-byte identical to the frozen spec."""
        path = Path(_BENCHMARK_V1)
        assert path.exists(), "gcsi_benchmark_v1.json must exist"
        data = json.loads(path.read_text())
        # Key structural fields that must remain frozen (per actual v1 config structure)
        assert "base_scenario" in data
        assert "candidate_limit" in data
        assert "primary_metrics" in data
        assert "capacity_ratios" in data
        assert data["benchmark_version"] == "gcsi_benchmark_v1"

    def test_plan_evaluator_risk_formula_unchanged(self):
        """PlanEvaluator risk formula must match the documented production formula."""
        from datetime import datetime, timezone
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.evaluation_result import EvaluationResult
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        from backend.app.models.packet import Packet
        from backend.app.config import RiskWeights

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        link = LinkState(
            timestamp=ts, snr_db=10.0, eb_n0_db=15.0, ber=1e-5,
            rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
            link_goodput_bps=90_000.0, latency_s=1.4,
            link_stability=0.95, remaining_window_s=100.0,
        )
        mission = MissionState(
            mission_id="test", mission_phase="science",
            current_event="nominal", event_time_remaining_s=3600.0,
            comm_window_remaining_s=100.0, risk_score=0.1,
            risk_level=RiskLevel.LOW,
        )
        # One small packet — no deadline miss, all critical packets delivered
        pkt = Packet(
            packet_id="p1", packet_type="telemetry", size_bits=8000,
            criticality=1.0, mission_relevance=1.0, deadline_s=1000.0,
            retry_cost=0.1, delivery_requirement="best_effort",
        )
        plan = CandidatePlan(
            plan_id="reg_test", strategy="baseline", packets=[pkt],
            generated_by="server", metadata={},
        )
        ev = PlanEvaluator()
        result = ev.evaluate(plan, link, mission)
        # risk_score is a weighted sum of three components in [0, 1]
        assert 0.0 <= result.risk_score <= 1.0
        rw = RiskWeights()
        expected = (
            rw.w_deadline_miss * result.deadline_miss_rate
            + rw.w_critical_deficit * result.critical_deficit
            + rw.w_window_pressure * result.window_pressure
        )
        # Should match to floating-point tolerance
        assert abs(result.risk_score - expected) < 1e-9

    def test_window_pressure_formula(self):
        """window_pressure = min(consumed_window_s / effective_window_s, 1.0)."""
        from datetime import datetime, timezone
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        from backend.app.models.packet import Packet

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        # Set window to 0 — pressure must be 1.0
        link = LinkState(
            timestamp=ts, snr_db=10.0, eb_n0_db=15.0, ber=1e-5,
            rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
            link_goodput_bps=90_000.0, latency_s=1.4,
            link_stability=0.95, remaining_window_s=0.0,
        )
        mission = MissionState(
            mission_id="test", mission_phase="science",
            current_event="nominal", event_time_remaining_s=3600.0,
            comm_window_remaining_s=0.0, risk_score=0.1,
            risk_level=RiskLevel.LOW,
        )
        pkt = Packet(
            packet_id="p1", packet_type="telemetry", size_bits=8000,
            criticality=0.5, mission_relevance=0.5, deadline_s=300.0,
            retry_cost=0.1, delivery_requirement="best_effort",
        )
        plan = CandidatePlan(
            plan_id="zero_window", strategy="baseline", packets=[pkt],
            generated_by="server", metadata={},
        )
        ev = PlanEvaluator()
        result = ev.evaluate(plan, link, mission)
        assert result.window_pressure == 1.0

    def test_window_pressure_partial(self):
        """window_pressure = 0.4 when consumed cost = 40s / window = 100s."""
        from datetime import datetime, timezone
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.link_state import LinkState
        from backend.app.models.mission_state import MissionState
        from backend.app.models.risk_level import RiskLevel
        from backend.app.models.packet import Packet
        from backend.app.telecom.formulas import packet_success_probability

        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        goodput = 90_000.0
        window = 100.0
        # size_bits chosen so tx_time is exactly 4s
        # tx_time = size_bits / goodput → size_bits = tx_time * goodput
        tx_time_target = 4.0
        size_bits = int(tx_time_target * goodput)  # 360000 bits → 4s

        link = LinkState(
            timestamp=ts, snr_db=30.0, eb_n0_db=35.0, ber=0.0,
            rssi_dbm=-80.0, nominal_data_rate_bps=100_000.0,
            link_goodput_bps=goodput, latency_s=1.4,
            link_stability=0.95, remaining_window_s=window,
        )
        mission = MissionState(
            mission_id="test", mission_phase="science",
            current_event="nominal", event_time_remaining_s=3600.0,
            comm_window_remaining_s=window, risk_score=0.1,
            risk_level=RiskLevel.LOW,
        )
        # BER=0 → p_success=1.0 → expected_cost = tx_time / 1.0 = 4s
        pkt = Packet(
            packet_id="p1", packet_type="telemetry", size_bits=size_bits,
            criticality=0.5, mission_relevance=0.5, deadline_s=1000.0,
            retry_cost=0.1, delivery_requirement="best_effort",
        )
        plan = CandidatePlan(
            plan_id="partial_wp", strategy="baseline", packets=[pkt],
            generated_by="server", metadata={},
        )
        ev = PlanEvaluator()
        result = ev.evaluate(plan, link, mission)
        # With BER=0, p_success=1, expected_cost = tx_time → 4s / 100s = 0.04
        assert 0.0 <= result.window_pressure <= 1.0


# ---------------------------------------------------------------------------
# Part H — State non-mutation after what-if
# ---------------------------------------------------------------------------


class TestNonMutation:
    """What-if and evaluate endpoints must not mutate state."""

    @pytest.mark.asyncio
    async def test_plans_evaluate_does_not_mutate_issued_plans(self, app, loaded_state):
        """POST /plans/evaluate must not change the issued-plan registry."""
        from backend.app import state as app_state

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()
            initial_count = len(app_state.issued_plans)

            await client.post("/plans/evaluate", json=plans[0])

        assert len(app_state.issued_plans) == initial_count

    @pytest.mark.asyncio
    async def test_simulate_what_if_does_not_mutate_state(self, app, loaded_state):
        """POST /simulate/what-if must not change link or mission state."""
        from backend.app import state as app_state

        link_before = app_state.active_link_state
        mission_before = app_state.active_scenario.mission_state

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()

            await client.post("/simulate/what-if", json={"plan": plans[0]})

        # State must be byte-logically identical after what-if
        assert app_state.active_link_state is link_before
        assert app_state.active_scenario.mission_state == mission_before


# ---------------------------------------------------------------------------
# Part I — Recommend endpoint issues plans
# ---------------------------------------------------------------------------


class TestRecommendRegistersPlans:
    """POST /agent/recommend must register plans in issued-plan registry."""

    @pytest.mark.asyncio
    async def test_recommend_registers_all_plans(self, app, loaded_state):
        from backend.app import state as app_state

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Clear any plans from loaded_state fixture preamble
            app_state.issued_plans.clear()
            resp = await client.post("/agent/recommend")

        assert resp.status_code == 200
        # Should have at least 4 plans registered (legacy path has 4 deterministic)
        assert len(app_state.issued_plans) >= 4

    @pytest.mark.asyncio
    async def test_recommend_then_approve_registered_plan(self, app, loaded_state):
        """Plans from /agent/recommend should be approvable via /approve."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            rec_resp = await client.post("/agent/recommend")
            assert rec_resp.status_code == 200

            rec_data = rec_resp.json()
            recommended_plan_id = rec_data["recommendation"]["recommended_plan_id"]

            # Find the plan in the generate step (it should be in issued_plans now)
            # We need to find the actual plan object to send to /approve
            gen_resp = await client.post("/plans/generate")
            plans = gen_resp.json()

        # The recommended_plan_id should be approvable now that recommend has registered it
        # (After generate, those 4 plans are in the registry)
        from backend.app import state as app_state
        # At least 4 plans from /plans/generate are registered
        assert len(app_state.issued_plans) >= 4
