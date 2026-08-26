"""Benchmark scenario variant generator.

Produces 12 deterministic core benchmark scenarios from a base scenario
by applying controlled factorial transformations:

  4 capacity levels × 3 anomaly modes = 12 core scenarios

Capacity levels (available_capacity / total_queued_bits):
  0.35  severely constrained
  0.60  strongly constrained
  0.90  moderately constrained
  1.20  near/unconstrained (negative control)

Anomaly modes:
  ORIGINAL      — v3 anomaly state unchanged
  NOANOM        — all applicable anomalies set to resolved
                  (tests whether AI advantage disappears without active anomalies)
  DECOY         — highest-severity applicable anomaly set to resolved only
                  (tests whether prioritizers avoid overreacting to historical linkage)

Scenario naming is deterministic:
  CAP035_ORIGINAL  CAP060_ORIGINAL  CAP090_ORIGINAL  CAP120_ORIGINAL
  CAP035_NOANOM    ...
  CAP035_DECOY     ...

An optional deadline_scale factor extends the matrix to 24 scenarios:
  new_deadline_s = original_deadline_s × deadline_scale

Invariants:
  - Base scenario is NEVER mutated (deep-copy before transformation).
  - Each generated variant records its actual_capacity_ratio.
  - The generator asserts actual ratio is within CAPACITY_TOLERANCE of the target.

Capacity computation:
  total_queued_bits  = sum(dp.size_bits for dp in data_products)
  target_capacity_bits = capacity_ratio × total_queued_bits
  window_s = target_capacity_bits / link_goodput_bps

All link inputs (SNR, BER, goodput, etc.) are derived from the base scenario's
link_inputs through TelecomEngine — unchanged.  Only remaining_window_s changes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Sequence

from ..models.anomaly_event import AnomalyEvent
from ..models.data_product import DataProduct
from ..models.scenario import Scenario
from ..simulation.scenario_loader import ScenarioLoader
from ..telecom.engine import TelecomEngine
from .models import AnomalyMode, ScenarioVariantSpec

# Tolerance for capacity ratio assertion
CAPACITY_TOLERANCE: float = 0.01  # ±1% of target ratio

# Default capacity ratios for the core benchmark matrix
DEFAULT_CAPACITY_RATIOS: tuple[float, ...] = (0.35, 0.60, 0.90, 1.20)

# Default anomaly modes
DEFAULT_ANOMALY_MODES: tuple[AnomalyMode, ...] = (
    AnomalyMode.ORIGINAL,
    AnomalyMode.NO_ANOMALY,
    AnomalyMode.RESOLVED_DECOY,
)

# Default deadline scales (1.0 = core; [1.0, 0.5] = full)
DEFAULT_DEADLINE_SCALES: tuple[float, ...] = (1.0,)
FULL_DEADLINE_SCALES: tuple[float, ...] = (1.0, 0.5)


def _sha256_scenario(scenario: Scenario) -> str:
    """Return the SHA-256 of the canonical JSON representation of a scenario."""
    raw = scenario.model_dump_json()
    return hashlib.sha256(raw.encode()).hexdigest()


def _scenario_id_label(capacity_ratio: float) -> str:
    """Return the 'CAP035'-style label for a capacity ratio."""
    return f"CAP{int(round(capacity_ratio * 100)):03d}"


def _apply_anomaly_mode(
    anomalies: list[AnomalyEvent],
    mode: AnomalyMode,
) -> list[AnomalyEvent]:
    """Return a NEW list of anomaly events transformed according to mode.

    ORIGINAL:
        Anomalies returned unchanged.

    NO_ANOMALY:
        All anomalies that are currently applicable (active or monitoring)
        have their status changed to 'resolved'.
        Historical product anomaly_id links are NOT cleared — they remain
        on the DataProduct objects.

    RESOLVED_DECOY:
        Only the single highest-severity applicable anomaly is set to
        'resolved'.  All other anomalies remain unchanged.
        This tests whether prioritizers correctly ignore resolved historical
        linkage while still responding to remaining active anomalies.
        Tie-breaking: if two anomalies share the maximum severity, the one
        with the lexicographically smallest anomaly_id is resolved.
        Documents the transformation so the result is reproducible.

    Args:
        anomalies: Original anomaly list (will not be mutated).
        mode:      Target transformation mode.

    Returns:
        A NEW list of :class:`AnomalyEvent` objects (deep copies).
    """
    from ..domain.anomaly_policy import is_applicable_anomaly

    if mode == AnomalyMode.ORIGINAL:
        return [ae.model_copy(deep=True) for ae in anomalies]

    applicable = [ae for ae in anomalies if is_applicable_anomaly(ae)]

    if mode == AnomalyMode.NO_ANOMALY:
        result = []
        applicable_ids = {ae.anomaly_id for ae in applicable}
        for ae in anomalies:
            copy_ae = ae.model_copy(deep=True)
            if copy_ae.anomaly_id in applicable_ids:
                copy_ae = AnomalyEvent(
                    anomaly_id=copy_ae.anomaly_id,
                    subsystem=copy_ae.subsystem,
                    severity=copy_ae.severity,
                    detected_at_s=copy_ae.detected_at_s,
                    description=copy_ae.description,
                    status="resolved",
                    related_product_ids=list(copy_ae.related_product_ids),
                )
            result.append(copy_ae)
        return result

    if mode == AnomalyMode.RESOLVED_DECOY:
        if not applicable:
            # No applicable anomalies; nothing to resolve
            return [ae.model_copy(deep=True) for ae in anomalies]
        # Find highest-severity applicable anomaly; tie-break by anomaly_id (lexicographic)
        target = sorted(applicable, key=lambda ae: (-ae.severity, ae.anomaly_id))[0]
        result = []
        for ae in anomalies:
            copy_ae = ae.model_copy(deep=True)
            if copy_ae.anomaly_id == target.anomaly_id:
                copy_ae = AnomalyEvent(
                    anomaly_id=copy_ae.anomaly_id,
                    subsystem=copy_ae.subsystem,
                    severity=copy_ae.severity,
                    detected_at_s=copy_ae.detected_at_s,
                    description=copy_ae.description,
                    status="resolved",
                    related_product_ids=list(copy_ae.related_product_ids),
                )
            result.append(copy_ae)
        return result

    raise ValueError(f"Unknown anomaly mode: {mode}")  # pragma: no cover


def _apply_deadline_scale(products: list[DataProduct], scale: float) -> list[DataProduct]:
    """Return new DataProduct list with deadline_s scaled by *scale*."""
    if scale == 1.0:
        return [p.model_copy(deep=True) for p in products]
    result = []
    for p in products:
        d = p.model_dump(mode="python")
        d["deadline_s"] = max(1.0, p.deadline_s * scale)  # floor at 1 s
        result.append(DataProduct.model_validate(d))
    return result


class BenchmarkScenarioVariant:
    """A generated benchmark scenario variant.

    Not a Pydantic model — holds the full Scenario object plus its descriptor.
    The Scenario object is a deep copy and can be freely modified by the runner
    without touching the base scenario.
    """

    __slots__ = ("spec", "scenario")

    def __init__(self, spec: ScenarioVariantSpec, scenario: Scenario) -> None:
        self.spec = spec
        self.scenario = scenario


class ScenarioVariantGenerator:
    """Generate benchmark scenario variants from a base scenario.

    The base scenario is loaded from disk on construction and stored
    internally.  All variant generation creates deep copies — the original
    Scenario object is NEVER mutated.

    Args:
        base_scenario_path: Path to the base scenario JSON file.
        capacity_ratios:    Target capacity ratios.
        anomaly_modes:      Anomaly transformation modes.
        deadline_scales:    Deadline scale factors.
    """

    def __init__(
        self,
        base_scenario_path: str | Path,
        *,
        capacity_ratios: Sequence[float] = DEFAULT_CAPACITY_RATIOS,
        anomaly_modes: Sequence[AnomalyMode] = DEFAULT_ANOMALY_MODES,
        deadline_scales: Sequence[float] = DEFAULT_DEADLINE_SCALES,
    ) -> None:
        loader = ScenarioLoader()
        self._base_scenario: Scenario = loader.load(str(base_scenario_path))
        self._base_sha256: str = _sha256_scenario(self._base_scenario)
        self._capacity_ratios = tuple(capacity_ratios)
        self._anomaly_modes = tuple(anomaly_modes)
        self._deadline_scales = tuple(deadline_scales)

        # Compute link state once from base scenario (link inputs unchanged across variants)
        engine = TelecomEngine()
        self._base_link_state = engine.compute(self._base_scenario.link_inputs)

    @property
    def base_scenario(self) -> Scenario:
        """The original base scenario (read-only reference)."""
        return self._base_scenario

    @property
    def base_sha256(self) -> str:
        return self._base_sha256

    def generate_all(self) -> list[BenchmarkScenarioVariant]:
        """Generate all variants according to the configured matrix."""
        variants = []
        for deadline_scale in self._deadline_scales:
            for anomaly_mode in self._anomaly_modes:
                for capacity_ratio in self._capacity_ratios:
                    variant = self._build_variant(
                        capacity_ratio=capacity_ratio,
                        anomaly_mode=anomaly_mode,
                        deadline_scale=deadline_scale,
                    )
                    variants.append(variant)
        return variants

    def generate_core(self) -> list[BenchmarkScenarioVariant]:
        """Generate the 12-scenario core benchmark matrix (deadline_scale=1.0 only)."""
        gen = ScenarioVariantGenerator(
            base_scenario_path="",  # unused — will override below
            capacity_ratios=self._capacity_ratios,
            anomaly_modes=self._anomaly_modes,
            deadline_scales=(1.0,),
        )
        gen._base_scenario = self._base_scenario
        gen._base_sha256 = self._base_sha256
        gen._base_link_state = self._base_link_state
        return gen.generate_all()

    def _build_variant(
        self,
        capacity_ratio: float,
        anomaly_mode: AnomalyMode,
        deadline_scale: float,
    ) -> BenchmarkScenarioVariant:
        """Build one scenario variant as a deep-copy of the base scenario.

        Steps:
        1. Apply anomaly mode transformation.
        2. Apply deadline scale (if != 1.0).
        3. Compute required communication window.
        4. Build new Scenario with updated link_inputs and anomalies.
        5. Verify actual capacity ratio is within tolerance.
        """
        base = self._base_scenario
        link_state = self._base_link_state

        # Total queued bits from all data products
        data_products = base.data_products
        total_queued_bits = sum(dp.size_bits for dp in data_products)
        if total_queued_bits == 0:
            raise ValueError("Base scenario has no data products with non-zero size_bits")

        goodput_bps = link_state.link_goodput_bps
        if goodput_bps <= 0:
            raise ValueError(f"Link goodput is zero or negative: {goodput_bps}")

        # Compute target window
        target_capacity_bits = capacity_ratio * total_queued_bits
        window_s = target_capacity_bits / goodput_bps
        # Floor at 1 second to avoid degenerate scenarios
        window_s = max(1.0, window_s)

        # Measure actual ratio
        available_capacity_bits = goodput_bps * window_s
        actual_ratio = available_capacity_bits / total_queued_bits

        # Assert within tolerance
        if abs(actual_ratio - capacity_ratio) > CAPACITY_TOLERANCE + 1e-6:
            raise ValueError(
                f"Capacity ratio assertion failed: target={capacity_ratio:.4f} "
                f"actual={actual_ratio:.4f} (tolerance={CAPACITY_TOLERANCE})"
            )

        # Transform anomalies (deep copy — original unchanged)
        new_anomalies = _apply_anomaly_mode(list(base.anomalies), anomaly_mode)

        # Transform deadlines if scale != 1.0
        if deadline_scale != 1.0:
            new_products = _apply_deadline_scale(list(data_products), deadline_scale)
        else:
            new_products = [p.model_copy(deep=True) for p in data_products]

        # Build new link_inputs with updated window
        new_link_inputs = dict(base.link_inputs)
        new_link_inputs["remaining_window_s"] = window_s

        # Build new mission_state with updated windows
        ms = base.mission_state
        ms_dict = ms.model_dump(mode="python")
        ms_dict["comm_window_remaining_s"] = window_s
        ms_dict["event_time_remaining_s"] = window_s
        from ..models.mission_state import MissionState
        new_mission_state = MissionState.model_validate(ms_dict)

        # Build scenario_id
        cap_label = _scenario_id_label(capacity_ratio)
        dl_suffix = f"_DL{int(deadline_scale * 100):03d}" if deadline_scale != 1.0 else ""
        scenario_id = f"{cap_label}_{anomaly_mode.value}{dl_suffix}"

        # Build new Scenario (preserves packets=[] from v3)
        from ..models.scenario import Scenario as _Scenario
        new_scenario = _Scenario(
            scenario_id=scenario_id,
            simulated=True,
            distance_km=base.distance_km,
            link_inputs=new_link_inputs,
            mission_state=new_mission_state,
            packets=list(base.packets),  # empty for v3
            data_products=new_products,
            anomalies=new_anomalies,
        )

        spec = ScenarioVariantSpec(
            scenario_id=scenario_id,
            capacity_ratio=capacity_ratio,
            anomaly_mode=anomaly_mode,
            deadline_scale=deadline_scale,
            total_queued_bits=total_queued_bits,
            link_goodput_bps=goodput_bps,
            communication_window_s=window_s,
            available_capacity_bits=available_capacity_bits,
            actual_capacity_ratio=actual_ratio,
            base_scenario_id=base.scenario_id,
            base_scenario_sha256=self._base_sha256,
        )

        return BenchmarkScenarioVariant(spec=spec, scenario=new_scenario)
