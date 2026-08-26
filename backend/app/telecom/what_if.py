"""What-if sensitivity analysis helper for link state.

This module provides ``apply_link_what_if()``, which wraps ``TelecomEngine``
to implement the Phase 3 override-precedence rules for the
``POST /plans/what-if`` endpoint.

Design rationale
----------------
``TelecomEngine.compute()`` is the authoritative physical pipeline:

    snr_db → Eb/N0 → BPSK BER → LinkState

``TelecomEngine`` does NOT accept a raw BER input — BER is always a *derived*
quantity in the normal path.

A BER what-if override is a SENSITIVITY ANALYSIS, not a physical measurement.
It intentionally replaces the derived BER with an explicit hypothetical value
so that analysts can ask "what would evaluation metrics look like at BER=X?".

The override precedence rules (Phase 3, Part A, section 4):

1. No override:
   Use the normal TelecomEngine-derived LinkState unchanged.

2. SNR only:
   Rebuild the LinkState with the new SNR.  TelecomEngine re-derives Eb/N0
   and BER normally.  All other independent fields (goodput, latency, etc.)
   remain unchanged.

3. BER only:
   Compute the baseline LinkState first from the original SNR.
   Then replace ONLY ``LinkState.ber`` with the explicit hypothetical BER.
   ``snr_db``, ``eb_n0_db``, and ``link_goodput_bps`` are unchanged.
   This is intentionally a sensitivity analysis — the reported SNR/Eb/N0
   reflect the actual channel, but the BER used in evaluation is overridden.

4. SNR + BER:
   Apply the SNR override first (TelecomEngine re-derives Eb/N0 and BER).
   Then replace ``LinkState.ber`` with the explicit BER.
   Explicit BER has FINAL precedence.

BER validation for what-if input
---------------------------------
For BPSK/AWGN the theoretical BER range is [0, 0.5].  An explicit BER > 0.5
or < 0 has no physical meaning in this model and is rejected with a ValueError.
NaN and ±infinity are also rejected.

Note: ``packet_success_probability()`` in formulas.py accepts [0, 1] and may
remain that way for backward compatibility.  The stricter [0, 0.5] constraint
applies only to BPSK model what-if input validated here.
"""

from __future__ import annotations

import math

from ..config import GCSIConfig
from ..models.link_state import LinkState
from ..telecom.engine import TelecomEngine


# ---------------------------------------------------------------------------
# BER validation
# ---------------------------------------------------------------------------

_BPSK_BER_MIN: float = 0.0
_BPSK_BER_MAX: float = 0.5


def _validate_what_if_ber(ber: float) -> None:
    """Validate an explicit what-if BER value for BPSK/AWGN context.

    Args:
        ber: Hypothetical BER to validate.

    Raises:
        ValueError: if ``ber`` is not finite, < 0, or > 0.5.
    """
    if not math.isfinite(ber):
        raise ValueError(
            f"what-if ber must be finite; got {ber!r}.  "
            "NaN and ±infinity are not valid BPSK model inputs."
        )
    if not (_BPSK_BER_MIN <= ber <= _BPSK_BER_MAX):
        raise ValueError(
            f"what-if ber must be in [0, 0.5] for BPSK/AWGN model; got {ber}.  "
            "Values outside this range have no physical meaning."
        )


def _validate_what_if_snr(snr_db: float) -> None:
    """Validate an explicit what-if SNR value.

    Args:
        snr_db: Hypothetical SNR in dB to validate.

    Raises:
        ValueError: if ``snr_db`` is not finite.
    """
    if not math.isfinite(snr_db):
        raise ValueError(
            f"what-if snr_db must be finite; got {snr_db!r}.  "
            "NaN and ±infinity are not valid inputs."
        )


# ---------------------------------------------------------------------------
# Typed what-if context
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field  # noqa: E402


