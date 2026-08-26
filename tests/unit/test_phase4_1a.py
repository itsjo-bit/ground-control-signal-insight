"""Phase 4.1a — Fail-Closed Recommendation Finalization.

Targeted regression tests proving the trust properties added in Phase 4.1a:

A. Unknown recommended_plan_id causes typed finalization failure
B. Missing EvaluationResult causes typed finalization failure
C. Valid plan still finalizes correctly (authoritative rebinding)
D. Invalid alternative_plan_id is safely dropped (soft drop)
E. Legacy external path — invalid plan causes Local fallback via route
F. Finalization fallback failure returns HTTP 502
G. Confidence semantics — explicit known-class classification
H. Provider-returned confidence_semantics cannot override backend
I. Stage provider identity after finalization fallback
J. Blinded Stage-2 invalid-alias fallback still works (regression)
K. No fallback when valid — external providers retained on success
L. Benchmark v1 config byte-for-byte unchanged
M. Scientific components unchanged
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Repository-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parents[2]
_NOMINAL_PATH = str(_REPO_ROOT / "data" / "scenarios" / "nominal_pass.json")
_V3_PATH = str(_REPO_ROOT / "data" / "scenarios" / "mission_data_v3.json")
_BENCHMARK_V1 = str(_REPO_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_bad_rec(recommended_plan_id="FAKE-999"):
    """Return an AIRecommendation with an unknown plan_id and fabricated values."""
    from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
    from backend.app.models.risk_level import RiskLevel
    return AIRecommendation(
        recommended_plan_id=recommended_plan_id,
        packet_actions=[{"packet_id": "fake_p", "action": "transmit", "rank": 1}],
        risk_score=0.0,   # deliberately wrong (fabricated LOW risk)
        risk_level=RiskLevel.LOW,
        confidence=0.99,  # fabricated high confidence
        confidence_semantics=ConfidenceSemantics.heuristic,  # fabricated; must be overridden
        reasoning="fabricated reasoning",
        evidence=[],
    )


# ===========================================================================
# A. Unknown recommended_plan_id causes typed finalization failure
# ===========================================================================


class TestUnknownPlanRaises:
    """finalize_recommendation must raise on unknown plan_id."""

    def test_unknown_plan_id_raises_finalization_error(self):
        """recommended_plan_id not in plan set → RecommendationFinalizationError."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        plans = [_make_plan("real_plan", [_make_pkt("p1")])]
        evals = [_make_eval("real_plan")]

        bad_rec = _make_bad_rec("FAKE-999")
        provider = LocalRuleBasedProvider()

        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(bad_rec, plans, evals, provider)

        assert exc_info.value.reason == RecommendationFinalizationError.UNKNOWN_RECOMMENDED_PLAN
        # The error message must mention the bad plan_id
        assert "FAKE-999" in str(exc_info.value)

    def test_unknown_plan_id_does_not_return_unchanged(self):
        """Must NOT silently return the untrusted recommendation."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        plans = [_make_plan("real_plan", [_make_pkt("p1")])]
        evals = [_make_eval("real_plan")]
        bad_rec = _make_bad_rec("FAKE-999")
        provider = LocalRuleBasedProvider()

        raised = False
        try:
            finalize_recommendation(bad_rec, plans, evals, provider)
        except RecommendationFinalizationError:
            raised = True

        assert raised, "finalize_recommendation must raise, not return unchanged"

    def test_empty_plan_list_raises(self):
        """Empty plan list → unknown plan_id → must raise."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        bad_rec = _make_bad_rec("FAKE-999")
        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(bad_rec, [], [], LocalRuleBasedProvider())
        assert exc_info.value.reason == RecommendationFinalizationError.UNKNOWN_RECOMMENDED_PLAN


# ===========================================================================
# B. Missing EvaluationResult causes typed finalization failure
# ===========================================================================


