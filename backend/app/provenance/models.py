"""GCSI Phase 6B — Mission-Data Provenance Domain Models.

DESIGN PRINCIPLES
-----------------
1.  FACT VALUES remain clean canonical GCSI domain values in their host
    models (DataProduct, Packet, etc.).  This sidecar carries ONLY
    provenance metadata — it never touches the host models.

2.  These models are completely unknown to the existing decision engine:
    ScenarioLoader, TelecomEngine, PlanEvaluator, MissionOutcomeEvaluator,
    TransmissionSimulator, candidate generators, AI providers, API routes,
    state.py, and the frontend.

3.  No persistence, no runtime registry, no global state.  The future
    replay assembler will own and populate these structures.

4.  IMPORTANT: This provenance is a *different concept* from the plan
    integrity / AI trust provenance in ``plan_integrity.PlanSource``.
    ``PlanSource`` answers "who generated the plan?"
    ``ProvenanceKind`` answers "where did this mission-data fact come from?"

SHA-256 FORMAT
--------------
``content_sha256``, when present, must be exactly 64 lowercase hexadecimal
characters.  This represents a source-content hash supplied by a future
adapter / snapshot layer; it is NOT computed automatically here.

DATETIME POLICY
---------------
All datetime fields must be timezone-aware (``tzinfo`` is not None).
Timezone-naive datetimes are rejected at validation time.  Replay
snapshots require reproducibility; ``datetime.now()`` is never called
during model construction.

IMMUTABILITY CONTRACT
---------------------
- ``model_config = ConfigDict(frozen=True, extra="forbid")``
  prevents mutation and rejects unknown fields, matching GCSI's existing
  strict model practice.
- List defaults use ``default_factory``, not mutable literals.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# SHA-256 validation helper
# ---------------------------------------------------------------------------

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256(value: str) -> str:
    """Validate that *value* is exactly 64 lowercase hex characters."""
    if not _SHA256_RE.match(value):
        raise ValueError(
            f"content_sha256 must be exactly 64 lowercase hexadecimal characters; "
            f"got {value!r} (length {len(value)})"
        )
    return value


def _validate_aware_datetime(value: datetime) -> datetime:
    """Reject timezone-naive datetimes."""
    if value.tzinfo is None:
        raise ValueError(
            "Datetime must be timezone-aware (tzinfo must not be None). "
            "Use datetime(..., tzinfo=timezone.utc) or an aware datetime."
        )
    return value


# ---------------------------------------------------------------------------
# A. ProvenanceKind
# ---------------------------------------------------------------------------


class ProvenanceKind(str, Enum):
    """Canonical taxonomy of mission-data fact origins.

    Values are stable snake_case strings suitable for JSON serialization.

    EXTERNAL_AUTHORITATIVE
        A value obtained from a validated authoritative external source,
        e.g. NASA/JPL Horizons or NASA PDS.  Does NOT mean infallible
        truth — means the value was obtained from the recorded authoritative
        source.

    DERIVED
        A value deterministically calculated by GCSI from one or more
        validated source values.  Examples: propagation delay from distance,
        age from observation timestamp, unit conversion.

    MODELED
        A value introduced by GCSI policy or replay construction because the
        real operational state is not publicly available.  Examples:
        reconstructed queue membership, replay deadline, criticality policy,
        delivery requirement.

    SYNTHETIC
        Controlled fictional ground truth created specifically for a GCSI
        scenario or benchmark, such as ASTERIA-7.

    AI_DERIVED
        A semantic or advisory output produced by an AI provider.  Examples:
        semantic ranking, recommendation reasoning.

    NOTE: HUMAN is intentionally absent.  Human/operator authority is a
    separate decision-authority concept, not the origin of a mission-data
    fact.
    """

    EXTERNAL_AUTHORITATIVE = "external_authoritative"
    DERIVED = "derived"
    MODELED = "modeled"
    SYNTHETIC = "synthetic"
    AI_DERIVED = "ai_derived"


# ---------------------------------------------------------------------------
# B. ProvenanceValidationStatus
# ---------------------------------------------------------------------------


class ProvenanceValidationStatus(str, Enum):
    """Validation gate status for a provenance record.

    VALIDATED
        The source and value have passed GCSI's validation boundary.
        May be treated as authoritative for its kind.

    PENDING
        The record has been ingested but validation has not yet completed.
        MUST NOT be treated as authoritative until promoted to VALIDATED.

    REJECTED
        Validation failed.  A REJECTED record MUST NEVER be interpreted as
        authoritative, regardless of its kind.  Systems that consume
        provenance manifests must treat REJECTED records as absent or
        erroneous.
    """

    VALIDATED = "validated"
    PENDING = "pending"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# C. ProvenanceRecord
# ---------------------------------------------------------------------------


class ProvenanceRecord(BaseModel):
    """One provenance lineage node: source or derivation origin of a fact.

    Each record represents a single origin story for one or more mission-data
    field values.  Multiple records may exist in a manifest (e.g. a PDS
    archive record for raw values and a DERIVED record for computed values).

    Fields
    ------
    provenance_id
        Stable unique identifier for this record within a manifest.
        Must be unique within a ``ProvenanceManifest``.

    kind
        Canonical provenance kind (see ``ProvenanceKind``).

    source_system
        Human-readable name of the originating system or subsystem.
        Examples: "NASA-PDS", "GCSI-replay-assembler", "GCSI-benchmark",
        "GCSI-AI-granite".  Always required — even SYNTHETIC and MODELED
        records must name the system that introduced them.

    source_record_id
        Optional opaque identifier in the source system
        (e.g. a PDS product LIDVID or Horizons query ID).

    source_uri
        Optional URI pointing to the source record.  Not validated as
        reachable — no network calls are made.

    source_version
        Optional version string of the source data or schema.

    observed_at
        Optional timezone-aware datetime when the value was originally
        observed or recorded by the source system.

    retrieved_at
        Optional timezone-aware datetime when GCSI retrieved/downloaded
        this value from the source.

    normalized_at
        Optional timezone-aware datetime when GCSI normalized or converted
        the value into canonical GCSI units.

    validation_status
        Current validation gate status.  Defaults to PENDING.

    content_sha256
        Optional SHA-256 hex digest (exactly 64 lowercase hex characters)
        of the raw source content, supplied by a future snapshot layer.
        Not computed automatically here.

    derivation_method
        For DERIVED records: a short, stable identifier for the computation
        method, e.g. "propagation_delay_from_distance_km".

    parent_provenance_ids
        IDs of provenance records that this record was derived from or
        depends on.  Used for lineage tracing.  Must be validated against
        existing records by ``ProvenanceManifest``.

    notes
        Optional free-text annotation.  Not semantically interpreted by
        GCSI.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    provenance_id: str = Field(
        description="Stable unique identifier for this record within a manifest."
    )
    kind: ProvenanceKind = Field(
        description="Canonical provenance kind."
    )
    source_system: str = Field(
        description=(
            "Name of the originating system, e.g. 'NASA-PDS', "
            "'GCSI-replay-assembler', 'GCSI-benchmark', 'GCSI-AI-granite'."
        )
    )
    source_record_id: Optional[str] = Field(
        default=None,
        description="Optional opaque record identifier in the source system.",
    )
    source_uri: Optional[str] = Field(
        default=None,
        description="Optional URI pointing to the source record (not validated as reachable).",
    )
    source_version: Optional[str] = Field(
        default=None,
        description="Optional version string of the source data or schema.",
    )
    observed_at: Optional[datetime] = Field(
        default=None,
        description="Timezone-aware datetime when the value was originally observed.",
    )
    retrieved_at: Optional[datetime] = Field(
        default=None,
        description="Timezone-aware datetime when GCSI retrieved this value.",
    )
    normalized_at: Optional[datetime] = Field(
        default=None,
        description="Timezone-aware datetime when GCSI normalized the value.",
    )
    validation_status: ProvenanceValidationStatus = Field(
        default=ProvenanceValidationStatus.PENDING,
        description="Validation gate status. Defaults to PENDING.",
    )
    content_sha256: Optional[str] = Field(
        default=None,
        description=(
            "SHA-256 hex digest (64 lowercase hex chars) of raw source content. "
            "Supplied by a future snapshot layer; not computed here."
        ),
    )
    derivation_method: Optional[str] = Field(
        default=None,
        description=(
            "For DERIVED records: stable identifier for the computation method, "
            "e.g. 'propagation_delay_from_distance_km'."
        ),
    )
    parent_provenance_ids: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of provenance records this record was derived from. "
            "Validated against existing records by ProvenanceManifest."
        ),
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional free-text annotation; not semantically interpreted.",
    )

    @field_validator("content_sha256", mode="before")
    @classmethod
    def _validate_content_sha256(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _validate_sha256(v)

    @field_validator("observed_at", "retrieved_at", "normalized_at", mode="before")
    @classmethod
    def _validate_aware_datetimes(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        return _validate_aware_datetime(v)

    @field_validator("parent_provenance_ids", mode="before")
    @classmethod
    def _validate_no_self_parent(cls, v: list[str], info) -> list[str]:
        # Self-parent check is deferred to model_validator where we have the full
        # model data including provenance_id.
        return v

    @model_validator(mode="after")
    def _reject_self_parent(self) -> "ProvenanceRecord":
        """A record cannot directly parent itself."""
        if self.provenance_id in self.parent_provenance_ids:
            raise ValueError(
                f"ProvenanceRecord '{self.provenance_id}' lists itself in "
                f"parent_provenance_ids. A record cannot directly parent itself."
            )
        return self


# ---------------------------------------------------------------------------
# D. FieldProvenanceBinding
# ---------------------------------------------------------------------------


class FieldProvenanceBinding(BaseModel):
    """Binds one exact canonical field on one entity to a provenance record.

    This is the sidecar link between "which fact" and "where it came from".

    Example future binding::

        FieldProvenanceBinding(
            entity_type="data_product",
            entity_id="JUNO-PRODUCT-123",
            field_path="size_bits",
            provenance_id="pds-record-001",
        )

    ``field_path`` is used rather than a top-level field name so that future
    nested canonical fields can be represented without model changes
    (e.g. ``"link_budget.snr_db"``).

    The binding is intentionally NOT placed inside DataProduct or any host
    model.  The sidecar design is intentional.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity_type: str = Field(
        description=(
            "Type of the GCSI domain entity owning this field, "
            "e.g. 'data_product', 'packet', 'link_state'."
        )
    )
    entity_id: str = Field(
        description="Canonical identifier of the entity instance, e.g. 'JUNO-PRODUCT-123'."
    )
    field_path: str = Field(
        description=(
            "Dot-separated path to the canonical field within the entity, "
            "e.g. 'size_bits', 'link_budget.snr_db'."
        )
    )
    provenance_id: str = Field(
        description="ID of the ProvenanceRecord that is the source of this field value."
    )


# ---------------------------------------------------------------------------
# E. ProvenanceManifest
# ---------------------------------------------------------------------------


class ProvenanceManifest(BaseModel):
    """Self-contained sidecar provenance manifest.

    Contains all provenance records and field bindings for one replay
    snapshot or mission-data set.  Validates full reference integrity on
    construction.

    Integrity rules enforced
    ------------------------
    1. ``provenance_id`` values are unique across all records.
    2. Every ``FieldProvenanceBinding.provenance_id`` references an existing
       record in this manifest.
    3. Every ``parent_provenance_id`` in every record references an existing
       record in this manifest.
    4. A record cannot directly parent itself (enforced by ``ProvenanceRecord``
       field validator; also enforced here during graph traversal).
    5. Duplicate bindings for the exact same (entity_type, entity_id, field_path)
       are rejected.  Phase 6B enforces ONE authoritative binding per canonical
       field.
    6. Provenance-parent cycles are detected using depth-first search over the
       parent-graph.

    Phase 6B does NOT implement persistence, runtime registry, global state,
    API endpoints, or snapshot loaders.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    records: list[ProvenanceRecord] = Field(
        default_factory=list,
        description="All provenance records in this manifest.",
    )
    bindings: list[FieldProvenanceBinding] = Field(
        default_factory=list,
        description="Field-to-record bindings in this manifest.",
    )

    @model_validator(mode="after")
    def _validate_manifest_integrity(self) -> "ProvenanceManifest":
        """Enforce all manifest integrity rules."""
        record_index = self._build_record_index()
        self._validate_parent_references(record_index)
        self._detect_parent_cycles(record_index)
        self._validate_binding_references(record_index)
        self._validate_no_duplicate_bindings()
        return self

    # ------------------------------------------------------------------
    # Rule 1 — unique provenance_ids
    # ------------------------------------------------------------------

    def _build_record_index(self) -> dict[str, ProvenanceRecord]:
        """Build {provenance_id: record} index; raise on duplicates."""
        index: dict[str, ProvenanceRecord] = {}
        for record in self.records:
            pid = record.provenance_id
            if pid in index:
                raise ValueError(
                    f"Duplicate provenance_id '{pid}' found in manifest. "
                    f"All provenance_id values must be unique."
                )
            index[pid] = record
        return index

    # ------------------------------------------------------------------
    # Rule 3 — parent references must exist
    # ------------------------------------------------------------------

    def _validate_parent_references(
        self, record_index: dict[str, ProvenanceRecord]
    ) -> None:
        """Every parent_provenance_id must reference an existing record."""
        for record in self.records:
            for parent_id in record.parent_provenance_ids:
                if parent_id not in record_index:
                    raise ValueError(
                        f"ProvenanceRecord '{record.provenance_id}' references "
                        f"parent_provenance_id '{parent_id}' which does not exist "
                        f"in this manifest."
                    )

    # ------------------------------------------------------------------
    # Rule 6 — cycle detection using iterative DFS
    # ------------------------------------------------------------------

    def _detect_parent_cycles(
        self, record_index: dict[str, ProvenanceRecord]
    ) -> None:
        """Detect cycles in the provenance parent-graph.

        Uses a standard iterative DFS with a ``grey/black`` (in-stack/done)
        node colouring scheme.  Raises ``ValueError`` with the cycle path if
        any cycle is detected.

        No additional dependencies are required.
        """
        # Build adjacency: node → set of parent nodes
        adjacency: dict[str, list[str]] = {
            pid: list(rec.parent_provenance_ids)
            for pid, rec in record_index.items()
        }

        # Node states: 0 = unvisited, 1 = in DFS stack (grey), 2 = done (black)
        state: dict[str, int] = {pid: 0 for pid in record_index}

        def _dfs(start: str) -> None:
            # Iterative DFS with explicit stack to avoid Python recursion limits.
            # Each stack entry: (node, iterator-over-parents, path-so-far)
            path: list[str] = []
            dfs_stack: list[tuple[str, int]] = []  # (node, parent_index)

            dfs_stack.append((start, 0))
            path.append(start)
            state[start] = 1

            while dfs_stack:
                node, idx = dfs_stack[-1]
                parents = adjacency[node]

                if idx >= len(parents):
                    # All parents of this node have been processed.
                    state[node] = 2
                    dfs_stack.pop()
                    path.pop()
                    continue

                # Advance to the next parent.
                dfs_stack[-1] = (node, idx + 1)
                parent = parents[idx]

                if state[parent] == 1:
                    # Back-edge detected → cycle found.
                    cycle_start_idx = path.index(parent)
                    cycle = path[cycle_start_idx:] + [parent]
                    raise ValueError(
                        f"Provenance parent cycle detected: "
                        + " → ".join(cycle)
                    )

                if state[parent] == 0:
                    state[parent] = 1
                    path.append(parent)
                    dfs_stack.append((parent, 0))
                # state == 2 → already fully processed; skip.

        for pid in record_index:
            if state[pid] == 0:
                _dfs(pid)

    # ------------------------------------------------------------------
    # Rule 2 — binding references must exist
    # ------------------------------------------------------------------

    def _validate_binding_references(
        self, record_index: dict[str, ProvenanceRecord]
    ) -> None:
        """Every FieldProvenanceBinding must reference an existing record."""
        for binding in self.bindings:
            if binding.provenance_id not in record_index:
                raise ValueError(
                    f"FieldProvenanceBinding for "
                    f"({binding.entity_type!r}, {binding.entity_id!r}, "
                    f"{binding.field_path!r}) references provenance_id "
                    f"'{binding.provenance_id}' which does not exist in this manifest."
                )

    # ------------------------------------------------------------------
    # Rule 5 — no duplicate field bindings
    # ------------------------------------------------------------------

    def _validate_no_duplicate_bindings(self) -> None:
        """Reject duplicate bindings for the exact same field on the same entity."""
        seen: set[tuple[str, str, str]] = set()
        for binding in self.bindings:
            key = (binding.entity_type, binding.entity_id, binding.field_path)
            if key in seen:
                raise ValueError(
                    f"Duplicate FieldProvenanceBinding for "
                    f"entity_type={binding.entity_type!r}, "
                    f"entity_id={binding.entity_id!r}, "
                    f"field_path={binding.field_path!r}. "
                    f"Phase 6B enforces one authoritative binding per canonical field."
                )
            seen.add(key)
