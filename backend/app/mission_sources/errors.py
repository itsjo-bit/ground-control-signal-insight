"""GCSI Phase 6C — Typed Mission Source Errors.

Small typed exception hierarchy for mission-source provider failures.

Hierarchy
---------
::

    MissionSourceError                 (base)
        |
        +-- MissionSourceUnavailableError
        |       source cannot be accessed (e.g. missing file)
        |
        +-- MissionSourceValidationError
                source exists but cannot produce a valid trusted bundle
                (e.g. invalid scenario JSON, simulated=False, source changed
                 during load, duplicate entity identities)

Design notes
------------
- No HTTP-specific exceptions: this layer is transport-agnostic.
- Do not expose raw file contents in exception messages.
- All classes are non-data: they carry only the message string.
"""

from __future__ import annotations


class MissionSourceError(Exception):
    """Base class for all mission-source provider failures.

    Callers that want to catch any provider error may catch this class.
    Callers that need to distinguish availability from validation failures
    should catch the specific subclass.
    """


class MissionSourceUnavailableError(MissionSourceError):
    """The requested source cannot be accessed.

    Examples
    --------
    - Scenario file path does not exist on disk.
    - Future: remote source unreachable (network error, auth failure).

    Raise this when the source *cannot be found or opened*, before any
    parsing or validation has occurred.
    """


class MissionSourceValidationError(MissionSourceError):
    """The source exists but cannot produce a valid trusted bundle.

    Examples
    --------
    - Scenario JSON is malformed or fails Pydantic validation.
    - Scenario has ``simulated=False``.
    - Source file content changed between the two hash reads
      (source-change race condition detected).
    - Duplicate entity identities make field provenance ambiguous.

    Raise this when the source was found and opened but the resulting
    bundle would be untrustworthy.
    """
