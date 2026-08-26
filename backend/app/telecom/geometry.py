"""GCSI Communication Geometry — one-way and round-trip propagation delay.

This module is the single authoritative source for the speed-of-light constant
and the two pure geometry helpers.  All production code paths that need
propagation delay or RTT **must** import from here.

Physical basis:
    Propagation delay = distance / c
    RTT               = 2 × propagation delay

Rounding policy:
    The helpers here return full float precision.
    Callers that need rounded values for display or AI context MUST apply
    rounding themselves:

        from .geometry import compute_communication_geometry
        geom = compute_communication_geometry(distance_km)
        ai_block = {
            "propagation_delay_s": round(geom["propagation_delay_s"], 3),
            "round_trip_time_s":   round(geom["round_trip_time_s"],   3),
        }

    Do NOT move rounding into this module.  The physical helper must always
    return full-precision values so that API consumers get exact floats.

Limitations (explicitly not modeled here):
    - Doppler shift
    - Orbital dynamics
    - Atmospheric or ionospheric delay
    - Relativistic corrections
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Exact SI value of the speed of light in a vacuum, in metres per second.
#: Source: BIPM / CODATA — exact by definition since 1983.
SPEED_OF_LIGHT_M_S: float = 299_792_458.0


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def compute_propagation_delay(distance_km: float) -> float:
    """Compute one-way free-space signal travel time from distance.

    Formula:
        propagation_delay_s = distance_km × 1000 / c

    Args:
        distance_km: Spacecraft-to-Earth distance in kilometres.
                     Must be finite and >= 0.

    Returns:
        One-way propagation delay in seconds (full float precision).

    Raises:
        ValueError: if distance_km is negative or not finite.
    """
    import math
    if not math.isfinite(distance_km):
        raise ValueError(f"distance_km must be finite; got {distance_km}")
    if distance_km < 0.0:
        raise ValueError(f"distance_km must be >= 0; got {distance_km}")
    return distance_km * 1_000.0 / SPEED_OF_LIGHT_M_S


def compute_round_trip_time(distance_km: float) -> float:
    """Compute two-way (round-trip) free-space signal travel time from distance.

    Formula:
        round_trip_time_s = 2 × compute_propagation_delay(distance_km)

    Args:
        distance_km: Spacecraft-to-Earth distance in kilometres.
                     Must be finite and >= 0.

    Returns:
        Round-trip time in seconds (full float precision).

    Raises:
        ValueError: if distance_km is negative or not finite.
    """
    return 2.0 * compute_propagation_delay(distance_km)


def compute_communication_geometry(distance_km: float) -> dict:
    """Compute both propagation delay and RTT for a given distance.

    Returns a plain dict with full-precision float values.  Callers should
    round when constructing display strings or AI context blocks.

    Args:
        distance_km: Spacecraft-to-Earth distance in kilometres.

    Returns:
        ``{"propagation_delay_s": float, "round_trip_time_s": float}``

    Raises:
        ValueError: if distance_km is negative or not finite.
    """
    prop = compute_propagation_delay(distance_km)
    return {
        "propagation_delay_s": prop,
        "round_trip_time_s": 2.0 * prop,
    }
