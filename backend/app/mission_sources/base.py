"""GCSI Phase 6C — Abstract Mission Source Provider Contract.

Defines the abstract base class that all mission-source providers must
implement.  Modelled after the discipline of GCSI's existing
``BaseAIProvider``, but completely independent of AI, telecom, evaluators,
state, HTTP APIs, or the frontend.

Provider contract
-----------------
Subclasses must implement:

``provider_name`` (property)
    A human-readable, stable string identifier for this provider.

``source_mode`` (property)
    The :class:`MissionSourceMode` enum value for this provider.

``load(source_ref: str) -> MissionSourceBundle``
    Load the source identified by ``source_ref`` and return a fully
    validated :class:`MissionSourceBundle`.

    Implementations are responsible for:
    - Resolving the source ref to actual data.
    - Delegating model validation to the appropriate existing machinery
      (e.g. ``ScenarioLoader`` for synthetic scenarios).
    - Constructing the ``ProvenanceManifest`` sidecar themselves — never
      trusting provenance that comes from the source data.
    - Raising :class:`~backend.app.mission_sources.errors.MissionSourceUnavailableError`
      when the source cannot be found or accessed.
    - Raising :class:`~backend.app.mission_sources.errors.MissionSourceValidationError`
      when the source was found but cannot produce a trustworthy bundle.

The provider boundary must NOT know about:
    - Telecom calculations (TelecomEngine, link budgets, SNR, BER, etc.)
    - Plan evaluators or candidate generators
    - AI providers
    - ``state.py`` or any runtime registry
    - HTTP APIs or API routes
    - The frontend
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import MissionSourceBundle, MissionSourceMode


class BaseMissionSourceProvider(ABC):
    """Abstract contract for all GCSI mission-source providers.

    Subclasses
    ----------
    - :class:`~backend.app.mission_sources.synthetic_provider.SyntheticScenarioProvider`
      (Phase 6C — implemented)
    - ``HistoricalReplayProvider`` (future — NOT implemented in Phase 6C)

    Usage example (Phase 6C)::

        provider = SyntheticScenarioProvider()
        bundle = provider.load("data/scenarios/asteria7_thermal_priority_contact_v1.json")
        scenario = bundle.scenario
        manifest = bundle.provenance

    The provider is intentionally NOT wired into ``state.py`` or any
    API route in Phase 6C.  It remains dormant until a later controlled
    activation phase.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable, stable name for this provider.

        Must not change across Python sessions for the same provider class.
        Used to populate :attr:`MissionSourceBundle.provider_name`.

        Example: ``"GCSI-SyntheticScenarioProvider"``
        """

    @property
    @abstractmethod
    def source_mode(self) -> MissionSourceMode:
        """Top-level source mode for this provider.

        Used to populate :attr:`MissionSourceBundle.source_mode`.
        """

    @abstractmethod
    def load(self, source_ref: str) -> MissionSourceBundle:
        """Load the source identified by *source_ref* and return a bundle.

        Parameters
        ----------
        source_ref:
            An opaque reference string identifying the source.
            For :class:`SyntheticScenarioProvider` this is a file-system
            path to a GCSI scenario JSON file.

            *Treat source_ref as untrusted local input.*
            Do NOT execute it, shell out, or expose its raw value in
            exception messages.

        Returns
        -------
        MissionSourceBundle
            A fully validated bundle containing:
            - the canonical :class:`~backend.app.models.scenario.Scenario`
            - an immutable :class:`~backend.app.provenance.models.ProvenanceManifest`
            - bundle metadata (provider_name, source_mode, source_ref)

        Raises
        ------
        MissionSourceUnavailableError
            The source cannot be found or accessed.

        MissionSourceValidationError
            The source was found but cannot produce a trustworthy bundle.
        """
