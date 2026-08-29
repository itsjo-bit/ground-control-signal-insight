"""Script to generate the updated discovery evidence sidecar with all normalized extractions."""
import json
import hashlib
import sys
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SIDECAR_PATH = REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_discovery_evidence.json"

d = json.load(open(SIDECAR_PATH))

# ---- JIRAM rows ----
JIRAM_IMG_TIMES = [
    "090046", "090117", "090147", "090218", "090248", "090319", "090349",
    "090420", "090450", "090652", "090722", "090753", "090823", "090854",
    "090924", "090955", "091156", "091227", "091257", "091328", "091359",
    "091429", "091500", "091701", "091732", "091802", "091833", "091903",
    "091934", "092004", "092206", "092236", "092307", "092337", "092408",
    "092438", "092509", "092711", "092741", "092812", "092842", "092913",
    "092943", "093014", "093215", "093246", "093316", "093347", "093417",
    "093448", "093518",
]  # 51

JIRAM_SPE_TIMES = [
    "090048", "090119", "090149", "090220", "090250", "090321", "090351",
    "090422", "090452", "090654", "090724", "090755", "090825", "090856",
    "090926", "090957", "091158", "091229", "091259", "091330", "091401",
    "091431", "091502", "091703", "091734", "091804", "091835", "091905",
    "091936", "092006", "092208", "092238", "092309", "092339", "092410",
    "092440", "092511", "092713", "092743", "092814", "092844", "092915",
    "092945", "093016", "093217", "093248", "093318", "093349", "093419",
    "093450", "093520",
]  # 51

jiram_rows = []
for ts in JIRAM_IMG_TIMES:
    stem = f"JIR_IMG_RDR_2024166T{ts}_V01"
    jiram_rows.append({"filename": f"{stem}.xml", "family": "IMG", "hhmmss": ts})
for ts in JIRAM_SPE_TIMES:
    stem = f"JIR_SPE_RDR_2024166T{ts}_V01"
    jiram_rows.append({"filename": f"{stem}.xml", "family": "SPE", "hhmmss": ts})
assert len(jiram_rows) == 102, f"JIRAM: {len(jiram_rows)}"

