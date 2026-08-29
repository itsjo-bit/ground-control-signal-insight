"""GCSI Phase 6F-B2.1.1 — Acquisition Plan Builder.

Enumerates all 411 logical PJ62 replay products and builds the frozen
HistoricalReplayV2AcquisitionPlan JSON artifact.

This module is a BUILD-TIME utility only.  It does NOT fetch product labels.
It loads the frozen discovery-evidence sidecar and uses it to enumerate
products deterministically.

Discovery sidecar
-----------------
data/replays/juno_pj62_large_replay_v2_discovery_evidence.json

The sidecar contains:
  - discovery_evidence: 14 fetched metadata resource records (real SHA-256,
    real retrieved_at, byte_count per resource)
  - normalized_extractions: per-instrument extracted product identity rows
    used to enumerate the plan (JunoCam INDEX rows, WAVES Burst INDEX rows)

The builder consumes the sidecar rather than containing hard-coded
NASA identity arrays that claim to represent fetched archive rows.
Hard-coded constants are acceptable ONLY for frozen GCSI policy values
(replay_id, decision_epoch, semantic-role mapping).

Temporal evidence contract
--------------------------
EXACT_DISCOVERY_METADATA instruments (per-product stop from index):
  JunoCam (JNOJNC_0029 INDEX.TAB), WAVES Burst (BSTFULL INDEX.TAB)

LABEL_VERIFICATION_PENDING instruments (directory HTML only):
  JIRAM, MWR, UVS, FGM, JADE, JEDI, WAVES Survey
  → discovery_availability_time_utc = None for all pending entries.
  → B2.2 MUST verify label before acceptance.

FINAL_TEMPORAL_ELIGIBILITY = LABEL_VERIFICATION_REQUIRED

Logical-ID formulas
-------------------
JIRAM:
    ``gcsi.jiram.pj62.{img|spe}.{HHMMSS}``
    Derived from the archive filename stem (timestamp component).

MWR:
    ``gcsi.mwr.pj62.{irdr|grdr}.{YYYYDDDHHMMSS}``
    Derived from the archive filename stem.

UVS:
    ``gcsi.uvs.pj62.{sensor_lower}_{sclk}_{doy}_{obstype_lower}``
    Derived from the archive filename stem (product key).

JUNOCAM:
    ``gcsi.junocam.pj62.obs.{PRODUCT_ID}``
    Derived from PRODUCT_ID (observation identity, not EDR/RDR representation).

FGM:
    ``gcsi.fgm.pj62.{product_id_lower}``
    Derived from PRODUCT_ID in the label.

JADE:
    ``gcsi.jade.pj62.{product_id_lower}``
    Derived from official archive product identity.

JEDI:
    ``gcsi.jedi.pj62.{product_id_lower}``
    Derived from official archive product identity (filename stem).

WAVES Survey:
    ``gcsi.waves.survey.pj62.{band}``
    Derived from wave band (b, e).

WAVES Burst:
    ``gcsi.waves.burst.pj62.{product_id_lower}_v01``
    Derived from official archive PRODUCT_ID from INDEX.TAB (lowercase).

JunoCam row reconciliation
--------------------------
ORBIT_62_RAW_ROWS     = 426  (all rows with ORBIT_62 in FILE_SPECIFICATION_NAME)
PRE_RAW_ROWS          = 112  (stop_time <= accumulation_start)
ELIGIBLE_RAW_ROWS     = 248  (124 EDR + 124 RDR within window)
POST_RAW_ROWS         =  66  (stop_time > decision_epoch)
PAIRED_EDR_ROWS       = 124
PAIRED_RDR_ROWS       = 124
UNPAIRED_OR_EXCLUDED  =   0  (all eligible rows are fully paired science obs)

OLD_JUNOCAM_LEDGER_SUPERSEDED = YES
Reason: The prior B2.1 builder comment stated "346 orbit-62 rows" and
"250 eligible raw rows / 2 excluded rows (62H00000/62P00000)".
The actual JNOJNC_0029 INDEX.TAB (SHA-256
3eaa77323900cdcf30cb7c896c8ab50b608e414e42f9df539717fa306145c382)
contains 426 ORBIT_62 rows (more approach/departure rows than the old ledger
assumed) and 248 eligible rows (all science imaging types C/G/M/R/T,
fully EDR+RDR paired). The old 213-obs ledger counted only EDR rows.

Policy: JUNOCAM_NONOBSERVATION_ROW_EXCLUSION_V1
Applied during B2.1.1 INDEX.TAB parse: exclude any row whose obs-type
character (6th character of observation number, e.g. 62H00000→H) is not
in the set {C, G, M, R, T} (science imaging types). In the actual
INDEX.TAB for orbit-62 eligible window, no rows fail this filter.
This policy is applied for determinism and future-proofing.

WAVES Burst row reconciliation
------------------------------
Reproduced from BSTFULL INDEX.TAB orbit-62 eligible rows:
INDEX.TAB SHA-256: b01bba8fb6264621f0b99d453b2ea3764f8c74de3084957c6e1ca59a0b33d0e3
ORBIT_62 total rows: 282
PRE_ROWS  = 175  (stop <= accumulation_start_utc)
ELIGIBLE  =  91  (stop within window)
POST_ROWS =  16  (stop > decision_epoch_utc)
Eligible families: B_BIN=41, E_BIN=41, B_REC=3, E_REC=3, NBS_REC=3 = 91

Usage
-----
    python -m backend.app.mission_sources.v2_acquisition_plan_builder
"""

