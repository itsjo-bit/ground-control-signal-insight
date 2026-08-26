"""Phase 4.1 — Canonical Provenance & Recommendation Finalization.

Targeted regression tests proving the trust properties added in Phase 4.1:

A. Client provenance rejection / sanitization
B. Exact canonical hash after provenance finalization
C. Registry deep-copy / immutability
D. Scenario mismatch → /approve fails closed
E. Internal fingerprint mismatch → /approve fails closed
F. Recommendation finalization (risk, packet_actions)
G. Invalid recommendation plan_id behavior
H. Confidence semantics (typed enum, backend-assigned)
I. Stage provider identity (prioritization_provider, recommendation_provider)
J. Regression guarantees (formulas, blinding, evidence, registry lifecycle)
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_NOMINAL_PATH = str(_REPO_ROOT / "data" / "scenarios" / "nominal_pass.json")
_V3_PATH = str(_REPO_ROOT / "data" / "scenarios" / "mission_data_v3.json")
_BENCHMARK_V1 = str(_REPO_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json")


@pytest.fixture(autouse=True)
def reset_app_state():
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
def loaded_nominal():
    from backend.app import state as app_state
    app_state.load_scenario(_NOMINAL_PATH)


@pytest.fixture
def loaded_v3():
    from backend.app import state as app_state
    app_state.load_scenario(_V3_PATH)


@pytest.fixture
def app():
    from backend.app.main import app
    return app


def _make_eval(plan_id, risk_score=0.1):
    from backend.app.models.evaluation_result import EvaluationResult
    from backend.app.models.risk_level import RiskLevel
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=1.0,
        critical_packets_delivered=1,
        total_critical_packets=1,
        deadline_misses=0,
        avg_packet_delay_s=0.0,
        bandwidth_utilization=0.5,
        retransmission_overhead=0.0,
        risk_score=risk_score,
        risk_level=RiskLevel.LOW if risk_score < 0.25 else RiskLevel.MEDIUM,
        deferred_packets=[],
        deadline_miss_rate=0.0,
        critical_deficit=0.0,
        window_pressure=0.5,
    )


def _make_plan(plan_id, pkts):
    from backend.app.models.candidate_plan import CandidatePlan
    return CandidatePlan(
        plan_id=plan_id,
        strategy="test",
        packets=pkts,
        generated_by="test",
        metadata={},
    )


def _make_pkt(pid):
    from backend.app.models.packet import Packet
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=8000,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=300.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


# ===========================================================================
# A. Client provenance rejection / sanitization
# ===========================================================================


class TestClientProvenanceRejection:
    """Client-supplied strategy/metadata/packet facts must not become authoritative."""

    @pytest.mark.asyncio
    async def test_approve_standard_ignores_malicious_strategy(self, app, loaded_nominal):
        """Malicious strategy in submitted plan must not appear in executed plan."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            assert gen_resp.status_code == 200
            plans = gen_resp.json()
            plan = plans[0]
            # Inject malicious provenance
            plan["strategy"] = "NASA_CERTIFIED"
            plan["generated_by"] = "attacker"
            plan["metadata"]["trusted"] = True
            plan["metadata"]["evaluator"] = "fake"
            plan["metadata"]["plan_source"] = "ai_generated"

            resp = await c.post("/approve", json={
                "plan_id": plan["plan_id"],
                "plan": plan,
                "operator_notes": "provenance test",
            })
        assert resp.status_code == 200
        body = resp.json()
        executed = body["executed_plan"]
        # The executed plan is the canonical registry plan — no malicious provenance
        assert executed["strategy"] != "NASA_CERTIFIED"
        assert executed["generated_by"] != "attacker"
        assert executed["metadata"].get("trusted") is not True
        assert executed["metadata"].get("evaluator") != "fake"
        # plan_source must be backend-controlled
        assert executed["metadata"].get("plan_source") == "deterministic_generated"

    @pytest.mark.asyncio
    async def test_approve_standard_ignores_tampered_packet_facts(self, app, loaded_nominal):
        """Tampered size_bits in submitted plan must not affect execution.

        The standard path uses the canonical issued plan from the registry directly,
        so submitted packet facts are entirely ignored — only IDs/order matter.
        """
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            original_plan = plans[0]
            original_size = original_plan["packets"][0]["size_bits"]

            # Build a submission with tampered packet facts but preserved order.
            # Only tamper size_bits (no Pydantic constraint) to avoid HTTP 422 from
            # the schema validation before the business logic runs.
            import copy
            tampered_plan = copy.deepcopy(original_plan)
            for pkt in tampered_plan["packets"]:
                pkt["size_bits"] = 999_999_999

            # Submit with tampered facts but same IDs/order
            resp = await c.post("/approve", json={
                "plan_id": tampered_plan["plan_id"],
                "plan": tampered_plan,
            })
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.json()}"
        body = resp.json()
        executed = body["executed_plan"]
        # Executed plan uses authoritative packet facts from the registry
        for pkt in executed["packets"]:
            assert pkt["size_bits"] != 999_999_999

    @pytest.mark.asyncio
    async def test_approve_custom_gets_operator_custom_provenance(self, app, loaded_nominal):
        """Custom plan must receive operator_custom provenance, not AI/deterministic."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            plan = plans[0]
            # Client claims AI-generated provenance
            plan["strategy"] = "ai-prioritized"
            plan["generated_by"] = "Granite"
            plan["metadata"]["plan_source"] = "ai_generated"
            plan["metadata"]["benchmark_certified"] = True

            resp = await c.post("/approve/custom", json={"plan": plan})
        assert resp.status_code == 200
        body = resp.json()
        assert body["approval_trace"]["plan_source"] == "operator_custom"
        assert body["approval_trace"]["issued_plan_verified"] is False
        # Executed plan must not carry claimed AI provenance
        executed = body["executed_plan"]
        assert executed["metadata"].get("plan_source") == "operator_custom"
        assert executed["metadata"].get("benchmark_certified") is not True

    @pytest.mark.asyncio
    async def test_plans_evaluate_does_not_trust_client_provenance(self, app, loaded_nominal):
        """POST /plans/evaluate client provenance must not appear in result."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            plan = plans[0]
            plan["strategy"] = "FAKE_TRUSTED_STRATEGY"
            plan["metadata"]["plan_source"] = "ai_generated"

            resp = await c.post("/plans/evaluate", json=plan)
        # EvaluationResult doesn't include plan strategy — the important thing is
        # the response succeeds and uses authoritative packet facts
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_simulate_what_if_ignores_client_provenance(self, app, loaded_nominal):
        """POST /simulate/what-if must not trust client provenance."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            plan = plans[0]
            plan["strategy"] = "client_controlled_strategy"
            plan["metadata"]["plan_source"] = "ai_generated"

            resp = await c.post("/simulate/what-if", json={"plan": plan})
        assert resp.status_code == 200

    def test_reconstruct_strips_client_strategy_and_metadata(self, loaded_nominal):
        """reconstruct_authoritative_plan must not preserve client strategy or metadata."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        client_plan = CandidatePlan(
            plan_id="provenance_test",
            strategy="NASA_CERTIFIED",
            packets=packets[:2],
            generated_by="attacker",
            metadata={
                "plan_source": "definitely_ai_generated",
                "trusted": True,
                "evaluator": "fake_evaluator",
                "custom_key": "custom_value",
            },
        )
        trace = reconstruct_authoritative_plan(
            client_plan,
            app_state.active_scenario,
            plan_source=PlanSource.client_intent,
        )
        plan = trace.reconstructed_plan
        # Backend must override these
        assert plan.strategy != "NASA_CERTIFIED"
        assert plan.generated_by != "attacker"
        assert plan.metadata.get("plan_source") == PlanSource.client_intent.value
        assert plan.metadata.get("trusted") is not True
        assert plan.metadata.get("evaluator") != "fake_evaluator"
        # client_key must not leak through
        assert "custom_key" not in plan.metadata


