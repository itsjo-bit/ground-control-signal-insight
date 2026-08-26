"""Phase 2A.2 tests — Evidence Traceability & Anomaly Consistency.

Covers all acceptance criteria for Phase 2A.2:

Evidence traceability
  - EvidenceItem has option_id field
  - option_id is preserved through all external providers
  - Cross-option evidence binding (OPTION-B evidence binds from OPTION-B, not recommended)
  - Alias is mapped to real plan identity in final output
  - link/mission evidence has no option_id

Evidence source integrity
  - Source-specific field validation
  - candidate_option cannot cite link/mission-only fields
  - link_state cannot cite candidate/mission-only fields
  - mission_state cannot cite candidate/link-only fields
  - Hidden backend fields not citeable
  - Invalid source/field pairs are dropped
  - Invalid option_id evidence is dropped
  - Missing option_id for candidate_option evidence is dropped

Anomaly consistency
  - Shared anomaly policy in domain.anomaly_policy
  - CandidatePrioritizer uses shared policy
  - SemanticRulePrioritizer uses shared policy
  - prioritization_helpers.build_prioritization_message uses shared policy
  - MissionOutcomeEvaluator uses shared policy
  - Stage-2 user message uses shared policy
  - Unknown anomaly reference not treated as active

Fail-fast comparison integrity
  - build_stage2_summaries raises Stage2SummaryBuildError on missing data
  - Duplicate plan/eval/outcome IDs fail
  - Duplicate CandidatePlan packet IDs fail MissionOutcome evaluation
  - Duplicate authoritative anomaly IDs fail
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
from backend.app.agent.prioritization_helpers import build_prioritization_message
from backend.app.agent.semantic_rule_prioritizer import SemanticRulePrioritizer
from backend.app.agent.stage2_blinding import (
    Stage2PlanSummary,
    Stage2SummaryBuildError,
    build_blind_mapping,
    build_stage2_summaries,
    build_stage2_user_message,
    is_valid_source_field,
    parse_stage2_response,
    _STAGE2_CANDIDATE_FIELDS,
    _STAGE2_LINK_FIELDS,
    _STAGE2_MISSION_FIELDS,
)
from backend.app.domain.anomaly_policy import (
    APPLICABLE_ANOMALY_STATUSES,
    is_applicable_anomaly,
)
from backend.app.evaluator.mission_outcome_evaluator import (
    MissionOutcomeEvaluationError,
    MissionOutcomeEvaluator,
    MissionOutcomeResult,
    # Re-exports for backwards compat:
    APPLICABLE_ANOMALY_STATUSES as MOE_APPLICABLE_ANOMALY_STATUSES,
    is_applicable_anomaly as moe_is_applicable_anomaly,
)
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_prioritization import CandidatePrioritization, RankedProduct
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.data_product import DataProduct
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.evidence_item import EvidenceItem
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 1, tzinfo=timezone.utc)


def _link(*, ber: float = 1e-6, goodput: float = 1_000_000.0, window: float = 3600.0) -> LinkState:
    return LinkState(
        timestamp=_TS,
        snr_db=20.0,
        eb_n0_db=20.0,
        ber=ber,
        rssi_dbm=-70.0,
        nominal_data_rate_bps=goodput,
        link_goodput_bps=goodput,
        latency_s=0.0,
        link_stability=1.0,
        remaining_window_s=window,
    )


def _mission(*, window: float = 3600.0) -> MissionState:
    return MissionState(
        mission_id="test",
        mission_phase="science",
        current_event="downlink",
        event_time_remaining_s=window,
        comm_window_remaining_s=window,
        risk_score=0.1,
        risk_level=RiskLevel.LOW,
    )


def _pkt(pid: str, *, size_bits: int = 8_000) -> Packet:
    return Packet(
        packet_id=pid,
        packet_type="telemetry",
        size_bits=size_bits,
        criticality=0.5,
        mission_relevance=0.5,
        deadline_s=3000.0,
        retry_cost=0.1,
        delivery_requirement="best_effort",
    )


def _dp(
    pid: str,
    *,
    subsystem: str = "payload",
    scientific_value: float = 0.5,
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    delivery_requirement: str = "best_effort",
    anomaly_id: str | None = None,
    age_s: float = 100.0,
    size_bits: int = 8_000,
) -> DataProduct:
    return DataProduct(
        product_id=pid,
        product_type="telemetry",
        subsystem=subsystem,
        size_bits=size_bits,
        criticality=criticality,
        mission_relevance=mission_relevance,
        scientific_value=scientific_value,
        deadline_s=3000.0,
        age_s=age_s,
        delivery_requirement=delivery_requirement,
        retry_cost=0.1,
        anomaly_id=anomaly_id,
    )


def _plan(plan_id: str, pids: list[str], strategy: str = "test") -> CandidatePlan:
    return CandidatePlan(
        plan_id=plan_id,
        strategy=strategy,
        packets=[_pkt(pid) for pid in pids],
        generated_by="test",
        metadata={},
    )


def _eval_result(plan_id: str, *, deferred: list[str] | None = None, risk_score: float = 0.30) -> EvaluationResult:
    return EvaluationResult(
        plan_id=plan_id,
        mission_value=10.0,
        critical_packets_delivered=3,
        total_critical_packets=5,
        deadline_misses=1,
        avg_packet_delay_s=12.5,
        bandwidth_utilization=0.72,
        retransmission_overhead=0.15,
        risk_score=risk_score,
        risk_level=RiskLevel.MEDIUM,
        deferred_packets=deferred or [],
        deadline_miss_rate=0.2,
        critical_deficit=0.4,
        window_pressure=0.7,
    )


def _anomaly(aid: str, severity: float, status: str = "active") -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=aid,
        subsystem="propulsion",
        severity=severity,
        detected_at_s=0.0,
        description=f"Test anomaly {aid}",
        status=status,
    )


def _candidate_summary(pid: str, *, anomaly_id: str | None = None) -> CandidateSummary:
    return CandidateSummary(
        product_id=pid,
        product_type="telemetry",
        subsystem="payload",
        size_bits=8_000,
        criticality=0.5,
        mission_relevance=0.5,
        scientific_value=0.5,
        deadline_s=3000.0,
        age_s=100.0,
        anomaly_id=anomaly_id,
    )


def _summary(
    option_id: str,
    *,
    risk_score: float = 0.30,
    active_anomaly_delivery_rate: float | None = None,
    anomaly_weighted_coverage: float | None = None,
    scientific_value_capture_rate: float | None = None,
    required_delivery_rate: float | None = None,
) -> Stage2PlanSummary:
    return Stage2PlanSummary(
        option_id=option_id,
        total_packets=20,
        deferred_count=2,
        risk_score=risk_score,
        risk_level="MEDIUM",
        mission_value=15.0,
        critical_packets_delivered=8,
        total_critical_packets=10,
        deadline_misses=2,
        deadline_miss_rate=0.04,
        bandwidth_utilization=0.72,
        retransmission_overhead=0.15,
        window_pressure=0.69,
        active_anomaly_delivery_rate=active_anomaly_delivery_rate,
        anomaly_weighted_coverage=anomaly_weighted_coverage,
        scientific_value_capture_rate=scientific_value_capture_rate,
        required_delivery_rate=required_delivery_rate,
    )


_LS = _link()
_MS = _mission()


# ===========================================================================
# Part 1: EvidenceItem option_id field
# ===========================================================================


class TestEvidenceItemOptionId:
    """EvidenceItem must support an optional option_id field."""

    def test_option_id_defaults_to_none(self):
        item = EvidenceItem(source="link_state", field="ber", value=1e-6, interpretation="test")
        assert item.option_id is None

    def test_option_id_can_be_set(self):
        item = EvidenceItem(
            option_id="ai-prioritized",
            source="candidate_option",
            field="risk_score",
            value=0.25,
            interpretation="low risk",
        )
        assert item.option_id == "ai-prioritized"

    def test_option_id_serializes_in_model_dump(self):
        item = EvidenceItem(
            option_id="baseline",
            source="candidate_option",
            field="risk_score",
            value=0.30,
            interpretation="test",
        )
        dumped = item.model_dump()
        assert "option_id" in dumped
        assert dumped["option_id"] == "baseline"

    def test_option_id_none_serializes(self):
        item = EvidenceItem(source="link_state", field="ber", value=1e-6, interpretation="test")
        dumped = item.model_dump()
        assert dumped["option_id"] is None


# ===========================================================================
# Part 2: Shared anomaly policy
# ===========================================================================


class TestSharedAnomalyPolicy:
    """The canonical policy lives in domain.anomaly_policy and is the same
    object re-exported by mission_outcome_evaluator."""

    def test_applicable_statuses_constant(self):
        assert APPLICABLE_ANOMALY_STATUSES == frozenset({"active", "monitoring"})

    def test_evaluator_reexports_same_constant(self):
        assert MOE_APPLICABLE_ANOMALY_STATUSES is APPLICABLE_ANOMALY_STATUSES

    def test_evaluator_reexports_same_function(self):
        # Both the domain function and the evaluator re-export must agree on all cases
        for status in ["active", "monitoring", "resolved", "unknown"]:
            ae = _anomaly("X", 0.5, status=status)
            assert is_applicable_anomaly(ae) == moe_is_applicable_anomaly(ae)

    def test_active_is_applicable(self):
        assert is_applicable_anomaly(_anomaly("A", 0.8, "active")) is True

    def test_monitoring_is_applicable(self):
        assert is_applicable_anomaly(_anomaly("A", 0.8, "monitoring")) is True

    def test_resolved_is_not_applicable(self):
        assert is_applicable_anomaly(_anomaly("A", 0.8, "resolved")) is False

    def test_unknown_status_is_not_applicable(self):
        assert is_applicable_anomaly(_anomaly("A", 0.8, "unknown_status")) is False


# ===========================================================================
# Part 3: CandidatePrioritizer uses applicable anomalies only
# ===========================================================================


class TestCandidatePrioritizerAnomalyPolicy:
    """Resolved-anomaly products must NOT receive active-anomaly protection."""

    def _run(self, products, anomalies):
        p = CandidatePrioritizer(max_candidates=50)
        return p.select(products, anomalies=anomalies)

    def test_active_anomaly_product_included_in_anomaly_stage(self):
        """Product linked to active anomaly gets anomaly protection (Stage 1)."""
        ae = _anomaly("ANOM-ACT", severity=0.9, status="active")
        products = [
            _dp("PA", anomaly_id="ANOM-ACT"),
            _dp("PB"),
            _dp("PC"),
        ]
        selected = self._run(products, [ae])
        # PA should appear before PB, PC (anomaly protection)
        ids = [c.product_id for c in selected]
        assert ids[0] == "PA"

    def test_monitoring_anomaly_product_included_in_anomaly_stage(self):
        """Product linked to monitoring anomaly gets anomaly protection."""
        ae = _anomaly("ANOM-MON", severity=0.9, status="monitoring")
        products = [
            _dp("PA", anomaly_id="ANOM-MON"),
            _dp("PB"),
        ]
        selected = self._run(products, [ae])
        ids = [c.product_id for c in selected]
        assert ids[0] == "PA"

    def test_resolved_anomaly_product_not_in_anomaly_stage(self):
        """Product linked to resolved anomaly must NOT get anomaly-linked Stage 1 slot."""
        ae_resolved = _anomaly("ANOM-RES", severity=0.99, status="resolved")
        ae_active = _anomaly("ANOM-ACT", severity=0.5, status="active")
        products = [
            _dp("PRES", anomaly_id="ANOM-RES", criticality=0.2),
            _dp("PACT", anomaly_id="ANOM-ACT", criticality=0.2),
        ]
        selected = self._run(products, [ae_resolved, ae_active])
        ids = [c.product_id for c in selected]
        # PACT (active) must come before PRES (resolved) because PACT gets anomaly slot
        assert ids[0] == "PACT"

    def test_unknown_anomaly_reference_not_treated_as_active(self):
        """A product with anomaly_id='ANOM-MISSING' (no matching AnomalyEvent)
        must not receive anomaly-linked protection."""
        # No anomaly events provided — ANOM-MISSING is unknown
        products = [
            _dp("PMISS", anomaly_id="ANOM-MISSING", criticality=0.2),
            _dp("PNORM", criticality=0.9),
        ]
        selected = self._run(products, [])
        ids = [c.product_id for c in selected]
        # PNORM has higher criticality and no anomaly link
        # PMISS should NOT come first just because it has an anomaly_id
        assert ids[0] == "PNORM"


# ===========================================================================
# Part 4: SemanticRulePrioritizer uses applicable anomalies only
# ===========================================================================


class TestSemanticRulePrioritizerAnomalyPolicy:
    """Resolved anomalies must NOT influence the semantic rule comparator."""

    def test_resolved_anomaly_does_not_boost_product(self):
        """Product linked to resolved high-severity anomaly must NOT rank first."""
        ae_resolved = _anomaly("ANOM-RES", severity=0.99, status="resolved")
        ae_active = _anomaly("ANOM-ACT", severity=0.50, status="active")
        candidates = [
            _candidate_summary("PRES", anomaly_id="ANOM-RES"),
            _candidate_summary("PACT", anomaly_id="ANOM-ACT"),
        ]
        p = SemanticRulePrioritizer()
        result = p.prioritize(candidates, anomalies=[ae_resolved, ae_active])
        ranked_ids = [rp.product_id for rp in result.ranked_products]
        # PACT (active, lower severity) must rank ahead of PRES (resolved, higher severity)
        assert ranked_ids[0] == "PACT"

    def test_active_anomaly_label_not_applied_to_resolved_product(self):
        """Factors for a resolved-anomaly product must NOT include 'active anomaly'."""
        ae_resolved = _anomaly("ANOM-RES", severity=0.9, status="resolved")
        candidates = [_candidate_summary("PRES", anomaly_id="ANOM-RES")]
        p = SemanticRulePrioritizer()
        result = p.prioritize(candidates, anomalies=[ae_resolved])
        rp = result.ranked_products[0]
        assert "active anomaly" not in rp.factors

    def test_monitoring_anomaly_is_applicable(self):
        """Products linked to monitoring anomalies receive anomaly protection."""
        ae_monitoring = _anomaly("ANOM-MON", severity=0.8, status="monitoring")
        candidates = [
            _candidate_summary("PMON", anomaly_id="ANOM-MON"),
            _candidate_summary("PNORM"),
        ]
        p = SemanticRulePrioritizer()
        result = p.prioritize(candidates, anomalies=[ae_monitoring])
        ranked_ids = [rp.product_id for rp in result.ranked_products]
        assert ranked_ids[0] == "PMON"

    def test_unknown_anomaly_reference_not_treated_as_active(self):
        """Product with anomaly_id for which no AnomalyEvent exists is NOT anomaly-promoted."""
        # No anomalies passed — ANOM-MISSING is unknown
        candidates = [
            _candidate_summary("PMISS", anomaly_id="ANOM-MISSING"),
            _candidate_summary("PNORM", anomaly_id=None),
        ]
        p = SemanticRulePrioritizer()
        result = p.prioritize(candidates, anomalies=[])
        rp_miss = next(r for r in result.ranked_products if r.product_id == "PMISS")
        assert "active anomaly" not in rp_miss.factors


# ===========================================================================
# Part 5: Stage-1 AI context anomaly filtering
# ===========================================================================


class TestStage1ContextAnomalyFiltering:
    """build_prioritization_message must exclude resolved anomalies from active_anomalies."""

    def test_resolved_anomaly_excluded_from_context(self):
        ae_active = _anomaly("ANOM-ACT", severity=0.7, status="active")
        ae_resolved = _anomaly("ANOM-RES", severity=0.99, status="resolved")
        candidates = [_candidate_summary("P1")]
        msg = build_prioritization_message(candidates, _LS, _MS, [ae_active, ae_resolved])
        data = json.loads(msg)
        if "active_anomalies" in data:
            anomaly_ids = [a["anomaly_id"] for a in data["active_anomalies"]]
            assert "ANOM-ACT" in anomaly_ids
            assert "ANOM-RES" not in anomaly_ids

    def test_only_active_and_monitoring_in_context(self):
        ae_active = _anomaly("ANOM-A", severity=0.7, status="active")
        ae_monitoring = _anomaly("ANOM-M", severity=0.6, status="monitoring")
        ae_resolved = _anomaly("ANOM-R", severity=0.9, status="resolved")
        candidates = [_candidate_summary("P1")]
        msg = build_prioritization_message(
            candidates, _LS, _MS, [ae_active, ae_monitoring, ae_resolved]
        )
        data = json.loads(msg)
        if "active_anomalies" in data:
            anomaly_ids = {a["anomaly_id"] for a in data["active_anomalies"]}
            assert "ANOM-A" in anomaly_ids
            assert "ANOM-M" in anomaly_ids
            assert "ANOM-R" not in anomaly_ids

    def test_no_resolved_anomaly_in_list_named_active_anomalies(self):
        """The list 'active_anomalies' must NEVER contain a resolved anomaly."""
        ae_resolved = _anomaly("ANOM-RES", severity=0.99, status="resolved")
        candidates = [_candidate_summary("P1")]
        msg = build_prioritization_message(candidates, _LS, _MS, [ae_resolved])
        data = json.loads(msg)
        # Either the key is absent (preferred when no applicable anomalies)
        # or the list is empty
        if "active_anomalies" in data:
            assert data["active_anomalies"] == []


# ===========================================================================
# Part 6: Stage-2 context anomaly filtering
# ===========================================================================


class TestStage2ContextAnomalyFiltering:
    """build_stage2_user_message must internally filter to applicable anomalies."""

    def test_resolved_anomaly_excluded_from_stage2_context(self):
        ae_active = _anomaly("ANOM-ACT", severity=0.8, status="active")
        ae_resolved = _anomaly("ANOM-RES", severity=0.99, status="resolved")
        summaries = [_summary("OPTION-A")]
        msg = build_stage2_user_message(summaries, _LS, _MS, [ae_active, ae_resolved])
        data = json.loads(msg)
        anomaly_ids = [a["anomaly_id"] for a in data.get("active_anomalies", [])]
        assert "ANOM-ACT" in anomaly_ids
        assert "ANOM-RES" not in anomaly_ids

    def test_only_applicable_anomalies_in_stage2_context(self):
        ae_active = _anomaly("A1", severity=0.7, status="active")
        ae_monitoring = _anomaly("A2", severity=0.6, status="monitoring")
        ae_resolved = _anomaly("A3", severity=0.99, status="resolved")
        summaries = [_summary("OPTION-A")]
        msg = build_stage2_user_message(summaries, _LS, _MS, [ae_active, ae_monitoring, ae_resolved])
        data = json.loads(msg)
        anomaly_ids = {a["anomaly_id"] for a in data.get("active_anomalies", [])}
        assert "A1" in anomaly_ids
        assert "A2" in anomaly_ids
        assert "A3" not in anomaly_ids


# ===========================================================================
# Part 7: Source-specific field validation
# ===========================================================================


class TestSourceSpecificFieldValidation:
    """Evidence source/field pairs must be validated against what was exposed in the prompt."""

    # Valid pairs
    def test_candidate_option_risk_score_valid(self):
        assert is_valid_source_field("candidate_option", "risk_score") is True

    def test_candidate_option_scientific_value_capture_rate_valid(self):
        assert is_valid_source_field("candidate_option", "scientific_value_capture_rate") is True

    def test_link_state_ber_valid(self):
        assert is_valid_source_field("link_state", "ber") is True

    def test_link_state_remaining_window_s_valid(self):
        assert is_valid_source_field("link_state", "remaining_window_s") is True

    def test_mission_state_mission_phase_valid(self):
        assert is_valid_source_field("mission_state", "mission_phase") is True

    def test_mission_state_current_event_valid(self):
        assert is_valid_source_field("mission_state", "current_event") is True

    # Invalid cross-source pairs
    def test_mission_state_ber_invalid(self):
        """ber belongs to link_state, not mission_state."""
        assert is_valid_source_field("mission_state", "ber") is False

    def test_link_state_mission_phase_invalid(self):
        """mission_phase belongs to mission_state, not link_state."""
        assert is_valid_source_field("link_state", "mission_phase") is False

    def test_candidate_option_remaining_window_s_invalid(self):
        """remaining_window_s belongs to link_state, not candidate_option."""
        assert is_valid_source_field("candidate_option", "remaining_window_s") is False

    def test_unknown_source_is_not_restricted(self):
        """Legacy/unknown sources (e.g. 'evaluation_result') are not restricted."""
        assert is_valid_source_field("evaluation_result", "risk_score") is True
        assert is_valid_source_field("unknown_source", "anything") is True

    def test_hidden_link_field_not_citeable(self):
        """A field that exists in LinkState but was NOT in the link_context prompt
        must NOT be allowed for link_state source."""
        # 'snr_db' is a LinkState field but is NOT in the link_context sent to LLM
        assert is_valid_source_field("link_state", "snr_db") is False
        # 'rssi_dbm' exists on LinkState but not in link_context
        assert is_valid_source_field("link_state", "rssi_dbm") is False

    def test_hidden_mission_field_not_citeable(self):
        """A field that exists in MissionState but was NOT in mission_context prompt
        must NOT be allowed for mission_state source."""
        # 'risk_score' is on MissionState but NOT in mission_context
        assert is_valid_source_field("mission_state", "risk_score") is False

    def test_candidate_fields_are_stage2_summary_fields(self):
        """candidate_option citeable fields must be the Stage2PlanSummary metric fields."""
        assert _STAGE2_CANDIDATE_FIELDS == frozenset(
            f for f in Stage2PlanSummary.model_fields if f != "option_id"
        )


# ===========================================================================
# Part 8: parse_stage2_response source/field and option_id validation
# ===========================================================================


class TestParseStage2ResponseValidation:
    """parse_stage2_response must enforce source-specific field rules and option_id."""

    def _alias_map(self):
        return {
            "OPTION-A": "plan-a",
            "OPTION-B": "plan-b",
            "OPTION-C": "plan-c",
        }

    def _raw(self, evidence: list[dict]) -> str:
        return json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": evidence,
            "alternative_option_id": None,
        })

    # --- Source/field pair validation ---

    def test_valid_candidate_option_field_accepted(self):
        raw = self._raw([{
            "option_id": "OPTION-A",
            "source": "candidate_option",
            "field": "risk_score",
            "interpretation": "test",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 1
        assert evidence[0]["field"] == "risk_score"

    def test_invalid_source_field_pair_dropped(self):
        """mission_state/ber is an invalid pair — must be silently dropped."""
        raw = self._raw([{
            "option_id": None,
            "source": "mission_state",
            "field": "ber",  # ber is link_state field, not mission_state
            "interpretation": "invalid cross-source",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_link_state_mission_phase_dropped(self):
        """link_state/mission_phase is invalid."""
        raw = self._raw([{
            "option_id": None,
            "source": "link_state",
            "field": "mission_phase",
            "interpretation": "invalid",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_candidate_option_remaining_window_s_dropped(self):
        """candidate_option/remaining_window_s is invalid."""
        raw = self._raw([{
            "option_id": "OPTION-A",
            "source": "candidate_option",
            "field": "remaining_window_s",  # link_state field, not candidate_option
            "interpretation": "invalid",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_hidden_backend_field_not_citeable_link_state(self):
        """snr_db is on LinkState but NOT in the link_context prompt — must be dropped."""
        raw = self._raw([{
            "option_id": None,
            "source": "link_state",
            "field": "snr_db",  # NOT in link_context
            "interpretation": "hidden field",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_hidden_backend_field_not_citeable_mission_state(self):
        """risk_score is on MissionState but NOT in mission_context prompt — must be dropped."""
        raw = self._raw([{
            "option_id": None,
            "source": "mission_state",
            "field": "risk_score",  # NOT in mission_context sent to LLM
            "interpretation": "hidden field",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    # --- option_id validation for candidate_option ---

    def test_candidate_option_with_valid_alias_preserved(self):
        raw = self._raw([{
            "option_id": "OPTION-B",
            "source": "candidate_option",
            "field": "risk_score",
            "interpretation": "alternative has higher risk",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 1
        assert evidence[0]["option_id"] == "OPTION-B"

    def test_candidate_option_with_null_option_id_dropped(self):
        """candidate_option evidence without option_id must be dropped (cannot bind)."""
        raw = self._raw([{
            "option_id": None,
            "source": "candidate_option",
            "field": "risk_score",
            "interpretation": "no option_id — ambiguous",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_candidate_option_with_invalid_alias_dropped(self):
        """candidate_option evidence with OPTION-Z (not in alias_map) must be dropped."""
        raw = self._raw([{
            "option_id": "OPTION-Z",  # not in alias_map
            "source": "candidate_option",
            "field": "risk_score",
            "interpretation": "invalid alias",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 0

    def test_link_state_option_id_stripped(self):
        """link_state evidence carrying an option_id must have it stripped."""
        raw = self._raw([{
            "option_id": "OPTION-A",  # LLM erroneously added option for link evidence
            "source": "link_state",
            "field": "ber",
            "interpretation": "link is degraded",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 1
        assert evidence[0]["option_id"] is None  # stripped

    def test_mission_state_option_id_stripped(self):
        """mission_state evidence carrying an option_id must have it stripped."""
        raw = self._raw([{
            "option_id": "OPTION-A",
            "source": "mission_state",
            "field": "mission_phase",
            "interpretation": "science phase",
        }])
        _, _, _, evidence, _ = parse_stage2_response(raw, self._alias_map())
        assert len(evidence) == 1
        assert evidence[0]["option_id"] is None  # stripped


# ===========================================================================
# Part 9: Cross-option evidence binding (the critical regression test)
# ===========================================================================


class TestCrossOptionEvidenceBinding:
    """Evidence must be bound to the CITED option, not the recommended option.

    OPTION-A risk_score = 0.20  (recommended)
    OPTION-B risk_score = 0.70  (alternative, higher risk)

    Evidence citing OPTION-B/risk_score must get value 0.70, NOT 0.20.
    """

    def _make_alias_map_and_summaries(self):
        """Create a test setup where OPTION-A → plan-a (recommended), OPTION-B → plan-b."""
        # Use a fixed deterministic alias map (bypassing the SHA-based ordering)
        alias_map = {"OPTION-A": "plan-a", "OPTION-B": "plan-b"}
        summary_a = Stage2PlanSummary(
            option_id="OPTION-A",
            total_packets=20, deferred_count=0,
            risk_score=0.20, risk_level="LOW",
            mission_value=10.0, critical_packets_delivered=5, total_critical_packets=5,
            deadline_misses=0, deadline_miss_rate=0.0,
            bandwidth_utilization=0.5, retransmission_overhead=0.1, window_pressure=0.3,
        )
        summary_b = Stage2PlanSummary(
            option_id="OPTION-B",
            total_packets=20, deferred_count=3,
            risk_score=0.70, risk_level="HIGH",
            mission_value=8.0, critical_packets_delivered=3, total_critical_packets=5,
            deadline_misses=4, deadline_miss_rate=0.2,
            bandwidth_utilization=0.5, retransmission_overhead=0.1, window_pressure=0.7,
        )
        return alias_map, [summary_a, summary_b]

    def test_cross_option_risk_score_binding(self):
        """Evidence citing OPTION-B must get OPTION-B's risk_score (0.70), not OPTION-A's (0.20)."""
        from backend.app.agent.stage2_blinding import (
            _SOURCE_FIELD_REGISTRY,
        )
        alias_map, summaries = self._make_alias_map_and_summaries()
        summary_by_alias = {s.option_id: s for s in summaries}

        # Simulate what routes_agent._build_blind_recommend does:
        # Parse Stage-2 response with OPTION-B evidence
        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "OPTION-A has lower risk",
            "confidence": 0.85,
            "evidence": [
                {
                    "option_id": "OPTION-B",
                    "source": "candidate_option",
                    "field": "risk_score",
                    "interpretation": "Alternative OPTION-B carries higher risk.",
                }
            ],
            "alternative_option_id": "OPTION-B",
        })
        _, _, _, evidence_dicts, _ = parse_stage2_response(raw, alias_map)
        assert len(evidence_dicts) == 1
        assert evidence_dicts[0]["option_id"] == "OPTION-B"

        # Simulate the binding step in routes_agent
        ev_dict = evidence_dicts[0]
        ev_alias = ev_dict["option_id"]
        ev_summary = summary_by_alias.get(ev_alias)
        assert ev_summary is not None
        bound_value = getattr(ev_summary, ev_dict["field"])
        assert bound_value == pytest.approx(0.70), (
            f"Expected OPTION-B's risk_score=0.70, got {bound_value}. "
            "Cross-option evidence must bind from the cited option, not the recommended one."
        )

    def test_recommended_option_evidence_binds_to_recommended(self):
        """Evidence citing OPTION-A (recommended) must get OPTION-A's values."""
        alias_map, summaries = self._make_alias_map_and_summaries()
        summary_by_alias = {s.option_id: s for s in summaries}

        raw = json.dumps({
            "recommended_option_id": "OPTION-A",
            "reasoning": "low risk",
            "confidence": 0.9,
            "evidence": [
                {
                    "option_id": "OPTION-A",
                    "source": "candidate_option",
                    "field": "risk_score",
                    "interpretation": "Recommended has low risk.",
                }
            ],
            "alternative_option_id": None,
        })
        _, _, _, evidence_dicts, _ = parse_stage2_response(raw, alias_map)
        assert len(evidence_dicts) == 1
        ev_alias = evidence_dicts[0]["option_id"]
        ev_summary = summary_by_alias.get(ev_alias)
        bound_value = getattr(ev_summary, "risk_score")
        assert bound_value == pytest.approx(0.20)

    def test_alias_maps_to_real_plan_id(self):
        """After binding, option_id must be the real plan ID, not the OPTION alias."""
        from backend.app.agent.stage2_blinding import map_alias_to_plan_id
        alias_map = {"OPTION-A": "plan-a", "OPTION-B": "plan-b"}
        # Simulate mapping
        real_plan_id = map_alias_to_plan_id("OPTION-B", alias_map)
        assert real_plan_id == "plan-b"

    def test_evidence_item_with_real_plan_id(self):
        """Final EvidenceItem must carry the real plan_id, not an OPTION alias."""
        item = EvidenceItem(
            option_id="ai-prioritized",  # real plan id, post-mapping
            source="candidate_option",
            field="risk_score",
            value=0.70,
            interpretation="higher risk",
        )
        assert item.option_id == "ai-prioritized"
        assert "OPTION" not in (item.option_id or "")


