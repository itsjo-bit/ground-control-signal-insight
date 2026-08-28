"""GCSI Phase 6E-C5 / C5.1 — HistoricalReplayProvider Unit Tests.

All tests are COMPLETELY OFFLINE.

C5.1 additions cover the descriptor source_ref trust-boundary hardening:
lexical rejection of unsafe paths, resolved containment of the descriptor
within data/replays/, and proof that the descriptor loader is never called
for rejected inputs.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path / sys.path setup
# ---------------------------------------------------------------------------

import sys

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from backend.app.mission_sources import (
    BaseMissionSourceProvider,
    HistoricalReplayProvider,
    MissionSourceMode,
)
from backend.app.mission_sources.errors import (
    MissionSourceUnavailableError,
    MissionSourceValidationError,
)

# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


def _no_network(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError(
        "HistoricalReplayProvider unit test: network access is forbidden."
    )


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# Committed descriptor path
# ---------------------------------------------------------------------------

_DESCRIPTOR_REF = "data/replays/juno_pj62_mwr_v1.json"

# ===========================================================================
# A. Class contract
# ===========================================================================


class TestClassContract:
    def test_is_base_provider_subclass(self):
        assert issubclass(HistoricalReplayProvider, BaseMissionSourceProvider)

    def test_provider_name(self):
        assert HistoricalReplayProvider().provider_name == "GCSI-HistoricalReplayProvider"

    def test_source_mode(self):
        assert HistoricalReplayProvider().source_mode == MissionSourceMode.HISTORICAL_REPLAY


# ===========================================================================
# B. Committed descriptor loads successfully
# ===========================================================================


class TestCommittedDescriptor:
    def test_load_returns_bundle(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        from backend.app.mission_sources.models import MissionSourceBundle
        assert isinstance(bundle, MissionSourceBundle)

    def test_source_ref_preserved_exactly(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.source_ref == _DESCRIPTOR_REF

    def test_provider_name_in_bundle(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.provider_name == "GCSI-HistoricalReplayProvider"

    def test_source_mode_in_bundle(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_scenario_id(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.scenario.scenario_id == "juno_pj62_mwr_2024166030000_v04_replay_v1"

    def test_simulated_true(self):
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.scenario.simulated is True


# ===========================================================================
# C. CWD independence
# ===========================================================================


class TestCWDIndependence:
    def test_relative_source_ref_works_from_changed_cwd(self, tmp_path):
        """Relative source_ref must work even when CWD is changed."""
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert bundle.scenario.scenario_id == "juno_pj62_mwr_2024166030000_v04_replay_v1"
        finally:
            os.chdir(original_cwd)


# ===========================================================================
# D. Error boundary — descriptor
# ===========================================================================


class TestDescriptorErrors:
    def test_missing_descriptor_raises_unavailable(self, tmp_path):
        nonexistent = "data/replays/nonexistent_descriptor_xyz.json"
        with pytest.raises(MissionSourceUnavailableError):
            HistoricalReplayProvider().load(nonexistent)

    def test_malformed_descriptor_raises_validation(self, tmp_path):
        """A JSON file that doesn't satisfy the descriptor schema -> validation error."""
        # Write a valid JSON but wrong schema
        bad_path = _ROOT / "data" / "replays" / "_test_bad_descriptor_tmp.json"
        try:
            bad_path.write_text('{"descriptor_schema": "wrong", "descriptor_version": 1}')
            with pytest.raises(MissionSourceValidationError):
                HistoricalReplayProvider().load(
                    "data/replays/_test_bad_descriptor_tmp.json"
                )
        finally:
            if bad_path.exists():
                bad_path.unlink()

    def test_directory_as_descriptor_raises_unavailable(self, tmp_path):
        """A directory at the descriptor path -> unavailable error."""
        # The "data/replays" directory itself -> unavailable
        dir_ref = "data/replays"
        with pytest.raises((MissionSourceUnavailableError, MissionSourceValidationError)):
            HistoricalReplayProvider().load(dir_ref)


# ===========================================================================
# E. Store call counts — verify exact once each
# ===========================================================================


