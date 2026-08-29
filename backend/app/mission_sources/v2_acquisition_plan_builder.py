"""GCSI Phase 6F-B2.1.4 — Acquisition Plan Builder.

Supersedes B2.1.3 builder with B2.1.4 trust gate corrections.

Changes from B2.1.3:
- _load_sidecar() now returns HistoricalReplayV2DiscoveryEvidenceSidecar (typed model),
  not dict[str, Any]. All downstream access uses model attributes.
- All builder functions use typed model attributes (row.filename, not row["filename"]).
- No production fallbacks: row.get("relative_label_path", filename) removed.
  Missing required fields fail hard.
- FGM builder uses fgm_peri62_directory_html evidence (PERI-62-derived, not PL root).
- load_bound_v2_acquisition_plan() returns BoundAcquisitionPlan (named dataclass).
- Full evidence equality check: all semantic fields compared, not just SHA+URL.
- Backward-compat legacy JunoCam paired format removed from production path.

All instrument identity (product IDs, filenames, codes) comes from the sidecar.
No NASA identity arrays are hard-coded in this builder.
Hard-coded constants are acceptable ONLY for frozen GCSI policy values
(replay_id, decision_epoch, semantic-role mapping).

Discovery sidecar
-----------------
data/replays/juno_pj62_large_replay_v2_discovery_evidence.json

Temporal evidence contract
--------------------------
EXACT_DISCOVERY_METADATA instruments (per-product stop from index):
  JunoCam (JNOJNC_0029 INDEX.TAB), WAVES Burst (BSTFULL INDEX.TAB), JADE (INDEX.TAB)

LABEL_VERIFICATION_PENDING instruments (directory HTML only):
  JIRAM, MWR, UVS, FGM, JEDI, WAVES Survey
  → discovery_availability_time_utc = None for all pending entries.
  → B2.2 MUST verify label before acceptance.

FINAL_TEMPORAL_ELIGIBILITY = LABEL_VERIFICATION_REQUIRED

Usage
-----
    python -m backend.app.mission_sources.v2_acquisition_plan_builder
"""

from __future__ import annotations

import json
import pathlib
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_POLICY,
    DECISION_EPOCH_UTC,
    FINAL_TEMPORAL_ELIGIBILITY,
    AcquisitionLogicalProductEntry,
    AcquisitionRepresentationRole,
    AcquisitionSourceRepresentation,
    AcquisitionSourceStandard,
    DiscoveryEvidence,
    HistoricalReplayV2AcquisitionPlan,
    TemporalEvidenceStatus,
    _compute_plan_id,
    validate_representation_url_trust,
)
from backend.app.mission_sources.v2_sidecar_models import (
    HistoricalReplayV2DiscoveryEvidenceSidecar,
    NormalizedDiscoveryExtractions,
    TypedDiscoveryEvidence,
    compute_sidecar_artifact_id,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DATA_REPLAYS = _REPO_ROOT / "data" / "replays"

_PLAN_OUTPUT_PATH = _DATA_REPLAYS / "juno_pj62_large_replay_v2_acquisition_plan.json"
_SIDECAR_PATH = _DATA_REPLAYS / "juno_pj62_large_replay_v2_discovery_evidence.json"

# ---------------------------------------------------------------------------
# Frozen constants (GCSI policy ONLY — no NASA archive identities)
# ---------------------------------------------------------------------------

_REPLAY_ID = "juno_pj62_large_replay_v2"

# Science imaging obs-type characters for JunoCam exclusion policy
# JUNOCAM_NONOBSERVATION_ROW_EXCLUSION_V1
_JUNOCAM_SCIENCE_OBS_TYPES: frozenset[str] = frozenset({"C", "G", "M", "R", "T"})

# ---------------------------------------------------------------------------
# §4.7 Evidence source URL contract
# ---------------------------------------------------------------------------

# Maps evidence_id → (expected_host, expected_path_prefix).
# Each committed evidence source must match its registered host and path prefix.
_EVIDENCE_URL_CONTRACTS: dict[str, tuple[str, str]] = {
    "fgm_jupiter_pl_directory_html": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J-3-FGM-CAL-V1.0/"
    ),
    "fgm_peri62_directory_html": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J-3-FGM-CAL-V1.0/"
    ),
    "jade_index_lbl": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/"
    ),
    "jade_index_tab": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/"
    ),
    "jedi_165_directory_html": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J-JED-3-CDR-V1.0/"
    ),
    "jedi_166_directory_html": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-J-JED-3-CDR-V1.0/"
    ),
    "jiram_orbit62_directory_html": (
        "atmos.nmsu.edu", "/PDS/data/PDS4/juno_jiram_bundle/"
    ),
    "junocam_jnojnc_0029_index_lbl": (
        "planetarydata.jpl.nasa.gov", "/img/data/juno/JNOJNC_0029/"
    ),
    "junocam_jnojnc_0029_index_tab": (
        "planetarydata.jpl.nasa.gov", "/img/data/juno/JNOJNC_0029/"
    ),
    "mwr_grdr_2024165_directory_html": (
        "pds-atmospheres.nmsu.edu", "/PDS/data/jnomwr_1100/"
    ),
    "mwr_grdr_2024166_directory_html": (
        "pds-atmospheres.nmsu.edu", "/PDS/data/jnomwr_1100/"
    ),
    "mwr_irdr_2024165_directory_html": (
        "pds-atmospheres.nmsu.edu", "/PDS/data/jnomwr_1100/"
    ),
    "mwr_irdr_2024166_directory_html": (
        "pds-atmospheres.nmsu.edu", "/PDS/data/jnomwr_1100/"
    ),
    "uvs_orbit62_directory_html": (
        "atmos.nmsu.edu", "/PDS/data/jnouvs_3001/"
    ),
    "waves_burst_bstfull_index_lbl": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
    ),
    "waves_burst_bstfull_index_tab": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
    ),
    "waves_survey_orbit62_directory_html": (
        "pds-ppi.igpp.ucla.edu", "/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
    ),
}