from __future__ import annotations

import json
import pathlib
import sys
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DATA_REPLAYS = _REPO_ROOT / "data" / "replays"

_PLAN_OUTPUT_PATH = _DATA_REPLAYS / "juno_pj62_large_replay_v2_acquisition_plan.json"
_SIDECAR_PATH = _DATA_REPLAYS / "juno_pj62_large_replay_v2_discovery_evidence.json"

# ---------------------------------------------------------------------------
# Frozen constants (GCSI policy, not NASA archive identities)
# ---------------------------------------------------------------------------

_REPLAY_ID = "juno_pj62_large_replay_v2"

# Science imaging obs-type characters for JunoCam exclusion policy
# JUNOCAM_NONOBSERVATION_ROW_EXCLUSION_V1
_JUNOCAM_SCIENCE_OBS_TYPES: frozenset[str] = frozenset({"C", "G", "M", "R", "T"})

# ---------------------------------------------------------------------------
# Sidecar loader
# ---------------------------------------------------------------------------

_SIDECAR_SCHEMA = "gcsi.pj62_discovery_evidence_sidecar"
_SIDECAR_VERSION = 1
_SIDECAR_ALLOWED_DIR = _DATA_REPLAYS.resolve()
_SIDECAR_MAX_BYTES = 32 * 1024 * 1024  # 32 MiB


def _load_sidecar() -> dict[str, Any]:
    """Load and validate the discovery evidence sidecar.

    Enforces:
    - Path must resolve to data/replays/ (no traversal, no symlink escape)
    - JSON only, bounded read
    - Schema and version check
    - extra fields forbidden
    """
    resolved = _SIDECAR_PATH.resolve()
    try:
        resolved.relative_to(_SIDECAR_ALLOWED_DIR)
    except ValueError as exc:
        raise ValueError(
            f"Sidecar path {_SIDECAR_PATH!r} resolves outside allowed directory "
            f"{_SIDECAR_ALLOWED_DIR!r}."
        ) from exc
    if resolved.is_symlink():
        raise ValueError(f"Sidecar path must not be a symlink: {_SIDECAR_PATH!r}.")

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

    required_keys = {
        "schema", "schema_version", "replay_id",
        "discovery_evidence", "normalized_extractions",
    }
    allowed_keys = required_keys | {"artifact_id"}
    extra = set(data.keys()) - allowed_keys
    if extra:
        raise ValueError(f"Sidecar contains forbidden extra keys: {sorted(extra)!r}.")

    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(f"Sidecar missing required keys: {sorted(missing)!r}.")

    if data["schema"] != _SIDECAR_SCHEMA:
        raise ValueError(
            f"Sidecar schema {data['schema']!r} != expected {_SIDECAR_SCHEMA!r}."
        )
    if data["schema_version"] != _SIDECAR_VERSION:
        raise ValueError(
            f"Sidecar schema_version {data['schema_version']!r} != expected {_SIDECAR_VERSION}."
        )
    if data["replay_id"] != _REPLAY_ID:
        raise ValueError(
            f"Sidecar replay_id {data['replay_id']!r} != expected {_REPLAY_ID!r}."
        )

    return data


