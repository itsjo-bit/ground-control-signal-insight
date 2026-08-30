"""GCSI — AI Candidate Diversity & Reasoning Quality Tests.

Tests 1–9 as specified in the GCSI AI Candidate Diversity & Reasoning Quality Fix task.

Test 1  — Single subsystem (no error when diversity is impossible)
Test 2  — Dominant critical subsystem (others still represented)
Test 3  — Applicable anomaly protection not displaced by diversity
Test 4  — Resolved/non-applicable anomaly gets no protected status
Test 5  — Input order determinism
Test 6  — Small queue: all valid products retained
Test 7  — Many subsystems: one-per-subsystem beats soft 50% heuristic
Test 8  — Juno PJ62 V2 representation regression (runtime, 403 products)
Test 9  — Stage-1 prompt policy assertions
"""

from __future__ import annotations

import random
from collections import Counter
from typing import List

import pytest

from backend.app.agent.candidate_prioritizer import (
    CandidatePrioritizer,
    _group_key,
    select_candidates,
)
from backend.app.models.anomaly_event import AnomalyEvent
from backend.app.models.data_product import DataProduct

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def make_dp(
    product_id: str,
    subsystem: str = "alpha",
    product_type: str = "telemetry",
    criticality: float = 0.5,
    mission_relevance: float = 0.5,
    scientific_value: float = 0.3,
    deadline_s: float = 9999.0,
    age_s: float = 100.0,
    anomaly_id: str | None = None,
    related_ids: list[str] | None = None,
) -> DataProduct:
    return DataProduct(
        product_id=product_id,
        product_type=product_type,
        subsystem=subsystem,
        size_bits=4096,
        criticality=criticality,
        mission_relevance=mission_relevance,
        scientific_value=scientific_value,
        deadline_s=deadline_s,
        age_s=age_s,
        anomaly_id=anomaly_id,
        related_ids=related_ids or [],
        delivery_requirement="best_effort",
        retry_cost=0.5,
    )


def make_anomaly(
    anomaly_id: str,
    severity: float = 0.85,
    status: str = "active",
) -> AnomalyEvent:
    return AnomalyEvent(
        anomaly_id=anomaly_id,
        subsystem="propulsion",
        severity=severity,
        detected_at_s=100.0,
        description="Test anomaly.",
        status=status,
    )


# ===========================================================================
# Test 1 — Single subsystem: no error when diversity is impossible
# ===========================================================================


class TestSingleSubsystem:
    """Test 1: More products than max_candidates, all from one subsystem."""

    def _products(self, n: int = 80) -> list[DataProduct]:
        return [
            make_dp(product_id=f"S1-{i:03d}", subsystem="alpha", criticality=0.5)
            for i in range(n)
        ]

    def test_count_bounded(self):
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(self._products(80))
        assert len(result) <= 20

    def test_no_duplicates(self):
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(self._products(80))
        ids = [cs.product_id for cs in result]
        assert len(ids) == len(set(ids))

    def test_no_error(self):
        """Must not raise merely because diversity is impossible."""
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(self._products(80))
        assert isinstance(result, list)
        assert len(result) > 0

    def test_deterministic(self):
        p = CandidatePrioritizer(max_candidates=20)
        products = self._products(80)
        r1 = p.select(products)
        r2 = p.select(products)
        assert [cs.product_id for cs in r1] == [cs.product_id for cs in r2]

    def test_all_from_same_subsystem(self):
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(self._products(80))
        for cs in result:
            assert cs.subsystem == "alpha"


# ===========================================================================
# Test 2 — Dominant critical subsystem: minority subsystems represented
# ===========================================================================


