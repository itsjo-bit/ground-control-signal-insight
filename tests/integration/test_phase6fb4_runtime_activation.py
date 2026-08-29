"""GCSI Phase 6F-B4 — Runtime Activation Tests.

Tests for V2 activation through state.py and the API:

- activate_mission_source_bundle() works for V2 (403 products)
- active_source_mode = HISTORICAL_REPLAY
- active_source_ref = descriptor path
- Reset is deterministic (identical 403 products)
- Failure atomicity: failed load leaves previous state unchanged
- Plan registry invalidated on V2 activation and reset
- Latency/propagation separation
- Communication constraint: queued > available
- TelecomEngine integration

All tests OFFLINE. No network.
"""

from __future__ import annotations

import pathlib
import socket

import pytest
from httpx import ASGITransport, AsyncClient

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_V1_SOURCE_REF = "data/replays/juno_pj62_mwr_v1.json"
_V2_SOURCE_REF = "data/replays/juno_pj62_large_replay_v2_descriptor.json"

# V2 expected values (frozen B3)
_EXPECTED_DISTANCE_KM = 893130069.5851377
_EXPECTED_LATENCY_S = 1.5
_EXPECTED_REMAINING_WINDOW_S = 900.0
_EXPECTED_SNR_DB = 3.0
_EXPECTED_RSSI_DBM = -95.0
_EXPECTED_NOMINAL_RATE_BPS = 100000.0
_EXPECTED_LINK_STABILITY = 0.8
_EXPECTED_RISK_LEVEL = "MEDIUM"
_EXPECTED_RISK_SCORE = 0.35
_EXPECTED_PRODUCT_COUNT = 403


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------


def _no_network(*args, **kwargs):
    raise RuntimeError("B4 test: network access is forbidden.")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


# ---------------------------------------------------------------------------
# State cleanup
# ---------------------------------------------------------------------------


def _reset_all_state():
    from backend.app import state as app_state
    app_state.active_scenario = None
    app_state.active_link_state = None
    app_state.active_scenario_path = None
    app_state.active_source_mode = None
    app_state.active_source_ref = None
    app_state.active_source_provider_name = None
    app_state.active_source_provenance = None
    app_state.issued_plans.clear()


@pytest.fixture(autouse=True)
def clean_state():
    _reset_all_state()
    yield
    _reset_all_state()


@pytest.fixture
def v2_activated():
    """Activate V2 replay in state and return the state module."""
    from backend.app import state as app_state
    app_state.load_historical_replay(_V2_SOURCE_REF)
    return app_state


# ===========================================================================
# State module activation tests
# ===========================================================================


class TestV2StateActivation:

    def test_source_mode_after_v2_activation(self, v2_activated):
        from backend.app.mission_sources.models import MissionSourceMode
        assert v2_activated.active_source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_source_ref_is_descriptor_path(self, v2_activated):
        assert v2_activated.active_source_ref == _V2_SOURCE_REF

    def test_provider_name(self, v2_activated):
        assert v2_activated.active_source_provider_name == "GCSI-HistoricalReplayProvider"

    def test_403_products(self, v2_activated):
        assert len(v2_activated.active_scenario.data_products) == _EXPECTED_PRODUCT_COUNT

    def test_scenario_id(self, v2_activated):
        assert v2_activated.active_scenario.scenario_id == "juno_pj62_large_replay_v2"

    def test_simulated_true(self, v2_activated):
        assert v2_activated.active_scenario.simulated is True

    def test_link_state_available(self, v2_activated):
        assert v2_activated.active_link_state is not None

    def test_link_state_ebn0_finite(self, v2_activated):  # noqa: F811
        import math
        assert math.isfinite(v2_activated.active_link_state.eb_n0_db)

    def test_link_state_ber_finite(self, v2_activated):
        import math
        assert math.isfinite(v2_activated.active_link_state.ber)
        assert 0.0 <= v2_activated.active_link_state.ber <= 1.0

    def test_link_state_goodput_positive(self, v2_activated):
        assert v2_activated.active_link_state.link_goodput_bps > 0

    def test_distance_km(self, v2_activated):
        assert v2_activated.active_scenario.distance_km == pytest.approx(
            _EXPECTED_DISTANCE_KM, rel=1e-6
        )

    def test_latency_is_protocol_overhead_not_propagation(self, v2_activated):
        """latency_s must NOT equal propagation delay — they are separate values."""
        latency_s = v2_activated.active_scenario.link_inputs.get("latency_s")
        prop_delay_s = _EXPECTED_DISTANCE_KM * 1000 / 299792458
        assert latency_s == pytest.approx(_EXPECTED_LATENCY_S)
        # Propagation delay ≈ 2979s; latency is 1.5s
        assert abs(latency_s - prop_delay_s) > 100, (
            "latency_s must not be the propagation delay."
        )

    def test_risk_level_medium(self, v2_activated):
        assert v2_activated.active_scenario.mission_state.risk_level.value == _EXPECTED_RISK_LEVEL

    def test_provenance_available(self, v2_activated):
        assert v2_activated.active_source_provenance is not None
        assert len(v2_activated.active_source_provenance.records) > 0

    def test_scenario_path_none(self, v2_activated):
        """active_scenario_path must be None for historical replay."""
        assert v2_activated.active_scenario_path is None

    def test_queued_data_bits_exceeds_capacity(self, v2_activated):
        """Communication constraint: queued > available (constrained downlink)."""
        queued_bits = sum(
            dp.size_bits for dp in v2_activated.active_scenario.data_products
        )
        link_state = v2_activated.active_link_state
        available_bits = int(link_state.link_goodput_bps * link_state.remaining_window_s)
        assert queued_bits > available_bits, (
            f"Expected constrained scenario: queued {queued_bits} > available {available_bits}"
        )


