"""GCSI Phase 6F-B2.1.2 — Historical Replay V2 Acquisition Plan Model.

Supersedes B2.1.1 with acquisition evidence chain closure:

B2.1.2 additions:
- HistoricalReplayV2AcquisitionPlan now carries discovery_evidence_artifact_id
  (the SHA-256 of the discovery sidecar). plan_id canonical content includes it.
- DiscoveryEvidence.capture() no longer accepts caller-supplied byte_count;
  byte_count is always len(response_bytes).
- load_acquisition_plan() enforces repository confinement: the resolved path
  must be inside data/replays/, must be a .json regular file, and must not
  escape via symlink.

This module defines the additive strict model that freezes the deterministic
mapping from 411 logical replay products to exact authoritative source
representations (label URLs + production profiles) for Juno PJ62.

All models: frozen=True, extra="forbid", strict where Pydantic allows.
All datetime fields are timezone-aware UTC.

Temporal evidence contract:
  FINAL_TEMPORAL_ELIGIBILITY = LABEL_VERIFICATION_REQUIRED
  EXACT instruments: JunoCam (JNOJNC_0029 INDEX.TAB), WAVES Burst (BSTFULL INDEX.TAB)
  PENDING instruments: JIRAM, MWR, UVS, FGM, JADE, JEDI, WAVES Survey
    => discovery_availability_time_utc = None for all pending entries.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Replay accumulation start (frozen).
ACCUMULATION_START_UTC: datetime = datetime(2024, 6, 13, 10, 0, 0, tzinfo=timezone.utc)

#: Decision epoch (frozen): end of JIRAM PJ62 diagnostic session.
DECISION_EPOCH_UTC: datetime = datetime(
    2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc
)

#: Decision epoch policy identifier.
DECISION_EPOCH_POLICY: str = "END_OF_JIRAM_PJ62_DIAGNOSTIC_SESSION"

#: Maximum serialized plan size: 32 MiB.
_MAX_PLAN_BYTES: int = 32 * 1024 * 1024

#: Plan-ID hash prefix.
_PLAN_ID_PREFIX: str = "gcsi.v2_acquisition_plan:v1:"


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AcquisitionSourceStandard(str, Enum):
    """Archive metadata standard for a planned source representation."""

    PDS3 = "pds3"
    PDS4 = "pds4"


class TemporalEvidenceStatus(str, Enum):
    """Temporal evidence classification for a logical acquisition entry.

    EXACT_DISCOVERY_METADATA:
        The exact per-product stop time exists in the captured discovery
        source (e.g. JunoCam INDEX.TAB, WAVES Burst INDEX.TAB).
        discovery_availability_time_utc MUST be set and satisfy the window.

    LABEL_VERIFICATION_PENDING:
        The discovery source establishes identity/URL but does NOT provide
        authoritative per-product observation_stop.  B2.2 MUST treat label
        verification as an inclusion gate.
        discovery_availability_time_utc MUST be None.
    """

    EXACT_DISCOVERY_METADATA = "EXACT_DISCOVERY_METADATA"
    LABEL_VERIFICATION_PENDING = "LABEL_VERIFICATION_PENDING"


class AcquisitionRepresentationRole(str, Enum):
    """Role of a source representation within a logical product."""

    CALIBRATED = "calibrated"
    EDR = "edr"
    RDR = "rdr"
    SURVEY_B = "survey_b"
    SURVEY_E = "survey_e"
    BURST_B_BIN = "burst_b_bin"
    BURST_E_BIN = "burst_e_bin"
    BURST_B_REC = "burst_b_rec"
    BURST_E_REC = "burst_e_rec"
    BURST_NBS_REC = "burst_nbs_rec"
    FULL_RESOLUTION = "full_resolution"


# ---------------------------------------------------------------------------
# A. DiscoveryEvidence
# ---------------------------------------------------------------------------


#: Known placeholder SHA-256 patterns rejected by DiscoveryEvidence.
#: These are all-single-character strings (length 64), all-zero,
#: and known sentinel patterns from prior B2.1 stub implementation.
_PLACEHOLDER_SHA_PATTERNS: frozenset[str] = frozenset(
    c * 64 for c in "0123456789abcdef"
)


class DiscoveryEvidence(BaseModel):
    """Discovery evidence record for enumeration provenance.

    Discovery evidence is used for ENUMERATION only.
    It is NOT automatically the final authority for product facts.
    B2.2 must verify each product label independently.

    Fields
    ------
    evidence_id : str
        Stable human-readable identifier for this evidence source.

    source_url : str
        Official archive URL of the metadata resource fetched.

    retrieved_at : datetime
        Timezone-aware UTC timestamp of the actual fetch.
        Must not be a fabricated constant such as 2025-07-18T00:00:00Z
        unless an actual stored source artifact proves that exact retrieval.

    response_sha256 : str
        SHA-256 of the exact response bytes (64 lowercase hex).
        Must not be a known placeholder pattern (all-one-char, all-zero).

    source_kind : str
        Kind of discovery resource, e.g. 'pds4_directory_html',
        'pds3_index_tab', 'pds3_index_lbl', 'pds4_xml_label'.

    relevant_row_count : int | None
        Number of index rows or product entries relevant to this plan,
        if the source is tabular or enumerable.

    byte_count : int | None
        Exact byte length of the fetched response body.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    evidence_id: str = Field(description="Stable identifier for this evidence source.")
    source_url: str = Field(description="Official archive URL fetched.")
    retrieved_at: datetime = Field(description="Timezone-aware UTC actual fetch timestamp.")
    response_sha256: str = Field(description="SHA-256 of exact response bytes (non-placeholder).")
    source_kind: str = Field(
        description=(
            "Kind of discovery resource: "
            "'pds4_directory_html', 'pds3_index_tab', 'pds3_index_lbl', "
            "'pds4_xml_label', 'pds3_label_file'."
        )
    )
    relevant_row_count: Optional[int] = Field(
        default=None,
        description="Row/entry count relevant to this plan, if applicable.",
    )
    byte_count: Optional[int] = Field(
        default=None,
        description="Exact byte length of the fetched response body.",
    )

    @field_validator("evidence_id", "source_url", "source_kind", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("retrieved_at", mode="after")
    @classmethod
    def _aware_dt(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware.")
        return v.astimezone(timezone.utc)

    @field_validator("response_sha256", mode="after")
    @classmethod
    def _sha256_format(cls, v: str) -> str:
        import re
        if not re.fullmatch(r"[0-9a-f]{64}", v):
            raise ValueError(
                "response_sha256 must be exactly 64 lowercase hex characters."
            )
        if v in _PLACEHOLDER_SHA_PATTERNS:
            raise ValueError(
                f"response_sha256 {v[:8]}... is a known placeholder pattern "
                "(all-one-character or all-zero SHA). "
                "Use DiscoveryEvidence.capture() with actual response bytes."
            )
        return v

    @classmethod
    def capture(
        cls,
        *,
        evidence_id: str,
        source_url: str,
        retrieved_at: datetime,
        response_bytes: bytes,
        source_kind: str,
        relevant_row_count: Optional[int] = None,
    ) -> "DiscoveryEvidence":
        """Production factory: computes response_sha256 and byte_count from actual bytes.

        The byte_count parameter has been removed (B2.1.2 hardening).
        byte_count is ALWAYS len(response_bytes); no caller override is permitted.
        This eliminates the possibility of a byte_count that disagrees with
        the actual response body length.

        Parameters
        ----------
        evidence_id : str
            Stable human-readable identifier for this evidence source.
        source_url : str
            Official archive URL of the metadata resource fetched.
        retrieved_at : datetime
            Timezone-aware UTC timestamp of the actual fetch.
        response_bytes : bytes
            Exact response body bytes (used for SHA-256 and byte_count).
        source_kind : str
            Kind of discovery resource.
        relevant_row_count : int | None
            Number of index rows or product entries relevant to this plan.
        """
        sha256 = hashlib.sha256(response_bytes).hexdigest()
        return cls(
            evidence_id=evidence_id,
            source_url=source_url,
            retrieved_at=retrieved_at,
            response_sha256=sha256,
            source_kind=source_kind,
            relevant_row_count=relevant_row_count,
            byte_count=len(response_bytes),
        )


# ---------------------------------------------------------------------------
# B. AcquisitionSourceRepresentation
# ---------------------------------------------------------------------------


class AcquisitionSourceRepresentation(BaseModel):
    """One planned source representation for a logical product.

    Fields
    ------
    representation_role : AcquisitionRepresentationRole
        Semantic role of this representation.

    source_standard : AcquisitionSourceStandard
        Archive metadata standard (PDS3 or PDS4).

    label_url : str
        Exact authoritative label URL planned for B2.2 acquisition.
        Must be an HTTPS URL on a trusted archive host.

    normalizer_id : str
        Stable GCSI normalizer identifier that will parse this label.

    profile_id : str
        Stable GCSI profile identifier that constrains the normalizer.

    expected_archive_identity : str | None
        Expected archive-native identity for cross-verification,
        where discovery metadata supplies it.
        PDS4: LIDVID.
        PDS3: product_id (or DATA_SET_ID:PRODUCT_ID).
        None when not derivable from discovery metadata alone.

    discovery_evidence_id : str | None
        Reference to the DiscoveryEvidence.evidence_id that established
        this representation's existence and label URL.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    representation_role: AcquisitionRepresentationRole = Field(
        description="Semantic role of this representation."
    )
    source_standard: AcquisitionSourceStandard = Field(
        description="Archive metadata standard (PDS3 or PDS4)."
    )
    label_url: str = Field(
        description="Exact HTTPS label URL planned for B2.2 acquisition."
    )
    normalizer_id: str = Field(description="GCSI normalizer identifier.")
    profile_id: str = Field(description="GCSI profile identifier.")
    expected_archive_identity: Optional[str] = Field(
        default=None,
        description="Expected archive-native identity for cross-verification.",
    )
    discovery_evidence_id: Optional[str] = Field(
        default=None,
        description="evidence_id of the DiscoveryEvidence that established this URL.",
    )

    @field_validator("label_url", "normalizer_id", "profile_id", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("label_url", mode="after")
    @classmethod
    def _https_url(cls, v: str) -> str:
        from urllib.parse import urlsplit
        if "%" in v:
            raise ValueError("label_url must not contain percent-encoded characters.")
        if "\\" in v:
            raise ValueError("label_url must not contain backslash characters.")
        try:
            parsed = urlsplit(v)
        except Exception as exc:
            raise ValueError("label_url could not be parsed as a URL.") from exc
        if parsed.scheme != "https":
            raise ValueError(
                f"label_url must use HTTPS scheme; got {parsed.scheme!r}."
            )
        if not parsed.hostname:
            raise ValueError("label_url must have a non-empty hostname.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("label_url must not contain userinfo.")
        if parsed.query:
            raise ValueError("label_url must not contain a query string.")
        if parsed.fragment:
            raise ValueError("label_url must not contain a fragment.")
        return v


# ---------------------------------------------------------------------------
# C. AcquisitionLogicalProductEntry
# ---------------------------------------------------------------------------


class AcquisitionLogicalProductEntry(BaseModel):
    """One logical acquisition-plan entry.

    Represents ONE logical GCSI replay candidate, which may have one or
    more source representations (e.g. JunoCam EDR + RDR = one entry, two
    representations).

    Fields
    ------
    logical_product_id : str
        Deterministic stable identifier for this logical product.
        Formula documented in the builder module.

    instrument : str
        Instrument name, e.g. 'JIRAM', 'MWR', 'JUNOCAM', etc.

    semantic_role : str
        GCSI semantic classification, e.g. 'instrument_diagnostic',
        'radiometry_science', 'visible_imaging'.

    temporal_evidence_status : TemporalEvidenceStatus
        Classification of the temporal availability evidence:
        - EXACT_DISCOVERY_METADATA: per-product stop from captured index.
          discovery_availability_time_utc MUST be set and satisfy window.
        - LABEL_VERIFICATION_PENDING: directory HTML only; no authoritative
          per-product stop available.  discovery_availability_time_utc MUST
          be None.  B2.2 MUST verify as inclusion gate.

    discovery_availability_time_utc : datetime | None
        Discovery-based availability time (per-product observation_stop
        from a captured index with STOP_TIME column).
        Set only when temporal_evidence_status = EXACT_DISCOVERY_METADATA.
        Must satisfy: ACCUMULATION_START < time <= DECISION_EPOCH.
        None when temporal_evidence_status = LABEL_VERIFICATION_PENDING.
        B2.2 will establish the authoritative time from the label itself.

    representations : tuple[AcquisitionSourceRepresentation, ...]
        One or more planned source representations. Non-empty.
        No duplicate label_urls within one entry.

    discovery_evidence_id : str | None
        Primary discovery evidence ID for this logical product's
        enumeration (may differ from individual representation evidence).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    logical_product_id: str = Field(
        description="Deterministic stable identifier for this logical product."
    )
    instrument: str = Field(description="Instrument name, e.g. 'JIRAM'.")
    semantic_role: str = Field(
        description="GCSI semantic classification, e.g. 'instrument_diagnostic'."
    )
    temporal_evidence_status: TemporalEvidenceStatus = Field(
        description=(
            "Temporal evidence classification. "
            "EXACT_DISCOVERY_METADATA: per-product stop from captured index. "
            "LABEL_VERIFICATION_PENDING: no authoritative stop from discovery source."
        )
    )
    discovery_availability_time_utc: Optional[datetime] = Field(
        default=None,
        description=(
            "Discovery-based observation_stop in UTC. "
            "Required when temporal_evidence_status = EXACT_DISCOVERY_METADATA. "
            "Must be None when temporal_evidence_status = LABEL_VERIFICATION_PENDING."
        ),
    )
    representations: tuple[AcquisitionSourceRepresentation, ...] = Field(
        description=(
            "Planned source representations. Non-empty. "
            "No duplicate label_urls within one entry."
        )
    )
    discovery_evidence_id: Optional[str] = Field(
        default=None,
        description="Primary discovery evidence ID for this entry's enumeration.",
    )

    @field_validator("logical_product_id", "instrument", "semantic_role", mode="after")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("discovery_availability_time_utc", mode="after")
    @classmethod
    def _aware_dt(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return v
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("discovery_availability_time_utc must be timezone-aware.")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_entry(self) -> "AcquisitionLogicalProductEntry":
        # 1. Representations must be non-empty.
        if not self.representations:
            raise ValueError(
                "representations must contain at least one "
                "AcquisitionSourceRepresentation."
            )
        # 2. No duplicate label_urls within one entry.
        urls = [r.label_url for r in self.representations]
        if len(urls) != len(set(urls)):
            raise ValueError(
                "representations must not contain duplicate label_urls within "
                "one AcquisitionLogicalProductEntry."
            )
        # 3. Temporal status / time consistency.
        status = self.temporal_evidence_status
        t = self.discovery_availability_time_utc
        if status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA:
            if t is None:
                raise ValueError(
                    "discovery_availability_time_utc must be set when "
                    "temporal_evidence_status = EXACT_DISCOVERY_METADATA."
                )
            if not (ACCUMULATION_START_UTC < t <= DECISION_EPOCH_UTC):
                raise ValueError(
                    f"discovery_availability_time_utc {t.isoformat()} must satisfy "
                    f"ACCUMULATION_START ({ACCUMULATION_START_UTC.isoformat()}) < t "
                    f"<= DECISION_EPOCH ({DECISION_EPOCH_UTC.isoformat()})."
                )
        elif status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING:
            if t is not None:
                raise ValueError(
                    "discovery_availability_time_utc must be None when "
                    "temporal_evidence_status = LABEL_VERIFICATION_PENDING. "
                    "Do not synthesize approximate timestamps as discovery facts."
                )
        return self


# ---------------------------------------------------------------------------
# D. Plan-ID computation
# ---------------------------------------------------------------------------


def _compute_plan_id(
    plan_id_placeholder: str,
    replay_id: str,
    accumulation_start_utc: str,
    decision_epoch_utc: str,
    decision_epoch_policy: str,
    logical_entries: tuple[AcquisitionLogicalProductEntry, ...],
    discovery_evidence: tuple[DiscoveryEvidence, ...],
    discovery_evidence_artifact_id: Optional[str] = None,
) -> str:
    """Compute a deterministic plan_id over canonical semantic content.

    The plan_id field is excluded from the hashed content (it is the output).
    Changing any of the following changes plan_id:
    - logical product membership
    - representation URL
    - normalizer/profile
    - logical ID
    - discovery availability time (or None for pending)
    - temporal_evidence_status
    - instrument/role
    - discovery evidence binding (including sha256, retrieved_at, byte_count)
    - frozen replay window/policy
    - discovery_evidence_artifact_id (B2.1.2: sidecar mutation → new artifact_id
      → new plan_id)

    Formula::

        SHA-256(
            "gcsi.v2_acquisition_plan:v1:"
            + JSON-canonical-repr of plan semantic fields
        )

    Canonical input ordering must not change plan_id:
    entries are sorted by logical_product_id;
    evidence is sorted by evidence_id.
    """
    canonical_entries = []
    for e in sorted(logical_entries, key=lambda x: x.logical_product_id):
        canonical_reprs = []
        for r in sorted(
            e.representations,
            key=lambda x: (x.representation_role.value, x.label_url),
        ):
            canonical_reprs.append({
                "discovery_evidence_id": r.discovery_evidence_id,
                "expected_archive_identity": r.expected_archive_identity,
                "label_url": r.label_url,
                "normalizer_id": r.normalizer_id,
                "profile_id": r.profile_id,
                "representation_role": r.representation_role.value,
                "source_standard": r.source_standard.value,
            })
        t = e.discovery_availability_time_utc
        canonical_entries.append({
            "discovery_availability_time_utc": (
                t.isoformat() if t is not None else None
            ),
            "discovery_evidence_id": e.discovery_evidence_id,
            "instrument": e.instrument,
            "logical_product_id": e.logical_product_id,
            "representations": canonical_reprs,
            "semantic_role": e.semantic_role,
            "temporal_evidence_status": e.temporal_evidence_status.value,
        })

    canonical_evidence = []
    for ev in sorted(discovery_evidence, key=lambda x: x.evidence_id):
        canonical_evidence.append({
            "byte_count": ev.byte_count,
            "evidence_id": ev.evidence_id,
            "relevant_row_count": ev.relevant_row_count,
            "response_sha256": ev.response_sha256,
            "retrieved_at": ev.retrieved_at.isoformat(),
            "source_kind": ev.source_kind,
            "source_url": ev.source_url,
        })

    payload = _PLAN_ID_PREFIX + json.dumps(
        {
            "accumulation_start_utc": accumulation_start_utc,
            "decision_epoch_policy": decision_epoch_policy,
            "decision_epoch_utc": decision_epoch_utc,
            "discovery_evidence": canonical_evidence,
            "discovery_evidence_artifact_id": discovery_evidence_artifact_id,
            "logical_entries": canonical_entries,
            "replay_id": replay_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# E. HistoricalReplayV2AcquisitionPlan
# ---------------------------------------------------------------------------


#: Contract identifier for pending temporal eligibility.
FINAL_TEMPORAL_ELIGIBILITY: str = "LABEL_VERIFICATION_REQUIRED"


class HistoricalReplayV2AcquisitionPlan(BaseModel):
    """Frozen acquisition plan for one GCSI V2 historical replay.

    This is the B2.1.2 artifact: a deterministic mapping from 411 logical
    replay products to exact authoritative source representations (label
    URLs + production profiles) BEFORE product-label bulk acquisition.

    B2.1.2 additions:
    - discovery_evidence_artifact_id: binds this plan to a specific sidecar
      artifact. Sidecar mutation → new sidecar artifact_id → new plan_id.

    Integrity rules enforced
    ------------------------
    1. plan_id is a deterministic SHA-256 over all semantic content
       (including discovery_evidence_artifact_id).
    2. logical_product_id values are unique across all entries.
    3. No duplicate label_url values across all representations
       (a source label may not be planned for two logical products).
    4. EXACT entries: discovery_availability_time_utc satisfies window.
       PENDING entries: discovery_availability_time_utc is None.
    5. All discovery_evidence_id references in entries resolve to
       evidence_id values in discovery_evidence.
    6. Serialized plan must not exceed _MAX_PLAN_BYTES (32 MiB).
    7. No placeholder SHA-256 values in discovery_evidence.
    8. final_temporal_eligibility == FINAL_TEMPORAL_ELIGIBILITY contract.
    9. discovery_evidence_artifact_id matches sidecar SHA-256 (verified
       by builder at build time; loader verifies plan_id integrity).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema: str = Field(
        description="Schema identifier: 'gcsi.historical_replay_v2_acquisition_plan'."
    )
    schema_version: int = Field(
        description="Schema version integer. Current: 1."
    )
    plan_id: str = Field(
        description=(
            "SHA-256 over canonical semantic plan content. "
            "Changes when any semantic content changes."
        )
    )
    replay_id: str = Field(
        description="Stable identifier for the replay this plan targets."
    )
    accumulation_start_utc: str = Field(
        description="ISO-8601 UTC accumulation start (frozen)."
    )
    decision_epoch_utc: str = Field(
        description="ISO-8601 UTC decision epoch (frozen)."
    )
    decision_epoch_policy: str = Field(
        description="Decision epoch policy identifier (frozen)."
    )
    final_temporal_eligibility: str = Field(
        description=(
            "Top-level temporal eligibility contract. "
            "Must equal FINAL_TEMPORAL_ELIGIBILITY = 'LABEL_VERIFICATION_REQUIRED'."
        )
    )
    logical_entries: tuple[AcquisitionLogicalProductEntry, ...] = Field(
        description="All planned logical acquisition entries. Non-empty."
    )
    discovery_evidence: tuple[DiscoveryEvidence, ...] = Field(
        description="Discovery evidence used for enumeration."
    )
    discovery_evidence_artifact_id: Optional[str] = Field(
        default=None,
        description=(
            "SHA-256 artifact_id of the discovery evidence sidecar. "
            "Binds this plan to the exact sidecar content. "
            "Sidecar mutation changes artifact_id and therefore plan_id."
        ),
    )

    @field_validator(
        "schema", "plan_id", "replay_id", "accumulation_start_utc",
        "decision_epoch_utc", "decision_epoch_policy",
        mode="after",
    )
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty.")
        return v

    @field_validator("final_temporal_eligibility", mode="after")
    @classmethod
    def _check_temporal_eligibility(cls, v: str) -> str:
        if v != FINAL_TEMPORAL_ELIGIBILITY:
            raise ValueError(
                f"final_temporal_eligibility must be {FINAL_TEMPORAL_ELIGIBILITY!r}, "
                f"got {v!r}."
            )
        return v

    @model_validator(mode="after")
    def _validate_plan(self) -> "HistoricalReplayV2AcquisitionPlan":
        entries = self.logical_entries
        evidence = self.discovery_evidence

        # 1. Entries must be non-empty.
        if not entries:
            raise ValueError("logical_entries must not be empty.")

        # 2. Unique logical_product_ids.
        ids = [e.logical_product_id for e in entries]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dups = [x for x in ids if x in seen or seen.add(x)]  # type: ignore
            raise ValueError(
                f"Duplicate logical_product_id values: {dups!r}."
            )

        # 3. No duplicate label_urls across all representations.
        all_urls: list[str] = []
        for e in entries:
            for r in e.representations:
                all_urls.append(r.label_url)
        if len(all_urls) != len(set(all_urls)):
            seen2: set[str] = set()
            dup_urls = [u for u in all_urls if u in seen2 or seen2.add(u)]  # type: ignore
            raise ValueError(
                f"Duplicate label_url values across representations: "
                f"{dup_urls[:5]!r} (showing first 5)."
            )

        # 4. Temporal constraint enforced per-entry (EXACT vs PENDING).

        # 5. Discovery evidence reference resolution (when evidence present).
        if evidence:
            evidence_ids = {ev.evidence_id for ev in evidence}
            for e in entries:
                if (
                    e.discovery_evidence_id is not None
                    and e.discovery_evidence_id not in evidence_ids
                ):
                    raise ValueError(
                        f"Entry {e.logical_product_id!r} references "
                        f"discovery_evidence_id {e.discovery_evidence_id!r} "
                        "which is not present in discovery_evidence."
                    )
            for e in entries:
                for r in e.representations:
                    if (
                        r.discovery_evidence_id is not None
                        and r.discovery_evidence_id not in evidence_ids
                    ):
                        raise ValueError(
                            f"Representation {r.label_url!r} in entry "
                            f"{e.logical_product_id!r} references "
                            f"discovery_evidence_id {r.discovery_evidence_id!r} "
                            "which is not present in discovery_evidence."
                        )

        # 6. Verify plan_id (includes discovery_evidence_artifact_id).
        expected_id = _compute_plan_id(
            plan_id_placeholder=self.plan_id,
            replay_id=self.replay_id,
            accumulation_start_utc=self.accumulation_start_utc,
            decision_epoch_utc=self.decision_epoch_utc,
            decision_epoch_policy=self.decision_epoch_policy,
            logical_entries=entries,
            discovery_evidence=evidence,
            discovery_evidence_artifact_id=self.discovery_evidence_artifact_id,
        )
        if self.plan_id != expected_id:
            raise ValueError(
                f"plan_id mismatch: stored {self.plan_id!r} != "
                f"computed {expected_id!r}."
            )

        return self


# ---------------------------------------------------------------------------
# F. URL trust validation helper
# ---------------------------------------------------------------------------


# Production trusted hosts and path prefixes for each normalizer/profile pair.
# This mirrors the production profiles in pds3_adapter.py and pds4_adapter.py.
_TRUSTED_PAIRS: dict[tuple[str, str], tuple[frozenset[str], tuple[str, ...]]] = {
    # PDS4
    ("gcsi.generic_pds4_label.v1", "jiram_pds4"): (
        frozenset({"atmos.nmsu.edu"}),
        ("/PDS/data/PDS4/juno_jiram_bundle/",),
    ),
    ("gcsi.generic_pds4_label.v1", "uvs_pds4"): (
        frozenset({"atmos.nmsu.edu"}),
        ("/PDS/data/jnouvs_3001/",),
    ),
    ("gcsi.generic_pds4_label.v1", "mwr_generic_pds4"): (
        frozenset({"pds-atmospheres.nmsu.edu"}),
        ("/PDS/data/jnomwr_1100/DATA/",),
    ),
    # PDS3
    ("gcsi.generic_pds3_label.v1", "waves_burst_pds3"): (
        frozenset({"pds-ppi.igpp.ucla.edu"}),
        ("/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/",),
    ),
    ("gcsi.generic_pds3_label.v1", "waves_survey_pds3"): (
        frozenset({"pds-ppi.igpp.ucla.edu"}),
        ("/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/",),
    ),
    ("gcsi.generic_pds3_label.v1", "junocam_pds3"): (
        frozenset({"planetarydata.jpl.nasa.gov"}),
        ("/img/data/juno/JNOJNC_0029/",),
    ),
    ("gcsi.generic_pds3_label.v1", "fgm_pds3"): (
        frozenset({"pds-ppi.igpp.ucla.edu"}),
        ("/data/JNO-J-3-FGM-CAL-V1.0/",),
    ),
    ("gcsi.generic_pds3_label.v1", "jade_pds3"): (
        frozenset({"pds-ppi.igpp.ucla.edu"}),
        ("/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/",),
    ),
    ("gcsi.generic_pds3_label.v1", "jedi_pds3"): (
        frozenset({"pds-ppi.igpp.ucla.edu"}),
        ("/data/JNO-J-JED-3-CDR-V1.0/",),
    ),
}


def validate_representation_url_trust(
    representation: AcquisitionSourceRepresentation,
) -> None:
    """Validate a planned label URL against the production profile trust rules.

    This mirrors the production trust validation in the adapter modules but
    operates on the AcquisitionSourceRepresentation model without fetching.

    Raises
    ------
    ValueError
        If the URL violates the trusted host/path constraints for the
        normalizer_id + profile_id pair.
    """
    from urllib.parse import urlsplit

    key = (representation.normalizer_id, representation.profile_id)
    if key not in _TRUSTED_PAIRS:
        raise ValueError(
            f"Unknown normalizer/profile pair: "
            f"normalizer_id={representation.normalizer_id!r}, "
            f"profile_id={representation.profile_id!r}. "
            f"Known pairs: {sorted(_TRUSTED_PAIRS.keys())!r}."
        )

    allowed_hosts, allowed_prefixes = _TRUSTED_PAIRS[key]
    url = representation.label_url

    # Basic structural checks already enforced by the model field validator.
    parsed = urlsplit(url)
    hostname = parsed.hostname or ""

    if hostname not in allowed_hosts:
        raise ValueError(
            f"label_url host {hostname!r} is not in the trusted host set "
            f"for profile {representation.profile_id!r}: {sorted(allowed_hosts)!r}."
        )

    path = parsed.path
    if not any(path.startswith(prefix) for prefix in allowed_prefixes):
        raise ValueError(
            f"label_url path {path!r} does not start with any allowed prefix "
            f"for profile {representation.profile_id!r}: {list(allowed_prefixes)!r}."
        )


# ---------------------------------------------------------------------------
# G. Bounded plan loader
# ---------------------------------------------------------------------------


# Production allowed directory for plan files (set at module load time).
_PLAN_ALLOWED_DIR: pathlib.Path = (
    pathlib.Path(__file__).resolve().parents[3] / "data" / "replays"
).resolve()


def load_acquisition_plan(path: str) -> HistoricalReplayV2AcquisitionPlan:
    """Load and validate an acquisition plan from a JSON file.

    Enforces repository confinement (B2.1.2):
    - The path must resolve inside data/replays/ (no traversal, no symlink escape).
    - The path must end with .json.
    - The file must be a regular file (not a symlink, directory, etc.).
    - Bounded read: reject files > 32 MiB.

    Parameters
    ----------
    path:
        Filesystem path to the acquisition plan JSON file.
        Must be inside the production data/replays/ directory.

    Returns
    -------
    HistoricalReplayV2AcquisitionPlan
        Fully validated plan.

    Raises
    ------
    ValueError
        If the path is outside the allowed directory, is not a .json file,
        is a symlink, the file is too large, JSON is invalid, or plan fails
        validation.
    FileNotFoundError
        If the file does not exist.
    """
    import pathlib as _pathlib

    p = _pathlib.Path(path)

    # Must end in .json
    if p.suffix.lower() != ".json":
        raise ValueError(
            f"Acquisition plan path must end with .json, got {p.suffix!r}: {path!r}."
        )

    # Check for traversal in the original path before resolving.
    # Any '..' component in the original path is a traversal attempt.
    try:
        p_parts = p.parts
    except Exception:
        p_parts = ()
    if any(part == ".." for part in p_parts):
        raise ValueError(
            f"Acquisition plan path must not contain path traversal sequences: {path!r}."
        )

    # Resolve the path.
    try:
        resolved = p.resolve()
    except Exception as exc:
        raise ValueError(
            f"Acquisition plan path could not be resolved: {path!r}: {exc}"
        ) from exc

    # Must not be a symlink (resolved target of symlink could be outside boundary).
    if resolved.is_symlink():
        raise ValueError(
            f"Acquisition plan path must not be a symlink: {path!r}."
        )

    # Must resolve inside the allowed directory.
    try:
        resolved.relative_to(_PLAN_ALLOWED_DIR)
    except ValueError as exc:
        raise ValueError(
            f"Acquisition plan path {path!r} resolves outside allowed directory "
            f"{_PLAN_ALLOWED_DIR!r}."
        ) from exc

    # Must be a regular file.
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Acquisition plan path is not a regular file: {path!r}."
        )

    # Bounded read: reject files > 32 MiB before parsing.
    size = resolved.stat().st_size
    if size > _MAX_PLAN_BYTES:
        raise ValueError(
            f"Acquisition plan file exceeds maximum size ({_MAX_PLAN_BYTES} bytes): "
            f"{path!r} is {size} bytes."
        )

    raw = resolved.read_text(encoding="utf-8")

    if len(raw.encode("utf-8")) > _MAX_PLAN_BYTES:
        raise ValueError(
            f"Acquisition plan file exceeds maximum size ({_MAX_PLAN_BYTES} bytes)."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Acquisition plan file is not valid JSON: {exc}"
        ) from exc

    # strict=False is required because JSON deserializes arrays as lists,
    # not tuples.  The plan model uses tuple fields; Pydantic strict mode
    # rejects list→tuple coercion.  All other semantic validation remains.
    return HistoricalReplayV2AcquisitionPlan.model_validate(data, strict=False)


def _load_acquisition_plan_any_path(path: str) -> HistoricalReplayV2AcquisitionPlan:
    """Private test helper: load a plan from any path without confinement checks.

    NOT for production use. Use load_acquisition_plan() in production code.
    This is a private utility only for test fixtures that need to load plans
    from temporary directories.
    """
    import os

    size = os.path.getsize(path)
    if size > _MAX_PLAN_BYTES:
        raise ValueError(
            f"Acquisition plan file exceeds maximum size ({_MAX_PLAN_BYTES} bytes)."
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read(_MAX_PLAN_BYTES + 1)

    if len(raw) > _MAX_PLAN_BYTES:
        raise ValueError(
            f"Acquisition plan file exceeds maximum size ({_MAX_PLAN_BYTES} bytes)."
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Acquisition plan file is not valid JSON: {exc}"
        ) from exc

    return HistoricalReplayV2AcquisitionPlan.model_validate(data, strict=False)
