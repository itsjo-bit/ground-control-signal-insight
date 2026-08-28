"""Module-level application state store.

Single-process, no database, no session management.
One active scenario per server process at a time.

Usage::

    from backend.app import state

    state.load_scenario("data/scenarios/nominal_pass.json")
    ls = state.active_link_state
    scenario = state.active_scenario

Issued plan registry
--------------------
``issued_plans`` is an in-memory registry of plans the backend has emitted to
the operator (via POST /plans/generate or POST /agent/recommend).  Only
server-generated plans are registered here; client-submitted plans are never
automatically trusted as issued.

The registry is keyed by ``plan_id``.  Each entry stores the canonical plan
(with authoritative packet facts), the fingerprints, and the trusted source
classification.

The registry is cleared when:
  - a new scenario is loaded (``load_scenario``)
  - the scenario is switched (``switch_scenario`` → ``load_scenario``)
  - the scenario is reset (``POST /state/reset``)
  - a state-mutating simulation or approval completes

Read-only operations (GET /state, POST /plans/what-if, POST /simulate/what-if,
POST /plans/evaluate) do NOT clear the registry.

Use ``invalidate_issued_plans(reason)`` to clear in all cases.
"""

from .config import GCSIConfig
from .mission_sources.models import MissionSourceMode, MissionSourceBundle
from .models.link_state import LinkState
from .models.scenario import Scenario
from .provenance.models import ProvenanceManifest
from .simulation.scenario_loader import ScenarioLoader
from .simulation.scenario_randomizer import randomize_scenario
from .telecom.engine import TelecomEngine

#: The currently active scenario, or None if none has been loaded.
active_scenario: Scenario | None = None

#: The LinkState derived from the active scenario's telecom inputs.
active_link_state: LinkState | None = None

#: The file path that was last passed to load_scenario(), retained so the
#: scenario can be reloaded (reset) without restarting the server process.
#: ONLY used for synthetic scenarios loaded via ScenarioLoader.
#: For historical replay this is always None; use active_source_ref instead.
active_scenario_path: str | None = None

# ---------------------------------------------------------------------------
# Phase 6E-C6 — Source metadata globals
# ---------------------------------------------------------------------------

#: Active source mode (synthetic_scenario or historical_replay), or None.
active_source_mode: MissionSourceMode | None = None

#: Opaque source reference for the active source.
#: For synthetic: scenario file path.
#: For historical replay: descriptor path (e.g. "data/replays/...").
active_source_ref: str | None = None

#: Human-readable provider name, or None for synthetic (ScenarioLoader-backed).
active_source_provider_name: str | None = None

#: Source-baseline provenance manifest (historical replay only); None for synthetic.
active_source_provenance: ProvenanceManifest | None = None

# ---------------------------------------------------------------------------
# Issued plan registry
# ---------------------------------------------------------------------------

# Type alias for the issued plan registry entry (avoids a circular import at
# the module level; plan_integrity is imported lazily in load_scenario).
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain.plan_integrity import PlanIntegrityTrace, PlanSource

from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone


@dataclass
class IssuedPlanRecord:
    """One entry in the issued-plan registry.

    Fields
    ------
    plan_id
        The plan identifier (must match the CandidatePlan this record covers).
    scenario_id
        The scenario that was active when this plan was issued.
    canonical_plan
        The authoritative CandidatePlan as issued (packet facts from scenario).
    packet_order_sha256
        SHA-256 hex digest of the ordered packet IDs.
    canonical_plan_sha256
        SHA-256 hex digest of the full canonical plan content.
    plan_source
        Trusted source classification (PlanSource enum value string).
    issued_at
        UTC timestamp when this plan was registered.
    """

    plan_id: str
    scenario_id: str
    canonical_plan: object  # CandidatePlan — typed as object to avoid circular import
    packet_order_sha256: str
    canonical_plan_sha256: str
    plan_source: str
    issued_at: datetime = dc_field(default_factory=lambda: datetime.now(timezone.utc))


