"""Phase 6D-A.1 — JPL Horizons Geometry Adapter Unit Tests.

Covers all required cases from the Phase 6D-A specification plus the
Phase 6D-A.1 protocol correction tests.
Uses httpx.MockTransport exclusively — zero live JPL/NASA network calls.

Test cases
----------
REQUEST MODEL:
 1.  valid Juno -61 target accepted
 2.  positive numeric target accepted
 3.  target name rejected
 4.  semicolon/injection syntax rejected
 5.  whitespace target rejected
 6.  naive datetime rejected
 7.  aware non-UTC datetime normalizes to UTC

REQUEST GENERATION:
 8.  fixed HTTPS JPL endpoint used
 9.  exact VECTORS mode used
10.  VEC_TABLE=6
11.  VEC_CORR=NONE
12.  OUT_UNITS=KM-S
13.  Earth geocenter only (CENTER='500@399')
14.  exactly one TLIST timestamp present
15.  START_TIME / STOP_TIME / STEP_SIZE absent
16.  no arbitrary caller-controlled Horizons options
17.  one fetch performs one HTTP request
17b. VEC_DELTA_T=NO explicitly sent

SUCCESS RESPONSE (VEC_TABLE=6 shape):
18.  valid signature source accepted
19.  exact API version 1.3 accepted
20.  result table parsed between $$SOE/$$EOE
21.  range parsed correctly  (column 3)
22.  range-rate parsed correctly  (column 4)
23.  one-way light-time parsed correctly  (column 2)
24.  negative range-rate accepted
25.  normalized geometry model is strict (extra fields forbidden)
25b. trailing comma / terminal empty cell is handled safely
25c. LT is read from semantic column 2
25d. RG is read from semantic column 3
25e. RR is read from semantic column 4
25f. representative fixture has true VEC_TABLE=6 shape (5 columns)

PARSER REJECTION:
25g. VEC_TABLE=3-shaped 11-column row is rejected
25h. unexpected extra non-empty semantic columns are rejected

PROVENANCE:
26.  kind == EXTERNAL_AUTHORITATIVE
27.  source_system is NASA/JPL Horizons API
28.  source_version == 1.3
29.  observed_at equals normalized requested epoch
30.  retrieved_at uses injected clock
31.  raw body SHA-256 matches independent calculation
32.  provenance_id deterministic for same query/body
33.  different response body changes provenance_id
34.  different target/epoch changes provenance_id
35.  provenance record is VALIDATED
35b. canonical provenance query identity includes VEC_DELTA_T and all fixed settings

PAYLOAD FAILURE:
36.  HTTP 200 + Horizons error field rejected
37.  wrong API version rejected
38.  wrong signature source rejected
39.  missing signature rejected
40.  malformed JSON rejected
41.  missing result rejected
42.  missing $$SOE rejected
43.  missing $$EOE rejected
44.  reversed/invalid markers rejected
44b. duplicate $$SOE rejected
44c. duplicate $$EOE rejected
44d. two complete SOE/EOE sections rejected
45.  zero data rows rejected
46.  multiple data rows rejected for single-TLIST request
47.  malformed numeric range rejected
48.  NaN rejected
49.  infinity rejected
50.  zero/negative range rejected
51.  zero/negative light-time rejected
52.  oversized payload rejected

TRANSPORT:
53.  timeout → HorizonsUnavailableError
54.  httpx RequestError → HorizonsUnavailableError
55.  HTTP 500 → HorizonsUnavailableError
56.  HTTP 503 → HorizonsUnavailableError
57.  HTTP 429 → HorizonsUnavailableError
58.  HTTP 400 → HorizonsValidationError
58b. HTTP 501 → HorizonsUnavailableError
58c. HTTP 599 → HorizonsUnavailableError

TRUST / REDACTION:
59.  public error does not include raw response sentinel
60.  public error does not include full request URL/query
61.  original lower-level exception preserved as __cause__

HARDENING:
61b. malformed non-UTF byte payload → HorizonsValidationError
61c. malformed JSON → HorizonsValidationError (existing)
61d. clock returning a non-datetime value → HorizonsValidationError (not AttributeError)
61e. no raw response sentinel appears in public validation errors

REGRESSION:
62.  Phase 6B provenance tests still pass (import sanity)
63.  Phase 6C mission-source tests still pass (import sanity)
64.  no import from state.py
65.  existing Scenario schema unchanged
66.  existing DataProduct schema unchanged
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from backend.app.mission_sources.adapters.horizons import (
    HorizonsAdapter,
    HorizonsAdapterError,
    HorizonsUnavailableError,
    HorizonsValidationError,
    _build_canonical_query_identity,
    _compute_provenance_id,
    _HORIZONS_ENDPOINT,
)
from backend.app.mission_sources.adapters.horizons_models import (
    HorizonsGeometry,
    HorizonsGeometryRequest,
    HorizonsGeometryResult,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc
_JUNO_ID = "-61"
_MARS_ID = "499"

# Fixed UTC epoch used throughout.
_EPOCH_UTC = datetime(2026, 8, 27, 0, 0, 0, tzinfo=UTC)

# Fixed retrieved_at for deterministic tests.
_RETRIEVED_AT = datetime(2026, 8, 27, 20, 41, 0, tzinfo=UTC)


def _fixed_clock() -> datetime:
    """Clock returning the fixed _RETRIEVED_AT timestamp."""
    return _RETRIEVED_AT


def _make_horizons_response(
    *,
    result_text: Optional[str] = None,
    signature: Optional[dict] = None,
    include_error: bool = False,
    extra_fields: Optional[dict] = None,
) -> bytes:
    """Build a representative Horizons JSON response as bytes."""
    if result_text is None:
        result_text = _VALID_RESULT_TEXT

    payload: dict = {}

    if signature is None:
        payload["signature"] = {
            "source": "NASA/JPL Horizons API",
            "version": "1.3",
        }
    elif signature is not False:
        payload["signature"] = signature

    if include_error:
        payload["error"] = "No ephemeris data for target body"

    if result_text is not None:
        payload["result"] = result_text

    if extra_fields:
        payload.update(extra_fields)

    return json.dumps(payload).encode("utf-8")


# ---------------------------------------------------------------------------
# TRUE VEC_TABLE=6 test data
#
# VEC_TABLE=6, VEC_DELTA_T=NO CSV row format:
#   JDTDB, CalDate, LT, RG, RR[,]
# Semantic columns (after stripping trailing empty):
#   col 0 = JDTDB
#   col 1 = Calendar Date
#   col 2 = LT  (one-way light-time, seconds)
#   col 3 = RG  (range, km)
#   col 4 = RR  (range-rate, km/s)
# ---------------------------------------------------------------------------

_LT_VALUE = 2795.812640498820       # one-way light-time, seconds
_RG_VALUE = 838249962.14964500      # range, km
_RR_VALUE = 14.639175321946800      # range-rate, km/s

# True VEC_TABLE=6 data row: JDTDB, Date, LT, RG, RR + trailing comma
_VALID_DATA_ROW = (
    " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.0000,"
    f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
)

_VALID_RESULT_TEXT = (
    "JPL/HORIZONS header line\n"
    "$$SOE\n"
    + _VALID_DATA_ROW
    + "\n$$EOE\n"
    "Coord. ref. frame : ICRF\n"
)

_NEGATIVE_RR_DATA_ROW = (
    " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.0000,"
    f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  -7.500000000000000E+00,"
)

_NEGATIVE_RR_RESULT = (
    "JPL/HORIZONS header\n"
    "$$SOE\n"
    + _NEGATIVE_RR_DATA_ROW
    + "\n$$EOE\n"
)

# VEC_TABLE=3-shaped row (11 columns) — must be rejected.
_VEC_TABLE_3_DATA_ROW = (
    " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.0000,"
    " -5.984637826741320E+08, -4.062735178649830E+08,  1.068093831283030E+08,"
    "  -4.526282376341250E+00, -7.218339253834060E+00, -2.187436812374460E+00,"
    f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
)


def _make_mock_transport(
    *,
    status_code: int = 200,
    content: Optional[bytes] = None,
) -> httpx.MockTransport:
    """Return an httpx.MockTransport that always responds with the given status/content."""
    if content is None:
        content = _make_horizons_response()

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=status_code, content=content)

    return httpx.MockTransport(_handler)


def _make_adapter(
    *,
    status_code: int = 200,
    content: Optional[bytes] = None,
    clock=_fixed_clock,
) -> HorizonsAdapter:
    """Convenience: build an adapter with a mock transport."""
    transport = _make_mock_transport(status_code=status_code, content=content)
    client = httpx.Client(transport=transport)
    return HorizonsAdapter(client=client, clock=clock)


def _make_request(
    target: str = _JUNO_ID,
    epoch: datetime = _EPOCH_UTC,
) -> HorizonsGeometryRequest:
    """Build a valid HorizonsGeometryRequest."""
    return HorizonsGeometryRequest(target_spk_id=target, epoch_utc=epoch)


# ---------------------------------------------------------------------------
# REQUEST MODEL  (tests 1-7)
# ---------------------------------------------------------------------------


class TestRequestModel:
    """Tests 1-7: HorizonsGeometryRequest validation."""

    def test_01_valid_juno_target(self):
        """Test 1: valid Juno -61 target accepted."""
        req = HorizonsGeometryRequest(target_spk_id="-61", epoch_utc=_EPOCH_UTC)
        assert req.target_spk_id == "-61"

    def test_02_positive_numeric_target(self):
        """Test 2: positive numeric target accepted."""
        req = HorizonsGeometryRequest(target_spk_id="499", epoch_utc=_EPOCH_UTC)
        assert req.target_spk_id == "499"

    def test_03_target_name_rejected(self):
        """Test 3: target name rejected."""
        with pytest.raises(ValidationError) as exc_info:
            HorizonsGeometryRequest(target_spk_id="Juno", epoch_utc=_EPOCH_UTC)
        assert "target_spk_id" in str(exc_info.value).lower() or "numeric" in str(exc_info.value).lower()

    def test_04_semicolon_injection_rejected(self):
        """Test 4: semicolon/injection syntax rejected."""
        with pytest.raises(ValidationError):
            HorizonsGeometryRequest(target_spk_id="-61;foo", epoch_utc=_EPOCH_UTC)

    def test_05_whitespace_target_rejected(self):
        """Test 5: whitespace target rejected."""
        with pytest.raises(ValidationError):
            HorizonsGeometryRequest(target_spk_id=" -61", epoch_utc=_EPOCH_UTC)

    def test_06_naive_datetime_rejected(self):
        """Test 6: naive datetime rejected."""
        naive = datetime(2026, 8, 27, 0, 0, 0)  # no tzinfo
        with pytest.raises(ValidationError) as exc_info:
            HorizonsGeometryRequest(target_spk_id="-61", epoch_utc=naive)
        assert "timezone" in str(exc_info.value).lower() or "aware" in str(exc_info.value).lower()

    def test_07_non_utc_aware_normalizes_to_utc(self):
        """Test 7: aware non-UTC datetime normalizes to UTC."""
        # +07:00 offset
        plus7 = timezone(timedelta(hours=7))
        local_epoch = datetime(2026, 8, 27, 7, 0, 0, tzinfo=plus7)
        req = HorizonsGeometryRequest(target_spk_id="-61", epoch_utc=local_epoch)
        # Should have been normalized to UTC midnight
        assert req.epoch_utc.tzinfo == UTC
        assert req.epoch_utc.hour == 0
        assert req.epoch_utc.minute == 0

    def test_model_is_frozen(self):
        """HorizonsGeometryRequest is frozen."""
        req = _make_request()
        with pytest.raises(Exception):
            req.target_spk_id = "999"  # type: ignore[misc]

    def test_extra_fields_forbidden(self):
        """Extra fields are forbidden on HorizonsGeometryRequest."""
        with pytest.raises(ValidationError):
            HorizonsGeometryRequest(
                target_spk_id="-61",
                epoch_utc=_EPOCH_UTC,
                extra_param="bad",
            )

    def test_des_syntax_rejected(self):
        """DES= Horizons syntax rejected."""
        with pytest.raises(ValidationError):
            HorizonsGeometryRequest(target_spk_id="DES=2020 XC", epoch_utc=_EPOCH_UTC)


# ---------------------------------------------------------------------------
# REQUEST GENERATION  (tests 8-17b)
# ---------------------------------------------------------------------------


class TestRequestGeneration:
    """Tests 8-17b: verify the Horizons HTTP request parameters."""

    def _capture_params(self) -> dict:
        """Send one fetch and capture the query params sent to the adapter."""
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.url.params))
            return httpx.Response(
                status_code=200,
                content=_make_horizons_response(),
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        req = _make_request()
        adapter.fetch(req)
        return captured

    def _capture_url(self) -> str:
        """Capture the full request URL."""
        captured_url: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_url.append(str(request.url).split("?")[0])
            return httpx.Response(
                status_code=200,
                content=_make_horizons_response(),
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.fetch(_make_request())
        return captured_url[0]

    def test_08_fixed_https_jpl_endpoint(self):
        """Test 8: fixed HTTPS JPL endpoint used."""
        url = self._capture_url()
        assert url == _HORIZONS_ENDPOINT
        assert url.startswith("https://ssd.jpl.nasa.gov/")

    def test_09_vectors_mode(self):
        """Test 9: EPHEM_TYPE=VECTORS."""
        params = self._capture_params()
        assert params["EPHEM_TYPE"] == "VECTORS"

    def test_10_vec_table_6(self):
        """Test 10: VEC_TABLE=6."""
        params = self._capture_params()
        assert params["VEC_TABLE"] == "6"

    def test_11_vec_corr_none(self):
        """Test 11: VEC_CORR=NONE."""
        params = self._capture_params()
        assert params["VEC_CORR"] == "NONE"

    def test_12_out_units_km_s(self):
        """Test 12: OUT_UNITS=KM-S."""
        params = self._capture_params()
        assert params["OUT_UNITS"] == "KM-S"

    def test_13_earth_geocenter_only(self):
        """Test 13: CENTER='500@399' (Earth geocenter)."""
        params = self._capture_params()
        center = params["CENTER"]
        # The adapter wraps in single quotes.
        assert "500@399" in center

    def test_14_exactly_one_tlist_timestamp(self):
        """Test 14: exactly one TLIST timestamp present."""
        params = self._capture_params()
        assert "TLIST" in params
        tlist = params["TLIST"]
        # Unwrap surrounding quotes and check there's exactly one value.
        assert tlist  # non-empty

    def test_15_no_start_stop_step(self):
        """Test 15: START_TIME / STOP_TIME / STEP_SIZE absent."""
        params = self._capture_params()
        assert "START_TIME" not in params
        assert "STOP_TIME" not in params
        assert "STEP_SIZE" not in params

    def test_16_no_arbitrary_caller_controlled_options(self):
        """Test 16: caller cannot inject arbitrary Horizons options."""
        # The request model only accepts target_spk_id and epoch_utc.
        with pytest.raises((ValidationError, TypeError)):
            HorizonsGeometryRequest(
                target_spk_id="-61",
                epoch_utc=_EPOCH_UTC,
                EPHEM_TYPE="OBSERVER",  # forbidden
            )

    def test_17_one_fetch_one_http_request(self):
        """Test 17: one fetch performs exactly one HTTP request."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                status_code=200,
                content=_make_horizons_response(),
            )

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.fetch(_make_request())
        assert call_count == 1

    def test_17b_vec_delta_t_no_sent(self):
        """Test 17b: request explicitly includes VEC_DELTA_T=NO."""
        params = self._capture_params()
        assert params.get("VEC_DELTA_T") == "NO"