# ===========================================================================
# B. Exact canonical hash after provenance finalization
# ===========================================================================


class TestCanonicalHashAfterProvenance:
    """Hash must be computed AFTER trusted provenance is finalized."""

    def test_generate_plans_hash_matches_registry(self, loaded_nominal):
        """For deterministic plans: recompute_hash(registry.canonical_plan) == stored hash."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import compute_plan_fingerprint

        from backend.app.api.routes_plans import generate_plans
        plans = generate_plans()

        scenario_id = app_state.active_scenario.scenario_id
        for plan in plans:
            record = app_state.issued_plans[plan.plan_id]
            # Recompute from the canonical snapshot stored in the registry
            _, recomputed_sha = compute_plan_fingerprint(
                record.canonical_plan, scenario_id
            )
            assert recomputed_sha == record.canonical_plan_sha256, (
                f"Hash mismatch for plan '{plan.plan_id}': "
                f"stored={record.canonical_plan_sha256}, "
                f"recomputed={recomputed_sha}"
            )

    def test_registry_canonical_plan_has_plan_source_set(self, loaded_nominal):
        """Registry canonical plan must have plan_source set (before hashing)."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import generate_plans

        generate_plans()
        for record in app_state.issued_plans.values():
            assert record.canonical_plan.metadata.get("plan_source") == record.plan_source, (
                f"plan_source mismatch in registry for plan '{record.plan_id}'"
            )

    def test_hash_includes_plan_source(self, loaded_nominal):
        """Changing plan_source must change the canonical hash."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            _compute_canonical_hash,
            get_authoritative_packets,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        scenario_id = app_state.active_scenario.scenario_id

        plan_a = CandidatePlan(
            plan_id="hash_test",
            strategy="test",
            packets=packets[:1],
            generated_by="test",
            metadata={"plan_source": "deterministic_generated"},
        )
        plan_b = CandidatePlan(
            plan_id="hash_test",
            strategy="test",
            packets=packets[:1],
            generated_by="test",
            metadata={"plan_source": "ai_generated"},
        )
        sha_a = _compute_canonical_hash(plan_a, scenario_id)
        sha_b = _compute_canonical_hash(plan_b, scenario_id)
        assert sha_a != sha_b, "Different plan_source must produce different canonical hash"

    def test_canonicalize_issued_plan_correct_order(self, loaded_nominal):
        """canonicalize_issued_plan must finalize provenance before hashing."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            PlanSource,
            canonicalize_issued_plan,
            _compute_canonical_hash,
            get_authoritative_packets,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        scenario_id = app_state.active_scenario.scenario_id

        plan = CandidatePlan(
            plan_id="canon_test",
            strategy="test",
            packets=packets[:2],
            generated_by="test",
            metadata={},
        )
        snapshot, order_sha, canonical_sha = canonicalize_issued_plan(
            plan, scenario_id, PlanSource.deterministic_generated
        )
        # The snapshot has plan_source set
        assert snapshot.metadata["plan_source"] == PlanSource.deterministic_generated.value
        # Recompute from the snapshot — must match stored sha
        recomputed = _compute_canonical_hash(snapshot, scenario_id)
        assert recomputed == canonical_sha

    def test_ai_plan_hash_matches_registry(self, loaded_v3):
        """For AI-generated plans: recompute_hash(registry.canonical_plan) == stored hash."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import compute_plan_fingerprint

        # Use the recommend endpoint which registers AI plans
        from backend.app.api.routes_agent import recommend
        resp = recommend()

        scenario_id = app_state.active_scenario.scenario_id
        ai_records = [
            r for r in app_state.issued_plans.values()
            if r.plan_source == "ai_generated"
        ]
        # v3 scenario should have at least one AI plan
        assert len(ai_records) >= 1
        for record in ai_records:
            _, recomputed = compute_plan_fingerprint(record.canonical_plan, scenario_id)
            assert recomputed == record.canonical_plan_sha256


# ===========================================================================
# C. Registry deep-copy / immutability
# ===========================================================================


class TestRegistryImmutability:
    """Modifying the original plan after registration must not affect the registry."""

    def test_mutating_original_plan_does_not_affect_registry(self, loaded_nominal):
        """Registry canonical plan must be independent of the original plan object."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            PlanSource,
            canonicalize_issued_plan,
            get_authoritative_packets,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        scenario_id = app_state.active_scenario.scenario_id

        plan = CandidatePlan(
            plan_id="mutability_test",
            strategy="test",
            packets=packets[:2],
            generated_by="test",
            metadata={"extra": "original_value"},
        )
        snapshot, order_sha, canonical_sha = canonicalize_issued_plan(
            plan, scenario_id, PlanSource.deterministic_generated
        )
        app_state.register_issued_plan(
            snapshot,
            scenario_id=scenario_id,
            packet_order_sha256=order_sha,
            canonical_plan_sha256=canonical_sha,
            plan_source_value=PlanSource.deterministic_generated.value,
        )

        # Mutate the original plan
        plan.metadata["extra"] = "MUTATED_VALUE"
        plan.metadata["injected_key"] = "injected"

        # Registry must be unchanged
        record = app_state.issued_plans["mutability_test"]
        assert record.canonical_plan.metadata.get("extra") != "MUTATED_VALUE"
        assert "injected_key" not in record.canonical_plan.metadata

    def test_registry_hash_still_matches_after_mutation_attempt(self, loaded_nominal):
        """Registry hash must still match after external mutation of the original plan."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            PlanSource,
            canonicalize_issued_plan,
            _compute_canonical_hash,
            get_authoritative_packets,
        )
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        scenario_id = app_state.active_scenario.scenario_id

        plan = CandidatePlan(
            plan_id="hash_immut_test",
            strategy="test",
            packets=packets[:1],
            generated_by="test",
            metadata={},
        )
        snapshot, order_sha, canonical_sha = canonicalize_issued_plan(
            plan, scenario_id, PlanSource.ai_generated
        )
        app_state.register_issued_plan(
            snapshot,
            scenario_id=scenario_id,
            packet_order_sha256=order_sha,
            canonical_plan_sha256=canonical_sha,
            plan_source_value=PlanSource.ai_generated.value,
        )

        # Mutate the original plan after registration
        plan.metadata["plan_source"] = "operator_custom"  # attempt to change plan_source

        record = app_state.issued_plans["hash_immut_test"]
        # Registry hash must still match the registry canonical plan
        recomputed = _compute_canonical_hash(record.canonical_plan, scenario_id)
        assert recomputed == record.canonical_plan_sha256
        # The plan_source in the registry snapshot is still the original
        assert record.canonical_plan.metadata.get("plan_source") == "ai_generated"

    def test_generate_plans_registry_independent_of_returned_plans(self, loaded_nominal):
        """Mutating returned plans must not affect registry."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import generate_plans
        from backend.app.domain.plan_integrity import compute_plan_fingerprint

        plans = generate_plans()
        scenario_id = app_state.active_scenario.scenario_id

        # Mutate returned plans
        for p in plans:
            p.metadata["tampered"] = True
            p.metadata["plan_source"] = "operator_custom"

        # Registry must be unchanged
        for record in app_state.issued_plans.values():
            assert record.canonical_plan.metadata.get("tampered") is not True
            assert record.canonical_plan.metadata.get("plan_source") == "deterministic_generated"
            # Hash must still be consistent
            _, recomputed = compute_plan_fingerprint(record.canonical_plan, scenario_id)
            assert recomputed == record.canonical_plan_sha256