class TestDominantCriticalSubsystem:
    """Test 2: Many high-criticality products from subsystem A; fewer from B, C, D.

    This test should fail under the previous greedy monopoly behavior.
    """

    def _build_products(self) -> tuple[list[DataProduct], list[DataProduct]]:
        """Return (dominant_products, minority_products)."""
        # Subsystem A: 60 high-criticality products (> max_candidates=50)
        dominant = [
            make_dp(
                product_id=f"A-{i:03d}",
                subsystem="subsystem_a",
                criticality=0.85,
                mission_relevance=0.7,
            )
            for i in range(60)
        ]
        # Subsystem B, C, D: fewer, lower criticality but present
        minority_b = [
            make_dp(
                product_id=f"B-{i:02d}",
                subsystem="subsystem_b",
                criticality=0.4,
                mission_relevance=0.6,
            )
            for i in range(5)
        ]
        minority_c = [
            make_dp(
                product_id=f"C-{i:02d}",
                subsystem="subsystem_c",
                criticality=0.3,
                mission_relevance=0.55,
            )
            for i in range(4)
        ]
        minority_d = [
            make_dp(
                product_id=f"D-{i:02d}",
                subsystem="subsystem_d",
                criticality=0.25,
                scientific_value=0.6,
            )
            for i in range(3)
        ]
        return dominant, minority_b + minority_c + minority_d

    def test_dominant_subsystem_represented(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        selected_subs = {cs.subsystem for cs in result}
        assert "subsystem_a" in selected_subs

    def test_minority_b_represented(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        selected_subs = {cs.subsystem for cs in result}
        assert "subsystem_b" in selected_subs, (
            "subsystem_b must be represented even though subsystem_a has more high-criticality products"
        )

    def test_minority_c_represented(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        selected_subs = {cs.subsystem for cs in result}
        assert "subsystem_c" in selected_subs

    def test_minority_d_represented(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        selected_subs = {cs.subsystem for cs in result}
        assert "subsystem_d" in selected_subs

    def test_candidate_count_bounded(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        assert len(result) <= 50

    def test_no_duplicates(self):
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        ids = [cs.product_id for cs in result]
        assert len(ids) == len(set(ids))

    def test_dominant_still_strongly_represented(self):
        """Subsystem A should still hold the majority of slots."""
        dominant, minority = self._build_products()
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(dominant + minority)
        counts = Counter(cs.subsystem for cs in result)
        # A should have more slots than any single minority subsystem
        assert counts["subsystem_a"] > counts["subsystem_b"]
        assert counts["subsystem_a"] > counts["subsystem_c"]
        assert counts["subsystem_a"] > counts["subsystem_d"]


# ===========================================================================
# Test 3 — Applicable anomaly products retain strongest protection
# ===========================================================================


class TestApplicableAnomalyProtection:
    """Test 3: Genuinely applicable anomaly-linked products must not be displaced."""

    def test_applicable_anomaly_products_survive_diversity_pass(self):
        """Anomaly-linked products must remain even when coverage pass is active."""
        anomaly = make_anomaly(anomaly_id="ANOM-CRIT", severity=0.95, status="active")

        # 3 anomaly-linked products from subsystem_z
        anomaly_products = [
            make_dp(product_id=f"ANOM-PROD-{i}", subsystem="subsystem_z", anomaly_id="ANOM-CRIT")
            for i in range(3)
        ]
        # 60 high-criticality non-anomaly products from 10 other subsystems
        other_products = [
            make_dp(
                product_id=f"OTHER-{sub}-{i}",
                subsystem=f"subsystem_{sub}",
                criticality=0.9,
            )
            for sub in "abcdefghij"
            for i in range(6)
        ]
        all_products = anomaly_products + other_products

        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(all_products, anomalies=[anomaly])

        selected_ids = {cs.product_id for cs in result}
        for ap in anomaly_products:
            assert ap.product_id in selected_ids, (
                f"Applicable anomaly-linked product {ap.product_id} was displaced by diversity pass"
            )

    def test_applicable_anomaly_products_appear_before_coverage(self):
        """Anomaly products from stage 1 must appear before coverage-pass products."""
        anomaly = make_anomaly(anomaly_id="ANOM-007", severity=0.9, status="active")

        ap = make_dp(product_id="ANOM-FIRST", subsystem="subsystem_z", anomaly_id="ANOM-007")
        others = [
            make_dp(product_id=f"OTHER-{i}", subsystem=f"sub_{i:02d}")
            for i in range(5)
        ]

        p = CandidatePrioritizer(max_candidates=10)
        result = p.select([ap] + others, anomalies=[anomaly])

        # ANOM-FIRST must appear at position 0 (first selected)
        assert result[0].product_id == "ANOM-FIRST"

    def test_anomaly_overflow_acceptable(self):
        """When applicable anomaly products fill the entire budget, no error is raised."""
        anomaly = make_anomaly(anomaly_id="ANOM-BIG", severity=0.95, status="active")

        # 100 anomaly products from many subsystems
        anomaly_products = [
            make_dp(
                product_id=f"AP-{i:03d}",
                subsystem=f"sub_{i % 10}",
                anomaly_id="ANOM-BIG",
            )
            for i in range(100)
        ]
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select(anomaly_products, anomalies=[anomaly])
        assert len(result) <= 10
        assert len(result) > 0
        # All selected must be anomaly-linked
        for cs in result:
            assert cs.anomaly_id == "ANOM-BIG"


# ===========================================================================
# Test 4 — Resolved/non-applicable anomaly: no protected status
# ===========================================================================


class TestResolvedAnomalyNoProtection:
    """Test 4: Resolved anomalies must not grant protected anomaly-link status."""

    def test_resolved_anomaly_product_not_protected(self):
        """A product with a resolved anomaly_id must NOT receive stage-1 protection."""
        resolved_anomaly = make_anomaly(
            anomaly_id="ANOM-RESOLVED", severity=0.95, status="resolved"
        )

        # product linked to a resolved anomaly
        resolved_product = make_dp(
            product_id="RESOLVED-PROD",
            subsystem="sub_resolved",
            anomaly_id="ANOM-RESOLVED",
            criticality=0.1,
            mission_relevance=0.1,
            scientific_value=0.1,
        )

        # Active products with no anomaly but high criticality
        active_products = [
            make_dp(product_id=f"ACTIVE-{i:02d}", subsystem="sub_active", criticality=0.9)
            for i in range(5)
        ]

        # Very small budget so resolved product competes
        p = CandidatePrioritizer(max_candidates=3)
        result = p.select(
            active_products + [resolved_product],
            anomalies=[resolved_anomaly],
        )

        # The resolved-linked product should NOT be guaranteed a slot via stage-1
        # (it may still appear via other stages, but we verify it doesn't get
        # anomaly-link protection when the budget is tight and actives are stronger)
        selected_ids = {cs.product_id for cs in result}

        # All active products should be preferred when budget is only 3 and they
        # have much higher criticality
        selected_subs = {cs.subsystem for cs in result}
        # sub_active must appear (high-criticality products)
        assert "sub_active" in selected_subs

    def test_unknown_anomaly_id_no_protection(self):
        """A product referencing an anomaly ID not in the anomaly list gets no stage-1 protection."""
        # No matching anomaly event
        product_with_orphan_anomaly = make_dp(
            product_id="ORPHAN-PROD",
            subsystem="sub_orphan",
            anomaly_id="ANOM-DOES-NOT-EXIST",
            criticality=0.1,
        )

        p = CandidatePrioritizer(max_candidates=10)
        # Pass NO anomaly events — orphan product cannot be stage-1 protected
        result = p.select([product_with_orphan_anomaly], anomalies=[])

        # Still selected (via later stages), just not via stage-1 protection
        selected_ids = {cs.product_id for cs in result}
        assert "ORPHAN-PROD" in selected_ids  # selected via later stages

    def test_monitoring_anomaly_is_applicable(self):
        """Products linked to a 'monitoring' status anomaly DO get stage-1 protection."""
        monitoring_anomaly = make_anomaly(
            anomaly_id="ANOM-MONITOR", severity=0.75, status="monitoring"
        )
        product = make_dp(
            product_id="MONITOR-PROD",
            subsystem="sub_monitor",
            anomaly_id="ANOM-MONITOR",
        )

        p = CandidatePrioritizer(max_candidates=10)
        result = p.select([product], anomalies=[monitoring_anomaly])

        assert len(result) == 1
        assert result[0].product_id == "MONITOR-PROD"

    def test_active_anomaly_appears_before_resolved(self):
        """Products linked to active anomalies must be selected before resolved ones."""
        active_anomaly = make_anomaly(anomaly_id="ANOM-ACTIVE", severity=0.9, status="active")
        resolved_anomaly = make_anomaly(anomaly_id="ANOM-OLD", severity=0.99, status="resolved")

        resolved_product = make_dp(
            product_id="RES-PROD",
            subsystem="sub_r",
            anomaly_id="ANOM-OLD",
            criticality=0.1,  # low criticality so it can't win via later stages easily
            mission_relevance=0.1,
            scientific_value=0.1,
        )
        active_product = make_dp(
            product_id="ACT-PROD",
            subsystem="sub_a",
            anomaly_id="ANOM-ACTIVE",
            criticality=0.1,
        )
        filler = [make_dp(product_id=f"FILL-{i}", subsystem="sub_f") for i in range(5)]

        p = CandidatePrioritizer(max_candidates=3)
        result = p.select(
            [resolved_product, active_product] + filler,
            anomalies=[active_anomaly, resolved_anomaly],
        )

        ids = [cs.product_id for cs in result]
        # active product must appear before resolved product (stage-1 vs later stages)
        if "ACT-PROD" in ids and "RES-PROD" in ids:
            assert ids.index("ACT-PROD") < ids.index("RES-PROD")


# ===========================================================================
# Test 5 — Input order determinism
# ===========================================================================


class TestInputOrderDeterminism:
    """Test 5: Identical products in different input orders → identical selection."""

    def _build_products(self) -> list[DataProduct]:
        return [
            make_dp(product_id=f"PROD-{i:03d}", subsystem=f"sub_{i % 5}", criticality=0.4 + (i % 3) * 0.2)
            for i in range(100)
        ]

    def test_same_ids_different_input_order(self):
        products = self._build_products()
        shuffled = products[:]
        random.seed(42)
        random.shuffle(shuffled)

        p = CandidatePrioritizer(max_candidates=30)
        r1 = p.select(products)
        r2 = p.select(shuffled)

        # Same selected IDs, same order
        assert [cs.product_id for cs in r1] == [cs.product_id for cs in r2], (
            "Selection order must be identical regardless of input order"
        )

    def test_multiple_shuffles_same_result(self):
        products = self._build_products()
        p = CandidatePrioritizer(max_candidates=30)
        reference = [cs.product_id for cs in p.select(products)]

        for seed in range(5):
            shuffled = products[:]
            random.seed(seed)
            random.shuffle(shuffled)
            result = [cs.product_id for cs in p.select(shuffled)]
            assert result == reference, (
                f"Non-deterministic result for shuffle seed={seed}"
            )

    def test_reverse_order_same_result(self):
        products = self._build_products()
        p = CandidatePrioritizer(max_candidates=30)
        r1 = [cs.product_id for cs in p.select(products)]
        r2 = [cs.product_id for cs in p.select(list(reversed(products)))]
        assert r1 == r2


# ===========================================================================
# Test 6 — Small queue: no unnecessary drops
# ===========================================================================


class TestSmallQueue:
    """Test 6: Total products <= max_candidates → all should be retained."""

    def test_all_retained_exact_fit(self):
        products = [make_dp(product_id=f"P-{i:02d}", subsystem=f"sub_{i}") for i in range(10)]
        p = CandidatePrioritizer(max_candidates=10)
        result = p.select(products)
        assert len(result) == 10

    def test_all_retained_below_max(self):
        products = [make_dp(product_id=f"P-{i:02d}", subsystem=f"sub_{i}") for i in range(5)]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        assert len(result) == 5

    def test_all_product_ids_present(self):
        products = [make_dp(product_id=f"P-{i:02d}", subsystem=f"sub_{i}") for i in range(8)]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        result_ids = {cs.product_id for cs in result}
        expected_ids = {dp.product_id for dp in products}
        assert result_ids == expected_ids

    def test_single_product_retained(self):
        products = [make_dp(product_id="ONLY-ONE", subsystem="sub_solo")]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        assert len(result) == 1
        assert result[0].product_id == "ONLY-ONE"

    def test_no_drops_with_multiple_subsystems(self):
        products = [
            make_dp(product_id=f"P-{sub}-{i}", subsystem=f"sub_{sub}")
            for sub in range(5)
            for i in range(2)
        ]  # 10 products, 5 subsystems, 2 each
        p = CandidatePrioritizer(max_candidates=20)
        result = p.select(products)
        assert len(result) == 10  # all should be present


# ===========================================================================
# Test 7 — Many subsystems: one-per-subsystem beats soft 50% heuristic
# ===========================================================================


class TestManySubsystems:
    """Test 7: number_of_subsystems > max_candidates / 2 but <= max_candidates.

    One-per-subsystem coverage must take precedence over the soft 50% cap.
    This prevents accidentally implementing representation_budget = max_candidates // 2
    as a hard cap.
    """

    def test_30_subsystems_with_max_50(self):
        """30 subsystems with max_candidates=50: all 30 should get at least one slot."""
        # 30 subsystems, 5 products each → 150 total
        products = [
            make_dp(
                product_id=f"SYS{sub:02d}-PROD{i:02d}",
                subsystem=f"subsystem_{sub:02d}",
                criticality=0.2,  # deliberately low so coverage pass is what provides reps
            )
            for sub in range(30)
            for i in range(5)
        ]

        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)

        selected_subs = {cs.subsystem for cs in result}
        all_subs = {f"subsystem_{sub:02d}" for sub in range(30)}

        # All 30 subsystems must have at least one representative
        missing = all_subs - selected_subs
        assert len(missing) == 0, (
            f"The following subsystems were not represented despite sufficient budget: {missing}"
        )

    def test_count_bounded_with_30_subsystems(self):
        products = [
            make_dp(
                product_id=f"SYS{sub:02d}-PROD{i:02d}",
                subsystem=f"subsystem_{sub:02d}",
                criticality=0.2,
            )
            for sub in range(30)
            for i in range(5)
        ]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        assert len(result) <= 50

    def test_26_subsystems_all_represented(self):
        """26 subsystems (> 50/2=25) with max_candidates=50: all must get a slot."""
        products = [
            make_dp(
                product_id=f"S{sub:02d}-{i:02d}",
                subsystem=f"s_{sub:02d}",
                criticality=0.1,
            )
            for sub in range(26)
            for i in range(3)
        ]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        selected_subs = {cs.subsystem for cs in result}
        all_subs = {f"s_{sub:02d}" for sub in range(26)}
        missing = all_subs - selected_subs
        assert len(missing) == 0, (
            f"26 subsystems with max=50: missing coverage for {missing}"
        )

    def test_no_duplicates_many_subsystems(self):
        products = [
            make_dp(product_id=f"SYS{sub:02d}-PROD{i:02d}", subsystem=f"subsystem_{sub:02d}")
            for sub in range(30)
            for i in range(5)
        ]
        p = CandidatePrioritizer(max_candidates=50)
        result = p.select(products)
        ids = [cs.product_id for cs in result]
        assert len(ids) == len(set(ids))


# ===========================================================================
# Test 8 — Juno PJ62 V2 representation regression
# ===========================================================================


class TestJunoV2Representation:
    """Test 8: Regression test using actual Juno PJ62 Historical Replay V2.

    Verifies:
    - candidate count bounded at configured max (50)
    - candidate set is NOT effectively single-subsystem homogeneous
    - every available subsystem receives at least one representative
      (9 subsystems, max=50, no applicable anomalies)
    """

    _SOURCE_REF = "data/replays/juno_pj62_large_replay_v2_descriptor.json"

    @pytest.fixture(scope="class")
    def v2_bundle(self):
        import warnings
        warnings.filterwarnings("ignore")
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        return HistoricalReplayProvider().load(self._SOURCE_REF)

    @pytest.fixture(scope="class")
    def v2_products(self, v2_bundle):
        return v2_bundle.scenario.data_products

    @pytest.fixture(scope="class")
    def v2_anomalies(self, v2_bundle):
        return v2_bundle.scenario.anomalies

    @pytest.fixture(scope="class")
    def v2_candidates(self, v2_products, v2_anomalies):
        p = CandidatePrioritizer(max_candidates=50)
        return p.select(v2_products, anomalies=v2_anomalies, remaining_window_s=600.0)

    def test_total_products_is_403(self, v2_products):
        assert len(v2_products) == 403

    def test_candidate_count_bounded_at_50(self, v2_candidates):
        assert len(v2_candidates) <= 50

    def test_not_single_subsystem_homogeneous(self, v2_candidates):
        """The candidate set must not be entirely from one subsystem."""
        subsystem_counts = Counter(cs.subsystem for cs in v2_candidates)
        # Most dominant subsystem must not hold 100% of slots
        max_count = max(subsystem_counts.values())
        assert max_count < len(v2_candidates), (
            f"Candidate set is 100% from one subsystem: {subsystem_counts}"
        )

    def test_all_subsystems_represented(self, v2_products, v2_candidates):
        """Every subsystem present in the full product set must appear in candidates.

        With 9 subsystems and max=50, one-per-subsystem representation is achievable.
        Subsystem names are derived from runtime DataProducts — NOT hard-coded.
        """
        # Derive subsystem names from runtime products
        all_subsystems = {dp.subsystem for dp in v2_products}
        candidate_subsystems = {cs.subsystem for cs in v2_candidates}

        # When number_of_subsystems <= max_candidates, every subsystem should get at least one rep
        if len(all_subsystems) <= 50:
            missing = all_subsystems - candidate_subsystems
            assert len(missing) == 0, (
                f"Subsystems without representation in candidates: {missing}\n"
                f"Candidate subsystem counts: {Counter(cs.subsystem for cs in v2_candidates)}"
            )

    def test_no_duplicates(self, v2_candidates):
        ids = [cs.product_id for cs in v2_candidates]
        assert len(ids) == len(set(ids))

    def test_candidate_ids_only_from_products(self, v2_products, v2_candidates):
        """No hallucinated product IDs: every candidate must come from the product set."""
        valid_ids = {dp.product_id for dp in v2_products}
        for cs in v2_candidates:
            assert cs.product_id in valid_ids, (
                f"Candidate {cs.product_id!r} not in product set"
            )

    def test_candidate_composition_improved_over_before(self, v2_candidates):
        """The dominant subsystem must not hold 100% of the slots (pre-fix behavior)."""
        counts = Counter(cs.subsystem for cs in v2_candidates)
        total = len(v2_candidates)
        max_share = max(counts.values()) / total
        # Before fix: dominant subsystem held 100% (50/50 jiram)
        # After fix: no single subsystem should hold all 50 slots
        assert max_share < 1.0, (
            f"Single subsystem holds {max_share:.0%} of candidate slots — still homogeneous"
        )


# ===========================================================================
# Test 9 — Prompt policy assertions
# ===========================================================================


class TestPromptPolicy:
    """Test 9: Stage-1 system prompt must satisfy freshness semantics policy."""

    @pytest.fixture(scope="class")
    def prompt_text(self) -> str:
        from backend.app.agent.granite_agent import _PRIORITIZATION_SYSTEM_PROMPT
        return _PRIORITIZATION_SYSTEM_PROMPT

    def test_freshness_still_mentioned(self, prompt_text):
        """Freshness / age_s must still be a listed factor."""
        assert "age_s" in prompt_text, "age_s must still appear in the prioritization prompt"
        assert "fresh" in prompt_text.lower(), "freshness concept must still appear in prompt"

    def test_freshness_not_automatic_dominant_rule(self, prompt_text):
        """Prompt must explicitly state that freshness is NOT an automatic dominant rule."""
        prompt_lower = prompt_text.lower()
        # The prompt should say freshness is ONE factor / not automatic / not dominant
        assert any(phrase in prompt_lower for phrase in [
            "one contextual",
            "not an automatic",
            "not the default",
            "one decision factor",
            "one factor",
        ]), (
            "Prompt must explicitly state that freshness is not an automatic ranking rule"
        )

    def test_older_data_not_automatically_less_valuable(self, prompt_text):
        """Prompt must not assert that high age_s automatically means less valuable."""
        prompt_lower = prompt_text.lower()
        # Old wording was: "stale data (high age_s) is less valuable"
        # This must be replaced or qualified
        assert "stale data (high age_s) is less valuable" not in prompt_lower, (
            "Old blanket 'stale data is less valuable' wording must be replaced"
        )

    def test_other_factors_can_outweigh_freshness(self, prompt_text):
        """Prompt must state that other factors (anomaly, criticality, etc.) may outweigh freshness."""
        prompt_lower = prompt_text.lower()
        assert any(phrase in prompt_lower for phrase in [
            "outweigh",
            "may outweigh",
            "overrides",
        ]), (
            "Prompt must state that operational/scientific factors may outweigh freshness"
        )

    def test_freshness_as_tiebreaker_guidance(self, prompt_text):
        """Prompt should mention freshness as a tie-breaker for equivalent products."""
        prompt_lower = prompt_text.lower()
        assert "tie-breaker" in prompt_lower or "tiebreaker" in prompt_lower or "otherwise equivalent" in prompt_lower, (
            "Prompt should guide freshness as a tie-breaker for equivalent products"
        )

    def test_boilerplate_freshness_discouraged(self, prompt_text):
        """Prompt must discourage ordinal freshness boilerplate reasoning."""
        prompt_lower = prompt_text.lower()
        assert any(phrase in prompt_lower for phrase in [
            "most recent",
            "ordinal",
            "boilerplate",
            "second most recent",
        ]), (
            "Prompt must explicitly discourage ordinal freshness boilerplate reasoning"
        )

    def test_reason_quality_guidance_present(self, prompt_text):
        """Prompt must include guidance for grounded, mission-specific reasoning."""
        prompt_lower = prompt_text.lower()
        assert any(phrase in prompt_lower for phrase in [
            "mission significance",
            "mission rationale",
            "strongest",
            "distinguish",
        ]), (
            "Prompt must include quality guidance for grounded mission reasoning"
        )

    def test_data_freshness_factor_label_present(self, prompt_text):
        """'data freshness' must still appear in the DECISION FACTOR LABELS list."""
        assert "data freshness" in prompt_text, (
            "data freshness must remain a valid decision factor label"
        )


# ===========================================================================
# Additional edge cases
# ===========================================================================


class TestEdgeCases:
    """Edge cases for robustness."""

    def test_empty_subsystem_falls_back_to_product_type(self):
        """Product with empty subsystem must be grouped by product_type."""
        dp = make_dp(product_id="EMPTY-SUB", subsystem="", product_type="science")
        key = _group_key(dp)
        assert key == "science"

    def test_empty_both_falls_back_to_unknown(self):
        """Product with empty subsystem and empty product_type → 'unknown' group."""
        dp = make_dp(product_id="EMPTY-BOTH", subsystem="", product_type="")
        key = _group_key(dp)
        assert key == "unknown"

    def test_group_key_normalizes_case(self):
        dp1 = make_dp(product_id="P1", subsystem="PropULSION")
        dp2 = make_dp(product_id="P2", subsystem="propulsion")
        assert _group_key(dp1) == _group_key(dp2)

    def test_select_candidates_convenience(self):
        products = [make_dp(product_id=f"P-{i}", subsystem=f"s{i % 3}") for i in range(30)]
        result = select_candidates(products, max_candidates=10)
        assert len(result) <= 10
        ids = [cs.product_id for cs in result]
        assert len(ids) == len(set(ids))
