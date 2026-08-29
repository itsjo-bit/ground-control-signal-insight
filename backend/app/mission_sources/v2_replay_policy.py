"""GCSI Phase 6F-B3 — Historical Replay V2 Policy Constants.

This module defines the FROZEN B3 modeled policy constants for the Juno PJ62
large historical replay V2:

- MODELED link inputs
- MODELED mission state
- MODELED queue membership policy
- MODELED size proxy policy
- MODELED product policy table (per semantic role)
- Semantic role → instrument mapping

All values are GCSI MODELED replay assumptions.
None are historical spacecraft / NASA / JPL facts.

Semantic distinctions
---------------------
- archive size ≠ historical spacecraft downlink bytes
- latency_s = protocol/link-stack overhead (NOT free-space propagation)
- queue membership = modeled reconstruction (NOT NASA TX queue claim)
- DataProduct.size_bits = modeled replay burden proxy (MODELED)
"""

from __future__ import annotations

from backend.app.mission_sources.v2_replay_descriptor import (
    V2ModeledLinkInputs,
    V2ModeledMissionState,
    V2ProductPolicy,
    V2ProductPolicyEntry,
    V2QueueMembershipPolicy,
    V2SizePolicy,
)

# ---------------------------------------------------------------------------
# Frozen B3 modeled link inputs
# ---------------------------------------------------------------------------

PJ62_V2_MODELED_LINK_INPUTS = V2ModeledLinkInputs(
    snr_db=3.0,
    rssi_dbm=-95.0,
    nominal_data_rate_bps=100000.0,
    latency_s=1.5,           # protocol/link-stack latency; NOT propagation time
    link_stability=0.8,
    remaining_window_s=900.0,
)

# ---------------------------------------------------------------------------
# Frozen B3 modeled mission state
# ---------------------------------------------------------------------------

PJ62_V2_MODELED_MISSION_STATE = V2ModeledMissionState(
    mission_id="JUNO_PJ62_HISTORICAL_REPLAY_V2",
    mission_phase="science_downlink",
    current_event="PJ62 large historical replay downlink decision",
    event_time_remaining_s=900.0,
    comm_window_remaining_s=900.0,
    risk_score=0.35,
    risk_level="MEDIUM",   # derived from risk_score 0.35 via gcsi_risk_thresholds_v1
)

# ---------------------------------------------------------------------------
# Frozen B3 queue membership policy
# ---------------------------------------------------------------------------

PJ62_V2_MODELED_QUEUE_MEMBERSHIP = V2QueueMembershipPolicy(
    policy_id="PJ62_V2_MODELED_QUEUE_MEMBERSHIP",
    description=(
        "GCSI modeled replay queue: 403 eligible logical products selected from "
        "the frozen verified inventory via temporal reconciliation. Queue membership "
        "is a MODELED reconstruction — NOT a historical NASA transmission-queue claim."
    ),
    source_mode="MODELED",
    eligible_logical_count=403,
)

# ---------------------------------------------------------------------------
# Frozen B3 archive size proxy policy
# ---------------------------------------------------------------------------

PJ62_V2_ARCHIVE_SIZE_PROXY = V2SizePolicy(
    policy_id="PJ62_V2_ARCHIVE_SIZE_PROXY_V1",
    description=(
        "GCSI modeled replay size proxy. Archive product size ≠ historical downlink bytes. "
        "DataProduct.size_bits is a MODELED replay burden proxy derived from archive evidence."
    ),
    exact_proxy_rule=(
        "logical_archive_proxy_bytes = max(exact archive_total_size_bytes "
        "among eligible representations); DataProduct.size_bits = proxy_bytes * 8"
    ),
    fallback_rule=(
        "fallback_archive_proxy_bytes = median_low of all positive "
        "archive_total_size_bytes from eligible SIZE_METADATA_EXACT source records; "
        "DataProduct.size_bits = fallback_proxy_bytes * 8"
    ),
)

# ---------------------------------------------------------------------------
# Frozen B3 product policy table
# ---------------------------------------------------------------------------

