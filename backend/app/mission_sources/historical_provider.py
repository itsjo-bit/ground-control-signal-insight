"""GCSI Phase 6E-C5 / C5.1 / B4 — Historical Replay Provider.

Implements :class:`BaseMissionSourceProvider` for the ``historical_replay``
source mode.

Provider responsibilities
-------------------------
- Descriptor IO (via load_historical_replay_descriptor / load_v2_replay_descriptor)
- Descriptor source_ref trust boundary (lexical + symlink-resolved containment)
- Safe schema discrimination (V1 vs V2 descriptor generation)
- Snapshot-path resolution (repository-relative, symlink-safe)
- Verified snapshot loading (via HorizonsSnapshotStore / PdsArchiveSnapshotStore)
- V2 source graph loading and assembly via ReplayAssemblerV2
- Error normalization (MissionSourceUnavailableError / MissionSourceValidationError)
- MissionSourceBundle construction with caller-provided source_ref

Architecture separation
-----------------------
This provider performs local file IO ONLY through:
    - load_historical_replay_descriptor (V1)
    - load_v2_replay_descriptor (V2 descriptor)
    - load_verified_v2_source_graph (V2 source graph + 535 snapshots)
    - HorizonsSnapshotStore.load (V1 only)
    - PdsArchiveSnapshotStore.load (V1 only)

ReplayAssembler (V1) and ReplayAssemblerV2 are pure and perform zero IO.

Repository root
---------------
All descriptor and snapshot paths are repository-relative.
The stable repository root is determined from the module's own location::

    _REPO_ROOT = Path(__file__).resolve().parents[3]

Verification: historical_provider.py lives at:
    <repo>/backend/app/mission_sources/historical_provider.py
    parents[0] = mission_sources/
    parents[1] = app/
    parents[2] = backend/
    parents[3] = <repo root>

Descriptor source_ref trust boundary (Phase 6E-C5.1)
-----------------------------------------------------
source_ref undergoes two layers of validation in _resolve_descriptor_path():

1. Lexical validation — performed before any filesystem access:
   - Must be a non-empty str
   - No NUL byte, backslash, colon, percent-encoding, query (?),
     fragment (#), absolute POSIX prefix (/), scheme-relative prefix (//),
     or ".." traversal components
   - Must start with the trusted prefix: ``data/replays/``
   - Basename must end with ``.json`` (case-insensitive)

2. Symlink-resolved containment — after resolving the candidate path:
   - Resolved absolute path must remain inside ``<repo>/data/replays/``
   - This catches symlink escapes even when the lexical path looks safe

Schema discrimination (Phase B4)
---------------------------------
After _resolve_descriptor_path() completes, a bounded JSON header read
performs safe schema discrimination:

    - Bounded read of already-validated resolved path
    - Check "schema" (V2) or "descriptor_schema" (V1)
    - V1 → existing V1 loader path unchanged
    - V2 → V2 loader path
    - Unknown schema → MissionSourceValidationError
    - Malformed JSON → MissionSourceValidationError
    - No filename heuristics

Security/reference split:
    The *resolved* path is used for IO only.
    The *caller-provided* source_ref is stored unchanged in the bundle.

Security invariants
-------------------
- Descriptor source_ref is fully validated before any IO.
- Snapshot symlink resolution is checked against the repository root.
- Absolute filesystem paths are never exposed in public exception messages.
- source_ref is stored as-is (opaque caller-provided string).
- V2 source graph errors are wrapped as MissionSourceValidationError.
- No silent fallback between descriptor generations.
"""

from __future__ import annotations

import json
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

# Trusted prefix for descriptor source_refs (lexical, forward-slash only).
_DESCRIPTOR_PREFIX: str = "data/replays/"

# Resolved trusted root for descriptor containment check.
_REPLAYS_ROOT: Path = (_REPO_ROOT / "data" / "replays").resolve()

# ---------------------------------------------------------------------------
# Schema discrimination constants (B4 safe bounded read)
# ---------------------------------------------------------------------------

#: Bounded read size for schema discrimination (64 KiB — same as loader limits).
_SCHEMA_DISC_MAX_BYTES: int = 64 * 1024

# V1 descriptor schema identifier
_V1_SCHEMA: str = "gcsi.historical_replay_descriptor"
_V1_VERSION: int = 1
# V1 used "descriptor_schema" as key in JSON; "descriptor_version" for version
_V1_SCHEMA_KEY: str = "descriptor_schema"
_V1_VERSION_KEY: str = "descriptor_version"

# V2 descriptor schema identifier
_V2_SCHEMA: str = "gcsi.historical_replay_v2_descriptor"
_V2_VERSION: int = 1
_V2_SCHEMA_KEY: str = "schema"
_V2_VERSION_KEY: str = "schema_version"


# ---------------------------------------------------------------------------
# Descriptor path resolver (Phase 6E-C5.1)
# ---------------------------------------------------------------------------