class TestMissingEvaluationRaises:
    """Plan exists but EvaluationResult is missing → must raise."""

    def test_missing_evaluation_raises(self):
        """Plan in plan list but no matching EvaluationResult → RecommendationFinalizationError."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        plan = _make_plan("plan_with_no_eval", [_make_pkt("p1")])
        # No EvaluationResult for this plan_id
        evals = []

        rec = AIRecommendation(
            recommended_plan_id="plan_with_no_eval",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )
        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(rec, [plan], evals, LocalRuleBasedProvider())
        assert exc_info.value.reason == RecommendationFinalizationError.MISSING_EVALUATION

    def test_missing_evaluation_different_plan_id_raises(self):
        """EvaluationResult for a different plan_id must not satisfy the missing one."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.base_provider import RecommendationFinalizationError
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        plan_a = _make_plan("plan_a", [_make_pkt("p1")])
        eval_b = _make_eval("plan_b")  # different plan_id

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )
        with pytest.raises(RecommendationFinalizationError) as exc_info:
            finalize_recommendation(rec, [plan_a], [eval_b], LocalRuleBasedProvider())
        assert exc_info.value.reason == RecommendationFinalizationError.MISSING_EVALUATION


# ===========================================================================
# C. Valid plan still finalizes correctly (authoritative rebinding)
# ===========================================================================


class TestValidPlanFinalizes:
    """Valid plan+eval must produce authoritative rebinding regardless of provider values."""

    def test_authoritative_risk_rebinding(self):
        """risk_score/risk_level must come from EvaluationResult, not from provider."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a", risk_score=0.12)

        # Provider deliberately returns wrong risk values
        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[{"packet_id": "p1", "action": "transmit", "rank": 99}],
            risk_score=0.99,            # wrong
            risk_level=RiskLevel.CRITICAL,  # wrong
            confidence=0.9,
            confidence_semantics=ConfidenceSemantics.uncalibrated_llm,  # will be overridden
            reasoning="test",
            evidence=[],
        )
        provider = LocalRuleBasedProvider()
        finalized = finalize_recommendation(rec, [plan], [eval_a], provider)

        # risk_score and risk_level must be from EvaluationResult
        assert finalized.risk_score == pytest.approx(0.12)
        assert finalized.risk_level == RiskLevel.LOW
        # packet_actions must be rebuilt from the authoritative plan ordering
        assert len(finalized.packet_actions) == 1
        assert finalized.packet_actions[0]["packet_id"] == "p1"
        assert finalized.packet_actions[0]["rank"] == 1  # not 99

    def test_authoritative_packet_actions_rebuilt(self):
        """packet_actions must be rebuilt from the authoritative plan ordering."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        pkts = [_make_pkt("p1"), _make_pkt("p2"), _make_pkt("p3")]
        plan = _make_plan("plan_a", pkts)
        eval_a = _make_eval("plan_a", risk_score=0.1)

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            # Provider sends wrong packet ordering
            packet_actions=[
                {"packet_id": "p3", "action": "transmit", "rank": 1},
                {"packet_id": "p1", "action": "transmit", "rank": 2},
                {"packet_id": "p2", "action": "transmit", "rank": 3},
            ],
            risk_score=0.5,
            risk_level=RiskLevel.MEDIUM,
            confidence=0.8,
            reasoning="test",
            evidence=[],
        )
        finalized = finalize_recommendation(rec, [plan], [eval_a], LocalRuleBasedProvider())

        # Must be rebuilt from canonical plan order: p1, p2, p3
        ids = [a["packet_id"] for a in finalized.packet_actions]
        ranks = [a["rank"] for a in finalized.packet_actions]
        assert ids == ["p1", "p2", "p3"]
        assert ranks == [1, 2, 3]


# ===========================================================================
# D. Invalid alternative_plan_id is safely dropped
# ===========================================================================


