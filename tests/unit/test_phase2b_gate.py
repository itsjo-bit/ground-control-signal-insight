"""Phase 2B Pre-flight Gate regression tests.

Covers all four integrity fixes required before benchmark execution:

Gate 0.1 — compact Stage-2 evidence source whitelist (unknown sources rejected)
Gate 0.2 — Stage-1 anomaly prompt distinguishes historical vs active anomaly
Gate 0.3 — SemanticRulePrioritizer counts only applicable anomaly-linked products
Gate 0.4 — candidate_option evidence with None metric value is dropped
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.app.agent.stage2_blinding import (
    Stage2PlanSummary,
    is_valid_source_field,
    is_valid_source_field_strict,
    parse_stage2_response,
)
from backend.app.agent.semantic_rule_prioritizer import SemanticRulePrioritizer
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.risk_level import RiskLevel

_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anomaly(aid: str, *, status: str = "active", severity: float = 0.9) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=aid,
        description="test anomaly",
        subsystem="propulsion",
        severity=severity,
        status=status,
        detected_at_s=0.0,
    )


def _cs(
    pid: str,
    *,
    anomaly_id: str | None = None,
    criticality: float = 0.5,
    deadline_s: float = 300.0,
) -> CandidateSummary:
    return CandidateSummary(
        product_id=pid,
        product_type="telemetry",
        description="test product",
        subsystem="payload",
        size_bits=8_000,
        criticality=criticality,
        mission_relevance=0.5,
        scientific_value=0.5,
        deadline_s=deadline_s,
        age_s=100.0,
        anomaly_id=anomaly_id,
    )


def _summary(alias: str, *, required_delivery_rate: float | None = None) -> Stage2PlanSummary:
    """Build a minimal Stage2PlanSummary for testing."""
    return Stage2PlanSummary(
        option_id=alias,
        total_packets=10,
        deferred_count=2,
        risk_score=0.3,
        risk_level="LOW",
        mission_value=5.0,
        critical_packets_delivered=3,
        total_critical_packets=5,
        deadline_misses=1,
        deadline_miss_rate=0.1,
        bandwidth_utilization=0.7,
        retransmission_overhead=0.1,
        window_pressure=0.6,
        required_delivery_rate=required_delivery_rate,
    )


def _make_valid_response(
    alias_map: dict[str, str],
    rec: str,
    evidence: list[dict] | None = None,
) -> str:
    return json.dumps({
        "recommended_option_id": rec,
        "reasoning": "test reasoning",
        "confidence": 0.8,
        "evidence": evidence or [],
        "alternative_option_id": None,
    })


_ALIAS_MAP = {"OPTION-A": "baseline", "OPTION-B": "ai-prioritized"}


# ===========================================================================
# Gate 0.1 — strict source whitelist
# ===========================================================================


class TestGate01StrictSourceWhitelist:
    """Unknown sources must be rejected in the compact Stage-2 parser."""

    # Legacy is_valid_source_field still allows unknown sources
    def test_legacy_allows_unknown_source(self):
        assert is_valid_source_field("space_magic", "some_field") is True
        assert is_valid_source_field("evaluation_result", "risk_score") is True
        assert is_valid_source_field("unknown_source", "x") is True

    # Strict version rejects unknown sources
    def test_strict_rejects_unknown_source(self):
        assert is_valid_source_field_strict("space_magic", "risk_score") is False
        assert is_valid_source_field_strict("evaluation_result", "risk_score") is False
        assert is_valid_source_field_strict("unknown_source", "x") is False
        assert is_valid_source_field_strict("whatever", "mission_value") is False

    # Strict version allows known sources with valid fields
    def test_strict_allows_known_sources(self):
        assert is_valid_source_field_strict("candidate_option", "risk_score") is True
        assert is_valid_source_field_strict("link_state", "remaining_window_s") is True
        assert is_valid_source_field_strict("mission_state", "mission_phase") is True

    # parse_stage2_response drops evidence with unknown source
    def test_parse_drops_unknown_source_evidence(self):
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "space_magic",
                "field": "risk_score",
                "interpretation": "magic",
            },
            {
                "option_id": "OPTION-A",
                "source": "unknown_source",
                "field": "mission_value",
                "interpretation": "unknown",
            },
            {
                "option_id": None,
                "source": "candidate_option",   # valid — but missing option_id → also dropped
                "field": "risk_score",
                "interpretation": "valid field, but no option_id",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP)
        # All evidence items must be dropped
        assert ev_out == [], f"Expected empty evidence but got: {ev_out}"

    def test_parse_keeps_valid_source_evidence(self):
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "candidate_option",
                "field": "risk_score",
                "interpretation": "low risk",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP)
        assert len(ev_out) == 1
        assert ev_out[0]["source"] == "candidate_option"

    def test_parse_drops_evaluation_result_source(self):
        """evaluation_result is a legacy source — must not appear in compact Stage-2."""
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "evaluation_result",
                "field": "risk_score",
                "interpretation": "from eval",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP)
        assert ev_out == []


# ===========================================================================
# Gate 0.2 — Stage-1 prompt anomaly semantics
# ===========================================================================


class TestGate02PromptAnomalySemantics:
    """Stage-1 prompt must distinguish historical anomaly_id from active anomaly."""

    def test_prompt_does_not_say_anomaly_id_not_null_means_active(self):
        from backend.app.agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
        lower = _PRIORITIZATION_SYSTEM_PROMPT.lower()
        # The old bad language "anomaly_id != null" or "anomaly_id is not None" implying active
        assert "anomaly_id != null" not in lower, (
            "Prompt still contains 'anomaly_id != null' implying active anomaly"
        )

    def test_prompt_mentions_active_anomalies_list(self):
        from backend.app.agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
        # The prompt must instruct the LLM to check active_anomalies list
        assert "active_anomalies" in _PRIORITIZATION_SYSTEM_PROMPT, (
            "Prompt must reference the active_anomalies list"
        )

    def test_prompt_mentions_resolved_or_historical(self):
        from backend.app.agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
        lower = _PRIORITIZATION_SYSTEM_PROMPT.lower()
        # Prompt must mention that anomaly may be resolved/historical
        has_resolved = "resolved" in lower or "historical" in lower
        assert has_resolved, (
            "Prompt must warn that anomaly_id may be resolved or historical-only"
        )

    def test_prompt_mentions_not_treat_as_active(self):
        from backend.app.agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
        lower = _PRIORITIZATION_SYSTEM_PROMPT.lower()
        # Should instruct not to grant active urgency when not in active_anomalies
        assert "do not" in lower or "do not" in _PRIORITIZATION_SYSTEM_PROMPT, (
            "Prompt must have explicit 'do NOT grant active-anomaly urgency' language"
        )


# ===========================================================================
# Gate 0.3 — SemanticRulePrioritizer applicable-anomaly count
# ===========================================================================


class TestGate03SemanticRuleAnomalyCount:
    """SemanticRulePrioritizer narrative must count only APPLICABLE anomaly products."""

    def test_resolved_anomaly_not_counted_in_narrative(self):
        """Product linked to resolved anomaly must NOT be counted as active-anomaly."""
        resolved_anom = _anomaly("ANOM-001", status="resolved")
        cs_resolved = _cs("P1", anomaly_id="ANOM-001")
        cs_normal = _cs("P2")

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs_resolved, cs_normal], anomalies=[resolved_anom])

        assert "0 product(s) are linked to APPLICABLE" in result.overall_reasoning, (
            f"Expected 0 applicable anomalies in narrative. Got: {result.overall_reasoning}"
        )

    def test_active_anomaly_counted_in_narrative(self):
        """Product linked to active anomaly must be counted."""
        active_anom = _anomaly("ANOM-001", status="active")
        cs_active = _cs("P1", anomaly_id="ANOM-001")
        cs_normal = _cs("P2")

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs_active, cs_normal], anomalies=[active_anom])

        assert "1 product(s) are linked to APPLICABLE" in result.overall_reasoning

    def test_unknown_anomaly_id_not_counted(self):
        """Product with anomaly_id not in provided anomaly list is not counted."""
        active_anom = _anomaly("ANOM-999", status="active")  # different ID
        cs_with_unknown_anom = _cs("P1", anomaly_id="ANOM-001")  # not in anomaly list

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs_with_unknown_anom], anomalies=[active_anom])

        assert "0 product(s) are linked to APPLICABLE" in result.overall_reasoning

    def test_active_anomaly_top_factor(self):
        """'active anomaly' top factor only when APPLICABLE anomalies exist."""
        resolved_anom = _anomaly("ANOM-001", status="resolved")
        cs = _cs("P1", anomaly_id="ANOM-001")

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs], anomalies=[resolved_anom])
        # resolved anomaly — 'active anomaly' must not appear in top decision_factors
        assert "active anomaly" not in result.decision_factors

    def test_resolved_anomaly_product_factor_not_active(self):
        """Per-product factor must not say 'active anomaly' for resolved-linked product."""
        resolved_anom = _anomaly("ANOM-001", status="resolved")
        cs = _cs("P1", anomaly_id="ANOM-001")

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs], anomalies=[resolved_anom])
        rp = result.ranked_products[0]
        assert "active anomaly" not in rp.factors

    def test_applicable_anomaly_product_factor_is_active(self):
        """Per-product factor says 'active anomaly' for applicable-linked product."""
        active_anom = _anomaly("ANOM-001", status="active")
        cs = _cs("P1", anomaly_id="ANOM-001")

        prioritizer = SemanticRulePrioritizer()
        result = prioritizer.prioritize([cs], anomalies=[active_anom])
        rp = result.ranked_products[0]
        assert "active anomaly" in rp.factors


# ===========================================================================
# Gate 0.4 — Drop None-valued candidate_option evidence
# ===========================================================================


class TestGate04DropNullMetricEvidence:
    """candidate_option evidence citing a None metric for that option must be dropped."""

    def test_drops_evidence_when_metric_is_none(self):
        summaries = [
            _summary("OPTION-A", required_delivery_rate=None),   # None
            _summary("OPTION-B", required_delivery_rate=0.8),    # non-null
        ]
        # LLM cites OPTION-A / required_delivery_rate, which is None for OPTION-A
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "candidate_option",
                "field": "required_delivery_rate",
                "interpretation": "should be dropped",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP, summaries=summaries)
        assert ev_out == [], f"Expected evidence dropped but got: {ev_out}"

    def test_keeps_evidence_when_metric_is_nonnull(self):
        summaries = [
            _summary("OPTION-A", required_delivery_rate=0.75),
            _summary("OPTION-B", required_delivery_rate=0.8),
        ]
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "candidate_option",
                "field": "required_delivery_rate",
                "interpretation": "should be kept",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP, summaries=summaries)
        assert len(ev_out) == 1
        assert ev_out[0]["field"] == "required_delivery_rate"

    def test_always_available_metric_not_dropped(self):
        """risk_score is always non-null; it must never be dropped."""
        summaries = [_summary("OPTION-A"), _summary("OPTION-B")]
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "candidate_option",
                "field": "risk_score",
                "interpretation": "always non-null",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP, summaries=summaries)
        assert len(ev_out) == 1

    def test_no_summaries_skips_gate04_check(self):
        """When summaries are not provided, Gate 0.4 check is skipped (backward compat)."""
        evidence = [
            {
                "option_id": "OPTION-A",
                "source": "candidate_option",
                "field": "required_delivery_rate",
                "interpretation": "no summaries supplied",
            },
        ]
        raw = _make_valid_response(_ALIAS_MAP, "OPTION-A", evidence)
        # Without summaries, the None check is skipped — evidence is kept
        _, _, _, ev_out, _ = parse_stage2_response(raw, _ALIAS_MAP, summaries=None)
        assert len(ev_out) == 1
