"""GCSI Phase 6C — Synthetic Scenario Provider.

Implements :class:`BaseMissionSourceProvider` for the ``synthetic_scenario``
source mode.

Load path
---------
::

    source_ref (file path)
         ↓
    read raw bytes → SHA-256 (first hash)
         ↓
    ScenarioLoader.load()  [delegates all Pydantic + simulated= validation]
         ↓
    read raw bytes → SHA-256 (second hash)
         ↓
    hashes must match  [source-change race guard — fails closed]
         ↓
    build synthetic ProvenanceRecord + field bindings
         ↓
    ProvenanceManifest
         ↓
    MissionSourceBundle

Security / trust
----------------
- ``source_ref`` is treated as an untrusted local path.  It is never
  executed or shell-expanded.  Raw file content is never exposed in
  exception messages.
- Provenance is constructed by *this* provider AFTER ScenarioLoader
  validation.  It is never extracted from the scenario JSON.
- No eval / exec / subprocess calls anywhere in this module.

Determinism
-----------
Identical source file + identical scenario → identical provenance output.

- ``provenance_id`` is derived deterministically from
  ``scenario_id + ":" + content_sha256`` using SHA-256.
- No UUID4.  No ``datetime.now()``.  All optional timestamp fields in the
  :class:`ProvenanceRecord` remain ``None``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models.anomaly_event import AnomalyEvent
from ..models.data_product import DataProduct
from ..models.mission_state import MissionState
from ..models.packet import Packet
from ..models.scenario import Scenario
from ..provenance.models import (
    FieldProvenanceBinding,
    ProvenanceKind,
    ProvenanceManifest,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)
from ..simulation.scenario_loader import ScenarioLoader
from .base import BaseMissionSourceProvider
from .errors import MissionSourceUnavailableError, MissionSourceValidationError
from .models import MissionSourceBundle, MissionSourceMode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROVIDER_NAME: str = "GCSI-SyntheticScenarioProvider"
_SOURCE_SYSTEM: str = "GCSI-scenario-json"


# ---------------------------------------------------------------------------
# Helper — SHA-256 file hash
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of *path*'s raw bytes.

    Uses Python standard library only — no new dependencies.
    """
    h = hashlib.sha256()
    # Read as raw bytes to ensure hash matches file on disk exactly.
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Helper — deterministic provenance_id
# ---------------------------------------------------------------------------


def _make_provenance_id(scenario_id: str, content_sha256: str) -> str:
    """Derive a deterministic provenance_id from scenario_id + sha256.

    The result is a 64-character lowercase hex string derived by hashing
    the concatenation ``scenario_id:content_sha256``.

    This is stable: the same inputs always produce the same output,
    with no UUID4 or timestamp involved.
    """
    raw = f"{scenario_id}:{content_sha256}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Helper — build field bindings for a single entity
# ---------------------------------------------------------------------------


def _bindings_for_entity(
    *,
    entity_type: str,
    entity_id: str,
    model_class: type,
    provenance_id: str,
) -> list[FieldProvenanceBinding]:
    """Create one :class:`FieldProvenanceBinding` per model field.

    Uses ``model_class.model_fields`` to discover field names so that if
    the model later gains a new field, this coverage automatically includes
    it without requiring manual updates here.
    """
    return [
        FieldProvenanceBinding(
            entity_type=entity_type,
            entity_id=entity_id,
            field_path=field_name,
            provenance_id=provenance_id,
        )
        for field_name in model_class.model_fields
    ]


# ---------------------------------------------------------------------------
# Helper — build complete field bindings for a Scenario
# ---------------------------------------------------------------------------