class TestStoreCallCounts:
    def test_descriptor_loader_called_exactly_once(self):
        """load_historical_replay_descriptor must be called exactly once."""
        from backend.app.mission_sources import historical_provider as hp_module
        original = hp_module.load_historical_replay_descriptor
        with patch.object(hp_module, "load_historical_replay_descriptor", wraps=original) as mock_load:
            HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert mock_load.call_count == 1

    def test_horizons_store_called_exactly_once(self):
        """HorizonsSnapshotStore.load must be called exactly once."""
        from backend.app.mission_sources import historical_provider as hp_module
        original_load = hp_module.HorizonsSnapshotStore.load
        with patch.object(hp_module.HorizonsSnapshotStore, "load", wraps=original_load) as mock_load:
            HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert mock_load.call_count == 1

    def test_pds_store_called_for_irdr_and_grdr(self):
        """PdsArchiveSnapshotStore.load must be called exactly twice (IRDR + GRDR)."""
        from backend.app.mission_sources import historical_provider as hp_module
        original_load = hp_module.PdsArchiveSnapshotStore.load
        with patch.object(hp_module.PdsArchiveSnapshotStore, "load", wraps=original_load) as mock_load:
            HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert mock_load.call_count == 2


# ===========================================================================
# F. Snapshot unavailability
# ===========================================================================


class TestSnapshotUnavailability:
    def test_missing_horizons_snapshot_raises_unavailable(self):
        """If Horizons snapshot file is missing -> MissionSourceUnavailableError."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.horizons_snapshot import (
            HorizonsSnapshotUnavailableError,
        )
        with patch.object(
            hp_module.HorizonsSnapshotStore,
            "load",
            side_effect=HorizonsSnapshotUnavailableError("not available"),
        ):
            with pytest.raises(MissionSourceUnavailableError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)

    def test_missing_irdr_snapshot_raises_unavailable(self):
        """If IRDR snapshot file is missing -> MissionSourceUnavailableError."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.pds_archive_snapshot import (
            PdsArchiveSnapshotUnavailableError,
        )
        with patch.object(
            hp_module.PdsArchiveSnapshotStore,
            "load",
            side_effect=PdsArchiveSnapshotUnavailableError("not available"),
        ):
            with pytest.raises(MissionSourceUnavailableError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)

    def test_invalid_horizons_snapshot_raises_validation(self):
        """Tampered Horizons snapshot -> MissionSourceValidationError."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.horizons_snapshot import (
            HorizonsSnapshotValidationError,
        )
        with patch.object(
            hp_module.HorizonsSnapshotStore,
            "load",
            side_effect=HorizonsSnapshotValidationError("integrity failed"),
        ):
            with pytest.raises(MissionSourceValidationError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)

    def test_invalid_pds_snapshot_raises_validation(self):
        """Tampered PDS archive snapshot -> MissionSourceValidationError."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.pds_archive_snapshot import (
            PdsArchiveSnapshotValidationError,
        )
        with patch.object(
            hp_module.PdsArchiveSnapshotStore,
            "load",
            side_effect=PdsArchiveSnapshotValidationError("hash mismatch"),
        ):
            with pytest.raises(MissionSourceValidationError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)


# ===========================================================================
# G. No retry, no fallback, no network
# ===========================================================================