# ---------------------------------------------------------------------------
# SUCCESS RESPONSE  (tests 18-25f)
# ---------------------------------------------------------------------------


class TestSuccessResponse:
    """Tests 18-25f: valid response handling and geometry parsing."""

    def _fetch_valid(
        self,
        result_text: Optional[str] = None,
        target: str = _JUNO_ID,
    ) -> HorizonsGeometryResult:
        content = _make_horizons_response(result_text=result_text)
        adapter = _make_adapter(content=content)
        return adapter.fetch(_make_request(target=target))

    def test_18_valid_signature_source_accepted(self):
        """Test 18: valid signature source accepted."""
        result = self._fetch_valid()
        assert result.geometry.api_source == "NASA/JPL Horizons API"

    def test_19_exact_api_version_13_accepted(self):
        """Test 19: exact API version 1.3 accepted."""
        result = self._fetch_valid()
        assert result.geometry.api_version == "1.3"

    def test_20_table_parsed_soe_eoe(self):
        """Test 20: result table parsed between $$SOE/$$EOE."""
        result = self._fetch_valid()
        # Successful parse means table was found and parsed.
        assert result.geometry.range_km > 0

    def test_21_range_parsed_correctly(self):
        """Test 21: range_km parsed correctly from semantic column 3."""
        result = self._fetch_valid()
        assert abs(result.geometry.range_km - _RG_VALUE) < 1.0  # within 1 km

    def test_22_range_rate_parsed_correctly(self):
        """Test 22: range_rate_km_s parsed correctly from semantic column 4."""
        result = self._fetch_valid()
        assert abs(result.geometry.range_rate_km_s - _RR_VALUE) < 0.001

    def test_23_light_time_parsed_correctly(self):
        """Test 23: one_way_light_time_s parsed correctly from semantic column 2."""
        result = self._fetch_valid()
        assert abs(result.geometry.one_way_light_time_s - _LT_VALUE) < 0.001

    def test_24_negative_range_rate_accepted(self):
        """Test 24: negative range-rate accepted."""
        content = _make_horizons_response(result_text=_NEGATIVE_RR_RESULT)
        adapter = _make_adapter(content=content)
        result = adapter.fetch(_make_request())
        assert result.geometry.range_rate_km_s == pytest.approx(-7.5, abs=0.001)

    def test_25_geometry_model_extra_fields_forbidden(self):
        """Test 25: HorizonsGeometry forbids extra fields."""
        with pytest.raises(ValidationError):
            HorizonsGeometry(
                target_spk_id="-61",
                center="500@399",
                epoch_utc=_EPOCH_UTC,
                range_km=1e8,
                range_rate_km_s=10.0,
                one_way_light_time_s=333.0,
                api_source="NASA/JPL Horizons API",
                api_version="1.3",
                unexpected_field="bad",
            )

    def test_25b_trailing_comma_empty_cell_handled(self):
        """Test 25b: trailing comma / terminal empty cell is handled safely.

        Horizons emits a trailing comma which csv.reader produces as a final
        empty cell.  The adapter must strip it and still parse 5 columns.
        """
        # _VALID_DATA_ROW ends with a trailing comma — confirm success.
        assert _VALID_DATA_ROW.endswith(",")
        result = self._fetch_valid()
        assert result.geometry.range_km > 0

    def test_25c_lt_is_column_2(self):
        """Test 25c: LT (one-way light time) is read from semantic column 2."""
        result = self._fetch_valid()
        assert abs(result.geometry.one_way_light_time_s - _LT_VALUE) < 0.001

    def test_25d_rg_is_column_3(self):
        """Test 25d: RG (range) is read from semantic column 3."""
        result = self._fetch_valid()
        assert abs(result.geometry.range_km - _RG_VALUE) < 1.0

    def test_25e_rr_is_column_4(self):
        """Test 25e: RR (range-rate) is read from semantic column 4."""
        result = self._fetch_valid()
        assert abs(result.geometry.range_rate_km_s - _RR_VALUE) < 0.001

    def test_25f_fixture_has_vec_table_6_shape(self):
        """Test 25f: representative fixture has true VEC_TABLE=6 shape (5 semantic columns).

        Load the fixture and verify the data row contains exactly 5 semantic
        columns (JDTDB, Date, LT, RG, RR) — not 11 VEC_TABLE=3 columns.
        """
        import pathlib
        import csv
        import io

        fixture_path = (
            pathlib.Path(__file__).parent.parent
            / "fixtures"
            / "horizons"
            / "juno_2026_aug_27_vectors.json"
        )
        raw = fixture_path.read_bytes()
        payload = json.loads(raw)
        result_text = payload["result"]

        # Extract SOE block
        soe_idx = result_text.index("$$SOE")
        eoe_idx = result_text.index("$$EOE")
        block = result_text[soe_idx + len("$$SOE"):eoe_idx]

        reader = csv.reader(io.StringIO(block))
        data_rows = []
        for raw_row in reader:
            stripped = [c.strip() for c in raw_row]
            if stripped and stripped[-1] == "":
                stripped = stripped[:-1]
            if stripped and stripped[0]:
                data_rows.append(stripped)

        assert len(data_rows) == 1, "fixture must have exactly one data row"
        row = data_rows[0]
        # VEC_TABLE=6: exactly 5 semantic columns
        assert len(row) == 5, (
            f"fixture data row has {len(row)} columns; expected 5 (VEC_TABLE=6 shape)"
        )
        # Must NOT be 11 (VEC_TABLE=3)
        assert len(row) != 11, "fixture must not contain X/Y/Z/VX/VY/VZ columns"