class WhatIfLinkContext(BaseModel):
    """Typed provenance record for a what-if link override.

    Explains exactly what the backend evaluated for ``POST /plans/what-if``
    so that reviewers/operators never need to guess whether an override was
    applied.

    Fields
    ------
    base_snr_db:
        SNR from the active scenario (before any override).
    base_ber:
        BER derived from the baseline scenario SNR via the normal TelecomEngine
        pipeline (before any override).
    requested_snr_db:
        SNR value supplied in the request (``null`` if not supplied).
    requested_ber:
        BER value supplied in the request (``null`` if not supplied).
    effective_snr_db:
        SNR actually used to (re-)derive Eb/N0.  Equals ``requested_snr_db``
        when that was supplied; otherwise equals ``base_snr_db``.
    effective_eb_n0_db:
        Eb/N0 derived from ``effective_snr_db`` via the normal pipeline.
    derived_ber_before_override:
        BER derived by TelecomEngine from ``effective_snr_db``.  This is what
        BER would be if no explicit BER override were requested.
    effective_ber:
        The BER that was actually passed to PlanEvaluator.  Equals
        ``requested_ber`` when that was supplied; otherwise equals
        ``derived_ber_before_override``.
    snr_override_applied:
        ``True`` when ``requested_snr_db`` was supplied and used.
    ber_override_applied:
        ``True`` when ``requested_ber`` replaced the derived BER.
    """

    base_snr_db: float = Field(description="SNR from baseline scenario (dB)")
    base_ber: float = Field(description="BER derived from baseline scenario SNR")

    requested_snr_db: float | None = Field(
        default=None, description="SNR override requested (dB), null if not supplied"
    )
    requested_ber: float | None = Field(
        default=None, description="BER override requested, null if not supplied"
    )

    effective_snr_db: float = Field(
        description="SNR used for Eb/N0 derivation (overridden or baseline)"
    )
    effective_eb_n0_db: float = Field(
        description="Eb/N0 derived from effective_snr_db (dB)"
    )
    derived_ber_before_override: float = Field(
        description="BER derived by TelecomEngine from effective_snr_db"
    )
    effective_ber: float = Field(
        description=(
            "BER passed to PlanEvaluator.  "
            "Equals requested_ber when supplied (explicit BER has final precedence); "
            "otherwise equals derived_ber_before_override."
        )
    )

    snr_override_applied: bool = Field(
        description="True when requested_snr_db replaced the baseline SNR"
    )
    ber_override_applied: bool = Field(
        description="True when requested_ber replaced the derived BER"
    )


# ---------------------------------------------------------------------------
# Main helper
# ---------------------------------------------------------------------------


def apply_link_what_if(
    link_inputs: dict,
    *,
    snr_db: float | None,
    ber: float | None,
    config: GCSIConfig | None = None,
) -> tuple[LinkState, WhatIfLinkContext]:
    """Build a hypothetical ``LinkState`` for sensitivity analysis.

    This is the single authoritative what-if construction path.  It must be
    used by ``POST /plans/what-if``; the normal scenario loading path
    (``TelecomEngine.compute()``) must never accept a raw BER input.

    Override precedence (Phase 3, Part A, section 4):

    1. No override → baseline LinkState from ``TelecomEngine``.
    2. SNR only   → rebuild with new SNR; Eb/N0 and BER re-derived normally.
    3. BER only   → baseline first; then replace only ``LinkState.ber``.
    4. SNR + BER  → SNR applied first; TelecomEngine re-derives; then BER
                    replaces derived BER.  Explicit BER has final precedence.

    Args:
        link_inputs: Raw link inputs dict from the active scenario (read-only).
        snr_db:      Hypothetical SNR override in dB.  ``None`` = no override.
        ber:         Hypothetical BER override in [0, 0.5].  ``None`` = no override.
        config:      GCSI configuration.  Uses defaults when ``None``.

    Returns:
        A ``(hypothetical_link_state, context)`` tuple where ``context`` is the
        typed ``WhatIfLinkContext`` provenance record.

    Raises:
        ValueError: if ``snr_db`` is non-finite or ``ber`` is outside [0, 0.5].
        KeyError:   propagated from ``TelecomEngine`` for missing required inputs.
    """
    # --- Validate overrides before any computation ---
    if snr_db is not None:
        _validate_what_if_snr(snr_db)
    if ber is not None:
        _validate_what_if_ber(ber)

    cfg = config or GCSIConfig()
    engine = TelecomEngine(cfg)

    # --- Baseline (from original link_inputs, never mutated) ---
    baseline_link = engine.compute(link_inputs)
    base_snr_db = baseline_link.snr_db
    base_ber = baseline_link.ber

    # --- Apply SNR override (if any) ---
    snr_override_applied = snr_db is not None
    if snr_override_applied:
        modified_inputs = dict(link_inputs)
        modified_inputs["snr_db"] = snr_db
        snr_derived_link = engine.compute(modified_inputs)
    else:
        snr_derived_link = baseline_link

    effective_snr_db = snr_derived_link.snr_db
    effective_eb_n0_db = snr_derived_link.eb_n0_db
    derived_ber_before_override = snr_derived_link.ber

    # --- Apply BER override (if any) — explicit BER has final precedence ---
    ber_override_applied = ber is not None
    if ber_override_applied:
        effective_ber = ber
        # Replace only the BER field; keep snr_db, eb_n0_db, goodput, etc.
        hypothetical_link = snr_derived_link.model_copy(update={"ber": ber})
    else:
        effective_ber = derived_ber_before_override
        hypothetical_link = snr_derived_link

    context = WhatIfLinkContext(
        base_snr_db=base_snr_db,
        base_ber=base_ber,
        requested_snr_db=snr_db,
        requested_ber=ber,
        effective_snr_db=effective_snr_db,
        effective_eb_n0_db=effective_eb_n0_db,
        derived_ber_before_override=derived_ber_before_override,
        effective_ber=effective_ber,
        snr_override_applied=snr_override_applied,
        ber_override_applied=ber_override_applied,
    )

    return hypothetical_link, context
