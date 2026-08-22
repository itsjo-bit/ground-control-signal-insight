"""TelecomEngine — converts raw link inputs into a complete LinkState.

The engine is the boundary between raw scenario inputs and the typed
domain model.  It calls formulas.py for all derived quantities and
sources all model constants from GCSIConfig / TelecomConfig.

Responsibilities:
    - Accept raw input fields (measurements / scenario values).
    - Retrieve channel constants from config.
    - Derive Eb/N0, BER, and link goodput.
    - Construct and return a complete LinkState.

Non-responsibilities:
    - Packet-level calculations (p_success, tx_time, cost) are packet-
      specific and must not be computed here; packet size is not part of
      LinkState.
    - No scheduling, no evaluator, no AI calls.
"""

from datetime import datetime

from ..config import GCSIConfig
from ..models.link_state import LinkState
from .formulas import snr_to_eb_n0, bpsk_ber, link_goodput


class TelecomEngine:
    """Deterministic BPSK/AWGN link model.

    Produces a :class:`~backend.app.models.link_state.LinkState` from a
    dictionary of raw link measurements.

    All telecom constants (bandwidth, bit rate, protocol efficiency,
    modulation) are sourced from the provided :class:`~backend.app.config.GCSIConfig`.
    """

    # Required keys in the raw_inputs dictionary
    REQUIRED_INPUTS: frozenset[str] = frozenset({
        "timestamp",
        "snr_db",
        "rssi_dbm",
        "nominal_data_rate_bps",
        "latency_s",
        "link_stability",
        "remaining_window_s",
    })

    def __init__(self, config: GCSIConfig | None = None) -> None:
        """
        Args:
            config: GCSI configuration instance.  If None, defaults are used.
        """
        self._config = config or GCSIConfig()

    def compute(self, raw_inputs: dict) -> LinkState:
        """Convert raw link measurements into a complete LinkState.

        Computation is structured in three explicit sections mirroring the
        architecture plan:

        Section 1 — Raw inputs (validated from the input dict).
        Section 2 — Derived metrics (all computed via formulas.py).
        Section 3 — Config/assumptions (sourced from TelecomConfig).

        Args:
            raw_inputs: Dictionary with keys matching REQUIRED_INPUTS.

        Returns:
            A fully populated :class:`LinkState`.

        Raises:
            KeyError:   if a required input key is missing.
            ValueError: if an input value is out of the valid range.
        """
        # -----------------------------------------------------------------
        # Section 1: Raw inputs
        # -----------------------------------------------------------------
        missing = self.REQUIRED_INPUTS - raw_inputs.keys()
        if missing:
            raise KeyError(f"Missing required raw inputs: {sorted(missing)}")

        timestamp: datetime = raw_inputs["timestamp"]
        snr_db: float = float(raw_inputs["snr_db"])
        rssi_dbm: float = float(raw_inputs["rssi_dbm"])
        nominal_data_rate_bps: float = float(raw_inputs["nominal_data_rate_bps"])
        latency_s: float = float(raw_inputs["latency_s"])
        link_stability: float = float(raw_inputs["link_stability"])
        remaining_window_s: float = float(raw_inputs["remaining_window_s"])

        # -----------------------------------------------------------------
        # Section 3: Config/assumptions (referenced in section 2)
        # -----------------------------------------------------------------
        tc = self._config.telecom
        bandwidth_hz: float = tc.channel_bandwidth_hz
        bit_rate_bps: float = tc.bit_rate_bps
        protocol_efficiency: float = tc.protocol_efficiency
        # modulation is BPSK — validated by TelecomConfig; formulas are modulation-specific

        # -----------------------------------------------------------------
        # Section 2: Derived metrics
        # -----------------------------------------------------------------
        eb_n0_db: float = snr_to_eb_n0(snr_db, bandwidth_hz, bit_rate_bps)
        ber: float = bpsk_ber(eb_n0_db)
        goodput_bps: float = link_goodput(nominal_data_rate_bps, protocol_efficiency)

        # -----------------------------------------------------------------
        # Construct and return LinkState
        # -----------------------------------------------------------------
        return LinkState(
            timestamp=timestamp,
            snr_db=snr_db,
            eb_n0_db=eb_n0_db,
            ber=ber,
            rssi_dbm=rssi_dbm,
            nominal_data_rate_bps=nominal_data_rate_bps,
            link_goodput_bps=goodput_bps,
            latency_s=latency_s,
            link_stability=link_stability,
            remaining_window_s=remaining_window_s,
        )