PJ62_V2_PRODUCT_POLICY = V2ProductPolicy(
    policy_id="PJ62_V2_PRODUCT_POLICY_V1",
    deadline_s=900.0,
    delivery_requirement="current_downlink_window",
    entries=(
        V2ProductPolicyEntry(
            semantic_role="instrument_diagnostic",
            criticality=0.85,
            mission_relevance=0.90,
            scientific_value=0.70,
            retry_cost=0.80,
        ),
        V2ProductPolicyEntry(
            semantic_role="radiometry_science",
            criticality=0.60,
            mission_relevance=0.95,
            scientific_value=0.95,
            retry_cost=0.65,
        ),
        V2ProductPolicyEntry(
            semantic_role="ultraviolet_observation",
            criticality=0.55,
            mission_relevance=0.85,
            scientific_value=0.90,
            retry_cost=0.60,
        ),
        V2ProductPolicyEntry(
            semantic_role="visible_imaging",
            criticality=0.40,
            mission_relevance=0.75,
            scientific_value=0.85,
            retry_cost=0.55,
        ),
        V2ProductPolicyEntry(
            semantic_role="magnetic_field",
            criticality=0.65,
            mission_relevance=0.90,
            scientific_value=0.90,
            retry_cost=0.70,
        ),
        V2ProductPolicyEntry(
            semantic_role="plasma_particles",
            criticality=0.65,
            mission_relevance=0.90,
            scientific_value=0.90,
            retry_cost=0.70,
        ),
        V2ProductPolicyEntry(
            semantic_role="energetic_particles",
            criticality=0.65,
            mission_relevance=0.90,
            scientific_value=0.90,
            retry_cost=0.70,
        ),
        V2ProductPolicyEntry(
            semantic_role="radio_plasma_survey",
            criticality=0.55,
            mission_relevance=0.85,
            scientific_value=0.85,
            retry_cost=0.60,
        ),
        V2ProductPolicyEntry(
            semantic_role="radio_plasma_burst",
            criticality=0.75,
            mission_relevance=0.95,
            scientific_value=0.90,
            retry_cost=0.80,
        ),
    ),
)

# ---------------------------------------------------------------------------
# Semantic role → instrument mapping (frozen B3 policy)
# ---------------------------------------------------------------------------

#: Maps instrument name (uppercase) → semantic role
INSTRUMENT_TO_SEMANTIC_ROLE: dict[str, str] = {
    "JIRAM":        "instrument_diagnostic",
    "MWR":          "radiometry_science",
    "UVS":          "ultraviolet_observation",
    "JUNOCAM":      "visible_imaging",
    "FGM":          "magnetic_field",
    "JADE":         "plasma_particles",
    "JEDI":         "energetic_particles",
    "WAVES_SURVEY": "radio_plasma_survey",
    "WAVES_BURST":  "radio_plasma_burst",
    # Additional aliases used in the ledger
    "WAVES":        "radio_plasma_survey",  # default; assembler uses profile_id to disambiguate
}

# ---------------------------------------------------------------------------
# Policy lookup helper
# ---------------------------------------------------------------------------


def get_policy_entry(semantic_role: str) -> V2ProductPolicyEntry:
    """Return the V2ProductPolicyEntry for a semantic role.

    Raises
    ------
    KeyError
        If the semantic role is not in the policy table.
    """
    for entry in PJ62_V2_PRODUCT_POLICY.entries:
        if entry.semantic_role == semantic_role:
            return entry
    raise KeyError(
        f"No V2 product policy entry for semantic role {semantic_role!r}. "
        f"Known roles: {[e.semantic_role for e in PJ62_V2_PRODUCT_POLICY.entries]!r}."
    )


def resolve_semantic_role(instrument: str, profile_id: str) -> str:
    """Resolve semantic role from instrument + profile_id.

    WAVES instruments are disambiguated by profile_id.
    """
    inst_upper = instrument.upper()
    if inst_upper == "WAVES":
        if "burst" in profile_id.lower():
            return "radio_plasma_burst"
        return "radio_plasma_survey"
    result = INSTRUMENT_TO_SEMANTIC_ROLE.get(inst_upper)
    if result is None:
        raise ValueError(
            f"No semantic role mapping for instrument {instrument!r} "
            f"(profile_id={profile_id!r}). "
            f"Known instruments: {sorted(INSTRUMENT_TO_SEMANTIC_ROLE.keys())!r}."
        )
    return result