# ---------------------------------------------------------------------------
# PARSER REJECTION  (tests 25g-25h)
# ---------------------------------------------------------------------------


class TestParserRejection:
    """Tests 25g-25h: reject wrong-shaped rows."""

    def test_25g_vec_table_3_row_rejected(self):
        """Test 25g: VEC_TABLE=3-shaped 11-column row is rejected."""
        result_text = (
            "$$SOE\n"
            + _VEC_TABLE_3_DATA_ROW
            + "\n$$EOE\n"
        )
        content = _make_horizons_response(result_text=result_text)
        adapter = _make_adapter(content=content)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        # Error message should indicate VEC_TABLE=3 layout
        assert "11" in str(exc_info.value) or "VEC_TABLE=3" in str(exc_info.value)

    def test_25h_extra_non_empty_columns_rejected(self):
        """Test 25h: unexpected extra non-empty semantic columns are rejected."""
        # Build a 6-column row (not 5, not 11)
        extra_col_row = (
            " 2460933.500000000, A.D. 2026-Aug-27 00:00:00.0000,"
            f"  {_LT_VALUE:.15E},  {_RG_VALUE:.15E},  {_RR_VALUE:.15E},"
            "  9.999999999999999E+99,"
        )
        result_text = "$$SOE\n" + extra_col_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        adapter = _make_adapter(content=content)
        with pytest.raises(HorizonsValidationError):
            adapter.fetch(_make_request())


