"""Unit tests for Phase 2E-B: mission_data_v3.json scenario validation.

Covers:
- Exactly 150 DataProducts
- v2 scenario is completely unaffected
- Multiple product types and subsystems exist
- Every DataProduct has a non-empty description
- Multiple anomalies exist
- Anomaly-linked products reference correct anomaly IDs
- Total queued data substantially exceeds communication capacity
- Deadline diversity (no single deadline value)
- Criticality diversity (spanning all ranges)
- Scientific value diversity on science products
- Data age diversity
- Product size diversity
- CandidatePrioritizer caps AI context at <= 50 candidates
- Candidate descriptions are propagated
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.agent.candidate_prioritizer import CandidatePrioritizer
from backend.app.simulation.scenario_loader import ScenarioLoader

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO = Path(__file__).parents[2]
_V3_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v3.json")
_V2_PATH = str(_REPO / "data" / "scenarios" / "mission_data_v2.json")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def v3():
    return ScenarioLoader.load(_V3_PATH)


@pytest.fixture(scope="module")
def v3_products(v3):
    return v3.data_products


@pytest.fixture(scope="module")
def v3_anomalies(v3):
    return v3.anomalies


@pytest.fixture(scope="module")
def v2():
    return ScenarioLoader.load(_V2_PATH)


# ===========================================================================
# Count
# ===========================================================================


class TestV3ProductCount:
    def test_exactly_150_products(self, v3_products):
        assert len(v3_products) == 150

    def test_all_product_ids_unique(self, v3_products):
        ids = [dp.product_id for dp in v3_products]
        assert len(set(ids)) == len(ids), "Duplicate product IDs found"

    def test_v2_unchanged_50_products(self, v2):
        """v2 scenario must remain completely untouched."""
        assert len(v2.data_products) == 50


# ===========================================================================
# Diversity — product types and subsystems
# ===========================================================================


class TestV3Diversity:
    def test_multiple_product_types(self, v3_products):
        types = {dp.product_type for dp in v3_products}
        # Must have at least 5 distinct types
        assert len(types) >= 5, f"Too few product types: {types}"

    def test_expected_product_types_present(self, v3_products):
        types = {dp.product_type for dp in v3_products}
        required = {"telemetry", "diagnostic", "housekeeping", "science", "image"}
        missing = required - types
        assert not missing, f"Missing expected product types: {missing}"

    def test_multiple_subsystems(self, v3_products):
        subsystems = {dp.subsystem for dp in v3_products}
        assert len(subsystems) >= 7, f"Too few subsystems: {subsystems}"

    def test_expected_subsystems_present(self, v3_products):
        subsystems = {dp.subsystem for dp in v3_products}
        required = {"propulsion", "power", "thermal", "communications"}
        missing = required - subsystems
        assert not missing, f"Missing expected subsystems: {missing}"


# ===========================================================================
# Semantic descriptions
# ===========================================================================


class TestV3Descriptions:
    def test_every_product_has_non_empty_description(self, v3_products):
        empty = [dp.product_id for dp in v3_products if not dp.description.strip()]
        assert not empty, f"Products with empty description: {empty}"

    def test_descriptions_are_meaningful_length(self, v3_products):
        """Each description should be at least 60 characters."""
        short = [
            (dp.product_id, len(dp.description))
            for dp in v3_products
            if len(dp.description) < 60
        ]
        assert not short, f"Products with suspiciously short descriptions: {short}"


# ===========================================================================
# Anomalies
# ===========================================================================


class TestV3Anomalies:
    def test_multiple_anomalies(self, v3_anomalies):
        assert len(v3_anomalies) >= 2, "Expected at least 2 anomalies"

    def test_anomaly_ids_unique(self, v3_anomalies):
        ids = [a.anomaly_id for a in v3_anomalies]
        assert len(set(ids)) == len(ids), "Duplicate anomaly IDs"

    def test_anomalies_have_descriptions(self, v3_anomalies):
        for a in v3_anomalies:
            assert a.description.strip(), f"Anomaly {a.anomaly_id} has empty description"

    def test_anomaly_severities_in_range(self, v3_anomalies):
        for a in v3_anomalies:
            assert 0.0 <= a.severity <= 1.0

    def test_anomaly_linked_products_exist(self, v3, v3_products):
        product_ids = {dp.product_id for dp in v3_products}
        for anomaly in v3.anomalies:
            for pid in anomaly.related_product_ids:
                assert pid in product_ids, (
                    f"Anomaly {anomaly.anomaly_id} references product {pid} "
                    "that does not exist in data_products"
                )

    def test_products_referencing_anomalies_are_consistent(self, v3, v3_products):
        """Products with anomaly_id must reference an actual anomaly."""
        anomaly_ids = {a.anomaly_id for a in v3.anomalies}
        for dp in v3_products:
            if dp.anomaly_id is not None:
                assert dp.anomaly_id in anomaly_ids, (
                    f"Product {dp.product_id} references unknown anomaly {dp.anomaly_id}"
                )

    def test_some_products_are_anomaly_linked(self, v3_products):
        linked = [dp for dp in v3_products if dp.anomaly_id is not None]
        assert len(linked) >= 20, (
            f"Expected at least 20 anomaly-linked products; got {len(linked)}"
        )


# ===========================================================================
# Data volume — communication pressure
# ===========================================================================


class TestV3DataVolume:
    def test_total_data_exceeds_comm_capacity(self, v3, v3_products):
        """Total queued data must be substantially larger than available capacity."""
        total_bits = sum(dp.size_bits for dp in v3_products)
        goodput_bps = v3.link_inputs["nominal_data_rate_bps"] * 0.9
        window_s = v3.link_inputs["remaining_window_s"]
        capacity_bits = goodput_bps * window_s

        ratio = total_bits / capacity_bits
        assert ratio >= 3.0, (
            f"Oversubscription ratio {ratio:.1f}x is too low; "
            f"total={total_bits:,} bits, capacity={capacity_bits:,} bits"
        )

    def test_size_varies_substantially(self, v3_products):
        sizes = [dp.size_bits for dp in v3_products]
        min_size = min(sizes)
        max_size = max(sizes)
        # Max must be at least 100x min to prove wide size range
        assert max_size >= 100 * min_size, (
            f"Size range too narrow: min={min_size}, max={max_size}"
        )


# ===========================================================================
# Deadline diversity
# ===========================================================================


class TestV3DeadlineDiversity:
    def test_deadlines_are_not_all_identical(self, v3_products):
        deadlines = {dp.deadline_s for dp in v3_products}
        assert len(deadlines) >= 10, (
            f"Expected at least 10 distinct deadlines; got {len(deadlines)}"
        )

    def test_very_urgent_products_exist(self, v3_products):
        very_urgent = [dp for dp in v3_products if dp.deadline_s <= 120]
        assert len(very_urgent) >= 3, (
            f"Expected at least 3 products with deadline <= 120s; got {len(very_urgent)}"
        )

    def test_low_urgency_products_exist(self, v3_products):
        low_urgency = [dp for dp in v3_products if dp.deadline_s > 900]
        assert len(low_urgency) >= 20, (
            f"Expected at least 20 products with deadline > 900s; got {len(low_urgency)}"
        )


# ===========================================================================
# Criticality diversity
# ===========================================================================


class TestV3CriticalityDiversity:
    def test_mission_critical_products_exist(self, v3_products):
        mc = [dp for dp in v3_products if dp.criticality >= 0.90]
        assert len(mc) >= 5

    def test_medium_criticality_products_exist(self, v3_products):
        med = [dp for dp in v3_products if 0.40 <= dp.criticality < 0.70]
        assert len(med) >= 20

    def test_low_criticality_products_exist(self, v3_products):
        low = [dp for dp in v3_products if dp.criticality < 0.40]
        assert len(low) >= 5

    def test_criticality_spans_full_range(self, v3_products):
        criticalities = [dp.criticality for dp in v3_products]
        assert min(criticalities) <= 0.30
        assert max(criticalities) >= 0.90


# ===========================================================================
# Scientific value diversity
# ===========================================================================


class TestV3ScientificValueDiversity:
    def test_high_scientific_value_products_exist(self, v3_products):
        high_sci = [dp for dp in v3_products if dp.scientific_value >= 0.80]
        assert len(high_sci) >= 10, (
            f"Expected at least 10 products with scientific_value >= 0.80; "
            f"got {len(high_sci)}"
        )

    def test_science_products_have_meaningful_scientific_value(self, v3_products):
        science_products = [dp for dp in v3_products if dp.product_type in ("science", "image")]
        assert science_products, "No science/image products found"
        avg_sci = sum(dp.scientific_value for dp in science_products) / len(science_products)
        assert avg_sci >= 0.60, f"Science products have low avg scientific_value: {avg_sci:.2f}"

    def test_non_science_products_have_low_scientific_value(self, v3_products):
        non_science = [dp for dp in v3_products if dp.product_type in ("housekeeping",)]
        high_sci = [dp for dp in non_science if dp.scientific_value >= 0.5]
        assert len(high_sci) == 0, (
            f"Housekeeping products should not have high scientific_value: "
            f"{[dp.product_id for dp in high_sci]}"
        )


# ===========================================================================
# Age diversity
# ===========================================================================


class TestV3AgeDiversity:
    def test_very_recent_products_exist(self, v3_products):
        recent = [dp for dp in v3_products if dp.age_s <= 30]
        assert len(recent) >= 5, (
            f"Expected at least 5 very recent products (age <= 30s); got {len(recent)}"
        )

    def test_older_products_exist(self, v3_products):
        old = [dp for dp in v3_products if dp.age_s >= 3600]
        assert len(old) >= 5, (
            f"Expected at least 5 older products (age >= 3600s); got {len(old)}"
        )

    def test_age_spans_wide_range(self, v3_products):
        ages = [dp.age_s for dp in v3_products]
        assert max(ages) / (min(ages) + 1) >= 100, (
            f"Age range too narrow: min={min(ages)}, max={max(ages)}"
        )


# ===========================================================================
# CandidatePrioritizer cap
# ===========================================================================


class TestV3CandidateCap:
    def test_prioritizer_caps_at_50(self, v3):
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            v3.data_products,
            anomalies=v3.anomalies,
            remaining_window_s=float(v3.link_inputs["remaining_window_s"]),
        )
        assert len(candidates) <= 50

    def test_prioritizer_returns_exactly_50_for_150_products(self, v3):
        """With 150 products and max=50, should always return exactly 50."""
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            v3.data_products,
            anomalies=v3.anomalies,
            remaining_window_s=float(v3.link_inputs["remaining_window_s"]),
        )
        assert len(candidates) == 50

    def test_candidate_ids_are_valid_product_ids(self, v3):
        product_ids = {dp.product_id for dp in v3.data_products}
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            v3.data_products,
            anomalies=v3.anomalies,
            remaining_window_s=float(v3.link_inputs["remaining_window_s"]),
        )
        for cs in candidates:
            assert cs.product_id in product_ids

    def test_candidates_include_anomaly_linked_products(self, v3):
        """High-severity anomaly products should survive candidate selection."""
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            v3.data_products,
            anomalies=v3.anomalies,
            remaining_window_s=float(v3.link_inputs["remaining_window_s"]),
        )
        anomaly_ids_in_candidates = {cs.product_id for cs in candidates if cs.anomaly_id}
        assert len(anomaly_ids_in_candidates) >= 10, (
            f"Expected at least 10 anomaly-linked candidates; "
            f"got {len(anomaly_ids_in_candidates)}"
        )

    def test_candidate_descriptions_populated(self, v3):
        prioritizer = CandidatePrioritizer(max_candidates=50)
        candidates = prioritizer.select(
            v3.data_products,
            anomalies=v3.anomalies,
            remaining_window_s=float(v3.link_inputs["remaining_window_s"]),
        )
        for cs in candidates:
            assert cs.description.strip(), (
                f"Candidate {cs.product_id} has empty description after prioritizer"
            )

    def test_custom_cap_respected(self, v3):
        for cap in (10, 25, 40):
            prioritizer = CandidatePrioritizer(max_candidates=cap)
            candidates = prioritizer.select(v3.data_products, anomalies=v3.anomalies)
            assert len(candidates) <= cap


# ===========================================================================
# Scenario loads through existing infrastructure
# ===========================================================================


class TestV3LoadsCleanly:
    def test_v3_loads_through_scenario_loader(self):
        scenario = ScenarioLoader.load(_V3_PATH)
        assert scenario.scenario_id == "mission_data_v3_high_volume_pass"
        assert scenario.simulated is True

    def test_v3_link_inputs_complete(self, v3):
        required = {
            "timestamp", "snr_db", "rssi_dbm", "nominal_data_rate_bps",
            "latency_s", "link_stability", "remaining_window_s",
        }
        missing = required - set(v3.link_inputs.keys())
        assert not missing, f"Missing link_inputs keys: {missing}"

    def test_v3_mission_state_complete(self, v3):
        ms = v3.mission_state
        assert ms.mission_id
        assert ms.mission_phase
        assert ms.comm_window_remaining_s > 0

    def test_v2_still_loads_unchanged(self, v2):
        assert v2.scenario_id == "mission_data_v2_anomaly_pass"
        assert len(v2.data_products) == 50
        assert len(v2.anomalies) == 3

    def test_v3_json_is_valid(self):
        """Raw JSON must be valid."""
        with open(_V3_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["data_products"]) == 150
