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


# ---------------------------------------------------------------------------
# Phase 7D: Benchmark provenance integrity regression
# ---------------------------------------------------------------------------
# These constants document the two authoritative SHA256 values for
# mission_data_v3.json.  They measure DIFFERENT representations of the same
# source data and are intentionally different:
#
#   _V3_FILE_SHA256   — SHA256 of the raw file bytes on disk.
#                       Enforced by test_phase4_2e_ground_reception.py and
#                       test_phase4_2f5_ground_reception.py.
#                       Must equal `hashlib.sha256(path.read_bytes()).hexdigest()`.
#
#   _V3_MODEL_SHA256  — SHA256 of scenario.model_dump_json() (the Pydantic
#                       object re-serialized).  This is what the benchmark
#                       runner records as `base_scenario_sha256` in every
#                       manifest.json and raw_results.jsonl trial record.
#                       Computed by _sha256_scenario() in scenario_variants.py.
#
# IMPORTANT: Modifying mission_data_v3.json will change BOTH values.
# Any such change requires operator review and freeze-stability assessment.

_V3_FILE_SHA256 = "dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9"
_V3_MODEL_SHA256 = "de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08"

# SHA256 recorded in the frozen benchmark result manifest
# benchmarks/results/run-20260826-110706-530179c2/manifest.json
_FROZEN_MANIFEST_BASE_SCENARIO_SHA256 = (
    "de43388647287c3b99849c0fc9b940ce7234acd4be6ae9d212befa5b6eac3b08"
)


class TestBenchmarkProvenanceIntegrity:
    """Phase 7D: Verify the two authoritative SHA256 hashes for mission_data_v3.json.

    These two hashes are intentionally different — they hash different representations
    of the same source file.  A test failure here means mission_data_v3.json has been
    mutated and requires operator review before any benchmark claim is valid.
    """

    def test_file_bytes_sha256_matches_frozen_constant(self):
        """Raw file bytes of mission_data_v3.json must equal _V3_FILE_SHA256.

        This is the same assertion made by test_phase4_2e_ground_reception.py and
        test_phase4_2f5_ground_reception.py.  It is reproduced here as an explicit
        benchmark-provenance checkpoint so that the connection between the file-byte
        hash and the benchmark model-sha is visible in one place.
        """
        if not BASE_SCENARIO_PATH.exists():
            pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")
        actual = hashlib.sha256(BASE_SCENARIO_PATH.read_bytes()).hexdigest()
        assert actual == _V3_FILE_SHA256, (
            f"data/scenarios/mission_data_v3.json raw-bytes SHA256 changed!\n"
            f"  Expected (frozen): {_V3_FILE_SHA256}\n"
            f"  Actual:            {actual}\n"
            "This file is the frozen benchmark base scenario and must not be modified "
            "without operator review and a freeze-stability assessment."
        )

    def test_model_dump_json_sha256_matches_frozen_constant(self):
        """SHA256 of scenario.model_dump_json() must equal _V3_MODEL_SHA256.

        This is the value recorded as `base_scenario_sha256` in every
        benchmark trial record and manifest.  If this changes, the frozen
        benchmark result in benchmarks/results/run-20260826-110706-530179c2/
        can no longer be reproduced from the current file.
        """
        if not BASE_SCENARIO_PATH.exists():
            pytest.skip(f"Base scenario not found at {BASE_SCENARIO_PATH}")
        gen = ScenarioVariantGenerator(
            base_scenario_path=BASE_SCENARIO_PATH,
            capacity_ratios=(0.35,),
            anomaly_modes=(AnomalyMode.ORIGINAL,),
            deadline_scales=(1.0,),
        )
        actual = gen.base_sha256
        assert actual == _V3_MODEL_SHA256, (
            f"SHA256 of mission_data_v3 model_dump_json() changed!\n"
            f"  Expected (frozen benchmark value): {_V3_MODEL_SHA256}\n"
            f"  Actual:                            {actual}\n"
            "This hash is recorded as `base_scenario_sha256` in the frozen benchmark "
            "result (run-20260826-110706-530179c2).  A change means the frozen result "
            "can no longer be reproduced from the current file bytes."
        )

    def test_frozen_manifest_base_scenario_sha256_matches_model_hash(self):
        """The `base_scenario_sha256` in the frozen manifest must equal _V3_MODEL_SHA256.

        This cross-checks that the model-dump hash constant here is consistent with
        what was actually written to the frozen benchmark manifest at commit bce0c61.
        """
        import json as _json
        manifest_path = Path("benchmarks/results/run-20260826-110706-530179c2/manifest.json")
        if not manifest_path.exists():
            pytest.skip(f"Frozen manifest not found at {manifest_path}")
        manifest = _json.loads(manifest_path.read_bytes())
        stored = manifest.get("base_scenario_sha256", "")
        assert stored == _FROZEN_MANIFEST_BASE_SCENARIO_SHA256, (
            f"Frozen manifest base_scenario_sha256 does not match expected value!\n"
            f"  Expected: {_FROZEN_MANIFEST_BASE_SCENARIO_SHA256}\n"
            f"  Stored:   {stored}\n"
            "The frozen manifest must not be modified."
        )
        # Also confirm it is NOT equal to the file-byte hash (intentional by design)
        assert stored != _V3_FILE_SHA256, (
            "base_scenario_sha256 in manifest unexpectedly equals the raw-file-byte hash. "
            "These hashes measure different representations and should differ."
        )

    def test_two_sha256_values_differ_by_design(self):
        """The file-byte SHA256 and the model-dump SHA256 must not be equal.

        Equality would indicate a collision or a change in Pydantic serialisation
        behaviour that accidentally produced matching output.  Both values are
        individually pinned above; this test documents the expected inequality.
        """
        assert _V3_FILE_SHA256 != _V3_MODEL_SHA256, (
            "File-byte SHA256 and model_dump_json SHA256 are equal — unexpected. "
            "Check whether _sha256_scenario() in scenario_variants.py was changed "
            "to hash raw bytes instead of the serialised Pydantic object."
        )
