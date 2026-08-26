"""GCSI Plan Integrity — authoritative packet boundary and plan reconstruction.

This module is the single authority for:

1. **Authoritative packet inventory** — deriving the canonical packet set from
   the active scenario.  Uses the same semantics as ``_effective_packets()``:
   ``scenario.packets`` when non-empty, otherwise ``data_products_to_packets()``.

2. **Plan reconstruction** — accepting a client-submitted ``CandidatePlan``
   and rebuilding it with authoritative packet facts from the scenario.

3. **Client intent model** — clients control only:
   - ordered packet IDs (which packets, in which order)
   - operator notes
   The backend is authoritative for ALL packet physical/mission facts.

4. **Typed errors** — ``PlanIntegrityError`` (and reason codes) allow routes to
   handle specific failure modes without string parsing.

5. **Plan fingerprinting** — deterministic SHA-256 hashes over canonical content
   for approval traceability.  These are *integrity fingerprints*, not
   cryptographic signatures.

Trust principle
---------------
    CLIENTS SUBMIT INTENT.
    THE BACKEND RECONSTRUCTS FACTS.
    DETERMINISTIC EVALUATORS AUTHORIZE PHYSICAL CLAIMS.

A client may supply any packet_id ordering and subset.  The backend ignores every
other field on the client's Packet objects.  The reconstructed plan carries
authoritative values for size_bits, criticality, mission_relevance, deadline_s,
retry_cost, delivery_requirement, and packet_type.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import NamedTuple

from ..models.bridge import data_products_to_packets
from ..models.candidate_plan import CandidatePlan
from ..models.packet import Packet


# ---------------------------------------------------------------------------
# Plan source classification
# ---------------------------------------------------------------------------


class PlanSource(str, Enum):
    """Trusted classification of how a plan was produced.

    Used in ``ApprovalTrace.plan_source`` and issued-plan registry entries.
    The backend assigns this classification; client-supplied ``generated_by``
    strings are NEVER used to infer source.
    """

    deterministic_generated = "deterministic_generated"
    """Four deterministic baseline plans from POST /plans/generate."""

    ai_generated = "ai_generated"
    """AI-prioritized plan from POST /agent/recommend."""

    operator_custom = "operator_custom"
    """Operator-reordered plan via POST /approve/custom."""

    legacy_regenerated = "legacy_regenerated"
    """Regenerated from plan_id in the legacy /approve path (no issued plan)."""

    client_intent = "client_intent"
    """Generic client intent — used when source cannot be determined."""


# ---------------------------------------------------------------------------
# Typed error
# ---------------------------------------------------------------------------


class IntegrityReason(str, Enum):
    """Reason codes for PlanIntegrityError."""

    unknown_packet = "UNKNOWN_PACKET"
    """Client supplied a packet_id not in the authoritative scenario inventory."""

    duplicate_packet = "DUPLICATE_PACKET"
    """Client supplied the same packet_id more than once."""

    duplicate_authoritative_id = "DUPLICATE_AUTHORITATIVE_ID"
    """Authoritative scenario inventory itself contains duplicate IDs."""

    scenario_mismatch = "SCENARIO_MISMATCH"
    """Client-supplied scenario_id does not match the active scenario."""

    issued_plan_mismatch = "ISSUED_PLAN_MISMATCH"
    """Submitted plan does not match the registered issued plan."""

    stale_plan = "STALE_PLAN"
    """Plan was issued for a scenario/state that has since been invalidated."""

    plan_id_conflict = "PLAN_ID_CONFLICT"
    """req.plan_id and req.plan.plan_id disagree."""

    fingerprint_mismatch = "FINGERPRINT_MISMATCH"
    """Recomputed canonical fingerprint does not match the stored fingerprint."""


class PlanIntegrityError(ValueError):
    """Raised when a client plan cannot be reconstructed authoritatively.

    Attributes:
        reason:  IntegrityReason code for programmatic handling.
        message: Human-readable description (may be passed to HTTP 422/409 detail).
    """

    def __init__(self, reason: IntegrityReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


# ---------------------------------------------------------------------------
# Authoritative packet inventory
# ---------------------------------------------------------------------------


def get_authoritative_packets(scenario) -> list[Packet]:
    """Return the authoritative packet list for *scenario*.

    Uses the same semantics as the legacy ``_effective_packets()`` helper:

    * If ``scenario.packets`` is non-empty → return those directly.
    * Otherwise → bridge ``scenario.data_products`` via
      ``data_products_to_packets()``.

    Args:
        scenario: Active :class:`~backend.app.models.scenario.Scenario`.

    Returns:
        Ordered list of :class:`Packet` objects.
    """
    if scenario.packets:
        return list(scenario.packets)
    return data_products_to_packets(scenario.data_products)


def build_authoritative_packet_index(scenario) -> dict[str, Packet]:
    """Build a ``{packet_id: Packet}`` lookup from the authoritative inventory.

    Args:
        scenario: Active :class:`~backend.app.models.scenario.Scenario`.

    Returns:
        Mapping from ``packet_id`` to authoritative :class:`Packet`.

    Raises:
        PlanIntegrityError(DUPLICATE_AUTHORITATIVE_ID): if the scenario itself
            contains packets with duplicate IDs.
    """
    packets = get_authoritative_packets(scenario)
    index: dict[str, Packet] = {}
    for pkt in packets:
        if pkt.packet_id in index:
            raise PlanIntegrityError(
                IntegrityReason.duplicate_authoritative_id,
                f"Authoritative scenario inventory contains duplicate packet_id "
                f"'{pkt.packet_id}'.  Scenario data must be corrected before "
                f"any plan can be reconstructed.",
            )
        index[pkt.packet_id] = pkt
    return index


# ---------------------------------------------------------------------------
# Plan reconstruction trace
# ---------------------------------------------------------------------------


class PlanIntegrityTrace(NamedTuple):
    """Lightweight provenance record produced by ``reconstruct_authoritative_plan``.

    Fields
    ------
    reconstructed_plan
        The canonical :class:`CandidatePlan` with authoritative packet facts.
    authoritative_reconstruction
        Always ``True`` — confirms packet facts were rebound from the scenario.
    packet_count
        Number of packets in the reconstructed plan.
    packet_order_sha256
        SHA-256 hex digest of the ordered packet IDs (whitespace-separated).
        Used for order comparison without exposing full plan content.
    canonical_plan_sha256
        SHA-256 hex digest of the full canonical plan content
        (scenario_id, plan_id, source, ordered authoritative Packet fields).
        Used to confirm the exact plan that was evaluated/executed.
    """

    reconstructed_plan: CandidatePlan
    authoritative_reconstruction: bool
    packet_count: int
    packet_order_sha256: str
    canonical_plan_sha256: str


# ---------------------------------------------------------------------------
# Plan reconstruction
# ---------------------------------------------------------------------------


def reconstruct_authoritative_plan(
    client_plan: CandidatePlan,
    scenario,
    *,
    plan_source: PlanSource = PlanSource.client_intent,
) -> PlanIntegrityTrace:
    """Rebuild *client_plan* with authoritative packet facts from *scenario*.

    The only client-controlled inputs consumed are:
    * ``client_plan.packets[i].packet_id`` — which packets, in which order.

    All other Packet fields (size_bits, criticality, mission_relevance,
    deadline_s, retry_cost, delivery_requirement, packet_type) are
    replaced with authoritative scenario values.

    Client-supplied strategy, generated_by, and metadata are NOT preserved.
    The backend assigns all provenance fields.

    Args:
        client_plan:  The client-submitted :class:`CandidatePlan`.
        scenario:     Active :class:`~backend.app.models.scenario.Scenario`.
        plan_source:  Trust classification for this plan (default:
                      ``PlanSource.client_intent``).

    Returns:
        :class:`PlanIntegrityTrace` containing the canonical plan and
        fingerprints.

    Raises:
        PlanIntegrityError(DUPLICATE_AUTHORITATIVE_ID): scenario has duplicate IDs.
        PlanIntegrityError(DUPLICATE_PACKET): client supplied duplicate IDs.
        PlanIntegrityError(UNKNOWN_PACKET): client supplied an unknown packet_id.
    """
    # Build authoritative index (also validates scenario integrity).
    auth_index = build_authoritative_packet_index(scenario)

    # Validate client packet list.
    seen_ids: set[str] = set()
    client_ids: list[str] = []

    for pkt in client_plan.packets:
        pid = pkt.packet_id
        if pid in seen_ids:
            raise PlanIntegrityError(
                IntegrityReason.duplicate_packet,
                f"Client plan contains duplicate packet_id '{pid}'.  "
                f"Each packet may appear at most once.",
            )
        seen_ids.add(pid)

        if pid not in auth_index:
            raise PlanIntegrityError(
                IntegrityReason.unknown_packet,
                f"Packet '{pid}' does not exist in the authoritative scenario "
                f"inventory.  Client plan cannot be reconstructed.",
            )
        client_ids.append(pid)

    # Reconstruct packets with authoritative facts, preserving client order.
    authoritative_packets: list[Packet] = [auth_index[pid] for pid in client_ids]

    # Assign backend-controlled provenance.
    # The client must NOT control strategy, generated_by, or metadata provenance.
    # Only packet IDs/order (already captured in client_ids) are client-controlled.
    reconstructed = CandidatePlan(
        plan_id=client_plan.plan_id,
        strategy=f"backend:{plan_source.value}",
        packets=authoritative_packets,
        generated_by=f"backend:{plan_source.value}",
        metadata={
            "plan_source": plan_source.value,
            "authoritative_reconstruction": True,
        },
    )

    # Compute fingerprints.
    order_sha = _compute_order_hash(client_ids)
    canonical_sha = _compute_canonical_hash(reconstructed, scenario.scenario_id)

    return PlanIntegrityTrace(
        reconstructed_plan=reconstructed,
        authoritative_reconstruction=True,
        packet_count=len(authoritative_packets),
        packet_order_sha256=order_sha,
        canonical_plan_sha256=canonical_sha,
    )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _compute_order_hash(packet_ids: list[str]) -> str:
    """SHA-256 of the space-joined ordered packet IDs.

    This lightweight hash makes order comparison easy without exposing the
    full plan content.  It changes when any ID is added, removed, or reordered.

    Note: these are *integrity fingerprints*, not cryptographic signatures.
    """
    content = " ".join(packet_ids)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _packet_to_canonical_dict(pkt: Packet) -> dict:
    """Convert a Packet to a stable dict for canonical hashing."""
    return {
        "packet_id": pkt.packet_id,
        "packet_type": pkt.packet_type,
        "size_bits": pkt.size_bits,
        "criticality": pkt.criticality,
        "mission_relevance": pkt.mission_relevance,
        "deadline_s": pkt.deadline_s,
        "retry_cost": pkt.retry_cost,
        "delivery_requirement": pkt.delivery_requirement,
    }


def _compute_canonical_hash(plan: CandidatePlan, scenario_id: str) -> str:
    """SHA-256 of the canonical plan JSON (stable serialization).

    Canonical content:
    * scenario_id
    * plan_id
    * plan_source (from metadata, or 'unknown')
    * ordered authoritative Packet objects (all fields)

    Uses sorted keys and no extra whitespace for determinism.

    Note: these are *integrity fingerprints*, not cryptographic signatures.
    """
    canonical: dict = {
        "scenario_id": scenario_id,
        "plan_id": plan.plan_id,
        "plan_source": plan.metadata.get("plan_source", "unknown"),
        "packets": [_packet_to_canonical_dict(p) for p in plan.packets],
    }
    content = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_plan_fingerprint(plan: CandidatePlan, scenario_id: str) -> tuple[str, str]:
    """Compute both fingerprints for a plan.

    The plan's ``metadata["plan_source"]`` MUST be finalized before calling
    this function.  The canonical hash includes ``plan_source``; calling this
    before provenance is set will produce a hash over ``"unknown"`` that will
    NOT match the hash computed after provenance is set.

    Args:
        plan:        Canonical :class:`CandidatePlan` (must have authoritative packets
                     and finalized provenance metadata).
        scenario_id: ID of the active scenario.

    Returns:
        ``(packet_order_sha256, canonical_plan_sha256)`` as hex strings.
    """
    order_sha = _compute_order_hash([p.packet_id for p in plan.packets])
    canonical_sha = _compute_canonical_hash(plan, scenario_id)
    return order_sha, canonical_sha


def canonicalize_issued_plan(
    plan: CandidatePlan,
    scenario_id: str,
    plan_source: "PlanSource",
) -> tuple[CandidatePlan, str, str]:
    """Finalize provenance, compute fingerprints, and return a deep-copy snapshot.

    This is the single authoritative helper for issuing a backend-generated plan.
    It guarantees the invariant:

        stored canonical_plan_sha256
            == SHA-256 of the exact canonical plan snapshot stored in the registry

    Order of operations (required for hash correctness):
        1. Assign trusted plan_source to metadata (BEFORE hashing).
        2. Compute order hash and canonical hash.
        3. Return a deep-copy snapshot of the finalized plan.

    Args:
        plan:        The backend-generated :class:`CandidatePlan`.  Its metadata
                     will have ``plan_source`` set to ``plan_source.value`` before
                     hashing.  The caller's plan object is mutated (plan_source set).
        scenario_id: Active scenario ID.
        plan_source: Trusted :class:`PlanSource` classification.

    Returns:
        Tuple of ``(canonical_snapshot, packet_order_sha256, canonical_plan_sha256)``.
        ``canonical_snapshot`` is a deep copy with finalized provenance.
    """
    # Step 1: Finalize trusted provenance metadata BEFORE hashing.
    plan.metadata["plan_source"] = plan_source.value
    plan.metadata["authoritative_reconstruction"] = True

    # Step 2: Compute fingerprints over the finalized plan.
    order_sha = _compute_order_hash([p.packet_id for p in plan.packets])
    canonical_sha = _compute_canonical_hash(plan, scenario_id)

    # Step 3: Deep-copy into an immutable snapshot for the registry.
    snapshot = plan.model_copy(deep=True)

    return snapshot, order_sha, canonical_sha


def validate_plan_intent(
    client_plan: CandidatePlan,
    scenario,
) -> None:
    """Validate that the client plan's packet IDs are acceptable.

    Does NOT reconstruct; use ``reconstruct_authoritative_plan()`` for that.
    This is a fast pre-check that raises immediately on obvious problems.

    Raises:
        PlanIntegrityError: on duplicate_authoritative_id, duplicate_packet,
                            or unknown_packet.
    """
    auth_index = build_authoritative_packet_index(scenario)
    seen: set[str] = set()
    for pkt in client_plan.packets:
        pid = pkt.packet_id
        if pid in seen:
            raise PlanIntegrityError(
                IntegrityReason.duplicate_packet,
                f"Client plan contains duplicate packet_id '{pid}'.",
            )
        seen.add(pid)
        if pid not in auth_index:
            raise PlanIntegrityError(
                IntegrityReason.unknown_packet,
                f"Packet '{pid}' not in authoritative scenario inventory.",
            )