def _make_evidence_from_sidecar(sidecar: dict[str, Any]) -> list[DiscoveryEvidence]:
    """Construct DiscoveryEvidence records from sidecar data."""
    records = []
    for rec in sidecar["discovery_evidence"]:
        ev = DiscoveryEvidence(
            evidence_id=rec["evidence_id"],
            source_url=rec["source_url"],
            retrieved_at=datetime.fromisoformat(rec["retrieved_at"]),
            response_sha256=rec["response_sha256"],
            source_kind=rec["source_kind"],
            relevant_row_count=rec.get("relevant_row_count"),
            byte_count=rec.get("byte_count"),
        )
        records.append(ev)
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

# HHMMSS timestamps extracted from jiram_orbit62_dir_full.html
_JIRAM_IMG_TIMES = [
    "090046", "090117", "090147", "090218", "090248", "090319", "090349",
    "090420", "090450", "090652", "090722", "090753", "090823", "090854",
    "090924", "090955", "091156", "091227", "091257", "091328", "091359",
    "091429", "091500", "091701", "091732", "091802", "091833", "091903",
    "091934", "092004", "092206", "092236", "092307", "092337", "092408",
    "092438", "092509", "092711", "092741", "092812", "092842", "092913",
    "092943", "093014", "093215", "093246", "093316", "093347", "093417",
    "093448", "093518",
]  # 51 entries

_JIRAM_SPE_TIMES = [
    "090048", "090119", "090149", "090220", "090250", "090321", "090351",
    "090422", "090452", "090654", "090724", "090755", "090825", "090856",
    "090926", "090957", "091158", "091229", "091259", "091330", "091401",
    "091431", "091502", "091703", "091734", "091804", "091835", "091905",
    "091936", "092006", "092208", "092238", "092309", "092339", "092410",
    "092440", "092511", "092713", "092743", "092814", "092844", "092915",
    "092945", "093016", "093217", "093248", "093318", "093349", "093419",
    "093450", "093520",
]  # 51 entries


def _build_jiram_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for ts in _JIRAM_IMG_TIMES:
        stem = f"JIR_IMG_RDR_2024166T{ts}_V01"
        lid = f"urn:nasa:pds:juno_jiram:data_calibrated:{stem.lower()}"
        url = f"{_JIRAM_BASE_URL}{stem}.xml"
        logical_id = f"gcsi.jiram.pj62.img.{ts}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            expected_archive_identity=f"{lid}::2.0",
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

    for ts in _JIRAM_SPE_TIMES:
        stem = f"JIR_SPE_RDR_2024166T{ts}_V01"
        lid = f"urn:nasa:pds:juno_jiram:data_calibrated:{stem.lower()}"
        url = f"{_JIRAM_BASE_URL}{stem}.xml"
        logical_id = f"gcsi.jiram.pj62.spe.{ts}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            expected_archive_identity=f"{lid}::1.0",
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
# DOY165 eligible hours: 10–23 = 14 slots
# DOY166 eligible hours: 00–08 = 9 slots
# Total: 23 per product type × 2 types = 46
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None for all MWR products.

_MWR_BASE_URL = "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/"

_MWR_IRDR_165_CODES = {
    10: "R04120", 11: "R06672", 12: "R30000", 13: "R30008", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944",
}
_MWR_IRDR_166_CODES = {
    0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120",
}
_MWR_GRDR_165_CODES = {
    10: "R04120", 11: "R06672", 12: "R30000", 13: "R30000", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944",
}
_MWR_GRDR_166_CODES = {
    0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120",
}


