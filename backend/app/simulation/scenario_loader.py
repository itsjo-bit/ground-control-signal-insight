"""Scenario loading — converts JSON scenario files into typed domain objects.

Responsibilities:
    - Load and parse JSON from disk.
    - Validate structure via the Scenario Pydantic model.
    - Enforce the simulated=True requirement.
    - Preserve packet order from the file.
    - Return a typed Scenario without touching global state or telecom metrics.
"""

import json
from pathlib import Path

from pydantic import ValidationError

from ..models.scenario import Scenario


class ScenarioLoader:
    """Loads GCSI scenario files from disk.

    All scenarios in this system must be explicitly marked ``simulated=True``
    to distinguish them from real spacecraft telemetry.  The loader rejects
    any file where that flag is missing or false.
    """

    @staticmethod
    def load(path: str) -> Scenario:
        """Load and validate a scenario JSON file.

        Args:
            path: File-system path to a ``.json`` scenario file.

        Returns:
            A fully validated :class:`~backend.app.models.scenario.Scenario`.

        Raises:
            FileNotFoundError: if the path does not exist.
            ValueError:        if the file is not valid JSON, fails Pydantic
                               validation, or has ``simulated=False``.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Scenario file not found: {path}")

        # --- Parse JSON ---
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Scenario file contains invalid JSON: {path}") from exc

        # --- Validate with Pydantic ---
        try:
            scenario = Scenario.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(
                f"Scenario file failed schema validation: {path}\n{exc}"
            ) from exc

        # --- Require simulated=True ---
        if not scenario.simulated:
            raise ValueError(
                f"Scenario '{scenario.scenario_id}' has simulated=False. "
                "All scenarios in this system must be explicitly marked simulated=True "
                "to distinguish them from real spacecraft telemetry."
            )

        return scenario