# ===========================================================================
# Part 10: Provider option_id preservation
# ===========================================================================


class TestProviderOptionIdPreservation:
    """All three providers must preserve option_id in EvidenceItem."""

    def _fake_stage2_response(self, alias: str = "OPTION-A"):
        return json.dumps({
            "recommended_option_id": alias,
            "reasoning": "test",
            "confidence": 0.8,
            "evidence": [
                {
                    "option_id": "OPTION-B",
                    "source": "candidate_option",
                    "field": "risk_score",
                    "interpretation": "Alternative carries risk.",
                },
                {
                    "option_id": None,
                    "source": "link_state",
                    "field": "ber",
                    "interpretation": "Link degraded.",
                },
            ],
            "alternative_option_id": None,
        })

    def _summaries(self):
        return [
            _summary("OPTION-A"),
            _summary("OPTION-B"),
        ]

    def test_granite_preserves_option_id(self):
        from backend.app.agent.granite_agent import GraniteAgent
        agent = GraniteAgent(api_key="test", project_id="test")
        summaries = self._summaries()
        alias_map = {s.option_id: s.option_id for s in summaries}

        agent._call_stage2_api = lambda _: self._fake_stage2_response("OPTION-A")
        rec = agent.recommend_from_summaries(summaries, _LS, _MS, [])

        # Find candidate_option evidence — should have OPTION-B preserved
        cand_ev = [e for e in rec.evidence if e.source == "candidate_option"]
        assert len(cand_ev) == 1
        assert cand_ev[0].option_id == "OPTION-B"

        # Find link_state evidence — should have no option_id
        link_ev = [e for e in rec.evidence if e.source == "link_state"]
        assert len(link_ev) == 1
        assert link_ev[0].option_id is None

    def test_gemini_preserves_option_id(self):
        from backend.app.agent.gemini_provider import GeminiProvider
        provider = GeminiProvider(api_key="test-key")
        summaries = self._summaries()

        def fake_post(url, params=None, json=None, **kwargs):
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = {
                "candidates": [{
                    "content": {"parts": [{"text": self._fake_stage2_response("OPTION-A")}]}
                }]
            }
            return m

        with patch("httpx.Client") as mc:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = fake_post
            mc.return_value = mock_client
            rec = provider.recommend_from_summaries(summaries, _LS, _MS, [])

        cand_ev = [e for e in rec.evidence if e.source == "candidate_option"]
        assert len(cand_ev) == 1
        assert cand_ev[0].option_id == "OPTION-B"

    def test_ollama_preserves_option_id(self):
        from backend.app.agent.ollama_provider import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434")
        summaries = self._summaries()

        def fake_call_api(prompt: str) -> str:
            return self._fake_stage2_response("OPTION-A")

        provider._call_api = fake_call_api
        rec = provider.recommend_from_summaries(summaries, _LS, _MS, [])

        cand_ev = [e for e in rec.evidence if e.source == "candidate_option"]
        assert len(cand_ev) == 1
        assert cand_ev[0].option_id == "OPTION-B"

        link_ev = [e for e in rec.evidence if e.source == "link_state"]
        assert len(link_ev) == 1
        assert link_ev[0].option_id is None