# ===========================================================================
# Reset determinism tests
# ===========================================================================


class TestV2ResetDeterminism:

    def test_reset_returns_same_403_products(self):
        """Reset must produce identical product IDs in identical order."""
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

        products_before = [dp.product_id for dp in app_state.active_scenario.data_products]
        sizes_before = [dp.size_bits for dp in app_state.active_scenario.data_products]
        source_ref_before = app_state.active_source_ref

        result = app_state.reset_active_source()
        assert result["randomized"] is False
        assert result["source_mode"] == "historical_replay"

        products_after = [dp.product_id for dp in app_state.active_scenario.data_products]
        sizes_after = [dp.size_bits for dp in app_state.active_scenario.data_products]

        assert products_before == products_after
        assert sizes_before == sizes_after
        assert app_state.active_source_ref == source_ref_before

    def test_reset_clears_issued_plans(self):
        """Reset must invalidate issued plans."""
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

        # Manually add a fake plan
        app_state.issued_plans["fake_plan_id"] = object()
        assert len(app_state.issued_plans) == 1

        app_state.reset_active_source()
        assert len(app_state.issued_plans) == 0

    def test_reset_semantic_fields_identical(self):
        """Key semantic fields must be identical before/after reset."""
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

        before_scenario = app_state.active_scenario
        before_products = {
            dp.product_id: (dp.size_bits, dp.criticality, dp.mission_relevance)
            for dp in before_scenario.data_products
        }
        before_distance = before_scenario.distance_km
        before_risk = before_scenario.mission_state.risk_level

        app_state.reset_active_source()

        after_scenario = app_state.active_scenario
        after_products = {
            dp.product_id: (dp.size_bits, dp.criticality, dp.mission_relevance)
            for dp in after_scenario.data_products
        }
        after_distance = after_scenario.distance_km
        after_risk = after_scenario.mission_state.risk_level

        assert before_products == after_products
        assert before_distance == after_distance
        assert before_risk == after_risk


# ===========================================================================
# Failure atomicity tests
# ===========================================================================