#: In-memory issued plan registry.
#: Keys are plan_id strings; values are IssuedPlanRecord instances.
#: Never persisted; cleared on scenario change or state mutation.
issued_plans: dict[str, "IssuedPlanRecord"] = {}

#: The ApprovalTrace from the most recent approved execution.
#: Not persisted; overwritten on each new approval.
last_approval_trace: object | None = None  # ApprovalTrace — typed as object to avoid circular import


def invalidate_issued_plans(reason: str = "unspecified") -> None:
    """Clear the issued-plan registry.

    Args:
        reason: Human-readable reason string (for logging/traceability).

    Call this on:
    - scenario load / switch / reset
    - state-mutating simulation (POST /simulate)
    - approval execution (POST /approve, POST /approve/custom)

    Do NOT call on read-only operations:
    - GET /state, GET /data-products
    - POST /plans/evaluate, POST /plans/what-if, POST /simulate/what-if
    """
    import logging

    logger = logging.getLogger(__name__)
    count = len(issued_plans)
    issued_plans.clear()
    if count:
        logger.debug(
            "Issued plan registry cleared (%d plan(s)) — reason: %s", count, reason
        )


def register_issued_plan(
    plan,  # CandidatePlan
    scenario_id: str,
    packet_order_sha256: str,
    canonical_plan_sha256: str,
    plan_source_value: str,
) -> "IssuedPlanRecord":
    """Add a server-generated plan to the issued-plan registry.

    Only call this for plans the backend has generated and sent to the operator.
    Client-submitted plans must NOT be registered here.

    The registry stores a DEEP COPY of the plan, not the original reference.
    This ensures that subsequent mutations to the original plan object do NOT
    affect the canonical snapshot in the registry, preserving fingerprint integrity.

    Args:
        plan:                   The :class:`CandidatePlan` to register.
        scenario_id:            Active scenario ID at registration time.
        packet_order_sha256:    SHA-256 of ordered packet IDs.
        canonical_plan_sha256:  SHA-256 of canonical plan content.
        plan_source_value:      ``PlanSource`` enum string value.

    Returns:
        The newly created :class:`IssuedPlanRecord`.
    """
    # Deep-copy the plan into the registry so later mutations to the caller's
    # plan object cannot affect what the registry considers "issued".
    canonical_snapshot = plan.model_copy(deep=True)

    record = IssuedPlanRecord(
        plan_id=plan.plan_id,
        scenario_id=scenario_id,
        canonical_plan=canonical_snapshot,
        packet_order_sha256=packet_order_sha256,
        canonical_plan_sha256=canonical_plan_sha256,
        plan_source=plan_source_value,
    )
    issued_plans[plan.plan_id] = record
    return record


# ---------------------------------------------------------------------------
# load_scenario
# ---------------------------------------------------------------------------


def load_scenario(
    path: str,
    config: GCSIConfig | None = None,
    randomize: bool = False,
) -> None:
    """Load a scenario from a JSON file and populate module state.

    Calls :class:`ScenarioLoader` to validate the file, then runs
    :class:`TelecomEngine` to derive :class:`LinkState`.  Both
    ``active_scenario`` and ``active_link_state`` are updated atomically.

    Also invalidates the issued-plan registry because plans from a previous
    scenario must not remain approvable after a scenario change.

    Sets source metadata globals to reflect synthetic mode.
    Historical provenance is cleared so no stale provenance is retained.

    Args:
        path:      Path to the scenario JSON file (absolute or relative to CWD).
        config:    Optional :class:`GCSIConfig`; defaults to env-configured instance.
        randomize: If True, apply bounded random jitter to link/mission values
                   before computing the LinkState.  Used by ``POST /state/reset``
                   so each reset produces a slightly different but realistic
                   scenario.  All existing callers default to False (static
                   behaviour preserved).

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError:        if the JSON is invalid or ``simulated != true``.
    """
    global active_scenario, active_link_state, active_scenario_path  # noqa: PLW0603
    global active_source_mode, active_source_ref  # noqa: PLW0603
    global active_source_provider_name, active_source_provenance  # noqa: PLW0603

    cfg = config or GCSIConfig()
    scenario = ScenarioLoader.load(path)
    if randomize:
        scenario = randomize_scenario(scenario)
    engine = TelecomEngine(cfg)
    link_state = engine.compute(scenario.link_inputs)

    # Assign all globals together so they are always in sync.
    active_scenario_path = path
    active_scenario = scenario
    active_link_state = link_state

    # Phase 6E-C6: source metadata — synthetic mode.
    # Do NOT pretend ScenarioLoader is a BaseMissionSourceProvider.
    # Clear any stale historical provenance atomically with the scenario switch.
    active_source_mode = MissionSourceMode.SYNTHETIC_SCENARIO
    active_source_ref = path
    active_source_provider_name = None
    active_source_provenance = None

    # Invalidate issued plans — plans from the previous scenario are now stale.
    invalidate_issued_plans(reason=f"scenario loaded: {path}")


