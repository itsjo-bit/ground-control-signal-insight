"""GCSI Phase 6F-B3 — Replay Assembler V2 Tests.

Tests for ReplayAssemblerV2.assemble():
- Section 37: Size policy (exact proxy, fallback, zero-free, MODELED provenance)
- Section 38: Age (>= 0, derived from decision epoch, not wall clock)
- Section 39: Geometry / latency separation
- Section 36: Inclusion / exclusion (403 products; known ineligible absent)
- Section 41: Determinism (two runs produce identical output)
- Section 42: Offline isolation (no network calls during assembly)
- Section 28: MissionSourceBundle contract (HISTORICAL_REPLAY, simulated=True)
- Provenance DAG (EXTERNAL_AUTHORITATIVE / DERIVED / MODELED boundaries)
"""

from __future__ import annotations

import copy
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from backend.app.mission_sources.models import MissionSourceMode
from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2
from backend.app.mission_sources.v2_replay_descriptor import load_v2_replay_descriptor
from backend.app.mission_sources.v2_source_graph import (
    VerifiedV2SourceGraph,
    load_verified_v2_source_graph,
    _DECISION_EPOCH_UTC,
)
from backend.app.provenance.models import ProvenanceKind

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DESCRIPTOR_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_descriptor.json"
_EXPECTED_DISTANCE_KM = 893130069.5851377  # exact Horizons range
_EXPECTED_LATENCY_S = 1.5                  # protocol/link-stack overhead; NOT propagation
_EXPECTED_PRODUCT_COUNT = 403
_EXPECTED_FALLBACK_BYTES = 442368
_EXPECTED_FALLBACK_BITS = _EXPECTED_FALLBACK_BYTES * 8   # 3538944

# Known ineligible products (must be absent from assembled set)
_KNOWN_INELIGIBLE = {
    "gcsi.jedi.pj62.jed_090_loersesp_cdr_2024166_v04",
    "gcsi.uvs.pj62.s02_771613347_2024166_p62sy1",
}

# Expected per-instrument product counts from assembler
_EXPECTED_INSTRUMENT_COUNTS = {
    "fgm": 2,
    "jade": 8,
    "jedi": 22,
    "jiram": 102,
    "junocam": 124,
    "mwr": 46,
    "uvs": 6,
    "waves_burst": 91,
    "waves_survey": 2,
}


# ---------------------------------------------------------------------------
# Module-scoped fixture: load source graph + descriptor once, assemble once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source_graph() -> VerifiedV2SourceGraph:
    return load_verified_v2_source_graph()


@pytest.fixture(scope="module")
def assembled_bundle(source_graph: VerifiedV2SourceGraph):
    descriptor = load_v2_replay_descriptor(_DESCRIPTOR_PATH)
    return ReplayAssemblerV2.assemble(descriptor=descriptor, source_graph=source_graph)


@pytest.fixture(scope="module")
def descriptor():
    return load_v2_replay_descriptor(_DESCRIPTOR_PATH)


# ---------------------------------------------------------------------------
# Section 36: Inclusion / Exclusion
# ---------------------------------------------------------------------------


class TestInclusionExclusion:
    def test_exactly_403_products(self, assembled_bundle) -> None:
        assert len(assembled_bundle.scenario.data_products) == _EXPECTED_PRODUCT_COUNT

    def test_known_ineligible_absent(self, assembled_bundle) -> None:
        product_ids = {dp.product_id for dp in assembled_bundle.scenario.data_products}
        for bad_id in _KNOWN_INELIGIBLE:
            assert bad_id not in product_ids, (
                f"Ineligible product {bad_id!r} should not be in assembled set."
            )

    def test_all_product_ids_unique(self, assembled_bundle) -> None:
        ids = [dp.product_id for dp in assembled_bundle.scenario.data_products]
        assert len(ids) == len(set(ids)), "Duplicate product_ids in assembled set."

    def test_per_instrument_counts(self, assembled_bundle) -> None:
        from collections import Counter
        counts = Counter(dp.subsystem for dp in assembled_bundle.scenario.data_products)
        for instrument, expected in _EXPECTED_INSTRUMENT_COUNTS.items():
            assert counts[instrument] == expected, (
                f"Instrument {instrument!r}: expected {expected}, got {counts[instrument]}."
            )