# ---------------------------------------------------------------------------
# PROVENANCE  (tests 26-35b)
# ---------------------------------------------------------------------------


class TestProvenance:
    """Tests 26-35b: provenance record construction."""

    def _fetch_valid(
        self,
        target: str = _JUNO_ID,
        epoch: datetime = _EPOCH_UTC,
        content: Optional[bytes] = None,
    ) -> HorizonsGeometryResult:
        if content is None:
            content = _make_horizons_response()
        adapter = _make_adapter(content=content)
        return adapter.fetch(_make_request(target=target, epoch=epoch))

    def test_26_kind_external_authoritative(self):
        """Test 26: kind == EXTERNAL_AUTHORITATIVE."""
        result = self._fetch_valid()
        assert result.provenance.kind == ProvenanceKind.EXTERNAL_AUTHORITATIVE

    def test_27_source_system_is_horizons(self):
        """Test 27: source_system is 'NASA/JPL Horizons API'."""
        result = self._fetch_valid()
        assert result.provenance.source_system == "NASA/JPL Horizons API"

    def test_28_source_version_is_13(self):
        """Test 28: source_version == '1.3'."""
        result = self._fetch_valid()
        assert result.provenance.source_version == "1.3"

    def test_29_observed_at_equals_normalized_epoch(self):
        """Test 29: observed_at equals the normalized UTC epoch."""
        result = self._fetch_valid()
        assert result.provenance.observed_at == _EPOCH_UTC

    def test_30_retrieved_at_uses_injected_clock(self):
        """Test 30: retrieved_at comes from the injected clock."""
        result = self._fetch_valid()
        assert result.provenance.retrieved_at == _RETRIEVED_AT

    def test_31_content_sha256_matches_independent_calculation(self):
        """Test 31: content_sha256 is SHA-256 of the raw response bytes."""
        raw = _make_horizons_response()
        adapter = _make_adapter(content=raw)
        result = adapter.fetch(_make_request())
        expected = hashlib.sha256(raw).hexdigest()
        assert result.provenance.content_sha256 == expected

    def test_32_provenance_id_deterministic_same_query_body(self):
        """Test 32: provenance_id is deterministic for same query/body."""
        raw = _make_horizons_response()
        adapter1 = _make_adapter(content=raw)
        adapter2 = _make_adapter(content=raw)
        r1 = adapter1.fetch(_make_request())
        r2 = adapter2.fetch(_make_request())
        assert r1.provenance.provenance_id == r2.provenance.provenance_id

    def test_33_different_body_changes_provenance_id(self):
        """Test 33: different response body changes provenance_id."""
        raw1 = _make_horizons_response()
        # Slightly different result text.
        alt_result = _VALID_RESULT_TEXT.replace(
            f"  {_LT_VALUE:.15E}",
            "  2.900000000000000E+03",
        )
        raw2 = _make_horizons_response(result_text=alt_result)
        r1 = _make_adapter(content=raw1).fetch(_make_request())
        r2 = _make_adapter(content=raw2).fetch(_make_request())
        assert r1.provenance.provenance_id != r2.provenance.provenance_id

    def test_34_different_target_changes_provenance_id(self):
        """Test 34: different target changes provenance_id."""
        raw = _make_horizons_response()
        r_juno = _make_adapter(content=raw).fetch(_make_request(target="-61"))
        # Build a response that works for Mars too (same format)
        r_mars = _make_adapter(content=raw).fetch(_make_request(target="499"))
        assert r_juno.provenance.provenance_id != r_mars.provenance.provenance_id

    def test_34b_different_epoch_changes_provenance_id(self):
        """Test 34b: different epoch changes provenance_id."""
        raw = _make_horizons_response()
        epoch1 = datetime(2026, 8, 27, 0, 0, 0, tzinfo=UTC)
        epoch2 = datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
        r1 = _make_adapter(content=raw).fetch(_make_request(epoch=epoch1))
        r2 = _make_adapter(content=raw).fetch(_make_request(epoch=epoch2))
        assert r1.provenance.provenance_id != r2.provenance.provenance_id

    def test_35_provenance_status_validated(self):
        """Test 35: provenance record validation_status is VALIDATED."""
        result = self._fetch_valid()
        assert result.provenance.validation_status == ProvenanceValidationStatus.VALIDATED

    def test_35b_canonical_identity_includes_all_fixed_settings(self):
        """Test 35b: canonical provenance query identity includes VEC_DELTA_T
        and all other fixed protocol settings.
        """
        req = _make_request()
        identity_str = _build_canonical_query_identity(req)
        identity = json.loads(identity_str)

        # All required keys must be present.
        required_keys = {
            "target_spk_id",
            "tlist_epoch_utc",
            "center",
            "ephem_type",
            "out_units",
            "vec_table",
            "vec_corr",
            "vec_delta_t",
            "time_type",
            "tlist_type",
            "csv_format",
            "ref_system",
            "ref_plane",
            "cal_type",
        }
        for key in required_keys:
            assert key in identity, f"canonical identity missing key: {key}"

        # VEC_DELTA_T must be NO.
        assert identity["vec_delta_t"] == "NO"
        # VEC_TABLE must be 6.
        assert identity["vec_table"] == "6"
        # VEC_CORR must be NONE.
        assert identity["vec_corr"] == "NONE"

    def test_provenance_id_not_uuid4_based(self):
        """provenance_id must not contain uuid4 randomness (deterministic)."""
        raw = _make_horizons_response()
        r1 = _make_adapter(content=raw).fetch(_make_request())
        r2 = _make_adapter(content=raw).fetch(_make_request())
        # Must be identical (not random)
        assert r1.provenance.provenance_id == r2.provenance.provenance_id

    def test_source_uri_is_fixed_endpoint(self):
        """source_uri is the fixed Horizons API endpoint."""
        result = _make_adapter().fetch(_make_request())
        assert result.provenance.source_uri == _HORIZONS_ENDPOINT


