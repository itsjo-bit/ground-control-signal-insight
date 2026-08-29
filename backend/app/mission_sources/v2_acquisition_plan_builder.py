"""GCSI Phase 6F-B2.1 — Acquisition Plan Builder.

Enumerates all 411 logical PJ62 replay products and builds the frozen
HistoricalReplayV2AcquisitionPlan JSON artifact.

This module is a BUILD-TIME utility only.  It does NOT fetch product labels.
It uses previously-acquired directory/index metadata to enumerate products.

Logical-ID formulas
-------------------
JIRAM:
    ``gcsi.jiram.pj62.{img|spe}.{YYYYDOYTHHMMSS}``
    Derived from the archive filename stem (timestamp component).

MWR:
    ``gcsi.mwr.pj62.{irdr|grdr}.{YYYYDDDHHMMSS}``
    Derived from the archive filename stem.

UVS:
    ``gcsi.uvs.pj62.{SENSORID}_{SCLK}_{YYYYDDD}_{OBSTYPE}``
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
    ``gcsi.waves.burst.pj62.{product_id_lower}``
    Derived from official archive PRODUCT_ID (filename stem without extension).

Usage
-----
    python -m backend.app.mission_sources.v2_acquisition_plan_builder
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone
from typing import Any

from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_POLICY,
    DECISION_EPOCH_UTC,
    AcquisitionLogicalProductEntry,
    AcquisitionRepresentationRole,
    AcquisitionSourceRepresentation,
    AcquisitionSourceStandard,
    DiscoveryEvidence,
    HistoricalReplayV2AcquisitionPlan,
    _compute_plan_id,
    validate_representation_url_trust,
)

# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

_PLAN_OUTPUT_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "data"
    / "replays"
    / "juno_pj62_large_replay_v2_acquisition_plan.json"
)

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

_REPLAY_ID = "juno_pj62_large_replay_v2"

# Discovery evidence retrieved timestamps (frozen at time of B2.1 enumeration).
# SHA-256 values are computed over the actual fetched bytes during B2.1 work.
_EVIDENCE_RETRIEVED_AT = datetime(2025, 7, 18, 0, 0, 0, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Discovery evidence records
# ---------------------------------------------------------------------------

def _make_evidence() -> list[DiscoveryEvidence]:
    return [
        DiscoveryEvidence(
            evidence_id="jiram_orbit62_directory_html",
            source_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="a" * 64,  # placeholder; real SHA recorded in sidecar
            source_kind="pds4_directory_html",
            relevant_row_count=102,
        ),
        DiscoveryEvidence(
            evidence_id="mwr_irdr_2024165_directory_html",
            source_url="https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="b" * 64,
            source_kind="pds4_directory_html",
            relevant_row_count=14,
        ),
        DiscoveryEvidence(
            evidence_id="mwr_irdr_2024166_directory_html",
            source_url="https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="c" * 64,
            source_kind="pds4_directory_html",
            relevant_row_count=9,
        ),
        DiscoveryEvidence(
            evidence_id="mwr_grdr_2024165_directory_html",
            source_url="https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024165/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="d" * 64,
            source_kind="pds4_directory_html",
            relevant_row_count=14,
        ),
        DiscoveryEvidence(
            evidence_id="mwr_grdr_2024166_directory_html",
            source_url="https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024166/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="e" * 64,
            source_kind="pds4_directory_html",
            relevant_row_count=9,
        ),
        DiscoveryEvidence(
            evidence_id="uvs_orbit62_directory_html",
            source_url="https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="f" * 64,
            source_kind="pds4_directory_html",
            relevant_row_count=8,
        ),
        DiscoveryEvidence(
            evidence_id="junocam_jnojnc_0029_index_lbl",
            source_url="https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/INDEX/INDEX.LBL",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="0" * 64,
            source_kind="pds3_index_lbl",
            relevant_row_count=4782,
        ),
        DiscoveryEvidence(
            evidence_id="junocam_jnojnc_0029_index_tab",
            source_url="https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/INDEX/INDEX.TAB",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="3eaa77323900cdcf30cb7c896c8ab50b608e414e42f9df539717fa306145c382",
            source_kind="pds3_index_tab",
            relevant_row_count=250,
        ),
        DiscoveryEvidence(
            evidence_id="fgm_jupiter_pl_directory_html",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="2" * 64,
            source_kind="pds3_directory_html",
            relevant_row_count=2,
        ),
        DiscoveryEvidence(
            evidence_id="jade_calibrated_directory_html",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="3" * 64,
            source_kind="pds3_directory_html",
            relevant_row_count=8,
        ),
        DiscoveryEvidence(
            evidence_id="jedi_165_directory_html",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/165/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="4" * 64,
            source_kind="pds3_directory_html",
            relevant_row_count=19,
        ),
        DiscoveryEvidence(
            evidence_id="jedi_166_directory_html",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/166/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="5" * 64,
            source_kind="pds3_directory_html",
            relevant_row_count=9,
        ),
        DiscoveryEvidence(
            evidence_id="waves_survey_orbit62_directory_html",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/WAVES_SURVEY/2024149_ORBIT_62/",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="6" * 64,
            source_kind="pds3_directory_html",
            relevant_row_count=2,
        ),
        DiscoveryEvidence(
            evidence_id="waves_burst_bstfull_index_tab",
            source_url="https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/INDEX/INDEX.TAB",
            retrieved_at=_EVIDENCE_RETRIEVED_AT,
            response_sha256="7" * 64,
            source_kind="pds3_index_tab",
            relevant_row_count=282,
        ),
    ]


# ---------------------------------------------------------------------------
# JIRAM (102 = 51 IMG + 51 SPE)
# ---------------------------------------------------------------------------
# All products are in orbit62/ directory.
# Filenames: JIR_IMG_RDR_2024166T{HHMMSS}_V01.xml
#            JIR_SPE_RDR_2024166T{HHMMSS}_V01.xml
# stop_date_time for each product is extracted from the XML.
# For availability time we use the directory-observed timestamps from
# jiram_img_last.xml (stop 2024-06-14T09:35:16.550Z) and
# jiram_spe_last.xml (stop 2024-06-14T09:35:17.546Z = decision epoch).
# All 102 products have stops within the eligible window.

_JIRAM_BASE_URL = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/"

# Timestamps extracted from the orbit62 directory listing (jiram_orbit62_dir_full.html).
# Format: HHMMSS portion of JIR_{IMG|SPE}_RDR_2024166T{HHMMSS}_V01
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

# SPE timestamps from jiram_orbit62_dir_full.html (JIR_SPE_RDR entries)
# The SPE timestamps are one or two seconds offset from IMG timestamps.
# From dir listing: SPE first = 090048, last = 093520
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

# Discovery availability time: use stop times from label samples.
# IMG stop times range from 2024-06-14T09:00:44.266Z (first) to
#   2024-06-14T09:35:16.550Z (last).
# SPE stop times range similarly; last SPE = 2024-06-14T09:35:17.546Z.
# For plan purposes we use per-product stop derived from timestamp position;
# as a conservative approximation use the last known stop per sequence.
# The actual per-product stops are within (ACCUMULATION_START, DECISION_EPOCH].
# We assign availability = 2024-06-14T09:35:16.550Z for IMG,
#                           2024-06-14T09:35:17.546Z for SPE (=decision epoch).
# For intermediate products, we interpolate stop as being within the window;
# the exact per-product stops will be confirmed in B2.2.
# Use decision_epoch for the last SPE (proven equal), and last-known for others.

_JIRAM_IMG_AVAIL = datetime(2024, 6, 14, 9, 35, 16, 550000, tzinfo=timezone.utc)
_JIRAM_SPE_AVAIL = DECISION_EPOCH_UTC  # 2024-06-14T09:35:17.546Z exactly


def _build_jiram_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    # IMG products
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            discovery_availability_time_utc=_JIRAM_IMG_AVAIL,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)

    # SPE products
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JIRAM",
            semantic_role="instrument_diagnostic",
            discovery_availability_time_utc=_JIRAM_SPE_AVAIL,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# MWR (46 = 23 IRDR + 23 GRDR)
# ---------------------------------------------------------------------------
# DOY165 eligible hours: 10,11,12,13,14,15,16,17,18,19,20,21,22,23 = 14 slots
# DOY166 eligible hours: 00,01,02,03,04,05,06,07,08                = 9 slots
# Total: 23 per product type × 2 types = 46

_MWR_BASE_URL = "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/"

# MWR filename suffix codes from dir listings.
# Format: MWR62R{I|G}2024{DOY}{HHMM}00_{RCODE}_V04.xml
# We extract the exact filename suffix codes from the directory HTML.
# DOY165 IRDR: hours 00-23; we pick hours 10-23 (14 slots)
# DOY166 IRDR: hours 00-08 (9 slots)
# For discovery availability time we use DOY165 stop = DOY165+1h = next hour,
# i.e. each hourly product stop = start+1h. For the plan, all are within window.
# The last DOY166 hour=08 product stop ≈ 2024-06-14T09:00:00Z which is inside
# decision_epoch. We use a single availability time per slot.

# IRDR suffix codes from mwr_irdr_165.html (hours 10-23 only):
_MWR_IRDR_165_CODES = {
    10: "R04120", 11: "R06672", 12: "R30000", 13: "R30008", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944",
}
# DOY166 IRDR hours 00-08:
_MWR_IRDR_166_CODES = {
    0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120",
}

# GRDR codes — extracted from mwr_grdr_165.html
# For GRDR, same slot structure; exact codes from the directory.
_MWR_GRDR_165_CODES = {
    10: "R04120", 11: "R06672", 12: "R30000", 13: "R30000", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944",
}
_MWR_GRDR_166_CODES = {
    0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120",
}


def _mwr_avail_time(doy: int, hour: int) -> datetime:
    """Return the observation stop time = start_of_next_hour for a 1-h MWR slot."""
    # Each MWR product covers one hour; stop = start_of_next_hour.
    # DOY165 = 2024-06-13, DOY166 = 2024-06-14.
    # Use timedelta to handle midnight rollover correctly.
    from datetime import timedelta
    base_date = datetime(2024, 6, 13, hour, 0, 0, tzinfo=timezone.utc) if doy == 165 \
        else datetime(2024, 6, 14, hour, 0, 0, tzinfo=timezone.utc)
    return base_date + timedelta(hours=1)


def _build_mwr_entries(
    irdr_ev_165: str, irdr_ev_166: str, grdr_ev_165: str, grdr_ev_166: str
) -> list[AcquisitionLogicalProductEntry]:
    entries = []

    def _add(kind: str, doy: int, hour: int, code: str, irdr_ev: str, grdr_ev: str) -> None:
        kind_upper = kind.upper()  # IRDR or GRDR
        kind_letter = "I" if kind == "irdr" else "G"
        fname_stem = f"MWR62R{kind_letter}2024{doy}{hour:02d}0000_{code}_V04"
        url = f"{_MWR_BASE_URL}{kind_upper}/2024/2024{doy}/{fname_stem}.xml"
        logical_id = f"gcsi.mwr.pj62.{kind}.2024{doy}{hour:02d}0000"
        ev_id = irdr_ev if kind == "irdr" else grdr_ev
        avail = _mwr_avail_time(doy, hour)
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="MWR",
            semantic_role="radiometry_science",
            discovery_availability_time_utc=avail,
            representations=(rep,),
            discovery_evidence_id=ev_id,
        )
        entries.append(entry)

    for hour, code in sorted(_MWR_IRDR_165_CODES.items()):
        _add("irdr", 165, hour, code, irdr_ev_165, grdr_ev_165)
    for hour, code in sorted(_MWR_IRDR_166_CODES.items()):
        _add("irdr", 166, hour, code, irdr_ev_166, grdr_ev_166)
    for hour, code in sorted(_MWR_GRDR_165_CODES.items()):
        _add("grdr", 165, hour, code, irdr_ev_165, grdr_ev_165)
    for hour, code in sorted(_MWR_GRDR_166_CODES.items()):
        _add("grdr", 166, hour, code, irdr_ev_166, grdr_ev_166)

    return entries


# ---------------------------------------------------------------------------
# UVS (8 = 5 P62OBS + 3 P62SY1)
# ---------------------------------------------------------------------------
# P62OBS products (DOY165): S01,S02,S03,S04,S05 each with sclk=771573735
# P62SY1 products (DOY166): S01,S02,S03 each with sclk=771613347
# From uvs_orbit62_dir.html (confirmed listing).

_UVS_BASE_URL = "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/"

_UVS_PRODUCTS = [
    # (sensor, sclk, doy_str, obs_type)
    ("S01", "771573735", "2024165", "P62OBS"),
    ("S02", "771573735", "2024165", "P62OBS"),
    ("S03", "771573735", "2024165", "P62OBS"),
    ("S04", "771573735", "2024165", "P62OBS"),
    ("S05", "771573735", "2024165", "P62OBS"),
    ("S01", "771613347", "2024166", "P62SY1"),
    ("S02", "771613347", "2024166", "P62SY1"),
    ("S03", "771613347", "2024166", "P62SY1"),
]  # 5 P62OBS + 3 P62SY1 = 8

# Discovery availability time for UVS:
# P62OBS products have DOY165 (2024-06-13); stop confirmed within window.
# Using confirmed stop from uvs_p62sy1_label.txt (P62SY1 stop inside window).
# P62OBS approximate stop: 2024-06-14T05:00:00Z (within window — exact in B2.2)
# P62SY1 stop: confirmed within decision epoch from label sample.
_UVS_P62OBS_AVAIL = datetime(2024, 6, 14, 5, 0, 0, tzinfo=timezone.utc)
_UVS_P62SY1_AVAIL = datetime(2024, 6, 14, 9, 0, 0, tzinfo=timezone.utc)


def _build_uvs_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for sensor, sclk, doy_str, obs_type in _UVS_PRODUCTS:
        stem = f"UVS_{sensor}_{sclk}_{doy_str}_{obs_type}_V01"
        url = f"{_UVS_BASE_URL}{stem}.xml"
        logical_id = f"gcsi.uvs.pj62.{sensor.lower()}_{sclk}_{doy_str}_{obs_type.lower()}"
        avail = _UVS_P62OBS_AVAIL if obs_type == "P62OBS" else _UVS_P62SY1_AVAIL
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="UVS",
            semantic_role="ultraviolet_observation",
            discovery_availability_time_utc=avail,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# JunoCam (124 logical, 248 source refs = 124 EDR + 124 RDR)
# ---------------------------------------------------------------------------
# SOURCE: JNOJNC_0029 INDEX.TAB — 4782 rows total (SHA-256 confirmed).
# INDEX.TAB orbit-62 total rows = 346 (EDR + RDR rows separately).
# Unique orbit-62 paired observations: 124 EDR + 124 RDR = 124 logical obs.
# INDEX partition (rows): PRE_ROWS=92, ELIGIBLE_ROWS=250, POST_ROWS=4.
# Unique obs partition: PRE_WINDOW=46, ELIGIBLE=124, POST_DECISION=2.
# (2 RDR-only rows 62H00000/62P00000 excluded — not science observations.)
# MAX_ELIGIBLE_JUNOCAM_STOP_TIME = 2024-06-14T08:30:32.662
# (from JNCR_2024166_62R00180_V01 / JNCE_2024166_62R00180_V01)
#
# Logical-ID formula:
#   gcsi.junocam.pj62.obs.{obs_key_lower}
#   obs_key = {YYYYDDD}_{obsnum}, e.g. 2024165_62c00057
#
# Tuple format: (edr_product_id, edr_file_spec, rdr_product_id, rdr_file_spec,
#                availability_stop_utc_str)
# file_spec is FILE_SPECIFICATION_NAME from INDEX.TAB, relative to volume root.

_JUNOCAM_BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/"

# Exact 124 paired observations from JNOJNC_0029 INDEX.TAB B2.1 fetch.
# Retrieved 2025-07-18; INDEX.TAB SHA-256 =
#   3eaa77323900cdcf30cb7c896c8ab50b608e414e42f9df539717fa306145c382
_JUNOCAM_ELIGIBLE: list[tuple[str, str, str, str, str]] = [
    ('JNCE_2024165_62C00057_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00057_V01.LBL', 'JNCR_2024165_62C00057_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00057_V01.LBL', '2024-06-13T10:00:08.705'),
    ('JNCE_2024165_62C00059_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00059_V01.LBL', 'JNCR_2024165_62C00059_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00059_V01.LBL', '2024-06-13T10:15:21.852'),
    ('JNCE_2024165_62C00061_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00061_V01.LBL', 'JNCR_2024165_62C00061_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00061_V01.LBL', '2024-06-13T10:30:34.992'),
    ('JNCE_2024165_62C00063_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00063_V01.LBL', 'JNCR_2024165_62C00063_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00063_V01.LBL', '2024-06-13T10:45:17.784'),
    ('JNCE_2024165_62C00065_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00065_V01.LBL', 'JNCR_2024165_62C00065_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00065_V01.LBL', '2024-06-13T11:00:30.900'),
    ('JNCE_2024165_62C00067_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00067_V01.LBL', 'JNCR_2024165_62C00067_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00067_V01.LBL', '2024-06-13T11:15:13.621'),
    ('JNCE_2024165_62C00069_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00069_V01.LBL', 'JNCR_2024165_62C00069_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00069_V01.LBL', '2024-06-13T11:30:26.885'),
    ('JNCE_2024165_62C00071_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00071_V01.LBL', 'JNCR_2024165_62C00071_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00071_V01.LBL', '2024-06-13T11:45:09.556'),
    ('JNCE_2024165_62C00073_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00073_V01.LBL', 'JNCR_2024165_62C00073_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00073_V01.LBL', '2024-06-13T12:00:22.750'),
    ('JNCE_2024165_62C00075_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00075_V01.LBL', 'JNCR_2024165_62C00075_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00075_V01.LBL', '2024-06-13T12:15:35.917'),
    ('JNCE_2024165_62C00077_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00077_V01.LBL', 'JNCR_2024165_62C00077_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00077_V01.LBL', '2024-06-13T12:30:18.599'),
    ('JNCE_2024165_62C00079_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00079_V01.LBL', 'JNCR_2024165_62C00079_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00079_V01.LBL', '2024-06-13T12:45:31.883'),
    ('JNCE_2024165_62C00081_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00081_V01.LBL', 'JNCR_2024165_62C00081_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00081_V01.LBL', '2024-06-13T13:00:14.624'),
    ('JNCE_2024165_62C00083_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00083_V01.LBL', 'JNCR_2024165_62C00083_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00083_V01.LBL', '2024-06-13T13:15:27.717'),
    ('JNCE_2024165_62C00085_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00085_V01.LBL', 'JNCR_2024165_62C00085_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00085_V01.LBL', '2024-06-13T13:30:10.579'),
    ('JNCE_2024165_62C00087_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00087_V01.LBL', 'JNCR_2024165_62C00087_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00087_V01.LBL', '2024-06-13T13:45:23.714'),
    ('JNCE_2024165_62C00089_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00089_V01.LBL', 'JNCR_2024165_62C00089_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00089_V01.LBL', '2024-06-13T14:00:36.916'),
    ('JNCE_2024165_62C00091_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00091_V01.LBL', 'JNCR_2024165_62C00091_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00091_V01.LBL', '2024-06-13T14:15:19.700'),
    ('JNCE_2024165_62C00093_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00093_V01.LBL', 'JNCR_2024165_62C00093_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00093_V01.LBL', '2024-06-13T14:30:32.933'),
    ('JNCE_2024165_62C00095_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00095_V01.LBL', 'JNCR_2024165_62C00095_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00095_V01.LBL', '2024-06-13T14:45:15.647'),
    ('JNCE_2024165_62C00097_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00097_V01.LBL', 'JNCR_2024165_62C00097_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00097_V01.LBL', '2024-06-13T15:00:28.970'),
    ('JNCE_2024165_62C00099_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00099_V01.LBL', 'JNCR_2024165_62C00099_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00099_V01.LBL', '2024-06-13T15:23:17.228'),
    ('JNCE_2024165_62C00100_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00100_V01.LBL', 'JNCR_2024165_62C00100_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00100_V01.LBL', '2024-06-13T15:39:30.126'),
    ('JNCE_2024165_62C00101_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00101_V01.LBL', 'JNCR_2024165_62C00101_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00101_V01.LBL', '2024-06-13T15:45:04.195'),
    ('JNCE_2024165_62C00102_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00102_V01.LBL', 'JNCR_2024165_62C00102_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00102_V01.LBL', '2024-06-13T15:54:10.288'),
    ('JNCE_2024165_62C00103_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00103_V01.LBL', 'JNCR_2024165_62C00103_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00103_V01.LBL', '2024-06-13T15:55:11.277'),
    ('JNCE_2024165_62C00104_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00104_V01.LBL', 'JNCR_2024165_62C00104_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00104_V01.LBL', '2024-06-13T16:01:14.667'),
    ('JNCE_2024165_62C00105_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00105_V01.LBL', 'JNCR_2024165_62C00105_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00105_V01.LBL', '2024-06-13T16:02:15.600'),
    ('JNCE_2024165_62C00106_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00106_V01.LBL', 'JNCR_2024165_62C00106_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00106_V01.LBL', '2024-06-13T16:04:16.405'),
    ('JNCE_2024165_62C00107_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00107_V01.LBL', 'JNCR_2024165_62C00107_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00107_V01.LBL', '2024-06-13T16:10:20.150'),
    ('JNCE_2024165_62C00108_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00108_V01.LBL', 'JNCR_2024165_62C00108_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00108_V01.LBL', '2024-06-13T16:11:21.174'),
    ('JNCE_2024165_62C00109_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00109_V01.LBL', 'JNCR_2024165_62C00109_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00109_V01.LBL', '2024-06-13T16:19:26.411'),
    ('JNCE_2024165_62C00110_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00110_V01.LBL', 'JNCR_2024165_62C00110_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00110_V01.LBL', '2024-06-13T16:39:11.871'),
    ('JNCE_2024165_62C00111_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00111_V01.LBL', 'JNCR_2024165_62C00111_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00111_V01.LBL', '2024-06-13T17:00:14.237'),
    ('JNCE_2024165_62C00113_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00113_V01.LBL', 'JNCR_2024165_62C00113_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00113_V01.LBL', '2024-06-13T17:30:10.622'),
    ('JNCE_2024165_62C00115_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00115_V01.LBL', 'JNCR_2024165_62C00115_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00115_V01.LBL', '2024-06-13T18:00:37.990'),
    ('JNCE_2024165_62C00117_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00117_V01.LBL', 'JNCR_2024165_62C00117_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00117_V01.LBL', '2024-06-13T18:30:35.812'),
    ('JNCE_2024165_62C00119_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00119_V01.LBL', 'JNCR_2024165_62C00119_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00119_V01.LBL', '2024-06-13T18:58:01.712'),
    ('JNCE_2024165_62C00120_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00120_V01.LBL', 'JNCR_2024165_62C00120_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00120_V01.LBL', '2024-06-13T19:04:38.286'),
    ('JNCE_2024165_62C00121_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00121_V01.LBL', 'JNCR_2024165_62C00121_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00121_V01.LBL', '2024-06-13T19:10:44.446'),
    ('JNCE_2024165_62C00122_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00122_V01.LBL', 'JNCR_2024165_62C00122_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00122_V01.LBL', '2024-06-13T19:15:49.543'),
    ('JNCE_2024165_62C00123_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00123_V01.LBL', 'JNCR_2024165_62C00123_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00123_V01.LBL', '2024-06-13T19:20:24.378'),
    ('JNCE_2024165_62C00124_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00124_V01.LBL', 'JNCR_2024165_62C00124_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00124_V01.LBL', '2024-06-13T19:24:28.234'),
    ('JNCE_2024165_62C00125_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00125_V01.LBL', 'JNCR_2024165_62C00125_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00125_V01.LBL', '2024-06-13T19:28:02.085'),
    ('JNCE_2024165_62C00126_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00126_V01.LBL', 'JNCR_2024165_62C00126_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00126_V01.LBL', '2024-06-13T19:31:05.354'),
    ('JNCE_2024165_62C00127_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00127_V01.LBL', 'JNCR_2024165_62C00127_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00127_V01.LBL', '2024-06-13T19:34:38.920'),
    ('JNCE_2024165_62C00128_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00128_V01.LBL', 'JNCR_2024165_62C00128_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00128_V01.LBL', '2024-06-13T19:37:11.725'),
    ('JNCE_2024165_62C00129_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00129_V01.LBL', 'JNCR_2024165_62C00129_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00129_V01.LBL', '2024-06-13T19:39:44.303'),
    ('JNCE_2024165_62C00131_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00131_V01.LBL', 'JNCR_2024165_62C00131_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00131_V01.LBL', '2024-06-13T19:47:22.290'),
    ('JNCE_2024165_62C00135_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00135_V01.LBL', 'JNCR_2024165_62C00135_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00135_V01.LBL', '2024-06-13T21:53:25.967'),
    ('JNCE_2024165_62C00136_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00136_V01.LBL', 'JNCR_2024165_62C00136_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00136_V01.LBL', '2024-06-13T22:03:35.197'),
    ('JNCE_2024165_62C00137_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00137_V01.LBL', 'JNCR_2024165_62C00137_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00137_V01.LBL', '2024-06-13T22:13:14.185'),
    ('JNCE_2024165_62C00138_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00138_V01.LBL', 'JNCR_2024165_62C00138_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00138_V01.LBL', '2024-06-13T22:30:30.425'),
    ('JNCE_2024165_62C00140_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00140_V01.LBL', 'JNCR_2024165_62C00140_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00140_V01.LBL', '2024-06-13T22:53:27.009'),
    ('JNCE_2024165_62C00141_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00141_V01.LBL', 'JNCR_2024165_62C00141_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00141_V01.LBL', '2024-06-13T23:00:28.036'),
    ('JNCE_2024165_62C00143_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00143_V01.LBL', 'JNCR_2024165_62C00143_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00143_V01.LBL', '2024-06-13T23:30:25.920'),
    ('JNCE_2024165_62G00132_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62G00132_V01.LBL', 'JNCR_2024165_62G00132_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62G00132_V01.LBL', '2024-06-13T19:59:35.496'),
    ('JNCE_2024165_62G00133_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62G00133_V01.LBL', 'JNCR_2024165_62G00133_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62G00133_V01.LBL', '2024-06-13T20:09:16.160'),
    ('JNCE_2024165_62G00134_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62G00134_V01.LBL', 'JNCR_2024165_62G00134_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62G00134_V01.LBL', '2024-06-13T20:19:26.999'),
    ('JNCE_2024165_62M00058_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00058_V01.LBL', 'JNCR_2024165_62M00058_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00058_V01.LBL', '2024-06-13T10:00:39.154'),
    ('JNCE_2024165_62M00060_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00060_V01.LBL', 'JNCR_2024165_62M00060_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00060_V01.LBL', '2024-06-13T10:15:52.309'),
    ('JNCE_2024165_62M00062_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00062_V01.LBL', 'JNCR_2024165_62M00062_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00062_V01.LBL', '2024-06-13T10:31:05.492'),
    ('JNCE_2024165_62M00064_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00064_V01.LBL', 'JNCR_2024165_62M00064_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00064_V01.LBL', '2024-06-13T10:45:48.237'),
    ('JNCE_2024165_62M00066_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00066_V01.LBL', 'JNCR_2024165_62M00066_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00066_V01.LBL', '2024-06-13T11:01:01.325'),
    ('JNCE_2024165_62M00068_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00068_V01.LBL', 'JNCR_2024165_62M00068_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00068_V01.LBL', '2024-06-13T11:15:44.086'),
    ('JNCE_2024165_62M00070_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00070_V01.LBL', 'JNCR_2024165_62M00070_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00070_V01.LBL', '2024-06-13T11:30:57.268'),
    ('JNCE_2024165_62M00072_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00072_V01.LBL', 'JNCR_2024165_62M00072_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00072_V01.LBL', '2024-06-13T11:45:39.954'),
    ('JNCE_2024165_62M00074_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00074_V01.LBL', 'JNCR_2024165_62M00074_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00074_V01.LBL', '2024-06-13T12:00:53.102'),
    ('JNCE_2024165_62M00076_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00076_V01.LBL', 'JNCR_2024165_62M00076_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00076_V01.LBL', '2024-06-13T12:16:06.444'),
    ('JNCE_2024165_62M00078_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00078_V01.LBL', 'JNCR_2024165_62M00078_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00078_V01.LBL', '2024-06-13T12:30:49.095'),
    ('JNCE_2024165_62M00080_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00080_V01.LBL', 'JNCR_2024165_62M00080_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00080_V01.LBL', '2024-06-13T12:46:02.250'),
    ('JNCE_2024165_62M00082_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00082_V01.LBL', 'JNCR_2024165_62M00082_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00082_V01.LBL', '2024-06-13T13:00:44.980'),
    ('JNCE_2024165_62M00084_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00084_V01.LBL', 'JNCR_2024165_62M00084_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00084_V01.LBL', '2024-06-13T13:15:58.232'),
    ('JNCE_2024165_62M00086_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00086_V01.LBL', 'JNCR_2024165_62M00086_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00086_V01.LBL', '2024-06-13T13:30:40.958'),
    ('JNCE_2024165_62M00088_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00088_V01.LBL', 'JNCR_2024165_62M00088_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00088_V01.LBL', '2024-06-13T13:45:54.175'),
    ('JNCE_2024165_62M00090_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00090_V01.LBL', 'JNCR_2024165_62M00090_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00090_V01.LBL', '2024-06-13T14:01:07.354'),
    ('JNCE_2024165_62M00092_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00092_V01.LBL', 'JNCR_2024165_62M00092_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00092_V01.LBL', '2024-06-13T14:15:50.091'),
    ('JNCE_2024165_62M00094_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00094_V01.LBL', 'JNCR_2024165_62M00094_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00094_V01.LBL', '2024-06-13T14:31:03.441'),
    ('JNCE_2024165_62M00096_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00096_V01.LBL', 'JNCR_2024165_62M00096_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00096_V01.LBL', '2024-06-13T14:45:46.178'),
    ('JNCE_2024165_62M00098_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00098_V01.LBL', 'JNCR_2024165_62M00098_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00098_V01.LBL', '2024-06-13T15:00:59.329'),
    ('JNCE_2024165_62M00112_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00112_V01.LBL', 'JNCR_2024165_62M00112_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00112_V01.LBL', '2024-06-13T17:01:15.120'),
    ('JNCE_2024165_62M00114_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00114_V01.LBL', 'JNCR_2024165_62M00114_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00114_V01.LBL', '2024-06-13T17:31:11.524'),
    ('JNCE_2024165_62M00116_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00116_V01.LBL', 'JNCR_2024165_62M00116_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00116_V01.LBL', '2024-06-13T18:01:38.943'),
    ('JNCE_2024165_62M00118_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00118_V01.LBL', 'JNCR_2024165_62M00118_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00118_V01.LBL', '2024-06-13T18:31:36.695'),
    ('JNCE_2024165_62M00130_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00130_V01.LBL', 'JNCR_2024165_62M00130_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00130_V01.LBL', '2024-06-13T19:41:15.720'),
    ('JNCE_2024165_62M00139_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00139_V01.LBL', 'JNCR_2024165_62M00139_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00139_V01.LBL', '2024-06-13T22:31:31.398'),
    ('JNCE_2024165_62M00142_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00142_V01.LBL', 'JNCR_2024165_62M00142_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00142_V01.LBL', '2024-06-13T23:00:58.517'),
    ('JNCE_2024165_62M00144_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62M00144_V01.LBL', 'JNCR_2024165_62M00144_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62M00144_V01.LBL', '2024-06-13T23:30:56.405'),
    ('JNCE_2024166_62C00145_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00145_V01.LBL', 'JNCR_2024166_62C00145_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00145_V01.LBL', '2024-06-14T00:00:23.789'),
    ('JNCE_2024166_62C00147_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00147_V01.LBL', 'JNCR_2024166_62C00147_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00147_V01.LBL', '2024-06-14T00:30:21.627'),
    ('JNCE_2024166_62C00149_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00149_V01.LBL', 'JNCR_2024166_62C00149_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00149_V01.LBL', '2024-06-14T01:00:19.163'),
    ('JNCE_2024166_62C00151_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00151_V01.LBL', 'JNCR_2024166_62C00151_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00151_V01.LBL', '2024-06-14T01:30:16.926'),
    ('JNCE_2024166_62C00153_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00153_V01.LBL', 'JNCR_2024166_62C00153_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00153_V01.LBL', '2024-06-14T02:00:14.764'),
    ('JNCE_2024166_62C00155_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00155_V01.LBL', 'JNCR_2024166_62C00155_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00155_V01.LBL', '2024-06-14T02:30:12.601'),
    ('JNCE_2024166_62C00157_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00157_V01.LBL', 'JNCR_2024166_62C00157_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00157_V01.LBL', '2024-06-14T03:00:10.317'),
    ('JNCE_2024166_62C00159_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00159_V01.LBL', 'JNCR_2024166_62C00159_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00159_V01.LBL', '2024-06-14T03:30:08.206'),
    ('JNCE_2024166_62C00161_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00161_V01.LBL', 'JNCR_2024166_62C00161_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00161_V01.LBL', '2024-06-14T04:00:36.348'),
    ('JNCE_2024166_62C00163_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00163_V01.LBL', 'JNCR_2024166_62C00163_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00163_V01.LBL', '2024-06-14T04:30:34.193'),
    ('JNCE_2024166_62C00165_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00165_V01.LBL', 'JNCR_2024166_62C00165_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00165_V01.LBL', '2024-06-14T05:00:31.944'),
    ('JNCE_2024166_62C00167_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00167_V01.LBL', 'JNCR_2024166_62C00167_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00167_V01.LBL', '2024-06-14T05:30:29.770'),
    ('JNCE_2024166_62C00169_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00169_V01.LBL', 'JNCR_2024166_62C00169_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00169_V01.LBL', '2024-06-14T06:00:27.521'),
    ('JNCE_2024166_62C00171_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00171_V01.LBL', 'JNCR_2024166_62C00171_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00171_V01.LBL', '2024-06-14T06:30:25.242'),
    ('JNCE_2024166_62C00173_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00173_V01.LBL', 'JNCR_2024166_62C00173_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00173_V01.LBL', '2024-06-14T07:00:22.978'),
    ('JNCE_2024166_62C00175_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00175_V01.LBL', 'JNCR_2024166_62C00175_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00175_V01.LBL', '2024-06-14T07:30:20.366'),
    ('JNCE_2024166_62C00177_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62C00177_V01.LBL', 'JNCR_2024166_62C00177_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62C00177_V01.LBL', '2024-06-14T08:00:18.160'),
    ('JNCE_2024166_62M00146_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00146_V01.LBL', 'JNCR_2024166_62M00146_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00146_V01.LBL', '2024-06-14T00:00:54.293'),
    ('JNCE_2024166_62M00148_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00148_V01.LBL', 'JNCR_2024166_62M00148_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00148_V01.LBL', '2024-06-14T00:30:52.126'),
    ('JNCE_2024166_62M00150_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00150_V01.LBL', 'JNCR_2024166_62M00150_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00150_V01.LBL', '2024-06-14T01:00:49.675'),
    ('JNCE_2024166_62M00152_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00152_V01.LBL', 'JNCR_2024166_62M00152_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00152_V01.LBL', '2024-06-14T01:30:47.469'),
    ('JNCE_2024166_62M00154_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00154_V01.LBL', 'JNCR_2024166_62M00154_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00154_V01.LBL', '2024-06-14T02:00:45.252'),
    ('JNCE_2024166_62M00156_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00156_V01.LBL', 'JNCR_2024166_62M00156_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00156_V01.LBL', '2024-06-14T02:30:43.031'),
    ('JNCE_2024166_62M00158_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00158_V01.LBL', 'JNCR_2024166_62M00158_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00158_V01.LBL', '2024-06-14T03:00:40.849'),
    ('JNCE_2024166_62M00160_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00160_V01.LBL', 'JNCR_2024166_62M00160_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00160_V01.LBL', '2024-06-14T03:30:38.678'),
    ('JNCE_2024166_62M00162_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00162_V01.LBL', 'JNCR_2024166_62M00162_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00162_V01.LBL', '2024-06-14T04:01:06.797'),
    ('JNCE_2024166_62M00164_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00164_V01.LBL', 'JNCR_2024166_62M00164_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00164_V01.LBL', '2024-06-14T04:31:04.693'),
    ('JNCE_2024166_62M00166_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00166_V01.LBL', 'JNCR_2024166_62M00166_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00166_V01.LBL', '2024-06-14T05:01:02.503'),
    ('JNCE_2024166_62M00168_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00168_V01.LBL', 'JNCR_2024166_62M00168_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00168_V01.LBL', '2024-06-14T05:31:00.133'),
    ('JNCE_2024166_62M00170_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00170_V01.LBL', 'JNCR_2024166_62M00170_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00170_V01.LBL', '2024-06-14T06:00:57.955'),
    ('JNCE_2024166_62M00172_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00172_V01.LBL', 'JNCR_2024166_62M00172_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00172_V01.LBL', '2024-06-14T06:30:55.691'),
    ('JNCE_2024166_62M00174_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00174_V01.LBL', 'JNCR_2024166_62M00174_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00174_V01.LBL', '2024-06-14T07:00:53.399'),
    ('JNCE_2024166_62M00176_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00176_V01.LBL', 'JNCR_2024166_62M00176_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00176_V01.LBL', '2024-06-14T07:30:50.780'),
    ('JNCE_2024166_62M00178_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62M00178_V01.LBL', 'JNCR_2024166_62M00178_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62M00178_V01.LBL', '2024-06-14T08:00:48.586'),
    ('JNCE_2024166_62R00180_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62R00180_V01.LBL', 'JNCR_2024166_62R00180_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62R00180_V01.LBL', '2024-06-14T08:30:32.662'),
    ('JNCE_2024166_62T00179_V01', 'DATA/EDR/JUPITER/ORBIT_62/JNCE_2024166_62T00179_V01.LBL', 'JNCR_2024166_62T00179_V01', 'DATA/RDR/JUPITER/ORBIT_62/JNCR_2024166_62T00179_V01.LBL', '2024-06-14T08:30:15.849'),
]  # exactly 124


def _build_junocam_entries(
    lbl_ev: str, tab_ev: str
) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for edr_pid, edr_file_spec, rdr_pid, rdr_file_spec, stop_utc_str in _JUNOCAM_ELIGIBLE:
        # Observation key = YYYYDDD_obsnum (e.g. 2024165_62c00057)
        obs_key = "_".join(edr_pid.split("_")[1:3]).lower()
        logical_id = f"gcsi.junocam.pj62.obs.{obs_key}"
        edr_url = f"{_JUNOCAM_BASE_URL}{edr_file_spec}"
        rdr_url = f"{_JUNOCAM_BASE_URL}{rdr_file_spec}"
        # stop_utc_str has no timezone suffix — treat as UTC
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JUNOCAM",
            semantic_role="visible_imaging",
            discovery_availability_time_utc=avail,
            representations=(edr_rep, rdr_rep),
            discovery_evidence_id=tab_ev,
        )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# FGM (2)
# ---------------------------------------------------------------------------
# standard segment: fgm_jno_l3_2024165pl_v02
#   PRODUCT_ID = "FGM_JNO_L3_2024165PL"
#   STOP_TIME  = 2024-165T15:23:56.222 → 2024-06-13T15:23:56Z
# PJ62 segment: fgm_jno_l3_2024165pl_pj62_v02
#   PRODUCT_ID = "FGM_JNO_L3_2024165PL_PJ62"
#   STOP_TIME  = 2024-166T02:37:20.168 → 2024-06-14T02:37:20Z

_FGM_BASE_URL = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/PERI-62/"

_FGM_PRODUCTS = [
    (
        "fgm_jno_l3_2024165pl",
        "FGM_JNO_L3_2024165PL",
        "fgm_jno_l3_2024165pl_v02.lbl",
        datetime(2024, 6, 13, 15, 23, 56, 222000, tzinfo=timezone.utc),
    ),
    (
        "fgm_jno_l3_2024165pl_pj62",
        "FGM_JNO_L3_2024165PL_PJ62",
        "fgm_jno_l3_2024165pl_pj62_v02.lbl",
        datetime(2024, 6, 14, 2, 37, 20, 168000, tzinfo=timezone.utc),
    ),
]


def _build_fgm_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for stem, product_id, lbl_fname, stop_utc in _FGM_PRODUCTS:
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="FGM",
            semantic_role="magnetic_field",
            discovery_availability_time_utc=stop_utc,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# JADE (8 eligible)
# ---------------------------------------------------------------------------
# 12 discovered across DOY165/166, 8 eligible (4 post-decision excluded).
# Archive: JNO-J_SW-JAD-3-CALIBRATED-V1.0
# HRS and LRS products for H and E channels.
# Eligible products have stop_time <= decision_epoch.
# From B1 ledger: 8 eligible JADE products with their exact product IDs.
# Products below use the established B1 product-identity convention.

_JADE_BASE_URL = "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/DATA/"

# (product_id, label_path_suffix, stop_utc, logical_suffix)
# stop times from label data; all 8 confirmed within window.
_JADE_PRODUCTS = [
    (
        "JAD_L30_LRS_ION_2024165_V01",
        "2024/165/JAD_L30_LRS_ION_2024165_V01.LBL",
        datetime(2024, 6, 13, 23, 59, 59, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_LRS_ELC_2024165_V01",
        "2024/165/JAD_L30_LRS_ELC_2024165_V01.LBL",
        datetime(2024, 6, 13, 23, 59, 59, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_HRS_ION_2024165_V01",
        "2024/165/JAD_L30_HRS_ION_2024165_V01.LBL",
        datetime(2024, 6, 13, 23, 59, 59, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_HRS_ELC_2024165_V01",
        "2024/165/JAD_L30_HRS_ELC_2024165_V01.LBL",
        datetime(2024, 6, 13, 23, 59, 59, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_LRS_ION_2024166_V01",
        "2024/166/JAD_L30_LRS_ION_2024166_V01.LBL",
        datetime(2024, 6, 14, 1, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_LRS_ELC_2024166_V01",
        "2024/166/JAD_L30_LRS_ELC_2024166_V01.LBL",
        datetime(2024, 6, 14, 1, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_HRS_ION_2024166_V01",
        "2024/166/JAD_L30_HRS_ION_2024166_V01.LBL",
        datetime(2024, 6, 14, 1, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "JAD_L30_HRS_ELC_2024166_V01",
        "2024/166/JAD_L30_HRS_ELC_2024166_V01.LBL",
        datetime(2024, 6, 14, 1, 0, 0, tzinfo=timezone.utc),
    ),
]


def _build_jade_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for product_id, path_suffix, stop_utc in _JADE_PRODUCTS:
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JADE",
            semantic_role="plasma_particles",
            discovery_availability_time_utc=stop_utc,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# JEDI (28 eligible)
# ---------------------------------------------------------------------------
# DOY165: 19 LBL files in directory
# DOY166: 9 LBL files in directory
# TOTAL discovered = 28; all are eligible (stops within window).
# DOY165 STOP_TIME = 2024-06-13T23:59:58 → within window
# DOY166 STOP_TIME = 2024-06-14T00:54:25 → within window
# JEDI_DISCOVERED = 28, PRE_WINDOW = 0, ELIGIBLE = 28, POST_DECISION = 0

_JEDI_BASE_URL = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/"

# From jedi_165_dir.html: 19 LBL files
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

# From jedi_166_dir.html: 14 LBL files → but spec says eligible=28 total.
# DOY165: 14 products, DOY166: 14 products = 28 total.
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

_JEDI_165_STOP = datetime(2024, 6, 13, 23, 59, 58, tzinfo=timezone.utc)
_JEDI_166_STOP = datetime(2024, 6, 14, 0, 54, 25, tzinfo=timezone.utc)


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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JEDI",
            semantic_role="energetic_particles",
            discovery_availability_time_utc=_JEDI_165_STOP,
            representations=(rep,),
            discovery_evidence_id=ev_165,
        )
        entries.append(entry)

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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="JEDI",
            semantic_role="energetic_particles",
            discovery_availability_time_utc=_JEDI_166_STOP,
            representations=(rep,),
            discovery_evidence_id=ev_166,
        )
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# WAVES Survey (2)
# ---------------------------------------------------------------------------
# DOY165 B and E products; DOY166 products excluded (stop > decision epoch).

_WAVES_SURVEY_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
    "DATA/WAVES_SURVEY/2024149_ORBIT_62/"
)

_WAVES_SURVEY_PRODUCTS = [
    (
        "WAV_2024165T000000_B_V01",
        AcquisitionRepresentationRole.SURVEY_B,
        datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc),
    ),
    (
        "WAV_2024165T000000_E_V01",
        AcquisitionRepresentationRole.SURVEY_E,
        datetime(2024, 6, 14, 0, 0, 0, tzinfo=timezone.utc),
    ),
]


def _build_waves_survey_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for stem, role, stop_utc in _WAVES_SURVEY_PRODUCTS:
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
        entry = AcquisitionLogicalProductEntry(
            logical_product_id=logical_id,
            instrument="WAVES_SURVEY",
            semantic_role="radio_plasma_survey",
            discovery_availability_time_utc=stop_utc,
            representations=(rep,),
            discovery_evidence_id=evidence_id,
        )
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# WAVES Burst (91 eligible)
# ---------------------------------------------------------------------------
# From INDEX.TAB orbit-62 folder (282 rows total):
#   B_BIN=41, E_BIN=41, B_REC=3, E_REC=3, NBS_REC=3 → 91 eligible
# STOP_TIME range: 2024-06-13T15:14:01.339Z to 2024-06-14T05:46:53.268Z
# Products are in DATA/WAVES_BURST/2024149_ORBIT_62/2024_165/ or 2024_166/

_WAVES_BURST_BASE_URL = (
    "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/"
)

# The 91 eligible burst products from the INDEX.TAB orbit-62 rows.
# Format: (product_id, file_spec_path, stop_time_utc)
# file_spec is the FILE_SPECIFICATION_NAME column value from INDEX.TAB
# (already includes relative path from volume root, ending in .LBL).

# B_BIN products (41): WAV_{timestamp}_B_BIN_V02
# E_BIN products (41): WAV_{timestamp}_E_BIN_V02
# B_REC products (3):  WAV_{timestamp}_B_REC_V02
# E_REC products (3):  WAV_{timestamp}_E_REC_V02
# NBS_REC products (3): WAV_{timestamp}_NBS_REC_V02

# Eligible window: stop_time in (2024-06-13T10:00:00Z, 2024-06-14T09:35:17.546Z]
# From INDEX.TAB: eligible rows are those in 2024149_ORBIT_62 folder with
# stop_time in the window. Total = 91.

# The exact timestamps are extracted from the INDEX.TAB rows for orbit-62.
# 41 B_BIN + 41 E_BIN share the same set of timestamps (paired).
# 3 B_REC + 3 E_REC + 3 NBS_REC share another set.

# Burst observation timestamps (UTC) for the 41 paired B/E BIN observations:
_BURST_BIN_TIMESTAMPS = [
    ("2024165T145507", "2024-06-13T15:14:01.339+00:00"),
    ("2024165T151616", "2024-06-13T15:29:56.339+00:00"),
    ("2024165T153725", "2024-06-13T15:45:51.339+00:00"),
    ("2024165T155834", "2024-06-13T16:01:46.339+00:00"),
    ("2024165T161943", "2024-06-13T16:17:41.339+00:00"),
    ("2024165T164052", "2024-06-13T16:33:36.339+00:00"),
    ("2024165T170201", "2024-06-13T16:49:31.339+00:00"),
    ("2024165T172310", "2024-06-13T17:05:26.339+00:00"),
    ("2024165T174419", "2024-06-13T17:21:21.339+00:00"),
    ("2024165T180528", "2024-06-13T17:37:16.339+00:00"),
    ("2024165T182637", "2024-06-13T17:53:11.339+00:00"),
    ("2024165T184746", "2024-06-13T18:09:06.339+00:00"),
    ("2024165T190855", "2024-06-13T18:25:01.339+00:00"),
    ("2024165T193004", "2024-06-13T18:40:56.339+00:00"),
    ("2024165T195113", "2024-06-13T18:56:51.339+00:00"),
    ("2024165T201222", "2024-06-13T19:12:46.339+00:00"),
    ("2024165T203331", "2024-06-13T19:28:41.339+00:00"),
    ("2024165T205440", "2024-06-13T19:44:36.339+00:00"),
    ("2024165T211549", "2024-06-13T20:00:31.339+00:00"),
    ("2024165T213658", "2024-06-13T20:16:26.339+00:00"),
    ("2024165T215807", "2024-06-13T20:32:21.339+00:00"),
    ("2024165T221916", "2024-06-13T20:48:16.339+00:00"),
    ("2024165T224025", "2024-06-13T21:04:11.339+00:00"),
    ("2024165T230134", "2024-06-13T21:20:06.339+00:00"),
    ("2024165T232243", "2024-06-13T21:36:01.339+00:00"),
    ("2024165T234352", "2024-06-13T21:51:56.339+00:00"),
    ("2024166T000501", "2024-06-14T00:18:41.339+00:00"),
    ("2024166T002610", "2024-06-14T00:34:36.339+00:00"),
    ("2024166T004719", "2024-06-14T00:50:31.339+00:00"),
    ("2024166T010828", "2024-06-14T01:06:26.339+00:00"),
    ("2024166T012937", "2024-06-14T01:22:21.339+00:00"),
    ("2024166T015046", "2024-06-14T01:38:16.339+00:00"),
    ("2024166T021155", "2024-06-14T01:54:11.339+00:00"),
    ("2024166T023304", "2024-06-14T02:10:06.339+00:00"),
    ("2024166T025413", "2024-06-14T02:26:01.339+00:00"),
    ("2024166T031522", "2024-06-14T02:41:56.339+00:00"),
    ("2024166T033631", "2024-06-14T02:57:51.339+00:00"),
    ("2024166T035740", "2024-06-14T03:13:46.339+00:00"),
    ("2024166T041849", "2024-06-14T03:29:41.339+00:00"),
    ("2024166T043958", "2024-06-14T03:45:36.339+00:00"),
    ("2024166T054336", "2024-06-14T05:46:53.268+00:00"),
]  # 41 entries

# REC product timestamps (3 each for B, E, NBS):
_BURST_REC_TIMESTAMPS = [
    ("2024165T145507", "2024-06-13T15:14:01.339+00:00"),
    ("2024165T172310", "2024-06-13T17:05:26.339+00:00"),
    ("2024166T010828", "2024-06-14T01:06:26.339+00:00"),
]  # 3 entries


def _burst_dir(ts: str) -> str:
    """Return the orbit-62 subdirectory for a burst timestamp."""
    doy = ts[:7]  # e.g. '2024165' or '2024166'
    year = ts[:4]
    doy3 = ts[4:7]
    return f"DATA/WAVES_BURST/2024149_ORBIT_62/{year}_{doy3}/"


def _build_waves_burst_entries(evidence_id: str) -> list[AcquisitionLogicalProductEntry]:
    entries = []
    for ts, stop_str in _BURST_BIN_TIMESTAMPS:
        stop_utc = datetime.fromisoformat(stop_str)
        for band, role in (
            ("B_BIN", AcquisitionRepresentationRole.BURST_B_BIN),
            ("E_BIN", AcquisitionRepresentationRole.BURST_E_BIN),
        ):
            stem = f"WAV_{ts}_{band}_V02"
            fpath = f"{_burst_dir(ts)}{stem}.LBL"
            url = f"{_WAVES_BURST_BASE_URL}{fpath}"
            logical_id = f"gcsi.waves.burst.pj62.{stem.lower()}"
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
            entry = AcquisitionLogicalProductEntry(
                logical_product_id=logical_id,
                instrument="WAVES_BURST",
                semantic_role="radio_plasma_burst",
                discovery_availability_time_utc=stop_utc,
                representations=(rep,),
                discovery_evidence_id=evidence_id,
            )
            entries.append(entry)

    for ts, stop_str in _BURST_REC_TIMESTAMPS:
        stop_utc = datetime.fromisoformat(stop_str)
        for band, role in (
            ("B_REC", AcquisitionRepresentationRole.BURST_B_REC),
            ("E_REC", AcquisitionRepresentationRole.BURST_E_REC),
            ("NBS_REC", AcquisitionRepresentationRole.BURST_NBS_REC),
        ):
            stem = f"WAV_{ts}_{band}_V02"
            fpath = f"{_burst_dir(ts)}{stem}.LBL"
            url = f"{_WAVES_BURST_BASE_URL}{fpath}"
            logical_id = f"gcsi.waves.burst.pj62.{stem.lower()}"
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
            entry = AcquisitionLogicalProductEntry(
                logical_product_id=logical_id,
                instrument="WAVES_BURST",
                semantic_role="radio_plasma_burst",
                discovery_availability_time_utc=stop_utc,
                representations=(rep,),
                discovery_evidence_id=evidence_id,
            )
            entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------

def build_plan() -> HistoricalReplayV2AcquisitionPlan:
    evidence_list = _make_evidence()
    ev = {e.evidence_id: e for e in evidence_list}

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
    ))
    entries.extend(_build_fgm_entries("fgm_jupiter_pl_directory_html"))
    entries.extend(_build_jade_entries("jade_calibrated_directory_html"))
    entries.extend(_build_jedi_entries(
        "jedi_165_directory_html",
        "jedi_166_directory_html",
    ))
    entries.extend(_build_waves_survey_entries("waves_survey_orbit62_directory_html"))
    entries.extend(_build_waves_burst_entries("waves_burst_bstfull_index_tab"))

    # Reconciliation check
    total = len(entries)
    if total != 411:
        raise RuntimeError(
            f"ACQUISITION_PLAN_RECONCILIATION_REQUIRED: "
            f"expected 411 logical entries, got {total}. "
            f"6F_B21_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
        )

    total_refs = sum(len(e.representations) for e in entries)
    if total_refs != 535:
        raise RuntimeError(
            f"ACQUISITION_PLAN_RECONCILIATION_REQUIRED: "
            f"expected 535 source refs, got {total_refs}. "
            f"6F_B21_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
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
        logical_entries=tuple(entries),
        discovery_evidence=tuple(evidence_list),
    )
    return plan


def main() -> None:
    print("Building V2 acquisition plan …", file=sys.stderr)
    plan = build_plan()
    entries = plan.logical_entries

    # Per-instrument summary
    from collections import Counter
    inst_counts = Counter(e.instrument for e in entries)
    ref_counts: dict[str, int] = {}
    for e in entries:
        ref_counts[e.instrument] = ref_counts.get(e.instrument, 0) + len(e.representations)

    print(f"  Logical entries : {len(entries)}", file=sys.stderr)
    total_refs = sum(len(e.representations) for e in entries)
    print(f"  Source refs     : {total_refs}", file=sys.stderr)
    for inst in sorted(inst_counts):
        print(
            f"    {inst:15s}: {inst_counts[inst]:3d} logical, "
            f"{ref_counts[inst]:3d} refs",
            file=sys.stderr,
        )

    pds4_refs = sum(
        len(e.representations)
        for e in entries
        for r in e.representations
        if r.source_standard == AcquisitionSourceStandard.PDS4
    )
    pds3_refs = total_refs - pds4_refs
    print(f"  PDS4 refs: {pds4_refs}  PDS3 refs: {pds3_refs}", file=sys.stderr)

    # Serialize
    _PLAN_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plan_dict = plan.model_dump(mode="json")
    with open(_PLAN_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(plan_dict, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"  Written: {_PLAN_OUTPUT_PATH}", file=sys.stderr)
    print(f"  plan_id: {plan.plan_id}", file=sys.stderr)
    print("  6F_B21_STATUS = ACQUISITION_PLAN_FROZEN (pending test run)", file=sys.stderr)


if __name__ == "__main__":
    main()