def _build_bindings(scenario: Scenario, provenance_id: str) -> list[FieldProvenanceBinding]:
    """Build all field provenance bindings for the scenario and its children.

    Entity boundaries are explicit (no arbitrary recursive reflection).
    Field discovery within each entity uses ``model_fields`` so that new
    model fields are automatically covered.
    """
    bindings: list[FieldProvenanceBinding] = []

    scenario_id = scenario.scenario_id
    mission_id = scenario.mission_state.mission_id

    # ------------------------------------------------------------------
    # 1. Scenario top-level fields
    # ------------------------------------------------------------------
    for field_name in Scenario.model_fields:
        bindings.append(
            FieldProvenanceBinding(
                entity_type="scenario",
                entity_id=scenario_id,
                field_path=field_name,
                provenance_id=provenance_id,
            )
        )

    # ------------------------------------------------------------------
    # 2. link_inputs keys — each key gets its own binding
    # ------------------------------------------------------------------
    for key in scenario.link_inputs:
        bindings.append(
            FieldProvenanceBinding(
                entity_type="scenario",
                entity_id=scenario_id,
                field_path=f"link_inputs.{key}",
                provenance_id=provenance_id,
            )
        )

    # ------------------------------------------------------------------
    # 3. MissionState fields
    # ------------------------------------------------------------------
    bindings.extend(
        _bindings_for_entity(
            entity_type="mission_state",
            entity_id=mission_id,
            model_class=MissionState,
            provenance_id=provenance_id,
        )
    )

    # ------------------------------------------------------------------
    # 4. Packets
    # ------------------------------------------------------------------
    for packet in scenario.packets:
        bindings.extend(
            _bindings_for_entity(
                entity_type="packet",
                entity_id=packet.packet_id,
                model_class=Packet,
                provenance_id=provenance_id,
            )
        )

    # ------------------------------------------------------------------
    # 5. DataProducts
    # ------------------------------------------------------------------
    for product in scenario.data_products:
        bindings.extend(
            _bindings_for_entity(
                entity_type="data_product",
                entity_id=product.product_id,
                model_class=DataProduct,
                provenance_id=provenance_id,
            )
        )

    # ------------------------------------------------------------------
    # 6. AnomalyEvents
    # ------------------------------------------------------------------
    for anomaly in scenario.anomalies:
        bindings.extend(
            _bindings_for_entity(
                entity_type="anomaly",
                entity_id=anomaly.anomaly_id,
                model_class=AnomalyEvent,
                provenance_id=provenance_id,
            )
        )

    return bindings


# ---------------------------------------------------------------------------
# SyntheticScenarioProvider
# ---------------------------------------------------------------------------