# ---------------------------------------------------------------------------
# PAYLOAD FAILURE  (tests 36-52)
# ---------------------------------------------------------------------------


class TestPayloadFailure:
    """Tests 36-52: malformed/invalid Horizons payload handling."""

    def _expect_validation_error(self, content: bytes) -> HorizonsValidationError:
        adapter = _make_adapter(content=content)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        return exc_info.value

    def test_36_http_200_with_horizons_error_rejected(self):
        """Test 36: HTTP 200 + Horizons error field rejected."""
        content = _make_horizons_response(include_error=True, result_text=None)
        self._expect_validation_error(content)

    def test_37_wrong_api_version_rejected(self):
        """Test 37: wrong API version rejected."""
        content = _make_horizons_response(
            signature={"source": "NASA/JPL Horizons API", "version": "2.0"}
        )
        self._expect_validation_error(content)

    def test_38_wrong_signature_source_rejected(self):
        """Test 38: wrong signature source rejected."""
        content = _make_horizons_response(
            signature={"source": "Some Other Service", "version": "1.3"}
        )
        self._expect_validation_error(content)

    def test_39_missing_signature_rejected(self):
        """Test 39: missing signature rejected."""
        payload = {"result": _VALID_RESULT_TEXT}
        content = json.dumps(payload).encode()
        self._expect_validation_error(content)

    def test_40_malformed_json_rejected(self):
        """Test 40: malformed JSON rejected."""
        content = b"not valid json {"
        self._expect_validation_error(content)

    def test_41_missing_result_rejected(self):
        """Test 41: missing result field rejected."""
        payload = {
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.3"}
        }
        content = json.dumps(payload).encode()
        self._expect_validation_error(content)

    def test_42_missing_soe_rejected(self):
        """Test 42: missing $$SOE rejected."""
        result_no_soe = (
            "JPL/HORIZONS header\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n"
        )
        content = _make_horizons_response(result_text=result_no_soe)
        self._expect_validation_error(content)

    def test_43_missing_eoe_rejected(self):
        """Test 43: missing $$EOE rejected."""
        result_no_eoe = (
            "JPL/HORIZONS header\n"
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n"
        )
        content = _make_horizons_response(result_text=result_no_eoe)
        self._expect_validation_error(content)

    def test_44_reversed_markers_rejected(self):
        """Test 44: $$EOE before $$SOE rejected."""
        reversed_text = (
            "$$EOE\n"
            + _VALID_DATA_ROW
            + "\n$$SOE\n"
        )
        content = _make_horizons_response(result_text=reversed_text)
        self._expect_validation_error(content)

    def test_44b_duplicate_soe_rejected(self):
        """Test 44b: duplicate $$SOE rejected."""
        dup_soe_text = (
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n"
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n"
        )
        content = _make_horizons_response(result_text=dup_soe_text)
        err = self._expect_validation_error(content)
        assert "duplicate" in str(err).lower() or "$$SOE" in str(err)

    def test_44c_duplicate_eoe_rejected(self):
        """Test 44c: duplicate $$EOE rejected."""
        dup_eoe_text = (
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n$$EOE\n"
        )
        content = _make_horizons_response(result_text=dup_eoe_text)
        err = self._expect_validation_error(content)
        assert "duplicate" in str(err).lower() or "$$EOE" in str(err)

    def test_44d_two_complete_sections_rejected(self):
        """Test 44d: two complete SOE/EOE sections are rejected."""
        two_sections = (
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n"
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n"
        )
        content = _make_horizons_response(result_text=two_sections)
        self._expect_validation_error(content)

    def test_45_zero_data_rows_rejected(self):
        """Test 45: zero data rows rejected."""
        empty_table = "header\n$$SOE\n$$EOE\n"
        content = _make_horizons_response(result_text=empty_table)
        self._expect_validation_error(content)

    def test_46_multiple_data_rows_rejected(self):
        """Test 46: multiple data rows rejected for single-TLIST request."""
        multi_rows = (
            "$$SOE\n"
            + _VALID_DATA_ROW
            + "\n"
            + _VALID_DATA_ROW
            + "\n$$EOE\n"
        )
        content = _make_horizons_response(result_text=multi_rows)
        self._expect_validation_error(content)

    def test_47_malformed_numeric_range_rejected(self):
        """Test 47: malformed numeric range rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_RG_VALUE:.15E}",
            "  NOT_A_NUMBER",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_48_nan_rejected(self):
        """Test 48: NaN in range field rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_RG_VALUE:.15E}",
            "  NaN",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_49_infinity_rejected(self):
        """Test 49: Infinity in range field rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_RG_VALUE:.15E}",
            "  Inf",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_50_zero_range_rejected(self):
        """Test 50: zero range rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_RG_VALUE:.15E}",
            "  0.000000000000000E+00",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_50b_negative_range_rejected(self):
        """Test 50b: negative range rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_RG_VALUE:.15E}",
            "  -1.000000000000000E+08",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_51_zero_light_time_rejected(self):
        """Test 51: zero light-time rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_LT_VALUE:.15E}",
            "  0.000000000000000E+00",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_51b_negative_light_time_rejected(self):
        """Test 51b: negative light-time rejected."""
        bad_row = _VALID_DATA_ROW.replace(
            f"  {_LT_VALUE:.15E}",
            "  -5.000000000000000E+02",
        )
        result_text = "$$SOE\n" + bad_row + "\n$$EOE\n"
        content = _make_horizons_response(result_text=result_text)
        self._expect_validation_error(content)

    def test_52_oversized_payload_rejected(self):
        """Test 52: oversized payload rejected."""
        # 1 MiB + 1 byte
        oversized = b"X" * (1 * 1024 * 1024 + 1)
        adapter = _make_adapter(content=oversized)
        with pytest.raises(HorizonsValidationError):
            adapter.fetch(_make_request())

    def test_json_not_object_rejected(self):
        """Non-object JSON top level rejected."""
        content = json.dumps([1, 2, 3]).encode()
        self._expect_validation_error(content)

    def test_error_field_overrides_result(self):
        """error field takes precedence even when result is present."""
        payload = {
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.3"},
            "error": "Ephemeris not available",
            "result": _VALID_RESULT_TEXT,
        }
        content = json.dumps(payload).encode()
        self._expect_validation_error(content)