def _resolve_descriptor_path(source_ref: str) -> Path:
    """Validate and resolve a descriptor source_ref to a trusted absolute Path.

    Two-layer validation:

    1. **Lexical** (before any filesystem access):
       - Must be a non-empty ``str``
       - Rejected characters: NUL, backslash, colon, percent (``%``),
         query (``?``), fragment (``#``)
       - Rejected forms: absolute POSIX (``/``), scheme-relative (``//``),
         ``..`` traversal components
       - Must start with ``data/replays/``
       - Basename must end with ``.json`` (case-insensitive)

    2. **Resolved containment** (after ``Path.resolve()``):
       - The resolved absolute path must remain inside
         ``<repo>/data/replays/`` (catches symlink escapes)
       - Target must exist and be a regular file

    The caller-provided ``source_ref`` string is **not** modified; only the
    returned resolved ``Path`` is used for IO.

    Args:
        source_ref: Caller-supplied opaque reference string.

    Returns:
        The resolved absolute ``Path`` to the descriptor file.

    Raises:
        MissionSourceValidationError: Lexical check fails, or symlink
            resolution escapes the trusted subtree.
        MissionSourceUnavailableError: Descriptor file is missing or is a
            directory.
    """
    # ---- Lexical layer ----

    if not isinstance(source_ref, str) or not source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # NUL byte
    if "\x00" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Backslash (Windows-style separator or UNC prefix)
    if "\\" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Colon (covers Windows drive letters like C: and URL schemes like http:)
    if ":" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Percent-encoding
    if "%" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Query string
    if "?" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Fragment
    if "#" in source_ref:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Scheme-relative URL (//)
    if source_ref.startswith("//"):
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Absolute POSIX path
    if source_ref.startswith("/"):
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # ".." traversal components (split on forward slash only)
    parts = source_ref.split("/")
    if ".." in parts or "." in parts:
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Required prefix
    if not source_ref.startswith(_DESCRIPTOR_PREFIX):
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # Required .json extension (case-insensitive)
    if not source_ref.lower().endswith(".json"):
        raise MissionSourceValidationError(
            "Replay descriptor source_ref is not an allowed repository-relative replay path."
        )

    # ---- Resolved containment layer ----

    candidate = _REPO_ROOT / source_ref
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise MissionSourceUnavailableError(
            "Replay descriptor is not available."
        ) from exc

    # Resolved path must stay inside <repo>/data/replays/
    try:
        resolved.relative_to(_REPLAYS_ROOT)
    except ValueError as exc:
        raise MissionSourceValidationError(
            "Replay descriptor source resolves outside the trusted replay directory."
        ) from exc

    # Existence and type checks — no path exposure in messages
    if not resolved.exists():
        raise MissionSourceUnavailableError(
            "Replay descriptor is not available."
        )
    if resolved.is_dir():
        raise MissionSourceUnavailableError(
            "Replay descriptor path points to a directory, not a file."
        )

    return resolved


# ---------------------------------------------------------------------------
# Snapshot path resolver
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
# Schema discriminator — bounded local JSON read (B4 Section 13)
# ---------------------------------------------------------------------------


def _discriminate_descriptor_schema(resolved_path: Path) -> str:
    """Perform a bounded local JSON read to discriminate descriptor generation.

    Only called on a path that has already been fully validated by
    _resolve_descriptor_path() (lexical + containment + existence checks).

    Returns one of:
        "V1" — V1 descriptor (gcsi.historical_replay_descriptor)
        "V2" — V2 descriptor (gcsi.historical_replay_v2_descriptor)

    Raises:
        MissionSourceValidationError — malformed JSON or unknown schema.
    """
    try:
        size = resolved_path.stat().st_size
        if size > _SCHEMA_DISC_MAX_BYTES:
            raise MissionSourceValidationError(
                "Replay descriptor file exceeds maximum allowed size."
            )
        raw = resolved_path.read_bytes()[:_SCHEMA_DISC_MAX_BYTES]
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise MissionSourceValidationError(
                "Replay descriptor is not valid JSON."
            )
    except (MissionSourceValidationError, MissionSourceUnavailableError):
        raise
    except OSError as exc:
        raise MissionSourceUnavailableError(
            "Replay descriptor is not available."
        ) from exc

    if not isinstance(data, dict):
        raise MissionSourceValidationError(
            "Replay descriptor JSON top level is not an object."
        )

    # Check V2 schema first (uses "schema" key)
    v2_schema = data.get(_V2_SCHEMA_KEY)
    if v2_schema == _V2_SCHEMA:
        return "V2"

    # Check V1 schema (uses "descriptor_schema" key)
    v1_schema = data.get(_V1_SCHEMA_KEY)
    if v1_schema == _V1_SCHEMA:
        return "V1"

    # Unknown or missing schema — no fallback, fail closed
    if v2_schema is not None or v1_schema is not None:
        raise MissionSourceValidationError(
            "Replay descriptor has an unknown schema. "
            "Only 'gcsi.historical_replay_descriptor' (V1) and "
            "'gcsi.historical_replay_v2_descriptor' (V2) are supported."
        )
    raise MissionSourceValidationError(
        "Replay descriptor has no recognized schema field. "
        "Expected 'schema' (V2) or 'descriptor_schema' (V1) at root level."
    )


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

        Dispatches to V1 or V2 load path based on safe schema discrimination.

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
            re-validation, cross-source semantic checks fail, assembly fails,
            or the descriptor schema is unknown.
        """
        # ----------------------------------------------------------------
        # Step 1: Validate source_ref and resolve descriptor path
        # ----------------------------------------------------------------
        try:
            descriptor_path = _resolve_descriptor_path(source_ref)
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise

        # ----------------------------------------------------------------
        # Step 2: Safe schema discrimination (bounded JSON read — no filename heuristics)
        # ----------------------------------------------------------------
        generation = _discriminate_descriptor_schema(descriptor_path)

        if generation == "V2":
            return self._load_v2(source_ref=source_ref, descriptor_path=descriptor_path)
        else:
            # generation == "V1"
            return self._load_v1(source_ref=source_ref, descriptor_path=descriptor_path)

    # ----------------------------------------------------------------
    # V1 load path (unchanged semantics from Phase 6E-C5)
    # ----------------------------------------------------------------

    def _load_v1(self, source_ref: str, descriptor_path: Path) -> MissionSourceBundle:
        """Load a V1 historical replay bundle."""
        # ----------------------------------------------------------------
        # Step 2: Load and validate the V1 descriptor content
        # ----------------------------------------------------------------
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
        # Step 3: Resolve snapshot paths (repository-relative, symlink-safe)
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
        # Step 4: Load Horizons snapshot
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
        # Step 5: Load IRDR snapshot
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
        # Step 6: Load GRDR snapshot
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
        # Step 7: Assemble Scenario + ProvenanceManifest (pure, no IO)
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
        # Step 8: Assemble MissionSourceBundle — source_ref preserved as-is
        # ----------------------------------------------------------------
        return MissionSourceBundle(
            scenario=scenario,
            provenance=manifest,
            provider_name=self.provider_name,
            source_mode=self.source_mode,
            source_ref=source_ref,  # opaque caller-provided string, unchanged
        )

    # ----------------------------------------------------------------
    # V2 load path (Phase B4)
    # ----------------------------------------------------------------

    def _load_v2(self, source_ref: str, descriptor_path: Path) -> MissionSourceBundle:
        """Load a V2 historical replay bundle.

        Loads the V2 descriptor, loads and verifies the complete source graph
        (411 candidates / 535 snapshots / 403 eligible), assembles the bundle
        via ReplayAssemblerV2, and wraps all errors as MissionSourceValidationError.

        The caller-provided source_ref is preserved as the bundle's source_ref
        so that reset semantics work correctly.

        No network access. All IO is local and offline.
        """
        from .v2_replay_descriptor import load_v2_replay_descriptor
        from .v2_source_graph import load_verified_v2_source_graph
        from .v2_replay_assembler import ReplayAssemblerV2

        # ----------------------------------------------------------------
        # Step 2: Load and validate the V2 descriptor
        # ----------------------------------------------------------------
        try:
            descriptor = load_v2_replay_descriptor(descriptor_path)
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "V2 replay descriptor could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 3: Load and fully verify the V2 source graph
        # ----------------------------------------------------------------
        try:
            source_graph = load_verified_v2_source_graph()
        except ValueError as exc:
            # Sanitize: do not expose absolute filesystem paths in public message.
            raise MissionSourceValidationError(
                "V2 source graph verification failed."
            ) from exc
        except RuntimeError as exc:
            # Sanitize: do not expose absolute filesystem paths in public message.
            raise MissionSourceValidationError(
                "V2 source graph verification failed."
            ) from exc
        except (MissionSourceUnavailableError, MissionSourceValidationError):
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "V2 source graph could not be loaded."
            ) from exc

        # ----------------------------------------------------------------
        # Step 4: Assemble MissionSourceBundle (pure, no IO)
        # The assembler receives source_ref so the bundle carries the
        # caller-provided descriptor path (not the source_bundle_ref).
        # ----------------------------------------------------------------
        try:
            bundle = ReplayAssemblerV2.assemble(
                descriptor=descriptor,
                source_graph=source_graph,
                source_ref=source_ref,
            )
        except MissionSourceValidationError:
            raise
        except Exception as exc:
            raise MissionSourceValidationError(
                "V2 historical replay assembly failed."
            ) from exc

        # ----------------------------------------------------------------
        # Step 5: Override provider_name and source_mode with canonical values
        # The assembler returns its own provider_name; the provider wraps it
        # with its own identity to preserve the single provider contract.
        # ----------------------------------------------------------------
        return MissionSourceBundle(
            scenario=bundle.scenario,
            provenance=bundle.provenance,
            provider_name=self.provider_name,
            source_mode=self.source_mode,
            source_ref=source_ref,  # opaque caller-provided string, unchanged
        )
