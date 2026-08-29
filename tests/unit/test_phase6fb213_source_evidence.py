"""GCSI Phase 6F-B2.1.3 — Source-Derived Acquisition Evidence Tests.

B2.1.3-specific test suite. All tests are OFFLINE. No live PDS requests.

Covers spec requirements:
 §31 Source-to-sidecar extractor tests (synthetic/fixture-based)
 §32 Full JunoCam test matrix from raw rows (426 → PRE/ELIGIBLE/POST)
 §33 Full WAVES Burst test matrix from raw rows (282 → PRE/ELIGIBLE/POST + families)
 §34 Directory family test matrix (JIRAM/MWR/UVS/FGM/JADE/JEDI/WAVES Survey)
 §35 Sidecar mutation/extra field rejection (evil_extra test)
 §36 Artifact-id tests (missing/malformed/wrong/mutation/reorder)
 §37 Plan-binding tests (missing/malformed/mismatch/bound loader)
 §38 Exact URL tests
 §39 JADE special acceptance test (no fabricated B2.1.2 naming)
"""

from __future__ import annotations

import copy
import json
import pathlib
import re
import tempfile
from unittest.mock import patch

import pytest

from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_UTC,
    DiscoveryEvidence,
    HistoricalReplayV2AcquisitionPlan,
    TemporalEvidenceStatus,
    _compute_plan_id,
    load_acquisition_plan,
)
from backend.app.mission_sources.v2_acquisition_plan_builder import (
    BoundAcquisitionPlan,
    _PLAN_OUTPUT_PATH,
    _SIDECAR_ALLOWED_DIR,
    _SIDECAR_PATH,
    _load_sidecar,
    build_plan,
    load_bound_v2_acquisition_plan,
)
from backend.app.mission_sources.v2_sidecar_models import (
    DiscoveryPartition,
    FgmDiscoveryLabel,
    JadeDiscoveryLabel,
    JadeInclusion,
    JediDiscoveryLabel,
    JiramDiscoveryLabel,
    JiramFamily,
    JunoCamDiscoveryRow,
    JunoCamPartition,
    JunoCamRepresentation,
    MwrDiscoveryLabel,
    MwrInclusion,
    MwrProductType,
    UvsDiscoveryLabel,
    WavesBurstDiscoveryRow,
    WavesBurstFamily,
    WavesBurstPartition,
    WavesSurveyDiscoveryLabel,
    WavesSurveyInclusion,
    compute_sidecar_artifact_id,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SIDECAR_FILE = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_discovery_evidence.json"
_PLAN_FILE = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_plan.json"


@pytest.fixture(scope="session")
def sidecar() -> dict:
    return json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def plan() -> HistoricalReplayV2AcquisitionPlan:
    return build_plan()


@pytest.fixture(scope="session")
def all_entries(plan):
    return plan.logical_entries


@pytest.fixture(scope="session")
def all_refs(all_entries):
    return [r for e in all_entries for r in e.representations]


# ===========================================================================
# §31 — Source-to-sidecar extractor tests (synthetic fixtures)
# ===========================================================================


class TestJiramExtractorSynthetic:
    """§31: Synthetic fixtures for JIRAM extractor."""

    MINIMAL_JIRAM_HTML = (
        b'<html><body>'
        b'<a href="JIR_IMG_RDR_2024166T090046_V01.xml">img</a>'
        b'<a href="JIR_SPE_RDR_2024166T090048_V01.xml">spe</a>'
        b'</body></html>'
    )

    def test_jiram_extractor_extracts_img_and_spe(self):
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        rows = _extract_jiram(self.MINIMAL_JIRAM_HTML, "test_ev")
        assert len(rows) == 2
        fams = {r["family"] for r in rows}
        assert fams == {"IMG", "SPE"}

    def test_jiram_extractor_deduplicates(self):
        """Duplicate hrefs must produce only one row."""
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        html = (
            b'<html><body>'
            b'<a href="JIR_IMG_RDR_2024166T090046_V01.xml">img</a>'
            b'<a href="JIR_IMG_RDR_2024166T090046_V01.xml">dup</a>'
            b'</body></html>'
        )
        rows = _extract_jiram(html, "test_ev")
        assert len(rows) == 1

    def test_jiram_extractor_sets_evidence_id(self):
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        rows = _extract_jiram(self.MINIMAL_JIRAM_HTML, "my_evidence")
        assert all(r["discovery_evidence_id"] == "my_evidence" for r in rows)

    def test_jiram_extractor_output_deterministic(self):
        """Running extractor twice on same bytes produces same result."""
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        r1 = _extract_jiram(self.MINIMAL_JIRAM_HTML, "ev")
        r2 = _extract_jiram(self.MINIMAL_JIRAM_HTML, "ev")
        assert r1 == r2

    def test_jiram_extractor_no_orbit62_html_returns_empty(self):
        """HTML with no JIR_ hrefs returns empty list."""
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        rows = _extract_jiram(b"<html><body>nothing</body></html>", "ev")
        assert rows == []

    def test_jiram_row_validates_as_model(self):
        """Extracted rows must validate as JiramDiscoveryLabel."""
        from scripts.refresh_v2_discovery_evidence import _extract_jiram
        rows = _extract_jiram(self.MINIMAL_JIRAM_HTML, "test_ev")
        parsed = [JiramDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 2

    def test_jiram_family_img_in_filename(self):
        """IMG family must match IMG in filename (cross-field)."""
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_SPE_RDR_2024166T090048_V01.xml",
                family=JiramFamily.IMG,  # mismatch!
                hhmmss="090048",
                relative_label_path="JIR_SPE_RDR_2024166T090048_V01.xml",
                discovery_evidence_id="ev",
            )


class TestMwrExtractorSynthetic:
    """§31: Synthetic fixtures for MWR extractor."""

    MINIMAL_MWR_IRDR_165_HTML = (
        b'<html><body>'
        b'<a href="MWR62RI2024165100000_R04120_V04.xml">irdr10</a>'
        b'<a href="MWR62RI2024165110000_R06672_V04.xml">irdr11</a>'
        b'</body></html>'
    )

    def test_mwr_extractor_extracts_irdr_rows(self):
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        rows = _extract_mwr(self.MINIMAL_MWR_IRDR_165_HTML, "ev", "IRDR", 165)
        assert len(rows) == 2
        assert all(r["product_type"] == "IRDR" for r in rows)
        assert all(r["doy"] == 165 for r in rows)

    def test_mwr_extractor_sets_inclusion(self):
        """DOY165 hours 10+ are ELIGIBLE; hours 0-9 are EXCLUDED."""
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        html_all_hours = b'<html><body>'
        for h in range(24):
            html_all_hours += f'<a href="MWR62RI2024165{h:02d}0000_R04112_V04.xml">h{h}</a>'.encode()
        html_all_hours += b'</body></html>'
        rows = _extract_mwr(html_all_hours, "ev", "IRDR", 165)
        eligible = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        excluded = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(eligible) == 14  # hours 10-23
        assert len(excluded) == 10  # hours 0-9

    def test_mwr_extractor_doy166_inclusion(self):
        """DOY166 hours 0-8 are ELIGIBLE; hours 9+ are EXCLUDED."""
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        html_all_hours = b'<html><body>'
        for h in range(24):
            html_all_hours += f'<a href="MWR62RI2024166{h:02d}0000_R04112_V04.xml">h{h}</a>'.encode()
        html_all_hours += b'</body></html>'
        rows = _extract_mwr(html_all_hours, "ev", "IRDR", 166)
        eligible = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        excluded = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(eligible) == 9   # hours 0-8
        assert len(excluded) == 15  # hours 9-23

    def test_mwr_extractor_deduplicates(self):
        """Duplicate hrefs produce only one row."""
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        html = (
            b'<html><body>'
            b'<a href="MWR62RI2024165100000_R04120_V04.xml">a</a>'
            b'<a href="MWR62RI2024165100000_R04120_V04.xml">dup</a>'
            b'</body></html>'
        )
        rows = _extract_mwr(html, "ev", "IRDR", 165)
        assert len(rows) == 1

    def test_mwr_extractor_output_deterministic(self):
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        r1 = _extract_mwr(self.MINIMAL_MWR_IRDR_165_HTML, "ev", "IRDR", 165)
        r2 = _extract_mwr(self.MINIMAL_MWR_IRDR_165_HTML, "ev", "IRDR", 165)
        assert r1 == r2

    def test_mwr_row_validates_as_model(self):
        from scripts.refresh_v2_discovery_evidence import _extract_mwr
        rows = _extract_mwr(self.MINIMAL_MWR_IRDR_165_HTML, "ev", "IRDR", 165)
        parsed = [MwrDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 2

    def test_mwr_cross_field_mismatch_rejected(self):
        """product_type IRDR must match RI in filename."""
        with pytest.raises(Exception):
            MwrDiscoveryLabel(
                filename="MWR62RG2024165100000_R04120_V04",  # RG = GRDR, not IRDR
                product_type=MwrProductType.IRDR,  # mismatch!
                doy=165, hour=10, code="R04120",
                relative_label_path="IRDR/2024/2024165/MWR62RG2024165100000_R04120_V04.xml",
                inclusion=MwrInclusion.ELIGIBLE,
                discovery_evidence_id="ev",
            )


class TestWavesBurstExtractorSynthetic:
    """§31: Synthetic fixtures for WAVES Burst extractor."""

    # Minimal CSV with header + 3 orbit-62 rows
    MINIMAL_WB_CSV = (
        b'"VOLUME_ID  ","SID   ","DATA_SET_ID                      ","PRODUCT_ID                 ","START_TIME           ","STOP_TIME            ","FILE_SPECIFICATION_NAME                                       ","CR_DATE ","PRODUCT_LABEL_MD5CHECKSUM       "\n'
        b'"JNOWAV_1000","BURST ","JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0","WAV_2024165T145507_B_BIN   ",2024-06-13T14:55:07.565,2024-06-13T15:14:01.339,"DATA/WAVES_BURST/2024149_ORBIT_62/WAV_2024165T145507_B_BIN_V01.LBL       ","2024-10-27","abcd1234"\n'
        b'"JNOWAV_1000","BURST ","JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0","WAV_2024165T145507_E_BIN   ",2024-06-13T14:55:07.565,2024-06-13T15:14:01.339,"DATA/WAVES_BURST/2024149_ORBIT_62/WAV_2024165T145507_E_BIN_V01.LBL       ","2024-10-27","efgh5678"\n'
        b'"JNOWAV_1000","BURST ","JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0","WAV_2011221T165656_B_REC   ",2011-08-09T16:56:56.796,2011-08-09T16:59:53.196,"DATA/WAVES_BURST/2011220_INNER_CRUISE_1/WAV_2011221T165656_B_REC_V02.LBL  ","2019-05-09","aaaa0000"\n'
    )

    def test_wb_extractor_filters_orbit_62(self):
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        rows = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        # Only 2 rows have ORBIT_62 in file_spec
        assert len(rows) == 2

    def test_wb_extractor_extracts_family(self):
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        rows = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        families = {r["family"] for r in rows}
        assert "B_BIN" in families
        assert "E_BIN" in families

    def test_wb_extractor_classifies_partition(self):
        """Rows within the window are ELIGIBLE."""
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        rows = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        assert all(r["partition"] == "ELIGIBLE" for r in rows)

    def test_wb_extractor_deduplicates(self):
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        dup_csv = self.MINIMAL_WB_CSV + (
            b'"JNOWAV_1000","BURST ","JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0","WAV_2024165T145507_B_BIN   ",2024-06-13T14:55:07.565,2024-06-13T15:14:01.339,"DATA/WAVES_BURST/2024149_ORBIT_62/WAV_2024165T145507_B_BIN_V01.LBL       ","2024-10-27","abcd1234"\n'
        )
        rows = _extract_waves_burst_index_tab(dup_csv, "ev")
        assert len(rows) == 2  # no duplicate

    def test_wb_extractor_output_deterministic(self):
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        r1 = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        r2 = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        assert r1 == r2

    def test_wb_row_validates_as_model(self):
        from scripts.refresh_v2_discovery_evidence import _extract_waves_burst_index_tab
        rows = _extract_waves_burst_index_tab(self.MINIMAL_WB_CSV, "ev")
        parsed = [WavesBurstDiscoveryRow.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 2


class TestJunoCamExtractorSynthetic:
    """§31: Synthetic fixtures for JunoCam INDEX.TAB extractor (CSV format)."""

    # Minimal CSV with 4 rows: 2 EDR + 2 RDR (1 PRE + 1 ELIGIBLE each)
    MINIMAL_JNC_CSV = (
        b'"JNOJNC_00029","JUNOCAM-EDR","JUNO-J-JUNOCAM-2-EDR-L0-V1.0 ","JNCE_2024084_59R00001_V01",2024-03-24T02:00:00.000,2024-03-24T02:00:01.000,"1","obs","1.0 <km>","1.0 <km>","0.0","0.0","JUPITER","DATA/EDR/JUPITER/ORBIT_62/JNCE_2024084_59R00001_V01.LBL","2024-09-18","abc123"\n'
        b'"JNOJNC_00029","JUNOCAM-RDR","JUNO-J-JUNOCAM-2-RDR-L0-V1.0 ","JNCR_2024084_59R00001_V01",2024-03-24T02:00:00.000,2024-03-24T02:00:01.000,"1","obs","1.0 <km>","1.0 <km>","0.0","0.0","JUPITER","DATA/RDR/JUPITER/ORBIT_62/JNCR_2024084_59R00001_V01.LBL","2024-09-18","def456"\n'
        b'"JNOJNC_00029","JUNOCAM-EDR","JUNO-J-JUNOCAM-2-EDR-L0-V1.0 ","JNCE_2024165_62C00001_V01",2024-06-13T10:00:04.000,2024-06-13T10:00:08.000,"1","obs","1.0 <km>","1.0 <km>","0.0","0.0","JUPITER","DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00001_V01.LBL","2024-09-18","ghi789"\n'
        b'"JNOJNC_00029","JUNOCAM-RDR","JUNO-J-JUNOCAM-2-RDR-L0-V1.0 ","JNCR_2024165_62C00001_V01",2024-06-13T10:00:04.000,2024-06-13T10:00:08.000,"1","obs","1.0 <km>","1.0 <km>","0.0","0.0","JUPITER","DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00001_V01.LBL","2024-09-18","jkl012"\n'
    )

    def test_jnc_extractor_parses_csv(self):
        from scripts.refresh_v2_discovery_evidence import _extract_junocam_index_tab
        rows = _extract_junocam_index_tab(self.MINIMAL_JNC_CSV, 502, "ev")
        # All 4 rows have ORBIT_62 in file_spec
        assert len(rows) == 4

    def test_jnc_extractor_identifies_edr_rdr(self):
        from scripts.refresh_v2_discovery_evidence import _extract_junocam_index_tab
        rows = _extract_junocam_index_tab(self.MINIMAL_JNC_CSV, 502, "ev")
        edr = [r for r in rows if r["representation_kind"] == "EDR"]
        rdr = [r for r in rows if r["representation_kind"] == "RDR"]
        assert len(edr) == 2
        assert len(rdr) == 2

    def test_jnc_extractor_partition(self):
        """First two rows are PRE, last two are ELIGIBLE."""
        from scripts.refresh_v2_discovery_evidence import _extract_junocam_index_tab
        rows = _extract_junocam_index_tab(self.MINIMAL_JNC_CSV, 502, "ev")
        pre = [r for r in rows if r["partition"] == "PRE"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        assert len(pre) == 2
        assert len(elig) == 2

    def test_jnc_extractor_output_deterministic(self):
        from scripts.refresh_v2_discovery_evidence import _extract_junocam_index_tab
        r1 = _extract_junocam_index_tab(self.MINIMAL_JNC_CSV, 502, "ev")
        r2 = _extract_junocam_index_tab(self.MINIMAL_JNC_CSV, 502, "ev")
        assert r1 == r2


class TestPathValidation:
    """§31: Invalid relative paths must be rejected."""

    def test_empty_path_rejected(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="",  # empty
                discovery_evidence_id="ev",
            )

    def test_absolute_path_rejected(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="/JIR_IMG_RDR_2024166T090046_V01.xml",  # absolute
                discovery_evidence_id="ev",
            )

    def test_dotdot_path_rejected(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="../JIR_IMG_RDR_2024166T090046_V01.xml",  # traversal
                discovery_evidence_id="ev",
            )

    def test_backslash_path_rejected(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path=r"dir\JIR_IMG_RDR_2024166T090046_V01.xml",  # backslash
                discovery_evidence_id="ev",
            )

    def test_query_fragment_path_rejected(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="JIR_IMG_RDR_2024166T090046_V01.xml?foo=bar",  # query
                discovery_evidence_id="ev",
            )

    def test_wrong_extension_rejected(self):
        """JIRAM path must have .xml extension."""
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.lbl",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="JIR_IMG_RDR_2024166T090046_V01.lbl",  # wrong ext
                discovery_evidence_id="ev",
            )

    def test_percent_encoded_traversal_rejected(self):
        """Percent-encoded ./ or ../ must be rejected."""
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG, hhmmss="090046",
                relative_label_path="%2E%2E/JIR_IMG_RDR_2024166T090046_V01.xml",
                discovery_evidence_id="ev",
            )


# ===========================================================================
# §32 — Full JunoCam test matrix from raw rows
# ===========================================================================


class TestJunoCamRawRowMatrix:
    """§32: Full JunoCam test matrix from sidecar raw rows (not just partition_summaries)."""

    def test_junocam_all_rows_is_426(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        assert len(rows) == 426, f"JunoCam all rows: {len(rows)}"

    def test_junocam_pre_rows_is_112(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        pre = [r for r in rows if r["partition"] == "PRE"]
        assert len(pre) == 112, f"JunoCam PRE rows: {len(pre)}"

    def test_junocam_eligible_rows_is_248(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        assert len(elig) == 248, f"JunoCam ELIGIBLE rows: {len(elig)}"

    def test_junocam_post_rows_is_66(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        post = [r for r in rows if r["partition"] == "POST"]
        assert len(post) == 66, f"JunoCam POST rows: {len(post)}"

    def test_junocam_partition_sum_equals_total(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        pre = sum(1 for r in rows if r["partition"] == "PRE")
        elig = sum(1 for r in rows if r["partition"] == "ELIGIBLE")
        post = sum(1 for r in rows if r["partition"] == "POST")
        assert pre + elig + post == len(rows) == 426

    def test_junocam_edr_count_is_213(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        edr = [r for r in rows if r["representation_kind"] == "EDR"]
        assert len(edr) == 213

    def test_junocam_rdr_count_is_213(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        rdr = [r for r in rows if r["representation_kind"] == "RDR"]
        assert len(rdr) == 213

    def test_junocam_logical_partition_pre_is_56(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        pre = sum(1 for r in rows if r["partition"] == "PRE")
        assert pre // 2 == 56

    def test_junocam_logical_partition_eligible_is_124(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        elig = sum(1 for r in rows if r["partition"] == "ELIGIBLE")
        assert elig // 2 == 124

    def test_junocam_logical_partition_post_is_33(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        post = sum(1 for r in rows if r["partition"] == "POST")
        assert post // 2 == 33

    def test_junocam_logical_total_is_213(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        total_logical = len(rows) // 2
        assert total_logical == 213

    def test_junocam_eligible_edr_count_is_124(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        elig_edr = [r for r in rows if r["partition"] == "ELIGIBLE" and r["representation_kind"] == "EDR"]
        assert len(elig_edr) == 124

    def test_junocam_eligible_rdr_count_is_124(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        elig_rdr = [r for r in rows if r["partition"] == "ELIGIBLE" and r["representation_kind"] == "RDR"]
        assert len(elig_rdr) == 124

    def test_junocam_every_observation_key_has_edr_and_rdr(self, sidecar):
        """Every observation_key must have exactly one EDR + one RDR."""
        from collections import defaultdict
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        by_key: dict = defaultdict(set)
        for r in rows:
            by_key[r["observation_key"]].add(r["representation_kind"])
        # Every key must have both EDR and RDR
        for key, kinds in by_key.items():
            assert "EDR" in kinds, f"Observation {key!r} missing EDR"
            assert "RDR" in kinds, f"Observation {key!r} missing RDR"

    def test_junocam_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        # Sample first 10 to avoid slowness
        parsed = [JunoCamDiscoveryRow.model_validate(r, strict=False) for r in rows[:10]]
        assert len(parsed) == 10

    def test_junocam_partition_summary_matches_raw_rows(self, sidecar):
        """Partition summary must equal derivation from raw rows."""
        rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        assert sum(1 for r in rows if r["partition"] == "PRE") == ps["pre_rows"]
        assert sum(1 for r in rows if r["partition"] == "ELIGIBLE") == ps["eligible_rows"]
        assert sum(1 for r in rows if r["partition"] == "POST") == ps["post_rows"]
        assert len(rows) == ps["total_orbit62_rows"]


# ===========================================================================
# §33 — Full WAVES Burst test matrix from raw rows
# ===========================================================================


class TestWavesBurstRawRowMatrix:
    """§33: Full WAVES Burst test matrix from sidecar raw rows."""

    def test_waves_burst_all_rows_is_282(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        assert len(rows) == 282, f"WAVES Burst all rows: {len(rows)}"

    def test_waves_burst_pre_rows_is_175(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        pre = [r for r in rows if r["partition"] == "PRE"]
        assert len(pre) == 175

    def test_waves_burst_eligible_rows_is_91(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        assert len(elig) == 91

    def test_waves_burst_post_rows_is_16(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        post = [r for r in rows if r["partition"] == "POST"]
        assert len(post) == 16

    def test_waves_burst_partition_sum_equals_total(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        pre = sum(1 for r in rows if r["partition"] == "PRE")
        elig = sum(1 for r in rows if r["partition"] == "ELIGIBLE")
        post = sum(1 for r in rows if r["partition"] == "POST")
        assert pre + elig + post == len(rows) == 282

    def test_waves_burst_eligible_b_bin_is_41(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        b_bin = [r for r in elig if r["family"] == "B_BIN"]
        assert len(b_bin) == 41

    def test_waves_burst_eligible_e_bin_is_41(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        e_bin = [r for r in elig if r["family"] == "E_BIN"]
        assert len(e_bin) == 41

    def test_waves_burst_eligible_b_rec_is_3(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        b_rec = [r for r in elig if r["family"] == "B_REC"]
        assert len(b_rec) == 3

    def test_waves_burst_eligible_e_rec_is_3(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        e_rec = [r for r in elig if r["family"] == "E_REC"]
        assert len(e_rec) == 3

    def test_waves_burst_eligible_nbs_rec_is_3(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        nbs = [r for r in elig if r["family"] == "NBS_REC"]
        assert len(nbs) == 3

    def test_waves_burst_eligible_family_total_is_91(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        from collections import Counter
        fam_counts = Counter(r["family"] for r in elig)
        total = sum(fam_counts.values())
        assert total == 91

    def test_waves_burst_partition_summary_matches_raw_rows(self, sidecar):
        """Partition summary must equal derivation from raw rows."""
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert sum(1 for r in rows if r["partition"] == "PRE") == ps["pre_rows"]
        assert sum(1 for r in rows if r["partition"] == "ELIGIBLE") == ps["eligible_rows"]
        assert sum(1 for r in rows if r["partition"] == "POST") == ps["post_rows"]
        assert len(rows) == ps["total_orbit62_rows"]

    def test_waves_burst_eligible_family_counts_match_summary(self, sidecar):
        """Eligible family counts in partition_summaries must match rows."""
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        elig = [r for r in rows if r["partition"] == "ELIGIBLE"]
        from collections import Counter
        fam_counts = Counter(r["family"] for r in elig)
        for fam, count in ps["eligible_families"].items():
            assert fam_counts[fam] == count, f"Family {fam}: expected {count}, got {fam_counts[fam]}"

    def test_waves_burst_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        # Sample first 10 to avoid slowness
        parsed = [WavesBurstDiscoveryRow.model_validate(r, strict=False) for r in rows[:10]]
        assert len(parsed) == 10


# ===========================================================================
# §34 — Directory family test matrix
# ===========================================================================


class TestDirectoryFamilyMatrix:
    """§34: Directory family test matrix for all instruments."""

    def test_jiram_img_count_is_51(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        img = [r for r in rows if r["family"] == "IMG"]
        assert len(img) == 51

    def test_jiram_spe_count_is_51(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        spe = [r for r in rows if r["family"] == "SPE"]
        assert len(spe) == 51

    def test_jiram_total_is_102(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        assert len(rows) == 102

    def test_mwr_irdr_eligible_is_23(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        irdr_elig = [r for r in rows if r["product_type"] == "IRDR" and r.get("inclusion") == "ELIGIBLE"]
        assert len(irdr_elig) == 23

    def test_mwr_grdr_eligible_is_23(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        grdr_elig = [r for r in rows if r["product_type"] == "GRDR" and r.get("inclusion") == "ELIGIBLE"]
        assert len(grdr_elig) == 23

    def test_uvs_p62obs_count_is_5(self, sidecar):
        rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        p62obs = [r for r in rows if r["obs_type"] == "P62OBS"]
        assert len(p62obs) == 5

    def test_uvs_p62sy1_count_is_3(self, sidecar):
        rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        p62sy1 = [r for r in rows if r["obs_type"] == "P62SY1"]
        assert len(p62sy1) == 3

    def test_uvs_total_is_8(self, sidecar):
        rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        assert len(rows) == 8

    def test_fgm_selected_count_is_2(self, sidecar):
        rows = sidecar["normalized_extractions"]["fgm_peri62_filenames"]
        selected = [r for r in rows if r["selected"]]
        assert len(selected) == 2

    def test_jade_total_discovered_is_12(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        assert len(rows) == 12

    def test_jade_selected_eligible_is_8(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        elig = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        assert len(elig) == 8

    def test_jade_excluded_is_4(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        excl = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(excl) == 4

    def test_jedi_total_is_28(self, sidecar):
        r165 = sidecar["normalized_extractions"]["jedi_165_labels"]
        r166 = sidecar["normalized_extractions"]["jedi_166_labels"]
        assert len(r165) + len(r166) == 28

    def test_waves_survey_relevant_is_4(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        assert len(rows) == 4

    def test_waves_survey_selected_is_2(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        elig = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        assert len(elig) == 2

    def test_waves_survey_excluded_is_2(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        excl = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(excl) == 2


# ===========================================================================
# §35 — Sidecar mutation/extra field rejection test
# ===========================================================================


class TestSidecarMutationExtraFieldRejection:
    """§35: Evil extra field in nested row must be rejected even with correct artifact_id."""

    def test_extra_field_in_jiram_row_rejected(self):
        """A JIRAM row with extra field 'evil_extra' must be rejected by ACTUAL production _load_sidecar.

        B2.1.4 §15: Call the actual production sidecar loader (not just the row model directly).
        The production loader must reject the artifact because of the nested extra field.
        """
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(sidecar_data)

        # Inject evil_extra into first JIRAM row
        mutated["normalized_extractions"]["jiram_orbit62_filenames"][0]["evil_extra"] = "x"

        # Recompute artifact_id for the mutated content (valid artifact_id)
        mutated["artifact_id"] = compute_sidecar_artifact_id(mutated)

        # Save to temp file and attempt to load via production _load_sidecar
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir) / "mutated_sidecar.json"
            tmp.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", tmp):
                with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                           pathlib.Path(tmpdir).resolve()):
                    # B2.1.4: _load_sidecar MUST reject due to nested extra field in JIRAM row.
                    # The production typed model validation (extra="forbid") catches this.
                    with pytest.raises((ValueError, Exception), match="evil_extra|Extra inputs|extra_forbidden"):
                        _load_sidecar()

    def test_extra_field_in_mwr_row_rejected_by_production_loader(self):
        """A MWR row with extra field must be rejected by production _load_sidecar."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(sidecar_data)
        mutated["normalized_extractions"]["mwr_orbit62_filenames"][0]["evil_extra"] = "x"
        mutated["artifact_id"] = compute_sidecar_artifact_id(mutated)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir) / "mutated_sidecar.json"
            tmp.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", tmp):
                with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                           pathlib.Path(tmpdir).resolve()):
                    with pytest.raises((ValueError, Exception), match="evil_extra|Extra inputs|extra_forbidden"):
                        _load_sidecar()

    def test_extra_field_in_junocam_row_rejected_by_production_loader(self):
        """A JunoCam row with extra field must be rejected by production _load_sidecar."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(sidecar_data)
        mutated["normalized_extractions"]["junocam_index_tab_orbit62_all"][0]["evil_extra"] = "x"
        mutated["artifact_id"] = compute_sidecar_artifact_id(mutated)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir) / "mutated_sidecar.json"
            tmp.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", tmp):
                with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                           pathlib.Path(tmpdir).resolve()):
                    with pytest.raises((ValueError, Exception), match="evil_extra|Extra inputs|extra_forbidden"):
                        _load_sidecar()

    def test_extra_field_in_waves_burst_row_rejected_by_production_loader(self):
        """A WAVES Burst row with extra field must be rejected by production _load_sidecar."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        mutated = copy.deepcopy(sidecar_data)
        mutated["normalized_extractions"]["waves_burst_index_tab_orbit62_all"][0]["evil_extra"] = "x"
        mutated["artifact_id"] = compute_sidecar_artifact_id(mutated)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = pathlib.Path(tmpdir) / "mutated_sidecar.json"
            tmp.write_text(json.dumps(mutated, indent=2), encoding="utf-8")
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", tmp):
                with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                           pathlib.Path(tmpdir).resolve()):
                    with pytest.raises((ValueError, Exception), match="evil_extra|Extra inputs|extra_forbidden"):
                        _load_sidecar()


# ===========================================================================
# §36 — Artifact-id tests
# ===========================================================================


class TestArtifactIdIntegrity:
    """§36: Comprehensive artifact_id tests."""

    def test_missing_artifact_id_rejected_by_load_sidecar(self, tmp_path):
        """Sidecar without artifact_id must be rejected."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        no_id = copy.deepcopy(sidecar_data)
        del no_id["artifact_id"]
        bad_path = tmp_path / "no_id.json"
        bad_path.write_text(json.dumps(no_id, indent=2), encoding="utf-8")
        with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", bad_path):
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                       tmp_path.resolve()):
                with pytest.raises(ValueError, match="artifact_id"):
                    _load_sidecar()

    def test_wrong_artifact_id_rejected_by_load_sidecar(self, tmp_path):
        """Sidecar with wrong artifact_id must be rejected."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        wrong = copy.deepcopy(sidecar_data)
        wrong["artifact_id"] = "0" * 64
        bad_path = tmp_path / "wrong_id.json"
        bad_path.write_text(json.dumps(wrong, indent=2), encoding="utf-8")
        with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", bad_path):
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                       tmp_path.resolve()):
                with pytest.raises(ValueError, match="artifact_id mismatch"):
                    _load_sidecar()

    def test_malformed_artifact_id_rejected(self):
        """artifact_id that is not 64 lowercase hex must be rejected by model."""
        from backend.app.mission_sources.v2_sidecar_models import HistoricalReplayV2DiscoveryEvidenceSidecar
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        bad = copy.deepcopy(sidecar_data)
        bad["artifact_id"] = "not_hex_at_all"
        with pytest.raises(Exception):
            HistoricalReplayV2DiscoveryEvidenceSidecar.model_validate(bad, strict=False)

    def test_nested_mutation_changes_artifact_id(self, sidecar):
        """Mutating any nested field changes artifact_id."""
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["jiram_orbit62_filenames"][0]["filename"] = "MUTATED.xml"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_path_mutation_changes_artifact_id(self, sidecar):
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["jiram_orbit62_filenames"][0]["relative_label_path"] = "MUTATED.xml"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_evidence_sha_mutation_changes_artifact_id(self, sidecar):
        mutated = copy.deepcopy(sidecar)
        mutated["discovery_evidence"][0]["response_sha256"] = "b" * 64
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_partition_mutation_changes_artifact_id(self, sidecar):
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["junocam_index_tab_orbit62_all"][0]["partition"] = "POST"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_row_reorder_does_not_change_artifact_id(self, sidecar):
        """Reversing normalized extraction collections must not change artifact_id.

        B2.1.4 §28/§29: compute_sidecar_artifact_id canonically sorts each collection
        by its registered sort key. The artifact_id is based on semantic content,
        not incidental extractor traversal order.
        """
        # Reverse JIRAM, JunoCam, and WAVES Burst collections
        reordered = copy.deepcopy(sidecar)
        reordered["normalized_extractions"]["jiram_orbit62_filenames"] = list(
            reversed(reordered["normalized_extractions"]["jiram_orbit62_filenames"])
        )
        reordered["normalized_extractions"]["junocam_index_tab_orbit62_all"] = list(
            reversed(reordered["normalized_extractions"]["junocam_index_tab_orbit62_all"])
        )
        reordered["normalized_extractions"]["waves_burst_index_tab_orbit62_all"] = list(
            reversed(reordered["normalized_extractions"]["waves_burst_index_tab_orbit62_all"])
        )
        # Also reverse evidence list
        reordered["discovery_evidence"] = list(reversed(reordered["discovery_evidence"]))

        # Under canonical sort policy, artifact_id must be unchanged
        id_original = compute_sidecar_artifact_id(sidecar)
        id_reordered = compute_sidecar_artifact_id(reordered)
        assert id_original == id_reordered, (
            f"artifact_id changed after row reorder: {id_original!r} != {id_reordered!r}. "
            "Collection sort canonicalization is not working."
        )
        assert id_original == sidecar["artifact_id"]

    def test_row_identity_mutation_changes_artifact_id(self, sidecar):
        """Mutating a row's identity field must change artifact_id (not just reorder)."""
        mutated = copy.deepcopy(sidecar)
        jiram = mutated["normalized_extractions"]["jiram_orbit62_filenames"]
        jiram[0] = dict(jiram[0])
        # Change hhmmss to something different (must still be valid 6 digits)
        jiram[0]["hhmmss"] = "000001" if jiram[0]["hhmmss"] != "000001" else "000002"
        # Keep filename/relative_label_path consistent with hhmmss (update filename too)
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]


# ===========================================================================
# §37 — Plan-binding tests
# ===========================================================================


class TestPlanBinding:
    """§37: Plan-binding tests."""

    def test_plan_has_required_discovery_evidence_artifact_id(self, plan):
        """discovery_evidence_artifact_id must be present and 64-hex."""
        aid = plan.discovery_evidence_artifact_id
        assert aid is not None
        assert re.fullmatch(r"[0-9a-f]{64}", aid)

    def test_plan_artifact_id_matches_sidecar(self, plan, sidecar):
        assert plan.discovery_evidence_artifact_id == sidecar["artifact_id"]

    def test_missing_discovery_evidence_artifact_id_rejected(self):
        """Plan without discovery_evidence_artifact_id must be rejected."""
        plan_data = json.loads(_PLAN_FILE.read_text(encoding="utf-8"))
        bad = copy.deepcopy(plan_data)
        bad.pop("discovery_evidence_artifact_id", None)
        with pytest.raises(Exception):
            HistoricalReplayV2AcquisitionPlan.model_validate(bad, strict=False)

    def test_malformed_discovery_evidence_artifact_id_rejected(self):
        """discovery_evidence_artifact_id that is not 64-hex must fail."""
        plan_data = json.loads(_PLAN_FILE.read_text(encoding="utf-8"))
        bad = copy.deepcopy(plan_data)
        bad["discovery_evidence_artifact_id"] = "not_valid"
        with pytest.raises(Exception):
            HistoricalReplayV2AcquisitionPlan.model_validate(bad, strict=False)

    def test_bound_loader_succeeds_for_correct_pair(self):
        """load_bound_v2_acquisition_plan() must succeed for the correct plan+sidecar."""
        result = load_bound_v2_acquisition_plan()
        assert isinstance(result, BoundAcquisitionPlan)
        assert result.plan.plan_id is not None
        assert result.sidecar.artifact_id == result.plan.discovery_evidence_artifact_id

    def test_bound_loader_rejects_mismatched_sidecar(self, tmp_path):
        """Plan built against sidecar A cannot bind sidecar B (different artifact_id)."""
        sidecar_data = json.loads(_SIDECAR_FILE.read_text(encoding="utf-8"))
        # Mutate the sidecar with a valid structural change (change evidence SHA)
        mutated = copy.deepcopy(sidecar_data)
        # Change a JIRAM filename to a valid but different file — this changes artifact_id
        # but doesn't invalidate the row model
        first_jiram = mutated["normalized_extractions"]["jiram_orbit62_filenames"][0]
        orig_fn = first_jiram["filename"]
        # Replace hhmmss in the filename to keep model valid
        new_fn = re.sub(r"T\d{6}", "T999999", orig_fn)
        first_jiram["filename"] = new_fn
        first_jiram["hhmmss"] = "999999"
        first_jiram["relative_label_path"] = new_fn
        mutated["artifact_id"] = compute_sidecar_artifact_id(mutated)

        mutated_path = tmp_path / "mutated_sidecar.json"
        mutated_path.write_text(json.dumps(mutated, indent=2), encoding="utf-8")

        with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_PATH", mutated_path):
            with patch("backend.app.mission_sources.v2_acquisition_plan_builder._SIDECAR_ALLOWED_DIR",
                       tmp_path.resolve()):
                with pytest.raises(ValueError, match="binding mismatch|artifact_id|mismatch"):
                    load_bound_v2_acquisition_plan()

    def test_plan_id_changes_when_sidecar_artifact_id_changes(self, plan):
        """A different sidecar artifact_id produces a different plan_id."""
        different_plan_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=plan.logical_entries,
            discovery_evidence=plan.discovery_evidence,
            discovery_evidence_artifact_id="a" * 64,  # different artifact_id
        )
        assert different_plan_id != plan.plan_id


# ===========================================================================
# §38 — Exact URL tests
# ===========================================================================


class TestExactUrls:
    """§38: URL must be produced from exact source-derived relative path."""

    def test_all_urls_are_source_derived_https(self, all_refs):
        """Every label URL must be HTTPS."""
        for ref in all_refs:
            assert ref.label_url.startswith("https://"), (
                f"Non-HTTPS URL: {ref.label_url!r}"
            )

    def test_no_guessed_science_extensions_in_urls(self, all_refs):
        """Label URLs must not reference science data files (IMG/DAT/FIT/CSV)."""
        forbidden = {".img", ".dat", ".fit", ".fits", ".csv", ".sts"}
        for ref in all_refs:
            ext = pathlib.Path(ref.label_url).suffix.lower()
            assert ext not in forbidden, (
                f"Label URL references science file: {ref.label_url!r}"
            )

    def test_all_label_urls_end_with_label_extension(self, all_refs):
        """All label URLs must end with .lbl, .LBL, or .xml."""
        valid_exts = {"lbl", "xml"}
        for ref in all_refs:
            ext = ref.label_url.rsplit(".", 1)[-1].lower() if "." in ref.label_url else ""
            assert ext in valid_exts, (
                f"Label URL does not end with label extension: {ref.label_url!r}"
            )

    def test_junocam_urls_come_from_file_specification_name(self, sidecar, all_refs):
        """JunoCam URLs must be derived from FILE_SPECIFICATION_NAME in sidecar rows."""
        jnc_rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        elig = [r for r in jnc_rows if r["partition"] == "ELIGIBLE"]
        known_file_specs = {r["file_specification_name"] for r in elig}
        junocam_refs = [r for r in all_refs if "JNCE_" in r.label_url or "JNCR_" in r.label_url]
        for ref in junocam_refs:
            # Extract the path after the base URL
            url_path = ref.label_url.split("planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/")[-1]
            assert url_path in known_file_specs, (
                f"JunoCam URL path {url_path!r} not in source FILE_SPECIFICATION_NAMEs"
            )

    def test_mwr_urls_come_from_source_relative_label_path(self, sidecar, all_refs):
        """MWR URLs must come from source-derived relative_label_path."""
        mwr_rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        elig = [r for r in mwr_rows if r.get("inclusion") == "ELIGIBLE"]
        known_relative_paths = {r["relative_label_path"] for r in elig}
        mwr_refs = [r for r in all_refs if "jnomwr_1100" in r.label_url]
        for ref in mwr_refs:
            # Extract the relative path after the base URL
            url_path = ref.label_url.split("pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/")[-1]
            assert url_path in known_relative_paths, (
                f"MWR URL path {url_path!r} not in source relative_label_paths"
            )

    def test_no_duplicate_urls(self, all_refs):
        """No two representations may share a label URL."""
        urls = [r.label_url for r in all_refs]
        assert len(urls) == len(set(urls)), "Duplicate label URLs found in plan"

    def test_waves_survey_urls_come_from_source_relative_label_path(self, sidecar, all_refs):
        """WAVES Survey URLs come from source relative_label_path."""
        ws_rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        elig = [r for r in ws_rows if r["inclusion"] == "ELIGIBLE"]
        known_paths = {r["relative_label_path"] for r in elig}
        ws_refs = [r for r in all_refs if "WAV-3-CDR-SRVFULL" in r.label_url]
        for ref in ws_refs:
            url_path = ref.label_url.split("2024149_ORBIT_62/")[-1]
            assert url_path in known_paths, (
                f"WAVES Survey URL path {url_path!r} not in source paths"
            )

    def test_jedi_urls_come_from_source_relative_label_path(self, sidecar, all_refs):
        """JEDI URLs come from source relative_label_path."""
        jedi_rows = (
            sidecar["normalized_extractions"]["jedi_165_labels"] +
            sidecar["normalized_extractions"]["jedi_166_labels"]
        )
        known_paths = {r["relative_label_path"] for r in jedi_rows}
        jedi_refs = [r for r in all_refs if "JNO-J-JED-3-CDR" in r.label_url]
        for ref in jedi_refs:
            url_path = ref.label_url.split("/DATA/2024/")[-1]
            assert url_path in known_paths, (
                f"JEDI URL path {url_path!r} not in source paths"
            )


# ===========================================================================
# §39 — JADE special acceptance test
# ===========================================================================


class TestJadeAcceptance:
    """§39: No selected JADE URL contains the simplified/fabricated B2.1.2 naming."""

    # B2.1.2 fabricated names (old simplified format without subdirectory or full name)
    _FABRICATED_PATTERNS = [
        "JAD_L30_LRS_ION_2024165_V01",
        "JAD_L30_HRS_ELC_2024165_V01",
        "JAD_L30_LRS_ION_PRI_2024165_V01",
        "JAD_L30_LRS_ELC_2024165_V01",
        "JAD_L30_HRS_ION_2024165_V01",
        "JAD_L30_LRS_ION_2024166_V01",
        "JAD_L30_LRS_ELC_2024166_V01",
        "JAD_L30_HRS_ION_2024166_V01",
        "JAD_L30_HRS_ELC_2024166_V01",
    ]

    # Expected real product identities from INDEX.TAB (actual families with full name)
    _EXPECTED_REAL_PRODUCT_IDS = {
        "JAD_L30_LRS_ION_ANY_CNT_2024165",
        "JAD_L30_HLS_ION_TOF_CNT_2024165",
        "JAD_L30_HLS_ION_LOG_CNT_2024165",
        "JAD_L30_LRS_ELC_ANY_CNT_2024165",
        "JAD_L30_HRS_ELC_TWO_CNT_2024165",
        "JAD_L30_HRS_ION_ANY_CNT_2024165",
        "JAD_L30_HRS_ELC_TWO_CNT_2024166",
        "JAD_L30_HRS_ION_ANY_CNT_2024166",
    }
    _EXPECTED_EXCLUDED_IDS = {
        "JAD_L30_HLS_ION_LOG_CNT_2024166",
        "JAD_L30_HLS_ION_TOF_CNT_2024166",
        "JAD_L30_LRS_ELC_ANY_CNT_2024166",
        "JAD_L30_LRS_ION_ANY_CNT_2024166",
    }

    def test_no_fabricated_product_id_in_selected_jade_urls(self, all_refs):
        """No selected JADE URL may contain a fabricated B2.1.2 product ID."""
        jade_refs = [r for r in all_refs if "JAD-3-CALIBRATED" in r.label_url]
        for ref in jade_refs:
            for fab in self._FABRICATED_PATTERNS:
                assert fab not in ref.label_url, (
                    f"Selected JADE URL contains fabricated B2.1.2 ID {fab!r}: {ref.label_url!r}"
                )

    def test_all_expected_eligible_jade_products_in_sidecar(self, sidecar):
        """All 8 expected real product IDs must be present as ELIGIBLE in sidecar."""
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        elig_ids = {r["product_id"] for r in rows if r["inclusion"] == "ELIGIBLE"}
        for pid in self._EXPECTED_REAL_PRODUCT_IDS:
            assert pid in elig_ids, f"Expected JADE eligible product {pid!r} not in sidecar"

    def test_all_expected_excluded_jade_products_in_sidecar(self, sidecar):
        """All 4 expected excluded product IDs must be EXCLUDED in sidecar."""
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        excl_ids = {r["product_id"] for r in rows if r["inclusion"] == "EXCLUDED"}
        for pid in self._EXPECTED_EXCLUDED_IDS:
            assert pid in excl_ids, f"Expected JADE excluded product {pid!r} not in sidecar"

    def test_jade_eligible_product_paths_are_source_derived(self, sidecar, all_refs):
        """JADE plan URLs must use relative_label_path from INDEX.TAB source."""
        jade_rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        elig = [r for r in jade_rows if r["inclusion"] == "ELIGIBLE"]
        known_paths = {r["relative_label_path"] for r in elig}
        jade_refs = [r for r in all_refs if "JAD-3-CALIBRATED" in r.label_url]
        for ref in jade_refs:
            url_path = ref.label_url.split("/DATA/")[-1]
            assert f"DATA/{url_path}" in known_paths or url_path in known_paths or any(
                ref.label_url.endswith(p.split("/")[-1]) for p in known_paths
            ), f"JADE URL {ref.label_url!r} path not found in source paths"

    def test_jade_12_product_identities_all_present(self, sidecar):
        """All 12 discovered JADE product identities must be in sidecar."""
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        all_ids = {r["product_id"] for r in rows}
        expected_all = self._EXPECTED_REAL_PRODUCT_IDS | self._EXPECTED_EXCLUDED_IDS
        assert expected_all == all_ids, (
            f"Missing: {expected_all - all_ids}, Extra: {all_ids - expected_all}"
        )

    def test_jade_relative_paths_use_subdirectory(self, sidecar):
        """JADE relative_label_paths must include subdirectory (not just DOY/file)."""
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        for r in rows:
            path = r["relative_label_path"]
            # Path must have at least 4 components: DATA/year/doy/subdir/file.LBL
            parts = path.replace("\\", "/").split("/")
            assert len(parts) >= 4, (
                f"JADE path {path!r} too shallow; expected DATA/year/doy/subdir/file.LBL"
            )


# ===========================================================================
# Scope enforcement tests
# ===========================================================================


class TestScopeEnforcement:
    """Verify no forbidden scope activities occurred."""

    def test_no_science_payload_in_plan_urls(self, all_refs):
        """Plan must not reference science binary files."""
        forbidden_exts = {".img", ".dat", ".fit", ".fits", ".sts", ".csv"}
        for ref in all_refs:
            ext = pathlib.Path(ref.label_url).suffix.lower()
            assert ext not in forbidden_exts, (
                f"Science payload in plan: {ref.label_url!r}"
            )

    def test_plan_has_exactly_535_source_refs(self, all_entries):
        # B2.2.1: 535 (restored from B2.2 527; includes 8 ineligible candidates)
        all_refs_local = [r for e in all_entries for r in e.representations]
        assert len(all_refs_local) == 535

    def test_plan_has_exactly_411_logical_entries(self, all_entries):
        # B2.2.1: 411 (restored from B2.2 403; includes 8 ineligible candidates)
        assert len(all_entries) == 411

    def test_plan_pds4_is_156(self, all_refs):
        # B2.2.1: 156 PDS4 (restored from B2.2 154; includes 2 ineligible UVS)
        from backend.app.mission_sources.v2_acquisition_plan import AcquisitionSourceStandard
        pds4 = [r for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS4]
        assert len(pds4) == 156

    def test_plan_pds3_is_379(self, all_refs):
        # B2.2.1: 379 PDS3 (restored from B2.2 373; includes 6 ineligible JEDI)
        from backend.app.mission_sources.v2_acquisition_plan import AcquisitionSourceStandard
        pds3 = [r for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS3]
        assert len(pds3) == 379