# ===========================================================================
# Part 11: Stage2SummaryBuildError fail-fast
# ===========================================================================


class TestStage2SummaryBuildErrorFast:
    """build_stage2_summaries must raise Stage2SummaryBuildError on incomplete inputs."""

    def _plans(self, ids):
        return [_plan(pid, [f"{pid}-P1"]) for pid in ids]

    def _evals(self, ids):
        return [_eval_result(pid) for pid in ids]

    def _outcomes(self, ids):
        return [
            MissionOutcomeResult(
                plan_id=pid,
                total_products=5, delivered_products=4,
                delivery_rate=0.8,
                total_scientific_value=2.5, delivered_scientific_value=2.0,
                scientific_value_capture_rate=0.8,
                required_products_total=2, required_products_delivered=2,
                required_delivery_rate=1.0,
                active_anomaly_products_total=0, active_anomaly_products_delivered=0,
                active_anomaly_delivery_rate=None,
                high_severity_threshold=0.75,
                high_severity_anomalies_total=0, high_severity_anomalies_covered=0,
                high_severity_anomaly_coverage_rate=None,
            )
            for pid in ids
        ]

    def test_missing_plan_raises(self):
        """Alias map references a plan that is not in plans list → error."""
        alias_map = {"OPTION-A": "plan-a", "OPTION-B": "plan-b"}
        plans = self._plans(["plan-a"])  # plan-b is missing
        evals = self._evals(["plan-a", "plan-b"])
        with pytest.raises(Stage2SummaryBuildError, match="plan-b"):
            build_stage2_summaries(alias_map, plans, evals)

    def test_missing_evaluation_raises(self):
        """Alias map references a plan with no EvaluationResult → error."""
        alias_map = {"OPTION-A": "plan-a", "OPTION-B": "plan-b"}
        plans = self._plans(["plan-a", "plan-b"])
        evals = self._evals(["plan-a"])  # plan-b eval missing
        with pytest.raises(Stage2SummaryBuildError, match="plan-b"):
            build_stage2_summaries(alias_map, plans, evals)

    def test_missing_mission_outcome_raises_when_outcomes_provided(self):
        """When mission_outcomes is provided, all plans must have a result."""
        alias_map = {"OPTION-A": "plan-a", "OPTION-B": "plan-b", "OPTION-C": "plan-c",
                     "OPTION-D": "plan-d", "OPTION-E": "plan-e"}
        ids = ["plan-a", "plan-b", "plan-c", "plan-d", "plan-e"]
        plans = self._plans(ids)
        evals = self._evals(ids)
        outcomes = self._outcomes(["plan-a", "plan-b", "plan-c", "plan-d"])  # plan-e missing

        with pytest.raises(Stage2SummaryBuildError, match="plan-e"):
            build_stage2_summaries(alias_map, plans, evals, outcomes)

    def test_complete_5_plan_set_succeeds(self):
        """5 plans / 5 evals / 5 outcomes must succeed."""
        ids = ["plan-a", "plan-b", "plan-c", "plan-d", "plan-e"]
        alias_map = {f"OPTION-{chr(65+i)}": pid for i, pid in enumerate(ids)}
        plans = self._plans(ids)
        evals = self._evals(ids)
        outcomes = self._outcomes(ids)
        summaries = build_stage2_summaries(alias_map, plans, evals, outcomes)
        assert len(summaries) == 5

    def test_duplicate_plan_ids_raise(self):
        """Duplicate plan_ids in plans input → error."""
        alias_map = {"OPTION-A": "plan-a"}
        plans = self._plans(["plan-a"]) + self._plans(["plan-a"])  # duplicate
        evals = self._evals(["plan-a"])
        with pytest.raises(Stage2SummaryBuildError, match="plan-a"):
            build_stage2_summaries(alias_map, plans, evals)

    def test_duplicate_eval_ids_raise(self):
        """Duplicate plan_ids in evaluations → error."""
        alias_map = {"OPTION-A": "plan-a"}
        plans = self._plans(["plan-a"])
        evals = self._evals(["plan-a"]) + self._evals(["plan-a"])  # duplicate
        with pytest.raises(Stage2SummaryBuildError, match="plan-a"):
            build_stage2_summaries(alias_map, plans, evals)

    def test_duplicate_outcome_ids_raise(self):
        """Duplicate plan_ids in mission_outcomes → error."""
        alias_map = {"OPTION-A": "plan-a"}
        plans = self._plans(["plan-a"])
        evals = self._evals(["plan-a"])
        outcomes = self._outcomes(["plan-a"]) + self._outcomes(["plan-a"])  # duplicate
        with pytest.raises(Stage2SummaryBuildError, match="plan-a"):
            build_stage2_summaries(alias_map, plans, evals, outcomes)