class SyntheticScenarioProvider(BaseMissionSourceProvider):
    """Mission-source provider for GCSI synthetic scenario JSON files.

    Wraps the existing :class:`~backend.app.simulation.scenario_loader.ScenarioLoader`
    with source-hashing, provenance construction, and source-change safety.

    Key invariant (Phase 6C — K)
    ----------------------------
    For any valid *source_ref*::

        direct = ScenarioLoader.load(source_ref)
        bundle = SyntheticScenarioProvider().load(source_ref)

        bundle.scenario.model_dump() == direct.model_dump()   # must be True

    The provider only adds the provenance sidecar OUTSIDE the Scenario;
    it never alters Scenario values, ordering, or structure.
    """

    # ------------------------------------------------------------------
    # BaseMissionSourceProvider properties
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return _PROVIDER_NAME

    @property
    def source_mode(self) -> MissionSourceMode:
        return MissionSourceMode.SYNTHETIC_SCENARIO

    # ------------------------------------------------------------------
    # load
    # ------------------------------------------------------------------

    def load(self, source_ref: str) -> MissionSourceBundle:
        """Load the synthetic scenario at *source_ref* and return a bundle.

        Parameters
        ----------
        source_ref:
            File-system path to a GCSI scenario JSON file.
            Treated as an untrusted local path — never executed or
            shell-expanded.

        Returns
        -------
        MissionSourceBundle

        Raises
        ------
        MissionSourceUnavailableError
            The scenario file does not exist at *source_ref*.

        MissionSourceValidationError
            The scenario JSON is invalid, ``simulated=False``, or the
            source file contents changed between the two hash reads
            (source-change race condition).
        """
        file_path = Path(source_ref)

        # ----------------------------------------------------------------
        # Step 1 — check existence before any hashing
        # ----------------------------------------------------------------
        if not file_path.exists():
            raise MissionSourceUnavailableError(
                f"Scenario source not found: <redacted path>"
            )

        # ----------------------------------------------------------------
        # Step 2 — first hash read (before ScenarioLoader)
        # ----------------------------------------------------------------
        try:
            sha256_before = _sha256_file(file_path)
        except OSError:
            raise MissionSourceUnavailableError(
                "Scenario source could not be read (IO error before loading)."
            )

        # ----------------------------------------------------------------
        # Step 3 — delegate to ScenarioLoader for all model validation
        #           (includes simulated=True enforcement)
        # ----------------------------------------------------------------
        try:
            scenario: Scenario = ScenarioLoader.load(source_ref)
        except FileNotFoundError:
            # Race: file disappeared between exists() and load()
            raise MissionSourceUnavailableError(
                "Scenario source disappeared between availability check and loading."
            )
        except ValueError as exc:
            # Covers: invalid JSON, Pydantic validation failure, simulated=False
            raise MissionSourceValidationError(
                f"Scenario source failed validation: {exc}"
            ) from exc

        # ----------------------------------------------------------------
        # Step 4 — second hash read (after ScenarioLoader)
        # ----------------------------------------------------------------
        try:
            sha256_after = _sha256_file(file_path)
        except OSError:
            raise MissionSourceValidationError(
                "Scenario source could not be re-read for hash verification "
                "(IO error after loading)."
            )

        # ----------------------------------------------------------------
        # Step 5 — source-change safety guard
        # ----------------------------------------------------------------
        if sha256_before != sha256_after:
            raise MissionSourceValidationError(
                "Scenario source content changed during loading "
                "(hash mismatch between pre-load and post-load reads). "
                "The bundle cannot be trusted."
            )

        content_sha256: str = sha256_before

        # ----------------------------------------------------------------
        # Step 6 — build provenance
        # ----------------------------------------------------------------
        try:
            bundle = self._build_bundle(
                scenario=scenario,
                content_sha256=content_sha256,
                source_ref=source_ref,
            )
        except Exception as exc:
            raise MissionSourceValidationError(
                f"Failed to build provenance for scenario: {exc}"
            ) from exc

        return bundle

    # ------------------------------------------------------------------
    # Internal — build the MissionSourceBundle
    # ------------------------------------------------------------------

    def _build_bundle(
        self,
        *,
        scenario: Scenario,
        content_sha256: str,
        source_ref: str,
    ) -> MissionSourceBundle:
        """Construct provenance and assemble the bundle.

        Called only after ScenarioLoader validation and source-change guard
        have both passed.
        """
        provenance_id = _make_provenance_id(scenario.scenario_id, content_sha256)

        # ----------------------------------------------------------------
        # Single SYNTHETIC provenance record for the scenario source file
        # ----------------------------------------------------------------
        record = ProvenanceRecord(
            provenance_id=provenance_id,
            kind=ProvenanceKind.SYNTHETIC,
            source_system=_SOURCE_SYSTEM,
            source_record_id=scenario.scenario_id,
            content_sha256=content_sha256,
            validation_status=ProvenanceValidationStatus.VALIDATED,
            # Explicitly NOT set (determinism requirement):
            #   retrieved_at=None, normalized_at=None, observed_at=None
        )

        # ----------------------------------------------------------------
        # Field-level bindings
        # ----------------------------------------------------------------
        bindings = _build_bindings(scenario, provenance_id)

        # ----------------------------------------------------------------
        # Assemble manifest — ProvenanceManifest validates integrity
        # ----------------------------------------------------------------
        manifest = ProvenanceManifest(
            records=(record,),
            bindings=tuple(bindings),
        )

        return MissionSourceBundle(
            scenario=scenario,
            provenance=manifest,
            provider_name=self.provider_name,
            source_mode=self.source_mode,
            source_ref=source_ref,
        )
