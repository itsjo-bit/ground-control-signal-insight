"""GCSI Phase 6D-A — JPL Horizons Geometry Domain Models.

These strict Pydantic models represent the domain objects produced by and
consumed by the :class:`~backend.app.mission_sources.adapters.horizons.HorizonsAdapter`.

They are completely independent of:
- GCSI runtime state (state.py)
- Scenario / ScenarioLoader
- TelecomEngine, RF, BER, SNR, link margin
- API routes
- Frontend

Models
------
HorizonsGeometryRequest
    Input contract for one geometry fetch.  Accepts a numeric SPK ID and a
    single timezone-aware UTC epoch.

HorizonsGeometry
    Normalized external fact object produced from a validated Horizons
    response.  Exposes:
        range_km
        range_rate_km_s
        one_way_light_time_s
    along with request metadata and API signature for traceability.

HorizonsGeometryResult
    Container binding request + geometry + provenance.

HorizonsGeometryCapture
    Immutable capture object bundling a validated HorizonsGeometryResult
    with the exact raw HTTP response bytes.  Used as the raw-capture
    contract for snapshot creation (Phase 6D-B1).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from math import isfinite, isnan
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.provenance.models import ProvenanceRecord


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Accepted Earth geocenter Horizons CENTER code.
_EARTH_CENTER: str = "500@399"

# Fixed API source string required by Phase 6D-A.
_HORIZONS_API_SOURCE: str = "NASA/JPL Horizons API"

# Explicit allow-list of accepted API versions, mirroring the adapter policy.
# Must be kept in sync with _SUPPORTED_SIGNATURE_VERSIONS in horizons.py.
_HORIZONS_API_VERSIONS: frozenset[str] = frozenset({"1.2", "1.3"})

# SPK numeric-only pattern (optional leading minus, then digits only).
_SPK_RE = re.compile(r"^-?\d+$")


# ---------------------------------------------------------------------------
# A. HorizonsGeometryRequest
# ---------------------------------------------------------------------------


class HorizonsGeometryRequest(BaseModel):
    """Input contract for one JPL Horizons geometry fetch.

    Fields
    ------
    target_spk_id
        Numeric-only Horizons/SPK major-body identifier.
        Examples: ``"-61"`` (Juno), ``"499"`` (Mars).
        Rejects names, semicolons, whitespace, and Horizons command syntax.

    epoch_utc
        Single desired geometry epoch.  Must be timezone-aware.
        Non-UTC aware datetimes are normalized to UTC at validation time.

    Constraints
    -----------
    - ``target_spk_id`` must match ``^-?\\d+$``.
    - ``epoch_utc`` must be timezone-aware (``tzinfo is not None``).
    - Extra fields are forbidden.
    - The model is frozen after construction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_spk_id: str = Field(
        description=(
            "Numeric Horizons/SPK major-body identifier, e.g. '-61' for Juno. "
            "Must match ^-?\\d+$.  No names, semicolons, whitespace, or "
            "Horizons command syntax."
        )
    )
    epoch_utc: datetime = Field(
        description=(
            "Single geometry epoch.  Must be timezone-aware. "
            "Non-UTC aware datetimes are normalized to UTC."
        )
    )

    @field_validator("target_spk_id", mode="after")
    @classmethod
    def _validate_target_spk_id(cls, v: str) -> str:
        """Reject anything that is not a bare numeric SPK identifier."""
        if not _SPK_RE.match(v):
            raise ValueError(
                "target_spk_id must be a numeric SPK identifier matching ^-?\\d+$ "
                "(e.g. '-61', '499'). Names, semicolons, whitespace, and "
                "Horizons command syntax are rejected."
            )
        return v

    @field_validator("epoch_utc", mode="after")
    @classmethod
    def _validate_and_normalize_epoch(cls, v: datetime) -> datetime:
        """Reject naive datetimes; normalize aware datetimes to UTC."""
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError(
                "epoch_utc must be timezone-aware. "
                "Use datetime(..., tzinfo=timezone.utc) or an offset-aware ISO string."
            )
        # Normalize to UTC.
        return v.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# B. HorizonsGeometry
# ---------------------------------------------------------------------------