def _build_mwr_entries(
    irdr_ev_165: str, irdr_ev_166: str, grdr_ev_165: str, grdr_ev_166: str
) -> list[AcquisitionLogicalProductEntry]:
    entries = []

    def _add(kind: str, doy: int, hour: int, code: str, ev_id: str) -> None:
        kind_upper = kind.upper()
        kind_letter = "I" if kind == "irdr" else "G"
        fname_stem = f"MWR62R{kind_letter}2024{doy}{hour:02d}0000_{code}_V04"
        url = f"{_MWR_BASE_URL}{kind_upper}/2024/2024{doy}/{fname_stem}.xml"
        logical_id = f"gcsi.mwr.pj62.{kind}.2024{doy}{hour:02d}0000"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="mwr_generic_pds4",
            expected_archive_identity=fname_stem,
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

    for hour, code in sorted(_MWR_IRDR_165_CODES.items()):
        _add("irdr", 165, hour, code, irdr_ev_165)
    for hour, code in sorted(_MWR_IRDR_166_CODES.items()):
        _add("irdr", 166, hour, code, irdr_ev_166)
    for hour, code in sorted(_MWR_GRDR_165_CODES.items()):
        _add("grdr", 165, hour, code, grdr_ev_165)
    for hour, code in sorted(_MWR_GRDR_166_CODES.items()):
        _add("grdr", 166, hour, code, grdr_ev_166)

    return entries


# ---------------------------------------------------------------------------
# UVS (8 = 5 P62OBS + 3 P62SY1)
# ---------------------------------------------------------------------------
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None for all UVS products.

_UVS_BASE_URL = "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/"

_UVS_PRODUCTS = [
    ("S01", "771573735", "2024165", "P62OBS"),
    ("S02", "771573735", "2024165", "P62OBS"),
    ("S03", "771573735", "2024165", "P62OBS"),
    ("S04", "771573735", "2024165", "P62OBS"),
    ("S05", "771573735", "2024165", "P62OBS"),
    ("S01", "771613347", "2024166", "P62SY1"),
    ("S02", "771613347", "2024166", "P62SY1"),
    ("S03", "771613347", "2024166", "P62SY1"),
]  # 5 P62OBS + 3 P62SY1 = 8


def _build_uvs_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for sensor, sclk, doy_str, obs_type in _UVS_PRODUCTS:
        stem = f"UVS_{sensor}_{sclk}_{doy_str}_{obs_type}_V01"
        url = f"{_UVS_BASE_URL}{stem}.xml"
        logical_id = (
            f"gcsi.uvs.pj62.{sensor.lower()}_{sclk}_{doy_str}_{obs_type.lower()}"
        )
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url=url,
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="uvs_pds4",
            expected_archive_identity=stem,
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
#
# Row reconciliation (see module docstring):
#   ORBIT_62_RAW_ROWS = 426
#   PRE  = 112, ELIGIBLE = 248, POST = 66
#   PAIRED_EDR = 124, PAIRED_RDR = 124, EXCLUDED = 0
#
# Tuple format: (edr_product_id, edr_file_spec, rdr_product_id, rdr_file_spec,
#                stop_utc_str)  — stop_utc_str from INDEX.TAB STOP_TIME column.
# All 124 observations loaded from normalized_extractions in sidecar.

_JUNOCAM_BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/"


def _build_junocam_entries(
    lbl_ev: str,
    tab_ev: str,
    sidecar_extractions: list[dict],
) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for row in sidecar_extractions:
        edr_pid = row["edr_product_id"]
        edr_file_spec = row["edr_file_specification_name"]
        rdr_pid = row["rdr_product_id"]
        rdr_file_spec = row["rdr_file_specification_name"]
        stop_utc_str = row["stop_time_utc"]
        obs_key = row["obs_key"]

        # Apply JUNOCAM_NONOBSERVATION_ROW_EXCLUSION_V1:
        # obs_type character is the 7th character of obs_num (after 62)
        obs_num_part = edr_pid.split("_")[2]  # e.g. 62C00057
        obs_type_char = obs_num_part[2] if len(obs_num_part) >= 3 else ""
        if obs_type_char not in _JUNOCAM_SCIENCE_OBS_TYPES:
            continue  # excluded — not a science imaging obs type

        logical_id = f"gcsi.junocam.pj62.obs.{obs_key}"
        edr_url = f"{_JUNOCAM_BASE_URL}{edr_file_spec}"
        rdr_url = f"{_JUNOCAM_BASE_URL}{rdr_file_spec}"
        avail = datetime.fromisoformat(stop_utc_str).replace(tzinfo=timezone.utc)

        edr_rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.EDR,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=edr_url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="junocam_pds3",
            expected_archive_identity=edr_pid,
            discovery_evidence_id=tab_ev,
        )
        rdr_rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.RDR,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=rdr_url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="junocam_pds3",
            expected_archive_identity=rdr_pid,
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
# FGM (2)
# ---------------------------------------------------------------------------
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None.

