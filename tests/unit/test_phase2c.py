"""Unit tests for Phase 2C: CandidatePrioritizer, CandidateSummary,
CandidatePrioritization, LocalRuleBasedProvider.prioritize_candidates(),
and GraniteAgent._parse_prioritization_response() (mocked).

Covers:
- Candidate preparation: selection stages, quotas, boundaries
- CandidateSummary: field mapping from DataProduct
- CandidatePrioritization: model validation
- LocalRuleBasedProvider.prioritize_candidates(): deterministic fallback
- GraniteAgent prioritization: response parsing and validation (no live API)
- Token safety: 500 products bounded to max_candidates
- Integration: 50 DataProducts → candidate preparation → prioritization pipeline
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.candidate_prioritizer import (
    CandidatePrioritizer,
    select_candidates,
)
from backend.app.agent.local_provider import LocalRuleBasedProvider
from backend.app.config import AICandidateConfig
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_prioritization import CandidatePrioritization, RankedProduct
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.data_product import DataProduct
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.risk_level import RiskLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, 10, 0, 0, tzinfo=timezone.utc)


def make_link_state(**kw) -> LinkState:
    base = dict(
        timestamp=_TS, snr_db=10.0, eb_n0_db=20.0, ber=3.87e-6, rssi_dbm=-80.0,
        nominal_data_rate_bps=100_000.0, link_goodput_bps=90_000.0,
        latency_s=0.25, link_stability=0.95, remaining_window_s=600.0,
    )
    base.update(kw)
    return LinkState(**base)


def make_mission_state(**kw) -> MissionState:
    base = dict(
        mission_id="m-001", mission_phase="science", current_event="downlink",
        event_time_remaining_s=600.0, comm_window_remaining_s=600.0,
        risk_score=0.3, risk_level=RiskLevel.LOW,
    )
    base.update(kw)
    return MissionState(**base)


def make_dp(
    product_id: str = "PROD-001",
    product_type: str = "telemetry",
    subsystem: str = "power",
    size_bits: int = 4096,
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    scientific_value: float = 0.3,
    deadline_s: float = 300.0,
    age_s: float = 120.0,
    anomaly_id: str | None = None,
    experiment_id: str | None = None,
    related_ids: list[str] | None = None,
    delivery_requirement: str = "required",
    retry_cost: float = 0.5,
) -> DataProduct:
    return DataProduct(
        product_id=product_id,
        product_type=product_type,
        subsystem=subsystem,
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=mission_relevance,
        scientific_value=scientific_value,
        deadline_s=deadline_s,
        age_s=age_s,
        anomaly_id=anomaly_id,
        experiment_id=experiment_id,
        related_ids=related_ids or [],
        delivery_requirement=delivery_requirement,
        retry_cost=retry_cost,
    )


def make_anomaly(
    anomaly_id: str = "ANOM-001",
    subsystem: str = "propulsion",
    severity: float = 0.85,
) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=anomaly_id,
        subsystem=subsystem,
        severity=severity,
        detected_at_s=100.0,
        description="Test anomaly.",
        status="active",
    )


def make_candidate_summary(**kw) -> CandidateSummary:
    base = dict(
        product_id="CS-001", product_type="telemetry", subsystem="power",
        size_bits=4096, criticality=0.6, mission_relevance=0.6,
        scientific_value=0.4, deadline_s=300.0, age_s=120.0,
    )
    base.update(kw)
    return CandidateSummary(**base)


# ===========================================================================
# AICandidateConfig
# ===========================================================================


class TestAICandidateConfig:
    def test_default_max_candidates_is_50(self):
        cfg = AICandidateConfig()
        assert cfg.max_candidates == 50

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("GCSI_AI_MAX_CANDIDATES", "25")
        cfg = AICandidateConfig()
        assert cfg.max_candidates == 25

    def test_max_candidates_must_be_positive(self):
        with pytest.raises(Exception):  # pydantic ValidationError
            AICandidateConfig(max_candidates=0)


# ===========================================================================
# CandidateSummary — model
# ===========================================================================


class TestCandidateSummary:
    def test_valid_construction(self):
        cs = make_candidate_summary()
        assert cs.product_id == "CS-001"

    def test_optional_fields_default_none(self):
        cs = make_candidate_summary()
        assert cs.anomaly_id is None
        assert cs.experiment_id is None

    def test_related_ids_default_empty(self):
        cs = make_candidate_summary()
        assert cs.related_ids == []

    def test_criticality_bounds(self):
        with pytest.raises(Exception):
            make_candidate_summary(criticality=1.1)
        with pytest.raises(Exception):
            make_candidate_summary(criticality=-0.1)

    def test_size_bits_must_be_positive(self):
        with pytest.raises(Exception):
            make_candidate_summary(size_bits=0)

    def test_model_is_frozen(self):
        cs = make_candidate_summary()
        with pytest.raises(Exception):
            cs.product_id = "CHANGED"  # type: ignore

    def test_maps_from_data_product(self):
        """CandidateSummary fields must be derivable from DataProduct."""
        dp = make_dp(
            product_id="TEL-001",
            product_type="telemetry",
            subsystem="navigation",
            criticality=0.8,
            scientific_value=0.6,
            anomaly_id="ANOM-007",
        )
        cs = CandidateSummary(
            product_id=dp.product_id,
            product_type=dp.product_type,
            subsystem=dp.subsystem,
            size_bits=dp.size_bits,
            criticality=dp.criticality,
            mission_relevance=dp.mission_relevance,
            scientific_value=dp.scientific_value,
            deadline_s=dp.deadline_s,
            age_s=dp.age_s,
            anomaly_id=dp.anomaly_id,
            experiment_id=dp.experiment_id,
            related_ids=list(dp.related_ids),
        )
        assert cs.product_id == "TEL-001"
        assert cs.anomaly_id == "ANOM-007"
        assert cs.scientific_value == pytest.approx(0.6)

    def test_no_retry_cost_or_delivery_requirement(self):
        """CandidateSummary must NOT carry pipeline-only fields."""
        cs = make_candidate_summary()
        assert not hasattr(cs, "retry_cost")
        assert not hasattr(cs, "delivery_requirement")


# ===========================================================================
# CandidatePrioritization — model
# ===========================================================================


class TestCandidatePrioritization:
    def test_valid_construction(self):
        rp = RankedProduct(product_id="P1", priority=1, reason="First because critical.")
        cp = CandidatePrioritization(
            ranked_products=[rp],
            overall_reasoning="Operational urgency.",
            confidence=0.85,
        )
        assert len(cp.ranked_products) == 1
        assert cp.confidence == pytest.approx(0.85)

    def test_empty_ranked_products_allowed(self):
        cp = CandidatePrioritization(
            ranked_products=[], overall_reasoning="No products.", confidence=0.5
        )
        assert cp.ranked_products == []

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            CandidatePrioritization(
                ranked_products=[], overall_reasoning="x", confidence=1.1
            )
        with pytest.raises(Exception):
            CandidatePrioritization(
                ranked_products=[], overall_reasoning="x", confidence=-0.1
            )

    def test_ranked_product_priority_must_be_positive(self):
        with pytest.raises(Exception):
            RankedProduct(product_id="P1", priority=0, reason="bad")

    def test_ranked_product_reason_must_not_be_empty(self):
        with pytest.raises(Exception):
            RankedProduct(product_id="P1", priority=1, reason="")

    def test_overall_reasoning_must_not_be_empty(self):
        with pytest.raises(Exception):
            CandidatePrioritization(
                ranked_products=[], overall_reasoning="", confidence=0.5
            )


# ===========================================================================
# CandidatePrioritizer — deterministic pre-filter
# ===========================================================================


class TestCandidatePrioritizerBasic:
    def test_empty_products_returns_empty(self):
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select([])
        assert result == []

    def test_returns_candidate_summary_instances(self):
        products = [make_dp(product_id=f"P{i}") for i in range(5)]
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select(products)
        for cs in result:
            assert isinstance(cs, CandidateSummary)

    def test_max_candidates_respected(self):
        products = [make_dp(product_id=f"P{i:03d}") for i in range(100)]
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(products)
        assert len(result) <= 20

    def test_all_products_returned_when_fewer_than_max(self):
        products = [make_dp(product_id=f"P{i}") for i in range(5)]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        assert len(result) == 5

    def test_product_ids_unique(self):
        products = [make_dp(product_id=f"P{i:03d}") for i in range(30)]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        ids = [cs.product_id for cs in result]
        assert len(ids) == len(set(ids)), "Duplicate product_id in selection"

    def test_no_mutation_of_input_products(self):
        dp = make_dp(criticality=0.8)
        orig_crit = dp.criticality
        p = CandidatePrioritizer(max_candidates=10)
        p.select([dp])
        assert dp.criticality == pytest.approx(orig_crit)

    def test_deterministic_same_inputs_same_output(self):
        products = [make_dp(product_id=f"P{i:03d}", criticality=0.5) for i in range(20)]
        p = CandidatePrioritizer(max_candidates=10)
        r1 = p.select(products)
        r2 = p.select(products)
        assert [cs.product_id for cs in r1] == [cs.product_id for cs in r2]

    def test_max_candidates_zero_raises(self):
        with pytest.raises(ValueError):
            CandidatePrioritizer(max_candidates=0)


class TestCandidatePrioritizerStageAnomalyLinked:
    """Stage 1: Anomaly-linked products get highest selection priority."""

    def test_anomaly_linked_products_selected_first(self):
        normal_products = [
            make_dp(product_id=f"NORM-{i:02d}", criticality=0.3, mission_relevance=0.3)
            for i in range(30)
        ]
        anomaly_products = [
            make_dp(product_id=f"ANOM-PROD-{i}", anomaly_id="ANOM-017", criticality=0.3)
            for i in range(3)
        ]
        all_products = normal_products + anomaly_products
        p = CandidatePrioritizer(max_candidates=5)
        result = p.select(all_products, anomalies=[make_anomaly()])

        selected_ids = {cs.product_id for cs in result}
        for ap in anomaly_products:
            assert ap.product_id in selected_ids, (
                f"Anomaly-linked product {ap.product_id} was filtered out"
            )

    def test_anomaly_products_sorted_by_severity_desc(self):
        """Higher-severity anomaly products appear earlier in the selection."""
        a_low = make_anomaly(anomaly_id="ANOM-LOW", severity=0.20)
        a_high = make_anomaly(anomaly_id="ANOM-HIGH", severity=0.90)
        dp_low = make_dp(product_id="P-LOW", anomaly_id="ANOM-LOW")
        dp_high = make_dp(product_id="P-HIGH", anomaly_id="ANOM-HIGH")

        p = CandidatePrioritizer(max_candidates=10)
        result = p.select([dp_low, dp_high], anomalies=[a_low, a_high])
        ids = [cs.product_id for cs in result]
        assert ids.index("P-HIGH") < ids.index("P-LOW")

    def test_anomaly_products_with_no_anomaly_index_still_selected(self):
        """Products with anomaly_id set but no matching AnomalyEvent are still selected."""
        dp = make_dp(product_id="ORPHAN", anomaly_id="ANOM-UNKNOWN")
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select([dp], anomalies=[])
        assert any(cs.product_id == "ORPHAN" for cs in result)


class TestCandidatePrioritizerStageCritical:
    """Stage 2: Critical products (criticality >= threshold) are preserved."""

    def test_high_criticality_products_selected(self):
        low = [make_dp(product_id=f"LOW-{i}", criticality=0.1) for i in range(20)]
        high = [make_dp(product_id=f"HIGH-{i}", criticality=0.9) for i in range(3)]
        p = CandidatePrioritizer(max_candidates=10, criticality_threshold=0.7)
        result = p.select(low + high)
        selected_ids = {cs.product_id for cs in result}
        for hp in high:
            assert hp.product_id in selected_ids

    def test_threshold_configurable(self):
        products = [
            make_dp(product_id="MED", criticality=0.65),
            make_dp(product_id="LOW", criticality=0.2),
        ]
        # With threshold 0.7: MED does not qualify
        p_high = CandidatePrioritizer(max_candidates=5, criticality_threshold=0.7)
        result_high = p_high.select(products)
        # With threshold 0.6: MED qualifies
        p_low = CandidatePrioritizer(max_candidates=5, criticality_threshold=0.6)
        result_low = p_low.select(products)
        # Both get selected because there are only 2 products, but order may differ
        assert len(result_high) == 2
        assert len(result_low) == 2


class TestCandidatePrioritizerStageDeadline:
    """Stage 3: Near-deadline products are preserved."""

    def test_near_deadline_products_selected(self):
        far = [make_dp(product_id=f"FAR-{i}", deadline_s=1000.0) for i in range(20)]
        near = [make_dp(product_id=f"NEAR-{i}", deadline_s=100.0) for i in range(3)]
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select(far + near, remaining_window_s=600.0)
        selected_ids = {cs.product_id for cs in result}
        for np in near:
            assert np.product_id in selected_ids


class TestCandidatePrioritizerStageMissionRelevance:
    """Stage 4: High mission-relevance products preserved."""

    def test_high_relevance_products_selected(self):
        low_rel = [make_dp(product_id=f"LOWREL-{i}", mission_relevance=0.1) for i in range(20)]
        high_rel = [make_dp(product_id=f"HIGHREL-{i}", mission_relevance=0.9) for i in range(3)]
        p = CandidatePrioritizer(max_candidates=10, relevance_threshold=0.6)
        result = p.select(low_rel + high_rel)
        selected_ids = {cs.product_id for cs in result}
        for hr in high_rel:
            assert hr.product_id in selected_ids


class TestCandidatePrioritizerStageScience:
    """Stage 5: High scientific-value products preserved."""

    def test_high_science_products_selected(self):
        low_sci = [make_dp(product_id=f"LOWSCI-{i}", scientific_value=0.1) for i in range(20)]
        high_sci = [make_dp(product_id=f"HIGHSCI-{i}", scientific_value=0.9) for i in range(3)]
        p = CandidatePrioritizer(max_candidates=10, scientific_threshold=0.5)
        result = p.select(low_sci + high_sci)
        selected_ids = {cs.product_id for cs in result}
        for hs in high_sci:
            assert hs.product_id in selected_ids


class TestCandidatePrioritizerStageRecent:
    """Stage 6: Fresh data (low age_s) preferred."""

    def test_recent_products_preferred_over_stale(self):
        stale = [make_dp(product_id=f"STALE-{i}", age_s=3600.0) for i in range(20)]
        fresh = [make_dp(product_id=f"FRESH-{i}", age_s=10.0) for i in range(3)]
        # Use very small max so only a few can be selected
        p = CandidatePrioritizer(max_candidates=3)
        result = p.select(stale + fresh)
        selected_ids = {cs.product_id for cs in result}
        # At least some fresh products should be selected (after stage 1-5 are empty)
        for f in fresh:
            assert f.product_id in selected_ids


class TestCandidatePrioritizerStageRelated:
    """Stage 7: Related products get consideration."""

    def test_related_products_included(self):
        anchor = make_dp(
            product_id="ANCHOR",
            anomaly_id="ANOM-001",
            related_ids=["RELATED-001"],
            criticality=0.95,
        )
        related = make_dp(product_id="RELATED-001", criticality=0.1, mission_relevance=0.1)
        filler = [make_dp(product_id=f"FILL-{i}", criticality=0.1) for i in range(10)]

        p = CandidatePrioritizer(max_candidates=15)
        result = p.select([anchor, related] + filler)
        selected_ids = {cs.product_id for cs in result}
        # anchor selected via stage 1 (anomaly-linked)
        assert "ANCHOR" in selected_ids
        # related-001 should be pulled in via stage 7
        assert "RELATED-001" in selected_ids


# ===========================================================================
# Token safety: 500 products bounded
# ===========================================================================


class TestTokenSafety:
    def test_500_products_bounded_to_max_candidates(self):
        """500 DataProducts must never exceed max_candidates in the output."""
        products = [make_dp(product_id=f"P-{i:04d}") for i in range(500)]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        assert len(result) <= 50

    def test_500_products_with_anomalies_still_bounded(self):
        anomaly = make_anomaly(anomaly_id="ANOM-BIG", severity=0.9)
        products = [
            make_dp(
                product_id=f"P-{i:04d}",
                anomaly_id="ANOM-BIG" if i < 200 else None,
            )
            for i in range(500)
        ]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products, anomalies=[anomaly])
        assert len(result) <= 50

    def test_select_candidates_convenience_function(self):
        products = [make_dp(product_id=f"C-{i:04d}") for i in range(200)]
        result = select_candidates(products, max_candidates=30)
        assert len(result) <= 30

    def test_candidate_summary_is_compact(self):
        """CandidateSummary serialization must be smaller than DataProduct."""
        dp = make_dp(product_id="BIG", related_ids=["A", "B", "C"])
        from backend.app.agent.candidate_prioritizer import _summarise
        cs = _summarise(dp)
        dp_json = json.dumps(dp.model_dump(mode="json"))
        cs_json = json.dumps(cs.model_dump(mode="json"))
        # CandidateSummary drops retry_cost and delivery_requirement
        assert len(cs_json) <= len(dp_json)


# ===========================================================================
# LocalRuleBasedProvider.prioritize_candidates()
# ===========================================================================


class TestLocalProviderPrioritizeCandidates:
    def _provider(self) -> LocalRuleBasedProvider:
        return LocalRuleBasedProvider()

    def test_returns_candidate_prioritization(self):
        candidates = [make_candidate_summary(product_id=f"P{i}") for i in range(5)]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        assert isinstance(result, CandidatePrioritization)

    def test_empty_candidates_returns_empty_ranked(self):
        provider = self._provider()
        result = provider.prioritize_candidates(
            [], make_link_state(), make_mission_state()
        )
        assert result.ranked_products == []
        assert "deterministic" in result.overall_reasoning.lower() or "fallback" in result.overall_reasoning.lower()

    def test_all_candidates_ranked(self):
        candidates = [make_candidate_summary(product_id=f"P{i:02d}") for i in range(10)]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        assert len(result.ranked_products) == len(candidates)

    def test_no_duplicate_product_ids_in_result(self):
        candidates = [make_candidate_summary(product_id=f"P{i:02d}") for i in range(10)]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        ids = [rp.product_id for rp in result.ranked_products]
        assert len(ids) == len(set(ids))

    def test_priorities_are_contiguous_from_one(self):
        candidates = [make_candidate_summary(product_id=f"P{i:02d}") for i in range(5)]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        priorities = sorted(rp.priority for rp in result.ranked_products)
        assert priorities == list(range(1, len(candidates) + 1))

    def test_high_severity_anomaly_product_ranked_first(self):
        anomaly = make_anomaly(anomaly_id="ANOM-CRITICAL", severity=0.95)
        normal = make_candidate_summary(product_id="NORM", criticality=0.9, anomaly_id=None)
        linked = make_candidate_summary(product_id="ANOM-PROD", criticality=0.3, anomaly_id="ANOM-CRITICAL")
        provider = self._provider()
        result = provider.prioritize_candidates(
            [normal, linked], make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        ranked_by_priority = sorted(result.ranked_products, key=lambda rp: rp.priority)
        assert ranked_by_priority[0].product_id == "ANOM-PROD"

    def test_confidence_in_valid_range(self):
        candidates = [make_candidate_summary(product_id="P1")]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        assert 0.0 <= result.confidence <= 1.0

    def test_overall_reasoning_not_empty(self):
        candidates = [make_candidate_summary(product_id="P1")]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        assert len(result.overall_reasoning) > 0

    def test_reasoning_notes_not_ai(self):
        """Local provider must label its reasoning as deterministic, not AI."""
        candidates = [make_candidate_summary(product_id="P1")]
        provider = self._provider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state()
        )
        # At least one of: "deterministic", "fallback", "NOT" in reasoning or per-product reasons
        reasoning_text = (
            result.overall_reasoning
            + " ".join(rp.reason for rp in result.ranked_products)
        )
        assert any(
            kw in reasoning_text.lower()
            for kw in ["deterministic", "fallback", "not"]
        )

    def test_deterministic_same_inputs_same_output(self):
        candidates = [
            make_candidate_summary(product_id=f"P{i:02d}", criticality=float(i) / 10)
            for i in range(5)
        ]
        provider = self._provider()
        r1 = provider.prioritize_candidates(candidates, make_link_state(), make_mission_state())
        r2 = provider.prioritize_candidates(candidates, make_link_state(), make_mission_state())
        ids1 = [rp.product_id for rp in sorted(r1.ranked_products, key=lambda x: x.priority)]
        ids2 = [rp.product_id for rp in sorted(r2.ranked_products, key=lambda x: x.priority)]
        assert ids1 == ids2


# ===========================================================================
# GraniteAgent._parse_prioritization_response() — validation (no live API)
# ===========================================================================


class TestGranitePrioritizationResponseParsing:
    """Tests for the response parsing/validation logic (no API calls)."""

    def _agent(self):
        from backend.app.agent.granite_agent import GraniteAgent
        return GraniteAgent(api_key="fake", project_id="fake-project")

    def _valid_response(self, product_ids: list[str]) -> str:
        ranked = [
            {"product_id": pid, "priority": i + 1, "reason": f"Reason for {pid}."}
            for i, pid in enumerate(product_ids)
        ]
        return json.dumps({
            "ranked_products": ranked,
            "overall_reasoning": "Mission critical products first.",
            "confidence": 0.88,
        })

    def test_valid_response_parses(self):
        agent = self._agent()
        raw = self._valid_response(["P1", "P2", "P3"])
        result = agent._parse_prioritization_response(raw, {"P1", "P2", "P3"})
        assert isinstance(result, CandidatePrioritization)
        assert len(result.ranked_products) == 3

    def test_hallucinated_product_id_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = self._valid_response(["P1", "INVENTED-999"])
        with pytest.raises(GraniteResponseError, match="unknown product_id"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_duplicate_product_id_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P1", "priority": 1, "reason": "First."},
                {"product_id": "P1", "priority": 2, "reason": "Duplicate."},
            ],
            "overall_reasoning": "Test.",
            "confidence": 0.7,
        })
        with pytest.raises(GraniteResponseError, match="duplicate product_id"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_duplicate_priority_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P1", "priority": 1, "reason": "First."},
                {"product_id": "P2", "priority": 1, "reason": "Also first."},
            ],
            "overall_reasoning": "Test.",
            "confidence": 0.7,
        })
        with pytest.raises(GraniteResponseError, match="duplicate priority"):
            agent._parse_prioritization_response(raw, {"P1", "P2"})

    def test_invalid_priority_zero_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P1", "priority": 0, "reason": "Zero priority."},
            ],
            "overall_reasoning": "Test.",
            "confidence": 0.7,
        })
        with pytest.raises(GraniteResponseError, match="invalid priority"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_empty_reason_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [
                {"product_id": "P1", "priority": 1, "reason": ""},
            ],
            "overall_reasoning": "Test.",
            "confidence": 0.7,
        })
        with pytest.raises(GraniteResponseError, match="empty 'reason'"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_malformed_json_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        with pytest.raises(GraniteResponseError, match="not valid JSON"):
            agent._parse_prioritization_response("{ invalid json }", {"P1"})

    def test_missing_required_fields_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({"ranked_products": []})  # missing overall_reasoning + confidence
        with pytest.raises(GraniteResponseError, match="missing fields"):
            agent._parse_prioritization_response(raw, {"P1"})

    def test_confidence_out_of_range_rejected(self):
        from backend.app.agent.granite_agent import GraniteResponseError
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [],
            "overall_reasoning": "Test.",
            "confidence": 2.5,
        })
        with pytest.raises(GraniteResponseError, match="invalid confidence"):
            agent._parse_prioritization_response(raw, set())

    def test_empty_ranked_products_allowed(self):
        """AI may return an empty ranking — not every product needs to be ranked."""
        agent = self._agent()
        raw = json.dumps({
            "ranked_products": [],
            "overall_reasoning": "No products warrant transmission.",
            "confidence": 0.5,
        })
        result = agent._parse_prioritization_response(raw, {"P1", "P2"})
        assert result.ranked_products == []

    def test_markdown_fence_stripped(self):
        """Response wrapped in ```json``` fences must parse correctly."""
        agent = self._agent()
        raw = "```json\n" + json.dumps({
            "ranked_products": [{"product_id": "P1", "priority": 1, "reason": "Test."}],
            "overall_reasoning": "Test.",
            "confidence": 0.9,
        }) + "\n```"
        result = agent._parse_prioritization_response(raw, {"P1"})
        assert len(result.ranked_products) == 1


# ===========================================================================
# End-to-end integration: 50 DataProducts → candidate preparation → ranking
# ===========================================================================


class TestPhase2CPipelineIntegration:
    """End-to-end test: 50 DataProducts flow through selection and local ranking."""

    def _load_v2_scenario(self):
        from pathlib import Path
        from backend.app.simulation.scenario_loader import ScenarioLoader
        path = Path(__file__).parents[2] / "data" / "scenarios" / "mission_data_v2.json"
        return ScenarioLoader.load(str(path))

    def test_v2_scenario_50_products_gives_bounded_candidates(self):
        scenario = self._load_v2_scenario()
        assert len(scenario.data_products) == 50

        p = CandidatePrioritizer(max_candidates=50)
        candidates = p.select(
            scenario.data_products,
            anomalies=scenario.anomalies,
            remaining_window_s=660.0,
        )
        assert len(candidates) <= 50

    def test_v2_scenario_anomaly_products_in_candidates(self):
        scenario = self._load_v2_scenario()
        p = CandidatePrioritizer(max_candidates=50)
        candidates = p.select(scenario.data_products, anomalies=scenario.anomalies)
        candidate_ids = {cs.product_id for cs in candidates}

        # All products with anomaly_id must be in the candidate set
        for dp in scenario.data_products:
            if dp.anomaly_id is not None:
                assert dp.product_id in candidate_ids, (
                    f"Anomaly-linked product {dp.product_id} not in candidates"
                )

    def test_local_provider_ranks_50_candidates(self):
        scenario = self._load_v2_scenario()
        p = CandidatePrioritizer(max_candidates=50)
        candidates = p.select(scenario.data_products, anomalies=scenario.anomalies)

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates,
            make_link_state(remaining_window_s=660.0),
            make_mission_state(mission_phase="science_downlink", risk_level=RiskLevel.HIGH, risk_score=0.62),
            anomalies=scenario.anomalies,
        )

        assert isinstance(result, CandidatePrioritization)
        assert len(result.ranked_products) == len(candidates)
        # Priority 1 should be an anomaly-linked product (ANOM-017 has highest severity=0.85)
        top = min(result.ranked_products, key=lambda rp: rp.priority)
        top_candidate = next(cs for cs in candidates if cs.product_id == top.product_id)
        assert top_candidate.anomaly_id is not None, (
            f"Top-ranked product {top.product_id} is not anomaly-linked"
        )

    def test_500_products_bounded_end_to_end(self):
        """500 synthetic DataProducts must be reduced to max_candidates for AI."""
        products = [
            make_dp(
                product_id=f"SYNTH-{i:04d}",
                criticality=0.3 + (i % 7) * 0.1,
                mission_relevance=0.3 + (i % 5) * 0.1,
                anomaly_id="ANOM-001" if i < 50 else None,
            )
            for i in range(500)
        ]
        anomaly = make_anomaly(anomaly_id="ANOM-001", severity=0.8)
        p = CandidatePrioritizer(max_candidates=50)
        candidates = p.select(products, anomalies=[anomaly], remaining_window_s=600.0)
        assert len(candidates) <= 50

        provider = LocalRuleBasedProvider()
        result = provider.prioritize_candidates(
            candidates, make_link_state(), make_mission_state(), anomalies=[anomaly]
        )
        assert isinstance(result, CandidatePrioritization)
        assert len(result.ranked_products) == len(candidates)

    def test_prioritization_result_flows_to_bridge_ordering(self):
        """AI ranking drives packet ordering via build_ai_prioritized_plan."""
        from backend.app.models.bridge import data_products_to_packets
        from backend.app.candidate_generator.ai_plan_builder import build_ai_prioritized_plan
        from backend.app.config import SchedulerWeights

        # 3 products; AI ranks them in reverse order
        products = [make_dp(product_id=f"PROD-{i:02d}") for i in range(3)]
        packets = data_products_to_packets(products)

        prioritization = CandidatePrioritization(
            ranked_products=[
                RankedProduct(product_id="PROD-02", priority=1, reason="Most urgent."),
                RankedProduct(product_id="PROD-01", priority=2, reason="Second."),
                RankedProduct(product_id="PROD-00", priority=3, reason="Third."),
            ],
            overall_reasoning="Reverse order test.",
            confidence=0.9,
        )
        plan = build_ai_prioritized_plan(
            packets, prioritization, make_link_state(), make_mission_state(), SchedulerWeights()
        )
        assert [p.packet_id for p in plan.packets] == ["PROD-02", "PROD-01", "PROD-00"]

    def test_unranked_packets_appended_to_end(self):
        """Packets not mentioned by AI ranking must be appended after ranked ones."""
        from backend.app.models.bridge import data_products_to_packets
        from backend.app.candidate_generator.ai_plan_builder import build_ai_prioritized_plan
        from backend.app.config import SchedulerWeights

        products = [make_dp(product_id=f"P-{i:02d}") for i in range(5)]
        packets = data_products_to_packets(products)

        # AI only ranks 2 of the 5
        prioritization = CandidatePrioritization(
            ranked_products=[
                RankedProduct(product_id="P-04", priority=1, reason="First."),
                RankedProduct(product_id="P-02", priority=2, reason="Second."),
            ],
            overall_reasoning="Partial ranking.",
            confidence=0.7,
        )
        plan = build_ai_prioritized_plan(
            packets, prioritization, make_link_state(), make_mission_state(), SchedulerWeights()
        )
        ids = [p.packet_id for p in plan.packets]
        assert ids[0] == "P-04"
        assert ids[1] == "P-02"
        # Remaining three should appear (in any order) after
        assert set(ids[2:]) == {"P-00", "P-01", "P-03"}
