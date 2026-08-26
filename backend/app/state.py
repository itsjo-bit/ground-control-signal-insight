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
from .models.link_state import LinkState
from .models.scenario import Scenario
from .simulation.scenario_loader import ScenarioLoader
from .simulation.scenario_randomizer import randomize_scenario
from .telecom.engine import TelecomEngine

#: The currently active scenario, or None if none has been loaded.
active_scenario: Scenario | None = None

#: The LinkState derived from the active scenario's telecom inputs.
active_link_state: LinkState | None = None

#: The file path that was last passed to load_scenario(), retained so the
#: scenario can be reloaded (reset) without restarting the server process.
active_scenario_path: str | None = None

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

    cfg = config or GCSIConfig()
    scenario = ScenarioLoader.load(path)
    if randomize:
        scenario = randomize_scenario(scenario)
    engine = TelecomEngine(cfg)
    link_state = engine.compute(scenario.link_inputs)

    # Assign all three together so they are always in sync.
    active_scenario_path = path
    active_scenario = scenario
    active_link_state = link_state

    # Invalidate issued plans — plans from the previous scenario are now stale.
    invalidate_issued_plans(reason=f"scenario loaded: {path}")