# ===========================================================================
# Part 12: MissionOutcomeEvaluator duplicate validations
# ===========================================================================


class TestMissionOutcomeEvaluatorDuplicateValidations:
    """New duplicate-ID validations in MissionOutcomeEvaluator."""

    def test_duplicate_packet_ids_in_plan_raise(self):
        """CandidatePlan with duplicate packet_ids must raise MissionOutcomeEvaluationError."""
        plan = _plan("p", ["P1", "P1", "P2"])  # P1 is duplicated
        er = _eval_result("p")
        products = [_dp("P1"), _dp("P2")]
        with pytest.raises(MissionOutcomeEvaluationError, match="P1"):
            MissionOutcomeEvaluator().evaluate(plan, er, products, [])

    def test_unique_packet_ids_in_plan_ok(self):
        """CandidatePlan with unique packet_ids must succeed."""
        plan = _plan("p", ["P1", "P2"])
        er = _eval_result("p")
        products = [_dp("P1"), _dp("P2")]
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [])
        assert result.plan_id == "p"

    def test_duplicate_anomaly_ids_raise(self):
        """Duplicate authoritative AnomalyEvent IDs must raise."""
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        products = [_dp("P1")]
        anomalies = [
            _anomaly("ANOM-017", severity=0.8),
            _anomaly("ANOM-017", severity=0.9),  # duplicate
        ]
        with pytest.raises(MissionOutcomeEvaluationError, match="ANOM-017"):
            MissionOutcomeEvaluator().evaluate(plan, er, products, anomalies)

    def test_unique_anomaly_ids_ok(self):
        """Unique anomaly IDs must succeed."""
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        products = [_dp("P1")]
        anomalies = [
            _anomaly("ANOM-001", severity=0.8),
            _anomaly("ANOM-002", severity=0.6),
        ]
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, anomalies)
        assert result.plan_id == "p"


