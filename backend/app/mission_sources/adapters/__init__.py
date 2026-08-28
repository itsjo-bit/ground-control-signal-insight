"""GCSI Mission Source Adapters.

This sub-package contains lower-level source adapters that will eventually
be consumed by a future ``HistoricalReplayProvider`` / ``ReplayAssembler``.

Architecture (conceptual future)::

    HistoricalReplayProvider          [future]
              |
         ReplayAssembler              [future]
          /          \\
 HorizonsGeometry   PDS Products
    Adapter          Adapter
       |                |
       ↓                ↓
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

Public surface (Phase 6E-A / 6E-D0)
------------------------------------
- :class:`~backend.app.mission_sources.adapters.pds.PdsRegistryAdapter`
- :class:`~backend.app.mission_sources.adapters.pds_models.PdsProductRequest`
- :class:`~backend.app.mission_sources.adapters.pds_models.PdsScienceProduct`
- :class:`~backend.app.mission_sources.adapters.pds_models.PdsScienceProductCapture`
- :class:`~backend.app.mission_sources.adapters.pds.PdsAdapterError`
- :class:`~backend.app.mission_sources.adapters.pds.PdsUnavailableError`
- :class:`~backend.app.mission_sources.adapters.pds.PdsValidationError`
"""

from .horizons import HorizonsAdapter, HorizonsAdapterError, HorizonsUnavailableError, HorizonsValidationError
from .horizons_models import HorizonsGeometry, HorizonsGeometryRequest, HorizonsGeometryResult
from .pds import PdsAdapterError, PdsRegistryAdapter, PdsUnavailableError, PdsValidationError
from .pds_models import PdsDataFile, PdsProductRequest, PdsScienceProduct, PdsScienceProductCapture

__all__ = [
    # Horizons adapter
    "HorizonsAdapter",
    "HorizonsAdapterError",
    "HorizonsUnavailableError",
    "HorizonsValidationError",
    "HorizonsGeometryRequest",
    "HorizonsGeometry",
    "HorizonsGeometryResult",
    # PDS adapter
    "PdsRegistryAdapter",
    "PdsAdapterError",
    "PdsUnavailableError",
    "PdsValidationError",
    # PDS models
    "PdsProductRequest",
    "PdsDataFile",
    "PdsScienceProduct",
    "PdsScienceProductCapture",
]
