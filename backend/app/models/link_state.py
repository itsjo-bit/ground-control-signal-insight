from datetime import datetime
from pydantic import BaseModel, Field


class LinkState(BaseModel):
    """Snapshot of the communication link at a point in time.

    link_goodput_bps is a link-level quantity derived analytically from
    nominal_data_rate_bps * protocol_efficiency. It does not depend on
    individual packet size or BER.

    Terminology note — three distinct latency / delay concepts exist in GCSI:

    * ``latency_s`` (this field) — link-layer / network-layer round-trip
      latency contributed by the communication protocol stack (headers, ACKs,
      framing).  Sourced from scenario ``link_inputs.latency_s`` and randomised
      by ±0.15 s on each scenario reset.  For deep-space scenarios this is a
      small protocol overhead value (~1–2 s), NOT the free-space signal travel
      time.

    * ``propagation_delay_s`` — one-way free-space signal travel time,
      computed from ``Scenario.distance_km`` in ``GET /state`` and displayed
      in ``SignalGeometryBlock``.  For a spacecraft 54 million km from Earth
      this is approximately 180 s.  This value is never stored in LinkState.

    * ``round_trip_time_s`` — propagation RTT only (2 × propagation_delay_s),
      also returned by ``GET /state``.  This is not an ACK/delivery guarantee.

    Do NOT derive ``propagation_delay_s`` from ``latency_s``; they are
    independent quantities with different physical meanings.
    """

    timestamp: datetime
    snr_db: float = Field(description="Signal-to-noise ratio in dB")
    eb_n0_db: float = Field(description="Energy per bit to noise power spectral density ratio in dB")
    ber: float = Field(ge=0.0, le=1.0, description="Bit error rate [0, 1]")
    rssi_dbm: float = Field(description="Received signal strength indicator in dBm")
    nominal_data_rate_bps: float = Field(gt=0.0, description="Nominal channel data rate in bps")
    link_goodput_bps: float = Field(
        gt=0.0,
        description=(
            "Effective link throughput in bps after protocol overhead "
            "(= nominal_data_rate_bps * protocol_efficiency)"
        ),
    )
    latency_s: float = Field(
        ge=0.0,
        description=(
            "Link-layer / protocol-stack latency in seconds.  "
            "This is communication-protocol overhead (headers, ACKs, framing), "
            "NOT free-space signal propagation delay.  "
            "For spacecraft scenarios the true one-way propagation delay "
            "(distance_km × 1000 / c) is computed separately and exposed via "
            "GET /state as propagation_delay_s."
        ),
    )
    link_stability: float = Field(ge=0.0, le=1.0, description="Link stability score [0, 1]")
    remaining_window_s: float = Field(ge=0.0, description="Remaining communication window in seconds")