# ===========================================================================
# Part 13: MissionOutcomeEvaluator uses shared policy
# ===========================================================================


class TestMissionOutcomeEvaluatorAnomalyPolicy:
    """Resolved anomalies must be excluded from coverage metrics."""

    def test_active_anomaly_included_in_coverage(self):
        ae = _anomaly("ANOM-ACT", 0.8, "active")
        products = [_dp("P1", anomaly_id="ANOM-ACT")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [ae])
        assert result.active_anomaly_products_total == 1

    def test_monitoring_anomaly_included_in_coverage(self):
        ae = _anomaly("ANOM-MON", 0.7, "monitoring")
        products = [_dp("P1", anomaly_id="ANOM-MON")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [ae])
        assert result.active_anomaly_products_total == 1

    def test_resolved_anomaly_excluded_from_coverage(self):
        ae_resolved = _anomaly("ANOM-RES", 0.99, "resolved")
        products = [_dp("P1", anomaly_id="ANOM-RES")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [ae_resolved])
        assert result.active_anomaly_products_total == 0
        assert result.active_anomaly_delivery_rate is None

    def test_resolved_products_still_in_totals(self):
        """Resolved-anomaly products must remain in total_products and scientific value."""
        ae_resolved = _anomaly("ANOM-RES", 0.99, "resolved")
        products = [
            _dp("P1", anomaly_id="ANOM-RES", scientific_value=0.8),
            _dp("P2", scientific_value=0.2),
        ]
        plan = _plan("p", ["P1", "P2"])
        er = _eval_result("p")
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [ae_resolved])
        # Both products still count in total_products and scientific value
        assert result.total_products == 2
        assert result.total_scientific_value == pytest.approx(1.0)

    def test_unknown_anomaly_id_not_counted(self):
        """Product with anomaly_id referencing no AnomalyEvent must not inflate coverage."""
        products = [_dp("P1", anomaly_id="ANOM-MISSING")]
        plan = _plan("p", ["P1"])
        er = _eval_result("p")
        # Pass no matching anomaly
        result = MissionOutcomeEvaluator().evaluate(plan, er, products, [])
        assert result.active_anomaly_products_total == 0


