"""GCSI Backend API — routes for /state."""

from fastapi import APIRouter, HTTPException

from .. import state

router = APIRouter()

# ---------------------------------------------------------------------------
# Physical constant — speed of light in m/s (exact SI value).
# Used only to derive one-way propagation delay from scenario distance_km.
# This constant must NOT be used in any RF/telecom formula; those remain
# in backend/app/telecom/formulas.py and are independent of distance.
# ---------------------------------------------------------------------------
_SPEED_OF_LIGHT_M_S: float = 299_792_458.0


class StateResponse:
    pass


@router.get("/state")
def get_state() -> dict:
    """Return current link_state, mission_state, and v2 data product / anomaly metadata.

    The ``data_products_count`` and ``anomalies_count`` fields are always present
    (zero for legacy scenarios that carry no v2 data).  The ``anomalies`` list
    contains the full serialised AnomalyEvent objects so the frontend and AI layer
    can surface active anomaly context without a separate round trip.

    Phase 2E-C1 adds two authoritative communication-budget fields:

    ``available_capacity_bits``
        The maximum number of bits that can realistically be transmitted during
        the remaining communication window at current link goodput.
        Formula: ``link_goodput_bps × remaining_window_s``
        Both operands come from the active LinkState so the value automatically
        reflects any link jitter applied by ``POST /state/reset``.

    ``queued_data_bits``
        Total size of all data products (or legacy packets) currently queued for
        transmission.  Formula: ``sum(product.size_bits)`` over the active
        scenario's data products; falls back to ``scenario.packets`` for legacy
        packet-only scenarios.  Returns 0 when neither collection is populated.

    Phase 2E-C3-C adds three spacecraft communication geometry fields:

    ``distance_km``
        Spacecraft-to-Earth distance in kilometres at the start of the pass,
        sourced directly from ``Scenario.distance_km``.
        ``null`` for legacy scenarios that do not carry a distance value.
        This field is mission geometry metadata only — it is NEVER used by
        TelecomEngine, PlanEvaluator, or TransmissionSimulator.

    ``propagation_delay_s``
        One-way signal propagation time from spacecraft to Earth, in seconds.
        Formula: ``distance_km × 1000 / c``  where ``c = 299,792,458 m/s``.
        Semantic note: this is the physical signal travel time, NOT the data
        transmission duration.  Transmission time is still determined by
        ``data_size / goodput`` in the existing pipeline.
        ``null`` when ``distance_km`` is ``null``.

    ``round_trip_time_s``
        Approximate signal round-trip time in seconds.
        Formula: ``2 × propagation_delay_s``.
        Represents propagation RTT only — not an ACK/retransmission completion
        guarantee.  ACK overhead remains folded into ``protocol_efficiency``.
        ``null`` when ``distance_km`` is ``null``.

    Raises 503 if no scenario has been loaded yet.
    """
    if state.active_scenario is None or state.active_link_state is None:
        raise HTTPException(status_code=503, detail="No active scenario loaded")
    scenario = state.active_scenario
    link_state = state.active_link_state

    # ── Phase 2E-C1: authoritative communication budget ─────────────────────
    # available_capacity_bits: the maximum payload that fits in the remaining
    # window at current goodput.  This is the same product that PlanEvaluator
    # and BaselineScheduler compute internally; exposing it here makes it the
    # single backend-authoritative value so the frontend never recalculates it.
    available_capacity_bits: int = int(
        link_state.link_goodput_bps * link_state.remaining_window_s
    )

    # queued_data_bits: total size of all queued items.
    # v2/v3 scenarios carry data_products; legacy scenarios carry packets.
    if scenario.data_products:
        queued_data_bits: int = sum(dp.size_bits for dp in scenario.data_products)
    elif scenario.packets:
        queued_data_bits = sum(pkt.size_bits for pkt in scenario.packets)
    else:
        queued_data_bits = 0

    # ── Phase 2E-C3-C: spacecraft communication geometry ────────────────────
    # These are read-only, deterministically derived fields.
    # They do NOT enter the RF/telecom calculation chain.
    # TelecomEngine, PlanEvaluator, and TransmissionSimulator are unchanged.
    distance_km: float | None = scenario.distance_km
    if distance_km is not None:
        propagation_delay_s: float | None = distance_km * 1_000.0 / _SPEED_OF_LIGHT_M_S
        round_trip_time_s: float | None = propagation_delay_s * 2.0
    else:
        propagation_delay_s = None
        round_trip_time_s = None

    return {
        "link_state": link_state.model_dump(mode="json"),
        "mission_state": scenario.mission_state.model_dump(mode="json"),
        "data_products_count": len(scenario.data_products),
        "anomalies_count": len(scenario.anomalies),
        "anomalies": [a.model_dump(mode="json") for a in scenario.anomalies],
        # Phase 2E-C1 communication budget fields
        "available_capacity_bits": available_capacity_bits,
        "queued_data_bits": queued_data_bits,
        # Phase 2E-C3-C spacecraft communication geometry fields
        "distance_km": distance_km,
        "propagation_delay_s": propagation_delay_s,
        "round_trip_time_s": round_trip_time_s,
    }


@router.post("/state/reset")
def reset_state() -> dict:
    """Reload the active scenario from disk, discarding all post-simulation mutations.

    Re-calls ``state.load_scenario()`` with the path that was used when the
    scenario was first loaded.  This restores ``active_scenario`` and
    ``active_link_state`` to their original, unconsumed values — exactly as
    if the server had just started with ``GCSI_SCENARIO_PATH`` set.

    Use this after running a simulation or approving a plan to get a clean
    slate without restarting the server process.

    Raises 503 if no scenario has ever been loaded (nothing to reset to).
    Raises 500 if the scenario file can no longer be read from disk.
    """
    if state.active_scenario_path is None:
        raise HTTPException(
            status_code=503,
            detail="No scenario has been loaded yet — nothing to reset to.",
        )
    try:
        state.load_scenario(state.active_scenario_path, randomize=True)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload scenario from '{state.active_scenario_path}': {exc}",
        ) from exc
    return {
        "status": "reset",
        "scenario_path": state.active_scenario_path,
        "comm_window_remaining_s": state.active_scenario.mission_state.comm_window_remaining_s,  # type: ignore[union-attr]
    }
