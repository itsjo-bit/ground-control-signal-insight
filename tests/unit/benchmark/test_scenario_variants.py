"""Tests for benchmark scenario variant generator.

Covers:
- Deterministic scenario generation (same input → same output)
- Base scenario is never mutated
- Capacity ratio targeting and verification
- All three anomaly modes
- Deadline scale transformation
- 12 core scenarios generated correctly
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.app.benchmark.scenario_variants import (
    AnomalyMode,
    CAPACITY_TOLERANCE,
    ScenarioVariantGenerator,
    _apply_anomaly_mode,
    _sha256_scenario,
)
from backend.app.models.anomaly_event import AnomalyEvent

# Path to the real base scenario
BASE_SCENARIO_PATH = Path("data/scenarios/mission_data_v3.json")


def _anomaly(aid: str, *, status: str = "active", severity: float = 0.9) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=aid,
        subsystem="propulsion",
        severity=severity,
        detected_at_s=0.0,
        description="test",
        status=status,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_generator() -> ScenarioVariantGenerator:
    if not BASE_SCENARIO_PATH.exists():
        pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")
    return ScenarioVariantGenerator(
        base_scenario_path=BASE_SCENARIO_PATH,
        capacity_ratios=(0.35, 0.60, 0.90, 1.20),
        anomaly_modes=(AnomalyMode.ORIGINAL, AnomalyMode.NO_ANOMALY, AnomalyMode.RESOLVED_DECOY),
        deadline_scales=(1.0,),
    )


# ---------------------------------------------------------------------------
# Test: base scenario is never mutated
# ---------------------------------------------------------------------------


class TestBaseScenarioImmutability:
    def test_base_scenario_sha_unchanged_after_generate(self):
        gen = _make_generator()
        original_sha = gen.base_sha256
        variants = gen.generate_all()
        assert gen.base_sha256 == original_sha

    def test_base_scenario_object_not_mutated(self):
        gen = _make_generator()
        original_sha = _sha256_scenario(gen.base_scenario)
        _ = gen.generate_all()
        after_sha = _sha256_scenario(gen.base_scenario)
        assert original_sha == after_sha


# ---------------------------------------------------------------------------
# Test: core matrix generates 12 scenarios
# ---------------------------------------------------------------------------


class TestCoreMatrix:
    def test_generates_12_core_scenarios(self):
        gen = _make_generator()
        variants = gen.generate_all()
        assert len(variants) == 12

    def test_scenario_ids_are_deterministic(self):
        gen1 = _make_generator()
        gen2 = _make_generator()
        ids1 = {v.spec.scenario_id for v in gen1.generate_all()}
        ids2 = {v.spec.scenario_id for v in gen2.generate_all()}
        assert ids1 == ids2

    def test_expected_scenario_ids_present(self):
        gen = _make_generator()
        variants = gen.generate_all()
        ids = {v.spec.scenario_id for v in variants}
        expected = {
            "CAP035_ORIGINAL", "CAP060_ORIGINAL", "CAP090_ORIGINAL", "CAP120_ORIGINAL",
            "CAP035_NOANOM", "CAP060_NOANOM", "CAP090_NOANOM", "CAP120_NOANOM",
            "CAP035_DECOY", "CAP060_DECOY", "CAP090_DECOY", "CAP120_DECOY",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Test: capacity ratio targeting
# ---------------------------------------------------------------------------


class TestCapacityRatios:
    @pytest.mark.parametrize("cap", [0.35, 0.60, 0.90, 1.20])
    def test_capacity_ratio_within_tolerance(self, cap: float):
        gen = _make_generator()
        variants = gen.generate_all()
        matching = [v for v in variants if abs(v.spec.capacity_ratio - cap) < 0.01]
        assert matching, f"No variant found for capacity ratio {cap}"
        for v in matching:
            assert abs(v.spec.actual_capacity_ratio - cap) <= CAPACITY_TOLERANCE + 1e-6, (
                f"Actual ratio {v.spec.actual_capacity_ratio:.4f} too far from target {cap}"
            )

    def test_communication_window_consistent(self):
        gen = _make_generator()
        for v in gen.generate_all():
            expected = v.spec.available_capacity_bits / v.spec.link_goodput_bps
            assert abs(expected - v.spec.communication_window_s) < 1e-6

    def test_cap035_label(self):
        gen = _make_generator()
        variants = gen.generate_all()
        cap35 = [v for v in variants if abs(v.spec.capacity_ratio - 0.35) < 0.01]
        for v in cap35:
            assert v.spec.scenario_id.startswith("CAP035_")


# ---------------------------------------------------------------------------
# Test: anomaly modes
# ---------------------------------------------------------------------------


class TestAnomalyModes:
    def test_original_mode_unchanged(self):
        gen = _make_generator()
        orig_anomaly_statuses = {ae.anomaly_id: ae.status for ae in gen.base_scenario.anomalies}

        orig_variants = [
            v for v in gen.generate_all() if v.spec.anomaly_mode == AnomalyMode.ORIGINAL
        ]
        for v in orig_variants:
            for ae in v.scenario.anomalies:
                assert ae.status == orig_anomaly_statuses[ae.anomaly_id], (
                    f"ORIGINAL mode changed status of {ae.anomaly_id}"
                )

    def test_noanom_mode_resolves_all_applicable(self):
        from backend.app.domain.anomaly_policy import is_applicable_anomaly

        gen = _make_generator()
        base = gen.base_scenario
        orig_applicable_ids = {
            ae.anomaly_id for ae in base.anomalies if is_applicable_anomaly(ae)
        }
        noanom_variants = [
            v for v in gen.generate_all() if v.spec.anomaly_mode == AnomalyMode.NO_ANOMALY
        ]
        for v in noanom_variants:
            for ae in v.scenario.anomalies:
                if ae.anomaly_id in orig_applicable_ids:
                    assert ae.status == "resolved", (
                        f"NOANOM: {ae.anomaly_id} status={ae.status}, expected resolved"
                    )

    def test_noanom_mode_preserves_anomaly_id_links(self):
        """Historical anomaly_id on DataProduct must remain after NOANOM mode."""
        gen = _make_generator()
        noanom = [v for v in gen.generate_all() if v.spec.anomaly_mode == AnomalyMode.NO_ANOMALY]
        orig = [v for v in gen.generate_all() if v.spec.anomaly_mode == AnomalyMode.ORIGINAL]

        if not noanom or not orig:
            pytest.skip("Not enough variants")

        # DataProduct anomaly_id links must be identical
        noanom_v = noanom[0]
        orig_v = next(
            v for v in orig
            if abs(v.spec.capacity_ratio - noanom_v.spec.capacity_ratio) < 0.01
        )
        for dp_n, dp_o in zip(noanom_v.scenario.data_products, orig_v.scenario.data_products):
            assert dp_n.anomaly_id == dp_o.anomaly_id

    def test_decoy_mode_resolves_only_highest_severity(self):
        from backend.app.domain.anomaly_policy import is_applicable_anomaly

        gen = _make_generator()
        base = gen.base_scenario
        applicable = [ae for ae in base.anomalies if is_applicable_anomaly(ae)]
        if not applicable:
            pytest.skip("Base scenario has no applicable anomalies")

        # Find expected decoy target: highest severity, tie-break by anomaly_id
        target = sorted(applicable, key=lambda ae: (-ae.severity, ae.anomaly_id))[0]

        decoy_variants = [
            v for v in gen.generate_all() if v.spec.anomaly_mode == AnomalyMode.RESOLVED_DECOY
        ]
        for v in decoy_variants:
            resolved_count = 0
            for ae in v.scenario.anomalies:
                was_applicable = any(
                    a.anomaly_id == ae.anomaly_id and is_applicable_anomaly(a)
                    for a in base.anomalies
                )
                if was_applicable:
                    if ae.anomaly_id == target.anomaly_id:
                        assert ae.status == "resolved", (
                            f"DECOY: highest-severity {ae.anomaly_id} not resolved"
                        )
                        resolved_count += 1
                    else:
                        assert ae.status != "resolved", (
                            f"DECOY: non-target anomaly {ae.anomaly_id} was resolved"
                        )
            assert resolved_count == 1

    def test_full_suite_has_24_scenarios(self):
        from backend.app.benchmark.scenario_variants import FULL_DEADLINE_SCALES
        gen = ScenarioVariantGenerator(
            base_scenario_path=BASE_SCENARIO_PATH,
            deadline_scales=FULL_DEADLINE_SCALES,
        )
        variants = gen.generate_all()
        assert len(variants) == 24


# ---------------------------------------------------------------------------
# Test: _apply_anomaly_mode unit tests (no disk I/O)
# ---------------------------------------------------------------------------


class TestApplyAnomalyModeUnit:
    def test_original_returns_copy(self):
        a1 = _anomaly("A1", status="active")
        a2 = _anomaly("A2", status="resolved")
        result = _apply_anomaly_mode([a1, a2], AnomalyMode.ORIGINAL)
        assert len(result) == 2
        assert result[0].status == "active"
        assert result[1].status == "resolved"
        # Must be copies, not same objects
        assert result[0] is not a1

    def test_noanom_resolves_active(self):
        a1 = _anomaly("A1", status="active")
        a2 = _anomaly("A2", status="resolved")
        result = _apply_anomaly_mode([a1, a2], AnomalyMode.NO_ANOMALY)
        assert result[0].status == "resolved"  # was active → resolved
        assert result[1].status == "resolved"  # already resolved

    def test_decoy_resolves_highest_severity(self):
        high = _anomaly("HIGH", status="active", severity=0.9)
        low = _anomaly("LOW", status="active", severity=0.5)
        result = _apply_anomaly_mode([high, low], AnomalyMode.RESOLVED_DECOY)
        statuses = {ae.anomaly_id: ae.status for ae in result}
        assert statuses["HIGH"] == "resolved"
        assert statuses["LOW"] == "active"

    def test_decoy_no_applicable_returns_unchanged(self):
        resolved = _anomaly("A1", status="resolved")
        result = _apply_anomaly_mode([resolved], AnomalyMode.RESOLVED_DECOY)
        assert result[0].status == "resolved"