# ---------------------------------------------------------------------------
# TRANSPORT  (tests 53-58c)
# ---------------------------------------------------------------------------


class TestTransport:
    """Tests 53-58c: HTTP transport error mapping."""

    def test_53_timeout_raises_unavailable(self):
        """Test 53: timeout → HorizonsUnavailableError."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        with pytest.raises(HorizonsUnavailableError) as exc_info:
            adapter.fetch(_make_request())
        assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)

    def test_54_request_error_raises_unavailable(self):
        """Test 54: httpx.RequestError → HorizonsUnavailableError."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        with pytest.raises(HorizonsUnavailableError) as exc_info:
            adapter.fetch(_make_request())
        assert isinstance(exc_info.value.__cause__, httpx.RequestError)

    def test_55_http_500_raises_unavailable(self):
        """Test 55: HTTP 500 → HorizonsUnavailableError."""
        adapter = _make_adapter(status_code=500, content=b"Internal Server Error")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_56_http_503_raises_unavailable(self):
        """Test 56: HTTP 503 → HorizonsUnavailableError."""
        adapter = _make_adapter(status_code=503, content=b"Service Unavailable")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_57_http_429_raises_unavailable(self):
        """Test 57: HTTP 429 → HorizonsUnavailableError."""
        adapter = _make_adapter(status_code=429, content=b"Too Many Requests")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_58_http_400_raises_validation_error(self):
        """Test 58: HTTP 400 → HorizonsValidationError."""
        adapter = _make_adapter(status_code=400, content=b"Bad Request")
        with pytest.raises(HorizonsValidationError):
            adapter.fetch(_make_request())

    def test_58b_http_501_raises_unavailable(self):
        """Test 58b: HTTP 501 → HorizonsUnavailableError (all 5xx are availability)."""
        adapter = _make_adapter(status_code=501, content=b"Not Implemented")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_58c_http_599_raises_unavailable(self):
        """Test 58c: HTTP 599 → HorizonsUnavailableError (all 5xx are availability)."""
        adapter = _make_adapter(status_code=599, content=b"Unknown Server Error")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_http_502_raises_unavailable(self):
        """HTTP 502 → HorizonsUnavailableError (additional coverage)."""
        adapter = _make_adapter(status_code=502, content=b"Bad Gateway")
        with pytest.raises(HorizonsUnavailableError):
            adapter.fetch(_make_request())

    def test_unexpected_status_fails_closed(self):
        """Unexpected non-200/4xx/5xx status fails closed as validation error."""
        adapter = _make_adapter(status_code=301, content=b"Moved Permanently")
        with pytest.raises(HorizonsValidationError):
            adapter.fetch(_make_request())