_FGM_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/PERI-62/"
)

# (logical_stem, product_id, lbl_filename)
_FGM_PRODUCTS = [
    ("fgm_jno_l3_2024165pl", "FGM_JNO_L3_2024165PL", "fgm_jno_l3_2024165pl_v02.lbl"),
    (
        "fgm_jno_l3_2024165pl_pj62",
        "FGM_JNO_L3_2024165PL_PJ62",
        "fgm_jno_l3_2024165pl_pj62_v02.lbl",
    ),
]


def _build_fgm_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for stem, product_id, lbl_fname in _FGM_PRODUCTS:
        url = f"{_FGM_BASE_URL}{lbl_fname}"
        logical_id = f"gcsi.fgm.pj62.{stem}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.FULL_RESOLUTION,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="fgm_pds3",
            expected_archive_identity=product_id,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="FGM",
            semantic_role="magnetic_field",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# JADE (8 eligible)
# ---------------------------------------------------------------------------
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None.

_JADE_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/"
)

_JADE_PRODUCTS = [
    ("JAD_L30_LRS_ION_2024165_V01", "2024/165/JAD_L30_LRS_ION_2024165_V01.LBL"),
    ("JAD_L30_LRS_ELC_2024165_V01", "2024/165/JAD_L30_LRS_ELC_2024165_V01.LBL"),
    ("JAD_L30_HRS_ION_2024165_V01", "2024/165/JAD_L30_HRS_ION_2024165_V01.LBL"),
    ("JAD_L30_HRS_ELC_2024165_V01", "2024/165/JAD_L30_HRS_ELC_2024165_V01.LBL"),
    ("JAD_L30_LRS_ION_2024166_V01", "2024/166/JAD_L30_LRS_ION_2024166_V01.LBL"),
    ("JAD_L30_LRS_ELC_2024166_V01", "2024/166/JAD_L30_LRS_ELC_2024166_V01.LBL"),
    ("JAD_L30_HRS_ION_2024166_V01", "2024/166/JAD_L30_HRS_ION_2024166_V01.LBL"),
    ("JAD_L30_HRS_ELC_2024166_V01", "2024/166/JAD_L30_HRS_ELC_2024166_V01.LBL"),
]


def _build_jade_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for product_id, path_suffix in _JADE_PRODUCTS:
        url = f"{_JADE_BASE_URL}{path_suffix}"
        logical_id = f"gcsi.jade.pj62.{product_id.lower()}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jade_pds3",
            expected_archive_identity=product_id,
            discovery_evidence_id=evidence_id,
        )
        validate_representation_url_trust(rep)
        entries.append(AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JADE",
            semantic_role="plasma_particles",
            temporal_evidence_status=_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        ))
    return entries


# ---------------------------------------------------------------------------
# JEDI (28 eligible = 14 DOY165 + 14 DOY166)
# ---------------------------------------------------------------------------
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None.
#
# JEDI_DISCOVERED = 28, PRE_WINDOW = 0, ELIGIBLE = 28, POST_DECISION = 0

_JEDI_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/"
)

