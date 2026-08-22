from datetime import datetime
from pydantic import BaseModel, Field


class LinkState(BaseModel):
    """Snapshot of the communication link at a point in time.

    link_goodput_bps is a link-level quantity derived analytically from
    nominal_data_rate_bps * protocol_efficiency. It does not depend on
    individual packet size or BER.
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
    latency_s: float = Field(ge=0.0, description="One-way propagation latency in seconds")
    link_stability: float = Field(ge=0.0, le=1.0, description="Link stability score [0, 1]")
    remaining_window_s: float = Field(ge=0.0, description="Remaining communication window in seconds")
