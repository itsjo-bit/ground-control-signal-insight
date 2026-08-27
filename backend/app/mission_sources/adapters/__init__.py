"""GCSI Phase 6D-A — JPL Horizons Source Adapters.

This sub-package contains lower-level source adapters that will eventually
be consumed by a future ``HistoricalReplayProvider`` / ``ReplayAssembler``.

Architecture (conceptual future)::

    HistoricalReplayProvider          [future]
              |
         ReplayAssembler              [future]
          /          \\
 HorizonsGeometry   PDS Products
    Adapter          Adapter [future]
       |
       ↓
validated external facts

These adapters are NOT ``BaseMissionSourceProvider`` subclasses.
They are lower-level boundary components that produce validated external
facts without creating Scenarios or touching runtime state.

Public surface (Phase 6D-A)
---------------------------
- :class:`~backend.app.mission_sources.adapters.horizons.HorizonsAdapter`
- :class:`~backend.app.mission_sources.adapters.horizons_models.HorizonsGeometryRequest`
- :class:`~backend.app.mission_sources.adapters.horizons_models.HorizonsGeometry`
- :class:`~backend.app.mission_sources.adapters.horizons_models.HorizonsGeometryResult`
- :class:`~backend.app.mission_sources.adapters.horizons.HorizonsAdapterError`
- :class:`~backend.app.mission_sources.adapters.horizons.HorizonsUnavailableError`
- :class:`~backend.app.mission_sources.adapters.horizons.HorizonsValidationError`
"""

from .horizons import HorizonsAdapter, HorizonsAdapterError, HorizonsUnavailableError, HorizonsValidationError
from .horizons_models import HorizonsGeometry, HorizonsGeometryRequest, HorizonsGeometryResult

__all__ = [
    # Adapter
    "HorizonsAdapter",
    # Errors
    "HorizonsAdapterError",
    "HorizonsUnavailableError",
    "HorizonsValidationError",
    # Models
    "HorizonsGeometryRequest",
    "HorizonsGeometry",
    "HorizonsGeometryResult",
]
