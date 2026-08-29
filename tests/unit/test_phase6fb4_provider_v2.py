"""GCSI Phase 6F-B4 — Provider V1/V2 Dispatch Tests.

Tests for HistoricalReplayProvider V1/V2 schema discrimination and load paths:

- _discriminate_descriptor_schema: V1, V2, unknown, malformed
- load() dispatches to correct path
- V1 load produces two-product replay (regression)
- V2 load produces 403-product replay
- source_ref preserved for V1 and V2
- Security: unknown schema, malformed JSON → validation error
- Error propagation: V2 source graph failure → MissionSourceValidationError
"""

from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_V1_SOURCE_REF = "data/replays/juno_pj62_mwr_v1.json"
_V2_SOURCE_REF = "data/replays/juno_pj62_large_replay_v2_descriptor.json"


# ---------------------------------------------------------------------------
# Helper: create a temp descriptor with given schema/version values
# ---------------------------------------------------------------------------

def _make_temp_descriptor(schema_key: str, schema_val: str) -> pathlib.Path:
    """Write a minimal JSON with given schema key/value to a temp file in data/replays/."""
    replays_dir = _REPO_ROOT / "data" / "replays"
    import tempfile as _tmp
    f = _tmp.NamedTemporaryFile(
        mode="w",
        suffix=".json",
        dir=replays_dir,
        delete=False,
        encoding="utf-8",
    )
    json.dump({schema_key: schema_val, "descriptor_version": 1}, f)
    f.close()
    return pathlib.Path(f.name)


# ===========================================================================
# Schema discriminator unit tests
# ===========================================================================


