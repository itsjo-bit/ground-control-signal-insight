"""GCSI — Unit tests for the mission source catalog.

Tests verify:
  - Catalog contains exactly the expected source IDs.
  - Deterministic ordering: asteria-7, juno-pj62-v1, juno-pj62-v2.
  - No duplicate source_id.
  - Each entry's attributes are correct (mode, historical, simulated).
  - get_catalog_entry returns correct entry or None.
  - source_ref is never empty / is a non-trivial string.
"""

from __future__ import annotations

import pytest
from backend.app.mission_sources.source_catalog import (
    AVAILABLE_MISSION_SOURCES,
    MissionSourceCatalogEntry,
    get_catalog_entry,
)
from backend.app.mission_sources.models import MissionSourceMode


class TestCatalogContents:
    def test_exactly_three_sources(self):
        assert len(AVAILABLE_MISSION_SOURCES) == 3

    def test_source_ids(self):
        ids = [e.source_id for e in AVAILABLE_MISSION_SOURCES]
        assert ids == ["asteria-7", "juno-pj62-v1", "juno-pj62-v2"]

    def test_no_duplicate_source_id(self):
        ids = [e.source_id for e in AVAILABLE_MISSION_SOURCES]
        assert len(ids) == len(set(ids))

    def test_deterministic_ordering(self):
        """Order must be: ASTERIA-7, Juno V1, Juno V2."""
        assert AVAILABLE_MISSION_SOURCES[0].source_id == "asteria-7"
        assert AVAILABLE_MISSION_SOURCES[1].source_id == "juno-pj62-v1"
        assert AVAILABLE_MISSION_SOURCES[2].source_id == "juno-pj62-v2"

    def test_asteria_mode_synthetic(self):
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert entry.mode == MissionSourceMode.SYNTHETIC_SCENARIO

    def test_asteria_not_historical(self):
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert entry.historical is False

    def test_asteria_simulated(self):
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert entry.simulated is True

    def test_asteria_source_ref_contains_asteria(self):
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        assert "asteria7" in entry.source_ref

    def test_v1_mode_historical_replay(self):
        entry = get_catalog_entry("juno-pj62-v1")
        assert entry is not None
        assert entry.mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_v1_historical(self):
        entry = get_catalog_entry("juno-pj62-v1")
        assert entry is not None
        assert entry.historical is True

    def test_v1_simulated(self):
        entry = get_catalog_entry("juno-pj62-v1")
        assert entry is not None
        assert entry.simulated is True

    def test_v1_source_ref_path(self):
        entry = get_catalog_entry("juno-pj62-v1")
        assert entry is not None
        assert "juno_pj62_mwr_v1.json" in entry.source_ref

    def test_v2_mode_historical_replay(self):
        entry = get_catalog_entry("juno-pj62-v2")
        assert entry is not None
        assert entry.mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_v2_historical(self):
        entry = get_catalog_entry("juno-pj62-v2")
        assert entry is not None
        assert entry.historical is True

    def test_v2_source_ref_path(self):
        entry = get_catalog_entry("juno-pj62-v2")
        assert entry is not None
        assert "juno_pj62_large_replay_v2_descriptor.json" in entry.source_ref

    def test_all_source_refs_nonempty(self):
        for entry in AVAILABLE_MISSION_SOURCES:
            assert entry.source_ref, f"source_ref empty for {entry.source_id}"

    def test_all_display_names_nonempty(self):
        for entry in AVAILABLE_MISSION_SOURCES:
            assert entry.display_name, f"display_name empty for {entry.source_id}"

    def test_all_descriptions_nonempty(self):
        for entry in AVAILABLE_MISSION_SOURCES:
            assert entry.description, f"description empty for {entry.source_id}"


class TestGetCatalogEntry:
    def test_known_id_returns_entry(self):
        assert get_catalog_entry("asteria-7") is not None

    def test_unknown_id_returns_none(self):
        assert get_catalog_entry("does-not-exist") is None

    def test_empty_string_returns_none(self):
        assert get_catalog_entry("") is None

    def test_path_traversal_returns_none(self):
        assert get_catalog_entry("../../../etc/passwd") is None

    def test_absolute_path_returns_none(self):
        assert get_catalog_entry("C:\\secret") is None

    def test_url_returns_none(self):
        assert get_catalog_entry("https://example.com/foo") is None

    def test_percent_encoded_returns_none(self):
        assert get_catalog_entry("%2e%2e%2f") is None

    def test_path_like_returns_none(self):
        assert get_catalog_entry("data/replays/foo.json") is None

    def test_filesystem_path_returns_none(self):
        entry = get_catalog_entry("juno-pj62-v1")
        # Passing source_ref directly must NOT be treated as a valid source_id
        assert entry is not None
        assert get_catalog_entry(entry.source_ref) is None

    def test_catalog_entry_is_frozen(self):
        """Catalog entries must be immutable (frozen dataclass)."""
        entry = get_catalog_entry("asteria-7")
        assert entry is not None
        with pytest.raises((AttributeError, TypeError)):
            entry.source_id = "tampered"  # type: ignore[misc]