class HorizonsGeometry(BaseModel):
    """Normalized geometry fact produced from a validated Horizons response.

    This is a JPL/Horizons external fact object.  It is NOT a
    ``ScenarioGeometry`` or any GCSI simulation construct.

    Fields
    ------
    target_spk_id
        Numeric SPK identifier of the queried body.

    center
        Horizons CENTER code used for this query (always ``'500@399'``).

    epoch_utc
        Requested geometry epoch in UTC.

    range_km
        Geometric range from target to Earth geocenter, km.  Must be > 0.

    range_rate_km_s
        Geometric range-rate (d(range)/dt), km/s.  May be positive, zero,
        or negative.

    one_way_light_time_s
        One-way light-time from target to Earth geocenter, seconds.  Must
        be > 0.

    api_source
        Verified ``signature.source`` from the Horizons response.

    api_version
        Verified ``signature.version`` from the Horizons response.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_spk_id: str = Field(description="Numeric SPK identifier.")
    center: str = Field(description="Horizons CENTER code.")
    epoch_utc: datetime = Field(description="Requested geometry epoch in UTC.")
    range_km: float = Field(description="Geometric range, km. Must be > 0.")
    range_rate_km_s: float = Field(description="Geometric range-rate, km/s.")
    one_way_light_time_s: float = Field(
        description="One-way light-time, seconds. Must be > 0."
    )
    api_source: str = Field(description="Verified Horizons signature.source.")
    api_version: str = Field(description="Verified Horizons signature.version.")

    @field_validator("epoch_utc", mode="after")
    @classmethod
    def _epoch_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("epoch_utc must be timezone-aware.")
        return v

    @field_validator("range_km", mode="after")
    @classmethod
    def _validate_range_km(cls, v: float) -> float:
        if isnan(v) or not isfinite(v):
            raise ValueError("range_km must be finite (no NaN/infinity).")
        if v <= 0.0:
            raise ValueError("range_km must be > 0.")
        return v

    @field_validator("range_rate_km_s", mode="after")
    @classmethod
    def _validate_range_rate(cls, v: float) -> float:
        if isnan(v) or not isfinite(v):
            raise ValueError("range_rate_km_s must be finite (no NaN/infinity).")
        return v

    @field_validator("one_way_light_time_s", mode="after")
    @classmethod
    def _validate_light_time(cls, v: float) -> float:
        if isnan(v) or not isfinite(v):
            raise ValueError("one_way_light_time_s must be finite (no NaN/infinity).")
        if v <= 0.0:
            raise ValueError("one_way_light_time_s must be > 0.")
        return v

    @field_validator("api_source", mode="after")
    @classmethod
    def _validate_api_source(cls, v: str) -> str:
        if v != _HORIZONS_API_SOURCE:
            raise ValueError(
                f"api_source must be {_HORIZONS_API_SOURCE!r}; got {v!r}."
            )
        return v

    @field_validator("api_version", mode="after")
    @classmethod
    def _validate_api_version(cls, v: str) -> str:
        if v not in _HORIZONS_API_VERSIONS:
            raise ValueError(
                f"api_version must be one of {sorted(_HORIZONS_API_VERSIONS)!r}; got {v!r}."
            )
        return v


# ---------------------------------------------------------------------------
# C. HorizonsGeometryResult
# ---------------------------------------------------------------------------


class HorizonsGeometryResult(BaseModel):
    """Container binding a validated geometry result with its provenance.

    Fields
    ------
    request
        The original :class:`HorizonsGeometryRequest` that produced this result.

    geometry
        The normalized :class:`HorizonsGeometry` fact.

    provenance
        A Phase 6B :class:`~backend.app.provenance.models.ProvenanceRecord`
        with ``kind=EXTERNAL_AUTHORITATIVE`` and
        ``validation_status=VALIDATED``.

    Notes
    -----
    - There is no canonical Scenario entity to bind these facts to until a
      future ``ReplayAssembler`` exists.  Therefore no
      ``FieldProvenanceBinding`` is created here.
    - The model is frozen and forbids extra fields.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request: HorizonsGeometryRequest = Field(
        description="Original geometry request."
    )
    geometry: HorizonsGeometry = Field(
        description="Normalized Horizons geometry fact."
    )
    provenance: ProvenanceRecord = Field(
        description=(
            "EXTERNAL_AUTHORITATIVE provenance record for this geometry fact."
        )
    )


# ---------------------------------------------------------------------------
# D. HorizonsGeometryCapture  (Phase 6D-B1)
# ---------------------------------------------------------------------------


class HorizonsGeometryCapture(BaseModel):
    """Immutable raw capture: validated result + exact raw HTTP response bytes.

    This is the internal contract between the live fetch path and the snapshot
    writer.  It is produced by
    :meth:`~backend.app.mission_sources.adapters.horizons.HorizonsAdapter.fetch_capture`
    and consumed by the snapshot store.

    The raw bytes are preserved exactly as received from the network so that
    the snapshot layer can store and later re-verify them byte-for-byte.

    Fields
    ------
    result
        The fully validated :class:`HorizonsGeometryResult`.

    raw_response
        Exact raw HTTP response body bytes from JPL Horizons.
        Must not be modified or re-serialized before passing to the snapshot
        writer.

    Notes
    -----
    - ``fetch()`` remains backward-compatible and returns only ``result``.
    - ``fetch_capture()`` performs exactly one HTTP request.
    - The model is frozen and forbids extra fields.
    - ``raw_response`` is bytes; Pydantic serializes it as base64 but the
      snapshot store handles encoding explicitly.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    result: HorizonsGeometryResult = Field(
        description="Validated Horizons geometry result."
    )
    raw_response: bytes = Field(
        description="Exact raw HTTP response body bytes from JPL Horizons."
    )
