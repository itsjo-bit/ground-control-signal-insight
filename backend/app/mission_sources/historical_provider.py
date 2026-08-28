"""GCSI Phase 6E-C5 — Historical Replay Provider.

Implements :class:`BaseMissionSourceProvider` for the ``historical_replay``
source mode.

Provider responsibilities
-------------------------
- Descriptor IO (via load_historical_replay_descriptor)
- Snapshot-path resolution (repository-relative, symlink-safe)
- Verified snapshot loading (via HorizonsSnapshotStore / PdsArchiveSnapshotStore)
- Error normalization (MissionSourceUnavailableError / MissionSourceValidationError)
- MissionSourceBundle construction

Architecture separation
-----------------------
This provider performs local file IO ONLY through:
    - load_historical_replay_descriptor
    - HorizonsSnapshotStore.load
    - PdsArchiveSnapshotStore.load

ReplayAssembler is pure and performs zero IO.

Repository root
---------------
All snapshot paths in the descriptor are repository-relative.
The stable repository root is determined from the module's own location::

    _REPO_ROOT = Path(__file__).resolve().parents[3]

Verification: historical_provider.py lives at:
    <repo>/backend/app/mission_sources/historical_provider.py
    parents[0] = mission_sources/
    parents[1] = app/
    parents[2] = backend/
    parents[3] = <repo root>

Security invariants
-------------------
- Symlink resolution is checked: the resolved target must remain within
  the repository root.  If it escapes: MissionSourceValidationError.
- Missing or directory path: MissionSourceUnavailableError.
- Absolute filesystem paths are never exposed in public exception messages.
- source_ref is stored as-is (opaque caller-provided string).

Dormancy
--------
This provider is callable directly in tests but NOT wired into state.py,
API routes, or application startup.  See Phase 6E-C6 for activation.
"""

from __future__ import annotations

from pathlib import Path

from .base import BaseMissionSourceProvider
from .errors import MissionSourceUnavailableError, MissionSourceValidationError
from .models import MissionSourceBundle, MissionSourceMode
from .replay_assembler import ReplayAssembler
from .replay_descriptor import load_historical_replay_descriptor
from .snapshots.horizons_snapshot import (
    HorizonsSnapshotStore,
    HorizonsSnapshotUnavailableError,
    HorizonsSnapshotValidationError,
)
from .snapshots.pds_archive_snapshot import (
    PdsArchiveSnapshotStore,
    PdsArchiveSnapshotUnavailableError,
    PdsArchiveSnapshotValidationError,
)

# ---------------------------------------------------------------------------
# Repository root — stable anchor for all snapshot path resolution.
# ---------------------------------------------------------------------------

# historical_provider.py lives at: <repo>/backend/app/mission_sources/
#   parents[0] = mission_sources/
#   parents[1] = app/
#   parents[2] = backend/
#   parents[3] = <repo root>
_REPO_ROOT: Path = Path(__file__).resolve().parents[3]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROVIDER_NAME: str = "GCSI-HistoricalReplayProvider"


# ---------------------------------------------------------------------------
# Path resolution helper
# ---------------------------------------------------------------------------


def _resolve_snapshot_path(relative_path: str) -> Path:
    """Resolve a repository-relative snapshot path safely.

    1. Joins the repo root with the validated relative path from the descriptor.
    2. Resolves symlinks (via Path.resolve()).
    3. Requires the resolved target to remain within the repository root.
    4. Requires the resolved target to exist and be a file (not a directory).

    Args:
        relative_path: A descriptor snapshot path that has already passed
            lexical validation by load_historical_replay_descriptor.

    Returns:
        The resolved Path.

    Raises:
        MissionSourceValidationError: if the resolved path escapes the repo root.
        MissionSourceUnavailableError: if the file is missing or is a directory.
    """
    candidate = _REPO_ROOT / relative_path
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise MissionSourceUnavailableError(
            "Snapshot path could not be resolved."
        ) from exc

    # Symlink-escape guard: resolved path must stay within repo root.
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise MissionSourceValidationError(
            "Snapshot path resolves outside the repository root."
        ) from exc

    # Existence and type checks — no path exposure in messages.
    if not resolved.exists():
        raise MissionSourceUnavailableError(
            "Snapshot file is not available."
        )
    if resolved.is_dir():
        raise MissionSourceUnavailableError(
            "Snapshot path points to a directory, not a file."
        )

    return resolved


# ---------------------------------------------------------------------------
# HistoricalReplayProvider
# ---------------------------------------------------------------------------