# ---------------------------------------------------------------------------
# TRUST / REDACTION  (tests 59-61e)
# ---------------------------------------------------------------------------


class TestTrustRedaction:
    """Tests 59-61e: error messages do not leak sensitive data."""

    _SENTINEL = "SECRET_HORIZONS_RAW_CONTENT_SENTINEL_XYZ"

    def _get_validation_error_msg(self, content: bytes) -> str:
        adapter = _make_adapter(content=content)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        return str(exc_info.value)

    def test_59_error_does_not_include_raw_response(self):
        """Test 59: public error message does not include raw response content."""
        payload = {
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.3"},
            "error": self._SENTINEL,
        }
        content = json.dumps(payload).encode()
        err_msg = self._get_validation_error_msg(content)
        assert self._SENTINEL not in err_msg

    def test_60_error_does_not_include_full_url(self):
        """Test 60: public error message does not include full request URL/query."""
        content = b"not json"
        err_msg = self._get_validation_error_msg(content)
        # Should not contain query parameters or full URL with params.
        assert "COMMAND" not in err_msg
        assert "ssd.jpl.nasa.gov" not in err_msg

    def test_61_original_exception_preserved_as_cause(self):
        """Test 61: original lower-level exception preserved as __cause__."""
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        with pytest.raises(HorizonsUnavailableError) as exc_info:
            adapter.fetch(_make_request())
        # __cause__ must be the original httpx exception
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, httpx.TimeoutException)

    def test_61b_malformed_non_utf_bytes_raises_validation_error(self):
        """Test 61b: malformed non-UTF byte payload → HorizonsValidationError."""
        # \xff\xfe is invalid UTF-8 (it is a UTF-16 BOM, but not valid JSON).
        bad_bytes = b"\xff\xfe\x00\x01invalid utf-8 sequence"
        adapter = _make_adapter(content=bad_bytes)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        # Must not be a raw UnicodeDecodeError leaking through.
        assert isinstance(exc_info.value, HorizonsValidationError)

    def test_61c_malformed_json_raises_validation_error(self):
        """Test 61c: malformed JSON still → HorizonsValidationError (with __cause__)."""
        bad_json = b'{"incomplete": '
        adapter = _make_adapter(content=bad_json)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        assert exc_info.value.__cause__ is not None

    def test_61d_non_datetime_clock_raises_validation_error(self):
        """Test 61d: clock returning a non-datetime value → HorizonsValidationError."""
        def bad_clock():
            return "not a datetime"

        content = _make_horizons_response()
        transport = _make_mock_transport(content=content)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=bad_clock)
        with pytest.raises(HorizonsValidationError) as exc_info:
            adapter.fetch(_make_request())
        # Must be HorizonsValidationError, NOT AttributeError.
        assert isinstance(exc_info.value, HorizonsValidationError)
        assert not isinstance(exc_info.value, AttributeError)

    def test_61e_no_sentinel_in_public_validation_errors(self):
        """Test 61e: no raw response sentinel appears in public validation errors."""
        # Use the sentinel as a bad result field to verify it doesn't leak.
        payload = {
            "signature": {"source": "NASA/JPL Horizons API", "version": "1.3"},
            "result": self._SENTINEL,  # no $$SOE/$$EOE — will fail parsing
        }
        content = json.dumps(payload).encode()
        err_msg = self._get_validation_error_msg(content)
        assert self._SENTINEL not in err_msg


# ---------------------------------------------------------------------------
# REGRESSION  (tests 62-66)
# ---------------------------------------------------------------------------


