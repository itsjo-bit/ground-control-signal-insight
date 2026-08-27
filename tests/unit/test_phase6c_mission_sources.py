"""Phase 6C — Mission Source Provider Boundary Unit Tests.

Tests cover all 30 required cases from the Phase 6C specification.

 1.  Base provider cannot be instantiated directly.
 2.  Synthetic provider exposes stable provider_name.
 3.  Synthetic provider mode == synthetic_scenario.
 4.  historical_replay enum exists but no historical provider exists yet.
 5.  Valid synthetic scenario loads through provider.
 6.  Provider Scenario model_dump exactly equals direct ScenarioLoader output.
 7.  Scenario packet ordering is identical.
 8.  Scenario DataProduct ordering is identical.
 9.  Scenario remains simulated=True.
10.  Provenance manifest contains one SYNTHETIC source record.
11.  Source record is VALIDATED.
12.  Source SHA-256 equals independently calculated file SHA-256.
13.  Provenance ID is deterministic.
14.  Loading same source twice creates equivalent provenance manifests.
15.  Scenario top-level fields have bindings.
16.  link_inputs keys have field-level bindings.
17.  every MissionState field has one binding.
18.  every Packet field has one binding.
19.  every DataProduct field has one binding.
20.  every AnomalyEvent field has one binding.
21.  each exact field has only one binding.
22.  all bindings resolve to the synthetic provenance record.
23.  missing source file raises MissionSourceUnavailableError.
24.  invalid JSON / invalid Scenario raises MissionSourceValidationError.
25.  simulated=False source is rejected and surfaced as
     MissionSourceValidationError.
26.  source-change-during-load guard fails closed.
27.  no timestamps are generated implicitly.
28.  existing direct ScenarioLoader path remains unchanged.
29.  DataProduct schema remains unchanged.
30.  Scenario schema remains unchanged.

Plus one integration test against the real ASTERIA-7 canonical demo
scenario to prove compatibility.
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.app.mission_sources import (
    BaseMissionSourceProvider,
    MissionSourceBundle,
    MissionSourceMode,
    MissionSourceUnavailableError,
    MissionSourceValidationError,
    SyntheticScenarioProvider,
)
from backend.app.mission_sources.models import MissionSourceBundle, MissionSourceMode
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.data_product import DataProduct
from backend.app.models.mission_state import MissionState
from backend.app.models.packet import Packet
from backend.app.models.scenario import Scenario
from backend.app.simulation.scenario_loader import ScenarioLoader


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ASTERIA7_PATH = (
    Path(__file__).parent.parent.parent
    / "data"
    / "scenarios"
    / "asteria7_thermal_priority_contact_v1.json"
)

# ---------------------------------------------------------------------------
# Minimal fixture scenario JSON
# ---------------------------------------------------------------------------

_MINIMAL_SCENARIO: dict[str, Any] = {
    "scenario_id": "test-phase6c-minimal",
    "simulated": True,
    "distance_km": 42.0,
    "link_inputs": {
        "snr_db": 10.0,
        "rssi_dbm": -90.0,
        "nominal_data_rate_bps": 1000000,
        "latency_s": 0.5,
        "link_stability": 0.9,
        "remaining_window_s": 300,
    },
    "mission_state": {
        "mission_id": "GCSI-TEST-001",
        "mission_phase": "test_phase",
        "current_event": "Unit-test event",
        "event_time_remaining_s": 60.0,
        "comm_window_remaining_s": 300.0,
        "risk_score": 0.1,
        "risk_level": "LOW",
    },
    "packets": [
        {
            "packet_id": "PKT-001",
            "packet_type": "telemetry",
            "size_bits": 8192,
            "criticality": 0.5,
            "mission_relevance": 0.5,
            "deadline_s": 120.0,
            "retry_cost": 0.3,
            "delivery_requirement": "best-effort",
        },
        {
            "packet_id": "PKT-002",
            "packet_type": "science",
            "size_bits": 16384,
            "criticality": 0.8,
            "mission_relevance": 0.9,
            "deadline_s": 200.0,
            "retry_cost": 0.1,
            "delivery_requirement": "required",
        },
    ],
    "data_products": [
        {
            "product_id": "DP-001",
            "product_type": "telemetry",
            "description": "Test telemetry product",
            "subsystem": "power",
            "size_bits": 4096,
            "criticality": 0.4,
            "mission_relevance": 0.6,
            "scientific_value": 0.2,
            "deadline_s": 180.0,
            "age_s": 30.0,
            "anomaly_id": "ANOM-001",
            "experiment_id": None,
            "related_ids": [],
            "delivery_requirement": "best_effort",
            "retry_cost": 0.2,
        }
    ],
    "anomalies": [
        {
            "anomaly_id": "ANOM-001",
            "subsystem": "power",
            "severity": 0.5,
            "detected_at_s": 10.0,
            "description": "Test anomaly",
            "status": "active",
            "related_product_ids": ["DP-001"],
        }
    ],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_scenario_file(tmp_path: Path) -> Path:
    """Write the minimal synthetic scenario to a temp file and return its path."""
    p = tmp_path / "test_phase6c_minimal.json"
    p.write_text(json.dumps(_MINIMAL_SCENARIO), encoding="utf-8")
    return p


@pytest.fixture
def provider() -> SyntheticScenarioProvider:
    return SyntheticScenarioProvider()


@pytest.fixture
def bundle(provider: SyntheticScenarioProvider, minimal_scenario_file: Path) -> MissionSourceBundle:
    return provider.load(str(minimal_scenario_file))


# ---------------------------------------------------------------------------
# Test 1 — Base provider cannot be instantiated directly
# ---------------------------------------------------------------------------


def test_1_base_provider_cannot_be_instantiated_directly() -> None:
    """BaseMissionSourceProvider is abstract and must raise TypeError on direct
    instantiation."""
    with pytest.raises(TypeError):
        BaseMissionSourceProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Test 2 — Synthetic provider exposes stable provider_name
# ---------------------------------------------------------------------------


def test_2_synthetic_provider_stable_provider_name(provider: SyntheticScenarioProvider) -> None:
    name = provider.provider_name
    assert isinstance(name, str)
    assert len(name) > 0
    # Must be stable — calling again returns the same value
    assert provider.provider_name == name


# ---------------------------------------------------------------------------
# Test 3 — Synthetic provider mode == synthetic_scenario
# ---------------------------------------------------------------------------


def test_3_synthetic_provider_mode_is_synthetic_scenario(
    provider: SyntheticScenarioProvider,
) -> None:
    assert provider.source_mode == MissionSourceMode.SYNTHETIC_SCENARIO


# ---------------------------------------------------------------------------
# Test 4 — historical_replay enum exists but no historical provider exists
# ---------------------------------------------------------------------------


def test_4_historical_replay_enum_exists_no_provider() -> None:
    """MissionSourceMode.HISTORICAL_REPLAY must exist as a forward contract.
    No HistoricalReplayProvider class must exist in the package."""
    assert MissionSourceMode.HISTORICAL_REPLAY is not None
    assert MissionSourceMode.HISTORICAL_REPLAY.value == "historical_replay"

    # Confirm no HistoricalReplayProvider is importable from the package
    import backend.app.mission_sources as pkg

    assert not hasattr(pkg, "HistoricalReplayProvider"), (
        "HistoricalReplayProvider must NOT be implemented in Phase 6C"
    )


# ---------------------------------------------------------------------------
# Test 5 — Valid synthetic scenario loads through provider
# ---------------------------------------------------------------------------


def test_5_valid_scenario_loads_through_provider(bundle: MissionSourceBundle) -> None:
    assert isinstance(bundle, MissionSourceBundle)
    assert isinstance(bundle.scenario, Scenario)
    assert bundle.scenario.scenario_id == "test-phase6c-minimal"


# ---------------------------------------------------------------------------
# Test 6 — Provider Scenario model_dump exactly equals direct ScenarioLoader output
# ---------------------------------------------------------------------------


def test_6_provider_scenario_equals_direct_loader(
    minimal_scenario_file: Path,
    bundle: MissionSourceBundle,
) -> None:
    direct = ScenarioLoader.load(str(minimal_scenario_file))
    assert bundle.scenario.model_dump() == direct.model_dump()


# ---------------------------------------------------------------------------
# Test 7 — Scenario packet ordering is identical
# ---------------------------------------------------------------------------


def test_7_packet_ordering_preserved(
    minimal_scenario_file: Path,
    bundle: MissionSourceBundle,
) -> None:
    direct = ScenarioLoader.load(str(minimal_scenario_file))
    provider_ids = [p.packet_id for p in bundle.scenario.packets]
    direct_ids = [p.packet_id for p in direct.packets]
    assert provider_ids == direct_ids


# ---------------------------------------------------------------------------
# Test 8 — Scenario DataProduct ordering is identical
# ---------------------------------------------------------------------------


def test_8_data_product_ordering_preserved(
    minimal_scenario_file: Path,
    bundle: MissionSourceBundle,
) -> None:
    direct = ScenarioLoader.load(str(minimal_scenario_file))
    provider_ids = [dp.product_id for dp in bundle.scenario.data_products]
    direct_ids = [dp.product_id for dp in direct.data_products]
    assert provider_ids == direct_ids


# ---------------------------------------------------------------------------
# Test 9 — Scenario remains simulated=True
# ---------------------------------------------------------------------------


def test_9_scenario_simulated_is_true(bundle: MissionSourceBundle) -> None:
    assert bundle.scenario.simulated is True


# ---------------------------------------------------------------------------
# Test 10 — Provenance manifest contains exactly one SYNTHETIC source record
# ---------------------------------------------------------------------------


def test_10_manifest_has_one_synthetic_record(bundle: MissionSourceBundle) -> None:
    records = bundle.provenance.records
    assert len(records) == 1
    assert records[0].kind.value == "synthetic"


# ---------------------------------------------------------------------------
# Test 11 — Source record is VALIDATED
# ---------------------------------------------------------------------------


def test_11_source_record_is_validated(bundle: MissionSourceBundle) -> None:
    record = bundle.provenance.records[0]
    from backend.app.provenance.models import ProvenanceValidationStatus

    assert record.validation_status == ProvenanceValidationStatus.VALIDATED


# ---------------------------------------------------------------------------
# Test 12 — Source SHA-256 equals independently calculated file SHA-256
# ---------------------------------------------------------------------------


def test_12_source_sha256_matches_file(
    minimal_scenario_file: Path,
    bundle: MissionSourceBundle,
) -> None:
    expected = hashlib.sha256(minimal_scenario_file.read_bytes()).hexdigest()
    record = bundle.provenance.records[0]
    assert record.content_sha256 == expected


# ---------------------------------------------------------------------------
# Test 13 — Provenance ID is deterministic
# ---------------------------------------------------------------------------


def test_13_provenance_id_is_deterministic(
    minimal_scenario_file: Path,
    provider: SyntheticScenarioProvider,
) -> None:
    bundle_a = provider.load(str(minimal_scenario_file))
    bundle_b = provider.load(str(minimal_scenario_file))
    assert bundle_a.provenance.records[0].provenance_id == (
        bundle_b.provenance.records[0].provenance_id
    )


# ---------------------------------------------------------------------------
# Test 14 — Loading same source twice creates equivalent provenance manifests
# ---------------------------------------------------------------------------


def test_14_same_source_twice_equivalent_manifests(
    minimal_scenario_file: Path,
    provider: SyntheticScenarioProvider,
) -> None:
    bundle_a = provider.load(str(minimal_scenario_file))
    bundle_b = provider.load(str(minimal_scenario_file))

    records_a = bundle_a.provenance.records
    records_b = bundle_b.provenance.records

    assert len(records_a) == len(records_b)
    assert records_a[0].provenance_id == records_b[0].provenance_id
    assert records_a[0].content_sha256 == records_b[0].content_sha256

    # Same number of bindings
    assert len(bundle_a.provenance.bindings) == len(bundle_b.provenance.bindings)


# ---------------------------------------------------------------------------
# Test 15 — Scenario top-level fields have bindings
# ---------------------------------------------------------------------------


def test_15_scenario_top_level_fields_have_bindings(bundle: MissionSourceBundle) -> None:
    scenario_id = bundle.scenario.scenario_id
    bound_fields = {
        b.field_path
        for b in bundle.provenance.bindings
        if b.entity_type == "scenario" and b.entity_id == scenario_id
        and "." not in b.field_path
    }
    for field_name in Scenario.model_fields:
        assert field_name in bound_fields, (
            f"Scenario field '{field_name}' missing from provenance bindings"
        )


# ---------------------------------------------------------------------------
# Test 16 — link_inputs keys have field-level bindings
# ---------------------------------------------------------------------------


def test_16_link_inputs_keys_have_bindings(bundle: MissionSourceBundle) -> None:
    scenario_id = bundle.scenario.scenario_id
    bound_paths = {
        b.field_path
        for b in bundle.provenance.bindings
        if b.entity_type == "scenario" and b.entity_id == scenario_id
    }
    for key in bundle.scenario.link_inputs:
        expected_path = f"link_inputs.{key}"
        assert expected_path in bound_paths, (
            f"link_inputs key '{key}' missing from provenance bindings"
        )


# ---------------------------------------------------------------------------
# Test 17 — every MissionState field has one binding
# ---------------------------------------------------------------------------


def test_17_mission_state_fields_have_bindings(bundle: MissionSourceBundle) -> None:
    mission_id = bundle.scenario.mission_state.mission_id
    bound_fields = {
        b.field_path
        for b in bundle.provenance.bindings
        if b.entity_type == "mission_state" and b.entity_id == mission_id
    }
    for field_name in MissionState.model_fields:
        assert field_name in bound_fields, (
            f"MissionState field '{field_name}' missing from provenance bindings"
        )


# ---------------------------------------------------------------------------
# Test 18 — every Packet field has one binding
# ---------------------------------------------------------------------------


def test_18_packet_fields_have_bindings(bundle: MissionSourceBundle) -> None:
    for packet in bundle.scenario.packets:
        bound_fields = {
            b.field_path
            for b in bundle.provenance.bindings
            if b.entity_type == "packet" and b.entity_id == packet.packet_id
        }
        for field_name in Packet.model_fields:
            assert field_name in bound_fields, (
                f"Packet '{packet.packet_id}' field '{field_name}' "
                "missing from provenance bindings"
            )


# ---------------------------------------------------------------------------
# Test 19 — every DataProduct field has one binding
# ---------------------------------------------------------------------------


def test_19_data_product_fields_have_bindings(bundle: MissionSourceBundle) -> None:
    for product in bundle.scenario.data_products:
        bound_fields = {
            b.field_path
            for b in bundle.provenance.bindings
            if b.entity_type == "data_product" and b.entity_id == product.product_id
        }
        for field_name in DataProduct.model_fields:
            assert field_name in bound_fields, (
                f"DataProduct '{product.product_id}' field '{field_name}' "
                "missing from provenance bindings"
            )


# ---------------------------------------------------------------------------
# Test 20 — every AnomalyEvent field has one binding
# ---------------------------------------------------------------------------


def test_20_anomaly_fields_have_bindings(bundle: MissionSourceBundle) -> None:
    for anomaly in bundle.scenario.anomalies:
        bound_fields = {
            b.field_path
            for b in bundle.provenance.bindings
            if b.entity_type == "anomaly" and b.entity_id == anomaly.anomaly_id
        }
        for field_name in AnomalyEvent.model_fields:
            assert field_name in bound_fields, (
                f"AnomalyEvent '{anomaly.anomaly_id}' field '{field_name}' "
                "missing from provenance bindings"
            )


# ---------------------------------------------------------------------------
# Test 21 — each exact field has only one binding (no duplicates)
# ---------------------------------------------------------------------------


def test_21_no_duplicate_bindings(bundle: MissionSourceBundle) -> None:
    seen: set[tuple[str, str, str]] = set()
    for b in bundle.provenance.bindings:
        key = (b.entity_type, b.entity_id, b.field_path)
        assert key not in seen, (
            f"Duplicate binding for entity_type={b.entity_type!r}, "
            f"entity_id={b.entity_id!r}, field_path={b.field_path!r}"
        )
        seen.add(key)


# ---------------------------------------------------------------------------
# Test 22 — all bindings resolve to the synthetic provenance record
# ---------------------------------------------------------------------------


def test_22_all_bindings_resolve_to_synthetic_record(bundle: MissionSourceBundle) -> None:
    record_id = bundle.provenance.records[0].provenance_id
    for b in bundle.provenance.bindings:
        assert b.provenance_id == record_id, (
            f"Binding for ({b.entity_type!r}, {b.entity_id!r}, {b.field_path!r}) "
            f"has provenance_id={b.provenance_id!r}, expected {record_id!r}"
        )


# ---------------------------------------------------------------------------
# Test 23 — missing source file raises MissionSourceUnavailableError
# ---------------------------------------------------------------------------


def test_23_missing_source_raises_unavailable_error(tmp_path: Path) -> None:
    provider = SyntheticScenarioProvider()
    non_existent = str(tmp_path / "does_not_exist.json")
    with pytest.raises(MissionSourceUnavailableError):
        provider.load(non_existent)


# ---------------------------------------------------------------------------
# Test 24 — invalid JSON raises MissionSourceValidationError
# ---------------------------------------------------------------------------


def test_24_invalid_json_raises_validation_error(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{ this is not json }", encoding="utf-8")
    provider = SyntheticScenarioProvider()
    with pytest.raises(MissionSourceValidationError):
        provider.load(str(bad_file))


def test_24b_invalid_scenario_schema_raises_validation_error(tmp_path: Path) -> None:
    """Valid JSON but failing Pydantic validation raises MissionSourceValidationError."""
    bad_scenario = tmp_path / "bad_schema.json"
    bad_scenario.write_text(
        json.dumps({"scenario_id": "x", "simulated": True}),
        encoding="utf-8",
    )
    provider = SyntheticScenarioProvider()
    with pytest.raises(MissionSourceValidationError):
        provider.load(str(bad_scenario))


# ---------------------------------------------------------------------------
# Test 25 — simulated=False source is rejected as MissionSourceValidationError
# ---------------------------------------------------------------------------


def test_25_simulated_false_raises_validation_error(tmp_path: Path) -> None:
    non_simulated = dict(_MINIMAL_SCENARIO)
    non_simulated["simulated"] = False
    p = tmp_path / "non_simulated.json"
    p.write_text(json.dumps(non_simulated), encoding="utf-8")
    provider = SyntheticScenarioProvider()
    with pytest.raises(MissionSourceValidationError):
        provider.load(str(p))


# ---------------------------------------------------------------------------
# Test 26 — source-change-during-load guard fails closed
# ---------------------------------------------------------------------------


def test_26_source_change_during_load_fails_closed(
    minimal_scenario_file: Path,
) -> None:
    """Simulate the file changing between the first and second hash reads.

    We patch ``_sha256_file`` to return different values on successive calls
    so the race-condition guard is exercised deterministically without
    actually writing to the filesystem during ScenarioLoader.load().
    """
    provider = SyntheticScenarioProvider()

    original_sha256 = hashlib.sha256(minimal_scenario_file.read_bytes()).hexdigest()
    tampered_sha256 = "a" * 64  # a valid-format but different hash

    call_count = {"n": 0}

    def mock_sha256(path: Path) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return original_sha256  # first read: "before load"
        return tampered_sha256  # second read: "after load" — simulates change

    import backend.app.mission_sources.synthetic_provider as sp_module

    with patch.object(sp_module, "_sha256_file", side_effect=mock_sha256):
        with pytest.raises(MissionSourceValidationError, match="content changed"):
            provider.load(str(minimal_scenario_file))


# ---------------------------------------------------------------------------
# Test 27 — no timestamps are generated implicitly
# ---------------------------------------------------------------------------


def test_27_no_timestamps_in_provenance(bundle: MissionSourceBundle) -> None:
    record = bundle.provenance.records[0]
    assert record.observed_at is None, "observed_at must remain None (no datetime.now())"
    assert record.retrieved_at is None, "retrieved_at must remain None (no datetime.now())"
    assert record.normalized_at is None, "normalized_at must remain None (no datetime.now())"


# ---------------------------------------------------------------------------
# Test 28 — existing direct ScenarioLoader path remains unchanged
# ---------------------------------------------------------------------------


def test_28_direct_scenario_loader_path_unchanged(minimal_scenario_file: Path) -> None:
    """ScenarioLoader.load() must continue to work exactly as it did before
    Phase 6C — provider existence must not interfere."""
    scenario = ScenarioLoader.load(str(minimal_scenario_file))
    assert scenario.scenario_id == "test-phase6c-minimal"
    assert scenario.simulated is True


# ---------------------------------------------------------------------------
# Test 29 — DataProduct schema remains unchanged
# ---------------------------------------------------------------------------


def test_29_data_product_schema_unchanged() -> None:
    """DataProduct must still have all its expected fields and no extras."""
    expected_fields = {
        "product_id",
        "product_type",
        "description",
        "subsystem",
        "size_bits",
        "criticality",
        "mission_relevance",
        "scientific_value",
        "deadline_s",
        "age_s",
        "anomaly_id",
        "experiment_id",
        "related_ids",
        "delivery_requirement",
        "retry_cost",
    }
    assert set(DataProduct.model_fields.keys()) == expected_fields


# ---------------------------------------------------------------------------
# Test 30 — Scenario schema remains unchanged
# ---------------------------------------------------------------------------


def test_30_scenario_schema_unchanged() -> None:
    """Scenario must still have all its expected fields and no extras."""
    expected_fields = {
        "scenario_id",
        "simulated",
        "link_inputs",
        "mission_state",
        "packets",
        "data_products",
        "anomalies",
        "distance_km",
    }
    assert set(Scenario.model_fields.keys()) == expected_fields


# ---------------------------------------------------------------------------
# Phase 6C.1 — Error boundary / redaction tests
# ---------------------------------------------------------------------------


def test_6c1_invalid_json_no_path_in_error(tmp_path: Path) -> None:
    """Invalid JSON raises MissionSourceValidationError; raw source path must
    NOT appear in the public exception message (Issue 1)."""
    sentinel_path = tmp_path / "GCSI_SENTINEL_PATH_DO_NOT_LEAK.json"
    sentinel_path.write_text("{ not valid json }", encoding="utf-8")
    provider = SyntheticScenarioProvider()

    with pytest.raises(MissionSourceValidationError) as exc_info:
        provider.load(str(sentinel_path))

    public_msg = str(exc_info.value)
    assert "GCSI_SENTINEL_PATH_DO_NOT_LEAK" not in public_msg, (
        f"Raw source path leaked into public error message: {public_msg!r}"
    )


def test_6c1_schema_invalid_json_no_path_in_error(tmp_path: Path) -> None:
    """Valid JSON but invalid Scenario schema raises MissionSourceValidationError;
    raw source path must NOT appear in the public exception message (Issue 1)."""
    sentinel_path = tmp_path / "GCSI_SCHEMA_SENTINEL_PATH_DO_NOT_LEAK.json"
    sentinel_path.write_text(
        json.dumps({"scenario_id": "x", "simulated": True}),
        encoding="utf-8",
    )
    provider = SyntheticScenarioProvider()

    with pytest.raises(MissionSourceValidationError) as exc_info:
        provider.load(str(sentinel_path))

    public_msg = str(exc_info.value)
    assert "GCSI_SCHEMA_SENTINEL_PATH_DO_NOT_LEAK" not in public_msg, (
        f"Raw source path leaked into public error message: {public_msg!r}"
    )


def test_6c1_pydantic_input_value_not_in_error(tmp_path: Path) -> None:
    """Pydantic input_value sentinel strings from an invalid field must NOT
    be copied into the public MissionSourceValidationError message (Issue 1)."""
    sentinel = "GCSI_SECRET_SOURCE_CONTENT_DO_NOT_LEAK"
    bad_scenario = dict(_MINIMAL_SCENARIO)
    # Inject the sentinel as an invalid value for a numeric field so that
    # Pydantic's ValidationError will reference it in input_value context.
    bad_scenario["distance_km"] = sentinel  # type: ignore[assignment]

    p = tmp_path / "sentinel_scenario.json"
    p.write_text(json.dumps(bad_scenario), encoding="utf-8")
    provider = SyntheticScenarioProvider()

    with pytest.raises(MissionSourceValidationError) as exc_info:
        provider.load(str(p))

    public_msg = str(exc_info.value)
    assert sentinel not in public_msg, (
        f"Source-controlled sentinel content leaked into public error: {public_msg!r}"
    )


def test_6c1_simulated_false_no_path_in_error(tmp_path: Path) -> None:
    """simulated=False rejection must NOT expose the raw source path in the
    public exception message (Issue 1)."""
    sentinel_path = tmp_path / "GCSI_SIMULATED_FALSE_PATH_DO_NOT_LEAK.json"
    non_simulated = dict(_MINIMAL_SCENARIO)
    non_simulated["simulated"] = False
    sentinel_path.write_text(json.dumps(non_simulated), encoding="utf-8")
    provider = SyntheticScenarioProvider()

    with pytest.raises(MissionSourceValidationError) as exc_info:
        provider.load(str(sentinel_path))

    public_msg = str(exc_info.value)
    assert "GCSI_SIMULATED_FALSE_PATH_DO_NOT_LEAK" not in public_msg, (
        f"Raw source path leaked into public error message: {public_msg!r}"
    )


def test_6c1_permission_error_normalized_to_unavailable(
    minimal_scenario_file: Path,
) -> None:
    """ScenarioLoader.load() raising PermissionError must be normalized to
    MissionSourceUnavailableError (Issue 2)."""
    import backend.app.mission_sources.synthetic_provider as sp_module

    provider = SyntheticScenarioProvider()

    with patch.object(
        sp_module.ScenarioLoader,
        "load",
        side_effect=PermissionError("access denied"),
    ):
        with pytest.raises(MissionSourceUnavailableError):
            provider.load(str(minimal_scenario_file))


def test_6c1_generic_oserror_normalized_to_unavailable(
    minimal_scenario_file: Path,
) -> None:
    """ScenarioLoader.load() raising a generic OSError must be normalized to
    MissionSourceUnavailableError (Issue 2)."""
    import backend.app.mission_sources.synthetic_provider as sp_module

    provider = SyntheticScenarioProvider()

    with patch.object(
        sp_module.ScenarioLoader,
        "load",
        side_effect=OSError("filesystem error"),
    ):
        with pytest.raises(MissionSourceUnavailableError):
            provider.load(str(minimal_scenario_file))


def test_6c1_sanitized_errors_preserve_cause(tmp_path: Path) -> None:
    """The sanitized public MissionSourceValidationError must preserve the
    original exception as __cause__ for debugging (Issue 1 + 3)."""
    bad_file = tmp_path / "bad_cause.json"
    bad_file.write_text("{ not valid json }", encoding="utf-8")
    provider = SyntheticScenarioProvider()

    with pytest.raises(MissionSourceValidationError) as exc_info:
        provider.load(str(bad_file))

    assert exc_info.value.__cause__ is not None, (
        "Public MissionSourceValidationError must chain the original exception "
        "as __cause__ for diagnostic purposes."
    )


def test_6c1_missing_file_still_unavailable(tmp_path: Path) -> None:
    """Missing file behavior must still raise MissionSourceUnavailableError
    after Phase 6C.1 changes (regression guard, Issue 2)."""
    provider = SyntheticScenarioProvider()
    with pytest.raises(MissionSourceUnavailableError):
        provider.load(str(tmp_path / "nonexistent.json"))


def test_6c1_provenance_failure_sanitized(
    minimal_scenario_file: Path,
) -> None:
    """A failure inside _build_bundle must surface as a sanitized
    MissionSourceValidationError without copying arbitrary exception text
    into the public message (Issue 1 — provenance construction path)."""
    import backend.app.mission_sources.synthetic_provider as sp_module

    sentinel_message = "GCSI_INTERNAL_PROVENANCE_SENTINEL_DO_NOT_LEAK"
    provider = SyntheticScenarioProvider()

    with patch.object(
        sp_module,
        "_build_bindings",
        side_effect=RuntimeError(sentinel_message),
    ):
        with pytest.raises(MissionSourceValidationError) as exc_info:
            provider.load(str(minimal_scenario_file))

    public_msg = str(exc_info.value)
    assert sentinel_message not in public_msg, (
        f"Internal exception sentinel leaked into public error message: {public_msg!r}"
    )
    # __cause__ must preserve it for debugging
    assert exc_info.value.__cause__ is not None
    assert sentinel_message in str(exc_info.value.__cause__)


# ---------------------------------------------------------------------------
# ASTERIA-7 integration test — real canonical demo scenario
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _ASTERIA7_PATH.exists(),
    reason="ASTERIA-7 scenario file not found — skipping integration test",
)
class TestAsteria7Integration:
    """Integration tests against the real ASTERIA-7 canonical demo scenario."""

    @pytest.fixture(scope="class")
    @classmethod
    def asteria_bundle(cls) -> MissionSourceBundle:
        provider = SyntheticScenarioProvider()
        return provider.load(str(_ASTERIA7_PATH))

    def test_asteria7_loads_successfully(self, asteria_bundle: MissionSourceBundle) -> None:
        assert asteria_bundle.scenario.scenario_id == "asteria7_thermal_priority_contact_v1"

    def test_asteria7_equals_direct_loader(self, asteria_bundle: MissionSourceBundle) -> None:
        direct = ScenarioLoader.load(str(_ASTERIA7_PATH))
        assert asteria_bundle.scenario.model_dump() == direct.model_dump()

    def test_asteria7_simulated_true(self, asteria_bundle: MissionSourceBundle) -> None:
        assert asteria_bundle.scenario.simulated is True

    def test_asteria7_source_mode(self, asteria_bundle: MissionSourceBundle) -> None:
        assert asteria_bundle.source_mode == MissionSourceMode.SYNTHETIC_SCENARIO

    def test_asteria7_provider_name(self, asteria_bundle: MissionSourceBundle) -> None:
        assert asteria_bundle.provider_name == "GCSI-SyntheticScenarioProvider"

    def test_asteria7_one_synthetic_record(self, asteria_bundle: MissionSourceBundle) -> None:
        assert len(asteria_bundle.provenance.records) == 1
        assert asteria_bundle.provenance.records[0].kind.value == "synthetic"

    def test_asteria7_record_validated(self, asteria_bundle: MissionSourceBundle) -> None:
        from backend.app.provenance.models import ProvenanceValidationStatus

        assert (
            asteria_bundle.provenance.records[0].validation_status
            == ProvenanceValidationStatus.VALIDATED
        )

    def test_asteria7_sha256_matches_file(self, asteria_bundle: MissionSourceBundle) -> None:
        expected = hashlib.sha256(_ASTERIA7_PATH.read_bytes()).hexdigest()
        assert asteria_bundle.provenance.records[0].content_sha256 == expected

    def test_asteria7_scenario_top_level_fields_bound(
        self, asteria_bundle: MissionSourceBundle
    ) -> None:
        scenario_id = asteria_bundle.scenario.scenario_id
        bound = {
            b.field_path
            for b in asteria_bundle.provenance.bindings
            if b.entity_type == "scenario"
            and b.entity_id == scenario_id
            and "." not in b.field_path
        }
        for field_name in Scenario.model_fields:
            assert field_name in bound

    def test_asteria7_all_anomaly_fields_bound(
        self, asteria_bundle: MissionSourceBundle
    ) -> None:
        for anomaly in asteria_bundle.scenario.anomalies:
            bound = {
                b.field_path
                for b in asteria_bundle.provenance.bindings
                if b.entity_type == "anomaly" and b.entity_id == anomaly.anomaly_id
            }
            for field_name in AnomalyEvent.model_fields:
                assert field_name in bound

    def test_asteria7_no_duplicate_bindings(self, asteria_bundle: MissionSourceBundle) -> None:
        seen: set[tuple[str, str, str]] = set()
        for b in asteria_bundle.provenance.bindings:
            key = (b.entity_type, b.entity_id, b.field_path)
            assert key not in seen
            seen.add(key)

    def test_asteria7_no_timestamps(self, asteria_bundle: MissionSourceBundle) -> None:
        record = asteria_bundle.provenance.records[0]
        assert record.observed_at is None
        assert record.retrieved_at is None
        assert record.normalized_at is None

    def test_asteria7_provenance_id_deterministic(self) -> None:
        provider = SyntheticScenarioProvider()
        b1 = provider.load(str(_ASTERIA7_PATH))
        b2 = provider.load(str(_ASTERIA7_PATH))
        assert b1.provenance.records[0].provenance_id == b2.provenance.records[0].provenance_id

    def test_asteria7_distance_km_bound(self, asteria_bundle: MissionSourceBundle) -> None:
        scenario_id = asteria_bundle.scenario.scenario_id
        bound = {
            b.field_path
            for b in asteria_bundle.provenance.bindings
            if b.entity_type == "scenario" and b.entity_id == scenario_id
        }
        assert "distance_km" in bound

    def test_asteria7_link_inputs_keys_bound(self, asteria_bundle: MissionSourceBundle) -> None:
        scenario_id = asteria_bundle.scenario.scenario_id
        bound = {
            b.field_path
            for b in asteria_bundle.provenance.bindings
            if b.entity_type == "scenario" and b.entity_id == scenario_id
        }
        for key in asteria_bundle.scenario.link_inputs:
            assert f"link_inputs.{key}" in bound