class HistoricalReplayProvider(BaseMissionSourceProvider):
    """Mission-source provider for GCSI historical replay bundles.

    Assembles a canonical Scenario + ProvenanceManifest entirely offline from:
    - A committed replay descriptor (``data/replays/*.json``)
    - Three verified snapshot files (Horizons + IRDR + GRDR)

    The source_ref is a repository-relative path to the descriptor, e.g.::

        "data/replays/juno_pj62_mwr_v1.json"

    Relative paths work regardless of the process CWD because snapshot
    resolution is anchored to the repository root derived from this module's
    own ``__file__``.

    The provider is dormant: it is not wired into ``state.py``, any API
    route, or application startup.  It must be constructed and called
    directly for testing.

    Usage::

        bundle = HistoricalReplayProvider().load(
            "data/replays/juno_pj62_mwr_v1.json"
        )

    Raises
    ------
    MissionSourceUnavailableError
        Descriptor or snapshot file is missing or unreadable.

    MissionSourceValidationError
        Descriptor is malformed, snapshot integrity fails, cross-source
        validation fails, or assembly fails.
    """

    # ----------------------------------------------------------------
    # BaseMissionSourceProvider abstract properties
    # ----------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    @property
    def source_mode(self) -> MissionSourceMode:
        return MissionSourceMode.HISTORICAL_REPLAY

    # ----------------------------------------------------------------
    # load
    # ----------------------------------------------------------------

    def load(self, source_ref: str) -> MissionSourceBundle:
        """Load a historical replay bundle from *source_ref*.

        Parameters
        ----------
        source_ref:
            Repository-relative path to the descriptor JSON file.
            Treated as an untrusted local path — never executed or
            shell-expanded.  Stored as-is in the returned bundle.

        Returns
        -------
        MissionSourceBundle

        Raises
        ------
        MissionSourceUnavailableError
            If the descriptor or any snapshot file cannot be found or read.

        MissionSourceValidationError
            If the descriptor fails validation, any snapshot fails integrity
            re-validation, cross-source semantic checks fail, or assembly fails.
        """
        # ----------------------------------------------------------------
        # Step 1: Load and validate the descriptor
        # ----------------------------------------------------------------
        descriptor_path = _REPO_ROOT / source_ref
        try:
            descriptor = load_historical_replay_descriptor(descriptor_path)
        except MissionSourceUnavailableError:
            raise
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "Replay descriptor could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 2: Resolve snapshot paths (repository-relative, symlink-safe)
        # ----------------------------------------------------------------
        try:
            horizons_path = _resolve_snapshot_path(descriptor.horizons_snapshot_path)
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise

        try:
            irdr_path = _resolve_snapshot_path(descriptor.irdr_snapshot_path)
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise

        try:
            grdr_path = _resolve_snapshot_path(descriptor.grdr_snapshot_path)
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise

        # ----------------------------------------------------------------
        # Step 3: Load Horizons snapshot
        # ----------------------------------------------------------------
        try:
            horizons_result = HorizonsSnapshotStore.load(horizons_path)
        except HorizonsSnapshotUnavailableError:
            raise
        except HorizonsSnapshotValidationError:
            raise
        except MissionSourceUnavailableError:
            raise
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "Horizons snapshot could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 4: Load IRDR snapshot
        # ----------------------------------------------------------------
        try:
            irdr_product, irdr_provenance = PdsArchiveSnapshotStore.load(irdr_path)
        except PdsArchiveSnapshotUnavailableError:
            raise
        except PdsArchiveSnapshotValidationError:
            raise
        except MissionSourceUnavailableError:
            raise
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "IRDR PDS archive snapshot could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 5: Load GRDR snapshot
        # ----------------------------------------------------------------
        try:
            grdr_product, grdr_provenance = PdsArchiveSnapshotStore.load(grdr_path)
        except PdsArchiveSnapshotUnavailableError:
            raise
        except PdsArchiveSnapshotValidationError:
            raise
        except MissionSourceUnavailableError:
            raise
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "GRDR PDS archive snapshot could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 6: Assemble Scenario + ProvenanceManifest (pure, no IO)
        # ----------------------------------------------------------------
        try:
            scenario, manifest = ReplayAssembler.assemble(
                descriptor=descriptor,
                horizons_result=horizons_result,
                irdr_product=irdr_product,
                irdr_provenance=irdr_provenance,
                grdr_product=grdr_product,
                grdr_provenance=grdr_provenance,
            )
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "Historical replay assembly failed."
            ) from exc

        # ----------------------------------------------------------------
        # Step 7: Assemble MissionSourceBundle — source_ref preserved as-is
        # ----------------------------------------------------------------
        return MissionSourceBundle(
            scenario=scenario,
            provenance=manifest,
            provider_name=self.provider_name,
            source_mode=self.source_mode,
            source_ref=source_ref,  # opaque caller-provided string, unchanged
        )
