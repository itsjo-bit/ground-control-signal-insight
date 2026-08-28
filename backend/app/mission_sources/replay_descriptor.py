"""GCSI Phase 6E-C4B — Historical Replay Descriptor: model, validation, loader.

This module provides:

1. Strict versioned Pydantic models for the historical replay descriptor:
   - ReplayLinkPolicyV1
   - ReplayMissionPolicyV1
   - ReplayDataProductPolicyV1
   - HistoricalReplayDescriptorV1

2. Path security helpers:
   - _validate_snapshot_path()  — reject unsafe paths before model construction

3. A bounded zero-network local descriptor loader:
   - load_historical_replay_descriptor(source_ref) -> HistoricalReplayDescriptorV1

4. A pure replay risk-level derivation helper:
   - replay_risk_level_from_score(score) -> str

Design invariants
-----------------
- All models use extra="forbid" and frozen=True.
- Descriptor v1 has Literal-typed top-level identity fields so the
  schema/version/simulated assumptions are visible and cannot be overridden.
- snapshot paths are validated as relative, traversal-safe, local-only
  before the Pydantic model is ever constructed.
- The loader is bounded to MAX_DESCRIPTOR_BYTES (64 KiB) and makes zero
  network requests.
- Risk-level derivation uses the frozen gcsi_risk_thresholds_v1 rule.

Provenance method identifiers frozen in C4B
-------------------------------------------
historical_replay_decision_epoch_from_mwr_stop_v1
historical_replay_distance_from_exact_horizons_range_v1
historical_replay_product_size_bits_from_pds_file_size_v1
historical_replay_product_id_from_mwr_role_v1
historical_replay_mission_id_from_juno_context_v1
historical_replay_product_age_from_decision_epoch_v1
historical_replay_risk_level_from_policy_score_v1
historical_replay_product_relationship_from_mwr_pair_v1

NOT implemented in this module
-------------------------------
- HistoricalReplayProvider
- ReplayAssembler
- Runtime integration / API / frontend
- Snapshot loading (that belongs to HorizonsSnapshotStore / PdsArchiveSnapshotStore)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import MissionSourceUnavailableError, MissionSourceValidationError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Maximum allowed descriptor file size (64 KiB).
MAX_DESCRIPTOR_BYTES: int = 64 * 1024

#: Descriptor schema name.
DESCRIPTOR_SCHEMA: str = "gcsi.historical_replay_descriptor"

#: Supported descriptor version.
DESCRIPTOR_VERSION: int = 1

#: Risk-level policy identifier frozen in C4B.
RISK_LEVEL_POLICY_V1: str = "gcsi_risk_thresholds_v1"

# Required path prefixes per snapshot role.
_HORIZONS_PREFIX = "data/verified_snapshots/horizons/"
_PDS_PREFIX = "data/verified_snapshots/pds_archive/"


# ---------------------------------------------------------------------------
# Risk-level derivation — gcsi_risk_thresholds_v1
# ---------------------------------------------------------------------------


def replay_risk_level_from_score(risk_score: float) -> str:
    """Derive the GCSI risk level string from a modeled risk score.

    Frozen threshold policy: ``gcsi_risk_thresholds_v1``.

    Thresholds::

        score < 0.25  → "LOW"
        score < 0.50  → "MEDIUM"
        score < 0.75  → "HIGH"
        else          → "CRITICAL"

    Args:
        risk_score: Modeled risk score in [0, 1].

    Returns:
        Risk level string: "LOW", "MEDIUM", "HIGH", or "CRITICAL".

    Raises:
        ValueError: if risk_score is outside [0, 1] or not finite.
    """
    if not math.isfinite(risk_score):
        raise ValueError(f"risk_score must be finite; got {risk_score!r}")
    if risk_score < 0.0 or risk_score > 1.0:
        raise ValueError(
            f"risk_score must be in [0, 1]; got {risk_score!r}"
        )
    if risk_score < 0.25:
        return "LOW"
    if risk_score < 0.50:
        return "MEDIUM"
    if risk_score < 0.75:
        return "HIGH"
    return "CRITICAL"


# ---------------------------------------------------------------------------
# Path security
# ---------------------------------------------------------------------------


def _validate_snapshot_path(path_str: str, role: str) -> str:
    """Validate a descriptor snapshot path for safety and expected prefix.

    Rejects:
    - Empty string
    - Absolute POSIX paths (start with /)
    - Drive-letter paths (C:, D:, etc.)
    - Any string containing ``\\`` (backslash)
    - URL schemes (http://, https://, //, etc.)
    - ``..`` path traversal components
    - ``?`` (query string)
    - ``#`` (fragment)
    - ``%`` (percent-encoding)
    - NUL byte
    - ``:`` anywhere (covers drive letters and URL schemes)

    Also requires the path to start with the expected prefix for the role:
    - Horizons paths must start with ``data/verified_snapshots/horizons/``
    - IRDR/GRDR paths must start with ``data/verified_snapshots/pds_archive/``

    Args:
        path_str: The candidate path string from the descriptor JSON.
        role:     One of ``"horizons"``, ``"irdr"``, ``"grdr"`` — used to
                  select the required prefix and produce clear error messages.

    Returns:
        The validated path string, unchanged.

    Raises:
        MissionSourceValidationError: if the path is unsafe or has the wrong
            prefix.
    """
    if not path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' must not be empty."
        )

    # Reject NUL bytes.
    if "\x00" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a NUL byte."
        )

    # Reject backslash (Windows-style path separator or UNC prefix).
    if "\\" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a backslash."
        )

    # Reject colon (covers drive letters like C: and URL schemes like http:).
    if ":" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a colon "
            "(drive-letter or URL scheme not allowed)."
        )

    # Reject percent-encoding.
    if "%" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains percent-encoding."
        )

    # Reject query strings.
    if "?" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a query string."
        )

    # Reject fragment identifiers.
    if "#" in path_str:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a fragment."
        )

    # Reject scheme-relative URLs (//...).
    if path_str.startswith("//"):
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' is a scheme-relative URL."
        )

    # Reject absolute POSIX paths.
    if path_str.startswith("/"):
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' is an absolute path."
        )

    # Reject traversal components.
    # Split on forward slash and check each component.
    parts = path_str.split("/")
    if ".." in parts:
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' contains a '..' traversal "
            "component."
        )

    # Require expected prefix for this role.
    if role == "horizons":
        required_prefix = _HORIZONS_PREFIX
    else:
        # irdr, grdr
        required_prefix = _PDS_PREFIX

    if not path_str.startswith(required_prefix):
        raise MissionSourceValidationError(
            f"Descriptor snapshot path for '{role}' must start with "
            f"'{required_prefix}'; got path starting with a different prefix."
        )

    return path_str


# ---------------------------------------------------------------------------
# ReplayLinkPolicyV1
# ---------------------------------------------------------------------------


class ReplayLinkPolicyV1(BaseModel):
    """Modeled link policy for a historical replay descriptor (version 1).

    All values are MODELED and attributed to GCSI-historical-replay-policy.
    They are NOT NASA/JPL/PDS measurements.

    ``latency_s`` is protocol-stack overhead.  It is NOT propagation delay.
    Propagation delay is derived from Horizons range_km and stored in
    Scenario.distance_km.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snr_db: float = Field(
        description=(
            "MODELED signal-to-noise ratio in dB. "
            "GCSI replay assumption — not derived from distance, RSSI, Horizons, PDS, or MWR."
        )
    )
    rssi_dbm: float = Field(
        description=(
            "MODELED received signal strength indicator in dBm. "
            "Context-only modeled link value."
        )
    )
    nominal_data_rate_bps: float = Field(
        description=(
            "MODELED nominal channel data rate in bits/s. "
            "Chosen to align with GCSI baseline telecom modeling scale. "
            "NOT an actual historical Juno downlink-rate claim."
        )
    )
    latency_s: float = Field(
        description=(
            "MODELED protocol-stack overhead latency in seconds. "
            "Explicitly NOT free-space propagation delay. "
            "Propagation delay is Scenario.distance_km derived from Horizons range_km."
        )
    )
    link_stability: float = Field(
        description=(
            "MODELED replay-quality link stability in [0, 1]."
        )
    )
    remaining_window_s: float = Field(
        description=(
            "MODELED communication decision window in seconds, "
            "beginning at the decision epoch."
        )
    )

    @field_validator("nominal_data_rate_bps", mode="after")
    @classmethod
    def _validate_data_rate(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("nominal_data_rate_bps must be finite.")
        if v <= 0.0:
            raise ValueError(
                f"nominal_data_rate_bps must be > 0; got {v}"
            )
        return v

    @field_validator("latency_s", mode="after")
    @classmethod
    def _validate_latency(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("latency_s must be finite.")
        if v < 0.0:
            raise ValueError(f"latency_s must be >= 0; got {v}")
        return v

    @field_validator("link_stability", mode="after")
    @classmethod
    def _validate_stability(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("link_stability must be finite.")
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"link_stability must be in [0, 1]; got {v}")
        return v

    @field_validator("remaining_window_s", mode="after")
    @classmethod
    def _validate_window(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("remaining_window_s must be finite.")
        if v <= 0.0:
            raise ValueError(f"remaining_window_s must be > 0; got {v}")
        return v

    @field_validator("snr_db", "rssi_dbm", mode="after")
    @classmethod
    def _validate_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Value must be finite.")
        return v


# ---------------------------------------------------------------------------
# ReplayMissionPolicyV1
# ---------------------------------------------------------------------------


class ReplayMissionPolicyV1(BaseModel):
    """Modeled mission policy for a historical replay descriptor (version 1).

    ``risk_score`` is MODELED.
    ``risk_level`` must be derived by the assembler using gcsi_risk_thresholds_v1.
    The descriptor stores only the modeled ``risk_score``; the assembler
    derives ``risk_level`` via :func:`replay_risk_level_from_score`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mission_phase: str = Field(
        description="MODELED mission phase identifier."
    )
    current_event: str = Field(
        description="MODELED current event description."
    )
    event_time_remaining_s: float = Field(
        description=(
            "MODELED time remaining until the event ends, in seconds. "
            "Must be >= 0."
        )
    )
    comm_window_remaining_s: float = Field(
        description=(
            "MODELED communication window remaining, in seconds. "
            "Must be > 0."
        )
    )
    risk_score: float = Field(
        description=(
            "MODELED risk score in [0, 1]. "
            "Derive risk_level via gcsi_risk_thresholds_v1."
        )
    )

    @field_validator("mission_phase", "current_event", mode="after")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("event_time_remaining_s", mode="after")
    @classmethod
    def _validate_event_time(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("event_time_remaining_s must be finite.")
        if v < 0.0:
            raise ValueError(
                f"event_time_remaining_s must be >= 0; got {v}"
            )
        return v

    @field_validator("comm_window_remaining_s", mode="after")
    @classmethod
    def _validate_comm_window(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("comm_window_remaining_s must be finite.")
        if v <= 0.0:
            raise ValueError(
                f"comm_window_remaining_s must be > 0; got {v}"
            )
        return v

    @field_validator("risk_score", mode="after")
    @classmethod
    def _validate_risk_score(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("risk_score must be finite.")
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"risk_score must be in [0, 1]; got {v}")
        return v


# ---------------------------------------------------------------------------
# ReplayDataProductPolicyV1
# ---------------------------------------------------------------------------


class ReplayDataProductPolicyV1(BaseModel):
    """Modeled data-product policy entry for a historical replay descriptor (version 1).

    Values are MODELED / DERIVED as documented per field.
    The descriptor stores modeled attributes; authoritative attributes
    (e.g. product size) are loaded from the verified snapshot at assembly time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_type: str = Field(
        description="MODELED product type identifier (e.g. 'science')."
    )
    criticality: float = Field(
        description="MODELED criticality score in [0, 1]."
    )
    mission_relevance: float = Field(
        description="MODELED mission relevance score in [0, 1]."
    )
    scientific_value: float = Field(
        description="MODELED scientific value score in [0, 1]."
    )
    deadline_s: float = Field(
        description=(
            "MODELED delivery deadline in seconds. "
            "Must be >= 0."
        )
    )
    delivery_requirement: str = Field(
        description="MODELED delivery requirement identifier (e.g. 'best_effort')."
    )
    retry_cost: float = Field(
        description="MODELED retry cost factor. Must be >= 0."
    )
    anomaly_id: Optional[str] = Field(
        default=None,
        description=(
            "Associated anomaly ID, or None. "
            "None means GCSI introduces no modeled anomaly event — "
            "NOT that NASA confirmed no anomalies."
        ),
    )

    @field_validator("product_type", "delivery_requirement", mode="after")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("criticality", "mission_relevance", "scientific_value", mode="after")
    @classmethod
    def _validate_unit_interval(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Value must be finite.")
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"Value must be in [0, 1]; got {v}")
        return v

    @field_validator("deadline_s", mode="after")
    @classmethod
    def _validate_deadline(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("deadline_s must be finite.")
        if v < 0.0:
            raise ValueError(f"deadline_s must be >= 0; got {v}")
        return v

    @field_validator("retry_cost", mode="after")
    @classmethod
    def _validate_retry_cost(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("retry_cost must be finite.")
        if v < 0.0:
            raise ValueError(f"retry_cost must be >= 0; got {v}")
        return v


# ---------------------------------------------------------------------------
# HistoricalReplayDescriptorV1
# ---------------------------------------------------------------------------


class HistoricalReplayDescriptorV1(BaseModel):
    """Strict versioned descriptor for a GCSI historical replay scenario (version 1).

    This model is the contract between the committed descriptor JSON file and
    the future HistoricalReplayProvider / ReplayAssembler.

    Design invariants
    -----------------
    - All identity fields are Literal-typed so the schema/version are fixed.
    - ``simulated`` is Literal[True]; historical replay is always simulated
      (deterministic reconstruction, not live telemetry).
    - extra="forbid": unknown fields cause a validation error.
    - frozen=True: model instances are immutable after construction.
    - Snapshot paths are pre-validated for safety before model construction.
    - Cross-field consistency (window coherence, deadline bounds, anomaly
      policy) is enforced by a model_validator.

    Snapshot paths
    --------------
    Paths reference local verified snapshot files.  They are validated as
    relative, traversal-safe, local-only strings by
    :func:`_validate_snapshot_path`.  The snapshot content is NOT loaded here;
    that belongs to HistoricalReplayProvider / ReplayAssembler.

    What is NOT stored in the descriptor
    -------------------------------------
    - NASA provenance records
    - Snapshot IDs / raw hashes
    - Authoritative geometry values (range_km, light-time, observation
      timestamps, retrieved_at)
    - MWR file sizes
    Those facts must be loaded from the verified snapshots at assembly time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # -- Identity (Literal-locked) ------------------------------------------

    descriptor_schema: Literal["gcsi.historical_replay_descriptor"] = Field(
        description=f"Schema identity. Must be '{DESCRIPTOR_SCHEMA}'."
    )
    descriptor_version: Literal[1] = Field(
        description=f"Schema version. Must be {DESCRIPTOR_VERSION}."
    )

    replay_id: str = Field(
        description="Deterministic replay identifier. Must be non-empty."
    )
    replay_policy_version: str = Field(
        description="Replay policy version string. Must be non-empty."
    )

    # -- Runtime policy (Literal-locked) ------------------------------------

    simulated: Literal[True] = Field(
        description=(
            "Historical replay is always simulated=True. "
            "This field is Literal[True] so the assumption is visible and "
            "cannot be overridden by descriptor authors."
        )
    )

    # -- Decision policy identifiers (Literal-locked for v1) ----------------

    decision_epoch_policy: Literal["mwr_observation_stop"] = Field(
        description="Decision epoch policy identifier."
    )
    geometry_alignment_policy: Literal["exact_epoch"] = Field(
        description="Geometry alignment policy identifier."
    )
    product_availability_policy: Literal["mwr_observation_stop"] = Field(
        description="Product availability policy identifier."
    )
    risk_level_policy: Literal["gcsi_risk_thresholds_v1"] = Field(
        description="Risk-level derivation policy identifier."
    )

    # -- Snapshot paths (validated before construction) ---------------------

    horizons_snapshot_path: str = Field(
        description=(
            "Relative local path to the verified Horizons snapshot. "
            f"Must start with '{_HORIZONS_PREFIX}'."
        )
    )
    irdr_snapshot_path: str = Field(
        description=(
            "Relative local path to the verified IRDR PDS archive snapshot. "
            f"Must start with '{_PDS_PREFIX}'."
        )
    )
    grdr_snapshot_path: str = Field(
        description=(
            "Relative local path to the verified GRDR PDS archive snapshot. "
            f"Must start with '{_PDS_PREFIX}'."
        )
    )

    # -- Sub-policies -------------------------------------------------------

    link_policy: ReplayLinkPolicyV1 = Field(
        description="MODELED link policy."
    )
    mission_policy: ReplayMissionPolicyV1 = Field(
        description="MODELED mission policy."
    )
    irdr_policy: ReplayDataProductPolicyV1 = Field(
        description="MODELED IRDR data-product policy."
    )
    grdr_policy: ReplayDataProductPolicyV1 = Field(
        description="MODELED GRDR data-product policy."
    )

    # -- Field validators ---------------------------------------------------

    @field_validator("replay_id", "replay_policy_version", mode="after")
    @classmethod
    def _validate_non_empty_str(cls, v: str) -> str:
        if not v:
            raise ValueError("Field must not be empty.")
        return v

    # -- Cross-field validators ---------------------------------------------

    @model_validator(mode="after")
    def _validate_cross_fields(self) -> "HistoricalReplayDescriptorV1":
        """Enforce descriptor v1 cross-field consistency rules."""
        lp = self.link_policy
        mp = self.mission_policy
        irdr = self.irdr_policy
        grdr = self.grdr_policy

        # Window coherence: all three window values must match exactly.
        if not (
            lp.remaining_window_s
            == mp.comm_window_remaining_s
            == mp.event_time_remaining_s
        ):
            raise ValueError(
                "Window mismatch: link_policy.remaining_window_s, "
                "mission_policy.comm_window_remaining_s, and "
                "mission_policy.event_time_remaining_s must be equal. "
                f"Got: {lp.remaining_window_s}, "
                f"{mp.comm_window_remaining_s}, "
                f"{mp.event_time_remaining_s}."
            )

        # IRDR deadline must not exceed the comm window.
        if irdr.deadline_s > lp.remaining_window_s:
            raise ValueError(
                f"irdr_policy.deadline_s ({irdr.deadline_s}) must be <= "
                f"link_policy.remaining_window_s ({lp.remaining_window_s})."
            )

        # GRDR deadline must not exceed the comm window.
        if grdr.deadline_s > lp.remaining_window_s:
            raise ValueError(
                f"grdr_policy.deadline_s ({grdr.deadline_s}) must be <= "
                f"link_policy.remaining_window_s ({lp.remaining_window_s})."
            )

        # No-anomaly policy: anomaly_id must be None for this replay.
        if irdr.anomaly_id is not None:
            raise ValueError(
                f"irdr_policy.anomaly_id must be None for this replay; "
                f"got {irdr.anomaly_id!r}."
            )
        if grdr.anomaly_id is not None:
            raise ValueError(
                f"grdr_policy.anomaly_id must be None for this replay; "
                f"got {grdr.anomaly_id!r}."
            )

        # Snapshot path distinctness: IRDR != GRDR.
        if self.irdr_snapshot_path == self.grdr_snapshot_path:
            raise ValueError(
                "irdr_snapshot_path and grdr_snapshot_path must be distinct."
            )

        # Horizons path must not be a PDS path.
        if self.horizons_snapshot_path == self.irdr_snapshot_path:
            raise ValueError(
                "horizons_snapshot_path must not equal irdr_snapshot_path."
            )
        if self.horizons_snapshot_path == self.grdr_snapshot_path:
            raise ValueError(
                "horizons_snapshot_path must not equal grdr_snapshot_path."
            )

        # Horizons path must not start with the PDS prefix.
        if self.horizons_snapshot_path.startswith(_PDS_PREFIX):
            raise ValueError(
                "horizons_snapshot_path must not use the PDS archive prefix."
            )

        # IRDR/GRDR paths must not start with the Horizons prefix.
        if self.irdr_snapshot_path.startswith(_HORIZONS_PREFIX):
            raise ValueError(
                "irdr_snapshot_path must not use the Horizons prefix."
            )
        if self.grdr_snapshot_path.startswith(_HORIZONS_PREFIX):
            raise ValueError(
                "grdr_snapshot_path must not use the Horizons prefix."
            )

        return self


# ---------------------------------------------------------------------------
# Bounded zero-network descriptor loader
# ---------------------------------------------------------------------------


def load_historical_replay_descriptor(
    source_ref: Union[str, Path],
) -> HistoricalReplayDescriptorV1:
    """Load and strictly validate a historical replay descriptor from a local file.

    Load sequence
    -------------
    ::

        untrusted local source_ref
            ↓  resolve Path (no shell expansion, no subprocess)
            ↓  bounded read: at most MAX_DESCRIPTOR_BYTES + 1 bytes
            ↓  size check: reject if > MAX_DESCRIPTOR_BYTES
            ↓  UTF-8 strict decode
            ↓  JSON parse
            ↓  top-level object check
            ↓  schema / version pre-check (clean error messages)
            ↓  path security validation (per role)
            ↓  strict HistoricalReplayDescriptorV1 Pydantic validation
        VALIDATED DESCRIPTOR RETURNED

    No HTTP, no URL fetch, no shell, no glob, no environment expansion,
    no eval, no exec, no dynamic import.

    Snapshots are NOT loaded here.  That responsibility belongs to
    HistoricalReplayProvider / ReplayAssembler.

    Parameters
    ----------
    source_ref:
        Local file path to the descriptor JSON.  Treated as untrusted input.

    Returns
    -------
    HistoricalReplayDescriptorV1
        Validated descriptor instance.

    Raises
    ------
    MissionSourceUnavailableError
        If the descriptor file is missing or cannot be read.

    MissionSourceValidationError
        If the descriptor is oversized, malformed, invalid UTF-8, contains
        malformed JSON, fails schema/version checks, contains unsafe paths,
        or fails Pydantic validation.
    """
    from pydantic import ValidationError as PydanticValidationError

    # Resolve to Path — no shell expansion.
    try:
        path = Path(source_ref)
    except Exception as exc:
        raise MissionSourceValidationError(
            "Descriptor source reference is not a valid path."
        ) from exc

    # Bounded read: at most MAX_DESCRIPTOR_BYTES + 1 bytes.
    try:
        with open(path, "rb") as fh:
            raw_bytes = fh.read(MAX_DESCRIPTOR_BYTES + 1)
    except FileNotFoundError as exc:
        raise MissionSourceUnavailableError(
            "Replay descriptor is not available."
        ) from exc
    except IsADirectoryError as exc:
        raise MissionSourceUnavailableError(
            "Replay descriptor path points to a directory, not a file."
        ) from exc
    except OSError as exc:
        raise MissionSourceUnavailableError(
            "Replay descriptor could not be read."
        ) from exc

    # Size check.
    if len(raw_bytes) > MAX_DESCRIPTOR_BYTES:
        raise MissionSourceValidationError(
            f"Descriptor file exceeds maximum allowed size "
            f"({MAX_DESCRIPTOR_BYTES} bytes)."
        )

    # UTF-8 strict decode.
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise MissionSourceValidationError(
            "Descriptor file is not valid UTF-8."
        ) from exc

    # JSON parse.
    try:
        raw_obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MissionSourceValidationError(
            "Descriptor file contains malformed JSON."
        ) from exc

    # Top-level object check.
    if not isinstance(raw_obj, dict):
        raise MissionSourceValidationError(
            "Descriptor JSON top level is not an object."
        )

    # Schema / version pre-check for clear error messages.
    schema_val = raw_obj.get("descriptor_schema")
    if schema_val != DESCRIPTOR_SCHEMA:
        raise MissionSourceValidationError(
            f"Descriptor has wrong schema; expected {DESCRIPTOR_SCHEMA!r}, "
            f"got {schema_val!r}."
        )
    version_val = raw_obj.get("descriptor_version")
    if version_val != DESCRIPTOR_VERSION:
        raise MissionSourceValidationError(
            f"Descriptor has unsupported version; expected {DESCRIPTOR_VERSION}, "
            f"got {version_val!r}."
        )

    # Path security validation — before Pydantic, so errors are typed correctly.
    for role, key in (
        ("horizons", "horizons_snapshot_path"),
        ("irdr", "irdr_snapshot_path"),
        ("grdr", "grdr_snapshot_path"),
    ):
        candidate = raw_obj.get(key)
        if not isinstance(candidate, str):
            raise MissionSourceValidationError(
                f"Descriptor field '{key}' is missing or not a string."
            )
        _validate_snapshot_path(candidate, role)

    # Strict Pydantic validation.
    try:
        descriptor = HistoricalReplayDescriptorV1.model_validate(raw_obj)
    except PydanticValidationError as exc:
        raise MissionSourceValidationError(
            "Descriptor failed schema validation."
        ) from exc

    return descriptor