# ---- MWR rows ----
MWR_IRDR_165 = {10: "R04120", 11: "R06672", 12: "R30000", 13: "R30008", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944"}
MWR_IRDR_166 = {0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120"}
MWR_GRDR_165 = {10: "R04120", 11: "R06672", 12: "R30000", 13: "R30000", 14: "R30000",
    15: "R30000", 16: "R30000", 17: "R30000", 18: "R30000", 19: "R30000",
    20: "R30000", 21: "R27308", 22: "R04112", 23: "R03944"}
MWR_GRDR_166 = {0: "R04112", 1: "R04120", 2: "R04112", 3: "R04112", 4: "R04120",
    5: "R04112", 6: "R04112", 7: "R04112", 8: "R04120"}

mwr_rows = []
for hour, code in sorted(MWR_IRDR_165.items()):
    fname_stem = f"MWR62RI2024165{hour:02d}0000_{code}_V04"
    mwr_rows.append({"filename": fname_stem, "product_type": "IRDR", "doy": 165, "hour": hour, "code": code})
for hour, code in sorted(MWR_IRDR_166.items()):
    fname_stem = f"MWR62RI2024166{hour:02d}0000_{code}_V04"
    mwr_rows.append({"filename": fname_stem, "product_type": "IRDR", "doy": 166, "hour": hour, "code": code})
for hour, code in sorted(MWR_GRDR_165.items()):
    fname_stem = f"MWR62RG2024165{hour:02d}0000_{code}_V04"
    mwr_rows.append({"filename": fname_stem, "product_type": "GRDR", "doy": 165, "hour": hour, "code": code})
for hour, code in sorted(MWR_GRDR_166.items()):
    fname_stem = f"MWR62RG2024166{hour:02d}0000_{code}_V04"
    mwr_rows.append({"filename": fname_stem, "product_type": "GRDR", "doy": 166, "hour": hour, "code": code})
assert len(mwr_rows) == 46, f"MWR: {len(mwr_rows)}"

# ---- UVS rows ----
UVS_PRODUCTS = [
    ("S01", "771573735", "2024165", "P62OBS"),
    ("S02", "771573735", "2024165", "P62OBS"),
    ("S03", "771573735", "2024165", "P62OBS"),
    ("S04", "771573735", "2024165", "P62OBS"),
    ("S05", "771573735", "2024165", "P62OBS"),
    ("S01", "771613347", "2024166", "P62SY1"),
    ("S02", "771613347", "2024166", "P62SY1"),
    ("S03", "771613347", "2024166", "P62SY1"),
]
uvs_rows = []
for sensor, sclk, doy_str, obs_type in UVS_PRODUCTS:
    stem = f"UVS_{sensor}_{sclk}_{doy_str}_{obs_type}_V01"
    uvs_rows.append({"filename": stem, "sensor": sensor, "sclk": sclk, "doy_str": doy_str, "obs_type": obs_type})
assert len(uvs_rows) == 8, f"UVS: {len(uvs_rows)}"

# ---- FGM rows (2 selected, 0 excluded -> 2 total discovered/selected) ----
fgm_rows = [
    {"logical_stem": "fgm_jno_l3_2024165pl", "product_id": "FGM_JNO_L3_2024165PL",
     "lbl_filename": "fgm_jno_l3_2024165pl_v02.lbl", "selected": True},
    {"logical_stem": "fgm_jno_l3_2024165pl_pj62", "product_id": "FGM_JNO_L3_2024165PL_PJ62",
     "lbl_filename": "fgm_jno_l3_2024165pl_pj62_v02.lbl", "selected": True},
]
assert len(fgm_rows) == 2, f"FGM: {len(fgm_rows)}"

# ---- JADE rows (8 eligible + 4 excluded = 12 discovered) ----
jade_rows = [
    {"product_id": "JAD_L30_LRS_ION_2024165_V01", "path_suffix": "2024/165/JAD_L30_LRS_ION_2024165_V01.LBL", "doy": 165, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_LRS_ELC_2024165_V01", "path_suffix": "2024/165/JAD_L30_LRS_ELC_2024165_V01.LBL", "doy": 165, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_HRS_ION_2024165_V01", "path_suffix": "2024/165/JAD_L30_HRS_ION_2024165_V01.LBL", "doy": 165, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_HRS_ELC_2024165_V01", "path_suffix": "2024/165/JAD_L30_HRS_ELC_2024165_V01.LBL", "doy": 165, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_LRS_ION_2024166_V01", "path_suffix": "2024/166/JAD_L30_LRS_ION_2024166_V01.LBL", "doy": 166, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_LRS_ELC_2024166_V01", "path_suffix": "2024/166/JAD_L30_LRS_ELC_2024166_V01.LBL", "doy": 166, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_HRS_ION_2024166_V01", "path_suffix": "2024/166/JAD_L30_HRS_ION_2024166_V01.LBL", "doy": 166, "inclusion": "ELIGIBLE"},
    {"product_id": "JAD_L30_HRS_ELC_2024166_V01", "path_suffix": "2024/166/JAD_L30_HRS_ELC_2024166_V01.LBL", "doy": 166, "inclusion": "ELIGIBLE"},
    # 4 excluded: survey/primary label variants that are NOT individual observation labels
    {"product_id": "JAD_L30_LRS_ION_PRI_2024165_V01", "path_suffix": "2024/165/JAD_L30_LRS_ION_PRI_2024165_V01.LBL", "doy": 165, "inclusion": "EXCLUDED"},
    {"product_id": "JAD_L30_LRS_ELC_PRI_2024165_V01", "path_suffix": "2024/165/JAD_L30_LRS_ELC_PRI_2024165_V01.LBL", "doy": 165, "inclusion": "EXCLUDED"},
    {"product_id": "JAD_L30_LRS_ION_PRI_2024166_V01", "path_suffix": "2024/166/JAD_L30_LRS_ION_PRI_2024166_V01.LBL", "doy": 166, "inclusion": "EXCLUDED"},
    {"product_id": "JAD_L30_LRS_ELC_PRI_2024166_V01", "path_suffix": "2024/166/JAD_L30_LRS_ELC_PRI_2024166_V01.LBL", "doy": 166, "inclusion": "EXCLUDED"},
]
assert len(jade_rows) == 12, f"JADE: {len(jade_rows)}"
assert sum(1 for r in jade_rows if r["inclusion"] == "ELIGIBLE") == 8
assert sum(1 for r in jade_rows if r["inclusion"] == "EXCLUDED") == 4

# ---- JEDI rows ----
jedi_165_rows = [
    {"product_id": "JED_090_HIERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_090_HIERSISP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_090_LOERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_180_HIERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_180_HIERSISP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_180_LOERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_HIERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_HIERSISP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_HIERSTOFXER_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_HIERSTOFXPHR_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_LOERSESP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_LOERSISP_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_NONPTOFXER_CDR_2024165_V04", "doy": 165},
    {"product_id": "JED_270_NONPTOFXPHR_CDR_2024165_V04", "doy": 165},
]
jedi_166_rows = [
    {"product_id": "JED_090_HIERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_090_HIERSISP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_090_LOERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_090_LOERSISP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_180_HIERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_180_HIERSISP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_180_LOERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_180_LOERSISP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_HIERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_HIERSTOFXER_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_HIERSTOFXPHR_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_LOERSESP_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_NONPTOFXER_CDR_2024166_V04", "doy": 166},
    {"product_id": "JED_270_NONPTOFXPHR_CDR_2024166_V04", "doy": 166},
]
assert len(jedi_165_rows) == 14, f"JEDI 165: {len(jedi_165_rows)}"
assert len(jedi_166_rows) == 14, f"JEDI 166: {len(jedi_166_rows)}"

# ---- WAVES Survey rows (4 discovered: 2 eligible + 2 excluded) ----
waves_survey_rows = [
    {"stem": "WAV_2024165T000000_B_V01", "band": "b", "inclusion": "ELIGIBLE"},
    {"stem": "WAV_2024165T000000_E_V01", "band": "e", "inclusion": "ELIGIBLE"},
    {"stem": "WAV_2024166T000000_B_V01", "band": "b", "inclusion": "EXCLUDED"},
    {"stem": "WAV_2024166T000000_E_V01", "band": "e", "inclusion": "EXCLUDED"},
]
assert len(waves_survey_rows) == 4
assert sum(1 for r in waves_survey_rows if r["inclusion"] == "ELIGIBLE") == 2
assert sum(1 for r in waves_survey_rows if r["inclusion"] == "EXCLUDED") == 2

# ---- JunoCam ALL rows (only eligible stored + partition summary) ----
jc_all_rows = []
for row in d['normalized_extractions']['junocam_index_tab_orbit62_eligible']:
    r = dict(row)
    r['partition'] = 'ELIGIBLE'
    jc_all_rows.append(r)
assert len(jc_all_rows) == 124

# ---- WAVES Burst ALL rows (only eligible stored + partition summary) ----
wb_all_rows = []
for row in d['normalized_extractions']['waves_burst_index_tab_orbit62_eligible']:
    r = dict(row)
    r['partition'] = 'ELIGIBLE'
    wb_all_rows.append(r)
assert len(wb_all_rows) == 91

# ---- Partition summaries ----
partition_summaries = {
    "junocam": {
        "instrument": "JUNOCAM",
        "source_evidence_id": "junocam_jnojnc_0029_index_tab",
        "total_orbit62_rows": 426,
        "pre_rows": 112,
        "eligible_rows": 248,
        "post_rows": 66,
        "note": "eligible_rows=248 = 124 EDR + 124 RDR = 124 logical observations. "
                "PRE_LOGICAL=56 ELIGIBLE_LOGICAL=124 POST_LOGICAL=33. "
                "B21_RAW_ROW_LEDGER_SUPERSEDED=YES HISTORICAL_213_LOGICAL_OBSERVATION_LEDGER=CONFIRMED."
    },
    "waves_burst": {
        "instrument": "WAVES_BURST",
        "source_evidence_id": "waves_burst_bstfull_index_tab",
        "total_orbit62_rows": 282,
        "pre_rows": 175,
        "eligible_rows": 91,
        "post_rows": 16,
        "eligible_families": {"B_BIN": 41, "E_BIN": 41, "B_REC": 3, "E_REC": 3, "NBS_REC": 3},
    }
}

# ---- Build new normalized_extractions ----
new_extractions = {
    "fgm_peri62_filenames": fgm_rows,
    "jade_orbit62_labels": jade_rows,
    "jedi_165_labels": jedi_165_rows,
    "jedi_166_labels": jedi_166_rows,
    "jiram_orbit62_filenames": jiram_rows,
    "junocam_index_tab_orbit62_all": jc_all_rows,
    "mwr_orbit62_filenames": mwr_rows,
    "partition_summaries": partition_summaries,
    "uvs_orbit62_filenames": uvs_rows,
    "waves_burst_index_tab_orbit62_all": wb_all_rows,
    "waves_survey_orbit62_labels": waves_survey_rows,
}

# ---- Build new sidecar (without artifact_id first) ----
new_sidecar = {
    "schema": d["schema"],
    "schema_version": d["schema_version"],
    "replay_id": d["replay_id"],
    "discovery_evidence": d["discovery_evidence"],
    "normalized_extractions": new_extractions,
}

# Compute artifact_id
PREFIX = "gcsi.pj62_discovery_evidence_sidecar:v1:"
canonical = {
    "discovery_evidence": sorted(new_sidecar["discovery_evidence"], key=lambda x: x["evidence_id"]),
    "normalized_extractions": {k: new_sidecar["normalized_extractions"][k] for k in sorted(new_sidecar["normalized_extractions"].keys())},
    "replay_id": new_sidecar["replay_id"],
    "schema": new_sidecar["schema"],
    "schema_version": new_sidecar["schema_version"],
}
payload = PREFIX + json.dumps(canonical, separators=(",", ":"), sort_keys=True)
artifact_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()

new_sidecar["artifact_id"] = artifact_id
print(f"New artifact_id: {artifact_id}")

# Write new sidecar
with open(SIDECAR_PATH, "w", encoding="utf-8") as f:
    json.dump(new_sidecar, f, indent=2, sort_keys=True)
    f.write("\n")

print(f"Written: {SIDECAR_PATH}")
print(f"Total extraction keys: {sorted(new_extractions.keys())}")
print(f"JIRAM: {len(jiram_rows)}, MWR: {len(mwr_rows)}, UVS: {len(uvs_rows)}, FGM: {len(fgm_rows)}")
print(f"JADE: {len(jade_rows)}, JEDI: {len(jedi_165_rows)+len(jedi_166_rows)}, WS: {len(waves_survey_rows)}")
print(f"JunoCam all: {len(jc_all_rows)}, WB all: {len(wb_all_rows)}")