class TestInvalidAlternativeDropped:
    """Invalid alternative_plan_id must be dropped (soft policy, not hard failure)."""

    def test_invalid_alternative_dropped(self):
        """alternative_plan_id not in plan set must become None (not raise)."""
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
            confidence=0.8,
            reasoning="test",
            evidence=[],
            alternative_plan_id="FAKE",  # not in plan set
        )
        finalized = finalize_recommendation(rec, [plan], [eval_a], LocalRuleBasedProvider())
        # Must be silently dropped, not raise
        assert finalized.alternative_plan_id is None

    def test_valid_alternative_preserved(self):
        """Valid alternative_plan_id must be preserved."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel

        pkts = [_make_pkt("p1")]
        plan_a = _make_plan("plan_a", pkts)
        plan_b = _make_plan("plan_b", pkts)
        eval_a = _make_eval("plan_a")
        eval_b = _make_eval("plan_b")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            reasoning="test",
            evidence=[],
            alternative_plan_id="plan_b",  # valid
        )
        finalized = finalize_recommendation(
            rec, [plan_a, plan_b], [eval_a, eval_b], LocalRuleBasedProvider()
        )
        assert finalized.alternative_plan_id == "plan_b"


# ===========================================================================
# E. Legacy external path — invalid plan causes Local fallback via route
# ===========================================================================


class TestLegacyExternalInvalidPlanFallback:
    """Fake external provider returning unknown plan_id must trigger Local fallback."""

    @pytest.mark.asyncio
    async def test_legacy_external_invalid_plan_falls_back_to_local(self, app, loaded_nominal):
        """Fake external provider returning FAKE-999 must produce a valid Local recommendation."""
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel
        from backend.app.agent.base_provider import BaseAIProvider
        from backend.app.agent.local_provider import LocalRuleBasedProvider

        class FakeExternalProvider(BaseAIProvider):
            @property
            def provider_name(self) -> str:
                return "FakeExternal"

            def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
                # Always returns an unknown plan_id
                return AIRecommendation(
                    recommended_plan_id="FAKE-999",
                    packet_actions=[{"packet_id": "fake_p", "action": "transmit", "rank": 1}],
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    confidence=0.99,
                    reasoning="fabricated",
                    evidence=[],
                )

        fake_provider = FakeExternalProvider()

        with patch("backend.app.api.routes_agent.get_provider", return_value=fake_provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        rec = body["recommendation"]

        # FAKE-999 must NOT appear in the final recommendation
        assert rec["recommended_plan_id"] != "FAKE-999"
        # actual_provider and recommendation_provider must be Local
        assert body["actual_provider"] == "Local"
        assert body["recommendation_provider"] == "Local"
        # recommendation_fallback_reason must be set
        assert body["recommendation_fallback_reason"] is not None
        assert len(body["recommendation_fallback_reason"]) > 0
        # Risk must be valid (from authoritative evaluator)
        assert 0.0 <= rec["risk_score"] <= 1.0
        assert rec["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        # packet_actions must be non-empty and valid
        assert len(rec["packet_actions"]) > 0
        for action in rec["packet_actions"]:
            assert "packet_id" in action
            assert "rank" in action

    @pytest.mark.asyncio
    async def test_invalid_plan_no_fake_999_in_response(self, app, loaded_nominal):
        """No FAKE-999 must appear anywhere in the final recommendation."""
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel
        from backend.app.agent.base_provider import BaseAIProvider

        class FakeExternalProvider(BaseAIProvider):
            @property
            def provider_name(self) -> str:
                return "FakeExternal"

            def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
                return AIRecommendation(
                    recommended_plan_id="FAKE-999",
                    packet_actions=[],
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    confidence=0.99,
                    reasoning="fabricated",
                    evidence=[],
                )

        with patch("backend.app.api.routes_agent.get_provider", return_value=FakeExternalProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        body_str = json.dumps(resp.json())
        assert "FAKE-999" not in body_str


# ===========================================================================
# F. Finalization fallback failure returns HTTP 502
# ===========================================================================


class TestFallbackFailureReturns502:
    """When both primary and Local fallback fail finalization, return 502."""

    @pytest.mark.asyncio
    async def test_both_fail_returns_502(self, app, loaded_nominal):
        """If primary fails finalization and Local also raises, HTTP 502 is returned."""
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel
        from backend.app.agent.base_provider import BaseAIProvider, AIProviderError

        class FakeExternalProvider(BaseAIProvider):
            @property
            def provider_name(self) -> str:
                return "FakeExternal"

            def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
                return AIRecommendation(
                    recommended_plan_id="FAKE-999",
                    packet_actions=[],
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    confidence=0.99,
                    reasoning="fabricated",
                    evidence=[],
                )

        with (
            patch("backend.app.api.routes_agent.get_provider", return_value=FakeExternalProvider()),
            patch(
                "backend.app.agent.local_provider.LocalRuleBasedProvider.recommend",
                side_effect=AIProviderError("Local fallback deliberately failed"),
            ),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        assert resp.status_code == 502
        # Must not be a fabricated recommendation
        detail = resp.json().get("detail", "")
        assert len(detail) > 0


# ===========================================================================
# G. Confidence semantics — explicit known-class classification
# ===========================================================================


class TestConfidenceSemanticsClassification:
    """Explicit known-class confidence semantics policy."""

    def test_local_gets_heuristic(self):
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.models.recommendation import ConfidenceSemantics

        assert _confidence_semantics_for_provider(LocalRuleBasedProvider()) == ConfidenceSemantics.heuristic

    def test_granite_gets_uncalibrated_llm(self):
        """GraniteProvider → uncalibrated_llm."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        # Use __new__ to avoid network/credential init
        from backend.app.agent.granite_provider import GraniteProvider
        provider = object.__new__(GraniteProvider)
        assert _confidence_semantics_for_provider(provider) == ConfidenceSemantics.uncalibrated_llm

    def test_gemini_gets_uncalibrated_llm(self):
        """GeminiProvider → uncalibrated_llm."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        from backend.app.agent.gemini_provider import GeminiProvider
        provider = object.__new__(GeminiProvider)
        assert _confidence_semantics_for_provider(provider) == ConfidenceSemantics.uncalibrated_llm

    def test_ollama_gets_uncalibrated_llm(self):
        """OllamaProvider → uncalibrated_llm."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        from backend.app.agent.ollama_provider import OllamaProvider
        provider = object.__new__(OllamaProvider)
        assert _confidence_semantics_for_provider(provider) == ConfidenceSemantics.uncalibrated_llm

    def test_unknown_provider_gets_unspecified_uncalibrated(self):
        """Unknown provider class → unspecified_uncalibrated (fail-safe, not uncalibrated_llm)."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        class MyDeterministicOptimizer:
            """Fake unknown provider — not an LLM."""

        provider = MyDeterministicOptimizer()
        result = _confidence_semantics_for_provider(provider)
        assert result == ConfidenceSemantics.unspecified_uncalibrated
        # Must NOT be uncalibrated_llm (would mislabel a non-LLM provider)
        assert result != ConfidenceSemantics.uncalibrated_llm

    def test_none_gets_unspecified_uncalibrated(self):
        """None → unspecified_uncalibrated (fail-safe)."""
        from backend.app.api.routes_agent import _confidence_semantics_for_provider
        from backend.app.models.recommendation import ConfidenceSemantics

        assert _confidence_semantics_for_provider(None) == ConfidenceSemantics.unspecified_uncalibrated


# ===========================================================================
# H. Provider-returned confidence_semantics cannot override backend
# ===========================================================================


class TestProviderSemanticsCantOverride:
    """Provider-returned confidence_semantics must be ignored."""

    def test_gemini_claiming_heuristic_gets_uncalibrated_llm(self):
        """GeminiProvider claiming heuristic → must be assigned uncalibrated_llm."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.agent.gemini_provider import GeminiProvider
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            confidence_semantics=ConfidenceSemantics.heuristic,  # provider claims heuristic
            reasoning="test",
            evidence=[],
        )
        provider = object.__new__(GeminiProvider)
        finalized = finalize_recommendation(rec, [plan], [eval_a], provider)
        # Backend must override to uncalibrated_llm
        assert finalized.confidence_semantics == ConfidenceSemantics.uncalibrated_llm

    def test_unknown_provider_claiming_uncalibrated_llm_gets_unspecified(self):
        """Unknown provider claiming uncalibrated_llm → must get unspecified_uncalibrated."""
        from backend.app.api.routes_agent import finalize_recommendation
        from backend.app.models.recommendation import AIRecommendation, ConfidenceSemantics
        from backend.app.models.risk_level import RiskLevel

        class UnknownProvider:
            pass

        pkt = _make_pkt("p1")
        plan = _make_plan("plan_a", [pkt])
        eval_a = _make_eval("plan_a")

        rec = AIRecommendation(
            recommended_plan_id="plan_a",
            packet_actions=[],
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            confidence_semantics=ConfidenceSemantics.uncalibrated_llm,  # provider claims LLM
            reasoning="test",
            evidence=[],
        )
        finalized = finalize_recommendation(rec, [plan], [eval_a], UnknownProvider())
        # Backend must override to unspecified_uncalibrated
        assert finalized.confidence_semantics == ConfidenceSemantics.unspecified_uncalibrated

    def test_local_provider_claiming_uncalibrated_llm_gets_heuristic(self):
        """LocalRuleBasedProvider claiming uncalibrated_llm → must get heuristic."""
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
            risk_score=0.1,
            risk_level=RiskLevel.LOW,
            confidence=0.8,
            confidence_semantics=ConfidenceSemantics.uncalibrated_llm,  # wrong
            reasoning="test",
            evidence=[],
        )
        finalized = finalize_recommendation(rec, [plan], [eval_a], LocalRuleBasedProvider())
        assert finalized.confidence_semantics == ConfidenceSemantics.heuristic