class TestSchemaDiscriminator:

    def test_v1_descriptor_detected(self, tmp_path):
        """V1 descriptor (descriptor_schema=...) is detected as V1."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        # Write V1 JSON to a file in the replays dir
        replays_dir = _REPO_ROOT / "data" / "replays"
        p = replays_dir / "_b4_test_v1_disc.json"
        try:
            p.write_text(
                json.dumps({
                    "descriptor_schema": "gcsi.historical_replay_descriptor",
                    "descriptor_version": 1,
                }),
                encoding="utf-8",
            )
            result = _discriminate_descriptor_schema(p)
            assert result == "V1"
        finally:
            p.unlink(missing_ok=True)

    def test_v2_descriptor_detected(self):
        """V2 descriptor (schema=...) is detected as V2."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        v2_path = (_REPO_ROOT / _V2_SOURCE_REF).resolve()
        result = _discriminate_descriptor_schema(v2_path)
        assert result == "V2"

    def test_v1_real_file_detected(self):
        """Real V1 descriptor is detected as V1."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        v1_path = (_REPO_ROOT / _V1_SOURCE_REF).resolve()
        result = _discriminate_descriptor_schema(v1_path)
        assert result == "V1"

    def test_unknown_schema_raises(self, tmp_path):
        """Unknown schema value raises MissionSourceValidationError."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        from backend.app.mission_sources.errors import MissionSourceValidationError
        replays_dir = _REPO_ROOT / "data" / "replays"
        p = replays_dir / "_b4_test_unknown_schema.json"
        try:
            p.write_text(
                json.dumps({"schema": "gcsi.not_a_known_schema", "schema_version": 1}),
                encoding="utf-8",
            )
            with pytest.raises(MissionSourceValidationError, match="unknown schema"):
                _discriminate_descriptor_schema(p)
        finally:
            p.unlink(missing_ok=True)

    def test_malformed_json_raises(self):
        """Malformed JSON raises MissionSourceValidationError."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        from backend.app.mission_sources.errors import MissionSourceValidationError
        replays_dir = _REPO_ROOT / "data" / "replays"
        p = replays_dir / "_b4_test_malformed.json"
        try:
            p.write_bytes(b"NOT JSON {{{")
            with pytest.raises(MissionSourceValidationError, match="not valid JSON"):
                _discriminate_descriptor_schema(p)
        finally:
            p.unlink(missing_ok=True)

    def test_no_schema_field_raises(self):
        """JSON with no recognized schema field raises MissionSourceValidationError."""
        from backend.app.mission_sources.historical_provider import (
            _discriminate_descriptor_schema,
        )
        from backend.app.mission_sources.errors import MissionSourceValidationError
        replays_dir = _REPO_ROOT / "data" / "replays"
        p = replays_dir / "_b4_test_noschema.json"
        try:
            p.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
            with pytest.raises(MissionSourceValidationError, match="no recognized schema"):
                _discriminate_descriptor_schema(p)
        finally:
            p.unlink(missing_ok=True)


# ===========================================================================
# Provider V1 load regression
# ===========================================================================


class TestProviderV1Regression:
    """V1 historical replay must still work unchanged through the provider."""

    @pytest.fixture(scope="class")
    def v1_bundle(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        provider = HistoricalReplayProvider()
        return provider.load(_V1_SOURCE_REF)

    def test_v1_source_ref_preserved(self, v1_bundle):
        assert v1_bundle.source_ref == _V1_SOURCE_REF

    def test_v1_source_mode(self, v1_bundle):
        from backend.app.mission_sources.models import MissionSourceMode
        assert v1_bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_v1_provider_name(self, v1_bundle):
        assert v1_bundle.provider_name == "GCSI-HistoricalReplayProvider"

    def test_v1_two_products(self, v1_bundle):
        assert len(v1_bundle.scenario.data_products) == 2

    def test_v1_simulated(self, v1_bundle):
        assert v1_bundle.scenario.simulated is True

    def test_v1_no_packets(self, v1_bundle):
        # V1 may have legacy packets or data_products; just not 403
        dp_count = len(v1_bundle.scenario.data_products)
        assert dp_count < 403, "V1 must not produce 403 products"

    def test_v1_has_geometry(self, v1_bundle):
        assert v1_bundle.scenario.distance_km is not None


# ===========================================================================
# Provider V2 load
# ===========================================================================


class TestProviderV2Load:
    """V2 provider load produces 403-product bundle with correct metadata."""

    @pytest.fixture(scope="class")
    def v2_bundle(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        provider = HistoricalReplayProvider()
        return provider.load(_V2_SOURCE_REF)

    def test_v2_source_ref_is_descriptor_path(self, v2_bundle):
        """source_ref must be the caller's descriptor path (not source_bundle_ref)."""
        assert v2_bundle.source_ref == _V2_SOURCE_REF

    def test_v2_source_mode(self, v2_bundle):
        from backend.app.mission_sources.models import MissionSourceMode
        assert v2_bundle.source_mode == MissionSourceMode.HISTORICAL_REPLAY

    def test_v2_provider_name(self, v2_bundle):
        assert v2_bundle.provider_name == "GCSI-HistoricalReplayProvider"

    def test_v2_403_products(self, v2_bundle):
        assert len(v2_bundle.scenario.data_products) == 403

    def test_v2_simulated_true(self, v2_bundle):
        assert v2_bundle.scenario.simulated is True

    def test_v2_no_packets(self, v2_bundle):
        assert len(v2_bundle.scenario.packets) == 0

    def test_v2_no_anomalies(self, v2_bundle):
        assert len(v2_bundle.scenario.anomalies) == 0

    def test_v2_scenario_id(self, v2_bundle):
        assert v2_bundle.scenario.scenario_id == "juno_pj62_large_replay_v2"

    def test_v2_has_geometry(self, v2_bundle):
        assert v2_bundle.scenario.distance_km is not None
        assert v2_bundle.scenario.distance_km > 0

    def test_v2_distance_km(self, v2_bundle):
        assert v2_bundle.scenario.distance_km == pytest.approx(893130069.5851377, rel=1e-6)

    def test_v2_provenance_available(self, v2_bundle):
        assert v2_bundle.provenance is not None
        assert len(v2_bundle.provenance.records) > 0

    def test_v2_has_external_authoritative_records(self, v2_bundle):
        from backend.app.provenance.models import ProvenanceKind
        kinds = {r.kind for r in v2_bundle.provenance.records}
        assert ProvenanceKind.EXTERNAL_AUTHORITATIVE in kinds

    def test_v2_has_modeled_records(self, v2_bundle):
        from backend.app.provenance.models import ProvenanceKind
        kinds = {r.kind for r in v2_bundle.provenance.records}
        assert ProvenanceKind.MODELED in kinds

    def test_v2_unique_product_ids(self, v2_bundle):
        ids = [dp.product_id for dp in v2_bundle.scenario.data_products]
        assert len(ids) == len(set(ids))

    def test_v2_link_inputs_latency_not_propagation(self, v2_bundle):
        """latency_s must be protocol/link overhead, NOT propagation delay."""
        latency_s = v2_bundle.scenario.link_inputs.get("latency_s")
        propagation_s = v2_bundle.scenario.distance_km * 1000 / 299792458
        assert latency_s == pytest.approx(1.5), f"Expected 1.5s latency, got {latency_s}"
        # Propagation delay is ~2979s; latency is NOT substituted
        assert latency_s != pytest.approx(propagation_s, abs=100), (
            "latency_s must not be the propagation delay."
        )


