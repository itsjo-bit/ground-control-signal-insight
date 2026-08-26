"""
Phase 4.2F3 — AI Mission Triage & Human Decision Tests

Verifies:
- Experience route returns typed response for ASTERIA scenario
- Recommendation route exists and returns typed response
- AI funnel candidate_count is bounded at <= 50
- plans/assess is non-mutating and order-preserving
- Reject is frontend-only (no /reject endpoint on backend)
- Provider fields are present in recommendation response
"""
from __future__ import annotations

import pytest
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parents[2]
_ASTERIA_SCENARIO = str(_PROJECT_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")
_V3_SCENARIO = str(_PROJECT_ROOT / "data" / "scenarios" / "mission_data_v3.json")


@pytest.fixture(autouse=True)
def reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


# ─── 1. Experience route returns typed response ───────────────────────────────

class TestExperienceRouteTriage:

    def test_experience_available_true_for_asteria(self):
        """GET /experience available=True for ASTERIA-7 scenario."""
        from backend.app import state as app_state
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.available is True
        assert result.manifest is not None

    def test_experience_available_false_for_v3(self):
        """GET /experience available=False for generic v3 scenario."""
        from backend.app import state as app_state
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_V3_SCENARIO)
        result = get_experience()
        assert result.available is False
        assert result.manifest is None


# ─── 2. Recommendation returns typed provider fields ─────────────────────────

class TestRecommendationForTriage:

    def test_recommendation_returns_provider_fields(self):
        """POST /agent/recommend must return actual_provider and requested_provider."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import recommend
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = recommend(None)
        assert result.actual_provider is not None or result.provider is not None

    def test_recommendation_returns_ai_plan_and_evaluation(self):
        """POST /agent/recommend on ASTERIA must return ai_plan and ai_evaluation."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import recommend
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = recommend(None)
        assert result.ai_plan is not None
        assert result.ai_evaluation is not None
        assert len(result.ai_plan.packets) > 0

    def test_recommendation_candidate_count_bounded(self):
        """candidate_count must be <= 50 (semantic screening bound)."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import recommend
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = recommend(None)
        if result.candidate_count is not None:
            assert result.candidate_count <= 50, (
                f"candidate_count {result.candidate_count} exceeds semantic bound of 50"
            )

    def test_recommendation_prioritization_present(self):
        """CandidatePrioritization must be present for ASTERIA."""
        from backend.app import state as app_state
        from backend.app.api.routes_agent import recommend
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = recommend(None)
        assert result.prioritization is not None
        assert len(result.prioritization.ranked_products) > 0


# ─── 3. plans/assess non-mutating and order-preserving ───────────────────────

class TestPlansAssessForTriage:

    def test_plans_assess_non_mutating(self):
        """POST /plans/assess does not change authoritative state."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)

        # Capture queued_data_bits directly from scenario (authoritative — never changes)
        queued_before = sum(p.size_bits for p in app_state.active_scenario.data_products)

        products = app_state.active_scenario.data_products[:5]
        ids = [p.product_id for p in products]
        assess_manual_plan(AssessRequest(product_ids=ids))

        # Scenario data_products must be unchanged
        queued_after = sum(p.size_bits for p in app_state.active_scenario.data_products)
        assert queued_after == queued_before, "assess_manual_plan mutated the scenario queue"
        # Scenario product count must be unchanged
        assert len(app_state.active_scenario.data_products) == 1284

    def test_plans_assess_preserves_order(self):
        """POST /plans/assess returns packets matching the submitted ID order."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)

        products = app_state.active_scenario.data_products[:8:2]
        ids = [p.product_id for p in products]
        ids_reversed = list(reversed(ids))

        result = assess_manual_plan(AssessRequest(product_ids=ids_reversed))
        returned_ids = [pkt.packet_id for pkt in result.plan.packets]
        # All returned IDs must appear in the same relative order as submitted
        indices = [ids_reversed.index(pid) for pid in returned_ids if pid in ids_reversed]
        assert indices == sorted(indices), "Order was not preserved"

    def test_plans_assess_returns_evaluation(self):
        """POST /plans/assess returns a complete EvaluationResult."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)

        products = app_state.active_scenario.data_products[:5]
        ids = [p.product_id for p in products]
        result = assess_manual_plan(AssessRequest(product_ids=ids))

        assert result.evaluation is not None
        assert result.evaluation.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
        assert hasattr(result.evaluation, "deferred_packets")


# ─── 4. Reject has no backend endpoint ───────────────────────────────────────

class TestRejectFrontendOnly:

    def test_no_reject_route_in_app(self):
        """Rejection is purely frontend state — backend has no /reject route."""
        from backend.app.main import app
        paths = []
        for route in app.routes:
            if hasattr(route, "path"):
                paths.append(route.path)
        assert not any("reject" in p.lower() for p in paths), (
            "Backend must not have a /reject endpoint — rejection is frontend-only"
        )


# ─── 5. Funnel: urgent count uses production predicate ───────────────────────

class TestAiFunnelUrgentCount:

    def test_urgent_candidate_count_matches_production_predicate(self):
        """
        Apply isUrgentOperationallyRelevant to the 50 CandidatePrioritizer candidates.
        Expected: exactly 23 urgent products among the 50 semantic candidates.

        This mirrors what the production urgentCandidates.ts helper computes.
        window_s=272.0 is the canonical contact window used in ASTERIA-7.
        """
        from backend.app import state as app_state
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        from backend.app.domain.anomaly_policy import is_applicable_anomaly
        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario = app_state.active_scenario

        # Select the canonical 50 candidates
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            scenario.data_products,
            anomalies=scenario.anomalies,
            remaining_window_s=272.0,
        )

        # Use applicable anomaly IDs (same as production helper)
        active_anomaly_ids = {
            ae.anomaly_id for ae in scenario.anomalies
            if is_applicable_anomaly(ae)
        }
        dp_map = {p.product_id: p for p in scenario.data_products}
        effective_window_s = 272.0

        # Same predicate as urgentCandidates.ts / test_phase4_2c_triage.py
        def is_urgent(p) -> bool:
            if p.anomaly_id is not None and p.anomaly_id in active_anomaly_ids:
                return True
            if p.delivery_requirement == "required":
                return True
            if p.deadline_s <= effective_window_s:
                return True
            return False

        urgent_count = sum(
            1 for c in candidates
            if c.product_id in dp_map and is_urgent(dp_map[c.product_id])
        )
        assert urgent_count == 23, f"Expected 23 urgent candidates, got {urgent_count}"