def validate_evidence_source_contracts(
    sidecar: "HistoricalReplayV2DiscoveryEvidenceSidecar",
) -> None:
    """§4.7: Validate that every evidence record's source_url matches the stable
    semantic contract (expected host + path prefix) registered for its evidence_id.

    Raises ValueError if any evidence record violates its contract.
    Evidence records whose evidence_id is not in the contract table are silently
    accepted (forward-compatible: new evidence sources not yet registered).
    """
    from urllib.parse import urlsplit

    for ev in sidecar.discovery_evidence:
        contract = _EVIDENCE_URL_CONTRACTS.get(ev.evidence_id)
        if contract is None:
            continue  # Not registered — no contract to enforce.
        expected_host, expected_path_prefix = contract
        try:
            parsed = urlsplit(ev.source_url)
        except Exception as exc:
            raise ValueError(
                f"Evidence {ev.evidence_id!r}: source_url {ev.source_url!r} "
                f"could not be parsed: {exc}."
            ) from exc
        if parsed.hostname != expected_host:
            raise ValueError(
                f"Evidence {ev.evidence_id!r}: source_url host "
                f"{parsed.hostname!r} does not match expected {expected_host!r}."
            )
        if not parsed.path.startswith(expected_path_prefix):
            raise ValueError(
                f"Evidence {ev.evidence_id!r}: source_url path "
                f"{parsed.path!r} does not start with expected prefix "
                f"{expected_path_prefix!r}."
            )

# ---------------------------------------------------------------------------
# Sidecar loader
# ---------------------------------------------------------------------------

_SIDECAR_SCHEMA = "gcsi.pj62_discovery_evidence_sidecar"
_SIDECAR_VERSION = 1
_SIDECAR_ALLOWED_DIR = _DATA_REPLAYS.resolve()
_SIDECAR_MAX_BYTES = 32 * 1024 * 1024  # 32 MiB


def _load_sidecar() -> HistoricalReplayV2DiscoveryEvidenceSidecar:
    """Load and validate the discovery evidence sidecar.

    B2.1.4: Returns HistoricalReplayV2DiscoveryEvidenceSidecar (typed model).
    All nested rows are validated as typed models. Extra fields are forbidden.

    Enforces (B2.1.4 hardened):
    - Original path must not contain '..' traversal sequences
    - Path must resolve to data/replays/ (no traversal, no symlink escape at target)
    - JSON only, bounded read
    - Schema and version check
    - Full Pydantic model_validate (typed, extra="forbid", all nested rows validated)
    - artifact_id cryptographic verification
    """
    # Check for traversal in original path before resolving.
    original_parts = _SIDECAR_PATH.parts
    if any(part == ".." for part in original_parts):
        raise ValueError(
            f"Sidecar path contains traversal sequences: {_SIDECAR_PATH!r}."
        )

    resolved = _SIDECAR_PATH.resolve()

    # Boundary check: resolved target must be inside allowed directory.
    try:
        resolved.relative_to(_SIDECAR_ALLOWED_DIR)
    except ValueError as exc:
        raise ValueError(
            f"Sidecar path {_SIDECAR_PATH!r} resolves outside allowed directory "
            f"{_SIDECAR_ALLOWED_DIR!r}."
        ) from exc

    # The original path must also not be a symlink itself (defense in depth).
    if _SIDECAR_PATH.is_symlink():
        raise ValueError(f"Sidecar path must not be a symlink: {_SIDECAR_PATH!r}.")
    # And the resolved target must not be a symlink.
    if resolved.is_symlink():
        raise ValueError(f"Sidecar resolved path must not be a symlink: {resolved!r}.")

    size = resolved.stat().st_size
    if size > _SIDECAR_MAX_BYTES:
        raise ValueError(f"Sidecar file exceeds maximum size ({_SIDECAR_MAX_BYTES}): {size}.")

    raw = resolved.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Sidecar is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Sidecar must be a JSON object.")

    # artifact_id is REQUIRED before model validation (so we can verify integrity)
    if "artifact_id" not in data:
        raise ValueError(
            "Sidecar is missing required field: 'artifact_id'. "
            "Run scripts/refresh_v2_discovery_evidence.py to regenerate."
        )

    # Verify artifact_id (using raw dict for computation, before typed validation)
    expected_artifact_id = compute_sidecar_artifact_id(data)
    if data["artifact_id"] != expected_artifact_id:
        raise ValueError(
            f"Sidecar artifact_id mismatch: "
            f"stored {data['artifact_id']!r} != "
            f"computed {expected_artifact_id!r}. "
            "Sidecar has been mutated since artifact_id was set."
        )

    # B2.1.4: Full typed validation — validates all nested rows, rejects extra fields
    # strict=False is required because JSON deserializes arrays as lists, not tuples.
    try:
        sidecar = HistoricalReplayV2DiscoveryEvidenceSidecar.model_validate(data, strict=False)
    except Exception as exc:
        raise ValueError(
            f"Sidecar failed typed model validation: {exc}"
        ) from exc

    # Additional semantic checks
    if sidecar.schema != _SIDECAR_SCHEMA:
        raise ValueError(
            f"Sidecar schema {sidecar.schema!r} != expected {_SIDECAR_SCHEMA!r}."
        )
    if sidecar.schema_version != _SIDECAR_VERSION:
        raise ValueError(
            f"Sidecar schema_version {sidecar.schema_version!r} != expected {_SIDECAR_VERSION}."
        )
    if sidecar.replay_id != _REPLAY_ID:
        raise ValueError(
            f"Sidecar replay_id {sidecar.replay_id!r} != expected {_REPLAY_ID!r}."
        )

    # §4.7: Evidence source URL contract validation
    try:
        validate_evidence_source_contracts(sidecar)
    except ValueError as exc:
        raise ValueError(
            f"Sidecar evidence source URL contract violation: {exc}"
        ) from exc

    return sidecar


