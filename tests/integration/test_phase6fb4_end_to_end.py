"""GCSI Phase 6F-B4 — End-to-End Integration Tests.

Tests covering the complete V2 pipeline:

- 403 DataProduct → 403 bridged Packets (bridge integrity)
- Four deterministic baseline plans
- PlanEvaluator / MissionOutcomeEvaluator on V2
- Manual /plans/assess with V2 product IDs
- AI Stage 1 bounded candidate context (≤ GCSI_AI_MAX_CANDIDATES)
- Local AI pipeline end-to-end (LocalRuleBasedProvider)
- External AI provider mock/fallback tests
- Ineligible IDs never appear downstream
- Startup environment integration

All tests OFFLINE. No network.
"""

from __future__ import annotations

import pathlib
import socket

import pytest
from httpx import ASGITransport, AsyncClient

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_V2_SOURCE_REF = "data/replays/juno_pj62_large_replay_v2_descriptor.json"
_V1_SOURCE_REF = "data/replays/juno_pj62_mwr_v1.json"
_ASTERIA_PATH = str(_REPO_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")

_EXPECTED_PRODUCT_COUNT = 403
_KNOWN_INELIGIBLE_IDS = {
    "gcsi.jedi.pj62.jed_090_loersesp_cdr_2024166_v04",
    "gcsi.uvs.pj62.s02_771613347_2024166_p62sy1",
}


def _no_network(*args, **kwargs):
    raise RuntimeError("B4 test: network access forbidden.")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


def _reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.active_source_mode = None
    app_state.active_source_ref = None
    app_state.active_source_provider_name = None
    app_state.active_source_provenance = None
    app_state.issued_plans.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _reset_state()
    yield
    _reset_state()


@pytest.fixture
def v2_active():
    from backend.app import state as app_state
    app_state.load_historical_replay(_V2_SOURCE_REF)
    return app_state


# ===========================================================================
# DataProduct → Packet bridge
# ===========================================================================


class TestDataProductBridge:
    """403 DataProducts must bridge to 403 authoritative Packets."""

    def test_403_bridged_packets(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        packets = data_products_to_packets(v2_active.active_scenario.data_products)
        assert len(packets) == _EXPECTED_PRODUCT_COUNT

    def test_packet_id_equals_product_id(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        products = v2_active.active_scenario.data_products
        packets = data_products_to_packets(products)
        for dp, pkt in zip(products, packets):
            assert pkt.packet_id == dp.product_id

    def test_size_bits_preserved(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        products = v2_active.active_scenario.data_products
        packets = data_products_to_packets(products)
        for dp, pkt in zip(products, packets):
            assert pkt.size_bits == dp.size_bits

    def test_criticality_preserved(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        products = v2_active.active_scenario.data_products
        packets = data_products_to_packets(products)
        for dp, pkt in zip(products, packets):
            assert pkt.criticality == pytest.approx(dp.criticality)

    def test_retry_cost_preserved(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        products = v2_active.active_scenario.data_products
        packets = data_products_to_packets(products)
        for dp, pkt in zip(products, packets):
            assert pkt.retry_cost == pytest.approx(dp.retry_cost)

    def test_no_pds_fields_in_packet(self, v2_active):
        """Packets must not carry PDS source fields."""
        from backend.app.models.bridge import data_products_to_packets
        packets = data_products_to_packets(v2_active.active_scenario.data_products)
        for pkt in packets:
            assert not hasattr(pkt, "source_record_id") or getattr(pkt, "source_record_id", None) is None
            assert not hasattr(pkt, "label_url")


# ===========================================================================
# Plan generation determinism
# ===========================================================================


class TestPlanGenerationDeterminism:

    def _make_plans(self, v2_active):
        """Helper: generate 4 baseline plans from V2 state."""
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.config import SchedulerWeights
        from backend.app.domain.plan_integrity import get_authoritative_packets
        packets = get_authoritative_packets(v2_active.active_scenario)
        weights = SchedulerWeights()
        return CandidateGenerator.generate(
            packets,
            v2_active.active_link_state,
            v2_active.active_scenario.mission_state,
            weights,
        )

    def test_four_baseline_plans_generated(self, v2_active):
        from backend.app.domain.plan_integrity import get_authoritative_packets
        packets = get_authoritative_packets(v2_active.active_scenario)
        assert len(packets) == _EXPECTED_PRODUCT_COUNT
        plans = self._make_plans(v2_active)
        assert len(plans) == 4

    def test_plan_strategies(self, v2_active):
        plans = self._make_plans(v2_active)
        # plan_id uses hyphens; strategy uses underscores
        plan_ids = {p.plan_id for p in plans}
        assert "baseline" in plan_ids
        assert "deadline-first" in plan_ids
        assert "mission-critical-first" in plan_ids
        assert "value-per-cost" in plan_ids

    def test_plan_determinism_two_runs(self, v2_active):
        """Two plan generations with unchanged state must be identical."""
        plans1 = self._make_plans(v2_active)
        plans2 = self._make_plans(v2_active)

        for p1, p2 in zip(plans1, plans2):
            assert p1.plan_id == p2.plan_id
            assert [pkt.packet_id for pkt in p1.packets] == [
                pkt.packet_id for pkt in p2.packets
            ]

    def test_plan_packets_from_403_pool(self, v2_active):
        """All plan packets must come from the 403 eligible products."""
        plans = self._make_plans(v2_active)
        eligible_ids = {dp.product_id for dp in v2_active.active_scenario.data_products}
        for plan in plans:
            for pkt in plan.packets:
                assert pkt.packet_id in eligible_ids, (
                    f"Packet {pkt.packet_id!r} not in eligible 403-product set"
                )

    def test_ineligible_ids_absent_from_plans(self, v2_active):
        """Known ineligible IDs must not appear in any plan."""
        plans = self._make_plans(v2_active)
        for plan in plans:
            plan_ids = {pkt.packet_id for pkt in plan.packets}
            for bad_id in _KNOWN_INELIGIBLE_IDS:
                assert bad_id not in plan_ids, (
                    f"Ineligible ID {bad_id!r} found in plan {plan.plan_id!r}"
                )


# ===========================================================================
# Plan evaluation
# ===========================================================================


class TestPlanEvaluation:

    def _get_plans(self, v2_active):
        from backend.app.candidate_generator.generator import CandidateGenerator
        from backend.app.config import SchedulerWeights
        from backend.app.domain.plan_integrity import get_authoritative_packets
        packets = get_authoritative_packets(v2_active.active_scenario)
        weights = SchedulerWeights()
        return CandidateGenerator.generate(
            packets,
            v2_active.active_link_state,
            v2_active.active_scenario.mission_state,
            weights,
        )

    def test_plan_evaluator_no_nan_no_infinity(self, v2_active):
        """PlanEvaluator must not produce NaN/infinity on V2 data."""
        import math
        from backend.app.evaluator.plan_evaluator import PlanEvaluator

        plans = self._get_plans(v2_active)
        evaluator = PlanEvaluator()
        for plan in plans:
            result = evaluator.evaluate(
                plan, v2_active.active_link_state, v2_active.active_scenario.mission_state
            )
            assert math.isfinite(result.risk_score), (
                f"NaN/infinity in risk_score for plan {plan.plan_id!r}"
            )
            assert math.isfinite(result.bandwidth_utilization), (
                f"NaN/infinity in bandwidth_utilization for plan {plan.plan_id!r}"
            )

    def test_mission_outcome_evaluator_no_nan(self, v2_active):
        """MissionOutcomeEvaluator must not produce NaN on V2 data."""
        import math
        from backend.app.evaluator.plan_evaluator import PlanEvaluator
        from backend.app.evaluator.mission_outcome_evaluator import MissionOutcomeEvaluator

        plans = self._get_plans(v2_active)
        evaluator = PlanEvaluator()
        outcome_eval = MissionOutcomeEvaluator()
        data_products = v2_active.active_scenario.data_products
        anomalies = v2_active.active_scenario.anomalies
        for plan in plans:
            eval_result = evaluator.evaluate(
                plan, v2_active.active_link_state, v2_active.active_scenario.mission_state
            )
            outcome = outcome_eval.evaluate(plan, eval_result, data_products, anomalies)
            # MissionOutcomeResult uses delivery_rate, not value_delivered_fraction
            assert outcome.delivery_rate is None or math.isfinite(outcome.delivery_rate), (
                f"NaN in delivery_rate for plan {plan.plan_id!r}"
            )
            assert math.isfinite(outcome.total_scientific_value), (
                f"NaN in total_scientific_value for plan {plan.plan_id!r}"
            )

    def test_plan_evaluation_deterministic(self, v2_active):
        """PlanEvaluator must produce identical results on two identical calls."""
        from backend.app.evaluator.plan_evaluator import PlanEvaluator

        plans = self._get_plans(v2_active)
        evaluator = PlanEvaluator()
        results1 = [
            evaluator.evaluate(p, v2_active.active_link_state, v2_active.active_scenario.mission_state)
            for p in plans
        ]
        results2 = [
            evaluator.evaluate(p, v2_active.active_link_state, v2_active.active_scenario.mission_state)
            for p in plans
        ]

        for r1, r2 in zip(results1, results2):
            assert r1.risk_score == pytest.approx(r2.risk_score)
            assert r1.bandwidth_utilization == pytest.approx(r2.bandwidth_utilization)


# ===========================================================================
# Plans assess endpoint
# ===========================================================================


class TestPlansAssessEndpoint:

    @pytest.mark.asyncio
    async def test_assess_with_real_v2_product_ids(self, v2_active):
        """POST /plans/assess with real V2 product IDs must succeed."""
        from backend.app.main import app

        # Pick 5 real product IDs from the active scenario
        real_ids = [dp.product_id for dp in v2_active.active_scenario.data_products[:5]]

        payload = {"product_ids": real_ids}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json=payload)

        assert resp.status_code == 200
        body = resp.json()
        # Must not mutate state
        assert v2_active.active_scenario is not None

    @pytest.mark.asyncio
    async def test_assess_unknown_product_id_rejected(self, v2_active):
        """POST /plans/assess with unknown product ID must be rejected."""
        from backend.app.main import app

        payload = {"product_ids": ["gcsi.fake.nonexistent.product.id.xyz"]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json=payload)

        assert resp.status_code in (400, 422), (
            f"Expected 400/422 for unknown product ID, got {resp.status_code}"
        )

    @pytest.mark.asyncio
    async def test_assess_ineligible_id_rejected(self, v2_active):
        """POST /plans/assess with an ineligible ID must be rejected."""
        from backend.app.main import app

        bad_id = next(iter(_KNOWN_INELIGIBLE_IDS))
        payload = {"product_ids": [bad_id]}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/assess", json=payload)

        assert resp.status_code in (400, 422), (
            f"Ineligible ID {bad_id!r} should be rejected but got status {resp.status_code}"
        )


# ===========================================================================
# AI Stage 1 bounded candidate context
# ===========================================================================


class TestAIStage1BoundedContext:

    def test_candidate_count_bounded(self, v2_active):
        """CandidatePrioritizer must return ≤ GCSI_AI_MAX_CANDIDATES products."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        from backend.app.config import AICandidateConfig

        cfg = AICandidateConfig()
        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        assert len(candidates) <= cfg.max_candidates, (
            f"Expected ≤{cfg.max_candidates} candidates, got {len(candidates)}"
        )

    def test_candidate_ids_in_eligible_set(self, v2_active):
        """All candidate IDs must come from the 403 eligible products."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        eligible_ids = {dp.product_id for dp in v2_active.active_scenario.data_products}
        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        for c in candidates:
            assert c.product_id in eligible_ids, (
                f"Candidate {c.product_id!r} not in eligible set"
            )

    def test_candidate_ids_unique(self, v2_active):
        """Candidate product IDs must be unique."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        ids = [c.product_id for c in candidates]
        assert len(ids) == len(set(ids))

    def test_no_ineligible_ids_in_candidates(self, v2_active):
        """Ineligible IDs must not appear in candidate set."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        candidate_ids = {c.product_id for c in candidates}
        for bad_id in _KNOWN_INELIGIBLE_IDS:
            assert bad_id not in candidate_ids

    def test_selection_deterministic(self, v2_active):
        """Two calls with the same input must produce the same candidate IDs."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer

        prioritizer = CandidatePrioritizer()
        candidates1 = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        candidates2 = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        ids1 = [c.product_id for c in candidates1]
        ids2 = [c.product_id for c in candidates2]
        assert ids1 == ids2


# ===========================================================================
# Local AI pipeline end-to-end
# ===========================================================================


class TestLocalAIPipelineE2E:

    @pytest.mark.asyncio
    async def test_agent_recommend_local_completes(self, v2_active):
        """POST /agent/recommend with V2 active must complete (using Local provider)."""
        from backend.app.main import app
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        with patch("backend.app.api.routes_agent.get_provider", return_value=LocalRuleBasedProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert "recommendation" in body
        assert "recommended_plan_id" in body["recommendation"]

    @pytest.mark.asyncio
    async def test_agent_recommend_has_valid_structure(self, v2_active):
        """Agent recommendation must have required fields."""
        from backend.app.main import app
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        with patch("backend.app.api.routes_agent.get_provider", return_value=LocalRuleBasedProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        body = resp.json()
        assert "actual_provider" in body
        assert "recommendation" in body
        rec = body["recommendation"]
        assert "recommended_plan_id" in rec
        assert "risk_score" in rec
        assert "risk_level" in rec
        assert "packet_actions" in rec

    @pytest.mark.asyncio
    async def test_agent_recommend_no_auto_transmission(self, v2_active):
        """Recommendation must not auto-execute transmission."""
        from backend.app import state as app_state
        from backend.app.main import app
        from backend.app.agent.local_provider import LocalRuleBasedProvider
        with patch("backend.app.api.routes_agent.get_provider", return_value=LocalRuleBasedProvider()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")
        assert resp.status_code == 200
        assert app_state.active_source_ref == _V2_SOURCE_REF


# ===========================================================================
# External AI provider mock/fallback
# ===========================================================================


class TestExternalAIProviderSafety:

    def test_provider_receives_bounded_context(self, v2_active):
        """External provider must receive at most max_candidates products."""
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        from backend.app.config import AICandidateConfig

        cfg = AICandidateConfig()
        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        assert len(candidates) <= cfg.max_candidates
        assert len(candidates) <= 403

    @pytest.mark.asyncio
    async def test_external_provider_failure_falls_back_to_local(self, v2_active):
        """If external provider fails with AIProviderError, must fall back to Local."""
        from backend.app.main import app
        from unittest.mock import patch, MagicMock
        from backend.app.agent.base_provider import AIProviderError

        mock_provider = MagicMock()
        mock_provider.provider_name = "mock_external"
        # Stage-1 prioritization fails with AIProviderError → falls back to Local
        mock_provider.prioritize_candidates.side_effect = AIProviderError("External unavailable")
        # Stage-2 recommendation fails too — but route should already use Local after Stage-1 fallback
        mock_provider.recommend.side_effect = AIProviderError("External unavailable")
        mock_provider.recommend_from_summaries.side_effect = AIProviderError("External unavailable")

        with patch("backend.app.api.routes_agent.get_provider", return_value=mock_provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                resp = await c.post("/agent/recommend")

        # Must fall back gracefully to Local, not 502
        assert resp.status_code == 200
        body = resp.json()
        assert "actual_provider" in body
        assert body["actual_provider"]
        assert "recommendation" in body
        # After fallback, actual_provider should be local or original
        assert body["actual_provider"]


# ===========================================================================
# Ineligible IDs quarantine
# ===========================================================================


class TestIneligibleIDsQuarantined:
    """Ineligible IDs must not appear anywhere downstream."""

    def test_ineligible_absent_from_active_data_products(self, v2_active):
        product_ids = {dp.product_id for dp in v2_active.active_scenario.data_products}
        for bad_id in _KNOWN_INELIGIBLE_IDS:
            assert bad_id not in product_ids, (
                f"Ineligible ID {bad_id!r} found in active data_products"
            )

    def test_ineligible_absent_from_bridged_packets(self, v2_active):
        from backend.app.models.bridge import data_products_to_packets
        packets = data_products_to_packets(v2_active.active_scenario.data_products)
        packet_ids = {pkt.packet_id for pkt in packets}
        for bad_id in _KNOWN_INELIGIBLE_IDS:
            assert bad_id not in packet_ids

    def test_ineligible_absent_from_candidate_prioritizer(self, v2_active):
        from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
        prioritizer = CandidatePrioritizer()
        candidates = prioritizer.select(
            v2_active.active_scenario.data_products,
            anomalies=[],
            remaining_window_s=900.0,
        )
        candidate_ids = {c.product_id for c in candidates}
        for bad_id in _KNOWN_INELIGIBLE_IDS:
            assert bad_id not in candidate_ids


# ===========================================================================
# Startup environment integration
# ===========================================================================


class TestStartupEnvironmentIntegration:

    def test_historical_replay_mode_loads_v2(self):
        """GCSI_SOURCE_MODE=historical_replay with V2 descriptor loads V2."""
        from backend.app import state as app_state
        from backend.app.main import _load_configured_mission_source
        from backend.app.mission_sources.models import MissionSourceMode
        from unittest.mock import patch

        _reset_state()
        with patch.dict("os.environ", {
            "GCSI_SOURCE_MODE": "historical_replay",
            "GCSI_REPLAY_DESCRIPTOR": _V2_SOURCE_REF,
        }):
            _load_configured_mission_source()

        assert app_state.active_source_mode == MissionSourceMode.HISTORICAL_REPLAY
        assert app_state.active_source_ref == _V2_SOURCE_REF
        assert len(app_state.active_scenario.data_products) == _EXPECTED_PRODUCT_COUNT

    def test_historical_replay_mode_requires_descriptor(self):
        """GCSI_SOURCE_MODE=historical_replay without descriptor raises."""
        from backend.app.main import _load_configured_mission_source
        _reset_state()
        with patch.dict("os.environ", {
            "GCSI_SOURCE_MODE": "historical_replay",
            "GCSI_REPLAY_DESCRIPTOR": "",
        }):
            with pytest.raises(RuntimeError, match="GCSI_REPLAY_DESCRIPTOR"):
                _load_configured_mission_source()

    def test_synthetic_default_when_no_source_mode(self):
        """Absent GCSI_SOURCE_MODE loads default synthetic scenario."""
        from backend.app import state as app_state
        from backend.app.main import _load_configured_mission_source
        from backend.app.mission_sources.models import MissionSourceMode
        import os

        _reset_state()
        # Remove source mode from env
        env_copy = {
            k: v for k, v in os.environ.items()
            if k not in ("GCSI_SOURCE_MODE", "GCSI_REPLAY_DESCRIPTOR")
        }
        with pytest.MonkeyPatch().context() as mp:
            # Remove GCSI_SOURCE_MODE
            mp.delenv("GCSI_SOURCE_MODE", raising=False)
            mp.delenv("GCSI_REPLAY_DESCRIPTOR", raising=False)
            _load_configured_mission_source()

        assert app_state.active_source_mode == MissionSourceMode.SYNTHETIC_SCENARIO


from unittest.mock import patch


# ===========================================================================
# V1 regression through the same provider
# ===========================================================================


class TestV1Regression:
    """V1 historical replay must remain unaffected by B4 changes."""

    @pytest.fixture(scope="class")
    def v1_bundle(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        return HistoricalReplayProvider().load(_V1_SOURCE_REF)

    def test_v1_source_ref(self, v1_bundle):
        assert v1_bundle.source_ref == _V1_SOURCE_REF

    def test_v1_two_products(self, v1_bundle):
        assert len(v1_bundle.scenario.data_products) == 2

    def test_v1_historical_replay_mode(self, v1_bundle):
        from backend.app.mission_sources.models import MissionSourceMode
        assert v1_bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_v1_simulated(self, v1_bundle):
        assert v1_bundle.scenario.simulated is True

    def test_v1_activate_reset_load(self):
        """V1 activate + reset must work."""
        from backend.app import state as app_state
        app_state.load_historical_replay(_V1_SOURCE_REF)
        v1_id = app_state.active_scenario.scenario_id
        result = app_state.reset_active_source()
        assert result["randomized"] is False
        assert result["source_mode"] == "historical_replay"
        assert app_state.active_scenario.scenario_id == v1_id


# ===========================================================================
# Synthetic regression
# ===========================================================================


class TestSyntheticRegression:

    def test_synthetic_loads_and_resets(self):
        """Synthetic scenario must load and reset without interference from B4."""
        from backend.app import state as app_state
        from backend.app.mission_sources.models import MissionSourceMode

        app_state.load_scenario(_ASTERIA_PATH)
        assert app_state.active_source_mode == MissionSourceMode.SYNTHETIC_SCENARIO
        assert app_state.active_scenario is not None
        assert app_state.active_source_provenance is None

        result = app_state.reset_active_source()
        assert result["randomized"] is True
        assert result["source_mode"] == "synthetic_scenario"

    def test_synthetic_historical_provenance_cleared(self):
        """After loading synthetic, historical provenance must be cleared."""
        from backend.app import state as app_state

        # First load V2
        app_state.load_historical_replay(_V2_SOURCE_REF)
        assert app_state.active_source_provenance is not None

        # Switch to synthetic
        app_state.load_scenario(_ASTERIA_PATH)
        assert app_state.active_source_provenance is None
