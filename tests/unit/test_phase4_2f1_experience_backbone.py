"""Phase 4.2F1: Experience backbone tests.

Covers:
1. ExperienceManifest Pydantic validation passes for valid ASTERIA sidecar
2. Malformed sidecar fails cleanly (HTTP 500 via ValidationError)
3. Generic scenario (v3) returns available=false
4. Ingest replay batch sum == total_products (1,284)
5. Ingest total_bytes == authoritative scenario queue bytes
6. Total products == 1,284
7. Playback config present and valid
8. POST /plans/assess is non-mutating
9. POST /plans/assess preserves order
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
_ASTERIA_SCENARIO = str(_PROJECT_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")
_V3_SCENARIO = str(_PROJECT_ROOT / "data" / "scenarios" / "mission_data_v3.json")
_SIDECAR_PATH = _PROJECT_ROOT / "data" / "demo" / "asteria7_experience.json"


@pytest.fixture(autouse=True)
def reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.issued_plans.clear()


class TestExperienceManifestValidation:

    def test_sidecar_parses_as_experience_manifest(self):
        """Valid ASTERIA sidecar parses cleanly into ExperienceManifest."""
        from backend.app.models.experience import ExperienceManifest
        raw = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        manifest = ExperienceManifest.model_validate(raw)
        assert manifest.scenario_id == "asteria7_thermal_priority_contact_v1"
        assert manifest.display.mission_name == "ASTERIA-7"

    def test_malformed_sidecar_raises_validation_error(self):
        """A sidecar missing required fields fails with Pydantic ValidationError."""
        from pydantic import ValidationError
        from backend.app.models.experience import ExperienceManifest
        with pytest.raises(ValidationError):
            ExperienceManifest.model_validate({"schema_version": "1.0"})

    def test_experience_response_available_false_for_v3(self):
        """ExperienceResponse.available is False for non-ASTERIA scenario."""
        from backend.app import state as app_state
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_V3_SCENARIO)
        result = get_experience()
        assert result.available is False
        assert result.manifest is None

    def test_experience_response_available_true_for_asteria(self):
        """ExperienceResponse.available is True for ASTERIA-7."""
        from backend.app import state as app_state
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.available is True
        assert result.manifest is not None
        assert result.manifest.scenario_id == "asteria7_thermal_priority_contact_v1"

    def test_manifest_has_playback_config(self):
        """Manifest includes playback config with positive durations."""
        from backend.app import state as app_state
        from backend.app.api.routes_experience import get_experience
        app_state.load_scenario(_ASTERIA_SCENARIO)
        result = get_experience()
        assert result.manifest is not None
        pb = result.manifest.playback
        assert pb.ingest_duration_ms > 0
        assert pb.uplink_duration_ms > 0
        assert pb.propagation_duration_ms > 0


class TestIngestReplayBatchIntegrity:

    def test_batch_sum_equals_total_products(self):
        """Sum of all batch product counts must equal ingest_replay.total_products (1,284)."""
        from backend.app.models.experience import ExperienceManifest
        raw = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        manifest = ExperienceManifest.model_validate(raw)

        batch_total = sum(
            p.count
            for batch in manifest.ingest_replay.batches
            for p in batch.products
        )
        assert batch_total == manifest.ingest_replay.total_products, (
            f"Batch sum {batch_total} != total_products {manifest.ingest_replay.total_products}"
        )

    def test_total_products_equals_1284(self):
        """total_products in manifest matches authoritative scenario count (1,284)."""
        from backend.app.models.experience import ExperienceManifest
        from backend.app.simulation.scenario_loader import ScenarioLoader
        raw = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        manifest = ExperienceManifest.model_validate(raw)

        scenario = ScenarioLoader.load(_ASTERIA_SCENARIO)
        assert manifest.ingest_replay.total_products == len(scenario.data_products)
        assert manifest.ingest_replay.total_products == 1284

    def test_total_bytes_equals_authoritative_queue(self):
        """total_bytes in manifest matches authoritative scenario queue bytes (2,740,000,000)."""
        from backend.app.models.experience import ExperienceManifest
        from backend.app.simulation.scenario_loader import ScenarioLoader
        raw = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        manifest = ExperienceManifest.model_validate(raw)

        scenario = ScenarioLoader.load(_ASTERIA_SCENARIO)
        authoritative_bytes = sum(p.size_bits // 8 for p in scenario.data_products)
        assert manifest.ingest_replay.total_bytes == authoritative_bytes
        assert manifest.ingest_replay.total_bytes == 2_740_000_000

    def test_no_final_correction_needed(self):
        """Batches accumulate naturally to total — no hidden adjustment required."""
        from backend.app.models.experience import ExperienceManifest
        raw = json.loads(_SIDECAR_PATH.read_text(encoding="utf-8"))
        manifest = ExperienceManifest.model_validate(raw)

        # Each batch count must be positive
        for batch in manifest.ingest_replay.batches:
            for product in batch.products:
                assert product.count > 0, f"Batch product has count <= 0: {product}"


class TestAssessNonMutating:

    def test_assess_does_not_mutate_issued_plans(self):
        """POST /plans/assess must not invalidate the issued-plan registry."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)

        # Pre-populate issued plans with a fake entry
        app_state.issued_plans["fake-plan"] = object()  # type: ignore[assignment]
        count_before = len(app_state.issued_plans)

        req = AssessRequest(product_ids=["TEL-THERM-HR-042", "DIAG-THERM-EVT-017"])
        assess_manual_plan(req)

        # Issued plans must not be cleared
        assert len(app_state.issued_plans) == count_before

    def test_assess_preserves_order(self):
        """POST /plans/assess preserves the requested product order."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        app_state.load_scenario(_ASTERIA_SCENARIO)

        ordered_ids = [
            "CAL-THERM-006",
            "TEL-THERM-HR-042",
            "DIAG-THERM-EVT-017",
        ]
        req = AssessRequest(product_ids=ordered_ids)
        result = assess_manual_plan(req)

        result_ids = [pkt.packet_id for pkt in result.plan.packets]
        assert result_ids == ordered_ids

    def test_assess_returns_authoritative_packet_facts(self):
        """POST /plans/assess returns authoritative size_bits from scenario, not client values."""
        from backend.app import state as app_state
        from backend.app.api.routes_plans import assess_manual_plan, AssessRequest
        from backend.app.simulation.scenario_loader import ScenarioLoader

        app_state.load_scenario(_ASTERIA_SCENARIO)
        scenario = ScenarioLoader.load(_ASTERIA_SCENARIO)
        dp_map = {dp.product_id: dp for dp in scenario.data_products}

        req = AssessRequest(product_ids=["TEL-THERM-HR-042"])
        result = assess_manual_plan(req)

        assert result.plan.packets[0].size_bits == dp_map["TEL-THERM-HR-042"].size_bits
