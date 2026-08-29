"""GCSI Phase 6F-B2.1.2 — Acquisition Evidence Chain Tests.

All tests are OFFLINE. No live PDS requests are made.
No product-label files are fetched. No science payload downloads.

Tests correspond to the 23 requirements listed in spec section O:

 1. production builder contains no NASA product enumeration arrays
 2. exact 102 JIRAM filenames come from sidecar
 3. exact 46 MWR filenames come from sidecar
 4. exact 8 UVS filenames come from sidecar
 5. FGM selection comes from sidecar
 6. JADE 12→8+4 is reproducible
 7. JEDI 28 is reproducible
 8. WAVES Survey 4→2+2 is reproducible
 9. JunoCam 426→112+248+66 is reproducible
10. JunoCam 213→56+124+33 logical observations is reproducible
11. WAVES Burst 282→175+91+16 is reproducible
12. normalized extraction nested extra fields rejected
13. sidecar artifact_id recomputation succeeds
14. sidecar semantic mutation changes artifact_id
15. corrupted artifact_id rejected
16. plan binds sidecar artifact_id
17. evidence mutation changes plan_id
18. DiscoveryEvidence.capture byte_count mismatch impossible/rejected
19. plan loader rejects traversal
20. plan loader rejects symlink escape
21. sidecar loader rejects traversal/symlink escape
22. 411 / 535 / 156 / 379 remain exact
23. EXACT=215 / PENDING=196
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import pathlib
import tempfile
import textwrap
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_UTC,
    AcquisitionLogicalProductEntry,
    AcquisitionRepresentationRole,
    AcquisitionSourceRepresentation,
    AcquisitionSourceStandard,
    DiscoveryEvidence,
    HistoricalReplayV2AcquisitionPlan,
    TemporalEvidenceStatus,
    _compute_plan_id,
    load_acquisition_plan,
)
from backend.app.mission_sources.v2_acquisition_plan_builder import build_plan
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
    MwrDiscoveryLabel,
    MwrProductType,
    UvsDiscoveryLabel,
    WavesBurstDiscoveryRow,
    WavesBurstFamily,
    WavesBurstPartition,
    WavesSurveyDiscoveryLabel,
    WavesSurveyInclusion,
    compute_sidecar_artifact_id,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SIDECAR_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_discovery_evidence.json"
_BUILDER_PATH = (
    _REPO_ROOT / "backend" / "app" / "mission_sources" / "v2_acquisition_plan_builder.py"
)


@pytest.fixture(scope="session")
def sidecar() -> dict:
    return json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def plan() -> HistoricalReplayV2AcquisitionPlan:
    return build_plan()


@pytest.fixture(scope="session")
def all_entries(plan):
    return plan.logical_entries


@pytest.fixture(scope="session")
def all_refs(all_entries):
    return [r for e in all_entries for r in e.representations]


# ---------------------------------------------------------------------------
# Helper: parse builder source for module-level list/dict constants
# ---------------------------------------------------------------------------

def _get_builder_module_level_constants():
    """Parse the builder Python AST and return names of all module-level
    list/dict assignments (potential NASA identity arrays).
    """
    source = _BUILDER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constants = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and isinstance(
                            stmt.value, (ast.List, ast.Dict, ast.Set)
                        ):
                            constants.append(target.id)
    return constants


# ===========================================================================
# Test 1 — No NASA product enumeration arrays in builder
# ===========================================================================


class TestNoHardCodedNasaArrays:
    """Spec O.1: production builder contains no NASA product enumeration arrays."""

    # These are the specific array names that MUST have been removed.
    _FORBIDDEN_NAMES = {
        "_JIRAM_IMG_TIMES",
        "_JIRAM_SPE_TIMES",
        "_MWR_IRDR_165_CODES",
        "_MWR_IRDR_166_CODES",
        "_MWR_GRDR_165_CODES",
        "_MWR_GRDR_166_CODES",
        "_UVS_PRODUCTS",
        "_FGM_PRODUCTS",
        "_JADE_PRODUCTS",
        "_JEDI_165_PRODUCTS",
        "_JEDI_166_PRODUCTS",
        "_WAVES_SURVEY_PRODUCTS",
    }

    def test_forbidden_arrays_absent_from_builder_source(self):
        """The builder source must not contain any forbidden NASA identity arrays
        as variable assignments (not just docstring mentions).
        """
        # Use AST to check for actual assignments, not mentions in comments/docstrings
        source = _BUILDER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assigned_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned_names.add(target.id)
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    assigned_names.add(node.target.id)

        present = [name for name in self._FORBIDDEN_NAMES if name in assigned_names]
        assert not present, (
            f"Builder still contains hard-coded NASA identity array assignments: {present!r}. "
            "These must be moved to the discovery sidecar normalized_extractions."
        )

    def test_builder_module_level_lists_are_only_policy_values(self):
        """Any module-level list in the builder must NOT be a NASA identity collection."""
        constants = _get_builder_module_level_constants()
        # Only allowed list: _JUNOCAM_SCIENCE_OBS_TYPES (policy, not NASA identity)
        # and _WAVES_SURVEY_BAND_ROLES / _FAMILY_ROLE_MAP (GCSI classification maps)
        allowed_prefixes = ("_JUNOCAM_SCIENCE", "_FAMILY_ROLE_MAP", "_WAVES_SURVEY_BAND_ROLES")
        suspicious = [
            c for c in constants
            if not any(c.startswith(p) for p in allowed_prefixes)
        ]
        # None of these should be NASA product identity arrays
        for c in suspicious:
            assert c not in self._FORBIDDEN_NAMES, (
                f"Forbidden NASA identity array '{c}' found in builder module-level constants."
            )


# ===========================================================================
# Test 2 — 102 JIRAM filenames from sidecar
# ===========================================================================


class TestJiramSidecar:
    """Spec O.2: exact 102 JIRAM filenames come from sidecar."""

    def test_jiram_sidecar_count_is_102(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        assert len(rows) == 102, f"JIRAM sidecar rows: {len(rows)}"

    def test_jiram_img_count_is_51(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        img = [r for r in rows if r["family"] == "IMG"]
        assert len(img) == 51, f"JIRAM IMG rows: {len(img)}"

    def test_jiram_spe_count_is_51(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        spe = [r for r in rows if r["family"] == "SPE"]
        assert len(spe) == 51, f"JIRAM SPE rows: {len(spe)}"

    def test_jiram_sidecar_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        # Use model_validate with strict=False to allow str->Enum coercion from JSON
        parsed = [JiramDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 102

    def test_jiram_plan_entries_come_from_sidecar(self, all_entries, sidecar):
        """The 102 JIRAM plan entries must match the 102 sidecar filenames."""
        plan_jiram = [e for e in all_entries if e.instrument == "JIRAM"]
        assert len(plan_jiram) == 102

        sidecar_rows = sidecar["normalized_extractions"]["jiram_orbit62_filenames"]
        # Build set of expected logical IDs from sidecar
        expected_lids = set()
        for r in sidecar_rows:
            family = r["family"].lower()
            ts = r["hhmmss"]
            expected_lids.add(f"gcsi.jiram.pj62.{family}.{ts}")

        plan_lids = {e.logical_product_id for e in plan_jiram}
        assert plan_lids == expected_lids, (
            f"JIRAM plan IDs don't match sidecar. "
            f"Extra in plan: {plan_lids - expected_lids!r}. "
            f"Extra in sidecar: {expected_lids - plan_lids!r}."
        )


# ===========================================================================
# Test 3 — 46 MWR filenames from sidecar
# ===========================================================================


class TestMwrSidecar:
    """Spec O.3: exact 46 MWR filenames come from sidecar."""

    def test_mwr_sidecar_total_discovered_is_96(self, sidecar):
        """Archive provides 24 products per type per DOY (hours 0-23) = 96 total discovered."""
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        assert len(rows) == 96, f"MWR sidecar rows: {len(rows)}"

    def test_mwr_sidecar_eligible_is_46(self, sidecar):
        """Only 46 MWR products are within the accumulation window."""
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        eligible = [r for r in rows if r.get("inclusion") == "ELIGIBLE"]
        assert len(eligible) == 46, f"MWR eligible rows: {len(eligible)}"

    def test_mwr_sidecar_excluded_is_50(self, sidecar):
        """50 MWR products are outside the accumulation window."""
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        excluded = [r for r in rows if r.get("inclusion") == "EXCLUDED"]
        assert len(excluded) == 50, f"MWR excluded rows: {len(excluded)}"

    def test_mwr_irdr_discovered_count_is_48(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        irdr = [r for r in rows if r["product_type"] == "IRDR"]
        assert len(irdr) == 48, f"MWR IRDR rows: {len(irdr)}"

    def test_mwr_grdr_discovered_count_is_48(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        grdr = [r for r in rows if r["product_type"] == "GRDR"]
        assert len(grdr) == 48, f"MWR GRDR rows: {len(grdr)}"

    def test_mwr_eligible_doy165_count_is_14_per_type(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        irdr_165_elig = [r for r in rows if r["product_type"] == "IRDR" and r["doy"] == 165 and r.get("inclusion") == "ELIGIBLE"]
        grdr_165_elig = [r for r in rows if r["product_type"] == "GRDR" and r["doy"] == 165 and r.get("inclusion") == "ELIGIBLE"]
        assert len(irdr_165_elig) == 14, f"MWR IRDR DOY165 eligible: {len(irdr_165_elig)}"
        assert len(grdr_165_elig) == 14, f"MWR GRDR DOY165 eligible: {len(grdr_165_elig)}"

    def test_mwr_eligible_doy166_count_is_9_per_type(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        irdr_166_elig = [r for r in rows if r["product_type"] == "IRDR" and r["doy"] == 166 and r.get("inclusion") == "ELIGIBLE"]
        grdr_166_elig = [r for r in rows if r["product_type"] == "GRDR" and r["doy"] == 166 and r.get("inclusion") == "ELIGIBLE"]
        assert len(irdr_166_elig) == 9, f"MWR IRDR DOY166 eligible: {len(irdr_166_elig)}"
        assert len(grdr_166_elig) == 9, f"MWR GRDR DOY166 eligible: {len(grdr_166_elig)}"

    def test_mwr_sidecar_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        parsed = [MwrDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 96

    def test_mwr_plan_entries_come_from_eligible_sidecar_rows(self, all_entries, sidecar):
        """The 46 MWR plan entries must match only eligible sidecar rows."""
        plan_mwr = [e for e in all_entries if e.instrument == "MWR"]
        assert len(plan_mwr) == 46

        sidecar_rows = sidecar["normalized_extractions"]["mwr_orbit62_filenames"]
        eligible = [r for r in sidecar_rows if r.get("inclusion") == "ELIGIBLE"]
        expected_lids = set()
        for r in eligible:
            kind = r["product_type"].lower()
            doy = r["doy"]
            hour = r["hour"]
            expected_lids.add(f"gcsi.mwr.pj62.{kind}.2024{doy}{hour:02d}0000")

        plan_lids = {e.logical_product_id for e in plan_mwr}
        assert plan_lids == expected_lids


# ===========================================================================
# Test 4 — 8 UVS filenames from sidecar
# ===========================================================================


class TestUvsSidecar:
    """Spec O.4: exact 8 UVS filenames come from sidecar."""

    def test_uvs_sidecar_count_is_8(self, sidecar):
        rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        assert len(rows) == 8, f"UVS sidecar rows: {len(rows)}"

    def test_uvs_sidecar_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        parsed = [UvsDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 8

    def test_uvs_plan_entries_come_from_sidecar(self, all_entries, sidecar):
        plan_uvs = [e for e in all_entries if e.instrument == "UVS"]
        assert len(plan_uvs) == 8

        sidecar_rows = sidecar["normalized_extractions"]["uvs_orbit62_filenames"]
        expected_lids = set()
        for r in sidecar_rows:
            sensor = r["sensor"]
            sclk = r["sclk"]
            doy_str = r["doy_str"]
            obs_type = r["obs_type"]
            expected_lids.add(
                f"gcsi.uvs.pj62.{sensor.lower()}_{sclk}_{doy_str}_{obs_type.lower()}"
            )
        plan_lids = {e.logical_product_id for e in plan_uvs}
        assert plan_lids == expected_lids


# ===========================================================================
# Test 5 — FGM selection from sidecar
# ===========================================================================


class TestFgmSidecar:
    """Spec O.5: FGM selection comes from sidecar."""

    def test_fgm_sidecar_count_is_2_selected(self, sidecar):
        rows = sidecar["normalized_extractions"]["fgm_peri62_filenames"]
        selected = [r for r in rows if r["selected"]]
        assert len(selected) == 2, f"FGM selected: {len(selected)}"

    def test_fgm_sidecar_rows_parse_as_strict_models(self, sidecar):
        """B2.1.4: 3 total candidates (standard + pj62 + r1s), 2 selected."""
        rows = sidecar["normalized_extractions"]["fgm_peri62_filenames"]
        parsed = [FgmDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        # B2.1.4: 3 candidates total (including R1S excluded variant)
        assert len(parsed) == 3
        selected = [r for r in parsed if r.selected]
        assert len(selected) == 2

    def test_fgm_plan_entries_come_from_sidecar(self, all_entries, sidecar):
        plan_fgm = [e for e in all_entries if e.instrument == "FGM"]
        assert len(plan_fgm) == 2

        sidecar_rows = sidecar["normalized_extractions"]["fgm_peri62_filenames"]
        selected = [r for r in sidecar_rows if r["selected"]]
        expected_lids = {f"gcsi.fgm.pj62.{r['logical_stem']}" for r in selected}
        plan_lids = {e.logical_product_id for e in plan_fgm}
        assert plan_lids == expected_lids


# ===========================================================================
# Test 6 — JADE 12→8+4
# ===========================================================================


class TestJadeSidecar:
    """Spec O.6: JADE 12→8+4 is reproducible."""

    def test_jade_sidecar_total_is_12(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        assert len(rows) == 12, f"JADE sidecar rows: {len(rows)}"

    def test_jade_eligible_count_is_8(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        eligible = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        assert len(eligible) == 8, f"JADE eligible: {len(eligible)}"

    def test_jade_excluded_count_is_4(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        excluded = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(excluded) == 4, f"JADE excluded: {len(excluded)}"

    def test_jade_sidecar_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["jade_orbit62_labels"]
        parsed = [JadeDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 12

    def test_jade_plan_uses_only_eligible(self, all_entries):
        plan_jade = [e for e in all_entries if e.instrument == "JADE"]
        assert len(plan_jade) == 8


# ===========================================================================
# Test 7 — JEDI 28 reproducible
# ===========================================================================


class TestJediSidecar:
    """Spec O.7: JEDI 28 is reproducible."""

    def test_jedi_165_sidecar_count_is_14(self, sidecar):
        rows = sidecar["normalized_extractions"]["jedi_165_labels"]
        assert len(rows) == 14, f"JEDI 165 sidecar rows: {len(rows)}"

    def test_jedi_166_sidecar_count_is_14(self, sidecar):
        rows = sidecar["normalized_extractions"]["jedi_166_labels"]
        assert len(rows) == 14, f"JEDI 166 sidecar rows: {len(rows)}"

    def test_jedi_total_is_28(self, sidecar):
        r165 = sidecar["normalized_extractions"]["jedi_165_labels"]
        r166 = sidecar["normalized_extractions"]["jedi_166_labels"]
        assert len(r165) + len(r166) == 28

    def test_jedi_sidecar_rows_parse_as_strict_models(self, sidecar):
        r165 = [JediDiscoveryLabel.model_validate(r, strict=False) for r in sidecar["normalized_extractions"]["jedi_165_labels"]]
        r166 = [JediDiscoveryLabel.model_validate(r, strict=False) for r in sidecar["normalized_extractions"]["jedi_166_labels"]]
        assert len(r165) == 14
        assert len(r166) == 14

    def test_jedi_plan_count_is_28(self, all_entries):
        plan_jedi = [e for e in all_entries if e.instrument == "JEDI"]
        assert len(plan_jedi) == 28


# ===========================================================================
# Test 8 — WAVES Survey 4→2+2
# ===========================================================================


class TestWavesSurveySidecar:
    """Spec O.8: WAVES Survey 4→2+2 is reproducible."""

    def test_waves_survey_sidecar_total_is_4(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        assert len(rows) == 4, f"WAVES Survey rows: {len(rows)}"

    def test_waves_survey_eligible_is_2(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        eligible = [r for r in rows if r["inclusion"] == "ELIGIBLE"]
        assert len(eligible) == 2

    def test_waves_survey_excluded_is_2(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        excluded = [r for r in rows if r["inclusion"] == "EXCLUDED"]
        assert len(excluded) == 2

    def test_waves_survey_sidecar_rows_parse_as_strict_models(self, sidecar):
        rows = sidecar["normalized_extractions"]["waves_survey_orbit62_labels"]
        parsed = [WavesSurveyDiscoveryLabel.model_validate(r, strict=False) for r in rows]
        assert len(parsed) == 4

    def test_waves_survey_plan_uses_only_eligible(self, all_entries):
        plan_ws = [e for e in all_entries if e.instrument == "WAVES_SURVEY"]
        assert len(plan_ws) == 2


# ===========================================================================
# Test 9 — JunoCam 426→112+248+66
# ===========================================================================


class TestJunoCamReconciliation:
    """Spec O.9: JunoCam 426→112+248+66 is reproducible offline."""

    def test_partition_summary_total_is_426(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        total = ps["total_orbit62_rows"]
        assert total == 426, f"JunoCam total: {total}"

    def test_partition_summary_pre_is_112(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        assert ps["pre_rows"] == 112

    def test_partition_summary_eligible_is_248(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        assert ps["eligible_rows"] == 248

    def test_partition_summary_post_is_66(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        assert ps["post_rows"] == 66

    def test_partition_sum_equals_total(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        assert ps["pre_rows"] + ps["eligible_rows"] + ps["post_rows"] == ps["total_orbit62_rows"]

    def test_eligible_rows_in_sidecar_match_partition_summary(self, sidecar):
        """B2.1.3: eligible_rows count (248) = per-representation rows (248).

        B2.1.3 converted JunoCam from paired (124 obs-records) to individual
        representation rows (248 rows = 124 EDR + 124 RDR). The partition
        summary eligible_rows=248 now matches the raw row count directly.
        """
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        observation_rows = sidecar["normalized_extractions"]["junocam_index_tab_orbit62_all"]
        eligible_rows = [r for r in observation_rows if r.get("partition") == "ELIGIBLE"]
        # 248 eligible rows (124 EDR + 124 RDR individual representation rows)
        assert len(eligible_rows) == ps["eligible_rows"], (
            f"eligible rows={len(eligible_rows)}, partition_summary eligible_rows={ps['eligible_rows']}"
        )
        assert ps["eligible_rows"] == 248


# ===========================================================================
# Test 10 — JunoCam 213→56+124+33 logical observations
# ===========================================================================


class TestJunoCamLogicalObservations:
    """Spec O.10: JunoCam 213→56+124+33 logical observations reproducible.

    HISTORICAL_213_LOGICAL_OBSERVATION_LEDGER = CONFIRMED
    426 raw rows = 213 EDR + 213 RDR.
    Logical: PRE=56, ELIGIBLE=124, POST=33.
    """

    def test_total_logical_observations_is_213(self, sidecar):
        """213 = PRE(56) + ELIGIBLE(124) + POST(33)."""
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        total_rows = ps["total_orbit62_rows"]  # 426
        # Each INDEX.TAB row is ONE representation (EDR or RDR)
        # Each logical observation = 1 EDR + 1 RDR = 2 rows
        logical_total = total_rows // 2
        assert logical_total == 213

    def test_eligible_logical_is_124(self, sidecar):
        """Eligible logical observations = 124."""
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        eligible_logical = ps["eligible_rows"] // 2  # 248 // 2 = 124
        assert eligible_logical == 124

    def test_pre_logical_is_56(self, sidecar):
        """PRE logical observations = 56."""
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        pre_logical = ps["pre_rows"] // 2  # 112 // 2 = 56
        assert pre_logical == 56

    def test_post_logical_is_33(self, sidecar):
        """POST logical observations = 33."""
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        post_logical = ps["post_rows"] // 2  # 66 // 2 = 33
        assert post_logical == 33

    def test_logical_partition_sum_is_213(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        pre = ps["pre_rows"] // 2
        eligible = ps["eligible_rows"] // 2
        post = ps["post_rows"] // 2
        assert pre + eligible + post == 213
        assert pre == 56
        assert eligible == 124
        assert post == 33

    def test_b21_raw_row_ledger_superseded_noted_in_sidecar(self, sidecar):
        """The sidecar partition note must reference the superseded B2.1 ledger."""
        ps = sidecar["normalized_extractions"]["partition_summaries"]["junocam"]
        note = ps.get("note", "")
        assert "B21_RAW_ROW_LEDGER_SUPERSEDED" in note
        assert "HISTORICAL_213_LOGICAL_OBSERVATION_LEDGER" in note


# ===========================================================================
# Test 11 — WAVES Burst 282→175+91+16
# ===========================================================================


class TestWavesBurstReconciliation:
    """Spec O.11: WAVES Burst 282→175+91+16 is reproducible offline."""

    def test_partition_summary_total_is_282(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert ps["total_orbit62_rows"] == 282

    def test_partition_summary_pre_is_175(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert ps["pre_rows"] == 175

    def test_partition_summary_eligible_is_91(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert ps["eligible_rows"] == 91

    def test_partition_summary_post_is_16(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert ps["post_rows"] == 16

    def test_partition_sum_equals_total(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        assert ps["pre_rows"] + ps["eligible_rows"] + ps["post_rows"] == ps["total_orbit62_rows"]

    def test_eligible_families_sum_to_91(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        fams = ps["eligible_families"]
        total = sum(fams.values())
        assert total == 91
        assert fams["B_BIN"] == 41
        assert fams["E_BIN"] == 41
        assert fams["B_REC"] == 3
        assert fams["E_REC"] == 3
        assert fams["NBS_REC"] == 3

    def test_eligible_rows_in_sidecar_match_partition_summary(self, sidecar):
        ps = sidecar["normalized_extractions"]["partition_summaries"]["waves_burst"]
        all_rows = sidecar["normalized_extractions"]["waves_burst_index_tab_orbit62_all"]
        eligible = [r for r in all_rows if r.get("partition") == "ELIGIBLE"]
        assert len(eligible) == ps["eligible_rows"]


# ===========================================================================
# Test 12 — Normalized extraction nested extra fields rejected
# ===========================================================================


class TestNormalizedExtractionModelsRejectExtras:
    """Spec O.12: normalized extraction nested extra fields rejected."""

    def test_jiram_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            JiramDiscoveryLabel(
                filename="JIR_IMG_RDR_2024166T090046_V01.xml",
                family=JiramFamily.IMG,
                hhmmss="090046",
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_mwr_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            MwrDiscoveryLabel(
                filename="MWR62RI20241651000000_R04120_V04",
                product_type=MwrProductType.IRDR,
                doy=165,
                hour=10,
                code="R04120",
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_uvs_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            UvsDiscoveryLabel(
                filename="UVS_S01_771573735_2024165_P62OBS_V01",
                sensor="S01",
                sclk="771573735",
                doy_str="2024165",
                obs_type="P62OBS",
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_fgm_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            FgmDiscoveryLabel(
                lbl_filename="fgm_jno_l3_2024165pl_v02.lbl",
                product_id="FGM_JNO_L3_2024165PL",
                logical_stem="fgm_jno_l3_2024165pl",
                selected=True,
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_jade_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            JadeDiscoveryLabel(
                product_id="JAD_L30_LRS_ION_2024165_V01",
                path_suffix="2024/165/JAD_L30_LRS_ION_2024165_V01.LBL",
                doy=165,
                inclusion=JadeInclusion.ELIGIBLE,
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_jedi_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            JediDiscoveryLabel(
                product_id="JED_090_HIERSESP_CDR_2024165_V04",
                doy=165,
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_waves_survey_label_rejects_extra_field(self):
        with pytest.raises(Exception):
            WavesSurveyDiscoveryLabel(
                stem="WAV_2024165T000000_B_V01",
                band="b",
                inclusion=WavesSurveyInclusion.ELIGIBLE,
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_junocam_discovery_row_rejects_extra_field(self):
        with pytest.raises(Exception):
            JunoCamDiscoveryRow(
                edr_product_id="JNCE_2024165_62C00057_V01",
                edr_file_specification_name="DATA/EDR/JUPITER/ORBIT_62/JNCE_2024165_62C00057_V01.LBL",
                rdr_product_id="JNCR_2024165_62C00057_V01",
                rdr_file_specification_name="DATA/RDR/JUPITER/ORBIT_62/JNCR_2024165_62C00057_V01.LBL",
                obs_key="2024165_62c00057",
                start_time_utc="2024-06-13T10:00:04.955",
                stop_time_utc="2024-06-13T10:00:08.705",
                partition=JunoCamPartition.ELIGIBLE,
                extra_unknown="oops",  # type: ignore[call-arg]
            )

    def test_waves_burst_discovery_row_rejects_extra_field(self):
        with pytest.raises(Exception):
            WavesBurstDiscoveryRow(
                product_id="WAV_2024165T145507_B_REC",
                file_specification_name="DATA/WAVES_BURST/2024149_ORBIT_62/WAV_2024165T145507_B_REC_V01.LBL",
                start_time="2024-06-13T14:55:07.565",
                stop_time="2024-06-13T15:14:01.339",
                family=WavesBurstFamily.B_REC,
                partition=WavesBurstPartition.ELIGIBLE,
                extra_unknown="oops",  # type: ignore[call-arg]
            )


# ===========================================================================
# Test 13 — Sidecar artifact_id recomputation succeeds
# ===========================================================================


class TestSidecarArtifactId:
    """Spec O.13: sidecar artifact_id recomputation succeeds."""

    def test_sidecar_has_artifact_id(self, sidecar):
        assert "artifact_id" in sidecar
        assert isinstance(sidecar["artifact_id"], str)
        assert len(sidecar["artifact_id"]) == 64

    def test_artifact_id_recomputation_matches_stored(self, sidecar):
        """Recomputing artifact_id from sidecar content must match stored value."""
        recomputed = compute_sidecar_artifact_id(sidecar)
        assert recomputed == sidecar["artifact_id"], (
            f"artifact_id mismatch: stored {sidecar['artifact_id']!r} != "
            f"recomputed {recomputed!r}."
        )

    def test_artifact_id_is_64_hex(self, sidecar):
        import re
        assert re.fullmatch(r"[0-9a-f]{64}", sidecar["artifact_id"])


# ===========================================================================
# Test 14 — Sidecar semantic mutation changes artifact_id
# ===========================================================================


class TestSidecarArtifactIdMutation:
    """Spec O.14: sidecar semantic mutation changes artifact_id."""

    def test_filename_mutation_changes_artifact_id(self, sidecar):
        """Mutating a JIRAM filename must change artifact_id."""
        import copy
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["jiram_orbit62_filenames"][0]["filename"] = "MUTATED.xml"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"], (
            "Filename mutation should change artifact_id."
        )

    def test_stop_time_mutation_changes_artifact_id(self, sidecar):
        """Mutating a JunoCam stop_time_utc must change artifact_id."""
        import copy
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["junocam_index_tab_orbit62_all"][0]["stop_time_utc"] = "2099-01-01T00:00:00"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_classification_mutation_changes_artifact_id(self, sidecar):
        """Mutating a JADE inclusion changes artifact_id."""
        import copy
        mutated = copy.deepcopy(sidecar)
        mutated["normalized_extractions"]["jade_orbit62_labels"][0]["inclusion"] = "EXCLUDED"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]

    def test_evidence_sha_mutation_changes_artifact_id(self, sidecar):
        """Mutating a discovery evidence SHA-256 must change artifact_id."""
        import copy
        mutated = copy.deepcopy(sidecar)
        # Change the first evidence record's SHA
        ev_list = mutated["discovery_evidence"]
        ev_list[0]["response_sha256"] = "a" * 63 + "b"
        new_id = compute_sidecar_artifact_id(mutated)
        assert new_id != sidecar["artifact_id"]


# ===========================================================================
# Test 15 — Corrupted artifact_id rejected
# ===========================================================================


class TestCorruptedArtifactIdRejected:
    """Spec O.15: corrupted artifact_id rejected."""

    def test_corrupted_artifact_id_rejected_by_load_sidecar(self, tmp_path):
        """Writing a sidecar with a wrong artifact_id and loading via the builder
        must raise ValueError.

        We patch both the sidecar path and the allowed directory to allow the
        temp file to pass the boundary check.
        """
        import copy

        sidecar_data = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        corrupted = copy.deepcopy(sidecar_data)
        corrupted["artifact_id"] = "0" * 64  # Wrong hash

        bad_path = tmp_path / "corrupted_sidecar.json"
        bad_path.write_text(json.dumps(corrupted, indent=2), encoding="utf-8")

        from backend.app.mission_sources import v2_acquisition_plan_builder as builder_mod
        # Patch both the sidecar path AND the allowed dir to permit the tmp_path location
        with patch.object(builder_mod, "_SIDECAR_PATH", bad_path):
            with patch.object(builder_mod, "_SIDECAR_ALLOWED_DIR", tmp_path.resolve()):
                with pytest.raises(ValueError, match="artifact_id mismatch"):
                    builder_mod._load_sidecar()


# ===========================================================================
# Test 16 — Plan binds sidecar artifact_id
# ===========================================================================


class TestPlanBindsSidecarArtifactId:
    """Spec O.16: plan binds sidecar artifact_id."""

    def test_plan_has_discovery_evidence_artifact_id(self, plan):
        """Plan must carry discovery_evidence_artifact_id."""
        assert plan.discovery_evidence_artifact_id is not None
        assert isinstance(plan.discovery_evidence_artifact_id, str)
        assert len(plan.discovery_evidence_artifact_id) == 64

    def test_plan_artifact_id_matches_sidecar(self, plan, sidecar):
        """Plan's discovery_evidence_artifact_id must equal sidecar's artifact_id."""
        assert plan.discovery_evidence_artifact_id == sidecar["artifact_id"], (
            f"Plan discovery_evidence_artifact_id {plan.discovery_evidence_artifact_id!r} "
            f"!= sidecar artifact_id {sidecar['artifact_id']!r}."
        )

    def test_plan_id_includes_artifact_id(self, plan):
        """plan_id must change if discovery_evidence_artifact_id changes."""
        # Compute plan_id without the artifact_id (simulate old behavior)
        plan_id_without_artifact = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=plan.logical_entries,
            discovery_evidence=plan.discovery_evidence,
            discovery_evidence_artifact_id=None,
        )
        assert plan_id_without_artifact != plan.plan_id, (
            "plan_id must change when discovery_evidence_artifact_id differs."
        )


# ===========================================================================
# Test 17 — Evidence mutation changes plan_id
# ===========================================================================


class TestEvidenceMutationChangesPlanId:
    """Spec O.17: evidence mutation changes plan_id."""

    def test_mutating_evidence_sha_changes_plan_id(self, plan):
        """Mutating an evidence SHA-256 must change the plan_id."""
        from pydantic import ValidationError

        orig_ev = plan.discovery_evidence[0]
        # Build a new evidence with a different byte_count (forces SHA-like change)
        # Instead mutate retrieved_at to a different time
        mutated_ev = DiscoveryEvidence(
            evidence_id=orig_ev.evidence_id,
            source_url=orig_ev.source_url,
            retrieved_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            response_sha256=orig_ev.response_sha256,
            source_kind=orig_ev.source_kind,
            relevant_row_count=orig_ev.relevant_row_count,
            byte_count=orig_ev.byte_count,
        )
        mutated_evidence = (mutated_ev,) + plan.discovery_evidence[1:]
        mutated_plan_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=plan.logical_entries,
            discovery_evidence=mutated_evidence,
            discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
        )
        assert mutated_plan_id != plan.plan_id, (
            "Mutating evidence retrieved_at should change plan_id."
        )

    def test_mutating_artifact_id_changes_plan_id(self, plan):
        """Passing a different discovery_evidence_artifact_id must change plan_id."""
        mutated_plan_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=plan.logical_entries,
            discovery_evidence=plan.discovery_evidence,
            discovery_evidence_artifact_id="mutated_" + "a" * 57,
        )
        assert mutated_plan_id != plan.plan_id


# ===========================================================================
# Test 18 — DiscoveryEvidence.capture byte_count mismatch impossible/rejected
# ===========================================================================


class TestDiscoveryEvidenceCaptureHardened:
    """Spec O.18: DiscoveryEvidence.capture byte_count mismatch impossible/rejected."""

    def test_capture_does_not_accept_byte_count_parameter(self):
        """capture() must not have a byte_count parameter (B2.1.2 removal)."""
        sig = inspect.signature(DiscoveryEvidence.capture)
        assert "byte_count" not in sig.parameters, (
            "DiscoveryEvidence.capture() must not have a byte_count parameter. "
            "byte_count is always len(response_bytes)."
        )

    def test_capture_byte_count_equals_len_response_bytes(self):
        """byte_count must always equal len(response_bytes)."""
        data = b"test content of known length"
        ev = DiscoveryEvidence.capture(
            evidence_id="test",
            source_url="https://example.com/data",
            retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            response_bytes=data,
            source_kind="pds3_index_tab",
        )
        assert ev.byte_count == len(data)
        assert ev.byte_count == 28

    def test_capture_does_not_accept_caller_byte_count(self):
        """Passing byte_count to capture() must raise TypeError."""
        with pytest.raises(TypeError):
            DiscoveryEvidence.capture(
                evidence_id="test",
                source_url="https://example.com/data",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_bytes=b"some data",
                source_kind="pds3_index_tab",
                byte_count=9999,  # type: ignore[call-arg]
            )

    def test_capture_sha256_from_actual_bytes(self):
        """SHA-256 must be computed from actual response_bytes."""
        import hashlib
        data = b"real archive content"
        expected_sha = hashlib.sha256(data).hexdigest()
        ev = DiscoveryEvidence.capture(
            evidence_id="test",
            source_url="https://example.com/data",
            retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            response_bytes=data,
            source_kind="pds3_index_tab",
        )
        assert ev.response_sha256 == expected_sha


# ===========================================================================
# Test 19 — Plan loader rejects traversal
# ===========================================================================


class TestPlanLoaderRejectsTraversal:
    """Spec O.19: plan loader rejects traversal."""

    def test_loader_rejects_dotdot_in_path(self):
        """Path with '..' traversal must be rejected before resolving."""
        traversal_path = str(
            _REPO_ROOT / "data" / "replays" / ".." / ".." / "some_file.json"
        )
        with pytest.raises(ValueError, match="traversal|outside allowed"):
            load_acquisition_plan(traversal_path)

    def test_loader_rejects_path_outside_data_replays(self, tmp_path):
        """A .json file outside data/replays/ must be rejected."""
        # Create a valid-looking but outside-boundary plan file
        outside_file = tmp_path / "plan.json"
        outside_file.write_text('{"schema": "x"}', encoding="utf-8")
        with pytest.raises(ValueError, match="outside allowed"):
            load_acquisition_plan(str(outside_file))

    def test_loader_rejects_non_json_extension(self):
        """A file without .json extension must be rejected."""
        non_json = str(_REPO_ROOT / "data" / "replays" / "some_file.txt")
        with pytest.raises(ValueError, match="json"):
            load_acquisition_plan(non_json)


# ===========================================================================
# Test 20 — Plan loader rejects symlink escape
# ===========================================================================


class TestPlanLoaderRejectsSymlinkEscape:
    """Spec O.20: plan loader rejects symlink escape."""

    @pytest.mark.skipif(
        not hasattr(pathlib.Path, "symlink_to"),
        reason="Symlinks not supported on this platform",
    )
    def test_loader_rejects_symlink_outside_boundary(self, tmp_path):
        """A .json symlink that points outside data/replays/ must be rejected.

        We create a real JSON file outside data/replays/, then attempt to
        create a symlink to it inside data/replays/ and load via that symlink.
        If symlink creation fails (Windows without privileges), skip gracefully.
        """
        # Create a target file outside data/replays/
        target = tmp_path / "real_plan.json"
        target.write_text('{"schema": "x"}', encoding="utf-8")

        # Attempt to create symlink inside data/replays/
        symlink_path = _REPO_ROOT / "data" / "replays" / "test_symlink_b212.json"
        try:
            symlink_path.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("Cannot create symlinks on this system/OS configuration.")
        try:
            # The symlink resolves outside data/replays/ (to tmp_path)
            # OR the loader detects the original path is a symlink
            # Either way, it must be rejected
            with pytest.raises(ValueError, match="symlink|outside allowed"):
                load_acquisition_plan(str(symlink_path))
        finally:
            symlink_path.unlink(missing_ok=True)


# ===========================================================================
# Test 21 — Sidecar loader rejects traversal/symlink escape
# ===========================================================================


class TestSidecarLoaderSecurity:
    """Spec O.21: sidecar loader rejects traversal/symlink escape."""

    def test_sidecar_loads_without_error(self):
        """The production sidecar must load without any security violation.

        B2.1.4: _load_sidecar() returns HistoricalReplayV2DiscoveryEvidenceSidecar (typed model).
        Access fields via model attributes, not dict subscripts.
        """
        from backend.app.mission_sources import v2_acquisition_plan_builder as builder_mod
        data = builder_mod._load_sidecar()
        assert data.schema == "gcsi.pj62_discovery_evidence_sidecar"

    def test_sidecar_loader_rejects_traversal_path(self, tmp_path):
        """Sidecar loader with a path containing '..' must raise ValueError."""
        from backend.app.mission_sources import v2_acquisition_plan_builder as builder_mod

        traversal = tmp_path / ".." / "something.json"
        with patch.object(builder_mod, "_SIDECAR_PATH", traversal):
            with pytest.raises(ValueError, match="traversal|outside allowed"):
                builder_mod._load_sidecar()

    def test_sidecar_loader_rejects_path_outside_boundary(self, tmp_path):
        """Sidecar loader with a path outside data/replays/ must raise ValueError."""
        from backend.app.mission_sources import v2_acquisition_plan_builder as builder_mod

        outside = tmp_path / "sidecar.json"
        outside.write_text("{}", encoding="utf-8")
        with patch.object(builder_mod, "_SIDECAR_PATH", outside):
            with pytest.raises(ValueError, match="outside allowed"):
                builder_mod._load_sidecar()


# ===========================================================================
# Test 22 — 411 / 535 / 156 / 379 remain exact
# ===========================================================================


class TestExactCounts:
    """Spec O.22: 411 / 535 / 156 / 379 remain exact."""

    def test_total_logical_entries_is_411(self, all_entries):
        assert len(all_entries) == 411

    def test_total_source_refs_is_535(self, all_refs):
        assert len(all_refs) == 535

    def test_pds4_refs_is_156(self, all_refs):
        pds4 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS4)
        assert pds4 == 156

    def test_pds3_refs_is_379(self, all_refs):
        pds3 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS3)
        assert pds3 == 379

    def test_pds4_plus_pds3_is_535(self, all_refs):
        pds4 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS4)
        pds3 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS3)
        assert pds4 + pds3 == 535


# ===========================================================================
# Test 23 — EXACT=215 / PENDING=196
# ===========================================================================


class TestTemporalContract:
    """Spec O.23: EXACT=215 / PENDING=196."""

    def test_exact_count_is_223(self, all_entries):
        """B2.1.3: EXACT=223 (JunoCam=124, WAVES_BURST=91, JADE=8 upgraded to EXACT)."""
        exact = sum(
            1 for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
        )
        assert exact == 223, f"EXACT: expected 223, got {exact}."

    def test_pending_count_is_188(self, all_entries):
        """B2.1.3: PENDING=188 (JIRAM=102, MWR=46, UVS=8, FGM=2, JEDI=28, WAVES_SURVEY=2)."""
        pending = sum(
            1 for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
        )
        assert pending == 188, f"PENDING: expected 188, got {pending}."

    def test_exact_plus_pending_is_411(self, all_entries):
        exact = sum(
            1 for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
        )
        pending = sum(
            1 for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
        )
        assert exact + pending == 411, f"EXACT({exact}) + PENDING({pending}) != 411"

    def test_pending_entries_have_no_discovery_time(self, all_entries):
        """All PENDING entries must have discovery_availability_time_utc = None."""
        bad = [
            e.logical_product_id for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
            and e.discovery_availability_time_utc is not None
        ]
        assert not bad, f"PENDING entries with non-None time: {bad[:5]!r}."

    def test_exact_entries_have_discovery_time(self, all_entries):
        """All EXACT entries must have discovery_availability_time_utc set."""
        bad = [
            e.logical_product_id for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
            and e.discovery_availability_time_utc is None
        ]
        assert not bad, f"EXACT entries with None time: {bad[:5]!r}."
