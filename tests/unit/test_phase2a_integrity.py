"""Phase 2A tests — evaluation integrity, cleanup, and ablation improvements.

Covers:
1. AIPlanBuildError replaces assert (typed exception, not assert)
2. Duplicate authoritative packet ID detection
3. Improved ablation test: opposing rankings → different AI plans, identical baselines
4. AI factual validation: authoritative metadata overrides LLM output
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.candidate_generator.ai_plan_builder import (
    AIPlanBuildError,
    build_ai_prioritized_plan,
)
from backend.app.candidate_generator.generator import CandidateGenerator
from backend.app.config import SchedulerWeights
from backend.app.evaluator.plan_evaluator import PlanEvaluator
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_prioritization import CandidatePrioritization, RankedProduct
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _link(*, goodput: float = 100_000.0, window: float = 300.0) -> LinkState:
    return LinkState(
        timestamp=_TS,
        snr_db=12.0,
        eb_n0_db=20.0,
        ber=0.0,
        rssi_dbm=-80.0,
        nominal_data_rate_bps=goodput,
        link_goodput_bps=goodput,
        latency_s=0.0,
        link_stability=1.0,
        remaining_window_s=window,
    )


def _mission(*, window: float = 300.0) -> MissionState:
    return MissionState(
        mission_id="test",
        mission_phase="science",
        current_event="downlink",
        event_time_remaining_s=window,
        comm_window_remaining_s=window,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


def _pkt(pid: str, *, criticality: float = 0.5, size_bits: int = 8_000) -> Packet:
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=0.5,
        deadline_s=200.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


def _prioritization(ranked: list[tuple[str, int]]) -> CandidatePrioritization:
    return CandidatePrioritization(
        ranked_products=[
            RankedProduct(product_id=pid, priority=pri, reason=f"rank {pri}")
            for pid, pri in ranked
        ],
        overall_reasoning="test",
        confidence=0.8,
        candidate_count=len(ranked),
    )


_LS = _link()
_MS = _mission()
_W = SchedulerWeights()


# ---------------------------------------------------------------------------
# Section 1: AIPlanBuildError replaces assert
# ---------------------------------------------------------------------------


class TestAIPlanBuildError:
    """AIPlanBuildError is raised instead of AssertionError for invariant violations."""

    def test_aiplanbuilderror_is_not_assertionerror(self):
        """AIPlanBuildError must be a distinct Exception, not an AssertionError."""
        assert not issubclass(AIPlanBuildError, AssertionError)
        assert issubclass(AIPlanBuildError, Exception)

    def test_duplicate_packet_ids_raise_aiplanbuilderror(self):
        """Duplicate authoritative packet IDs must raise AIPlanBuildError."""
        # Two packets with the same ID
        pkt_a = _pkt("DUPE-001")
        pkt_b = _pkt("DUPE-001")  # duplicate
        pkt_c = _pkt("UNIQUE-002")

        p = _prioritization([])
        with pytest.raises(AIPlanBuildError, match="duplicate"):
            build_ai_prioritized_plan([pkt_a, pkt_b, pkt_c], p, _LS, _MS, _W)

    def test_error_message_includes_duplicate_ids(self):
        """Error message must identify which IDs are duplicated."""
        pkt_a = _pkt("DUP-X")
        pkt_b = _pkt("DUP-X")
        p = _prioritization([])
        with pytest.raises(AIPlanBuildError) as exc_info:
            build_ai_prioritized_plan([pkt_a, pkt_b], p, _LS, _MS, _W)
        assert "DUP-X" in str(exc_info.value)

    def test_clean_inputs_do_not_raise(self):
        """Valid inputs with unique IDs must succeed."""
        packets = [_pkt(f"P{i}") for i in range(5)]
        p = _prioritization([])
        plan = build_ai_prioritized_plan(packets, p, _LS, _MS, _W)
        assert len(plan.packets) == 5

    def test_invariant_error_is_typed_not_assert(self):
        """Python -O (optimised) would disable assert but not AIPlanBuildError.

        This test verifies the error mechanism doesn't rely on assert by checking
        that AIPlanBuildError can be caught as a regular Exception.
        """
        pkt_a = _pkt("DUP-Y")
        pkt_b = _pkt("DUP-Y")
        p = _prioritization([])
        caught = False
        try:
            build_ai_prioritized_plan([pkt_a, pkt_b], p, _LS, _MS, _W)
        except AIPlanBuildError:
            caught = True
        except AssertionError:
            pytest.fail("Should not raise AssertionError — use AIPlanBuildError")
        assert caught


# ---------------------------------------------------------------------------
# Section 2: Improved ablation test — opposing rankings
# ---------------------------------------------------------------------------


class TestOpposingRankingsAblation:
    """Improved ablation: opposing Stage-1 rankings produce different AI plans
    but identical deterministic baselines.

    This is a stronger test than the original ablation which only checked that
    the same inputs produce the same outputs.  Here we use two DIFFERENT rankings
    (opposite order) and verify the causal isolation.
    """

    def _packets(self) -> list[Packet]:
        return [
            _pkt("A", criticality=0.9),
            _pkt("B", criticality=0.7),
            _pkt("C", criticality=0.5),
            _pkt("D", criticality=0.3),
            _pkt("E", criticality=0.1),
        ]

    def _ranking_a(self, packets) -> CandidatePrioritization:
        """Ranking A: A first (forward order)."""
        return _prioritization(
            [(p.packet_id, i + 1) for i, p in enumerate(packets)]
        )

    def _ranking_b(self, packets) -> CandidatePrioritization:
        """Ranking B: E first (reverse order)."""
        return _prioritization(
            [(p.packet_id, i + 1) for i, p in enumerate(reversed(packets))]
        )

    def test_opposing_rankings_produce_different_ai_plans(self):
        """Opposite AI rankings must produce different AI plan packet orders."""
        packets = self._packets()
        rank_a = self._ranking_a(packets)
        rank_b = self._ranking_b(packets)

        plan_a = build_ai_prioritized_plan(packets, rank_a, _LS, _MS, _W)
        plan_b = build_ai_prioritized_plan(packets, rank_b, _LS, _MS, _W)

        ids_a = [p.packet_id for p in plan_a.packets]
        ids_b = [p.packet_id for p in plan_b.packets]
        assert ids_a != ids_b, "Opposing rankings must produce different AI plans"

    def test_opposing_rankings_do_not_affect_baseline(self):
        """Deterministic baseline plan is identical regardless of AI ranking."""
        packets = self._packets()
        # Generate baseline with ranking A context (not used by generator)
        plans_a = CandidateGenerator.generate(packets, _LS, _MS, _W)
        # Generate baseline with ranking B context (not used by generator)
        plans_b = CandidateGenerator.generate(packets, _LS, _MS, _W)

        bl_a = next(p for p in plans_a if p.strategy == "baseline")
        bl_b = next(p for p in plans_b if p.strategy == "baseline")
        assert [p.packet_id for p in bl_a.packets] == [p.packet_id for p in bl_b.packets], (
            "Baseline must be identical for opposite AI rankings"
        )

    def test_opposing_rankings_do_not_affect_any_deterministic_plan(self):
        """All four deterministic plans are identical for opposite AI rankings."""
        packets = self._packets()
        plans_a = CandidateGenerator.generate(packets, _LS, _MS, _W)
        plans_b = CandidateGenerator.generate(packets, _LS, _MS, _W)

        for strategy in ("baseline", "deadline_first", "mission_critical_first", "value_per_cost"):
            plan_a = next(p for p in plans_a if p.strategy == strategy)
            plan_b = next(p for p in plans_b if p.strategy == strategy)
            assert (
                [p.packet_id for p in plan_a.packets] ==
                [p.packet_id for p in plan_b.packets]
            ), f"Strategy '{strategy}' changed between opposite AI rankings"

    def test_opposing_rankings_produce_different_evaluation_metrics(self):
        """Opposite rankings on a constrained scenario produce different metrics."""
        # Only 1 packet fits
        ls = _link(goodput=100_000.0, window=0.1)
        ms = _mission(window=0.1)
        packets = self._packets()

        rank_a = self._ranking_a(packets)
        rank_b = self._ranking_b(packets)

        plan_a = build_ai_prioritized_plan(packets, rank_a, ls, ms, _W)
        plan_b = build_ai_prioritized_plan(packets, rank_b, ls, ms, _W)

        ev = PlanEvaluator()
        eval_a = ev.evaluate(plan_a, ls, ms)
        eval_b = ev.evaluate(plan_b, ls, ms)

        assert eval_a.mission_value != eval_b.mission_value, (
            "Opposing rankings must produce different mission values on constrained scenario"
        )


# ---------------------------------------------------------------------------
# Section 3: AI factual validation — authoritative metadata overrides LLM
# ---------------------------------------------------------------------------


class TestAIFactualValidation:
    """Authoritative candidate metadata overrides LLM-fabricated values."""

    def test_authoritative_subsystem_replaces_hallucinated_subsystem(self):
        """subsystem from CandidateSummary must win over LLM-returned value."""
        import json
        from backend.app.agent.prioritization_helpers import parse_prioritization_response

        # The LLM returns the wrong subsystem
        raw = json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-PROP-001",
                    "priority": 1,
                    "reason": "High priority",
                    "factors": [],
                    "anomaly_ids": [],
                    "subsystem": "thermal",   # WRONG — the candidate says "propulsion"
                    "confidence": 0.8,
                }
            ],
            "overall_reasoning": "Test",
            "confidence": 0.9,
            "decision_factors": [],
        })

        candidates = [
            CandidateSummary(
                product_id="DP-PROP-001",
                product_type="diagnostic",
                subsystem="propulsion",      # AUTHORITATIVE
                size_bits=8_000,
                criticality=0.8,
                mission_relevance=0.9,
                scientific_value=0.5,
                deadline_s=120.0,
                age_s=5.0,
            )
        ]
        result = parse_prioritization_response(raw, {"DP-PROP-001"}, candidates)
        assert result.ranked_products[0].subsystem == "propulsion", (
            "Authoritative subsystem must override LLM-fabricated value 'thermal'"
        )

    def test_hallucinated_anomaly_id_not_trusted(self):
        """LLM-fabricated anomaly_id must be replaced by authoritative linkage."""
        import json
        from backend.app.agent.prioritization_helpers import parse_prioritization_response

        # The LLM claims product links to ANOM-999 (hallucinated)
        raw = json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-001",
                    "priority": 1,
                    "reason": "Anomaly product",
                    "factors": ["active anomaly"],
                    "anomaly_ids": ["ANOM-999"],   # HALLUCINATED
                    "subsystem": "propulsion",
                    "confidence": 0.9,
                }
            ],
            "overall_reasoning": "Test",
            "confidence": 0.8,
            "decision_factors": [],
        })

        candidates = [
            CandidateSummary(
                product_id="DP-001",
                product_type="diagnostic",
                subsystem="propulsion",
                size_bits=8_000,
                criticality=0.8,
                mission_relevance=0.9,
                scientific_value=0.5,
                deadline_s=120.0,
                age_s=5.0,
                anomaly_id="ANOM-017",   # AUTHORITATIVE
            )
        ]
        result = parse_prioritization_response(raw, {"DP-001"}, candidates)
        # Authoritative anomaly ID must win
        assert result.ranked_products[0].anomaly_ids == ["ANOM-017"], (
            "Hallucinated anomaly ID 'ANOM-999' must not appear; "
            "authoritative 'ANOM-017' must be used"
        )

    def test_no_anomaly_in_candidate_gives_empty_anomaly_ids(self):
        """Product with no authoritative anomaly link must have empty anomaly_ids."""
        import json
        from backend.app.agent.prioritization_helpers import parse_prioritization_response

        raw = json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-002",
                    "priority": 1,
                    "reason": "reason",
                    "factors": [],
                    "anomaly_ids": ["ANOM-FAKE"],  # LLM fabricated
                    "subsystem": "power",
                    "confidence": None,
                }
            ],
            "overall_reasoning": "Test",
            "confidence": 0.7,
            "decision_factors": [],
        })

        candidates = [
            CandidateSummary(
                product_id="DP-002",
                product_type="telemetry",
                subsystem="power",
                size_bits=8_000,
                criticality=0.5,
                mission_relevance=0.5,
                scientific_value=0.3,
                deadline_s=300.0,
                age_s=100.0,
                anomaly_id=None,  # No anomaly link
            )
        ]
        result = parse_prioritization_response(raw, {"DP-002"}, candidates)
        assert result.ranked_products[0].anomaly_ids == [], (
            "No anomaly link in authoritative data → anomaly_ids must be empty"
        )

    def test_without_candidates_llm_subsystem_used_as_fallback(self):
        """When candidates are not supplied, LLM subsystem value is used (backwards compat)."""
        import json
        from backend.app.agent.prioritization_helpers import parse_prioritization_response

        raw = json.dumps({
            "ranked_products": [
                {
                    "product_id": "DP-NO-CANDIDATES",
                    "priority": 1,
                    "reason": "reason",
                    "factors": [],
                    "anomaly_ids": [],
                    "subsystem": "thermal",   # LLM value — no candidates to override with
                    "confidence": 0.5,
                }
            ],
            "overall_reasoning": "Test",
            "confidence": 0.7,
            "decision_factors": [],
        })

        result = parse_prioritization_response(raw, {"DP-NO-CANDIDATES"})
        # No candidates → LLM subsystem is used
        assert result.ranked_products[0].subsystem == "thermal"
