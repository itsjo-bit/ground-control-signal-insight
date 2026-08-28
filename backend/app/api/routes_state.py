"""GCSI Backend API — routes for /state."""

from fastapi import APIRouter, HTTPException

from .. import state
from ..telecom.geometry import compute_communication_geometry

router = APIRouter()

# ---------------------------------------------------------------------------
# Phase 3: speed-of-light authority is now telecom/geometry.py.
# This re-export preserves backwards compatibility for any test that imports
# _SPEED_OF_LIGHT_M_S directly from this module.
# ---------------------------------------------------------------------------
from ..telecom.geometry import SPEED_OF_LIGHT_M_S as _SPEED_OF_LIGHT_M_S  # noqa: E402


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

    Phase 6E-C6 adds:

    ``source``
        Source-mode and provenance summary from ``state.get_active_source_summary()``.

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
        _geom = compute_communication_geometry(distance_km)
        propagation_delay_s: float | None = _geom["propagation_delay_s"]
        round_trip_time_s: float | None = _geom["round_trip_time_s"]
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
        # Phase 6E-C6 source metadata
        "source": state.get_active_source_summary(),
    }


@router.post("/state/reset")
def reset_state() -> dict:
    """Reload the active source from its baseline, discarding all post-simulation mutations.

    Phase 6E-C6: delegates to ``state.reset_active_source()`` which selects
    the correct reset path based on the active source mode.

    Historical replay:
        Reloads the same descriptor through HistoricalReplayProvider.
        Deterministic and non-randomized.
        Returns ``scenario_path: null`` (replay is descriptor-backed).

    Synthetic scenario:
        Reloads the same scenario file with randomize=True.
        Preserves existing jitter behaviour.

    Raises 503 if no source has ever been loaded (nothing to reset to).
    Raises 500 if the source can no longer be loaded from disk.
    """
    if state.active_source_mode is None:
        raise HTTPException(
            status_code=503,
            detail="No source has been loaded yet — nothing to reset to.",
        )
    try:
        reset_info = state.reset_active_source()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reset active source: {exc}",
        ) from exc

    return {
        "status": "reset",
        "scenario_path": state.active_scenario_path,
        "comm_window_remaining_s": state.active_scenario.mission_state.comm_window_remaining_s,  # type: ignore[union-attr]
        "source_mode": reset_info["source_mode"],
        "randomized": reset_info["randomized"],
    }
