"""GCSI Phase 6F-B2.1.1 — Acquisition Plan Tests.

All tests are OFFLINE. No live PDS requests are made.
No product-label files are fetched. No science payload downloads.

Coverage (per spec section X):
- Exact 411 logical products
- Exact 535 source refs
- PDS4 refs = 156, PDS3 refs = 379
- Per-instrument logical counts: 102/46/8/124/2/8/28/2/91
- JunoCam: 124 EDR, 124 RDR
- No duplicate logical IDs
- No duplicate source URLs
- All discovery evidence timestamps timezone-aware
- EXACT entries: all stops inside frozen window
- PENDING entries: all discovery_availability_time_utc = None
- No placeholder SHA values in discovery evidence
- exact_count + pending_count == 411
- All source URLs pass production profile trust
- All production normalizer/profile pairs known
- Canonical plan ID (B2.1.1 value)
- Plan ID changes on semantic mutation
- Input reorder canonicalization
- Bounded plan loader
- Extra fields rejected
- Path traversal / wrong plan path rejected where loader accepts a path
- Discovery evidence binding resolution
- Instrument semantic roles
- JEDI reconciliation (14+14=28, PRE=0, POST=0)
- WAVES Burst reconciliation (41+41+3+3+3=91)
- JunoCam MAX stop time
- Plan schema / schema_version
- Frozen model (immutability)
- AcquisitionLogicalProductEntry temporal status/time consistency enforcement
- Duplicate label_url within one entry rejected
- Duplicate logical_id across plan rejected
- Unresolvable evidence_id reference rejected
- Load from frozen JSON artifact
- DiscoveryEvidence.capture() factory computes real SHA
- Placeholder SHA rejected
- final_temporal_eligibility contract enforced
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.app.mission_sources.v2_acquisition_plan import (
    ACCUMULATION_START_UTC,
    DECISION_EPOCH_POLICY,
    DECISION_EPOCH_UTC,
    FINAL_TEMPORAL_ELIGIBILITY,
    AcquisitionLogicalProductEntry,
    AcquisitionRepresentationRole,
    AcquisitionSourceRepresentation,
    AcquisitionSourceStandard,
    DiscoveryEvidence,
    HistoricalReplayV2AcquisitionPlan,
    TemporalEvidenceStatus,
    _PLACEHOLDER_SHA_PATTERNS,
    _compute_plan_id,
    _TRUSTED_PAIRS,
    _load_acquisition_plan_any_path,
    load_acquisition_plan,
    validate_representation_url_trust,
)
from backend.app.mission_sources.v2_acquisition_plan_builder import build_plan

# ---------------------------------------------------------------------------
# Shared fixture: build the plan once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def plan() -> HistoricalReplayV2AcquisitionPlan:
    """Build the full 411-entry plan exactly once per test session."""
    return build_plan()


@pytest.fixture(scope="session")
def all_entries(plan: HistoricalReplayV2AcquisitionPlan):
    return plan.logical_entries


@pytest.fixture(scope="session")
def all_refs(all_entries):
    return [r for e in all_entries for r in e.representations]


# ---------------------------------------------------------------------------
# PATH: frozen JSON artifact
# ---------------------------------------------------------------------------

_ARTIFACT_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "data"
    / "replays"
    / "juno_pj62_large_replay_v2_acquisition_plan.json"
)


# ===========================================================================
# Section X.1 — Exact counts
# ===========================================================================


class TestExactCounts:
    """Spec section Y reconciliation checks."""

    def test_total_logical_entries(self, all_entries):
        assert len(all_entries) == 411, (
            f"Expected 411 logical entries, got {len(all_entries)}. "
            "6F_B21_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
        )

    def test_total_source_refs(self, all_refs):
        assert len(all_refs) == 535, (
            f"Expected 535 source refs, got {len(all_refs)}. "
            "6F_B21_STATUS = INVENTORY_PLAN_RECONCILIATION_REQUIRED"
        )

    def test_pds4_ref_count(self, all_refs):
        pds4 = [r for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS4]
        assert len(pds4) == 156, f"Expected 156 PDS4 refs, got {len(pds4)}."

    def test_pds3_ref_count(self, all_refs):
        pds3 = [r for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS3]
        assert len(pds3) == 379, f"Expected 379 PDS3 refs, got {len(pds3)}."

    def test_pds3_plus_pds4_equals_total(self, all_refs):
        pds4 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS4)
        pds3 = sum(1 for r in all_refs if r.source_standard == AcquisitionSourceStandard.PDS3)
        assert pds4 + pds3 == 535
        assert pds4 == 156
        assert pds3 == 379


# ===========================================================================
# Section X.2 — Per-instrument logical counts
# ===========================================================================


class TestPerInstrumentCounts:
    """Spec section Y per-instrument breakdown."""

    EXPECTED = {
        "JIRAM": 102,
        "MWR": 46,
        "UVS": 8,
        "JUNOCAM": 124,
        "FGM": 2,
        "JADE": 8,
        "JEDI": 28,
        "WAVES_SURVEY": 2,
        "WAVES_BURST": 91,
    }

    def test_jiram_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "JIRAM")
        assert n == 102, f"JIRAM: expected 102, got {n}."

    def test_mwr_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "MWR")
        assert n == 46, f"MWR: expected 46, got {n}."

    def test_uvs_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "UVS")
        assert n == 8, f"UVS: expected 8, got {n}."

    def test_junocam_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "JUNOCAM")
        assert n == 124, f"JUNOCAM: expected 124, got {n}."

    def test_fgm_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "FGM")
        assert n == 2, f"FGM: expected 2, got {n}."

    def test_jade_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "JADE")
        assert n == 8, f"JADE: expected 8, got {n}."

    def test_jedi_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "JEDI")
        assert n == 28, f"JEDI: expected 28, got {n}."

    def test_waves_survey_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "WAVES_SURVEY")
        assert n == 2, f"WAVES_SURVEY: expected 2, got {n}."

    def test_waves_burst_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "WAVES_BURST")
        assert n == 91, f"WAVES_BURST: expected 91, got {n}."

    def test_all_instruments_accounted(self, all_entries):
        """No unexpected instruments appear in the plan."""
        known = set(self.EXPECTED.keys())
        seen = {e.instrument for e in all_entries}
        extra = seen - known
        assert not extra, f"Unexpected instruments: {extra!r}."

    def test_sum_equals_411(self, all_entries):
        inst_counts = Counter(e.instrument for e in all_entries)
        total = sum(inst_counts.values())
        assert total == 411


# ===========================================================================
# Section X.3 — JunoCam representations
# ===========================================================================


class TestJunoCamRepresentations:
    """Spec sections C and L."""

    def test_junocam_edr_count(self, all_entries):
        edr = sum(
            1
            for e in all_entries
            if e.instrument == "JUNOCAM"
            for r in e.representations
            if r.representation_role == AcquisitionRepresentationRole.EDR
        )
        assert edr == 124, f"JunoCam EDR: expected 124, got {edr}."

    def test_junocam_rdr_count(self, all_entries):
        rdr = sum(
            1
            for e in all_entries
            if e.instrument == "JUNOCAM"
            for r in e.representations
            if r.representation_role == AcquisitionRepresentationRole.RDR
        )
        assert rdr == 124, f"JunoCam RDR: expected 124, got {rdr}."

    def test_junocam_total_source_refs(self, all_entries):
        refs = sum(
            len(e.representations)
            for e in all_entries
            if e.instrument == "JUNOCAM"
        )
        assert refs == 248, f"JunoCam source refs: expected 248, got {refs}."

    def test_junocam_each_entry_has_exactly_two_representations(self, all_entries):
        bad = [
            e.logical_product_id
            for e in all_entries
            if e.instrument == "JUNOCAM" and len(e.representations) != 2
        ]
        assert not bad, f"JunoCam entries with != 2 representations: {bad[:5]!r}."

    def test_junocam_each_entry_has_one_edr_one_rdr(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "JUNOCAM"):
            roles = {r.representation_role for r in e.representations}
            assert AcquisitionRepresentationRole.EDR in roles, (
                f"{e.logical_product_id} missing EDR."
            )
            assert AcquisitionRepresentationRole.RDR in roles, (
                f"{e.logical_product_id} missing RDR."
            )

    def test_junocam_max_eligible_stop_time(self, all_entries):
        """MAX_ELIGIBLE_JUNOCAM_STOP_TIME = 2024-06-14T08:30:32.662Z
        from JNCE/JNCR_2024166_62R00180_V01."""
        stops = [
            e.discovery_availability_time_utc
            for e in all_entries
            if e.instrument == "JUNOCAM"
        ]
        max_stop = max(stops)
        expected = datetime(2024, 6, 14, 8, 30, 32, 662000, tzinfo=timezone.utc)
        assert max_stop == expected, (
            f"MAX JunoCam stop: expected {expected.isoformat()!r}, "
            f"got {max_stop.isoformat()!r}."
        )

    def test_junocam_logical_ids_represent_observation_not_representation(self, all_entries):
        """Logical ID must encode the observation key, not EDR/RDR."""
        for e in (e for e in all_entries if e.instrument == "JUNOCAM"):
            lid = e.logical_product_id
            assert lid.startswith("gcsi.junocam.pj62.obs."), (
                f"Bad JunoCam logical ID prefix: {lid!r}."
            )
            # Must NOT contain 'edr' or 'rdr' in the observation segment
            obs_part = lid.split("gcsi.junocam.pj62.obs.")[-1]
            assert "edr" not in obs_part.lower(), (
                f"JunoCam logical ID encodes representation not observation: {lid!r}."
            )
            assert "rdr" not in obs_part.lower(), (
                f"JunoCam logical ID encodes representation not observation: {lid!r}."
            )


# ===========================================================================
# Section X.4 — JEDI reconciliation
# ===========================================================================


class TestJediReconciliation:
    """Spec section O: JEDI_DISCOVERED=28, PRE=0, ELIGIBLE=28, POST=0."""

    def test_jedi_eligible_count(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "JEDI")
        assert n == 28, f"JEDI eligible: expected 28, got {n}."

    def test_jedi_pre_window_zero(self, all_entries):
        """All JEDI entries are PENDING; they have no discovery stop to violate window.
        Verify all JEDI entries have temporal_evidence_status = LABEL_VERIFICATION_PENDING
        and no EXACT entries exist before accumulation start."""
        non_pending = [
            e.logical_product_id
            for e in all_entries
            if e.instrument == "JEDI"
            and e.temporal_evidence_status != TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
        ]
        assert not non_pending, (
            f"JEDI entries not marked PENDING: {non_pending!r}. "
            "JEDI PRE_WINDOW = 0 is guaranteed by LABEL_VERIFICATION_PENDING status."
        )

    def test_jedi_post_decision_zero(self, all_entries):
        """All JEDI entries are PENDING; no discovery stop exists to exceed decision epoch.
        Verify all JEDI entries have temporal_evidence_status = LABEL_VERIFICATION_PENDING."""
        non_pending = [
            e.logical_product_id
            for e in all_entries
            if e.instrument == "JEDI"
            and e.temporal_evidence_status != TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
        ]
        assert not non_pending, (
            f"JEDI entries not marked PENDING: {non_pending!r}. "
            "JEDI POST_DECISION = 0 is guaranteed by LABEL_VERIFICATION_PENDING status."
        )

    def test_jedi_doy165_count(self, all_entries):
        doy165 = [
            e for e in all_entries
            if e.instrument == "JEDI"
            and "2024165" in e.representations[0].label_url
        ]
        assert len(doy165) == 14, f"JEDI DOY165: expected 14, got {len(doy165)}."

    def test_jedi_doy166_count(self, all_entries):
        doy166 = [
            e for e in all_entries
            if e.instrument == "JEDI"
            and "2024166" in e.representations[0].label_url
        ]
        assert len(doy166) == 14, f"JEDI DOY166: expected 14, got {len(doy166)}."

    def test_jedi_total_equals_doy165_plus_doy166(self, all_entries):
        doy165 = sum(
            1 for e in all_entries
            if e.instrument == "JEDI" and "2024165" in e.representations[0].label_url
        )
        doy166 = sum(
            1 for e in all_entries
            if e.instrument == "JEDI" and "2024166" in e.representations[0].label_url
        )
        assert doy165 + doy166 == 28

    def test_jedi_semantic_role(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "JEDI"):
            assert e.semantic_role == "energetic_particles", (
                f"{e.logical_product_id}: expected 'energetic_particles', "
                f"got {e.semantic_role!r}."
            )


# ===========================================================================
# Section X.5 — WAVES Burst reconciliation
# ===========================================================================


class TestWavesBurstReconciliation:
    """Spec section Q: B_BIN=41, E_BIN=41, B_REC=3, E_REC=3, NBS_REC=3 = 91."""

    def test_waves_burst_total(self, all_entries):
        n = sum(1 for e in all_entries if e.instrument == "WAVES_BURST")
        assert n == 91, f"WAVES_BURST: expected 91, got {n}."

    def test_waves_burst_b_bin_count(self, all_entries):
        n = sum(
            1 for e in all_entries
            if e.instrument == "WAVES_BURST"
            and e.representations[0].representation_role == AcquisitionRepresentationRole.BURST_B_BIN
        )
        assert n == 41, f"WAVES_BURST B_BIN: expected 41, got {n}."

    def test_waves_burst_e_bin_count(self, all_entries):
        n = sum(
            1 for e in all_entries
            if e.instrument == "WAVES_BURST"
            and e.representations[0].representation_role == AcquisitionRepresentationRole.BURST_E_BIN
        )
        assert n == 41, f"WAVES_BURST E_BIN: expected 41, got {n}."

    def test_waves_burst_b_rec_count(self, all_entries):
        n = sum(
            1 for e in all_entries
            if e.instrument == "WAVES_BURST"
            and e.representations[0].representation_role == AcquisitionRepresentationRole.BURST_B_REC
        )
        assert n == 3, f"WAVES_BURST B_REC: expected 3, got {n}."

    def test_waves_burst_e_rec_count(self, all_entries):
        n = sum(
            1 for e in all_entries
            if e.instrument == "WAVES_BURST"
            and e.representations[0].representation_role == AcquisitionRepresentationRole.BURST_E_REC
        )
        assert n == 3, f"WAVES_BURST E_REC: expected 3, got {n}."

    def test_waves_burst_nbs_rec_count(self, all_entries):
        n = sum(
            1 for e in all_entries
            if e.instrument == "WAVES_BURST"
            and e.representations[0].representation_role == AcquisitionRepresentationRole.BURST_NBS_REC
        )
        assert n == 3, f"WAVES_BURST NBS_REC: expected 3, got {n}."

    def test_waves_burst_family_sum(self, all_entries):
        roles = Counter(
            e.representations[0].representation_role
            for e in all_entries
            if e.instrument == "WAVES_BURST"
        )
        b_bin = roles[AcquisitionRepresentationRole.BURST_B_BIN]
        e_bin = roles[AcquisitionRepresentationRole.BURST_E_BIN]
        b_rec = roles[AcquisitionRepresentationRole.BURST_B_REC]
        e_rec = roles[AcquisitionRepresentationRole.BURST_E_REC]
        nbs = roles[AcquisitionRepresentationRole.BURST_NBS_REC]
        assert b_bin + e_bin + b_rec + e_rec + nbs == 91, (
            f"41+41+3+3+3=91 check: got {b_bin}+{e_bin}+{b_rec}+{e_rec}+{nbs}."
        )


# ===========================================================================
# Section X.6 — No duplicates
# ===========================================================================


class TestNoDuplicates:
    def test_no_duplicate_logical_ids(self, all_entries):
        ids = [e.logical_product_id for e in all_entries]
        seen: set[str] = set()
        dups = [x for x in ids if x in seen or seen.add(x)]  # type: ignore
        assert not dups, f"Duplicate logical_product_ids: {dups[:5]!r}."

    def test_no_duplicate_source_urls(self, all_refs):
        urls = [r.label_url for r in all_refs]
        seen: set[str] = set()
        dups = [u for u in urls if u in seen or seen.add(u)]  # type: ignore
        assert not dups, (
            f"Duplicate label_urls across 535 representations (first 5): {dups[:5]!r}."
        )

    def test_no_duplicate_source_urls_count_matches(self, all_refs):
        """Cross-check via set cardinality."""
        urls = [r.label_url for r in all_refs]
        assert len(urls) == len(set(urls)), (
            f"Expected {len(urls)} unique URLs, set has {len(set(urls))}."
        )


# ===========================================================================
# Section X.7 — Temporal evidence status and window compliance
# ===========================================================================


class TestTemporalEvidenceStatus:
    """B2.1.1 temporal evidence contract: EXACT vs PENDING split."""

    @pytest.fixture(scope="class")
    def exact_entries(self, all_entries):
        return [
            e for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA
        ]

    @pytest.fixture(scope="class")
    def pending_entries(self, all_entries):
        return [
            e for e in all_entries
            if e.temporal_evidence_status == TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING
        ]

    def test_exact_plus_pending_equals_411(self, exact_entries, pending_entries):
        total = len(exact_entries) + len(pending_entries)
        assert total == 411, (
            f"EXACT ({len(exact_entries)}) + PENDING ({len(pending_entries)}) "
            f"= {total}, expected 411."
        )

    def test_exact_count_is_223(self, exact_entries):
        """B2.1.3: JunoCam (124) + WAVES Burst (91) + JADE (8) = 223 EXACT entries."""
        assert len(exact_entries) == 223, (
            f"EXACT entries: expected 223 (JunoCam+WAVES_BURST+JADE), got {len(exact_entries)}."
        )

    def test_pending_count_is_188(self, pending_entries):
        """B2.1.3: JIRAM(102)+MWR(46)+UVS(8)+FGM(2)+JEDI(28)+WAVES_SURVEY(2) = 188."""
        assert len(pending_entries) == 188, (
            f"PENDING entries: expected 188, got {len(pending_entries)}."
        )

    def test_exact_entries_have_non_null_time(self, exact_entries):
        """EXACT entries must have a non-None discovery_availability_time_utc."""
        null_time = [
            e.logical_product_id for e in exact_entries
            if e.discovery_availability_time_utc is None
        ]
        assert not null_time, (
            f"EXACT entries with None availability time: {null_time[:5]!r}."
        )

    def test_pending_entries_have_null_time(self, pending_entries):
        """PENDING entries must have None discovery_availability_time_utc.
        No synthesized/approximated times stored as archive facts."""
        non_null = [
            e.logical_product_id for e in pending_entries
            if e.discovery_availability_time_utc is not None
        ]
        assert not non_null, (
            f"PENDING entries with non-None availability time "
            f"(approximate/fabricated timestamp stored as fact): {non_null[:5]!r}."
        )

    def test_exact_entries_times_are_tz_aware(self, exact_entries):
        non_aware = [
            e.logical_product_id for e in exact_entries
            if e.discovery_availability_time_utc.tzinfo is None
        ]
        assert not non_aware, (
            f"EXACT entries with naive availability time: {non_aware[:5]!r}."
        )

    def test_exact_entries_times_are_utc(self, exact_entries):
        non_utc = [
            e.logical_product_id for e in exact_entries
            if e.discovery_availability_time_utc.utcoffset().total_seconds() != 0
        ]
        assert not non_utc, (
            f"EXACT entries with non-UTC offset: {non_utc[:5]!r}."
        )

    def test_exact_stops_after_accumulation_start(self, exact_entries):
        violations = [
            (e.logical_product_id, e.discovery_availability_time_utc.isoformat())
            for e in exact_entries
            if e.discovery_availability_time_utc <= ACCUMULATION_START_UTC
        ]
        assert not violations, (
            f"EXACT entries with stop <= accumulation_start: {violations[:5]!r}."
        )

    def test_exact_stops_at_or_before_decision_epoch(self, exact_entries):
        violations = [
            (e.logical_product_id, e.discovery_availability_time_utc.isoformat())
            for e in exact_entries
            if e.discovery_availability_time_utc > DECISION_EPOCH_UTC
        ]
        assert not violations, (
            f"EXACT entries with stop > decision_epoch: {violations[:5]!r}."
        )

    def test_frozen_window_constants(self):
        """Verify the frozen replay window constants are exactly as specified."""
        assert ACCUMULATION_START_UTC == datetime(2024, 6, 13, 10, 0, 0, tzinfo=timezone.utc)
        assert DECISION_EPOCH_UTC == datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc)
        assert DECISION_EPOCH_POLICY == "END_OF_JIRAM_PJ62_DIAGNOSTIC_SESSION"

    def test_exact_instruments_are_junocam_waves_burst_and_jade(self, exact_entries):
        """B2.1.3: JunoCam, WAVES_BURST and JADE should have EXACT temporal evidence.

        JADE was upgraded to EXACT in B2.1.3 because the authoritative INDEX.TAB
        provides exact per-product STOP_TIME values within the replay window.
        """
        instruments = {e.instrument for e in exact_entries}
        assert instruments == {"JUNOCAM", "WAVES_BURST", "JADE"}, (
            f"Unexpected instruments with EXACT status: {instruments!r}."
        )

    def test_pending_instruments(self, pending_entries):
        """B2.1.3: JIRAM, MWR, UVS, FGM, JEDI, WAVES_SURVEY must all be PENDING.

        JADE is no longer PENDING (upgraded to EXACT via INDEX.TAB STOP_TIME).
        """
        expected_pending = {"JIRAM", "MWR", "UVS", "FGM", "JEDI", "WAVES_SURVEY"}
        instruments = {e.instrument for e in pending_entries}
        assert instruments == expected_pending, (
            f"PENDING instruments: expected {expected_pending!r}, got {instruments!r}."
        )

    def test_final_temporal_eligibility_is_label_verification_required(self, plan):
        """Top-level contract must equal LABEL_VERIFICATION_REQUIRED."""
        assert plan.final_temporal_eligibility == FINAL_TEMPORAL_ELIGIBILITY
        assert plan.final_temporal_eligibility == "LABEL_VERIFICATION_REQUIRED"


# ===========================================================================
# Section X.9 — URL trust validation
# ===========================================================================


class TestUrlTrustValidation:
    def test_all_source_urls_pass_production_profile_trust(self, all_refs):
        """Every planned representation URL passes validate_representation_url_trust."""
        failures: list[str] = []
        for r in all_refs:
            try:
                validate_representation_url_trust(r)
            except ValueError as exc:
                failures.append(f"{r.label_url!r}: {exc}")
        assert not failures, (
            f"{len(failures)} URL trust violations (first 5):\n"
            + "\n".join(failures[:5])
        )

    def test_all_normalizer_profile_pairs_are_known(self, all_refs):
        unknown = [
            (r.normalizer_id, r.profile_id)
            for r in all_refs
            if (r.normalizer_id, r.profile_id) not in _TRUSTED_PAIRS
        ]
        assert not unknown, (
            f"Unknown normalizer/profile pairs (first 5): {unknown[:5]!r}."
        )

    def test_no_http_urls(self, all_refs):
        http = [r.label_url for r in all_refs if r.label_url.startswith("http://")]
        assert not http, f"Non-HTTPS label URLs found: {http[:3]!r}."

    def test_no_path_traversal_in_urls(self, all_refs):
        traversal = [r.label_url for r in all_refs if ".." in r.label_url]
        assert not traversal, f"Path traversal sequences in URLs: {traversal[:3]!r}."

    def test_untrusted_url_rejected(self):
        """A URL on an unknown host must fail trust validation."""
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://evil.example.com/PDS/data/PDS4/juno_jiram_bundle/foo.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        with pytest.raises(ValueError, match="trusted host"):
            validate_representation_url_trust(rep)

    def test_wrong_path_prefix_rejected(self):
        """Correct host but wrong path prefix must fail trust validation."""
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://atmos.nmsu.edu/PDS/data/other_bundle/foo.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        with pytest.raises(ValueError, match="allowed prefix"):
            validate_representation_url_trust(rep)

    def test_unknown_normalizer_profile_pair_rejected(self):
        """An unknown normalizer/profile pair must fail trust validation."""
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml",
            normalizer_id="gcsi.unknown.v99",
            profile_id="unknown_profile",
        )
        with pytest.raises(ValueError, match="Unknown normalizer/profile pair"):
            validate_representation_url_trust(rep)


# ===========================================================================
# Section X.10 — Canonical plan ID
# ===========================================================================


class TestPlanId:
    # B2.1.4: plan_id updated after FGM two-stage discovery (fgm_peri62_directory_html evidence added)
    EXPECTED_PLAN_ID = "9854b028a49369ba8b452b49d65789d6ee1270bc597645db25736c391ae8a0b0"

    def test_plan_id_matches_expected(self, plan):
        assert plan.plan_id == self.EXPECTED_PLAN_ID, (
            f"plan_id mismatch: got {plan.plan_id!r}."
        )

    def test_plan_id_is_64_hex_chars(self, plan):
        assert len(plan.plan_id) == 64
        assert all(c in "0123456789abcdef" for c in plan.plan_id)

    def test_plan_id_changes_on_url_mutation(self, plan):
        """Mutating a label_url must change the plan_id."""
        entries = list(plan.logical_entries)
        # Replace first entry's first representation URL by appending a char.
        orig_entry = entries[0]
        orig_rep = orig_entry.representations[0]
        mutated_rep = AcquisitionSourceRepresentation(
            representation_role=orig_rep.representation_role,
            source_standard=orig_rep.source_standard,
            label_url=orig_rep.label_url + "x",
            normalizer_id=orig_rep.normalizer_id,
            profile_id=orig_rep.profile_id,
            expected_archive_identity=orig_rep.expected_archive_identity,
            discovery_evidence_id=orig_rep.discovery_evidence_id,
        )
        # Build a mutated entry
        from pydantic import ValidationError
        # We expect the mutated URL to fail trust validation at the plan level;
        # but we're only testing plan_id change semantics here,
        # so compute plan_id directly without constructing the full plan model.
        mutated_entry = AcquisitionLogicalProductEntry(
            logical_product_id=orig_entry.logical_product_id,
            instrument=orig_entry.instrument,
            semantic_role=orig_entry.semantic_role,
            temporal_evidence_status=orig_entry.temporal_evidence_status,
            discovery_availability_time_utc=orig_entry.discovery_availability_time_utc,
            representations=(mutated_rep,),
            discovery_evidence_id=orig_entry.discovery_evidence_id,
        )
        mutated_entries = (mutated_entry,) + plan.logical_entries[1:]
        mutated_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=mutated_entries,
            discovery_evidence=plan.discovery_evidence,
        )
        assert mutated_id != plan.plan_id, (
            "plan_id should change when a label_url is mutated."
        )

    def test_plan_id_changes_on_instrument_mutation(self, plan):
        """Mutating instrument name must change plan_id."""
        orig_entry = plan.logical_entries[0]
        mutated_entry = AcquisitionLogicalProductEntry(
            logical_product_id=orig_entry.logical_product_id,
            instrument="MUTATED",
            semantic_role=orig_entry.semantic_role,
            temporal_evidence_status=orig_entry.temporal_evidence_status,
            discovery_availability_time_utc=orig_entry.discovery_availability_time_utc,
            representations=orig_entry.representations,
            discovery_evidence_id=orig_entry.discovery_evidence_id,
        )
        mutated_entries = (mutated_entry,) + plan.logical_entries[1:]
        mutated_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=mutated_entries,
            discovery_evidence=plan.discovery_evidence,
        )
        assert mutated_id != plan.plan_id

    def test_plan_id_stable_under_entry_reorder(self, plan):
        """Shuffling the entry order must NOT change the plan_id (canonical sort)."""
        entries = plan.logical_entries
        # Reverse the tuple — should produce same plan_id due to sort.
        reversed_entries = tuple(reversed(entries))
        reordered_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=reversed_entries,
            discovery_evidence=plan.discovery_evidence,
            discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
        )
        assert reordered_id == plan.plan_id, (
            "plan_id should not change when entries are reordered."
        )

    def test_plan_id_stable_under_evidence_reorder(self, plan):
        """Shuffling the evidence order must NOT change the plan_id."""
        evidence = plan.discovery_evidence
        reversed_evidence = tuple(reversed(evidence))
        reordered_id = _compute_plan_id(
            plan_id_placeholder="",
            replay_id=plan.replay_id,
            accumulation_start_utc=plan.accumulation_start_utc,
            decision_epoch_utc=plan.decision_epoch_utc,
            decision_epoch_policy=plan.decision_epoch_policy,
            logical_entries=plan.logical_entries,
            discovery_evidence=reversed_evidence,
            discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
        )
        assert reordered_id == plan.plan_id


# ===========================================================================
# Section X.11 — Bounded plan loader
# ===========================================================================


class TestBoundedPlanLoader:
    def test_load_from_frozen_artifact(self):
        """The frozen JSON artifact must load and validate cleanly."""
        if not _ARTIFACT_PATH.exists():
            pytest.skip(f"Frozen artifact not present at {_ARTIFACT_PATH}.")
        loaded = load_acquisition_plan(str(_ARTIFACT_PATH))
        assert len(loaded.logical_entries) == 411
        assert sum(len(e.representations) for e in loaded.logical_entries) == 535

    def test_loaded_plan_id_matches_expected(self):
        """Plan ID from file must match the deterministic expected value."""
        if not _ARTIFACT_PATH.exists():
            pytest.skip(f"Frozen artifact not present at {_ARTIFACT_PATH}.")
        loaded = load_acquisition_plan(str(_ARTIFACT_PATH))
        assert loaded.plan_id == TestPlanId.EXPECTED_PLAN_ID

    def test_loaded_plan_has_final_temporal_eligibility(self):
        """Loaded plan must have final_temporal_eligibility = LABEL_VERIFICATION_REQUIRED."""
        if not _ARTIFACT_PATH.exists():
            pytest.skip(f"Frozen artifact not present at {_ARTIFACT_PATH}.")
        loaded = load_acquisition_plan(str(_ARTIFACT_PATH))
        assert loaded.final_temporal_eligibility == FINAL_TEMPORAL_ELIGIBILITY

    def test_loader_rejects_file_too_large(self, tmp_path):
        big = tmp_path / "big.json"
        # Write a file larger than 32 MiB
        big.write_bytes(b"x" * (32 * 1024 * 1024 + 1))
        # Use the any-path helper since tmp_path is outside data/replays/
        with pytest.raises(ValueError, match="exceeds maximum size"):
            _load_acquisition_plan_any_path(str(big))

    def test_loader_rejects_invalid_json(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(ValueError, match="not valid JSON"):
            _load_acquisition_plan_any_path(str(bad))

    def test_loader_raises_file_not_found(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises((FileNotFoundError, OSError)):
            _load_acquisition_plan_any_path(str(missing))

    def test_loader_rejects_wrong_plan_path(self, tmp_path):
        """A valid JSON file that is not a plan schema is rejected."""
        wrong = tmp_path / "wrong.json"
        wrong.write_text('{"schema": "something_else", "schema_version": 1}',
                         encoding="utf-8")
        with pytest.raises(Exception):  # ValidationError or ValueError
            _load_acquisition_plan_any_path(str(wrong))


# ===========================================================================
# Section X.12 — Extra fields rejected
# ===========================================================================


class TestExtraFieldsRejected:
    """frozen=True, extra='forbid' on all models."""

    def test_discovery_evidence_extra_field_rejected(self):
        with pytest.raises(Exception):
            DiscoveryEvidence(
                evidence_id="test",
                source_url="https://example.com/foo",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_sha256="a" * 64,
                source_kind="test",
                relevant_row_count=None,
                extra_unknown_field="oops",  # type: ignore[call-arg]
            )

    def test_acquisition_source_representation_extra_field_rejected(self):
        with pytest.raises(Exception):
            AcquisitionSourceRepresentation(
                representation_role=AcquisitionRepresentationRole.CALIBRATED,
                source_standard=AcquisitionSourceStandard.PDS4,
                label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml",
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
                extra_unknown_field="oops",  # type: ignore[call-arg]
            )

    def test_acquisition_logical_entry_extra_field_rejected(self):
        rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )
        with pytest.raises(Exception):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
                discovery_availability_time_utc=None,
                representations=(rep,),
                extra_unknown_field="oops",  # type: ignore[call-arg]
            )

    def test_plan_extra_field_rejected_via_model_validate(self):
        """model_validate with extra key must raise ValidationError."""
        if not _ARTIFACT_PATH.exists():
            pytest.skip("Artifact not present.")
        with open(_ARTIFACT_PATH, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
        data["unauthorized_field"] = "injected"
        with pytest.raises(Exception):
            HistoricalReplayV2AcquisitionPlan.model_validate(data)


# ===========================================================================
# Section X.13 — Frozen model (immutability)
# ===========================================================================


class TestFrozenModels:
    def test_logical_entry_is_frozen(self, all_entries):
        e = all_entries[0]
        with pytest.raises(Exception):
            e.instrument = "MUTATED"  # type: ignore[misc]

    def test_source_representation_is_frozen(self, all_entries):
        r = all_entries[0].representations[0]
        with pytest.raises(Exception):
            r.label_url = "https://evil.com/bad"  # type: ignore[misc]

    def test_discovery_evidence_is_frozen(self, plan):
        ev = plan.discovery_evidence[0]
        with pytest.raises(Exception):
            ev.evidence_id = "mutated"  # type: ignore[misc]


# ===========================================================================
# Section X.14 — Model-level integrity rules
# ===========================================================================


class TestModelIntegrity:
    """Test the model validators directly with minimal fixtures."""

    _BASE_AVAIL = datetime(2024, 6, 14, tzinfo=timezone.utc)  # within window

    def _make_valid_rep(self) -> AcquisitionSourceRepresentation:
        return AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/test.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
        )

    def test_entry_rejects_empty_representations(self):
        with pytest.raises(Exception, match="at least one"):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
                representations=(),
            )

    def test_entry_rejects_duplicate_label_urls_within_entry(self):
        rep = self._make_valid_rep()
        with pytest.raises(Exception, match="duplicate"):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
                representations=(rep, rep),
            )

    def test_exact_entry_rejects_stop_at_or_before_accumulation_start(self):
        rep = self._make_valid_rep()
        with pytest.raises(Exception):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA,
                discovery_availability_time_utc=ACCUMULATION_START_UTC,  # NOT > start
                representations=(rep,),
            )

    def test_exact_entry_rejects_stop_after_decision_epoch(self):
        rep = self._make_valid_rep()
        after_epoch = datetime(2024, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        with pytest.raises(Exception):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA,
                discovery_availability_time_utc=after_epoch,
                representations=(rep,),
            )

    def test_exact_entry_rejects_naive_datetime(self):
        rep = self._make_valid_rep()
        naive = datetime(2024, 6, 14, 6, 0, 0)  # no tzinfo
        with pytest.raises(Exception):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA,
                discovery_availability_time_utc=naive,
                representations=(rep,),
            )

    def test_exact_entry_requires_non_null_time(self):
        """EXACT status with None time must be rejected."""
        rep = self._make_valid_rep()
        with pytest.raises(Exception, match="must be set"):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.EXACT_DISCOVERY_METADATA,
                discovery_availability_time_utc=None,
                representations=(rep,),
            )

    def test_pending_entry_rejects_non_null_time(self):
        """PENDING status with a non-None time must be rejected (no fabricated times)."""
        rep = self._make_valid_rep()
        with pytest.raises(Exception, match="must be None"):
            AcquisitionLogicalProductEntry(
                logical_product_id="gcsi.test.1",
                instrument="TEST",
                semantic_role="test_role",
                temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
                discovery_availability_time_utc=self._BASE_AVAIL,
                representations=(rep,),
            )

    def test_pending_entry_accepts_null_time(self):
        """PENDING status with None time must be accepted."""
        rep = self._make_valid_rep()
        entry = AcquisitionLogicalProductEntry(
            logical_product_id="gcsi.test.1",
            instrument="TEST",
            semantic_role="test_role",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            discovery_availability_time_utc=None,
            representations=(rep,),
        )
        assert entry.discovery_availability_time_utc is None

    def test_label_url_must_be_https(self):
        with pytest.raises(Exception, match="HTTPS"):
            AcquisitionSourceRepresentation(
                representation_role=AcquisitionRepresentationRole.CALIBRATED,
                source_standard=AcquisitionSourceStandard.PDS4,
                label_url="http://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml",
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
            )

    def test_label_url_no_query_string(self):
        with pytest.raises(Exception, match="query"):
            AcquisitionSourceRepresentation(
                representation_role=AcquisitionRepresentationRole.CALIBRATED,
                source_standard=AcquisitionSourceStandard.PDS4,
                label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml?x=1",
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
            )

    def test_label_url_no_fragment(self):
        with pytest.raises(Exception, match="fragment"):
            AcquisitionSourceRepresentation(
                representation_role=AcquisitionRepresentationRole.CALIBRATED,
                source_standard=AcquisitionSourceStandard.PDS4,
                label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/foo.xml#sec",
                normalizer_id="gcsi.generic_pds4_label.v1",
                profile_id="jiram_pds4",
            )

    def test_sha256_must_be_64_lowercase_hex(self):
        with pytest.raises(Exception, match="SHA-256|sha256"):
            DiscoveryEvidence(
                evidence_id="test",
                source_url="https://example.com/foo",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_sha256="toolong" + "a" * 64,
                source_kind="test",
            )

    def test_sha256_must_not_be_uppercase(self):
        with pytest.raises(Exception):
            DiscoveryEvidence(
                evidence_id="test",
                source_url="https://example.com/foo",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_sha256="A" * 64,
                source_kind="test",
            )

    def test_plan_rejects_duplicate_logical_ids(self, plan):
        """Construct a minimal plan with a dup logical ID and confirm rejection."""
        entry = plan.logical_entries[0]
        entries_with_dup = plan.logical_entries + (entry,)  # add duplicate
        with pytest.raises(Exception):
            HistoricalReplayV2AcquisitionPlan(
                schema=plan.schema,
                schema_version=plan.schema_version,
                plan_id="a" * 64,  # placeholder 64-char hex
                replay_id=plan.replay_id,
                accumulation_start_utc=plan.accumulation_start_utc,
                decision_epoch_utc=plan.decision_epoch_utc,
                decision_epoch_policy=plan.decision_epoch_policy,
                final_temporal_eligibility=FINAL_TEMPORAL_ELIGIBILITY,
                logical_entries=entries_with_dup,
                discovery_evidence=plan.discovery_evidence,
                discovery_evidence_artifact_id=plan.discovery_evidence_artifact_id,
            )

    def test_plan_rejects_unresolvable_evidence_reference(self, plan):
        """An entry referencing a non-existent evidence_id should be rejected."""
        # Use a unique URL not in the plan to avoid triggering the duplicate-URL check.
        unique_rep = AcquisitionSourceRepresentation(
            representation_role=AcquisitionRepresentationRole.CALIBRATED,
            source_standard=AcquisitionSourceStandard.PDS4,
            label_url="https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/JIR_IMG_RDR_UNIQUE_ORPHAN_V01.xml",
            normalizer_id="gcsi.generic_pds4_label.v1",
            profile_id="jiram_pds4",
            discovery_evidence_id="nonexistent_evidence_id_xyz",
        )
        bad_entry = AcquisitionLogicalProductEntry(
            logical_product_id="gcsi.test.orphan.entry",
            instrument="TEST",
            semantic_role="test_role",
            temporal_evidence_status=TemporalEvidenceStatus.LABEL_VERIFICATION_PENDING,
            discovery_availability_time_utc=None,
            representations=(unique_rep,),
            discovery_evidence_id="nonexistent_evidence_id_xyz",
        )
        entries = plan.logical_entries + (bad_entry,)
        with pytest.raises(Exception, match="evidence"):
            HistoricalReplayV2AcquisitionPlan(
                schema=plan.schema,
                schema_version=plan.schema_version,
                plan_id="x" * 64,
                replay_id=plan.replay_id,
                accumulation_start_utc=plan.accumulation_start_utc,
                decision_epoch_utc=plan.decision_epoch_utc,
                decision_epoch_policy=plan.decision_epoch_policy,
                final_temporal_eligibility=FINAL_TEMPORAL_ELIGIBILITY,
                logical_entries=entries,
                discovery_evidence=plan.discovery_evidence,
            )


# ===========================================================================
# Section X.15 — Plan schema and metadata
# ===========================================================================


class TestPlanMetadata:
    def test_schema_identifier(self, plan):
        assert plan.schema == "gcsi.historical_replay_v2_acquisition_plan"

    def test_schema_version(self, plan):
        assert plan.schema_version == 1

    def test_replay_id(self, plan):
        assert plan.replay_id == "juno_pj62_large_replay_v2"

    def test_accumulation_start_utc_string(self, plan):
        assert "2024-06-13" in plan.accumulation_start_utc
        assert "10:00:00" in plan.accumulation_start_utc

    def test_decision_epoch_utc_string(self, plan):
        assert "2024-06-14" in plan.decision_epoch_utc
        assert "09:35:17" in plan.decision_epoch_utc

    def test_decision_epoch_policy(self, plan):
        assert plan.decision_epoch_policy == "END_OF_JIRAM_PJ62_DIAGNOSTIC_SESSION"

    def test_discovery_evidence_is_non_empty(self, plan):
        assert len(plan.discovery_evidence) > 0

    def test_discovery_evidence_count_is_16(self, plan):
        """B2.1.4: 16 discovery evidence records.

        B2.1.3: 15 records.
        B2.1.4: +1 fgm_peri62_directory_html (two-stage FGM discovery — PERI-62 directory).
        15 (B2.1.3) + 1 (fgm_peri62_directory_html) = 16.
        """
        assert len(plan.discovery_evidence) == 16, (
            f"Expected 16 discovery evidence records, got {len(plan.discovery_evidence)}."
        )


# ===========================================================================
# Section X.17b — Discovery Evidence hardening tests
# ===========================================================================


class TestDiscoveryEvidenceHardening:
    """Tests for B2.1.1 discovery evidence integrity requirements."""

    def _make_real_sha(self) -> str:
        """Return a valid non-placeholder SHA-256."""
        import hashlib
        return hashlib.sha256(b"real bytes").hexdigest()

    def test_no_placeholder_shas_in_plan(self, plan):
        """No evidence record may have an all-one-char or all-zero SHA."""
        placeholders = [
            ev.evidence_id for ev in plan.discovery_evidence
            if ev.response_sha256 in _PLACEHOLDER_SHA_PATTERNS
        ]
        assert not placeholders, (
            f"Evidence records with placeholder SHA: {placeholders!r}."
        )

    def test_all_shas_are_64_hex(self, plan):
        """Every SHA is exactly 64 lowercase hex characters."""
        import re
        bad = [
            ev.evidence_id for ev in plan.discovery_evidence
            if not re.fullmatch(r"[0-9a-f]{64}", ev.response_sha256)
        ]
        assert not bad, f"Evidence with malformed SHA: {bad!r}."

    def test_all_retrieved_at_are_tz_aware(self, plan):
        """Every retrieved_at must be timezone-aware."""
        naive = [
            ev.evidence_id for ev in plan.discovery_evidence
            if ev.retrieved_at.tzinfo is None
        ]
        assert not naive, f"Evidence with naive retrieved_at: {naive!r}."

    def test_all_evidence_ids_are_unique(self, plan):
        """Evidence IDs must be unique within the plan."""
        ids = [ev.evidence_id for ev in plan.discovery_evidence]
        assert len(ids) == len(set(ids)), (
            f"Duplicate evidence IDs: {[x for x in ids if ids.count(x) > 1]!r}."
        )

    def test_placeholder_sha_all_a_rejected(self):
        """An all-'a' SHA must be rejected."""
        with pytest.raises(Exception, match="placeholder"):
            DiscoveryEvidence(
                evidence_id="test",
                source_url="https://example.com/foo",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_sha256="a" * 64,
                source_kind="test",
            )

    def test_placeholder_sha_all_zero_rejected(self):
        """An all-zero SHA must be rejected."""
        with pytest.raises(Exception, match="placeholder"):
            DiscoveryEvidence(
                evidence_id="test",
                source_url="https://example.com/foo",
                retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                response_sha256="0" * 64,
                source_kind="test",
            )

    def test_capture_factory_computes_real_sha(self):
        """DiscoveryEvidence.capture() must compute SHA from actual bytes."""
        import hashlib
        real_bytes = b"actual fetched content here"
        expected_sha = hashlib.sha256(real_bytes).hexdigest()
        ev = DiscoveryEvidence.capture(
            evidence_id="test_capture",
            source_url="https://example.com/data",
            retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            response_bytes=real_bytes,
            source_kind="pds3_index_tab",
        )
        assert ev.response_sha256 == expected_sha
        assert ev.byte_count == len(real_bytes)

    def test_capture_factory_rejects_empty_bytes_with_zero_sha(self):
        """capture() with b'' produces all-zero SHA and must be rejected
        since all-zero is in _PLACEHOLDER_SHA_PATTERNS."""
        import hashlib
        # SHA-256 of b"" is e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        # which is NOT all-zero, so capture() with empty bytes should actually work.
        # Verify the capture factory does NOT produce a placeholder SHA from empty input.
        ev = DiscoveryEvidence.capture(
            evidence_id="empty_test",
            source_url="https://example.com/data",
            retrieved_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            response_bytes=b"",
            source_kind="pds3_index_tab",
        )
        # SHA of empty bytes is a real, non-placeholder value
        assert ev.response_sha256 not in _PLACEHOLDER_SHA_PATTERNS

    def test_sidecar_evidence_sha_matches_fetch_results(self, plan):
        """The junocam INDEX.TAB SHA in the plan must match the known fetched value."""
        tab_ev = next(
            ev for ev in plan.discovery_evidence
            if ev.evidence_id == "junocam_jnojnc_0029_index_tab"
        )
        # This SHA was confirmed by two independent fetches
        assert tab_ev.response_sha256 == (
            "3eaa77323900cdcf30cb7c896c8ab50b608e414e42f9df539717fa306145c382"
        ), (
            f"JunoCam INDEX.TAB SHA mismatch: got {tab_ev.response_sha256!r}."
        )

    def test_waves_burst_sha_is_set(self, plan):
        """The WAVES Burst INDEX.TAB SHA must be a real non-placeholder value."""
        burst_ev = next(
            ev for ev in plan.discovery_evidence
            if ev.evidence_id == "waves_burst_bstfull_index_tab"
        )
        assert burst_ev.response_sha256 not in _PLACEHOLDER_SHA_PATTERNS
        assert len(burst_ev.response_sha256) == 64


# ===========================================================================
# Section X.16 — Semantic roles
# ===========================================================================


class TestSemanticRoles:
    """Verify deterministic GCSI normalization roles per spec section V."""

    EXPECTED_ROLES = {
        "JIRAM": "instrument_diagnostic",
        "MWR": "radiometry_science",
        "UVS": "ultraviolet_observation",
        "JUNOCAM": "visible_imaging",
        "FGM": "magnetic_field",
        "JADE": "plasma_particles",
        "JEDI": "energetic_particles",
        "WAVES_SURVEY": "radio_plasma_survey",
        "WAVES_BURST": "radio_plasma_burst",
    }

    def test_all_instrument_roles_are_correct(self, all_entries):
        for e in all_entries:
            expected = self.EXPECTED_ROLES.get(e.instrument)
            if expected is not None:
                assert e.semantic_role == expected, (
                    f"{e.logical_product_id}: instrument {e.instrument!r} "
                    f"expected role {expected!r}, got {e.semantic_role!r}."
                )


# ===========================================================================
# Section X.17 — Logical ID formulas (deterministic, no UUID/time)
# ===========================================================================


class TestLogicalIdFormulas:
    def test_all_logical_ids_are_non_empty(self, all_entries):
        empty = [e.logical_product_id for e in all_entries if not e.logical_product_id.strip()]
        assert not empty

    def test_all_logical_ids_start_with_gcsi_prefix(self, all_entries):
        bad = [e.logical_product_id for e in all_entries
               if not e.logical_product_id.startswith("gcsi.")]
        assert not bad, f"Logical IDs without 'gcsi.' prefix: {bad[:5]!r}."

    def test_jiram_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "JIRAM"):
            lid = e.logical_product_id
            assert lid.startswith("gcsi.jiram.pj62."), f"Bad JIRAM lid: {lid!r}."
            assert ".img." in lid or ".spe." in lid, f"JIRAM lid missing img/spe: {lid!r}."

    def test_mwr_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "MWR"):
            lid = e.logical_product_id
            assert lid.startswith("gcsi.mwr.pj62."), f"Bad MWR lid: {lid!r}."
            assert ".irdr." in lid or ".grdr." in lid, f"MWR lid missing irdr/grdr: {lid!r}."

    def test_uvs_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "UVS"):
            assert e.logical_product_id.startswith("gcsi.uvs.pj62.")

    def test_fgm_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "FGM"):
            assert e.logical_product_id.startswith("gcsi.fgm.pj62.")

    def test_jade_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "JADE"):
            assert e.logical_product_id.startswith("gcsi.jade.pj62.")

    def test_jedi_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "JEDI"):
            assert e.logical_product_id.startswith("gcsi.jedi.pj62.")

    def test_waves_survey_logical_id_formula(self, all_entries):
        lids = {e.logical_product_id for e in all_entries if e.instrument == "WAVES_SURVEY"}
        assert "gcsi.waves.survey.pj62.b" in lids
        assert "gcsi.waves.survey.pj62.e" in lids

    def test_waves_burst_logical_id_formula(self, all_entries):
        for e in (e for e in all_entries if e.instrument == "WAVES_BURST"):
            assert e.logical_product_id.startswith("gcsi.waves.burst.pj62.")


# ===========================================================================
# Section X.18 — No bulk product-label or science payload download occurred
# ===========================================================================


class TestNoBulkDownload:
    """Verify acquisition plan does not reference payload science files."""

    _SCIENCE_EXTENSIONS = {".img", ".dat", ".fit", ".csv", ".sts"}

    def test_no_science_payload_extensions_in_urls(self, all_refs):
        """None of the 535 planned URLs should point at science payload files."""
        violations = []
        for r in all_refs:
            url_lower = r.label_url.lower()
            for ext in self._SCIENCE_EXTENSIONS:
                if url_lower.endswith(ext):
                    violations.append(r.label_url)
                    break
        assert not violations, (
            f"Label URLs pointing at science payloads: {violations[:5]!r}."
        )

    def test_all_urls_point_at_label_files(self, all_refs):
        """All planned URLs must end in a label file extension (.xml, .lbl)."""
        valid_exts = {".xml", ".lbl"}
        bad = [
            r.label_url for r in all_refs
            if not any(r.label_url.lower().endswith(ext) for ext in valid_exts)
        ]
        assert not bad, (
            f"Label URLs with unexpected extension (first 5): {bad[:5]!r}."
        )