# ---------------------------------------------------------------------------
# Phase 6E-C6 — Atomic bundle activation
# ---------------------------------------------------------------------------


def activate_mission_source_bundle(
    bundle: MissionSourceBundle,
    config: GCSIConfig | None = None,
) -> None:
    """Atomically activate a :class:`MissionSourceBundle` as the runtime state.

    Responsibilities:

    1. Deep-copy the bundle Scenario so provider output cannot be mutated
       through an external reference after activation.
    2. Compute LinkState from the copied Scenario's link_inputs.
    3. Only after every step succeeds: atomically assign all runtime and
       source metadata globals.
    4. Invalidate issued plans.

    If any step fails, existing runtime state is completely unchanged.

    Args:
        bundle: Fully-loaded :class:`MissionSourceBundle` from a provider.
        config: Optional :class:`GCSIConfig`; defaults to env-configured instance.

    Raises:
        Any exception raised by model_copy, TelecomEngine.compute, etc.
        The caller receives the exception and existing state is untouched.
    """
    global active_scenario, active_link_state, active_scenario_path  # noqa: PLW0603
    global active_source_mode, active_source_ref  # noqa: PLW0603
    global active_source_provider_name, active_source_provenance  # noqa: PLW0603

    # Step 1: Deep-copy the runtime scenario so provider references cannot
    # mutate runtime state from outside.
    runtime_scenario = bundle.scenario.model_copy(deep=True)

    # Step 2: Compute LinkState — may raise on invalid link inputs.
    cfg = config or GCSIConfig()
    engine = TelecomEngine(cfg)
    link_state = engine.compute(runtime_scenario.link_inputs)

    # Step 3: Atomic assignment — all globals updated together.
    # active_scenario_path is None for historical replay (descriptor-backed).
    active_scenario_path = None
    active_scenario = runtime_scenario
    active_link_state = link_state
    active_source_mode = bundle.source_mode
    active_source_ref = bundle.source_ref
    active_source_provider_name = bundle.provider_name
    # ProvenanceManifest is already immutable (frozen=True) — no copy needed.
    active_source_provenance = bundle.provenance

    # Step 4: Invalidate issued plans.
    invalidate_issued_plans(reason=f"mission source bundle activated: {bundle.source_ref}")


# ---------------------------------------------------------------------------
# Phase 6E-C6 — Historical replay loader
# ---------------------------------------------------------------------------


def load_historical_replay(
    source_ref: str,
    config: GCSIConfig | None = None,
) -> None:
    """Load a historical replay bundle and activate it as the runtime state.

    Flow::

        HistoricalReplayProvider().load(source_ref)
            ↓
        MissionSourceBundle
            ↓
        activate_mission_source_bundle()

    No network. No fallback. Does not bypass HistoricalReplayProvider.

    Args:
        source_ref: Repository-relative path to the replay descriptor JSON.
        config:     Optional :class:`GCSIConfig`.

    Raises:
        MissionSourceUnavailableError: Descriptor or snapshot not found.
        MissionSourceValidationError:  Validation failure at any stage.
        Any exception propagated from TelecomEngine.compute.
    """
    from .mission_sources.historical_provider import HistoricalReplayProvider

    provider = HistoricalReplayProvider()
    bundle = provider.load(source_ref)
    activate_mission_source_bundle(bundle, config=config)