class TestNoRetryNoFallback:
    def test_no_retry_on_store_failure(self):
        """If a store fails, the provider does not retry."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.horizons_snapshot import (
            HorizonsSnapshotUnavailableError,
        )
        call_count = 0

        def _fail_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise HorizonsSnapshotUnavailableError("unavailable")

        with patch.object(hp_module.HorizonsSnapshotStore, "load", side_effect=_fail_once):
            with pytest.raises(MissionSourceUnavailableError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)

        assert call_count == 1  # exactly once, no retry

    def test_no_online_fallback(self):
        """If all snapshots are missing, no network call is made."""
        from backend.app.mission_sources import historical_provider as hp_module
        from backend.app.mission_sources.snapshots.horizons_snapshot import (
            HorizonsSnapshotUnavailableError,
        )
        with patch.object(
            hp_module.HorizonsSnapshotStore, "load",
            side_effect=HorizonsSnapshotUnavailableError("gone"),
        ):
            with pytest.raises(MissionSourceUnavailableError):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        # If we got here without a network guard firing, network was not touched


# ===========================================================================
# H. Snapshot path security (existing coverage from C5)
# ===========================================================================


class TestPathSecurity:
    def test_symlink_escape_raises_validation(self, tmp_path):
        """A snapshot symlink that escapes the repo root raises MissionSourceValidationError.

        We test this by patching _resolve_snapshot_path directly.
        """
        from backend.app.mission_sources import historical_provider

        original_resolve = historical_provider._resolve_snapshot_path

        def _mock_resolve(relative_path):
            if "horizons" in relative_path:
                raise MissionSourceValidationError(
                    "Snapshot path resolves outside the repository root."
                )
            return original_resolve(relative_path)

        with patch.object(historical_provider, "_resolve_snapshot_path", side_effect=_mock_resolve):
            with pytest.raises(MissionSourceValidationError, match="outside the repository root"):
                HistoricalReplayProvider().load(_DESCRIPTOR_REF)

    def test_source_ref_not_replaced_with_absolute_path(self):
        """source_ref in bundle must be the caller-provided string, not an absolute path."""
        ref = _DESCRIPTOR_REF
        bundle = HistoricalReplayProvider().load(ref)
        assert bundle.source_ref == ref
        assert not bundle.source_ref.startswith("/")
        assert ":\\" not in bundle.source_ref  # not a Windows absolute path


# ===========================================================================
# C5.1 — Descriptor source_ref trust-boundary hardening
# ===========================================================================

# ---------------------------------------------------------------------------
# Helper: assert a source_ref is rejected before any descriptor IO
# ---------------------------------------------------------------------------


def _assert_rejected_before_io(
    source_ref: str,
    expected_exc: type = MissionSourceValidationError,
) -> None:
    """Assert that source_ref raises the expected exception type AND that
    load_historical_replay_descriptor is NEVER called.

    Proves the validation fires before any descriptor file IO.
    """
    from backend.app.mission_sources import historical_provider as hp_module

    with patch.object(
        hp_module,
        "load_historical_replay_descriptor",
        side_effect=AssertionError(
            "descriptor loader must NOT be called for rejected path"
        ),
    ) as mock_loader:
        with pytest.raises(expected_exc):
            HistoricalReplayProvider().load(source_ref)
        assert mock_loader.call_count == 0, (
            f"Descriptor loader must not be called for {source_ref!r}"
        )


# ===========================================================================
# I. Lexical rejection — unsafe source_refs (C5.1)
# ===========================================================================


class TestLexicalRejection:
    """Every unsafe source_ref must be rejected before any descriptor IO."""

    def test_absolute_posix_path(self):
        _assert_rejected_before_io("/etc/passwd")

    def test_absolute_posix_path_with_data_suffix(self):
        _assert_rejected_before_io("/data/replays/test.json")

    def test_simple_traversal(self):
        _assert_rejected_before_io("../outside.json")

    def test_nested_traversal_escaping_replays(self):
        """data/replays/../../outside.json must be rejected before descriptor IO."""
        _assert_rejected_before_io("data/replays/../../outside.json")

    def test_single_dot_component(self):
        _assert_rejected_before_io("data/replays/../replays/test.json")

    def test_windows_backslash_path(self):
        _assert_rejected_before_io("data\\replays\\test.json")

    def test_windows_drive_path_backslash(self):
        _assert_rejected_before_io("C:\\outside\\replay.json")

    def test_windows_drive_path_forward_slash(self):
        _assert_rejected_before_io("C:/outside/replay.json")

    def test_windows_drive_path_in_replays(self):
        """Even if it looks like data/replays, a drive letter is rejected."""
        _assert_rejected_before_io("C:/data/replays/test.json")

    def test_unc_backslash(self):
        _assert_rejected_before_io("\\\\server\\share\\replay.json")

    def test_scheme_relative(self):
        _assert_rejected_before_io("//server/share/replay.json")

    def test_http_url(self):
        _assert_rejected_before_io("http://example.com/replay.json")

    def test_https_url(self):
        _assert_rejected_before_io("https://example.com/replay.json")

    def test_query_string(self):
        _assert_rejected_before_io("data/replays/test.json?x=1")

    def test_fragment(self):
        _assert_rejected_before_io("data/replays/test.json#section")

    def test_percent_encoding(self):
        _assert_rejected_before_io("data/replays/%2e%2e/outside.json")

    def test_percent_in_filename(self):
        _assert_rejected_before_io("data/replays/test%20file.json")

    def test_nul_byte(self):
        _assert_rejected_before_io("data/replays/test\x00.json")

    def test_wrong_prefix_scenarios(self):
        _assert_rejected_before_io("data/scenarios/scenario.json")

    def test_wrong_prefix_root_relative(self):
        _assert_rejected_before_io("backend/app/test.json")

    def test_wrong_extension_txt(self):
        _assert_rejected_before_io("data/replays/test.txt")

    def test_wrong_extension_py(self):
        _assert_rejected_before_io("data/replays/test.py")

    def test_wrong_extension_json_exe(self):
        _assert_rejected_before_io("data/replays/test.json.exe")

    def test_empty_string(self):
        _assert_rejected_before_io("", expected_exc=MissionSourceValidationError)

    def test_json_extension_case_insensitive_upper_passes_lexical(self):
        """Upper-case .JSON extension should pass lexical validation.

        The file doesn't exist so we expect MissionSourceUnavailableError,
        NOT MissionSourceValidationError (which would mean lexical rejection).
        """
        with pytest.raises(MissionSourceUnavailableError):
            HistoricalReplayProvider().load("data/replays/NONEXISTENT.JSON")


# ===========================================================================
# J. Resolved containment — descriptor symlink escape (C5.1)
# ===========================================================================


class TestDescriptorSymlinkSecurity:
    """Symlink-resolved containment tests for the descriptor path."""

    def test_symlink_escaping_replays_root_raises_validation(self, tmp_path):
        """A symlink inside data/replays/ that resolves outside data/replays/
        must raise MissionSourceValidationError.

        If platform permissions prevent symlink creation, the test is skipped.
        """
        # Create a real JSON file outside the replays directory
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "evil_descriptor.json"
        outside_file.write_text('{"descriptor_schema":"gcsi.historical_replay_descriptor"}')

        # Place the symlink inside the actual data/replays/
        replays_dir = _ROOT / "data" / "replays"
        symlink_path = replays_dir / "_c51_test_symlink.json"

        try:
            if symlink_path.exists() or symlink_path.is_symlink():
                symlink_path.unlink()
            os.symlink(str(outside_file), str(symlink_path))
        except (OSError, NotImplementedError):
            pytest.skip(
                "Platform does not support symlink creation; skipping symlink test."
            )

        try:
            # Lexically this path looks fine (data/replays/*.json) but
            # resolves to a file outside data/replays/
            with pytest.raises(
                MissionSourceValidationError,
                match="outside the trusted replay directory",
            ):
                HistoricalReplayProvider().load("data/replays/_c51_test_symlink.json")
        finally:
            try:
                symlink_path.unlink()
            except OSError:
                pass

    def test_descriptor_resolver_called_once_on_happy_path(self):
        """_resolve_descriptor_path must be called exactly once on a valid path."""
        from backend.app.mission_sources import historical_provider as hp_module

        with patch.object(
            hp_module,
            "_resolve_descriptor_path",
            wraps=hp_module._resolve_descriptor_path,
        ) as mock_resolve:
            HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert mock_resolve.call_count == 1

    def test_descriptor_loader_called_once_on_happy_path(self):
        """load_historical_replay_descriptor must be called exactly once."""
        from backend.app.mission_sources import historical_provider as hp_module

        with patch.object(
            hp_module,
            "load_historical_replay_descriptor",
            wraps=hp_module.load_historical_replay_descriptor,
        ) as mock_load:
            HistoricalReplayProvider().load(_DESCRIPTOR_REF)
            assert mock_load.call_count == 1

    def test_source_ref_preserved_after_resolution(self):
        """bundle.source_ref must still equal the caller-provided string."""
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert bundle.source_ref == _DESCRIPTOR_REF

    def test_resolved_path_not_exposed_in_source_ref(self):
        """source_ref must not contain an absolute resolved filesystem path."""
        bundle = HistoricalReplayProvider().load(_DESCRIPTOR_REF)
        assert not Path(bundle.source_ref).is_absolute()


# ===========================================================================
# K. Missing-but-valid-path descriptor (C5.1)
# ===========================================================================


class TestMissingValidPathDescriptor:
    """A source_ref that passes lexical validation but the file does not exist
    must raise MissionSourceUnavailableError."""

    def test_nonexistent_valid_path_raises_unavailable(self):
        with pytest.raises(MissionSourceUnavailableError):
            HistoricalReplayProvider().load(
                "data/replays/nonexistent_descriptor_xyz_c51.json"
            )

    def test_directory_as_descriptor_raises_lexically(self):
        """'data/replays' has no .json extension -> lexical rejection (ValidationError)."""
        with pytest.raises(MissionSourceValidationError):
            HistoricalReplayProvider().load("data/replays")