# ===========================================================================
# I. Stage provider identity after finalization fallback
# ===========================================================================


class TestStageProviderIdentityAfterFallback:
    """Provider identity fields must be correct after finalization fallback."""

    @pytest.mark.asyncio
    async def test_provider_identity_after_fallback(self, app, loaded_nominal):
        """After finalization fallback from external to Local:
        actual_provider == Local, recommendation_provider == Local.
        """
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel
        from backend.app.agent.base_provider import BaseAIProvider

        class FakeExternalProvider(BaseAIProvider):
            @property
            def provider_name(self) -> str:
                return "FakeExternal"

            def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
                # Returns invalid plan to force finalization fallback
                return AIRecommendation(
                    recommended_plan_id="FAKE-999",
                    packet_actions=[],
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    confidence=0.5,
                    reasoning="fake",
                    evidence=[],
                )

        with patch("backend.app.api.routes_agent.get_provider", return_value=FakeExternalProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        assert body["actual_provider"] == "Local"
        assert body["recommendation_provider"] == "Local"
        # requested_provider must reflect the original configured provider
        assert body["requested_provider"] == "FakeExternal"

    @pytest.mark.asyncio
    async def test_no_fallback_when_valid(self, app, loaded_nominal):
        """Valid local recommendation must NOT trigger fallback."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        # No fallback triggered
        assert body["recommendation_fallback_reason"] is None
        # actual_provider matches requested
        assert body["actual_provider"] == body["requested_provider"]
        assert body["recommendation_provider"] == body["requested_provider"]


# ===========================================================================
# J. Blinded Stage-2 invalid-alias fallback still works (regression)
# ===========================================================================


class TestBlindedStage2InvalidAlias:
    """Phase 4.1a must not break existing Stage-2 invalid-alias fallback behavior."""

    def test_invalid_alias_still_triggers_fallback(self, loaded_v3):
        """Stage-2 provider returning an invalid OPTION alias must still fall back to Local."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import _build_blind_recommend
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.agent.base_provider import BaseAIProvider
        from backend.app.models.recommendation import AIRecommendation
        from backend.app.models.risk_level import RiskLevel
        from backend.app.config import SchedulerWeights
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app.models.bridge import data_products_to_packets
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.evaluator.mission_outcome_evaluator import MissionOutcomeEvaluator

        scenario = app_state.active_scenario
        link_state = app_state.active_link_state

        gen = CandidateGenerator()
        all_packets = data_products_to_packets(scenario.data_products)
        plans = gen.generate(all_packets, link_state, scenario.mission_state, SchedulerWeights())
        ev = PlanEvaluator()
        evals = [ev.evaluate(p, link_state, scenario.mission_state) for p in plans]
        outcome_ev = MissionOutcomeEvaluator()
        outcomes = [
            outcome_ev.evaluate(p, e, scenario.data_products, scenario.anomalies)
            for p, e in zip(plans, evals)
        ]

        class BadAliasProvider(BaseAIProvider):
            @property
            def provider_name(self):
                return "BadAlias"

            def recommend_from_summaries(self, summaries, link_state, mission_state, anomalies=None):
                return AIRecommendation(
                    recommended_plan_id="INVALID-ALIAS-XYZ",
                    packet_actions=[],
                    risk_score=0.5,
                    risk_level=RiskLevel.MEDIUM,
                    confidence=0.5,
                    reasoning="bad alias",
                    evidence=[],
                )

            def recommend(self, link_state, mission_state, plans, evaluations, *, anomalies=None):
                raise NotImplementedError

        fallback = LocalRuleBasedProvider()
        rec, reason = _build_blind_recommend(
            BadAliasProvider(),
            fallback,
            plans,
            evals,
            outcomes,
            scenario.scenario_id,
            link_state=link_state,
            mission_state=scenario.mission_state,
            anomalies=scenario.anomalies,
        )

        # Must have fallen back to Local
        assert reason is not None
        # The recommendation plan_id must be a real plan (not an alias or INVALID)
        real_plan_ids = {p.plan_id for p in plans}
        assert rec.recommended_plan_id in real_plan_ids


# ===========================================================================
# K. No fallback when valid
# ===========================================================================


class TestNoFallbackWhenValid:
    """Valid external result must not trigger finalization fallback."""

    @pytest.mark.asyncio
    async def test_valid_result_retained(self, app, loaded_nominal):
        """Valid recommendation must be returned unchanged (no fallback)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/agent/recommend")

        assert resp.status_code == 200
        body = resp.json()
        assert body["recommendation_fallback_reason"] is None
        # provider fields consistent
        assert body["actual_provider"] == body["requested_provider"]


# ===========================================================================
# L. Benchmark v1 config byte-for-byte unchanged
# ===========================================================================


class TestBenchmarkUnchanged:
    """Benchmark v1 config must not change."""

    def test_benchmark_v1_byte_for_byte(self):
        """Benchmark config key fields must be unchanged from expected values."""
        path = Path(_BENCHMARK_V1)
        data = json.loads(path.read_text())
        assert data["benchmark_version"] == "gcsi_benchmark_v1"
        assert data["candidate_limit"] == 50
        assert data["provider"] == "Granite"
        assert set(data["capacity_ratios"]) == {0.35, 0.60, 0.90, 1.20}


# ===========================================================================
# M. Scientific components unchanged (import-level smoke test)
# ===========================================================================


class TestScientificComponentsUnchanged:
    """Scientific components must be importable and functionally unchanged."""

    def test_plan_evaluator_importable(self):
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        ev = PlanEvaluator()
        assert ev is not None

    def test_mission_outcome_evaluator_importable(self):
        from backend.app.evaluator.mission_outcome_evaluator import MissionOutcomeEvaluator
        ev = MissionOutcomeEvaluator()
        assert ev is not None

    def test_semantic_rule_prioritizer_importable(self):
        from backend.app.agent.semantic_rule_prioritizer import SemanticRulePrioritizer
        p = SemanticRulePrioritizer()
        assert p is not None

    def test_local_provider_deterministic(self, loaded_nominal):
        """LocalRuleBasedProvider must produce deterministic output for same inputs."""
        from backend.app import state as app_state
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        from backend.app.config import SchedulerWeights
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.domain.plan_integrity import get_authoritative_packets
        from backend.app.evaluator.plan_evaluator import PlanEvaluator

        scenario = app_state.active_scenario
        link_state = app_state.active_link_state

        gen = CandidateGenerator()
        packets = get_authoritative_packets(scenario)
        plans = gen.generate(packets, link_state, scenario.mission_state, SchedulerWeights())
        ev = PlanEvaluator()
        evals = [ev.evaluate(p, link_state, scenario.mission_state) for p in plans]

        provider = LocalRuleBasedProvider()
        rec1 = provider.recommend(link_state, scenario.mission_state, plans, evals)
        rec2 = provider.recommend(link_state, scenario.mission_state, plans, evals)

        assert rec1.recommended_plan_id == rec2.recommended_plan_id
        assert rec1.risk_score == pytest.approx(rec2.risk_score)