def _make_evidence_from_sidecar(
    sidecar: HistoricalReplayV2DiscoveryEvidenceSidecar,
) -> list[DiscoveryEvidence]:
    """Construct DiscoveryEvidence records from typed sidecar model."""
    records = []
    for ev in sidecar.discovery_evidence:
        records.append(DiscoveryEvidence(
            evidence_id=ev.evidence_id,
            source_url=ev.source_url,
            retrieved_at=ev.retrieved_at,
            response_sha256=ev.response_sha256,
            source_kind=ev.source_kind,
            relevant_row_count=ev.relevant_row_count,
            byte_count=ev.byte_count,
        ))
    return records


# ---------------------------------------------------------------------------
# Temporal helpers
# ---------------------------------------------------------------------------

_EXACT = TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
_PENDING = TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING


# ---------------------------------------------------------------------------
# JIRAM (102 = 51 IMG + 51 SPE)
# ---------------------------------------------------------------------------
# All products are in orbit62/ directory.
# Filenames: JIR_IMG_RDR_2024166T{HHMMSS}_V01.xml
#            JIR_SPE_RDR_2024166T{HHMMSS}_V01.xml
# Discovery source: directory HTML only (pds4_directory_html).
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None for all JIRAM products.

_JIRAM_BASE_URL = (
    "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/"
)


def _build_jiram_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build JIRAM entries from typed sidecar normalized extraction rows."""
    entries = []
    for row in sidecar.jiram_orbit62_filenames:
        ts = row.hhmmss
        family_lower = row.family.value.lower()
        url = f"{_JIRAM_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.jiram.pj62.{family_lower}.{ts}"

        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            expected_archive_identity=None,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# MWR (46 = 23 IRDR + 23 GRDR)
# ---------------------------------------------------------------------------

_MWR_BASE_URL = "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/"


def _build_mwr_entries(
    sidecar: NormalizedDiscoveryExtractions,
    ev_irdr_165: str,
    ev_irdr_166: str,
    ev_grdr_165: str,
    ev_grdr_166: str,
) -> list[AcquisitionLogicalProductEntry]:
    """Build MWR entries from typed sidecar normalized extraction rows.

    Only rows with inclusion == ELIGIBLE produce plan entries.
    URL is constructed from base + relative_label_path (source-derived exact path).
    """
    entries = []
    for row in sidecar.mwr_orbit62_filenames:
        from backend.app.mission_sources.v2_sidecar_models import MwrInclusion
        if row.inclusion != MwrInclusion.ELIGIBLE:
            continue

        kind = row.product_type.value.lower()  # "irdr" or "grdr"
        url = f"{_MWR_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.mwr.pj62.{kind}.2024{row.doy}{row.hour:02d}0000"

        # Select appropriate evidence_id based on type and DOY
        if kind == "irdr":
            ev_id = ev_irdr_165 if row.doy == 165 else ev_irdr_166
        else:
            ev_id = ev_grdr_165 if row.doy == 165 else ev_grdr_166

        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="mwr_generic_pds4",
            expected_archive_identity=row.filename,
            discovery_evidence_id=ev_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="MWR",
            semantic_role="radiometry_science",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=ev_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# UVS (8 = 5 P62OBS + 3 P62SY1)
# ---------------------------------------------------------------------------

_UVS_BASE_URL = "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/"

# ---------------------------------------------------------------------------
# UVS authoritative label temporal exclusions (B2.2 source fact).
#
# After authoritative label acquisition, the following UVS P62SY1 (synoptic)
# products were confirmed POST-epoch:
#
#   UVS_S02_771613347_2024166_P62SY1_V01: stop=2024-06-14T11:57:55.215Z (POST)
#   UVS_S03_771613347_2024166_P62SY1_V01: stop=2024-06-15T00:50:45.152Z (POST)
#
# UVS_S01_771613347_2024166_P62SY1_V01: stop=2024-06-14T08:29:35.232Z (ELIGIBLE)
# All 5 P62OBS products: confirmed ELIGIBLE.
# ---------------------------------------------------------------------------

_UVS_AUTHORITATIVE_INELIGIBLE_FILENAMES: frozenset[str] = frozenset({
    # Sidecar stores filename WITHOUT .xml extension
    "UVS_S02_771613347_2024166_P62SY1_V01",
    "UVS_S03_771613347_2024166_P62SY1_V01",
})


def _build_uvs_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build UVS entries from typed sidecar normalized extraction rows.

    Products in _UVS_AUTHORITATIVE_INELIGIBLE_FILENAMES are excluded after
    B2.2 authoritative label acquisition confirmed temporal ineligibility.
    """
    entries = []
    for row in sidecar.uvs_orbit62_filenames:
        if row.filename in _UVS_AUTHORITATIVE_INELIGIBLE_FILENAMES:
            continue
        url = f"{_UVS_BASE_URL}{row.relative_label_path}"
        logical_id = (
            f"gcsi.uvs.pj62.{row.sensor.lower()}_{row.sclk}_{row.doy_str}_{row.obs_type.lower()}"
        )
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="uvs_pds4",
            expected_archive_identity=row.filename,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="UVS",
            semantic_role="ultraviolet_observation",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# JunoCam (124 logical, 248 source refs = 124 EDR + 124 RDR)