# ---------------------------------------------------------------------------
# Phase 6E-C6 — Source-aware reset
# ---------------------------------------------------------------------------


def reset_active_source(config: GCSIConfig | None = None) -> dict:
    """Reset the active source to its baseline state.

    Historical replay:
        Reloads the same descriptor through HistoricalReplayProvider.
        Result is deterministic and non-randomized.
        Returns ``{"source_mode": "historical_replay", "randomized": False}``.

    Synthetic scenario:
        Reloads the same scenario file with randomize=True.
        Preserves existing jitter behaviour.
        Returns ``{"source_mode": "synthetic_scenario", "randomized": True}``.

    Raises:
        RuntimeError: if no source has been loaded yet.
        Any exception from the underlying load call.
    """
    if active_source_mode is None:
        raise RuntimeError("No source has been loaded yet — nothing to reset to.")

    if active_source_mode == MissionSourceMode.HISTORICAL_REPLAY:
        if active_source_ref is None:
            raise RuntimeError("Historical replay active but source_ref is None.")
        load_historical_replay(active_source_ref, config=config)
        return {
            "source_mode": MissionSourceMode.HISTORICAL_REPLAY.value,
            "randomized": False,
        }

    # Synthetic scenario
    if active_scenario_path is None:
        raise RuntimeError("Synthetic scenario active but active_scenario_path is None.")
    load_scenario(active_scenario_path, config=config, randomize=True)
    return {
        "source_mode": MissionSourceMode.SYNTHETIC_SCENARIO.value,
        "randomized": True,
    }


# ---------------------------------------------------------------------------
# Phase 6E-C6 — Source summary
# ---------------------------------------------------------------------------


def get_active_source_summary() -> dict:
    """Return a deterministic summary dict of the active source metadata.

    Reads the existing active source globals — does not create a second
    source-of-truth object.

    Historical example::

        {
            "mode": "historical_replay",
            "provider_name": "GCSI-HistoricalReplayProvider",
            "source_ref": "data/replays/juno_pj62_mwr_v1.json",
            "is_historical_replay": True,
            "provenance_available": True,
            "provenance_scope": "source_baseline",
            "provenance_record_count": 17,
            "provenance_binding_count": <actual>,
            "provenance_kind_counts": {
                "external_authoritative": 3,
                "derived": 13,
                "modeled": 1,
                "synthetic": 0,
            },
        }

    Synthetic example::

        {
            "mode": "synthetic_scenario",
            "provider_name": None,
            "source_ref": "<path>",
            "is_historical_replay": False,
            "provenance_available": False,
            "provenance_scope": None,
            "provenance_record_count": 0,
            "provenance_binding_count": 0,
            "provenance_kind_counts": {},
        }
    """
    mode_value = active_source_mode.value if active_source_mode else None
    is_historical = active_source_mode == MissionSourceMode.HISTORICAL_REPLAY
    prov = active_source_provenance

    if prov is not None:
        from .provenance.models import ProvenanceKind
        kind_counts: dict[str, int] = {}
        for record in prov.records:
            k = record.kind.value
            kind_counts[k] = kind_counts.get(k, 0) + 1
        # Ensure all known kinds appear (even if 0)
        for kind in ProvenanceKind:
            if kind.value not in kind_counts:
                kind_counts[kind.value] = 0
        provenance_available = True
        provenance_scope = "source_baseline"
        provenance_record_count = len(prov.records)
        provenance_binding_count = len(prov.bindings)
    else:
        kind_counts = {}
        provenance_available = False
        provenance_scope = None
        provenance_record_count = 0
        provenance_binding_count = 0

    return {
        "mode": mode_value,
        "provider_name": active_source_provider_name,
        "source_ref": active_source_ref,
        "is_historical_replay": is_historical,
        "provenance_available": provenance_available,
        "provenance_scope": provenance_scope,
        "provenance_record_count": provenance_record_count,
        "provenance_binding_count": provenance_binding_count,
        "provenance_kind_counts": kind_counts,
    }