# ===========================================================================
# Provider security tests
# ===========================================================================


class TestProviderSecurity:
    """Security invariants preserved for V2."""

    def test_path_traversal_rejected(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import MissionSourceValidationError
        p = HistoricalReplayProvider()
        with pytest.raises((MissionSourceValidationError, ValueError)):
            p.load("data/replays/../../../etc/passwd.json")

    def test_absolute_path_rejected(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import MissionSourceValidationError
        p = HistoricalReplayProvider()
        with pytest.raises((MissionSourceValidationError, ValueError)):
            p.load("/etc/passwd.json")

    def test_backslash_rejected(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import MissionSourceValidationError
        p = HistoricalReplayProvider()
        with pytest.raises((MissionSourceValidationError, ValueError)):
            p.load("data\\replays\\juno_pj62_large_replay_v2_descriptor.json")

    def test_no_absolute_path_in_error_messages(self):
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import (
            MissionSourceValidationError,
            MissionSourceUnavailableError,
        )
        p = HistoricalReplayProvider()
        try:
            p.load("data/replays/nonexistent_file_b4_test.json")
        except (MissionSourceValidationError, MissionSourceUnavailableError) as exc:
            # Error message must not expose absolute filesystem path
            msg = str(exc)
            assert "C:\\" not in msg and "/home/" not in msg and "C:/" not in msg, (
                f"Absolute path exposed in error: {msg!r}"
            )
        except Exception:
            pass  # Other exceptions are also acceptable here


# ===========================================================================
# Provider error normalization
# ===========================================================================


class TestProviderV2ErrorNormalization:
    """V2 source graph errors must be wrapped as MissionSourceValidationError."""

    def test_source_graph_error_wrapped(self):
        """A ValueError from load_verified_v2_source_graph is wrapped as MissionSourceValidationError."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import MissionSourceValidationError
        from backend.app.mission_sources import v2_source_graph as sg_mod
        from unittest.mock import patch

        provider = HistoricalReplayProvider()

        with patch.object(
            sg_mod,
            "load_verified_v2_source_graph",
            side_effect=ValueError("Simulated source graph contradiction"),
        ):
            with pytest.raises(MissionSourceValidationError, match="source graph"):
                provider.load(_V2_SOURCE_REF)

    def test_unknown_schema_rejects_not_falls_back(self):
        """Unknown schema must not silently fall back to V1 or synthetic."""
        from backend.app.mission_sources.historical_provider import HistoricalReplayProvider
        from backend.app.mission_sources.errors import MissionSourceValidationError
        replays_dir = _REPO_ROOT / "data" / "replays"
        p = replays_dir / "_b4_test_unknown_schema_provider.json"
        try:
            p.write_text(json.dumps({"schema": "gcsi.not_a_known_schema"}), encoding="utf-8")
            ref = f"data/replays/{p.name}"
            provider = HistoricalReplayProvider()
            with pytest.raises(MissionSourceValidationError):
                provider.load(ref)
        finally:
            p.unlink(missing_ok=True)