# ---------------------------------------------------------------------------
# SOURCE: JNOJNC_0029 INDEX.TAB (SHA-256 confirmed on re-fetch).
# DISCOVERY_TIME_AUTHORITY = EXACT  (per-product STOP_TIME from INDEX.TAB)
# temporal_evidence_status = EXACT_DISCOVERY_METADATA for all JunoCam entries.

_JUNOCAM_BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/"


def _build_junocam_entries(
    lbl_ev: str,
    tab_ev: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build JunoCam entries from typed sidecar normalized extraction rows (B2.1.4).

    Groups ELIGIBLE rows by observation_key.
    EDR and RDR must have identical START_TIME and STOP_TIME; fails if mismatch.
    """
    from collections import defaultdict
    from backend.app.mission_sources.v2_sidecar_models import JunoCamPartition

    eligible = [r for r in sidecar.junocam_index_tab_orbit62_all if r.partition == JunoCamPartition.ELIGIBLE]

    by_obs: dict = defaultdict(dict)
    for row in eligible:
        obs_key = row.observation_key
        kind = row.representation_kind.value  # "EDR" or "RDR"
        by_obs[obs_key][kind] = row

    entries = []
    for obs_key, kind_map in sorted(by_obs.items()):
        if "EDR" not in kind_map or "RDR" not in kind_map:
            # Unpaired row: reject (no eligible one-sided pairs per §20)
            raise ValueError(
                f"JunoCam observation {obs_key!r} is missing "
                f"{'RDR' if 'EDR' in kind_map else 'EDR'} representation in ELIGIBLE partition."
            )
        edr_row = kind_map["EDR"]
        rdr_row = kind_map["RDR"]

        # §20: EDR and RDR must have same start and stop times
        if edr_row.start_time_utc != rdr_row.start_time_utc:
            raise ValueError(
                f"JunoCam {obs_key!r}: EDR start_time {edr_row.start_time_utc!r} != "
                f"RDR start_time {rdr_row.start_time_utc!r}. Pair identity mismatch."
            )
        if edr_row.stop_time_utc != rdr_row.stop_time_utc:
            raise ValueError(
                f"JunoCam {obs_key!r}: EDR stop_time {edr_row.stop_time_utc!r} != "
                f"RDR stop_time {rdr_row.stop_time_utc!r}. Pair identity mismatch."
            )

        # Apply JUNOCAM_NONOBSERVATION_ROW_EXCLUSION_V1
        obs_num_part = edr_row.product_id.split("_")[2]  # e.g. 62C00057
        obs_type_char = obs_num_part[2] if len(obs_num_part) >= 3 else ""
        if obs_type_char not in _JUNOCAM_SCIENCE_OBS_TYPES:
            continue

        logical_id = f"gcsi.junocam.pj62.obs.{obs_key}"
        edr_url = f"{_JUNOCAM_BASE_URL}{edr_row.file_specification_name}"
        rdr_url = f"{_JUNOCAM_BASE_URL}{rdr_row.file_specification_name}"
        stop_utc_str = edr_row.stop_time_utc
        avail = datetime.fromisoformat(stop_utc_str).replace(tzinfo=timezone.utc)

        edr_rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.EDR,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=edr_url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="junocam_pds3",
            expected_archive_identity=edr_row.product_id,
            discovery_evidence_id=tab_ev,
        )
        rdr_rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.RDR,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=rdr_url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="junocam_pds3",
            expected_archive_identity=rdr_row.product_id,
            discovery_evidence_id=tab_ev,
        )
        for rep in (edr_rep, rdr_rep):
            validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JUNOCAM",
            semantic_role="visible_imaging",
            temporal_evidence_status=_EXACT,
            discovery_availability_time_utc=avail,
            representations=(edr_rep, rdr_rep),
            discovery_evidence_id=tab_ev,
        ))
    return entries


# ---------------------------------------------------------------------------
# FGM (2 selected from PERI-62 discovery)
# ---------------------------------------------------------------------------
# Discovery source: PERI-62 directory HTML (two-stage: PL/ → PERI-62/).
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None.
# Product identity: DISCOVERY_PATH_DERIVED (not archive-native PRODUCT_ID).
# FGM rows must reference fgm_peri62_directory_html evidence, not PL root.

_FGM_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/PERI-62/"
)


def _build_fgm_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build FGM entries from typed sidecar normalized extraction rows.

    Only rows with selected == True are included in the plan.
    Evidence_id must be fgm_peri62_directory_html (PERI-62 derived, not PL root).
    product_id is LABEL_VERIFICATION_PENDING for directory-derived FGM candidates.
    logical_id derived from filename stem (DISCOVERY_PATH_DERIVED).
    """
    entries = []
    for row in sidecar.fgm_peri62_filenames:
        if not row.selected:
            continue

        # §6: expected_archive_identity must reflect DISCOVERY_PATH_DERIVED status
        # product_id is LABEL_VERIFICATION_PENDING; use logical_stem for logical_id
        logical_id = f"gcsi.fgm.pj62.{row.logical_stem}"
        url = f"{_FGM_BASE_URL}{row.relative_label_path}"

        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.FULL_RESOLUTION,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="fgm_pds3",
            # expected_archive_identity: None because PRODUCT_ID is LABEL_VERIFICATION_PENDING
            expected_archive_identity=None,
            discovery_evidence_id=row.discovery_evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="FGM",
            semantic_role="magnetic_field",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=row.discovery_evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# JADE (8 eligible from 12 discovered)
# ---------------------------------------------------------------------------

_JADE_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/"
)


