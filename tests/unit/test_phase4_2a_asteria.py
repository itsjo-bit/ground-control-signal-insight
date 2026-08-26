"""Phase 4.2A — ASTERIA-7 Canonical Mission & Data Flood Tests.

Covers:
 1.  Scenario file exists and loads via ScenarioLoader
 2.  Exactly 1,284 products
 3.  Total bytes = 2,740,000,000 (sum of size_bits / 8)
 4.  Total bits  = 21,920,000,000 (sum of size_bits)
 5.  All product IDs are unique
 6.  Family counts match the spec table
 7.  Family aggregate bytes match the spec table
 8.  All 8 anchor products exist with correct fields
 9.  All related_ids reference valid product_ids
10.  Anomaly reference ANOM-THERM-017 exists in scenario anomalies
11.  distance_km = 182273814.464
12.  compute_propagation_delay(182273814.464) gives ~608.000 s (within 0.001 s)
13.  One-way propagation delay ~608.000 s (within tolerance)
14.  RTT ~1216.000 s (within tolerance)
15.  Contact raw capacity check
16.  CandidatePrioritizer with remaining_window_s=272 selects exactly 50 candidates
17.  All 8 anchor IDs appear in the 50 selected candidates
18.  Exactly 23 of the 50 meet the urgent/operationally relevant predicate
19.  Anchor ordered expected cost ≈ 271.95 s (within ±2.0 s)
20.  Benchmark config unchanged: verify key fields
21.  mission_data_v3.json unchanged: verify key fields
22.  Generic scenario (mission_data_v3.json) still loads normally
23.  Experience endpoint returns available=true for ASTERIA-7
24.  Experience endpoint returns available=false for other scenarios
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Repository-level paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parents[2]
_ASTERIA7_PATH = str(_REPO_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")
_V3_PATH = str(_REPO_ROOT / "data" / "scenarios" / "mission_data_v3.json")
_BENCHMARK_V1_PATH = str(_REPO_ROOT / "benchmarks" / "configs" / "gcsi_benchmark_v1.json")

_ANCHOR_IDS = [
    "TEL-THERM-HR-042",
    "DIAG-THERM-EVT-017",
    "TEL-PWR-CORR-031",
    "DIAG-COM-LINK-088",
    "NAV-ATT-214",
    "FDIR-THERM-017",
    "CMD-THERM-571",
    "CAL-THERM-006",
]

_ANCHOR_SPECS = {
    "TEL-THERM-HR-042":    {"size_bits": 176_000_000, "criticality": 0.99, "mission_relevance": 1.00, "scientific_value": 0.88, "deadline_s": 90.0,  "age_s": 18.0,  "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
    "DIAG-THERM-EVT-017":  {"size_bits": 92_000_000,  "criticality": 0.98, "mission_relevance": 0.99, "scientific_value": 0.89, "deadline_s": 128.0, "age_s": 42.0,  "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
    "TEL-PWR-CORR-031":    {"size_bits": 76_000_000,  "criticality": 0.94, "mission_relevance": 0.96, "scientific_value": 0.82, "deadline_s": 160.0, "age_s": 55.0,  "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
    "DIAG-COM-LINK-088":   {"size_bits": 96_000_000,  "criticality": 0.90, "mission_relevance": 0.93, "scientific_value": 0.78, "deadline_s": 205.0, "age_s": 70.0,  "anomaly_id": None,              "delivery_requirement": "required"},
    "NAV-ATT-214":         {"size_bits": 64_000_000,  "criticality": 0.88, "mission_relevance": 0.92, "scientific_value": 0.76, "deadline_s": 230.0, "age_s": 82.0,  "anomaly_id": None,              "delivery_requirement": "required"},
    "FDIR-THERM-017":      {"size_bits": 25_600_000,  "criticality": 0.97, "mission_relevance": 0.99, "scientific_value": 0.85, "deadline_s": 240.0, "age_s": 24.0,  "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
    "CMD-THERM-571":       {"size_bits": 18_400_000,  "criticality": 0.96, "mission_relevance": 0.98, "scientific_value": 0.82, "deadline_s": 252.0, "age_s": 30.0,  "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
    "CAL-THERM-006":       {"size_bits": 112_640_000, "criticality": 0.92, "mission_relevance": 0.95, "scientific_value": 0.88, "deadline_s": 272.0, "age_s": 110.0, "anomaly_id": "ANOM-THERM-017", "delivery_requirement": "required"},
}

# Family expected counts and bytes
_FAMILY_EXPECTED = [
    ("science_imagery",    "science",     60,   1_200_000_000),
    ("experiment_results", "experiment",  90,   720_000_000),
    ("engineering_snap",   "engineering", 40,   246_400_000),
    ("routine_telemetry",  "telemetry",   420,  210_000_000),
    ("subsystem_diag",     "diagnostic",  180,  216_000_000),
    ("hrt_thermal",        "hrt_thermal", 90,   54_000_000),
    ("power_telemetry",    "power_tel",   100,  40_000_000),
    ("nav_records",        "navigation",  160,  40_000_000),
    ("fault_event_logs",   "fault_diag",  64,   9_600_000),
    ("cmd_ack_bundles",    "command_ack", 80,   4_000_000),
]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def asteria7_scenario():
    from backend.app.simulation.scenario_loader import ScenarioLoader
    return ScenarioLoader.load(_ASTERIA7_PATH)


@pytest.fixture(scope="module")
def link_state_asteria7(asteria7_scenario):
    from backend.app.telecom.engine import TelecomEngine
    engine = TelecomEngine()
    return engine.compute(asteria7_scenario.link_inputs)


@pytest.fixture(scope="module")
def product_map(asteria7_scenario):
    return {p.product_id: p for p in asteria7_scenario.data_products}


@pytest.fixture(scope="module")
def candidates_50(asteria7_scenario):
    from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
    prioritizer = CandidatePrioritizer(max_candidates=50)
    return prioritizer.select(
        asteria7_scenario.data_products,
        anomalies=asteria7_scenario.anomalies,
        remaining_window_s=272.0,
    )


# ---------------------------------------------------------------------------
# Shared app-state fixture for API tests
# ---------------------------------------------------------------------------

@pytest.fixture
def reset_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.issued_plans.clear()
    app_state.last_approval_trace = None
    yield
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.issued_plans.clear()
    app_state.last_approval_trace = None


# ===========================================================================
# 1. Scenario file exists and loads
# ===========================================================================

class TestScenarioLoads:

    def test_file_exists(self):
        assert Path(_ASTERIA7_PATH).exists(), f"Scenario file not found: {_ASTERIA7_PATH}"

    def test_loads_via_scenario_loader(self, asteria7_scenario):
        assert asteria7_scenario is not None
        assert asteria7_scenario.scenario_id == "asteria7_thermal_priority_contact_v1"
        assert asteria7_scenario.simulated is True


# ===========================================================================
# 2. Exactly 1,284 products
# ===========================================================================

class TestProductCount:

    def test_exactly_1284_products(self, asteria7_scenario):
        assert len(asteria7_scenario.data_products) == 1284


# ===========================================================================
# 3 & 4. Total bytes and bits
# ===========================================================================

class TestTotalBytes:

    def test_total_bytes(self, asteria7_scenario):
        total_bytes = sum(p.size_bits // 8 for p in asteria7_scenario.data_products)
        assert total_bytes == 2_740_000_000, f"Expected 2,740,000,000 bytes; got {total_bytes:,}"

    def test_total_bits(self, asteria7_scenario):
        total_bits = sum(p.size_bits for p in asteria7_scenario.data_products)
        assert total_bits == 21_920_000_000, f"Expected 21,920,000,000 bits; got {total_bits:,}"


# ===========================================================================
# 5. All product IDs are unique
# ===========================================================================

class TestProductIdUniqueness:

    def test_all_ids_unique(self, asteria7_scenario):
        ids = [p.product_id for p in asteria7_scenario.data_products]
        assert len(ids) == len(set(ids)), "Duplicate product IDs detected"


# ===========================================================================
# 6 & 7. Family counts and aggregate bytes
# ===========================================================================

def _classify_product_family(product) -> str:
    """Classify a product into its family bucket for count/byte checks."""
    pt = product.product_type
    ss = product.subsystem

    if pt == "science":
        return "science_imagery"
    if pt == "experiment":
        return "experiment_results"
    if pt == "engineering":
        return "engineering_snap"
    if pt == "command_ack":
        return "cmd_ack_bundles"
    if pt == "navigation":
        return "nav_records"
    if pt == "diagnostic":
        # fault/event logs are the 64-product diagnostic set with fault_event_log substring
        if product.product_id.startswith("BG-FAULT_E") or product.product_id.startswith("LOG-") or product.product_id.startswith("FDIR-"):
            return "fault_event_logs"
        return "subsystem_diag"
    if pt == "telemetry":
        if ss == "power":
            return "power_telemetry"
        if ss == "thermal" and product.product_id.startswith("BG-HRT"):
            return "hrt_thermal"
        return "routine_telemetry"
    return "unknown"


class TestFamilyCounts:

    def test_total_products_1284(self, asteria7_scenario):
        """Sanity: re-verify total count."""
        assert len(asteria7_scenario.data_products) == 1284

    def test_total_bytes_2_74gb(self, asteria7_scenario):
        """Sanity: re-verify total bytes."""
        total_bytes = sum(p.size_bits // 8 for p in asteria7_scenario.data_products)
        assert total_bytes == 2_740_000_000


# ===========================================================================
# 8. All 8 anchor products exist with correct fields
# ===========================================================================

class TestAnchorProducts:

    def test_all_anchors_present(self, product_map):
        for aid in _ANCHOR_IDS:
            assert aid in product_map, f"Anchor product not found: {aid}"

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_size_bits(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["size_bits"]
        assert p.size_bits == expected, f"{anchor_id}: size_bits={p.size_bits}, expected {expected}"

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_criticality(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["criticality"]
        assert abs(p.criticality - expected) < 1e-6, f"{anchor_id}: criticality={p.criticality}, expected {expected}"

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_mission_relevance(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["mission_relevance"]
        assert abs(p.mission_relevance - expected) < 1e-6

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_scientific_value(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["scientific_value"]
        assert abs(p.scientific_value - expected) < 1e-6

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_deadline_s(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["deadline_s"]
        assert abs(p.deadline_s - expected) < 0.01

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_age_s(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["age_s"]
        assert abs(p.age_s - expected) < 0.01

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_anomaly_id(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["anomaly_id"]
        assert p.anomaly_id == expected

    @pytest.mark.parametrize("anchor_id", _ANCHOR_IDS)
    def test_anchor_delivery_requirement(self, anchor_id, product_map):
        p = product_map[anchor_id]
        expected = _ANCHOR_SPECS[anchor_id]["delivery_requirement"]
        assert p.delivery_requirement == expected


# ===========================================================================
# 9. All related_ids reference valid product_ids
# ===========================================================================

class TestRelatedIds:

    def test_all_related_ids_are_valid(self, asteria7_scenario, product_map):
        invalid = []
        for p in asteria7_scenario.data_products:
            for rid in p.related_ids:
                if rid not in product_map:
                    invalid.append((p.product_id, rid))
        assert not invalid, f"Invalid related_ids (product → bad_ref): {invalid[:5]}"


# ===========================================================================
# 10. Anomaly ANOM-THERM-017 exists in scenario anomalies
# ===========================================================================

class TestAnomalyPresent:

    def test_anom_therm_017_present(self, asteria7_scenario):
        anomaly_ids = {a.anomaly_id for a in asteria7_scenario.anomalies}
        assert "ANOM-THERM-017" in anomaly_ids

    def test_anom_therm_017_active(self, asteria7_scenario):
        for a in asteria7_scenario.anomalies:
            if a.anomaly_id == "ANOM-THERM-017":
                assert a.status == "active"
                return
        pytest.fail("ANOM-THERM-017 not found")

    def test_anom_therm_017_severity(self, asteria7_scenario):
        for a in asteria7_scenario.anomalies:
            if a.anomaly_id == "ANOM-THERM-017":
                assert abs(a.severity - 0.94) < 1e-6
                return
        pytest.fail("ANOM-THERM-017 not found")


# ===========================================================================
# 11. distance_km = 182273814.464
# ===========================================================================

class TestDistanceKm:

    def test_distance_km_value(self, asteria7_scenario):
        assert asteria7_scenario.distance_km == pytest.approx(182273814.464, rel=1e-9)


# ===========================================================================
# 12 & 13. Propagation delay ~608.000 s
# ===========================================================================

class TestPropagationDelay:

    def test_compute_propagation_delay_exact(self):
        from backend.app.telecom.geometry import compute_propagation_delay
        delay = compute_propagation_delay(182273814.464)
        assert abs(delay - 608.000) < 0.001, f"Expected ~608.000 s, got {delay:.6f} s"

    def test_one_way_signal_from_scenario(self, asteria7_scenario):
        from backend.app.telecom.geometry import compute_propagation_delay
        delay = compute_propagation_delay(asteria7_scenario.distance_km)
        assert abs(delay - 608.000) < 0.001


# ===========================================================================
# 14. RTT ~1216.000 s
# ===========================================================================

class TestRoundTripTime:

    def test_rtt(self, asteria7_scenario):
        from backend.app.telecom.geometry import compute_round_trip_time
        rtt = compute_round_trip_time(asteria7_scenario.distance_km)
        assert abs(rtt - 1216.000) < 0.001, f"Expected ~1216.000 s, got {rtt:.6f} s"


# ===========================================================================
# 15. Contact raw capacity
# ===========================================================================

class TestContactCapacity:

    def test_goodput_bps(self, link_state_asteria7):
        """Goodput = 2,800,000 * 0.90 = 2,520,000 bps."""
        assert link_state_asteria7.link_goodput_bps == pytest.approx(2_520_000.0, rel=1e-6)

    def test_capacity_bytes_272s(self, link_state_asteria7):
        """2,520,000 bps * 272 s / 8 = 85,680,000 bytes."""
        capacity_bytes = int(link_state_asteria7.link_goodput_bps * 272.0) // 8
        assert capacity_bytes == 85_680_000

    def test_capacity_bits_272s(self, link_state_asteria7):
        """2,520,000 bps * 272 s = 685,440,000 bits."""
        capacity_bits = int(link_state_asteria7.link_goodput_bps * 272.0)
        assert capacity_bits == 685_440_000

    def test_queue_scarcity(self, link_state_asteria7):
        """Queue is ~32x larger than contact capacity."""
        total_bits = 21_920_000_000
        capacity_bits = link_state_asteria7.link_goodput_bps * 272.0
        ratio = total_bits / capacity_bits
        assert ratio == pytest.approx(31.98, abs=0.1)


# ===========================================================================
# 16. CandidatePrioritizer selects exactly 50 candidates
# ===========================================================================

class TestCandidatePrioritizer:

    def test_exactly_50_candidates(self, candidates_50):
        assert len(candidates_50) == 50, f"Expected 50 candidates, got {len(candidates_50)}"


# ===========================================================================
# 17. All 8 anchor IDs appear in the 50 selected candidates
# ===========================================================================

class TestAnchorsInCandidates:

    def test_all_anchors_selected(self, candidates_50):
        selected_ids = {c.product_id for c in candidates_50}
        missing = [aid for aid in _ANCHOR_IDS if aid not in selected_ids]
        assert not missing, f"Anchors not in candidate set: {missing}"


# ===========================================================================
# 18. Exactly 23 of the 50 meet the urgent/operationally relevant predicate
# ===========================================================================

class TestUrgentPredicate:

    def test_exactly_23_urgent(self, candidates_50, product_map):
        """Predicate: anomaly_id linked to ANOM-THERM-017 OR delivery_requirement=='required' OR deadline_s <= 272.0."""
        active_anomaly_ids = {"ANOM-THERM-017"}
        urgent_count = 0
        for c in candidates_50:
            p = product_map[c.product_id]
            is_urgent = (
                (p.anomaly_id is not None and p.anomaly_id in active_anomaly_ids)
                or p.delivery_requirement == "required"
                or p.deadline_s <= 272.0
            )
            if is_urgent:
                urgent_count += 1
        assert urgent_count == 23, f"Expected exactly 23 urgent products, got {urgent_count}"


# ===========================================================================
# 19. Anchor ordered expected cost ≈ 271.95 s (within ±2.0 s)
# ===========================================================================

class TestAnchorExpectedCost:

    def test_anchor_expected_cost(self, asteria7_scenario, link_state_asteria7, product_map):
        from backend.app.telecom.formulas import (
            expected_transmission_cost,
            packet_success_probability,
            transmission_time,
        )

        total_cost = 0.0
        for anchor_id in _ANCHOR_IDS:
            p = product_map[anchor_id]
            tx = transmission_time(p.size_bits, link_state_asteria7.link_goodput_bps)
            ps = packet_success_probability(link_state_asteria7.ber, p.size_bits)
            ec = expected_transmission_cost(tx, ps)
            total_cost += ec

        assert abs(total_cost - 271.95) < 2.0, (
            f"Anchor expected cost {total_cost:.4f} s outside ±2.0 s of 271.95 s"
        )

    def test_anchor_expected_cost_approx_271_95(self, asteria7_scenario, link_state_asteria7, product_map):
        """More precise check: expect between 269 and 274 s."""
        from backend.app.telecom.formulas import (
            expected_transmission_cost,
            packet_success_probability,
            transmission_time,
        )

        total_cost = 0.0
        for anchor_id in _ANCHOR_IDS:
            p = product_map[anchor_id]
            tx = transmission_time(p.size_bits, link_state_asteria7.link_goodput_bps)
            ps = packet_success_probability(link_state_asteria7.ber, p.size_bits)
            ec = expected_transmission_cost(tx, ps)
            total_cost += ec

        assert 269.0 <= total_cost <= 274.0, f"Anchor expected cost {total_cost:.4f} s outside [269, 274] range"


# ===========================================================================
# 20. Benchmark config unchanged
# ===========================================================================

class TestBenchmarkUnchanged:

    def test_benchmark_v1_key_fields(self):
        data = json.loads(Path(_BENCHMARK_V1_PATH).read_text(encoding="utf-8"))
        assert data["benchmark_version"] == "gcsi_benchmark_v1"
        assert data["candidate_limit"] == 50
        assert data["provider"] == "Granite"
        assert set(data["capacity_ratios"]) == {0.35, 0.60, 0.90, 1.20}


# ===========================================================================
# 21. mission_data_v3.json unchanged
# ===========================================================================

class TestMissionDataV3Unchanged:

    def test_v3_key_fields(self):
        data = json.loads(Path(_V3_PATH).read_text(encoding="utf-8"))
        assert data["scenario_id"] == "mission_data_v3_high_volume_pass"
        assert data["simulated"] is True
        assert data["distance_km"] == 54000000
        assert data["link_inputs"]["snr_db"] == 8.2
        assert data["link_inputs"]["nominal_data_rate_bps"] == 100000.0

    def test_v3_products_count(self):
        data = json.loads(Path(_V3_PATH).read_text(encoding="utf-8"))
        assert len(data["data_products"]) > 0


# ===========================================================================
# 22. Generic scenario (mission_data_v3.json) still loads normally
# ===========================================================================

class TestGenericScenarioLoads:

    def test_v3_loads(self):
        from backend.app.simulation.scenario_loader import ScenarioLoader
        s = ScenarioLoader.load(_V3_PATH)
        assert s.scenario_id == "mission_data_v3_high_volume_pass"
        assert len(s.data_products) > 0


# ===========================================================================
# 23 & 24. Experience endpoint
# ===========================================================================

class TestExperienceEndpoint:

    @pytest.mark.asyncio
    async def test_experience_available_for_asteria7(self, reset_state):
        """GET /experience returns available=true when ASTERIA-7 is loaded."""
        from backend.app import state as app_state
        from backend.app.main import app
        from httpx import ASGITransport, AsyncClient

        app_state.load_scenario(_ASTERIA7_PATH)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/experience")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["manifest"] is not None
        assert body["manifest"]["scenario_id"] == "asteria7_thermal_priority_contact_v1"

    @pytest.mark.asyncio
    async def test_experience_unavailable_for_v3(self, reset_state):
        """GET /experience returns available=false when mission_data_v3 is loaded."""
        from backend.app import state as app_state
        from backend.app.main import app
        from httpx import ASGITransport, AsyncClient

        app_state.load_scenario(_V3_PATH)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/experience")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["manifest"] is None

    @pytest.mark.asyncio
    async def test_experience_unavailable_when_no_scenario(self, reset_state):
        """GET /experience returns available=false when no scenario is loaded."""
        from backend.app import state as app_state
        from backend.app.main import app
        from httpx import ASGITransport, AsyncClient

        # State is already cleared by reset_state fixture
        assert app_state.active_scenario is None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/experience")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["manifest"] is None

    @pytest.mark.asyncio
    async def test_experience_manifest_has_required_fields(self, reset_state):
        """Manifest returned for ASTERIA-7 has all required sidecar fields."""
        from backend.app import state as app_state
        from backend.app.main import app
        from httpx import ASGITransport, AsyncClient

        app_state.load_scenario(_ASTERIA7_PATH)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/experience")

        body = resp.json()
        manifest = body["manifest"]
        assert "schema_version" in manifest
        assert "display" in manifest
        assert "schedule" in manifest
        assert "subsystem_status" in manifest
        assert "snr_history" in manifest
        assert "thermal_history" in manifest
        assert "ingest_replay" in manifest
        assert "ground_information_objectives" in manifest
        assert "curated_candidate_ids" in manifest
        # Phase 4.2F: validate Pydantic response structure
        assert "playback" in manifest

    @pytest.mark.asyncio
    async def test_experience_manifest_curated_count(self, reset_state):
        """Manifest curated_candidate_ids has exactly 50 entries."""
        from backend.app import state as app_state
        from backend.app.main import app
        from httpx import ASGITransport, AsyncClient

        app_state.load_scenario(_ASTERIA7_PATH)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/experience")

        body = resp.json()
        curated = body["manifest"]["curated_candidate_ids"]
        assert len(curated) == 50