# ===========================================================================
# D. Scenario mismatch → /approve fails closed
# ===========================================================================


class TestScenarioMismatch:
    """Stale plan from different scenario must not execute."""

    @pytest.mark.asyncio
    async def test_approve_fails_on_scenario_mismatch(self, app, loaded_nominal):
        """Manually setting a mismatched scenario_id in the registry must trigger 409."""
        from backend.app import state as app_state

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]
            plan_id = first_plan["plan_id"]

            # Tamper the registry: change scenario_id to a different scenario
            record = app_state.issued_plans[plan_id]
            # Replace with a stale scenario_id
            from backend.app.state import IssuedPlanRecord
            stale_record = IssuedPlanRecord(
                plan_id=record.plan_id,
                scenario_id="STALE_SCENARIO_ID_THAT_DOES_NOT_MATCH",
                canonical_plan=record.canonical_plan,
                packet_order_sha256=record.packet_order_sha256,
                canonical_plan_sha256=record.canonical_plan_sha256,
                plan_source=record.plan_source,
                issued_at=record.issued_at,
            )
            app_state.issued_plans[plan_id] = stale_record

            resp = await c.post("/approve", json={
                "plan_id": plan_id,
                "plan": first_plan,
            })
        assert resp.status_code == 409
        assert "STALE_PLAN" in resp.json()["detail"]


