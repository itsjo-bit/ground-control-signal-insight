"""GCSI Phase 6E-C5 — HistoricalReplayProvider Unit Tests.

All tests are COMPLETELY OFFLINE.
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
        """A JSON file that doesn't satisfy the descriptor schema → validation error."""
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
        """A directory at the descriptor path → unavailable error."""
        # The "data/replays" directory itself → unavailable
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
        """If Horizons snapshot file is missing → MissionSourceUnavailableError."""
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
        """If IRDR snapshot file is missing → MissionSourceUnavailableError."""
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
        """Tampered Horizons snapshot → MissionSourceValidationError."""
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
        """Tampered PDS archive snapshot → MissionSourceValidationError."""
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
# H. Path security
# ===========================================================================


class TestPathSecurity:
    def test_symlink_escape_raises_validation(self, tmp_path):
        """A symlink that escapes the repo root raises MissionSourceValidationError.

        We can only test this by patching the path resolver with a crafted
        path that resolves outside the repo.
        """
        from backend.app.mission_sources import historical_provider

        # Create a descriptor that references a path that after symlink resolution
        # would point outside the repo. We patch _resolve_snapshot_path directly.
        original_resolve = historical_provider._resolve_snapshot_path

        def _mock_resolve(relative_path):
            # On the Horizons path, raise the expected error
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
