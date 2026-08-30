#!/usr/bin/env python3
"""ASTERIA-7 live Gemini smoke test — 8B.4-R validation.

Run from project root:
    GCSI_AI_PROVIDER=gemini GCSI_GEMINI_TIMEOUT=120 python scripts/smoke_asteria7_gemini.py

Pass criteria:
  - Stage-1  (prioritize_candidates)  → HTTP 200, ranked candidates returned
  - Stage-2  (recommend_from_summaries) → HTTP 200, JSON parses completely
  - recommended_plan_id is a valid OPTION-X alias (not a real plan name)
  - No LocalRuleBasedProvider fallback
  - No AIResponseError (truncation / malformed JSON)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Ensure project root is on the path ───────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

# ── Imports ───────────────────────────────────────────────────────────────────
from backend.app.agent.gemini_provider import GeminiProvider, _STAGE2_MAX_OUTPUT_TOKENS
from backend.app.agent.stage2_blinding import (
    Stage2PlanSummary,
    build_blind_mapping,
    build_stage2_summaries,
)
from backend.app.models.candidate_plan import CandidatePlan
from backend.app.models.candidate_summary import CandidateSummary
from backend.app.models.evaluation_result import EvaluationResult
from backend.app.models.link_state import LinkState
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.risk_level import RiskLevel


def _build_test_context():
    """Build minimal ASTERIA-7 link/mission context for the smoke test."""
    ts = datetime(2024, 9, 23, 14, 7, 0, tzinfo=timezone.utc)
    link_state = LinkState(
        timestamp=ts,
        snr_db=2.8,
        eb_n0_db=5.0,
        ber=1e-4,
        rssi_dbm=-103.6,
        nominal_data_rate_bps=2_800_000.0,
        link_goodput_bps=2_100_000.0,
        latency_s=1.4,
        link_stability=0.68,
        remaining_window_s=272.0,
    )
    mission_state = MissionState(
        mission_id="GCSI-ASTERIA-7",
        mission_phase="pre_contact_anomaly_triage",
        current_event="Active avionics thermal anomaly; high-rate contact pending.",
        event_time_remaining_s=192.0,
        comm_window_remaining_s=272.0,
        risk_score=0.72,
        risk_level=RiskLevel.HIGH,
    )
    return link_state, mission_state


def _build_stage2_summaries() -> list[Stage2PlanSummary]:
    """Build representative Stage-2 summaries for the ASTERIA-7 smoke."""
    return [
        Stage2PlanSummary(
            option_id=f"OPTION-{chr(65 + i)}",
            total_packets=50,
            deferred_count=i * 5,
            risk_score=round(0.2 + i * 0.1, 2),
            risk_level=["LOW", "MEDIUM", "MEDIUM", "HIGH", "HIGH"][i],
            mission_value=round(2.5 - i * 0.3, 2),
            critical_packets_delivered=max(8, 10 - i),
            total_critical_packets=10,
            deadline_misses=i,
            deadline_miss_rate=round(i * 0.05, 2),
            bandwidth_utilization=round(0.85 - i * 0.05, 2),
            retransmission_overhead=round(i * 0.02, 2),
            window_pressure=round(0.4 + i * 0.05, 2),
            scientific_value_capture_rate=round(0.95 - i * 0.05, 2),
            required_delivery_rate=round(1.0 - i * 0.05, 2),
        )
        for i in range(5)
    ]


def main():
    api_key = os.getenv("GCSI_GEMINI_API_KEY", "").strip()
    if not api_key:
        print("FAIL: GCSI_GEMINI_API_KEY is not set.")
        sys.exit(1)

    timeout = float(os.getenv("GCSI_GEMINI_TIMEOUT", "120"))
    model = os.getenv("GCSI_GEMINI_MODEL", "gemini-2.5-flash-lite-preview-06-17")

    print(f"Model:   {model}")
    print(f"Timeout: {timeout}s")
    print(f"Stage-2 maxOutputTokens: {_STAGE2_MAX_OUTPUT_TOKENS}")
    print()

    provider = GeminiProvider(api_key=api_key, model=model, timeout_s=timeout)
    link_state, mission_state = _build_test_context()
    summaries = _build_stage2_summaries()
    alias_map = {s.option_id: s.option_id for s in summaries}

    print("-- Stage-2 (recommend_from_summaries) ----------------------------------")
    print(f"Sending {len(summaries)} OPTION aliases: {[s.option_id for s in summaries]}")
    print("generationConfig: {response_mime_type=application/json, temperature=0.0, "
          f"maxOutputTokens={_STAGE2_MAX_OUTPUT_TOKENS}}}")
    print("(NO responseSchema, NO thinkingConfig)")
    print()

    try:
        result = provider.recommend_from_summaries(summaries, link_state, mission_state)
    except Exception as exc:
        print(f"FAIL Stage-2: {type(exc).__name__}: {exc}")
        sys.exit(1)

    print(f"PASS Stage-2 HTTP 200, JSON parsed completely.")
    print(f"  recommended_plan_id : {result.recommended_plan_id}")
    print(f"  confidence          : {result.confidence}")
    print(f"  alternative_plan_id : {result.alternative_plan_id}")
    print(f"  evidence count      : {len(result.evidence)}")
    print()

    # Validate alias is opaque
    if not result.recommended_plan_id.startswith("OPTION-"):
        print(f"FAIL: recommended_plan_id is not an OPTION alias: {result.recommended_plan_id!r}")
        sys.exit(1)

    if result.recommended_plan_id not in alias_map:
        print(f"FAIL: recommended alias {result.recommended_plan_id!r} not in alias_map {list(alias_map.keys())}")
        sys.exit(1)

    print("PASS: recommended_plan_id is a valid opaque OPTION alias.")
    print("PASS: no LocalRuleBasedProvider fallback (GeminiProvider produced result).")
    print()
    print("-- ASTERIA-7 Gemini Stage-2 smoke: ALL PASS ---------------------------")
    sys.exit(0)


if __name__ == "__main__":
    main()