_JEDI_165_PRODUCTS = [
    "JED_090_HIERSESP_CDR_2024165_V04",
    "JED_090_HIERSISP_CDR_2024165_V04",
    "JED_090_LOERSESP_CDR_2024165_V04",
    "JED_180_HIERSESP_CDR_2024165_V04",
    "JED_180_HIERSISP_CDR_2024165_V04",
    "JED_180_LOERSESP_CDR_2024165_V04",
    "JED_270_HIERSESP_CDR_2024165_V04",
    "JED_270_HIERSISP_CDR_2024165_V04",
    "JED_270_HIERSTOFXER_CDR_2024165_V04",
    "JED_270_HIERSTOFXPHR_CDR_2024165_V04",
    "JED_270_LOERSESP_CDR_2024165_V04",
    "JED_270_LOERSISP_CDR_2024165_V04",
    "JED_270_NONPTOFXER_CDR_2024165_V04",
    "JED_270_NONPTOFXPHR_CDR_2024165_V04",
]  # 14 products

_JEDI_166_PRODUCTS = [
    "JED_090_HIERSESP_CDR_2024166_V04",
    "JED_090_HIERSISP_CDR_2024166_V04",
    "JED_090_LOERSESP_CDR_2024166_V04",
    "JED_090_LOERSISP_CDR_2024166_V04",
    "JED_180_HIERSESP_CDR_2024166_V04",
    "JED_180_HIERSISP_CDR_2024166_V04",
    "JED_180_LOERSESP_CDR_2024166_V04",
    "JED_180_LOERSISP_CDR_2024166_V04",
    "JED_270_HIERSESP_CDR_2024166_V04",
    "JED_270_HIERSTOFXER_CDR_2024166_V04",
    "JED_270_HIERSTOFXPHR_CDR_2024166_V04",
    "JED_270_LOERSESP_CDR_2024166_V04",
    "JED_270_NONPTOFXER_CDR_2024166_V04",
    "JED_270_NONPTOFXPHR_CDR_2024166_V04",
]  # 14 products


def _build_jedi_entries(ev_165: str, ev_166: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for product_id in _JEDI_165_PRODUCTS:
        url = f"{_JEDI_BASE_URL}165/{product_id}.LBL"
        logical_id = f"gcsi.jedi.pj62.{product_id.lower()}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jedi_pds3",
            expected_archive_identity=product_id,
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

    for product_id in _JEDI_166_PRODUCTS:
        url = f"{_JEDI_BASE_URL}166/{product_id}.LBL"
        logical_id = f"gcsi.jedi.pj62.{product_id.lower()}"
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="jedi_pds3",
            expected_archive_identity=product_id,
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
# WAVES Survey (2)
# ---------------------------------------------------------------------------
# Discovery source: directory HTML only.
# DISCOVERY_TIME_AUTHORITY = LABEL_PENDING
# discovery_availability_time_utc = None.

_WAVES_SURVEY_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
    "DATA/WAVES_SURVEY/2024149_ORBIT_62/"
)

_WAVES_SURVEY_PRODUCTS = [
    ("WAV_2024165T000000_B_V01", AcquisitionRepresentationRole.SURVEY_B),
    ("WAV_2024165T000000_E_V01", AcquisitionRepresentationRole.SURVEY_E),
]