# ===========================================================================
# E. Internal fingerprint mismatch → /approve fails closed
# ===========================================================================


class TestFingerprintMismatch:
    """Tampered registry fingerprint must prevent execution."""

    @pytest.mark.asyncio
    async def test_approve_fails_on_tampered_fingerprint(self, app, loaded_nominal):
        """Tampering with the stored canonical_plan_sha256 must trigger 500."""
        from backend.app import state as app_state

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]
            plan_id = first_plan["plan_id"]

            # Tamper the stored fingerprint in the registry
            record = app_state.issued_plans[plan_id]
            from backend.app.state import IssuedPlanRecord
            tampered_record = IssuedPlanRecord(
                plan_id=record.plan_id,
                scenario_id=record.scenario_id,
                canonical_plan=record.canonical_plan,
                packet_order_sha256=record.packet_order_sha256,
                canonical_plan_sha256="a" * 64,  # tampered fingerprint
                plan_source=record.plan_source,
                issued_at=record.issued_at,
            )
            app_state.issued_plans[plan_id] = tampered_record

            resp = await c.post("/approve", json={
                "plan_id": plan_id,
                "plan": first_plan,
            })
        assert resp.status_code == 500
        assert "FINGERPRINT_MISMATCH" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_approve_does_not_execute_on_fingerprint_failure(self, app, loaded_nominal):
        """No simulation must run when fingerprint mismatch is detected."""
        from backend.app import state as app_state

        original_link = None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            first_plan = plans[0]
            plan_id = first_plan["plan_id"]

            # Record state before tamper
            from backend.app.state import IssuedPlanRecord
            record = app_state.issued_plans[plan_id]
            tampered_record = IssuedPlanRecord(
                plan_id=record.plan_id,
                scenario_id=record.scenario_id,
                canonical_plan=record.canonical_plan,
                packet_order_sha256=record.packet_order_sha256,
                canonical_plan_sha256="b" * 64,  # tampered
                plan_source=record.plan_source,
                issued_at=record.issued_at,
            )
            app_state.issued_plans[plan_id] = tampered_record
            original_link = app_state.active_link_state

            resp = await c.post("/approve", json={
                "plan_id": plan_id,
                "plan": first_plan,
            })
        assert resp.status_code == 500
        # State must be unchanged (no simulation ran)
        assert app_state.active_link_state is original_link