class TestFailureAtomicity:
    """Failed V2 load must leave previous state unchanged."""

    def test_failed_v2_load_preserves_synthetic_state(self):
        """If V2 load fails, synthetic state must remain."""
        from backend.app import state as app_state
        from backend.app.mission_sources.errors import (
            MissionSourceUnavailableError,
            MissionSourceValidationError,
        )

        # Load synthetic scenario first
        asteria_path = str(_REPO_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")
        app_state.load_scenario(asteria_path)

        previous_scenario_id = app_state.active_scenario.scenario_id
        previous_product_count = len(app_state.active_scenario.data_products)

        # Attempt to load a non-existent V2 descriptor
        try:
            app_state.load_historical_replay("data/replays/nonexistent_b4_test.json")
        except (MissionSourceUnavailableError, MissionSourceValidationError, Exception):
            pass  # Expected to fail

        # Previous state must be preserved
        assert app_state.active_scenario is not None
        assert app_state.active_scenario.scenario_id == previous_scenario_id
        assert len(app_state.active_scenario.data_products) == previous_product_count

    def test_failed_v2_load_preserves_v1_state(self):
        """If V2 load fails mid-way, V1 historical state must remain."""
        from backend.app import state as app_state
        from backend.app.mission_sources.errors import (
            MissionSourceUnavailableError,
            MissionSourceValidationError,
        )
        from unittest.mock import patch

        # Load V1 first
        app_state.load_historical_replay(_V1_SOURCE_REF)
        v1_scenario_id = app_state.active_scenario.scenario_id

        # Attempt V2 with a patched assembler that raises
        with patch(
            "backend.app.mission_sources.historical_provider.HistoricalReplayProvider._load_v2",
            side_effect=MissionSourceValidationError("Simulated V2 assembly failure"),
        ):
            with pytest.raises(MissionSourceValidationError):
                app_state.load_historical_replay(_V2_SOURCE_REF)

        # V1 state must still be active
        assert app_state.active_scenario is not None
        assert app_state.active_scenario.scenario_id == v1_scenario_id


# ===========================================================================
# Plan registry invalidation tests
# ===========================================================================


class TestPlanRegistryInvalidation:

    def test_v2_activation_clears_plan_registry(self):
        from backend.app import state as app_state
        # Load synthetic first
        asteria_path = str(_REPO_ROOT / "data" / "scenarios" / "asteria7_thermal_priority_contact_v1.json")
        app_state.load_scenario(asteria_path)

        # Register a fake plan
        app_state.issued_plans["plan_from_synthetic"] = object()
        assert "plan_from_synthetic" in app_state.issued_plans

        # Activate V2
        app_state.load_historical_replay(_V2_SOURCE_REF)

        # Plans from previous scenario must be gone
        assert "plan_from_synthetic" not in app_state.issued_plans
        assert len(app_state.issued_plans) == 0

    def test_v2_reset_clears_v2_plans(self):
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

        # Register a fake V2 plan
        app_state.issued_plans["v2_plan_123"] = object()

        app_state.reset_active_source()

        # V2 plans invalidated on reset
        assert "v2_plan_123" not in app_state.issued_plans


# ===========================================================================
# Communication budget tests
# ===========================================================================


class TestCommunicationBudget:
    """Communication constraint must be enforced with V2 data."""

    def test_fallback_size_bits_used_for_unknown_size_products(self, v2_activated):
        """Some products must use the fallback size (3538944 bits)."""
        _EXPECTED_FALLBACK_BITS = 3538944
        fallback_products = [
            dp for dp in v2_activated.active_scenario.data_products
            if dp.size_bits == _EXPECTED_FALLBACK_BITS
        ]
        assert len(fallback_products) > 0, (
            f"Expected some products to use fallback size {_EXPECTED_FALLBACK_BITS}; found none."
        )

    def test_all_products_have_positive_size_bits(self, v2_activated):
        for dp in v2_activated.active_scenario.data_products:
            assert dp.size_bits > 0, f"Product {dp.product_id!r} has non-positive size_bits"

    def test_queued_data_bits_calculated_correctly(self, v2_activated):
        expected = sum(dp.size_bits for dp in v2_activated.active_scenario.data_products)
        link_state = v2_activated.active_link_state
        available = int(link_state.link_goodput_bps * link_state.remaining_window_s)
        # Verify queue > capacity
        assert expected > available

    def test_capacity_derived_from_goodput_and_window(self, v2_activated):
        link = v2_activated.active_link_state
        expected_capacity = int(link.link_goodput_bps * link.remaining_window_s)
        assert expected_capacity > 0


# ===========================================================================
# Telecom integration tests
# ===========================================================================


class TestTelecomIntegration:

    def test_ebn0_reasonable(self, v2_activated):
        """Eb/N0 should be a finite reasonable value given SNR=3dB."""
        import math
        ebn0 = v2_activated.active_link_state.eb_n0_db
        assert math.isfinite(ebn0)
        # With SNR=3 and BW=1MHz, Rb=100kbps: Eb/N0 = 3 + 10*log10(1e6/1e5) = 13 dB
        assert -10 < ebn0 < 50

    def test_ber_from_ebn0(self, v2_activated):
        """BER should be in [0, 1] and deterministic."""
        import math
        ber = v2_activated.active_link_state.ber
        assert math.isfinite(ber)
        assert 0.0 <= ber <= 1.0

    def test_goodput_from_rate_and_efficiency(self, v2_activated):
        """Goodput = nominal_data_rate × protocol_efficiency."""
        link = v2_activated.active_link_state
        # With nominal_rate=100000 and protocol_efficiency=0.9: goodput = 90000
        assert link.link_goodput_bps == pytest.approx(90000.0)

    def test_remaining_window_preserved(self, v2_activated):
        link = v2_activated.active_link_state
        assert link.remaining_window_s == pytest.approx(_EXPECTED_REMAINING_WINDOW_S)

    def test_propagation_delay_separate_from_latency(self, v2_activated):
        """Propagation delay is NOT substituted into telecom latency_s."""
        from backend.app.telecom.geometry import compute_communication_geometry
        geom = compute_communication_geometry(_EXPECTED_DISTANCE_KM)
        propagation_delay_s = geom["propagation_delay_s"]
        latency_s = v2_activated.active_scenario.link_inputs.get("latency_s")

        # They must be significantly different
        assert latency_s == pytest.approx(_EXPECTED_LATENCY_S)
        assert propagation_delay_s > 1000, f"Expected propagation > 1000s, got {propagation_delay_s}"
        assert abs(latency_s - propagation_delay_s) > 1000


# ===========================================================================
# API integration with V2 state
# ===========================================================================


class TestV2APIIntegration:

    @pytest.fixture(autouse=True)
    def load_v2(self):
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

    @pytest.mark.asyncio
    async def test_health_historical_replay_active(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["historical_replay_active"] is True
        assert body["data_products_count"] == _EXPECTED_PRODUCT_COUNT
        assert body["scenario_loaded"] is True
        assert body["has_geometry"] is True

    @pytest.mark.asyncio
    async def test_state_source_mode(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"]["mode"] == "historical_replay"
        assert body["source"]["provider_name"] == "GCSI-HistoricalReplayProvider"
        assert body["source"]["is_historical_replay"] is True

    @pytest.mark.asyncio
    async def test_state_data_products_count(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["data_products_count"] == _EXPECTED_PRODUCT_COUNT

    @pytest.mark.asyncio
    async def test_state_queued_exceeds_available(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["queued_data_bits"] > body["available_capacity_bits"]

    @pytest.mark.asyncio
    async def test_state_distance_km(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        assert body["distance_km"] == pytest.approx(_EXPECTED_DISTANCE_KM, rel=1e-5)

    @pytest.mark.asyncio
    async def test_state_propagation_delay(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        body = resp.json()
        prop = body["propagation_delay_s"]
        assert prop is not None
        assert prop > 1000  # ~2979s for ~893M km

    @pytest.mark.asyncio
    async def test_data_products_count_403(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.status_code == 200
        body = resp.json()
        # The endpoint returns a list or object with data_products
        products = body if isinstance(body, list) else body.get("data_products", body)
        if isinstance(products, list):
            assert len(products) == _EXPECTED_PRODUCT_COUNT
        else:
            # May be nested
            assert body.get("total", body.get("count")) == _EXPECTED_PRODUCT_COUNT

    @pytest.mark.asyncio
    async def test_data_products_unique_ids(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        body = resp.json()
        products = body if isinstance(body, list) else body.get("data_products", [])
        if products and isinstance(products[0], dict):
            ids = [p.get("product_id") for p in products]
            assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_state_reset_endpoint_works(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/state/reset")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("source_mode") == "historical_replay"
        assert body.get("randomized") is False

    @pytest.mark.asyncio
    async def test_state_reset_preserves_403_products(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/state/reset")
            resp = await c.get("/health")
        body = resp.json()
        assert body["data_products_count"] == _EXPECTED_PRODUCT_COUNT

    @pytest.mark.asyncio
    async def test_plans_generate_four_baseline_plans(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        assert resp.status_code == 200
        plans = resp.json()
        assert len(plans) == 4
        # plan_id uses hyphens; strategy uses underscores
        plan_ids = {p["plan_id"] for p in plans}
        assert "baseline" in plan_ids
        assert "deadline-first" in plan_ids

    @pytest.mark.asyncio
    async def test_plans_generate_deterministic(self):
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp1 = await c.post("/plans/generate")
            resp2 = await c.post("/plans/generate")
        plans1 = resp1.json()
        plans2 = resp2.json()
        for p1, p2 in zip(plans1, plans2):
            assert p1["strategy"] == p2["strategy"]
            assert [pkt["packet_id"] for pkt in p1["packets"]] == [
                pkt["packet_id"] for pkt in p2["packets"]
            ]
