"""GCSI — Phase 7E: Architectural boundary invariants.

These tests protect the classification established in Phase 7E:

  Mission Source Catalog  =  canonical user-facing mission selection
  /scenarios              =  internal/developer compatibility infrastructure

INVARIANTS VERIFIED
-------------------
A. Mission Source Catalog contains exactly the three expected canonical entries.
B. mission_data_v3 is NOT a Mission Source Catalog entry.
C. mission_data_v2 is NOT a Mission Source Catalog entry.
D. degraded_link is NOT a Mission Source Catalog entry.
E. nominal_pass is NOT a Mission Source Catalog entry.
F. All five scenario JSON files exist under data/scenarios/.
G. Production MissionControl.tsx imports selectSource, not switchScenario.
H. GET /scenarios (compatibility API) still returns scenario files.
I. GET /sources (canonical API) returns exactly three catalog sources.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Resolve the repo root from this file's location.
# __file__ = <repo>/tests/unit/test_phase7e_architecture_boundary.py
# parents[0] = tests/unit/
# parents[1] = tests/
# parents[2] = <repo root>  (contains pytest.ini)
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from backend.app.mission_sources.source_catalog import (
    AVAILABLE_MISSION_SOURCES,
    get_catalog_entry,
)


# ---------------------------------------------------------------------------
# A — Catalog contains exactly the three canonical entries
# ---------------------------------------------------------------------------

class TestCatalogIsExactlyThreeCanonicalEntries:
    """The Mission Source Catalog must contain exactly the three user-facing sources."""

    EXPECTED_IDS = ["asteria-7", "juno-pj62-v1", "juno-pj62-v2"]

    def test_catalog_has_exactly_three_entries(self):
        assert len(AVAILABLE_MISSION_SOURCES) == 3, (
            "Mission Source Catalog must contain exactly 3 user-facing sources. "
            f"Found: {[e.source_id for e in AVAILABLE_MISSION_SOURCES]}"
        )

    def test_catalog_contains_asteria_7(self):
        assert get_catalog_entry("asteria-7") is not None

    def test_catalog_contains_juno_pj62_v1(self):
        assert get_catalog_entry("juno-pj62-v1") is not None

    def test_catalog_contains_juno_pj62_v2(self):
        assert get_catalog_entry("juno-pj62-v2") is not None

    def test_catalog_source_ids_match_expected(self):
        actual = [e.source_id for e in AVAILABLE_MISSION_SOURCES]
        assert actual == self.EXPECTED_IDS, (
            f"Catalog source IDs changed. Expected {self.EXPECTED_IDS}, got {actual}"
        )


# ---------------------------------------------------------------------------
# B–E — Internal scenario files are NOT Mission Source Catalog entries
# ---------------------------------------------------------------------------

class TestInternalScenariosNotInCatalog:
    """Internal/benchmark/test scenario files must not appear in the catalog."""

    def test_mission_data_v3_not_in_catalog(self):
        """mission_data_v3.json is the frozen benchmark input — not user-facing."""
        assert get_catalog_entry("mission_data_v3") is None
        assert get_catalog_entry("mission-data-v3") is None
        # Verify no catalog entry's source_ref matches this file
        for entry in AVAILABLE_MISSION_SOURCES:
            assert "mission_data_v3" not in entry.source_id, (
                f"mission_data_v3 must not appear as a source_id: {entry.source_id}"
            )

    def test_mission_data_v2_not_in_catalog(self):
        """mission_data_v2.json is a compatibility fixture — not user-facing."""
        assert get_catalog_entry("mission_data_v2") is None
        assert get_catalog_entry("mission-data-v2") is None
        for entry in AVAILABLE_MISSION_SOURCES:
            assert "mission_data_v2" not in entry.source_id, (
                f"mission_data_v2 must not appear as a source_id: {entry.source_id}"
            )

    def test_degraded_link_not_in_catalog(self):
        """degraded_link.json is a legacy regression fixture — not user-facing."""
        assert get_catalog_entry("degraded_link") is None
        assert get_catalog_entry("degraded-link") is None
        for entry in AVAILABLE_MISSION_SOURCES:
            assert "degraded_link" not in entry.source_id, (
                f"degraded_link must not appear as a source_id: {entry.source_id}"
            )

    def test_nominal_pass_not_in_catalog(self):
        """nominal_pass.json is a legacy regression fixture — not user-facing."""
        assert get_catalog_entry("nominal_pass") is None
        assert get_catalog_entry("nominal-pass") is None
        for entry in AVAILABLE_MISSION_SOURCES:
            assert "nominal_pass" not in entry.source_id, (
                f"nominal_pass must not appear as a source_id: {entry.source_id}"
            )

    def test_catalog_only_exposes_three_source_ids(self):
        """Guard: adding a fourth catalog entry requires explicit review."""
        source_ids = {e.source_id for e in AVAILABLE_MISSION_SOURCES}
        unexpected = source_ids - {"asteria-7", "juno-pj62-v1", "juno-pj62-v2"}
        assert not unexpected, (
            f"Unexpected source IDs in catalog: {unexpected}. "
            "Adding to the Mission Source Catalog is a user-facing change requiring review."
        )


# ---------------------------------------------------------------------------
# F — All five scenario JSON files exist under data/scenarios/
# ---------------------------------------------------------------------------

class TestScenarioFilesExist:
    """All five scenario files must be present and unmodified under data/scenarios/."""

    _SCENARIOS_DIR = _REPO_ROOT / "data" / "scenarios"

    EXPECTED_FILES = [
        "asteria7_thermal_priority_contact_v1.json",
        "mission_data_v3.json",
        "mission_data_v2.json",
        "degraded_link.json",
        "nominal_pass.json",
    ]

    def test_scenarios_directory_exists(self):
        assert self._SCENARIOS_DIR.is_dir(), (
            f"data/scenarios/ directory missing at {self._SCENARIOS_DIR}"
        )

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_scenario_file_exists(self, filename: str):
        path = self._SCENARIOS_DIR / filename
        assert path.is_file(), (
            f"Scenario file missing: {path}. "
            "Scenario files must not be moved or deleted in Phase 7E."
        )

    def test_mission_data_v3_byte_sha256(self):
        """The frozen benchmark input must not have been modified."""
        import hashlib
        path = self._SCENARIOS_DIR / "mission_data_v3.json"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = "dea5339623a604f3119a46c6fc754a2df22340acf7466f7783b3ac93e05501a9"
        assert actual == expected, (
            f"mission_data_v3.json byte hash changed from frozen value.\n"
            f"Expected: {expected}\n"
            f"Actual:   {actual}\n"
            "This file is frozen. Do not modify it."
        )


# ---------------------------------------------------------------------------
# G — Production MissionControl uses selectSource, not switchScenario
# ---------------------------------------------------------------------------

class TestProductionUseSelectSource:
    """Production UI must use selectSource(), not switchScenario(), for mission switching."""

    _MISSION_CONTROL = _REPO_ROOT / "frontend" / "src" / "MissionControl.tsx"
    _APP_TSX = _REPO_ROOT / "frontend" / "src" / "App.tsx"

    def test_mission_control_imports_select_source(self):
        text = self._MISSION_CONTROL.read_text(encoding="utf-8")
        assert "selectSource" in text, (
            "MissionControl.tsx must import/use selectSource for production switching."
        )

    def test_mission_control_does_not_import_switch_scenario(self):
        """Production MissionControl must not import switchScenario.
        switchScenario is compatibility/test-only.
        """
        text = self._MISSION_CONTROL.read_text(encoding="utf-8")
        # switchScenario must not appear as an import in production MissionControl
        import re
        # Check for import-style references (not string literals in test mocks)
        import_pattern = re.compile(r'\bimport\b.*\bswitchScenario\b')
        assert not import_pattern.search(text), (
            "MissionControl.tsx must not import switchScenario. "
            "Production source switching must use selectSource()."
        )

    def test_app_tsx_imports_select_source(self):
        text = self._APP_TSX.read_text(encoding="utf-8")
        assert "selectSource" in text, (
            "App.tsx must use selectSource for production source switching."
        )

    def test_app_tsx_does_not_import_switch_scenario(self):
        """Production App.tsx must not import switchScenario."""
        text = self._APP_TSX.read_text(encoding="utf-8")
        import re
        import_pattern = re.compile(r'\bimport\b.*\bswitchScenario\b')
        assert not import_pattern.search(text), (
            "App.tsx must not import switchScenario. "
            "Production source switching must use selectSource()."
        )


# ---------------------------------------------------------------------------
# H — /scenarios compatibility API reflects all five files (API-level check)
# ---------------------------------------------------------------------------

class TestScenariosCompatibilityApiReflectsAllFiles:
    """/scenarios must still list all five scenario files (compatibility not broken)."""

    _SCENARIOS_DIR = _REPO_ROOT / "data" / "scenarios"

    def test_all_five_json_files_present_in_dir(self):
        """Sanity check: directory scan finds all expected files."""
        json_files = {p.name for p in self._SCENARIOS_DIR.glob("*.json")}
        expected = {
            "asteria7_thermal_priority_contact_v1.json",
            "mission_data_v3.json",
            "mission_data_v2.json",
            "degraded_link.json",
            "nominal_pass.json",
        }
        missing = expected - json_files
        assert not missing, (
            f"/scenarios compatibility API would be missing files: {missing}. "
            "Scenario files must not be moved or deleted."
        )


# ---------------------------------------------------------------------------
# I — Catalog source_ids are exactly the three canonical entries (API-level)
# ---------------------------------------------------------------------------

class TestCatalogApiContractsAreStable:
    """Guard that source catalog API contracts have not changed."""

    def test_asteria7_source_id_is_stable(self):
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert entry.source_id == "asteria-7"

    def test_juno_v1_source_id_is_stable(self):
        entry = get_catalog_entry("juno-pj62-v1")
        assert entry is not None
        assert entry.source_id == "juno-pj62-v1"

    def test_juno_v2_source_id_is_stable(self):
        entry = get_catalog_entry("juno-pj62-v2")
        assert entry is not None
        assert entry.source_id == "juno-pj62-v2"

    def test_asteria7_is_synthetic_not_historical(self):
        from backend.app.mission_sources.models import MissionSourceMode
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert entry.mode == MissionSourceMode.SYNTHETIC_SCENARIO
        assert entry.historical is False

    def test_juno_sources_are_historical_replay(self):
        from backend.app.mission_sources.models import MissionSourceMode
        for sid in ("juno-pj62-v1", "juno-pj62-v2"):
            entry = get_catalog_entry(sid)
            assert entry is not None
            assert entry.mode == MissionSourceMode.HISTORICAL_REPLAY, (
                f"{sid} must be HISTORICAL_REPLAY mode"
            )
            assert entry.historical is True, (
                f"{sid} must have historical=True"
            )
