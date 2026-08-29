"""GCSI Phase 6F-B3 — Replay Descriptor V2 Tests.

Tests for HistoricalReplayV2Descriptor and load_v2_replay_descriptor():
- Committed descriptor loads and validates
- descriptor_id is deterministic SHA-256
- Schema / version validation
- Loader enforces repository confinement, bounded read, no symlink, no traversal
- Mutation of any semantic field causes descriptor_id rejection
- Descriptor binds to correct source bundle
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import tempfile

import pytest

from backend.app.mission_sources.v2_replay_descriptor import (
    DESCRIPTOR_V2_SCHEMA,
    DESCRIPTOR_V2_VERSION,
    HistoricalReplayV2Descriptor,
    compute_descriptor_id,
    compute_descriptor_id_from_dict,
    load_v2_replay_descriptor,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESCRIPTOR_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_descriptor.json"
_EXPECTED_DESCRIPTOR_ID = "8474ffc69dc63b42c711483968f279fd078eec41fbc1f2b3ad42422666bc1ada"
_EXPECTED_SOURCE_BUNDLE_ID = "950432d121a3aaa8340dcb24107bb42138fd6042f2ed2b254485f14c6a6e821a"

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def descriptor() -> HistoricalReplayV2Descriptor:
    return load_v2_replay_descriptor(_DESCRIPTOR_PATH)


# ---------------------------------------------------------------------------
# Section 14/15: Descriptor loads and validates
# ---------------------------------------------------------------------------


class TestDescriptorLoads:
    def test_descriptor_loads(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor is not None

    def test_schema_and_version(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.descriptor_schema == DESCRIPTOR_V2_SCHEMA
        assert descriptor.schema_version == DESCRIPTOR_V2_VERSION

    def test_descriptor_id_matches(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.descriptor_id == _EXPECTED_DESCRIPTOR_ID

    def test_replay_id(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.replay_id == "juno_pj62_large_replay_v2"

    def test_simulated_true(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.simulated is True

    def test_source_bundle_binding(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.source_bundle_id == _EXPECTED_SOURCE_BUNDLE_ID

    def test_decision_epoch(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert "2024-06-14" in descriptor.decision_epoch_utc
        assert "09:35:17" in descriptor.decision_epoch_utc

    def test_decision_epoch_policy(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.decision_epoch_policy == "END_OF_JIRAM_PJ62_DIAGNOSTIC_SESSION"

    def test_size_policy_id(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.size_policy_id == "PJ62_V2_ARCHIVE_SIZE_PROXY_V1"

    def test_product_policy_id(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        assert descriptor.product_policy_id == "PJ62_V2_PRODUCT_POLICY_V1"

    def test_link_inputs_values(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        li = descriptor.modeled_link_inputs
        assert li.snr_db == 3.0
        assert li.rssi_dbm == -95.0
        assert li.nominal_data_rate_bps == 100000.0
        assert li.latency_s == 1.5
        assert li.link_stability == 0.8
        assert li.remaining_window_s == 900.0

    def test_mission_state_values(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        ms = descriptor.modeled_mission_state
        assert ms.mission_id == "JUNO_PJ62_HISTORICAL_REPLAY_V2"
        assert ms.mission_phase == "science_downlink"
        assert ms.risk_score == 0.35
        assert ms.risk_level == "MEDIUM"
        assert ms.comm_window_remaining_s == 900.0

    def test_queue_membership_policy(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        qmp = descriptor.queue_membership_policy
        assert qmp.policy_id == "PJ62_V2_MODELED_QUEUE_MEMBERSHIP"
        assert qmp.source_mode == "MODELED"
        assert qmp.eligible_logical_count == 403

    def test_product_policy_entries(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        """All 9 semantic roles are present in the product policy."""
        roles = {e.semantic_role for e in descriptor.product_policy.entries}
        expected = {
            "instrument_diagnostic", "radiometry_science", "ultraviolet_observation",
            "visible_imaging", "magnetic_field", "plasma_particles",
            "energetic_particles", "radio_plasma_survey", "radio_plasma_burst",
        }
        assert roles == expected


# ---------------------------------------------------------------------------
# Deterministic descriptor_id
# ---------------------------------------------------------------------------


class TestDescriptorIdDeterminism:
    def test_descriptor_id_recomputes_correctly(
        self, descriptor: HistoricalReplayV2Descriptor
    ) -> None:
        """compute_descriptor_id produces the same value as stored."""
        recomputed = compute_descriptor_id(descriptor)
        assert recomputed == descriptor.descriptor_id

    def test_descriptor_id_from_dict(self) -> None:
        """compute_descriptor_id_from_dict matches stored id from raw JSON."""
        raw = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        computed = compute_descriptor_id_from_dict(raw)
        assert computed == _EXPECTED_DESCRIPTOR_ID

    def test_descriptor_id_is_sha256(self, descriptor: HistoricalReplayV2Descriptor) -> None:
        """descriptor_id is a 64-char hex string (SHA-256)."""
        assert len(descriptor.descriptor_id) == 64
        assert all(c in "0123456789abcdef" for c in descriptor.descriptor_id)


# ---------------------------------------------------------------------------
# Section 40: Descriptor mutation tests
# ---------------------------------------------------------------------------


class TestDescriptorMutationRejection:
    """Loader must reject stale descriptor_ids after any semantic mutation."""

    def _load_raw(self) -> dict:
        return json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))

    def _write_and_reload(self, data: dict) -> None:
        """Write mutated descriptor to temp file and attempt to load it."""
        allowed_dir = _REPO_ROOT / "data" / "replays"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8",
            dir=allowed_dir, delete=False,
        ) as f:
            json.dump(data, f, indent=2, sort_keys=True)
            temp_path = pathlib.Path(f.name)
        try:
            load_v2_replay_descriptor(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _mutate_and_expect_fail(self, mutation_fn) -> None:
        data = self._load_raw()
        mutation_fn(data)
        # Do NOT recompute descriptor_id — stale ID should be rejected
        with pytest.raises((MissionSourceValidationError, ValueError)):
            self._write_and_reload(data)

    def test_mutation_source_bundle_id_rejected(self) -> None:
        def mutate(d):
            d["source_bundle_id"] = "a" * 64
        self._mutate_and_expect_fail(mutate)

    def test_mutation_decision_epoch_rejected(self) -> None:
        def mutate(d):
            d["decision_epoch_utc"] = "2099-01-01T00:00:00+00:00"
        self._mutate_and_expect_fail(mutate)

    def test_mutation_size_policy_id_rejected(self) -> None:
        def mutate(d):
            d["size_policy_id"] = "MUTATED_POLICY"
        self._mutate_and_expect_fail(mutate)

    def test_mutation_product_policy_id_rejected(self) -> None:
        def mutate(d):
            d["product_policy_id"] = "MUTATED_PRODUCT_POLICY"
        self._mutate_and_expect_fail(mutate)

    def test_mutation_link_inputs_rejected(self) -> None:
        def mutate(d):
            d["modeled_link_inputs"]["latency_s"] = 9999.0
        self._mutate_and_expect_fail(mutate)

    def test_mutation_mission_state_risk_score_rejected(self) -> None:
        def mutate(d):
            d["modeled_mission_state"]["risk_score"] = 0.99
        self._mutate_and_expect_fail(mutate)

    def test_mutation_mission_state_mission_id_rejected(self) -> None:
        def mutate(d):
            d["modeled_mission_state"]["mission_id"] = "MUTATED_MISSION"
        self._mutate_and_expect_fail(mutate)

    def test_mutation_queue_policy_count_rejected(self) -> None:
        def mutate(d):
            d["queue_membership_policy"]["eligible_logical_count"] = 999
        self._mutate_and_expect_fail(mutate)


# ---------------------------------------------------------------------------
# Loader safety checks
# ---------------------------------------------------------------------------


class TestLoaderSafetyChecks:
    def test_missing_file_raises_unavailable(self) -> None:
        missing = _DESCRIPTOR_PATH.parent / "nonexistent_file_b3_test.json"
        with pytest.raises(MissionSourceUnavailableError):
            load_v2_replay_descriptor(missing)

    def test_traversal_path_rejected(self) -> None:
        traversal = _DESCRIPTOR_PATH.parent / ".." / "replays" / "juno_pj62_large_replay_v2_descriptor.json"
        with pytest.raises(MissionSourceValidationError, match="traversal"):
            load_v2_replay_descriptor(traversal)

    def test_wrong_schema_rejected(self) -> None:
        """File with wrong schema field is rejected before model parse."""
        data = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        data["schema"] = "gcsi.wrong_schema"
        allowed_dir = _REPO_ROOT / "data" / "replays"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8",
            dir=allowed_dir, delete=False,
        ) as f:
            json.dump(data, f)
            temp_path = pathlib.Path(f.name)
        try:
            with pytest.raises(MissionSourceValidationError, match="schema"):
                load_v2_replay_descriptor(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_wrong_version_rejected(self) -> None:
        data = json.loads(_DESCRIPTOR_PATH.read_text(encoding="utf-8"))
        data["schema_version"] = 999
        allowed_dir = _REPO_ROOT / "data" / "replays"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", encoding="utf-8",
            dir=allowed_dir, delete=False,
        ) as f:
            json.dump(data, f)
            temp_path = pathlib.Path(f.name)
        try:
            with pytest.raises(MissionSourceValidationError, match="schema_version"):
                load_v2_replay_descriptor(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
