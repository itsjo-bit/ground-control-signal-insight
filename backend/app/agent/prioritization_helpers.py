"""Stateless prioritization helpers shared by all AI providers.

These functions were previously methods of :class:`GraniteAgent` but carry no
instance state — they depend only on their arguments.  Extracting them here
removes the fragile ``GraniteAgent.__new__(GraniteAgent)`` pattern that Gemini
and Ollama used to share the logic without constructing a real agent.

All providers import directly from this module:

    from .prioritization_helpers import (
        build_prioritization_message,
        parse_prioritization_response,
    )

The ``GraniteAgent`` methods delegate to these functions unchanged so the
existing ``GraniteAgent`` public API remains intact.

Geometry precision policy
--------------------------
``build_prioritization_message`` rounds geometry values to **3 decimal places**
when constructing the AI context block::

    "communication_geometry": {
        "distance_km": 54000000.0,
        "propagation_delay_s": 180.124,   # rounded to 3 dp
        "round_trip_time_s": 360.249      # rounded to 3 dp
    }

This is intentional.  The rounding is a context-size / token optimisation:
AI models do not benefit from sub-millisecond propagation precision and the
extra digits consume prompt tokens unnecessarily.

The ``GET /state`` API returns the same values at full float precision for UI
and API consumers that may need exact values.  The two representations are
semantically equivalent for all operational purposes — the difference is less
than 1 ms at Mars distances (~180 s).  Do NOT remove the rounding here.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.candidate_prioritization import CandidatePrioritization, RankedProduct
from ..models.candidate_summary import CandidateSummary
from ..models.link_state import LinkState
from ..models.mission_state import MissionState

# ---------------------------------------------------------------------------
# Physical constant
# ---------------------------------------------------------------------------

# Speed of light in m/s — exact SI value, same as routes_state._SPEED_OF_LIGHT_M_S.
# Defined here as a module-level constant so all three providers use an
# identical value without importing from the routes layer.
_SPEED_OF_LIGHT_M_S: float = 299_792_458.0


# ---------------------------------------------------------------------------
# build_prioritization_message
# ---------------------------------------------------------------------------


def build_prioritization_message(
    candidates: Sequence[CandidateSummary],
    link_state: LinkState,
    mission_state: MissionState,
    anomalies: Sequence[AnomalyEvent] | None,
    *,
    distance_km: float | None = None,
) -> str:
    """Serialise prioritization context into a compact JSON user message.

    Shared by Granite, Gemini, and Ollama providers.  Stateless — no
    reference to ``self`` or any provider instance.

    Args:
        candidates:    Pre-filtered :class:`CandidateSummary` list.
        link_state:    Current link snapshot.
        mission_state: Current mission snapshot.
        anomalies:     Active anomaly events; ``None`` or empty omits the key.
        distance_km:   Spacecraft distance from Earth in km (Phase 2E-C3-E).
                       When provided a ``communication_geometry`` block is
                       included.  When ``None`` the block is present but null.

    Geometry precision note
    -----------------------
    ``propagation_delay_s`` and ``round_trip_time_s`` are rounded to 3 decimal
    places.  See module docstring for the full precision policy rationale.

    Returns:
        JSON string suitable for use as an LLM user message.
    """
    if distance_km is not None:
        propagation_delay_s = (distance_km * 1_000.0) / _SPEED_OF_LIGHT_M_S
        # Round to 3 dp — intentional token optimisation; see module docstring.
        geometry: dict[str, Any] | None = {
            "distance_km": distance_km,
            "propagation_delay_s": round(propagation_delay_s, 3),
            "round_trip_time_s": round(2.0 * propagation_delay_s, 3),
        }
    else:
        geometry = None

    ctx: dict[str, Any] = {
        "mission_context": {
            "mission_phase": mission_state.mission_phase,
            "current_event": mission_state.current_event,
            "comm_window_remaining_s": mission_state.comm_window_remaining_s,
            "risk_score": mission_state.risk_score,
            "risk_level": mission_state.risk_level.value,
        },
        "link_context": {
            "remaining_window_s": link_state.remaining_window_s,
            "ber": link_state.ber,
            "link_goodput_bps": link_state.link_goodput_bps,
        },
        "communication_geometry": geometry,
        "candidates": [cs.model_dump(mode="json") for cs in candidates],
    }
    if anomalies:
        ctx["active_anomalies"] = [
            {
                "anomaly_id": ae.anomaly_id,
                "subsystem": ae.subsystem,
                "severity": ae.severity,
                "status": ae.status,
                "description": ae.description,
            }
            for ae in anomalies
        ]
    return json.dumps(ctx, indent=2)


# ---------------------------------------------------------------------------
# parse_prioritization_response
# ---------------------------------------------------------------------------


def parse_prioritization_response(
    raw: str,
    valid_ids: set[str],
    candidates: Sequence[CandidateSummary] | None = None,
) -> CandidatePrioritization:
    """Parse and validate a raw LLM prioritization response.

    Shared by Granite, Gemini, and Ollama providers.  Stateless.

    Args:
        raw:        Raw text returned by the LLM.
        valid_ids:  Set of allowed ``product_id`` values.
        candidates: Optional original candidate list.  When supplied, each
                    ``RankedProduct.description`` is populated from the
                    matching ``CandidateSummary.description`` (Phase 2E-D3).
                    Callers that do not pass ``candidates`` get the same
                    behaviour as before (``description`` defaults to ``""``).

    Returns:
        A validated :class:`CandidatePrioritization`.

    Raises:
        GraniteResponseError: If the JSON is malformed, required fields are
                              missing, product IDs are hallucinated, or
                              priorities are duplicated.
    """
    # Build product_id → description lookup when candidates are supplied.
    desc_map: dict[str, str] = (
        {cs.product_id: cs.description for cs in candidates}
        if candidates
        else {}
    )
    # Import here to avoid circular imports; GraniteResponseError is the
    # canonical structured error for prioritization validation failures.
    from .granite_agent import GraniteResponseError  # noqa: PLC0415

    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()

    try:
        data, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as exc:
        raise GraniteResponseError(
            f"Prioritization response is not valid JSON: {exc}\nRaw: {raw[:200]}"
        ) from exc

    # Validate required top-level fields
    required = {"ranked_products", "overall_reasoning", "confidence"}
    missing = required - data.keys()
    if missing:
        raise GraniteResponseError(
            f"Prioritization response missing fields: {missing}"
        )

    # Validate and build ranked products
    seen_ids: set[str] = set()
    seen_priorities: set[int] = set()
    ranked: list[RankedProduct] = []

    for i, item in enumerate(data.get("ranked_products", [])):
        pid = item.get("product_id", "")
        if pid not in valid_ids:
            raise GraniteResponseError(
                f"ranked_products[{i}] contains unknown product_id '{pid}'. "
                f"Valid IDs: {sorted(valid_ids)[:10]}..."
            )
        if pid in seen_ids:
            raise GraniteResponseError(
                f"ranked_products[{i}] contains duplicate product_id '{pid}'."
            )
        seen_ids.add(pid)

        priority = item.get("priority")
        if not isinstance(priority, int) or priority < 1:
            raise GraniteResponseError(
                f"ranked_products[{i}] has invalid priority '{priority}'. "
                "Must be int >= 1."
            )
        if priority in seen_priorities:
            raise GraniteResponseError(
                f"ranked_products[{i}] has duplicate priority {priority}."
            )
        seen_priorities.add(priority)

        reason = item.get("reason", "")
        if not reason:
            raise GraniteResponseError(
                f"ranked_products[{i}] has empty 'reason'."
            )

        factors = item.get("factors", [])
        if not isinstance(factors, list):
            factors = []
        factors = [str(f) for f in factors if f]

        anomaly_ids = item.get("anomaly_ids", [])
        if not isinstance(anomaly_ids, list):
            anomaly_ids = []
        anomaly_ids = [str(a) for a in anomaly_ids if a]

        subsystem = str(item.get("subsystem", ""))

        item_confidence = item.get("confidence")
        per_confidence: float | None = None
        if item_confidence is not None:
            try:
                per_confidence = float(item_confidence)
                if not (0.0 <= per_confidence <= 1.0):
                    per_confidence = None
            except (TypeError, ValueError):
                per_confidence = None

        ranked.append(RankedProduct(
            product_id=pid,
            priority=priority,
            reason=reason,
            # Phase 2E-D3: description comes from the original candidate, not the LLM.
            description=desc_map.get(pid, ""),
            factors=factors,
            anomaly_ids=anomaly_ids,
            subsystem=subsystem,
            confidence=per_confidence,
        ))

    # Validate top-level confidence
    try:
        confidence = float(data["confidence"])
        if not (0.0 <= confidence <= 1.0):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise GraniteResponseError(
            f"Prioritization response has invalid confidence "
            f"'{data.get('confidence')}'."
        ) from exc

    overall_reasoning = str(data.get("overall_reasoning", ""))
    if not overall_reasoning:
        raise GraniteResponseError(
            "Prioritization response has empty overall_reasoning."
        )

    decision_factors = data.get("decision_factors", [])
    if not isinstance(decision_factors, list):
        decision_factors = []
    decision_factors = [str(f) for f in decision_factors if f]

    try:
        return CandidatePrioritization(
            ranked_products=ranked,
            overall_reasoning=overall_reasoning,
            confidence=confidence,
            decision_factors=decision_factors,
            candidate_count=len(valid_ids),
        )
    except Exception as exc:  # noqa: BLE001
        raise GraniteResponseError(
            f"Prioritization response failed CandidatePrioritization validation: {exc}"
        ) from exc