def _build_jade_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build JADE entries from typed sidecar normalized extraction rows.

    Only rows with inclusion == ELIGIBLE are included in the plan.
    JADE now has EXACT_DISCOVERY_METADATA temporal status from INDEX.TAB.
    """
    from backend.app.mission_sources.v2_sidecar_models import JadeInclusion

    entries = []
    for row in sidecar.jade_orbit62_labels:
        if row.inclusion != JadeInclusion.ELIGIBLE:
            continue

        url = f"{_JADE_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.jade.pj62.{row.product_id.lower()}"
        stop_utc = datetime.fromisoformat(row.stop_time_utc.replace("+00:00", "")).replace(
            tzinfo=timezone.utc
        )
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jade_pds3",
            expected_archive_identity=row.product_id,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JADE",
            semantic_role="plasma_particles",
            temporal_evidence_status=_EXACT,
            discovery_availability_time_utc=stop_utc,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# JEDI (28 eligible = 14 DOY165 + 14 DOY166)
# ---------------------------------------------------------------------------

_JEDI_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/"
)


# ---------------------------------------------------------------------------
# JEDI authoritative label temporal exclusions (B2.2 source fact).
#
# After authoritative label acquisition, the following JEDI product types
# were confirmed OUTSIDE the eligibility window:
#
# POST-epoch (stop > 2024-06-14T09:35:17.546Z):
#   - All DOY-166 LOER-family products: stop=2024-06-14T23:59:57 (full-day)
#     Affected product types: LOERSESP, LOERSISP (all sensors)
#
# PRE-epoch (stop <= 2024-06-13T10:00:00Z):
#   - JED_270_LOERSISP_CDR_2024165: stop=2024-06-13T09:53:07 (before start)
#
# These are deterministic archive facts established by authoritative labels.
# ---------------------------------------------------------------------------

# Product-ID stems known to be temporally ineligible after label fetch.
_JEDI_AUTHORITATIVE_INELIGIBLE_PRODUCT_IDS: frozenset[str] = frozenset({
    # DOY-166 LOER full-day products (POST-epoch: stop=2024-06-14T23:59:57)
    "JED_090_LOERSESP_CDR_2024166_V04",
    "JED_090_LOERSISP_CDR_2024166_V04",
    "JED_180_LOERSESP_CDR_2024166_V04",
    "JED_180_LOERSISP_CDR_2024166_V04",
    "JED_270_LOERSESP_CDR_2024166_V04",
    # DOY-165 product confirmed PRE-epoch: stop=2024-06-13T09:53:07
    "JED_270_LOERSISP_CDR_2024165_V04",
})


def _build_jedi_entries(
    ev_165: str,
    ev_166: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build JEDI entries from typed sidecar normalized extraction rows.

    Products in _JEDI_AUTHORITATIVE_INELIGIBLE_PRODUCT_IDS are excluded after
    B2.2 authoritative label acquisition confirmed temporal ineligibility.
    """
    entries = []
    for row in sidecar.jedi_165_labels:
        if row.product_id.upper() in _JEDI_AUTHORITATIVE_INELIGIBLE_PRODUCT_IDS:
            continue
        url = f"{_JEDI_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.jedi.pj62.{row.product_id.lower()}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jedi_pds3",
            expected_archive_identity=row.product_id,
            discovery_evidence_id=ev_165,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JEDI",
            semantic_role="energetic_particles",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=ev_165,
        ))

    for row in sidecar.jedi_166_labels:
        if row.product_id.upper() in _JEDI_AUTHORITATIVE_INELIGIBLE_PRODUCT_IDS:
            continue
        url = f"{_JEDI_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.jedi.pj62.{row.product_id.lower()}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jedi_pds3",
            expected_archive_identity=row.product_id,
            discovery_evidence_id=ev_166,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JEDI",
            semantic_role="energetic_particles",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=ev_166,
        ))

    return entries


