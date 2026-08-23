"""Module-level application state store.

Single-process, no database, no session management.
One active scenario per server process at a time.

Usage::

    from backend.app import state

    state.load_scenario("data/scenarios/nominal_pass.json")
    ls = state.active_link_state
    scenario = state.active_scenario
"""

from .config import GCSIConfig
from .models.link_state import LinkState
from .models.scenario import Scenario
from .simulation.scenario_loader import ScenarioLoader
from .telecom.engine import TelecomEngine

#: The currently active scenario, or None if none has been loaded.
active_scenario: Scenario | None = None

#: The LinkState derived from the active scenario's telecom inputs.
active_link_state: LinkState | None = None

#: The file path that was last passed to load_scenario(), retained so the
#: scenario can be reloaded (reset) without restarting the server process.
active_scenario_path: str | None = None


def load_scenario(path: str, config: GCSIConfig | None = None) -> None:
    """Load a scenario from a JSON file and populate module state.

    Calls :class:`ScenarioLoader` to validate the file, then runs
    :class:`TelecomEngine` to derive :class:`LinkState`.  Both
    ``active_scenario`` and ``active_link_state`` are updated atomically.

    Args:
        path:   Path to the scenario JSON file (absolute or relative to CWD).
        config: Optional :class:`GCSIConfig`; defaults to env-configured instance.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError:        if the JSON is invalid or ``simulated != true``.
    """
    global active_scenario, active_link_state, active_scenario_path  # noqa: PLW0603

    cfg = config or GCSIConfig()
    scenario = ScenarioLoader.load(path)
    engine = TelecomEngine(cfg)
    link_state = engine.compute(scenario.link_inputs)

    # Assign all three together so they are always in sync.
    active_scenario_path = path
    active_scenario = scenario
    active_link_state = link_state
