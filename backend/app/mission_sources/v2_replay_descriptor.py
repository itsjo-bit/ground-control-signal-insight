"""GCSI Phase 6F-B3 — Historical Replay V2 Descriptor Model and Loader.

This module provides:

1. HistoricalReplayV2Descriptor — strict immutable descriptor model
2. load_v2_replay_descriptor()  — production safe loader with confinement

The V2 descriptor is the replay CONFIGURATION that binds:
- replay identity
- source bundle identity/reference
- decision epoch and policy
- replay policy identity
- modeled mission state
- modeled telecom/link inputs
- queue construction policy
- size proxy policy
- product policy

It does NOT duplicate the 403 archive records. Those come from the frozen
verified source graph.

Design invariants
-----------------
- frozen=True, extra="forbid"
- descriptor_id is deterministic SHA-256 over all semantic descriptor content
  (excluding descriptor_id itself).
- Production loader enforces repository confinement, bounded read, strict
  model parse, descriptor_id recomputation.
- No network requests. No source data reconstruction.

Versioned prefix for descriptor_id
------------------------------------
  "gcsi.historical_replay_v2_descriptor:v1:"
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import MissionSourceUnavailableError, MissionSourceValidationError
from .replay_descriptor import replay_risk_level_from_score


# ---------------------------------------------------------------------------
# Required semantic roles — complete role set for V2 product policy
# ---------------------------------------------------------------------------

_REQUIRED_SEMANTIC_ROLES: frozenset[str] = frozenset({
    "instrument_diagnostic",
    "radiometry_science",
    "ultraviolet_observation",
    "visible_imaging",
    "magnetic_field",
    "plasma_particles",
    "energetic_particles",
    "radio_plasma_survey",
    "radio_plasma_burst",
})

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DESCRIPTOR_V2_SCHEMA: str = "gcsi.historical_replay_v2_descriptor"
DESCRIPTOR_V2_VERSION: int = 1
_MAX_DESCRIPTOR_V2_BYTES: int = 64 * 1024  # 64 KiB
_DESCRIPTOR_V2_ID_PREFIX: str = "gcsi.historical_replay_v2_descriptor:v1:"

_ALLOWED_DIR: Path = (Path(__file__).resolve().parents[3] / "data" / "replays")


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class V2ModeledLinkInputs(BaseModel):
    """MODELED link inputs for a V2 historical replay.

    These are GCSI replay modeling assumptions — NOT NASA/DSN measurements.
    latency_s is protocol/link-stack latency, NOT free-space propagation time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snr_db: float = Field(description="MODELED SNR in dB.")
    rssi_dbm: float = Field(description="MODELED RSSI in dBm.")
    nominal_data_rate_bps: float = Field(description="MODELED nominal data rate in bps.")
    latency_s: float = Field(
        description=(
            "MODELED protocol/link-stack overhead latency in seconds. "
            "NOT free-space propagation time. "
            "Propagation delay is derived from Horizons range_km."
        )
    )
    link_stability: float = Field(description="MODELED link stability in [0, 1].")
    remaining_window_s: float = Field(description="MODELED communication window in seconds.")

    @field_validator("nominal_data_rate_bps", mode="after")
    @classmethod
    def _pos_rate(cls, v: float) -> float:
        if not math.isfinite(v) or v <= 0:
            raise ValueError("nominal_data_rate_bps must be finite and > 0.")
        return v

    @field_validator("latency_s", "remaining_window_s", mode="after")
    @classmethod
    def _non_neg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("Value must be finite and >= 0.")
        return v

    @field_validator("link_stability", mode="after")
    @classmethod
    def _unit_interval(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("link_stability must be finite and in [0, 1].")
        return v

    @field_validator("snr_db", "rssi_dbm", mode="after")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Value must be finite.")
        return v


class V2ModeledMissionState(BaseModel):
    """MODELED mission state for a V2 historical replay.

    mission_id identifies GCSI's modeled replay context for PJ62.
    NOT a NASA mission-phase assertion.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission_id: str = Field(description="GCSI modeled mission identifier.")
    mission_phase: str = Field(description="GCSI modeled mission phase.")
    current_event: str = Field(description="GCSI modeled current event description.")
    event_time_remaining_s: float = Field(description="GCSI modeled event time remaining (s).")
    comm_window_remaining_s: float = Field(description="GCSI modeled comm window remaining (s).")
    risk_score: float = Field(description="GCSI modeled risk score in [0, 1].")
    risk_level: str = Field(description="GCSI modeled risk level (LOW/MEDIUM/HIGH/CRITICAL).")

    @field_validator("mission_id", "mission_phase", "current_event", "risk_level", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("event_time_remaining_s", "comm_window_remaining_s", mode="after")
    @classmethod
    def _non_neg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("Value must be finite and >= 0.")
        return v

    @field_validator("risk_score", mode="after")
    @classmethod
    def _risk_score_range(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("risk_score must be finite and in [0, 1].")
        return v


class V2QueueMembershipPolicy(BaseModel):
    """GCSI modeled queue membership policy for V2 replay.

    Queue membership is a MODELED reconstruction, not a historical claim.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(description="Stable policy identifier.")
    description: str = Field(description="Human-readable policy description.")
    source_mode: str = Field(description="Must be 'MODELED'.")
    eligible_logical_count: int = Field(description="Expected eligible logical product count.")

    @field_validator("policy_id", "description", "source_mode", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("source_mode", mode="after")
    @classmethod
    def _must_be_modeled(cls, v: str) -> str:
        if v != "MODELED":
            raise ValueError("source_mode must be 'MODELED'.")
        return v


class V2SizePolicy(BaseModel):
    """GCSI modeled size proxy policy for V2 replay.

    Archive product size ≠ historical spacecraft downlink bytes.
    DataProduct.size_bits is a MODELED replay burden proxy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(description="Stable policy identifier.")
    description: str = Field(description="Human-readable policy description.")
    exact_proxy_rule: str = Field(
        description=(
            "Rule for products with exact archive size: "
            "'max(exact archive_total_size_bytes) * 8'."
        )
    )
    fallback_rule: str = Field(
        description=(
            "Rule for products with no exact size: "
            "'median_low of eligible SIZE_METADATA_EXACT sources * 8'."
        )
    )

    @field_validator("policy_id", "description", "exact_proxy_rule", "fallback_rule", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v


class V2ProductPolicyEntry(BaseModel):
    """GCSI modeled product scoring policy for one semantic role."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    semantic_role: str = Field(description="GCSI semantic role identifier.")
    criticality: float = Field(description="MODELED criticality [0, 1].")
    mission_relevance: float = Field(description="MODELED mission relevance [0, 1].")
    scientific_value: float = Field(description="MODELED scientific value [0, 1].")
    retry_cost: float = Field(description="MODELED retry cost [0, 1].")

    @field_validator("semantic_role", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("semantic_role must not be empty.")
        return v

    @field_validator("criticality", "mission_relevance", "scientific_value", "retry_cost", mode="after")
    @classmethod
    def _unit_interval(cls, v: float) -> float:
        if not math.isfinite(v) or not (0.0 <= v <= 1.0):
            raise ValueError("Value must be finite and in [0, 1].")
        return v


class V2ProductPolicy(BaseModel):
    """GCSI centralized product policy table for V2 replay."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(description="Stable policy identifier.")
    deadline_s: float = Field(description="MODELED delivery deadline in seconds.")
    delivery_requirement: str = Field(description="MODELED delivery requirement identifier.")
    entries: tuple[V2ProductPolicyEntry, ...] = Field(
        description="Per-semantic-role scoring entries."
    )

    @field_validator("policy_id", "delivery_requirement", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("deadline_s", mode="after")
    @classmethod
    def _non_neg(cls, v: float) -> float:
        if not math.isfinite(v) or v < 0:
            raise ValueError("deadline_s must be finite and >= 0.")
        return v


# ---------------------------------------------------------------------------
# HistoricalReplayV2Descriptor — the main model
# ---------------------------------------------------------------------------


class HistoricalReplayV2Descriptor(BaseModel):
    """Strict immutable descriptor for a GCSI V2 historical replay.

    Binds replay identity, source bundle reference, decision epoch,
    and all modeled policy parameters.

    Does NOT contain the 403 archive records — those live in the verified
    source graph. The descriptor is the replay CONFIGURATION.

    descriptor_id is a deterministic SHA-256 over ALL semantic content
    (excluding descriptor_id itself). Prefix:
        "gcsi.historical_replay_v2_descriptor:v1:"
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    # Identity
    descriptor_schema: Literal["gcsi.historical_replay_v2_descriptor"] = Field(
        alias="schema",
        description="Schema identifier.",
    )
    schema_version: Literal[1] = Field(description="Schema version. Must be 1.")
    descriptor_id: str = Field(
        description=(
            "Deterministic SHA-256 over all semantic descriptor content, "
            "excluding descriptor_id. Prefix: "
            "'gcsi.historical_replay_v2_descriptor:v1:'"
        )
    )
    replay_id: str = Field(description="Stable replay identifier.")
    simulated: Literal[True] = Field(
        description="Historical replay is always simulated=True."
    )

    # Source bundle binding
    source_bundle_id: str = Field(description="SHA-256 bundle_id of the source bundle.")
    source_bundle_ref: str = Field(
        description="POSIX relative path to the source bundle JSON."
    )

    # Decision epoch
    decision_epoch_utc: str = Field(description="ISO-8601 UTC decision epoch.")
    decision_epoch_policy: str = Field(description="Decision epoch policy identifier.")

    # Policy IDs
    size_policy_id: str = Field(description="Stable size proxy policy identifier.")
    product_policy_id: str = Field(description="Stable product policy identifier.")

    # Modeled state
    modeled_link_inputs: V2ModeledLinkInputs = Field(description="MODELED link inputs.")
    modeled_mission_state: V2ModeledMissionState = Field(description="MODELED mission state.")

    # Queue policy
    queue_membership_policy: V2QueueMembershipPolicy = Field(
        description="MODELED queue membership policy."
    )

    # Size policy (embedded for completeness)
    size_policy: V2SizePolicy = Field(description="MODELED size proxy policy.")

    # Product policy table
    product_policy: V2ProductPolicy = Field(description="GCSI product policy table.")

    # ---- Validators ----

    @field_validator(
        "descriptor_id", "replay_id", "source_bundle_id", "source_bundle_ref",
        "decision_epoch_utc", "decision_epoch_policy", "size_policy_id", "product_policy_id",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @model_validator(mode="after")
    def _validate_descriptor(self) -> "HistoricalReplayV2Descriptor":
        # ---- Cross-field: size_policy_id == size_policy.policy_id ----
        if self.size_policy_id != self.size_policy.policy_id:
            raise ValueError(
                f"size_policy_id {self.size_policy_id!r} != "
                f"size_policy.policy_id {self.size_policy.policy_id!r}. "
                "Cross-field invariant violated."
            )

        # ---- Cross-field: product_policy_id == product_policy.policy_id ----
        if self.product_policy_id != self.product_policy.policy_id:
            raise ValueError(
                f"product_policy_id {self.product_policy_id!r} != "
                f"product_policy.policy_id {self.product_policy.policy_id!r}. "
                "Cross-field invariant violated."
            )

        # ---- Product policy: no duplicate semantic roles ----
        roles_seen: list[str] = []
        for entry in self.product_policy.entries:
            if entry.semantic_role in roles_seen:
                raise ValueError(
                    f"Duplicate semantic role {entry.semantic_role!r} in product_policy.entries."
                )
            roles_seen.append(entry.semantic_role)
        roles_present = set(roles_seen)

        # ---- Product policy: complete required role set ----
        missing_roles = _REQUIRED_SEMANTIC_ROLES - roles_present
        if missing_roles:
            raise ValueError(
                f"product_policy.entries is missing required semantic roles: "
                f"{sorted(missing_roles)!r}."
            )

        # ---- Queue membership: eligible_logical_count > 0 ----
        if self.queue_membership_policy.eligible_logical_count <= 0:
            raise ValueError(
                f"queue_membership_policy.eligible_logical_count must be > 0; "
                f"got {self.queue_membership_policy.eligible_logical_count}."
            )

        # ---- Risk level consistency ----
        expected_risk_level = replay_risk_level_from_score(
            self.modeled_mission_state.risk_score
        )
        if self.modeled_mission_state.risk_level != expected_risk_level:
            raise ValueError(
                f"modeled_mission_state.risk_level {self.modeled_mission_state.risk_level!r} "
                f"!= replay_risk_level_from_score({self.modeled_mission_state.risk_score}) "
                f"= {expected_risk_level!r}. "
                "Cross-field risk level consistency violated."
            )

        # ---- descriptor_id recomputation and verification ----
        expected_id = compute_descriptor_id(self)
        if self.descriptor_id != expected_id:
            raise ValueError(
                f"descriptor_id mismatch: stored {self.descriptor_id!r} "
                f"!= computed {expected_id!r}."
            )
        return self


# ---------------------------------------------------------------------------
# descriptor_id computation
# ---------------------------------------------------------------------------


def compute_descriptor_id(descriptor: "HistoricalReplayV2Descriptor") -> str:
    """Compute the deterministic descriptor_id.

    Hashes all semantic content excluding descriptor_id itself.
    """
    # Build canonical dict from all fields except descriptor_id
    canonical = {
        "decision_epoch_policy": descriptor.decision_epoch_policy,
        "decision_epoch_utc": descriptor.decision_epoch_utc,
        "modeled_link_inputs": descriptor.modeled_link_inputs.model_dump(mode="json"),
        "modeled_mission_state": descriptor.modeled_mission_state.model_dump(mode="json"),
        "product_policy": descriptor.product_policy.model_dump(mode="json"),
        "product_policy_id": descriptor.product_policy_id,
        "queue_membership_policy": descriptor.queue_membership_policy.model_dump(mode="json"),
        "replay_id": descriptor.replay_id,
        "schema": descriptor.descriptor_schema,
        "schema_version": descriptor.schema_version,
        "simulated": descriptor.simulated,
        "size_policy": descriptor.size_policy.model_dump(mode="json"),
        "size_policy_id": descriptor.size_policy_id,
        "source_bundle_id": descriptor.source_bundle_id,
        "source_bundle_ref": descriptor.source_bundle_ref,
    }
    payload = _DESCRIPTOR_V2_ID_PREFIX + json.dumps(
        canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_descriptor_id_from_dict(d: dict) -> str:
    """Compute descriptor_id from a raw dict (for loader use before model parse)."""
    # Build a temporary descriptor minus descriptor_id for hashing
    canonical_keys = {
        "decision_epoch_policy", "decision_epoch_utc", "modeled_link_inputs",
        "modeled_mission_state", "product_policy", "product_policy_id",
        "queue_membership_policy", "replay_id", "schema", "schema_version",
        "simulated", "size_policy", "size_policy_id", "source_bundle_id",
        "source_bundle_ref",
    }
    canonical = {k: v for k, v in d.items() if k in canonical_keys}
    # Ensure all canonical keys present
    for k in canonical_keys:
        if k not in canonical:
            canonical[k] = None
    payload = _DESCRIPTOR_V2_ID_PREFIX + json.dumps(
        canonical, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Production safe loader
# ---------------------------------------------------------------------------


def load_v2_replay_descriptor(
    path: "Path | str",
) -> HistoricalReplayV2Descriptor:
    """Load and strictly validate a V2 historical replay descriptor.

    Enforces:
    - No path traversal (..)
    - Path must resolve inside data/replays/
    - Not a symlink
    - Regular JSON file
    - Bounded read (64 KiB)
    - Strict typed model parse
    - descriptor_id recomputed and verified

    Does NOT load source data or make network requests.

    Raises
    ------
    MissionSourceUnavailableError
        If the file does not exist or cannot be read.
    MissionSourceValidationError
        If any integrity check fails.
    """
    from pydantic import ValidationError as PydanticValidationError

    path = Path(path)

    # Traversal check
    if any(part == ".." for part in path.parts):
        raise MissionSourceValidationError(
            f"V2 descriptor path contains traversal: {path!r}."
        )

    resolved = path.resolve()
    allowed_dir = _ALLOWED_DIR.resolve()

    try:
        resolved.relative_to(allowed_dir)
    except ValueError as exc:
        raise MissionSourceValidationError(
            f"V2 descriptor path {path!r} resolves outside allowed directory."
        ) from exc

    if path.is_symlink() or resolved.is_symlink():
        raise MissionSourceValidationError(
            f"V2 descriptor path must not be a symlink: {path!r}."
        )

    if not resolved.is_file():
        raise MissionSourceUnavailableError(
            f"V2 descriptor file not found: {resolved!r}."
        )

    size = resolved.stat().st_size
    if size > _MAX_DESCRIPTOR_V2_BYTES:
        raise MissionSourceValidationError(
            f"V2 descriptor file exceeds maximum size ({_MAX_DESCRIPTOR_V2_BYTES} bytes)."
        )

    raw = resolved.read_text(encoding="utf-8")
    if len(raw.encode("utf-8")) > _MAX_DESCRIPTOR_V2_BYTES:
        raise MissionSourceValidationError(
            "V2 descriptor content exceeds maximum size."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MissionSourceValidationError(
            "V2 descriptor is not valid JSON."
        ) from exc

    if not isinstance(data, dict):
        raise MissionSourceValidationError("V2 descriptor JSON top level is not an object.")

    # Schema pre-check
    schema_val = data.get("schema")
    if schema_val != DESCRIPTOR_V2_SCHEMA:
        raise MissionSourceValidationError(
            f"V2 descriptor has wrong schema {schema_val!r}; expected {DESCRIPTOR_V2_SCHEMA!r}."
        )
    version_val = data.get("schema_version")
    if version_val != DESCRIPTOR_V2_VERSION:
        raise MissionSourceValidationError(
            f"V2 descriptor has wrong schema_version {version_val!r}; expected {DESCRIPTOR_V2_VERSION}."
        )

    # Strict typed parse (which also recomputes and verifies descriptor_id in the model validator)
    try:
        descriptor = HistoricalReplayV2Descriptor.model_validate(data, strict=False)
    except PydanticValidationError as exc:
        raise MissionSourceValidationError(
            f"V2 descriptor failed schema validation: {exc}"
        ) from exc

    return descriptor