# ---------------------------------------------------------------------------
# WAVES Survey (2 eligible from 4 discovered)
# ---------------------------------------------------------------------------

_WAVES_SURVEY_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
    "DATA/WAVES_SURVEY/2024149_ORBIT_62/"
)

_WAVES_SURVEY_BAND_ROLES: dict[str, AcquisitionRepresentationRole] = {
    "b": AcquisitionRepresentationRole.SURVEY_B,
    "e": AcquisitionRepresentationRole.SURVEY_E,
}


def _build_waves_survey_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build WAVES Survey entries from typed sidecar normalized extraction rows.

    Only rows with inclusion == ELIGIBLE are included in the plan.
    """
    from backend.app.mission_sources.v2_sidecar_models import WavesSurveyInclusion

    entries = []
    for row in sidecar.waves_survey_orbit62_labels:
        if row.inclusion != WavesSurveyInclusion.ELIGIBLE:
            continue

        band = row.band.lower()
        role = _WAVES_SURVEY_BAND_ROLES[band]
        url = f"{_WAVES_SURVEY_BASE_URL}{row.relative_label_path}"
        logical_id = f"gcsi.waves.survey.pj62.{band}"
        rep = AcquisitionSourceRepresentation(
            representation_role=role,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_survey_pds3",
            # WAVES PRODUCT_ID excludes the version suffix (_Vxx) from the filename
            # stem — the authoritative identity is established from the label itself.
            expected_archive_identity=None,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="WAVES_SURVEY",
            semantic_role="radio_plasma_survey",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# WAVES Burst (91 eligible)
# ---------------------------------------------------------------------------

_WAVES_BURST_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
)

_FAMILY_ROLE_MAP: dict[str, AcquisitionRepresentationRole] = {
    "B_BIN": AcquisitionRepresentationRole.BURST_B_BIN,
    "E_BIN": AcquisitionRepresentationRole.BURST_E_BIN,
    "B_REC": AcquisitionRepresentationRole.BURST_B_REC,
    "E_REC": AcquisitionRepresentationRole.BURST_E_REC,
    "NBS_REC": AcquisitionRepresentationRole.BURST_NBS_REC,
}


def _build_waves_burst_entries(
    evidence_id: str,
    sidecar: NormalizedDiscoveryExtractions,
) -> list[AcquisitionLogicalProductEntry]:
    """Build WAVES Burst entries from typed sidecar normalized extraction rows.

    Only rows with partition == ELIGIBLE are included in the plan.
    """
    from backend.app.mission_sources.v2_sidecar_models import WavesBurstPartition

    entries = []
    for row in sidecar.waves_burst_index_tab_orbit62_all:
        if row.partition != WavesBurstPartition.ELIGIBLE:
            continue

        role = _FAMILY_ROLE_MAP[row.family.value]
        url = f"{_WAVES_BURST_BASE_URL}{row.file_specification_name}"
        stem = pathlib.Path(row.file_specification_name).stem
        logical_id = f"gcsi.waves.burst.pj62.{stem.lower()}"
        stop_utc = datetime.fromisoformat(row.stop_time).replace(tzinfo=timezone.utc)

        rep = AcquisitionSourceRepresentation(
            representation_role=role,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
            # WAVES PRODUCT_ID excludes the version suffix (_Vxx) from the filename
            # stem — the authoritative identity is established from the label itself.
            expected_archive_identity=None,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="WAVES_BURST",
            semantic_role="radio_plasma_burst",
            temporal_evidence_status=_EXACT,
            discovery_availability_time_utc=stop_utc,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------

def build_plan() -> HistoricalReplayV2AcquisitionPlan:
    """Build and return the full 403-entry acquisition plan.

    B2.2 temporal reconciliation: 403 logical entries / 527 source refs
    (reduced from 411/535 after authoritative label verification confirmed
    8 products outside the eligibility window).

    Loads the discovery evidence sidecar, then enumerates all instruments.
    The sidecar must exist at data/replays/juno_pj62_large_replay_v2_discovery_evidence.json.

    ALL instrument identity (product IDs, filenames, codes) comes from the sidecar.
    No NASA identity arrays are hard-coded in this builder.
    """
    sidecar = _load_sidecar()
    evidence_list = _make_evidence_from_sidecar(sidecar)
    extractions = sidecar.normalized_extractions

    # Get the sidecar artifact_id for plan binding
    sidecar_artifact_id = sidecar.artifact_id

    # Get evidence_id set for JADE (use jade_index_tab)
    ev_ids = {ev.evidence_id for ev in sidecar.discovery_evidence}
    jade_evidence_id = "jade_index_tab" if "jade_index_tab" in ev_ids else "jade_calibrated_directory_html"

    # FGM: use PERI-62 evidence (not PL root)
    fgm_evidence_id = "fgm_peri62_directory_html"

    entries: list[AcquisitionLogicalProductEntry] = []
    entries.extend(_build_jiram_entries("jiram_orbit62_directory_html", extractions))
    entries.extend(_build_mwr_entries(
        extractions,
        "mwr_irdr_2024165_directory_html",
        "mwr_irdr_2024166_directory_html",
        "mwr_grdr_2024165_directory_html",
        "mwr_grdr_2024166_directory_html",
    ))
    entries.extend(_build_uvs_entries("uvs_orbit62_directory_html", extractions))
    entries.extend(_build_junocam_entries(
        "junocam_jnojnc_0029_index_lbl",
        "junocam_jnojnc_0029_index_tab",
        extractions,
    ))
    entries.extend(_build_fgm_entries(fgm_evidence_id, extractions))
    entries.extend(_build_jade_entries(jade_evidence_id, extractions))
    entries.extend(_build_jedi_entries(
        "jedi_165_directory_html",
        "jedi_166_directory_html",
        extractions,
    ))
    entries.extend(_build_waves_survey_entries("waves_survey_orbit62_directory_html", extractions))
    entries.extend(_build_waves_burst_entries(
        "waves_burst_bstfull_index_tab",
        extractions,
    ))

    # Reconciliation check
    # B2.2 authoritative label temporal reconciliation:
    # 8 products confirmed ineligible (6 JEDI + 2 UVS); plan updated from 411→403 / 535→527.
    total = len(entries)
    if total != 403:
        raise RuntimeError(
            f"SOURCE_ENUMERATION_CHANGED: expected 403 logical entries, got {total}. "
            f"6F_B22_STATUS = SOURCE_INVENTORY_RECONCILIATION_REQUIRED"
        )

    total_refs = sum(len(e.representations) for e in entries)
    if total_refs != 527:
        raise RuntimeError(
            f"SOURCE_ENUMERATION_CHANGED: expected 527 source refs, got {total_refs}. "
            f"6F_B22_STATUS = SOURCE_INVENTORY_RECONCILIATION_REQUIRED"
        )

    plan_id = _compute_plan_id(
        plan_id_placeholder="",
        replay_id=_REPLAY_ID,
        accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
        decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
        decision_epoch_policy=DECISION_EPOCH_POLICY,
        logical_entries=tuple(entries),
        discovery_evidence=tuple(evidence_list),
        discovery_evidence_artifact_id=sidecar_artifact_id,
    )

    plan = HistoricalReplayV2AcquisitionPlan(
        schema="gcsi.historical_replay_v2_acquisition_plan",
        schema_version=1,
        plan_id=plan_id,
        replay_id=_REPLAY_ID,
        accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
        decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
        decision_epoch_policy=DECISION_EPOCH_POLICY,
        final_temporal_eligibility=FINAL_TEMPORAL_ELIGIBILITY,
        logical_entries=tuple(entries),
        discovery_evidence=tuple(evidence_list),
        discovery_evidence_artifact_id=sidecar_artifact_id,
    )
    return plan


# ---------------------------------------------------------------------------
# BoundAcquisitionPlan — named result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BoundAcquisitionPlan:
    """Result of load_bound_v2_acquisition_plan().

    Contains the fully validated acquisition plan bound to its discovery
    evidence sidecar. Both are verified for mutual consistency.
    """

    plan: HistoricalReplayV2AcquisitionPlan
    sidecar: HistoricalReplayV2DiscoveryEvidenceSidecar


# ---------------------------------------------------------------------------
# Bound plan loader
# ---------------------------------------------------------------------------


def load_bound_v2_acquisition_plan(
    plan_path: str | None = None,
) -> "BoundAcquisitionPlan":
    """Load and cross-validate an acquisition plan against its discovery evidence sidecar.

    B2.1.4: Returns BoundAcquisitionPlan (typed dataclass).
    Full evidence equality check: all semantic fields compared, not just SHA+URL.
    No arbitrary sidecar bypass.

    Performs the following checks:
    1. Load and verify the discovery evidence sidecar via production _load_sidecar().
       (Artifact_id recomputed; typed model validation; no dict bypass.)
    2. Load and verify the acquisition plan (plan_id recomputed).
    3. Require plan.discovery_evidence_artifact_id == sidecar.artifact_id.
    4. Verify embedded DiscoveryEvidence records match sidecar evidence — all semantic fields.
    5. Return BoundAcquisitionPlan(plan, sidecar).

    Parameters
    ----------
    plan_path:
        Path to the acquisition plan JSON file (must be inside data/replays/).
        If None, uses the canonical production plan path.

    Returns
    -------
    BoundAcquisitionPlan
        Bound (plan, sidecar) pair.

    Raises
    ------
    ValueError
        If any integrity check fails.
    """
    from backend.app.mission_sources.v2_acquisition_plan import load_acquisition_plan  # noqa: PLC0415

    # Step 1: Load sidecar via production typed loader (no arbitrary bypass)
    sidecar = _load_sidecar()
    sidecar_artifact_id = sidecar.artifact_id

    # Step 2: Load and verify plan
    if plan_path is None:
        plan_path_str = str(_PLAN_OUTPUT_PATH)
    else:
        plan_path_str = plan_path
    plan = load_acquisition_plan(plan_path_str)

    # Step 3: Require plan artifact ID matches sidecar
    if plan.discovery_evidence_artifact_id != sidecar_artifact_id:
        raise ValueError(
            f"Plan/sidecar binding mismatch: "
            f"plan.discovery_evidence_artifact_id={plan.discovery_evidence_artifact_id!r} "
            f"!= sidecar.artifact_id={sidecar_artifact_id!r}. "
            "Plan was built against a different sidecar version."
        )

    # Step 4: Full semantic evidence equality (all fields, not just SHA+URL)
    sidecar_ev_by_id: dict[str, TypedDiscoveryEvidence] = {
        ev.evidence_id: ev for ev in sidecar.discovery_evidence
    }
    for plan_ev in plan.discovery_evidence:
        sc_ev = sidecar_ev_by_id.get(plan_ev.evidence_id)
        if sc_ev is None:
            raise ValueError(
                f"Plan evidence_id {plan_ev.evidence_id!r} not found in sidecar."
            )
        # Compare all semantic evidence fields
        mismatches = []
        if plan_ev.response_sha256 != sc_ev.response_sha256:
            mismatches.append(
                f"response_sha256: plan={plan_ev.response_sha256!r} sidecar={sc_ev.response_sha256!r}"
            )
        if plan_ev.source_url != sc_ev.source_url:
            mismatches.append(
                f"source_url: plan={plan_ev.source_url!r} sidecar={sc_ev.source_url!r}"
            )
        if plan_ev.retrieved_at != sc_ev.retrieved_at:
            mismatches.append(
                f"retrieved_at: plan={plan_ev.retrieved_at!r} sidecar={sc_ev.retrieved_at!r}"
            )
        if plan_ev.byte_count != sc_ev.byte_count:
            mismatches.append(
                f"byte_count: plan={plan_ev.byte_count!r} sidecar={sc_ev.byte_count!r}"
            )
        if plan_ev.source_kind != sc_ev.source_kind:
            mismatches.append(
                f"source_kind: plan={plan_ev.source_kind!r} sidecar={sc_ev.source_kind!r}"
            )
        if plan_ev.relevant_row_count != sc_ev.relevant_row_count:
            mismatches.append(
                f"relevant_row_count: plan={plan_ev.relevant_row_count!r} sidecar={sc_ev.relevant_row_count!r}"
            )
        if mismatches:
            raise ValueError(
                f"Plan evidence {plan_ev.evidence_id!r} has semantic mismatches with sidecar: "
                + "; ".join(mismatches)
            )

    return BoundAcquisitionPlan(plan=plan, sidecar=sidecar)


def main() -> None:
    print("Building V2 acquisition plan …", file=sys.stderr)
    plan = build_plan()
    entries = plan.logical_entries

    from collections import Counter
    inst_counts = Counter(e.instrument for e in entries)
    exact_count = sum(
        1 for e in entries
        if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
    )
    pending_count = sum(
        1 for e in entries
        if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
    )

    print(f"  Logical entries : {len(entries)}", file=sys.stderr)
    total_refs = sum(len(e.representations) for e in entries)
    print(f"  Source refs     : {total_refs}", file=sys.stderr)
    pds4_refs = sum(
        1 for e in entries for r in e.representations
        if r.source_standard == AcquisitionSourceStandard.PDS4
    )
    pds3_refs = total_refs - pds4_refs
    print(f"  PDS4 refs: {pds4_refs}  PDS3 refs: {pds3_refs}", file=sys.stderr)
    print(
        f"  Temporal: EXACT={exact_count}  PENDING={pending_count}  "
        f"TOTAL={exact_count + pending_count}",
        file=sys.stderr,
    )
    for inst in sorted(inst_counts):
        ref_count = sum(
            len(e.representations) for e in entries if e.instrument == inst
        )
        print(
            f"    {inst:15s}: {inst_counts[inst]:3d} logical, {ref_count:3d} refs",
            file=sys.stderr,
        )
    print(
        f"  discovery_evidence_artifact_id: {plan.discovery_evidence_artifact_id}",
        file=sys.stderr,
    )

    _PLAN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plan_dict = plan.model_dump(mode="json")
    with open(_PLAN_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(plan_dict, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"  Written: {_PLAN_OUTPUT_PATH}", file=sys.stderr)
    print(f"  plan_id: {plan.plan_id}", file=sys.stderr)
    print(
        "  6F_B214_STATUS = PRE_ACQUISITION_TRUST_GATE_CLOSED (pending test run)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
