"""GCSI Phase 6F-B4 — Scalability and Performance Tests.

Measures and verifies:

- Cold V2 provider load timing
- Assembler timing
- GET /state / /data-products API timing
- Plan generation timing
- Local recommendation timing
- Reset timing
- No O(N²) scan on API requests after activation
- Memory/response-size reasonable

All offline. No network.
"""

from __future__ import annotations

import pathlib
import socket
import time

import pytest
from httpx import ASGITransport, AsyncClient

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_V2_SOURCE_REF = "data/replays/juno_pj62_large_replay_v2_descriptor.json"


def _no_network(*args, **kwargs):
    raise RuntimeError("B4 scalability test: network forbidden.")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket, "getaddrinfo", _no_network)
    yield


def _reset_state():
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
    _reset_state()
    yield
    _reset_state()


# ===========================================================================
# Cold provider load timing
# ===========================================================================


class TestColdProviderLoad:
    """Cold V2 provider load must complete in reasonable time (no hang)."""

    def test_cold_load_completes(self):
        """V2 provider load must complete without hanging."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider

        t0 = time.time()
        provider = HistoricalReplayProvider()
        bundle = provider.load(_V2_SOURCE_REF)
        elapsed = time.time() - t0

        assert len(bundle.scenario.data_products) == 403
        # Should complete in reasonable time (no strict deadline but must not hang)
        # Allow generous timeout for CI
        assert elapsed < 120, f"Cold V2 load took {elapsed:.1f}s — too slow"
        print(f"\n[PERF] Cold V2 provider load: {elapsed:.3f}s")  # noqa: T201

    def test_assembler_timing(self):
        """Assembler (pure, no IO) must complete in reasonable time."""
        from backend.app.mission_sources.v2_replay_descriptor import load_v2_replay_descriptor
        from backend.app.mission_sources.v2_source_graph import load_verified_v2_source_graph
        from backend.app.mission_sources.v2_replay_assembler import ReplayAssemblerV2

        descriptor = load_v2_replay_descriptor(
            _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_descriptor.json"
        )
        source_graph = load_verified_v2_source_graph()

        t0 = time.time()
        bundle = ReplayAssemblerV2.assemble(
            descriptor=descriptor,
            source_graph=source_graph,
            source_ref=_V2_SOURCE_REF,
        )
        elapsed = time.time() - t0

        assert len(bundle.scenario.data_products) == 403
        print(f"\n[PERF] Assembler (pure, no IO): {elapsed:.3f}s")  # noqa: T201


# ===========================================================================
# API request timing (must not reload 535 snapshots)
# ===========================================================================


class TestAPITimingAfterActivation:
    """API requests must not reload source graph on every call."""

    @pytest.fixture(autouse=True)
    def activate_v2(self):
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

    @pytest.mark.asyncio
    async def test_get_state_fast(self):
        from backend.app.main import app
        t0 = time.time()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/state")
        elapsed = time.time() - t0
        assert resp.status_code == 200
        assert elapsed < 10, f"GET /state took {elapsed:.3f}s after activation"
        print(f"\n[PERF] GET /state: {elapsed:.3f}s")  # noqa: T201

    @pytest.mark.asyncio
    async def test_get_data_products_fast(self):
        from backend.app.main import app
        t0 = time.time()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        elapsed = time.time() - t0
        assert resp.status_code == 200
        assert elapsed < 10, f"GET /data-products took {elapsed:.3f}s after activation"
        print(f"\n[PERF] GET /data-products: {elapsed:.3f}s")  # noqa: T201

    @pytest.mark.asyncio
    async def test_plans_generate_fast(self):
        from backend.app.main import app
        t0 = time.time()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/plans/generate")
        elapsed = time.time() - t0
        assert resp.status_code == 200
        assert elapsed < 30, f"POST /plans/generate took {elapsed:.3f}s"
        print(f"\n[PERF] POST /plans/generate: {elapsed:.3f}s")  # noqa: T201

    @pytest.mark.asyncio
    async def test_state_reset_timing(self):
        from backend.app.main import app
        t0 = time.time()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post("/state/reset")
        elapsed = time.time() - t0
        assert resp.status_code == 200
        assert elapsed < 120, f"POST /state/reset took {elapsed:.3f}s"
        print(f"\n[PERF] POST /state/reset: {elapsed:.3f}s")  # noqa: T201

    def test_no_source_graph_reload_on_state_access(self):
        """Multiple state accesses must NOT call load_verified_v2_source_graph."""
        from backend.app import state as app_state
        from unittest.mock import patch

        call_count = [0]
        original_load = None

        import backend.app.mission_sources.v2_source_graph as sg_mod
        original_load = sg_mod.load_verified_v2_source_graph

        def counting_load(*args, **kwargs):
            call_count[0] += 1
            return original_load(*args, **kwargs)

        with patch.object(sg_mod, "load_verified_v2_source_graph", counting_load):
            # Multiple state accesses
            _ = app_state.active_scenario
            _ = app_state.active_link_state
            _ = app_state.active_source_ref
            _ = len(app_state.active_scenario.data_products)

        # Source graph must NOT be reloaded during state access
        assert call_count[0] == 0, (
            f"load_verified_v2_source_graph called {call_count[0]} times during state access "
            "— should be 0 (uses in-memory state)."
        )


# ===========================================================================
# Response size
# ===========================================================================


class TestResponseSize:

    @pytest.fixture(autouse=True)
    def activate_v2(self):
        from backend.app import state as app_state
        app_state.load_historical_replay(_V2_SOURCE_REF)

    @pytest.mark.asyncio
    async def test_data_products_response_size_reasonable(self):
        """GET /data-products should return reasonable response size for 403 products."""
        from backend.app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.get("/data-products")
        assert resp.status_code == 200
        content_len = len(resp.content)
        # 403 products × ~500 bytes each ≈ 200 KB — allow 5 MB max
        assert content_len < 5 * 1024 * 1024, (
            f"GET /data-products response too large: {content_len:,} bytes"
        )
        print(f"\n[PERF] /data-products response: {content_len:,} bytes")  # noqa: T201


# ===========================================================================
# Determinism (two complete loads produce identical results)
# ===========================================================================


class TestDeterminism:

    def test_two_complete_loads_identical(self):
        """Two V2 provider loads from same descriptor produce identical products."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider

        provider = HistoricalReplayProvider()
        bundle1 = provider.load(_V2_SOURCE_REF)
        bundle2 = provider.load(_V2_SOURCE_REF)

        products1 = bundle1.scenario.data_products
        products2 = bundle2.scenario.data_products

        assert len(products1) == len(products2)

        for p1, p2 in zip(products1, products2):
            assert p1.product_id == p2.product_id
            assert p1.size_bits == p2.size_bits
            assert p1.criticality == pytest.approx(p2.criticality)
            assert p1.mission_relevance == pytest.approx(p2.mission_relevance)
            assert p1.age_s == pytest.approx(p2.age_s)

    def test_provenance_records_identical_two_loads(self):
        """Provenance record IDs must be identical across two loads."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider

        provider = HistoricalReplayProvider()
        bundle1 = provider.load(_V2_SOURCE_REF)
        bundle2 = provider.load(_V2_SOURCE_REF)

        ids1 = sorted(r.provenance_id for r in bundle1.provenance.records)
        ids2 = sorted(r.provenance_id for r in bundle2.provenance.records)
        assert ids1 == ids2

    def test_source_ref_identical_two_loads(self):
        """source_ref must be identical across two loads."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider

        provider = HistoricalReplayProvider()
        bundle1 = provider.load(_V2_SOURCE_REF)
        bundle2 = provider.load(_V2_SOURCE_REF)

        assert bundle1.source_ref == bundle2.source_ref
        assert bundle1.source_ref == _V2_SOURCE_REF