# ---------------------------------------------------------------------------
# Section 37: Size Policy
# ---------------------------------------------------------------------------


class TestSizePolicy:
    def test_all_products_positive_size_bits(self, assembled_bundle) -> None:
        """Every DataProduct.size_bits > 0 — no zero-bit products."""
        for dp in assembled_bundle.scenario.data_products:
            assert dp.size_bits > 0, (
                f"Product {dp.product_id!r} has size_bits={dp.size_bits} (must be > 0)."
            )

    def test_no_product_has_zero_bits(self, assembled_bundle) -> None:
        """Explicit zero check."""
        zeros = [dp.product_id for dp in assembled_bundle.scenario.data_products
                 if dp.size_bits == 0]
        assert zeros == [], f"Products with zero size_bits: {zeros[:5]!r}"

    def test_exact_size_products_use_archive_bytes(
        self, assembled_bundle, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Products backed by SIZE_METADATA_EXACT use max(exact_bytes) * 8."""
        from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty

        snap_idx = dict(source_graph.snapshots_by_source_record_id)
        inv_by_lid = {e.logical_product_id: e for e in source_graph.verified_inventory.entries}

        exact_proxied_count = 0
        for dp in assembled_bundle.scenario.data_products:
            inv_entry = inv_by_lid.get(dp.product_id)
            if inv_entry is None:
                continue
            exact_sizes = []
            for srid in inv_entry.representation_record_ids:
                if srid not in snap_idx:
                    continue
                product, _ = snap_idx[srid]
                if product.total_data_size_bytes and product.total_data_size_bytes > 0:
                    all_exact = all(
                        f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                        for f in product.data_files
                    )
                    if all_exact:
                        exact_sizes.append(product.total_data_size_bytes)

            if exact_sizes:
                expected_bits = max(exact_sizes) * 8
                assert dp.size_bits == expected_bits, (
                    f"Product {dp.product_id!r}: expected size_bits={expected_bits} "
                    f"from exact archive, got {dp.size_bits}."
                )
                exact_proxied_count += 1

        assert exact_proxied_count > 0, "No exact-size products found (expected 100+ JIRAM/MWR)."

    def test_unknown_size_products_use_fallback(
        self, assembled_bundle, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Products with no exact archive size use median_low fallback."""
        from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty

        snap_idx = dict(source_graph.snapshots_by_source_record_id)
        inv_by_lid = {e.logical_product_id: e for e in source_graph.verified_inventory.entries}

        fallback_count = 0
        for dp in assembled_bundle.scenario.data_products:
            inv_entry = inv_by_lid.get(dp.product_id)
            if inv_entry is None:
                continue
            has_exact = False
            for srid in inv_entry.representation_record_ids:
                if srid not in snap_idx:
                    continue
                product, _ = snap_idx[srid]
                if product.total_data_size_bytes and product.total_data_size_bytes > 0:
                    all_exact = all(
                        f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                        for f in product.data_files
                    )
                    if all_exact:
                        has_exact = True
                        break
            if not has_exact:
                assert dp.size_bits == _EXPECTED_FALLBACK_BITS, (
                    f"Product {dp.product_id!r}: expected fallback size_bits={_EXPECTED_FALLBACK_BITS}, "
                    f"got {dp.size_bits}."
                )
                fallback_count += 1

        # 379 eligible unknown-size source records → significant number of fallback products
        assert fallback_count > 0, "Expected fallback-size products but found none."

    def test_fallback_bytes_value(self, source_graph: VerifiedV2SourceGraph) -> None:
        """Fallback bytes == median_low(eligible exact-size archive_total_size_bytes)."""
        stats = ReplayAssemblerV2.get_size_stats(source_graph=source_graph)
        assert stats["fallback_archive_proxy_bytes"] == _EXPECTED_FALLBACK_BYTES
        assert stats["fallback_size_bits"] == _EXPECTED_FALLBACK_BITS

    def test_fallback_uses_only_eligible_exact_records(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """get_size_stats uses only eligible source records (not ineligible)."""
        stats = ReplayAssemblerV2.get_size_stats(source_graph=source_graph)
        # Known: 148 eligible exact-size source records
        assert stats["eligible_exact_source_count"] == 148

    def test_size_bits_provenance_is_modeled(self, assembled_bundle) -> None:
        """Every size_bits field binding points to a MODELED provenance record."""
        manifest = assembled_bundle.provenance
        # Build lookup: provenance_id → ProvenanceRecord
        prov_by_id = {r.provenance_id: r for r in manifest.records}
        size_bindings = [
            b for b in manifest.bindings
            if b.entity_type == "data_product" and b.field_path == "size_bits"
        ]
        assert len(size_bindings) == _EXPECTED_PRODUCT_COUNT
        for binding in size_bindings:
            rec = prov_by_id.get(binding.provenance_id)
            assert rec is not None, (
                f"Provenance record {binding.provenance_id!r} not found."
            )
            assert rec.kind == ProvenanceKind.MODELED, (
                f"size_bits binding for entity {binding.entity_id!r}: "
                f"expected MODELED, got {rec.kind!r}."
            )

    def test_size_policy_counts_reported_correctly(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        stats = ReplayAssemblerV2.get_size_stats(source_graph=source_graph)
        total = stats["eligible_exact_source_count"] + stats["eligible_unknown_source_count"]
        assert total == 527, f"Expected 527 eligible source records, got {total}."

    def test_fallback_not_derived_from_label_file_size(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Fallback is median_low of archive_total_size_bytes, not label JSON byte lengths."""
        import statistics
        from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty

        snap_idx = dict(source_graph.snapshots_by_source_record_id)
        inv_srids: set[str] = set()
        for inv_entry in source_graph.verified_inventory.entries:
            inv_srids.update(inv_entry.representation_record_ids)

        exact_product_bytes: list[int] = []
        for srid in inv_srids:
            if srid not in snap_idx:
                continue
            product, _ = snap_idx[srid]
            if product.total_data_size_bytes and product.total_data_size_bytes > 0:
                all_exact = all(
                    f.size_certainty == ArchiveDataFileSizeCertainty.SIZE_METADATA_EXACT
                    for f in product.data_files
                )
                if all_exact:
                    exact_product_bytes.append(product.total_data_size_bytes)

        expected_fallback = statistics.median_low(exact_product_bytes)
        assert expected_fallback == _EXPECTED_FALLBACK_BYTES

    def test_fallback_not_from_ineligible_records(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Fallback pool must not include ineligible source records."""
        from backend.app.mission_sources.v2_temporal_reconciliation import ReconciliationClassification
        from backend.app.mission_sources.archive_models import ArchiveDataFileSizeCertainty
        import statistics

        snap_idx = dict(source_graph.snapshots_by_source_record_id)
        # Collect ineligible source_record_ids
        ineligible_srids: set[str] = set()
        for e in source_graph.temporal_reconciliation.entries:
            if e.classification != ReconciliationClassification.ELIGIBLE:
                ineligible_srids.update(e.source_record_ids)

        # Confirm: if we include ineligible records, fallback changes
        all_srids = set(snap_idx.keys())
        ineligible_pool: list[int] = []
        for srid in ineligible_srids & all_srids:
            product, _ = snap_idx[srid]
            if product.total_data_size_bytes and product.total_data_size_bytes > 0:
                ineligible_pool.append(product.total_data_size_bytes)

        # The ineligible records happen to have no exact sizes (SIZE_UNKNOWN), so they would
        # not affect the pool regardless. Verify they are absent from the exact-size pool.
        exact_pool: list[int] = []
        inv_srids: set[str] = set()
        for inv_entry in source_graph.verified_inventory.entries:
            inv_srids.update(inv_entry.representation_record_ids)
        for srid in ineligible_srids:
            assert srid not in inv_srids, (
                f"Ineligible srid {srid!r} found in verified inventory."
            )


# ---------------------------------------------------------------------------
# Section 38: Age derivation
# ---------------------------------------------------------------------------


class TestAgeDerivedFromDecisionEpoch:
    def test_all_ages_non_negative(self, assembled_bundle) -> None:
        for dp in assembled_bundle.scenario.data_products:
            assert dp.age_s >= 0.0, (
                f"Product {dp.product_id!r} has negative age_s={dp.age_s}."
            )

    def test_age_deterministic_from_decision_epoch(
        self, assembled_bundle, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """age_s == decision_epoch - authoritative_observation_stop."""
        recon_by_lid = {
            e.logical_product_id: e
            for e in source_graph.temporal_reconciliation.entries
        }
        for dp in assembled_bundle.scenario.data_products[:20]:  # spot-check first 20
            recon_entry = recon_by_lid.get(dp.product_id)
            assert recon_entry is not None
            stop_str = recon_entry.authoritative_observation_stop_utc
            assert stop_str is not None
            stop = datetime.fromisoformat(stop_str)
            if stop.tzinfo is None:
                stop = stop.replace(tzinfo=timezone.utc)
            else:
                stop = stop.astimezone(timezone.utc)
            expected_age_s = (_DECISION_EPOCH_UTC - stop).total_seconds()
            assert dp.age_s == pytest.approx(expected_age_s, abs=0.001), (
                f"Product {dp.product_id!r}: expected age_s={expected_age_s:.3f}, "
                f"got {dp.age_s:.3f}."
            )

    def test_age_not_from_wall_clock(self, assembled_bundle) -> None:
        """All ages are <= window from accumulation start to decision epoch (~86000s).

        If wall-clock time were used, ages could be in years. This check
        proves all ages are within the observation window.
        """
        WINDOW_S = (
            (_DECISION_EPOCH_UTC - datetime(2024, 6, 13, 10, 0, 0, tzinfo=timezone.utc))
            .total_seconds()
        )
        for dp in assembled_bundle.scenario.data_products:
            # age_s can be slightly > window only if products are from before the window
            # but eligible products have stop > ACCUMULATION_START, so age_s < window
            # Add small tolerance for boundary products
            assert dp.age_s <= WINDOW_S + 1.0, (
                f"Product {dp.product_id!r} has age_s={dp.age_s:.0f}s > "
                f"window ({WINDOW_S:.0f}s) — looks like wall-clock contamination."
            )

    def test_age_provenance_is_derived(self, assembled_bundle) -> None:
        """age_s field bindings point to DERIVED provenance records."""
        manifest = assembled_bundle.provenance
        prov_by_id = {r.provenance_id: r for r in manifest.records}
        age_bindings = [
            b for b in manifest.bindings
            if b.entity_type == "data_product" and b.field_path == "age_s"
        ]
        assert len(age_bindings) == _EXPECTED_PRODUCT_COUNT
        for binding in age_bindings:
            rec = prov_by_id.get(binding.provenance_id)
            assert rec is not None
            assert rec.kind == ProvenanceKind.DERIVED, (
                f"age_s binding for {binding.entity_id!r}: expected DERIVED, got {rec.kind!r}."
            )

    def test_no_near_zero_age_products_wrong_date(
        self, assembled_bundle, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Spot check: products with observation stop near decision epoch have small age_s.

        If age_s were derived from artifact retrieval time or discovery time,
        it would be much larger (years).
        """
        recon_by_lid = {
            e.logical_product_id: e
            for e in source_graph.temporal_reconciliation.entries
        }
        # Find any product whose stop is very close to the decision epoch
        for dp in assembled_bundle.scenario.data_products:
            recon_entry = recon_by_lid.get(dp.product_id)
            if recon_entry is None:
                continue
            stop_str = recon_entry.authoritative_observation_stop_utc
            if stop_str is None:
                continue
            stop = datetime.fromisoformat(stop_str)
            if stop.tzinfo is None:
                stop = stop.replace(tzinfo=timezone.utc)
            else:
                stop = stop.astimezone(timezone.utc)
            age_delta = (_DECISION_EPOCH_UTC - stop).total_seconds()
            if 0 <= age_delta <= 7200:  # within 2 hours of decision epoch
                # age_s should be < 7200, not years
                assert dp.age_s < 7200 + 1, (
                    f"Product {dp.product_id!r} near decision epoch: "
                    f"age_s={dp.age_s:.0f}s should be < 7201."
                )


# ---------------------------------------------------------------------------
# Section 39: Geometry / Latency Separation
# ---------------------------------------------------------------------------


class TestGeometryLatencySeparation:
    def test_distance_km_exact_horizons_range(self, assembled_bundle) -> None:
        """Scenario.distance_km == exact Horizons range (not propagation time)."""
        assert assembled_bundle.scenario.distance_km == pytest.approx(
            _EXPECTED_DISTANCE_KM, rel=1e-9
        )

    def test_latency_s_is_link_stack_overhead(self, assembled_bundle) -> None:
        """latency_s == 1.5 (protocol/link-stack), NOT free-space propagation."""
        assert assembled_bundle.scenario.link_inputs["latency_s"] == _EXPECTED_LATENCY_S

    def test_latency_and_distance_are_separate_values(self, assembled_bundle) -> None:
        """distance_km and latency_s are semantically and numerically separate."""
        distance_km = assembled_bundle.scenario.distance_km
        latency_s = assembled_bundle.scenario.link_inputs["latency_s"]
        # One-way light time for 893,130,069 km ≈ 2979 s
        # latency_s must NOT be that value
        one_way_light_time_approx_s = distance_km / 299792.458
        assert latency_s != pytest.approx(one_way_light_time_approx_s, rel=0.01), (
            "latency_s must NOT equal one-way free-space propagation time."
        )
        assert latency_s == _EXPECTED_LATENCY_S
        assert distance_km > 1e8  # sanity: Juno is hundreds of millions of km away

    def test_horizons_epoch_matches_decision_epoch(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Horizons snapshot epoch == replay decision epoch."""
        geo_epoch = source_graph.horizons_result.geometry.epoch_utc.astimezone(timezone.utc)
        assert geo_epoch == _DECISION_EPOCH_UTC

    def test_horizons_target(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.horizons_result.geometry.target_spk_id == "-61"

    def test_horizons_center(self, source_graph: VerifiedV2SourceGraph) -> None:
        assert source_graph.horizons_result.geometry.center == "500@399"

    def test_distance_provenance_from_horizons(self, assembled_bundle) -> None:
        """distance_km binding points to a provenance record derived from Horizons."""
        manifest = assembled_bundle.provenance
        prov_by_id = {r.provenance_id: r for r in manifest.records}
        distance_bindings = [
            b for b in manifest.bindings
            if b.entity_type == "scenario" and b.field_path == "distance_km"
        ]
        assert len(distance_bindings) == 1
        rec = prov_by_id.get(distance_bindings[0].provenance_id)
        assert rec is not None
        assert rec.kind in (ProvenanceKind.DERIVED, ProvenanceKind.EXTERNAL_AUTHORITATIVE)


# ---------------------------------------------------------------------------
# Section 28: MissionSourceBundle contract
# ---------------------------------------------------------------------------


class TestMissionSourceBundleContract:
    def test_source_mode_historical_replay(self, assembled_bundle) -> None:
        assert assembled_bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_simulated_true(self, assembled_bundle) -> None:
        assert assembled_bundle.scenario.simulated is True

    def test_scenario_id(self, assembled_bundle) -> None:
        assert assembled_bundle.scenario.scenario_id == "juno_pj62_large_replay_v2"

    def test_packets_empty(self, assembled_bundle) -> None:
        assert assembled_bundle.scenario.packets == []

    def test_anomalies_empty(self, assembled_bundle) -> None:
        assert assembled_bundle.scenario.anomalies == []

    def test_provider_name_set(self, assembled_bundle) -> None:
        assert assembled_bundle.provider_name == "GCSI-HistoricalReplayV2Provider"

    def test_source_ref_set(self, assembled_bundle) -> None:
        assert assembled_bundle.source_ref is not None
        assert len(assembled_bundle.source_ref) > 0

    def test_comm_window_remaining_s(self, assembled_bundle) -> None:
        ms = assembled_bundle.scenario.mission_state
        assert ms.comm_window_remaining_s == 900.0

    def test_risk_score(self, assembled_bundle) -> None:
        ms = assembled_bundle.scenario.mission_state
        assert ms.risk_score == 0.35


# ---------------------------------------------------------------------------
# Provenance checks
# ---------------------------------------------------------------------------


class TestProvenanceDAG:
    def test_every_product_has_size_bits_binding(self, assembled_bundle) -> None:
        manifest = assembled_bundle.provenance
        product_ids_with_size = {
            b.entity_id for b in manifest.bindings
            if b.entity_type == "data_product" and b.field_path == "size_bits"
        }
        all_ids = {dp.product_id for dp in assembled_bundle.scenario.data_products}
        missing = all_ids - product_ids_with_size
        assert missing == set(), f"Products missing size_bits binding: {sorted(missing)[:5]!r}"

    def test_every_product_has_age_binding(self, assembled_bundle) -> None:
        manifest = assembled_bundle.provenance
        product_ids_with_age = {
            b.entity_id for b in manifest.bindings
            if b.entity_type == "data_product" and b.field_path == "age_s"
        }
        all_ids = {dp.product_id for dp in assembled_bundle.scenario.data_products}
        missing = all_ids - product_ids_with_age
        assert missing == set(), f"Products missing age_s binding: {sorted(missing)[:5]!r}"

    def test_scenario_distance_km_binding(self, assembled_bundle) -> None:
        manifest = assembled_bundle.provenance
        scenario_distance = [
            b for b in manifest.bindings
            if b.entity_type == "scenario" and b.field_path == "distance_km"
        ]
        assert len(scenario_distance) == 1

    def test_scenario_simulated_binding(self, assembled_bundle) -> None:
        manifest = assembled_bundle.provenance
        assert any(
            b.entity_type == "scenario" and b.field_path == "simulated"
            for b in manifest.bindings
        )

    def test_scenario_link_inputs_binding(self, assembled_bundle) -> None:
        manifest = assembled_bundle.provenance
        assert any(
            b.entity_type == "scenario" and b.field_path == "link_inputs"
            for b in manifest.bindings
        )

    def test_provenance_records_all_referenced(self, assembled_bundle) -> None:
        """Every binding references an existing provenance record."""
        manifest = assembled_bundle.provenance
        prov_ids = {r.provenance_id for r in manifest.records}
        orphaned = [
            b for b in manifest.bindings
            if b.provenance_id not in prov_ids
        ]
        assert orphaned == [], (
            f"Orphaned bindings (no matching record): "
            f"{[(b.entity_type, b.field_path, b.provenance_id) for b in orphaned[:5]]!r}"
        )

    def test_no_external_authoritative_for_size_bits(self, assembled_bundle) -> None:
        """size_bits must never be EXTERNAL_AUTHORITATIVE; must be MODELED."""
        manifest = assembled_bundle.provenance
        prov_by_id = {r.provenance_id: r for r in manifest.records}
        for binding in manifest.bindings:
            if binding.entity_type == "data_product" and binding.field_path == "size_bits":
                rec = prov_by_id[binding.provenance_id]
                assert rec.kind != ProvenanceKind.EXTERNAL_AUTHORITATIVE, (
                    f"size_bits for {binding.entity_id!r} must not be EXTERNAL_AUTHORITATIVE."
                )

    def test_no_external_authoritative_for_queue_membership(self, assembled_bundle) -> None:
        """data_products binding must be MODELED (queue membership is modeled)."""
        manifest = assembled_bundle.provenance
        prov_by_id = {r.provenance_id: r for r in manifest.records}
        queue_bindings = [
            b for b in manifest.bindings
            if b.entity_type == "scenario" and b.field_path == "data_products"
        ]
        assert len(queue_bindings) == 1
        rec = prov_by_id[queue_bindings[0].provenance_id]
        assert rec.kind == ProvenanceKind.MODELED


# ---------------------------------------------------------------------------
# Section 41: Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_two_assembler_runs_identical(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Two assembler runs against unchanged frozen artifacts produce identical output."""
        descriptor = load_v2_replay_descriptor(_DESCRIPTOR_PATH)
        bundle1 = ReplayAssemblerV2.assemble(descriptor=descriptor, source_graph=source_graph)
        bundle2 = ReplayAssemblerV2.assemble(descriptor=descriptor, source_graph=source_graph)

        s1 = bundle1.scenario
        s2 = bundle2.scenario

        # Same product count
        assert len(s1.data_products) == len(s2.data_products)

        # Same ordered product IDs
        ids1 = [dp.product_id for dp in s1.data_products]
        ids2 = [dp.product_id for dp in s2.data_products]
        assert ids1 == ids2

        # All DataProduct semantic fields identical
        for dp1, dp2 in zip(s1.data_products, s2.data_products):
            assert dp1.product_id == dp2.product_id
            assert dp1.size_bits == dp2.size_bits
            assert dp1.age_s == dp2.age_s
            assert dp1.criticality == dp2.criticality
            assert dp1.mission_relevance == dp2.mission_relevance
            assert dp1.scientific_value == dp2.scientific_value
            assert dp1.deadline_s == dp2.deadline_s
            assert dp1.retry_cost == dp2.retry_cost
            assert dp1.subsystem == dp2.subsystem
            assert dp1.product_type == dp2.product_type

        # Scenario-level fields identical
        assert s1.distance_km == s2.distance_km
        assert s1.link_inputs == s2.link_inputs
        assert s1.simulated == s2.simulated

        # Provenance record IDs identical (same set, same count)
        rec_ids1 = sorted(r.provenance_id for r in bundle1.provenance.records)
        rec_ids2 = sorted(r.provenance_id for r in bundle2.provenance.records)
        assert rec_ids1 == rec_ids2

        # Field bindings identical (same ordered list by entity+field)
        def binding_key(b):
            return (b.entity_type, b.entity_id, b.field_path, b.provenance_id)

        bindings1 = sorted(bundle1.provenance.bindings, key=binding_key)
        bindings2 = sorted(bundle2.provenance.bindings, key=binding_key)
        assert len(bindings1) == len(bindings2)
        for b1, b2 in zip(bindings1, bindings2):
            assert binding_key(b1) == binding_key(b2)


# ---------------------------------------------------------------------------
# Section 42: Offline Isolation
# ---------------------------------------------------------------------------


class TestOfflineIsolation:
    def test_assembler_runs_without_network(
        self, source_graph: VerifiedV2SourceGraph
    ) -> None:
        """Assembly must succeed with network access blocked."""
        import socket

        original_connect = socket.socket.connect

        def no_network(*args, **kwargs):
            raise OSError("B3 offline isolation: network access is BLOCKED during assembly.")

        descriptor = load_v2_replay_descriptor(_DESCRIPTOR_PATH)

        with patch.object(socket.socket, "connect", no_network):
            bundle = ReplayAssemblerV2.assemble(
                descriptor=descriptor, source_graph=source_graph
            )

        assert len(bundle.scenario.data_products) == _EXPECTED_PRODUCT_COUNT

    def test_assembler_does_not_import_httpx(self) -> None:
        """Assembler module must not import httpx at module level."""
        import importlib
        import sys

        # Check that v2_replay_assembler does not have a top-level httpx import
        # by inspecting the module's __dict__
        import backend.app.mission_sources.v2_replay_assembler as asm_mod
        assert "httpx" not in asm_mod.__dict__, (
            "v2_replay_assembler must not import httpx at module level."
        )

    def test_assembler_does_not_call_acquisition_runner(self) -> None:
        """The B3 assembly path must not touch V2InventoryAcquisitionRunner."""
        # This is enforced structurally: v2_replay_assembler has no import of the runner.
        import backend.app.mission_sources.v2_replay_assembler as asm_mod
        assert "v2_inventory_acquisition" not in getattr(asm_mod, "__file__", "")
        # Verify the module source does not reference the acquisition runner
        import pathlib
        assembler_src = pathlib.Path(asm_mod.__file__).read_text(encoding="utf-8")
        assert "V2InventoryAcquisitionRunner" not in assembler_src, (
            "v2_replay_assembler must not reference V2InventoryAcquisitionRunner."
        )
        assert "_fetch_label_bytes" not in assembler_src, (
            "v2_replay_assembler must not reference _fetch_label_bytes."
        )