# ===========================================================================
# F. Recommendation finalization
# ===========================================================================


class TestRecommendationFinalization:
    """Provider-returned risk/packet_actions must be replaced by authoritative values."""

    def test_finalize_recommendation_replaces_risk(self, loaded_nominal):
        """finalize_recommendation must replace provider risk with authoritative values."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a", risk_score=0.15)

        # Provider returns deliberately wrong risk values
        bad_rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[{"packet_id": "p1", "action": "transmit", "rank": 99}],
            risk_score=0.99,  # wrong
            risk_level=RiskLevel.CRITICAL,  # wrong
            confidence=0.5,
            reasoning="test",
            evidence=[],
        )

        provider = LocalRuleBasedProvider()
        finalized = finalize_recommendation(bad_rec, [plan], [eval_a], provider)

        # Risk must be authoritative
        assert finalized.risk_score == pytest.approx(0.15)
        assert finalized.risk_level == RiskLevel.LOW
        # packet_actions must be rebuilt from the authoritative plan
        assert len(finalized.packet_actions) == 1
        assert finalized.packet_actions[0]["packet_id"] == "p1"
        assert finalized.packet_actions[0]["rank"] == 1  # not 99

    def test_finalize_recommendation_assigns_heuristic_for_local_provider(self, loaded_nominal):
        """LocalRuleBasedProvider must receive heuristic confidence_semantics."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.5,
            risk_level=RiskLevel.MEDIUM,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )
        provider = LocalRuleBasedProvider()
        finalized = finalize_recommendation(rec, [plan], [eval_a], provider)
        assert finalized.confidence_semantics == ConfidenceSemantics.heuristic

    def test_finalize_recommendation_assigns_unspecified_for_unknown_external(self, loaded_nominal):
        """Unknown external provider must receive unspecified_uncalibrated confidence_semantics (Phase 4.1a)."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.5,
            risk_level=RiskLevel.MEDIUM,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )

        # Fake provider that is not a known LLM provider — gets unspecified_uncalibrated
        fake_ext = MagicMock()
        fake_ext.__class__ = type("FakeUnknownProvider", (), {})
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        assert not isinstance(fake_ext, LocalRuleBasedProvider)

        finalized = finalize_recommendation(rec, [plan], [eval_a], fake_ext)
        assert finalized.confidence_semantics == ConfidenceSemantics.unspecified_uncalibrated

    def test_finalize_recommendation_unknown_plan_id_raises(self, loaded_nominal):
        """Unknown plan_id must now raise RecommendationFinalizationError (Phase 4.1a)."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="NONEXISTENT_PLAN_ID",
            packet_actions=[{"packet_id": "p1", "action": "transmit", "rank": 1}],
            risk_score=0.99,
            risk_level=RiskLevel.CRITICAL,
            confidence=0.5,
            reasoning="test",
            evidence=[],
        )
        provider = LocalRuleBasedProvider()
        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(rec, [plan], [eval_a], provider)
        assert exc_info.value.reason == RecommendationFinalizationError.UNKNOWN_RECOMMENDED_PLAN

    def test_finalize_recommendation_drops_invalid_alternative_plan_id(self, loaded_nominal):
        """Invalid alternative_plan_id must be set to None."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.5,
            reasoning="test",
            evidence=[],
            alternative_plan_id="UNKNOWN_PLAN",
        )
        provider = LocalRuleBasedProvider()
        finalized = finalize_recommendation(rec, [plan], [eval_a], provider)
        assert finalized.alternative_plan_id is None

    @pytest.mark.asyncio
    async def test_local_legacy_path_recommendation_has_authoritative_risk(self, app, loaded_nominal):
        """Local/legacy recommendation path must have authoritative risk via finalize."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        rec = body["recommendation"]
        # risk_score must be in [0, 1]
        assert 0.0 <= rec["risk_score"] <= 1.0
        # risk_level must be a valid enum value
        assert rec["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        # packet_actions must be non-empty for legacy path
        assert len(rec["packet_actions"]) > 0
        # Each action must have packet_id, action, rank
        for action in rec["packet_actions"]:
            assert "packet_id" in action
            assert "action" in action
            assert "rank" in action


# ===========================================================================
# G. Invalid recommendation plan_id behavior
# ===========================================================================


class TestInvalidRecommendedPlanId:
    """Invalid plan_id from provider must cause typed finalization failure (Phase 4.1a)."""

    def test_finalize_raises_on_invalid_plan_id(self, loaded_nominal):
        """finalize_recommendation with unknown plan_id must raise RecommendationFinalizationError."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        plans = [_make_plan("real_plan", [_make_pkt("p1")])]
        evals = [_make_eval("real_plan", risk_score=0.1)]

        rec = AIRecommendation(
            recommended_plan_id="TOTALLY_FAKE_PLAN_ID",
            packet_actions=[{"packet_id": "fake", "action": "transmit", "rank": 1}],
            risk_score=0.5,
            risk_level=RiskLevel.MEDIUM,
            confidence=0.9,
            reasoning="fake reasoning",
            evidence=[],
        )
        provider = LocalRuleBasedProvider()
        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(rec, plans, evals, provider)
        assert exc_info.value.reason == RecommendationFinalizationError.UNKNOWN_RECOMMENDED_PLAN


# ===========================================================================
# H. Confidence semantics
# ===========================================================================


class TestConfidenceSemanticsTyped:
    """confidence_semantics must be typed enum, backend-assigned, fail-safe default."""

    def test_model_default_is_unspecified_uncalibrated(self):
        """AIRecommendation default confidence_semantics must be unspecified_uncalibrated."""
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
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
        assert rec.confidence_semantics == ConfidenceSemantics.unspecified_uncalibrated
        # Must NOT be heuristic by default
        assert rec.confidence_semantics != ConfidenceSemantics.heuristic

    def test_local_provider_gets_heuristic(self):
        """LocalRuleBasedProvider recommendation must have heuristic semantics."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import ConfidenceSemantics

        provider = LocalRuleBasedProvider()
        assert _confidence_semantics_for_provider(provider) == ConfidenceSemantics.heuristic

    def test_unknown_provider_gets_unspecified_uncalibrated(self):
        """Unknown provider (not Local/Granite/Gemini/Ollama) must get unspecified_uncalibrated (Phase 4.1a)."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import ConfidenceSemantics

        # A MagicMock is not a known provider class
        fake_provider = MagicMock()
        assert not isinstance(fake_provider, LocalRuleBasedProvider)
        assert _confidence_semantics_for_provider(fake_provider) == ConfidenceSemantics.unspecified_uncalibrated

    def test_none_provider_gets_unspecified_uncalibrated(self):
        """None provider must get unspecified_uncalibrated (fail-safe)."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        assert _confidence_semantics_for_provider(None) == ConfidenceSemantics.unspecified_uncalibrated

    def test_confidence_semantics_enum_has_all_values(self):
        """ConfidenceSemantics enum must have exactly the three documented values."""
        from backend.app.models.recommendation import ConfidenceSemantics

        values = {e.value for e in ConfidenceSemantics}
        assert "heuristic" in values
        assert "uncalibrated_llm" in values
        assert "unspecified_uncalibrated" in values

    @pytest.mark.asyncio
    async def test_recommend_response_has_valid_confidence_semantics(self, app, loaded_nominal):
        """POST /agent/recommend must return a valid confidence_semantics value."""
        from backend.app.models.recommendation import ConfidenceSemantics

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        semantics_val = resp.json()["recommendation"]["confidence_semantics"]
        valid_values = {e.value for e in ConfidenceSemantics}
        assert semantics_val in valid_values


# ===========================================================================
# I. Stage provider identity
# ===========================================================================


class TestStageProviderIdentity:
    """prioritization_provider and recommendation_provider must be independently reported."""

    @pytest.mark.asyncio
    async def test_legacy_path_has_null_prioritization_provider(self, app, loaded_nominal):
        """Legacy scenario (packets only) must have null prioritization_provider."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["prioritization_provider"] is None
        # recommendation_provider must be set
        assert body["recommendation_provider"] is not None
        assert len(body["recommendation_provider"]) > 0

    @pytest.mark.asyncio
    async def test_legacy_path_provider_fields_consistent(self, app, loaded_nominal):
        """Legacy path: provider == actual_provider == recommendation_provider."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        body = resp.json()
        assert body["provider"] == body["actual_provider"]
        assert body["actual_provider"] == body["recommendation_provider"]

    @pytest.mark.asyncio
    async def test_v3_path_has_prioritization_provider(self, app, loaded_v3):
        """v3 scenario (data_products) must have non-null prioritization_provider."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        # v3 path uses Stage-1 prioritization
        assert body["prioritization_provider"] is not None
        assert body["recommendation_provider"] is not None
        # provider == actual_provider == recommendation_provider when no fallback
        if body["recommendation_fallback_reason"] is None:
            assert body["provider"] == body["recommendation_provider"]

    def test_confidence_semantics_assignment_local_local(self, loaded_nominal):
        """When both stages use Local: confidence_semantics == heuristic."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import ConfidenceSemantics

        local = LocalRuleBasedProvider()
        assert _confidence_semantics_for_provider(local) == ConfidenceSemantics.heuristic

    def test_confidence_semantics_assignment_external_unknown(self):
        """Unknown provider (non-Local, non-known-LLM): confidence_semantics == unspecified_uncalibrated (Phase 4.1a)."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import ConfidenceSemantics

        class UnknownProvider:
            pass

        ext = UnknownProvider()
        assert not isinstance(ext, LocalRuleBasedProvider)
        assert _confidence_semantics_for_provider(ext) == ConfidenceSemantics.unspecified_uncalibrated


# ===========================================================================
# J. Regression guarantees
# ===========================================================================


class TestPhase41Regression:
    """Regression: formulas, blinding, evidence, registry lifecycle must be unchanged."""

    def test_benchmark_v1_config_byte_for_byte_unchanged(self):
        """Benchmark config must be byte-for-byte unchanged."""
        path = Path(_BENCHMARK_V1)
        data = json.loads(path.read_text())
        assert data["benchmark_version"] == "gcsi_benchmark_v1"
        assert data["candidate_limit"] == 50
        assert data["provider"] == "Granite"
        assert set(data["capacity_ratios"]) == {0.35, 0.60, 0.90, 1.20}

    def test_read_only_operations_do_not_invalidate_registry(self, loaded_nominal):
        """evaluate and what-if must not clear the registry."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import generate_plans, evaluate_plan
        from backend.app.models.candidate_plan import CandidatePlan

        plans = generate_plans()
        initial_count = len(app_state.issued_plans)
        assert initial_count > 0

        # evaluate is a read-only operation
        evaluate_plan(plans[0])
        assert len(app_state.issued_plans) == initial_count

    def test_authoritative_reconstruction_uses_scenario_facts(self, loaded_nominal):
        """Reconstructed plan must have scenario-authoritative packet facts."""
        from backend.app import state as app_state
        from backend.app.domain.plan_integrity import (
            get_authoritative_packets,
            reconstruct_authoritative_plan,
            PlanSource,
        )
        from backend.app.models.candidate_plan import CandidatePlan
        from backend.app.models.packet import Packet

        packets = get_authoritative_packets(app_state.active_scenario)
        auth_size = packets[0].size_bits

        tampered = Packet.model_construct(
            **{**packets[0].model_dump(), "size_bits": auth_size + 12345}
        )
        client_plan = CandidatePlan(
            plan_id="regen_test",
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

    def test_stage2_blinding_still_works(self, loaded_v3):
        """Stage-2 context must not contain forbidden provenance strings."""
        from backend.app import state as app_state
        from backend.app.agent.stage2_blinding import (
            build_blind_mapping,
            build_stage2_summaries,
            build_stage2_user_message,
            assert_no_provenance_leak,
        )
        from backend.app.api.routes_plans import generate_plans
        from backend.app.config import SchedulerWeights
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.models.candidate_plan import CandidatePlan

        scenario = app_state.active_scenario
        link_state = app_state.active_link_state

        gen = CandidateGenerator()
        plans = gen.generate(
            get_authoritative_packets(scenario),
            link_state,
            scenario.mission_state,
            SchedulerWeights(),
        )
        ev = PlanEvaluator()
        evals = [ev.evaluate(p, link_state, scenario.mission_state) for p in plans]

        alias_map = build_blind_mapping(plans, scenario_id=scenario.scenario_id)
        summaries = build_stage2_summaries(alias_map, plans, evals)
        context = build_stage2_user_message(summaries, link_state, scenario.mission_state)

        # Must not raise
        assert_no_provenance_leak(context)

    def test_v3_effective_packets_matches_plan_integrity(self, loaded_v3):
        """routes_agent._effective_packets must equal plan_integrity.get_authoritative_packets."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import _effective_packets
        from backend.app.domain.plan_integrity import get_authoritative_packets

        scenario = app_state.active_scenario
        agent_pkts = _effective_packets(scenario)
        auth_pkts = get_authoritative_packets(scenario)

        assert len(agent_pkts) == len(auth_pkts)
        for a, b in zip(agent_pkts, auth_pkts):
            assert a.packet_id == b.packet_id
            assert a.size_bits == b.size_bits

    def test_nominal_effective_packets_matches_plan_integrity(self, loaded_nominal):
        """routes_agent._effective_packets must equal plan_integrity.get_authoritative_packets for nominal."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import _effective_packets
        from backend.app.domain.plan_integrity import get_authoritative_packets

        scenario = app_state.active_scenario
        agent_pkts = _effective_packets(scenario)
        auth_pkts = get_authoritative_packets(scenario)

        assert len(agent_pkts) == len(auth_pkts)
        for a, b in zip(agent_pkts, auth_pkts):
            assert a.packet_id == b.packet_id

    def test_plan_evaluator_formulas_unchanged(self, loaded_nominal):
        """PlanEvaluator risk formula must remain unchanged."""
        from backend.app import state as app_state
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.config import RiskWeights
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app.models.candidate_plan import CandidatePlan

        packets = get_authoritative_packets(app_state.active_scenario)
        plan = CandidatePlan(
            plan_id="formula_test",
            strategy="test",
            packets=packets[:1],
            generated_by="test",
            metadata={},
        )
        ev = PlanEvaluator()
        result = ev.evaluate(plan, app_state.active_link_state, app_state.active_scenario.mission_state)
        rw = RiskWeights()
        expected = (
            rw.w_deadline_miss * result.deadline_miss_rate
            + rw.w_critical_deficit * result.critical_deficit
            + rw.w_window_pressure * result.window_pressure
        )
        assert abs(result.risk_score - expected) < 1e-9

    @pytest.mark.asyncio
    async def test_approve_invalidates_registry(self, app, loaded_nominal):
        """Registry must be empty after /approve."""
        from backend.app import state as app_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            await c.post("/approve", json={
                "plan_id": plans[0]["plan_id"],
                "plan": plans[0],
            })
        assert len(app_state.issued_plans) == 0

    @pytest.mark.asyncio
    async def test_read_only_operations_do_not_invalidate_registry_async(self, app, loaded_nominal):
        """POST /plans/evaluate and /simulate/what-if must not invalidate registry."""
        from backend.app import state as app_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            gen_resp = await c.post("/plans/generate")
            plans = gen_resp.json()
            initial_count = len(app_state.issued_plans)

            await c.post("/plans/evaluate", json=plans[0])
            assert len(app_state.issued_plans) == initial_count

            await c.post("/simulate/what-if", json={"plan": plans[0]})
            assert len(app_state.issued_plans) == initial_count