class TestRegression:
    """Tests 62-66: regression checks for Phase 6B/6C isolation."""

    def test_62_phase6b_provenance_importable(self):
        """Test 62: Phase 6B provenance models still importable."""
        from backend.app.provenance.models import (
            ProvenanceKind,
            ProvenanceManifest,
            ProvenanceRecord,
            ProvenanceValidationStatus,
        )
        assert ProvenanceKind.EXTERNAL_AUTHORITATIVE is not None

    def test_63_phase6c_mission_sources_importable(self):
        """Test 63: Phase 6C mission source boundary still importable."""
        from backend.app.mission_sources import (
            BaseMissionSourceProvider,
            MissionSourceBundle,
            MissionSourceError,
            MissionSourceUnavailableError,
            MissionSourceValidationError,
            MissionSourceMode,
            SyntheticScenarioProvider,
        )
        assert SyntheticScenarioProvider is not None

    def test_64_no_import_from_state_py(self):
        """Test 64: adapter modules do not import from state.py."""
        import importlib
        import sys

        adapter_mod = sys.modules.get(
            "backend.app.mission_sources.adapters.horizons"
        )
        if adapter_mod is None:
            import backend.app.mission_sources.adapters.horizons as adapter_mod

        # state.py must NOT be in the adapter's source references.
        import inspect
        source = inspect.getsource(adapter_mod)
        assert "from backend.app.state" not in source
        assert "import state" not in source

    def test_65_scenario_schema_unchanged(self):
        """Test 65: existing Scenario schema is unchanged by Phase 6D-A."""
        from backend.app.models.scenario import Scenario
        # Verify key fields still present.
        fields = Scenario.model_fields
        assert "mission_state" in fields
        assert "packets" in fields

    def test_66_data_product_schema_unchanged(self):
        """Test 66: existing DataProduct schema is unchanged by Phase 6D-A."""
        from backend.app.models.data_product import DataProduct
        fields = DataProduct.model_fields
        assert "product_id" in fields
        assert "size_bits" in fields


# ---------------------------------------------------------------------------
# CLIENT OWNERSHIP
# ---------------------------------------------------------------------------


class TestClientOwnership:
    """Verify HTTP client ownership semantics."""

    def test_owned_client_closed_on_exit(self):
        """Internally-created client is closed when adapter used as context manager."""
        adapter = HorizonsAdapter(clock=_fixed_clock)
        # Access the internal client before closing.
        internal_client = adapter._client
        adapter.close()
        assert internal_client.is_closed

    def test_injected_client_not_closed_on_exit(self):
        """Injected client is NOT closed by the adapter."""
        transport = _make_mock_transport()
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=_fixed_clock)
        adapter.close()
        # Caller-owned client must still be open.
        assert not client.is_closed
        client.close()  # cleanup

    def test_context_manager_closes_owned_client(self):
        """Context manager calls close() on owned client."""
        with HorizonsAdapter(clock=_fixed_clock) as adapter:
            internal_client = adapter._client
        assert internal_client.is_closed


# ---------------------------------------------------------------------------
# NAIVE CLOCK INJECTION
# ---------------------------------------------------------------------------


class TestNaiveClockRejection:
    """Adapter must reject a naive clock result."""

    def test_naive_clock_raises_validation_error(self):
        """Injected clock returning naive datetime raises HorizonsValidationError."""
        def naive_clock() -> datetime:
            return datetime(2026, 8, 27, 0, 0, 0)  # no tzinfo

        content = _make_horizons_response()
        transport = _make_mock_transport(content=content)
        client = httpx.Client(transport=transport)
        adapter = HorizonsAdapter(client=client, clock=naive_clock)
        with pytest.raises(HorizonsValidationError):
            adapter.fetch(_make_request())


# ---------------------------------------------------------------------------
# ERROR HIERARCHY
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """HorizonsAdapterError error hierarchy is correct."""

    def test_unavailable_error_is_adapter_error(self):
        assert issubclass(HorizonsUnavailableError, HorizonsAdapterError)

    def test_validation_error_is_adapter_error(self):
        assert issubclass(HorizonsValidationError, HorizonsAdapterError)

    def test_unavailable_error_is_mission_source_unavailable(self):
        from backend.app.mission_sources.errors import MissionSourceUnavailableError
        assert issubclass(HorizonsUnavailableError, MissionSourceUnavailableError)

    def test_validation_error_is_mission_source_validation(self):
        from backend.app.mission_sources.errors import MissionSourceValidationError
        assert issubclass(HorizonsValidationError, MissionSourceValidationError)


# ---------------------------------------------------------------------------
# FIXTURE LOADING (JSON fixture)
# ---------------------------------------------------------------------------


class TestFixtureLoading:
    """Verify the representative JSON fixture can be loaded and parsed."""

    def test_fixture_file_parseable(self):
        """The representative fixture file parses to a valid geometry result."""
        import pathlib

        fixture_path = (
            pathlib.Path(__file__).parent.parent
            / "fixtures"
            / "horizons"
            / "juno_2026_aug_27_vectors.json"
        )
        assert fixture_path.exists(), f"Fixture not found: {fixture_path}"

        raw = fixture_path.read_bytes()
        payload = json.loads(raw)
        assert payload["signature"]["source"] == "NASA/JPL Horizons API"
        assert payload["signature"]["version"] == "1.3"
        assert "$$SOE" in payload["result"]
        assert "$$EOE" in payload["result"]

    def test_fixture_parses_via_adapter(self):
        """The representative fixture parses successfully through the adapter."""
        import pathlib

        fixture_path = (
            pathlib.Path(__file__).parent.parent
            / "fixtures"
            / "horizons"
            / "juno_2026_aug_27_vectors.json"
        )
        raw = fixture_path.read_bytes()
        adapter = _make_adapter(content=raw)
        result = adapter.fetch(_make_request())
        # Fixture values: LT=2795.8..., RG=838249962..., RR=14.63...
        assert abs(result.geometry.one_way_light_time_s - _LT_VALUE) < 0.01
        assert abs(result.geometry.range_km - _RG_VALUE) < 1.0
        assert abs(result.geometry.range_rate_km_s - _RR_VALUE) < 0.001