def _build_waves_survey_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for stem, role in _WAVES_SURVEY_PRODUCTS:
        url = f"{_WAVES_SURVEY_BASE_URL}{stem}.LBL"
        band = "b" if role == AcquisitionRepresentationRole.SURVEY_B else "e"
        logical_id = f"gcsi.waves.survey.pj62.{band}"
        rep = AcquisitionSourceRepresentation(
            representation_role=role,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_survey_pds3",
            expected_archive_identity=stem,
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
# SOURCE: BSTFULL INDEX.TAB — exact rows from sidecar normalized extraction.
# DISCOVERY_TIME_AUTHORITY = EXACT (per-product stop from INDEX.TAB)
# temporal_evidence_status = EXACT_DISCOVERY_METADATA for all WAVES Burst.
#
# Row reconciliation (see module docstring):
#   ORBIT_62 total=282, PRE=175, ELIGIBLE=91, POST=16
#   B_BIN=41, E_BIN=41, B_REC=3, E_REC=3, NBS_REC=3 = 91

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
    sidecar_extractions: list[dict],
) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for row in sidecar_extractions:
        product_id = row["product_id"]
        file_spec = row["file_specification_name"]
        stop_utc_str = row["stop_time"]
        family = row["family"]

        role = _FAMILY_ROLE_MAP[family]
        # The label URL is the file_specification_name relative to the volume root
        url = f"{_WAVES_BURST_BASE_URL}{file_spec}"
        # Logical ID from the file spec stem (already _V01 from index)
        # product_id does not include version; file spec stem does
        stem = pathlib.Path(file_spec).stem  # e.g. WAV_2024165T145507_B_REC_V01
        logical_id = f"gcsi.waves.burst.pj62.{stem.lower()}"
        stop_utc = datetime.fromisoformat(stop_utc_str).replace(tzinfo=timezone.utc)

        rep = AcquisitionSourceRepresentation(
            representation_role=role,
            source_standard=AcquisitionSourceStandard.PDS3,
            label_url=url,
            normalizer_id="gcsi.generic_pds3_label.v1",
            profile_id="waves_burst_pds3",
            expected_archive_identity=stem,
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
    """Build and return the full 411-entry acquisition plan.

    Loads the discovery evidence sidecar, then enumerates all instruments.
    The sidecar must exist at data/replays/juno_pj62_large_replay_v2_discovery_evidence.json.
    """
    sidecar = _load_sidecar()
    evidence_list = _make_evidence_from_sidecar(sidecar)
    extractions = sidecar["normalized_extractions"]

    junocam_rows = extractions["junocam_index_tab_orbit62_eligible"]
    waves_burst_rows = extractions["waves_burst_index_tab_orbit62_eligible"]

    entries: list[AcquisitionLogicalProductEntry] = []
    entries.extend(_build_jiram_entries("jiram_orbit62_directory_html"))
    entries.extend(_build_mwr_entries(
        "mwr_irdr_2024165_directory_html",
        "mwr_irdr_2024166_directory_html",
        "mwr_grdr_2024165_directory_html",
        "mwr_grdr_2024166_directory_html",
    ))
    entries.extend(_build_uvs_entries("uvs_orbit62_directory_html"))
    entries.extend(_build_junocam_entries(
        "junocam_jnojnc_0029_index_lbl",
        "junocam_jnojnc_0029_index_tab",
        junocam_rows,
    ))
    entries.extend(_build_fgm_entries("fgm_jupiter_pl_directory_html"))
    entries.extend(_build_jade_entries("jade_calibrated_directory_html"))
    entries.extend(_build_jedi_entries(
        "jedi_165_directory_html",
        "jedi_166_directory_html",
    ))
    entries.extend(_build_waves_survey_entries("waves_survey_orbit62_directory_html"))
    entries.extend(_build_waves_burst_entries(
        "waves_burst_bstfull_index_tab",
        waves_burst_rows,
    ))

    # Reconciliation check
    total = len(entries)
    if total != 411:
        raise RuntimeError(
            f"SOURCE_ENUMERATION_CHANGED: expected 411 logical entries, got {total}. "
            f"6F_B211_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
        )

    total_refs = sum(len(e.representations) for e in entries)
    if total_refs != 535:
        raise RuntimeError(
            f"SOURCE_ENUMERATION_CHANGED: expected 535 source refs, got {total_refs}. "
            f"6F_B211_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
        )

    plan_id = _compute_plan_id(
        plan_id_placeholder="",
        replay_id=_REPLAY_ID,
        accumulation_start_utc=ACCUMULATION_START_UTC.isoformat(),
        decision_epoch_utc=DECISION_EPOCH_UTC.isoformat(),
        decision_epoch_policy=DECISION_EPOCH_POLICY,
        logical_entries=tuple(entries),
        discovery_evidence=tuple(evidence_list),
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
    )
    return plan


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

    _PLAN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plan_dict = plan.model_dump(mode="json")
    with open(_PLAN_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(plan_dict, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"  Written: {_PLAN_OUTPUT_PATH}", file=sys.stderr)
    print(f"  plan_id: {plan.plan_id}", file=sys.stderr)
    print(
        "  6F_B211_STATUS = ACQUISITION_PLAN_EVIDENCE_FROZEN (pending test run)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