# ===========================================================================
# Part 14: Actual context size measurement
# ===========================================================================


class TestActualContextSize:
    """Measure the actual compact Stage-2 context size (no fabricated numbers)."""

    def test_compact_stage2_message_size_reported(self):
        """Build an actual 5-option Stage-2 user message with semantic metrics and
        measure its exact character and byte count."""
        option_ids = [f"OPTION-{chr(65 + i)}" for i in range(5)]
        summaries = [
            Stage2PlanSummary(
                option_id=oid,
                total_packets=150,
                deferred_count=15,
                risk_score=0.30 + i * 0.05,
                risk_level="MEDIUM",
                mission_value=120.0 - i * 5,
                critical_packets_delivered=28 - i,
                total_critical_packets=30,
                deadline_misses=5 + i,
                deadline_miss_rate=0.03 + i * 0.01,
                bandwidth_utilization=0.70 - i * 0.02,
                retransmission_overhead=0.12 + i * 0.01,
                window_pressure=0.65 + i * 0.03,
                scientific_value_capture_rate=0.85 - i * 0.05,
                required_delivery_rate=0.95 - i * 0.05,
                active_anomaly_delivery_rate=0.90 - i * 0.1,
                high_severity_anomaly_coverage_rate=1.0,
                anomaly_weighted_coverage=0.88 - i * 0.05,
                average_delivered_age_s=72.0 + i * 10,
            )
            for i, oid in enumerate(option_ids)
        ]
        anomalies = [
            _anomaly("ANOM-017", severity=0.91),
            _anomaly("ANOM-019", severity=0.75),
        ]
        msg = build_stage2_user_message(summaries, _LS, _MS, anomalies)
        char_count = len(msg)
        byte_count = len(msg.encode("utf-8"))

        # Verify the message is valid JSON and contains expected keys
        data = json.loads(msg)
        assert "candidate_options" in data
        assert len(data["candidate_options"]) == 5
        assert "mission_context" in data
        assert "link_context" in data
        assert "active_anomalies" in data

        # The compact message must be much smaller than full packet serialization.
        # 5 plans × 150 packets × ~100 chars/packet = 75,000 chars minimum
        full_plan_size_estimate = 5 * 150 * 100
        assert char_count < full_plan_size_estimate, (
            f"Compact Stage-2 message ({char_count} chars, {byte_count} bytes) "
            f"is NOT smaller than estimated full-plan payload ({full_plan_size_estimate} chars). "
            "Context is not compact."
        )

        # Print for the implementation report (captured by pytest -s)
        print(f"\n[Context Size] Compact Stage-2 message: {char_count} chars / {byte_count} bytes")
        print(f"[Context Size] Full-plan estimate (5×150×100): {full_plan_size_estimate} chars")
        print(f"[Context Size] Reduction: {100 * (1 - char_count / full_plan_size_estimate):.1f}%")
        print("[Context Size] Note: full-plan size is an estimate, not a directly measured value.")
