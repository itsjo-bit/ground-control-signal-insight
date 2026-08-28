"""GCSI Phase 6E-C3B — PDS Archive-Label Domain Models.

These models represent the input request and capture output for the
PDS Atmospheres Node archive-label adapter path.

Unlike the Search-API path (pds.py / pds_models.py), this adapter reads
the XML PDS4 label directly from the PDS Atmospheres Node file server.
The science payload (CSV) is NOT fetched — only the XML label.

Models
------
PdsArchiveLabelRequest
    Input contract: one exact LIDVID + its pre-computed label URL.
    label_url must match the trusted archive origin and the LIDVID
    cross-binding rules (IRDR/GRDR path, file basename).

PdsArchiveLabelCapture
    Immutable capture binding the validated PdsScienceProduct and
    ProvenanceRecord to the exact raw XML label bytes.
    Used as the raw-capture contract for archive snapshot creation.

Notes
-----
- These models are frozen and forbid extra fields.
- file_ref in the resulting PdsScienceProduct is DERIVED from the
  label URL directory + label-reported file_name; it is NOT
  source-reported from the XML.
- This adapter does NOT authenticate CSV bytes — only the XML label.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.mission_sources.adapters.pds_models import (
    PdsProductRequest,
    PdsScienceProduct,
)
from backend.app.provenance.models import (
    ProvenanceKind,
    ProvenanceRecord,
    ProvenanceValidationStatus,
)


# ---------------------------------------------------------------------------
# Archive origin constants — NOT configurable by callers
# ---------------------------------------------------------------------------

# Trusted archive origin: exact scheme + host + path prefix.
# Suffix matching is NOT used; only this exact host is trusted.
_ARCHIVE_SCHEME: str = "https"
_ARCHIVE_HOST: str = "pds-atmospheres.nmsu.edu"
_ARCHIVE_PATH_PREFIX: str = "/PDS/data/jnomwr_1100/DATA/"

# Supported sub-directory names (case-sensitive as served).
_ARCHIVE_DATA_DIRS: frozenset[str] = frozenset({"IRDR", "GRDR"})

# Supported PDS information-model version (only this version is accepted).
_SUPPORTED_IM_VERSION: str = "1.7.0.0"

# LIDVID cross-binding regex for MWR calibrated data.
# Groups: (pj, role, timestamp, reccode, localver, pdsver)
# pj        = 2-digit periapsis-juno number
# role      = i (IRDR) or g (GRDR)
# timestamp = YYYYDDDhhmmss (13 digits; DDD = day-of-year)
# reccode   = r<5 digits>
# localver  = v<2 digits>
# pdsver    = version component after ::
_LIDVID_CROSS_RE = re.compile(
    r"^urn:nasa:pds:juno_mwr:data_calibrated:"
    r"mwr([0-9]{2})r([ig])([0-9]{13})_(r[0-9]{5})_(v[0-9]{2})"
    r"::([A-Za-z0-9._-]+)$"
)

# Expected LID prefix for MWR calibrated products.
_MWR_LID_PREFIX: str = "urn:nasa:pds:juno_mwr:data_calibrated:"


# ---------------------------------------------------------------------------
# A. PdsArchiveLabelRequest
# ---------------------------------------------------------------------------


class PdsArchiveLabelRequest(BaseModel):
    """Input contract for one exact archive-label fetch.

    Fields
    ------
    lidvid : str
        A fully-versioned PDS4 LIDVID of the MWR calibrated data product form::

            urn:nasa:pds:juno_mwr:data_calibrated:mwr<PJ>r<role><timestamp>_<reccode>_<localver>::<pdsver>

        Examples::

            urn:nasa:pds:juno_mwr:data_calibrated:mwr62ri2024166030000_r04112_v04::1.0

    label_url : str
        Direct HTTPS URL to the XML label file on the PDS Atmospheres Node
        file server.  Must satisfy:

        - Scheme: ``https``
        - Host: exactly ``pds-atmospheres.nmsu.edu`` (no suffix matching)
        - Path prefix: ``/PDS/data/jnomwr_1100/DATA/``
        - Sub-directory: ``IRDR`` or ``GRDR`` (matching the LIDVID role)
        - Path: ``/PDS/data/jnomwr_1100/DATA/<DIR>/<YYYY>/<YYYYDDD>/<basename>.xml``
        - Basename must be the local product token from the LIDVID (case-insensitive)

    Constraints
    -----------
    - LIDVID must match the MWR calibrated cross-binding regex.
    - label_url must be from the trusted archive origin.
    - label_url directory must match the LIDVID-derived IRDR/GRDR, year, and day-dir.
    - label_url basename must match the LIDVID local product token (case-insensitive).
    - Extra fields are forbidden.
    - The model is frozen after construction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lidvid: str = Field(
        description=(
            "Exact versioned LIDVID for a MWR calibrated product. "
            "Must match the MWR cross-binding regex pattern."
        )
    )
    label_url: str = Field(
        description=(
            "Direct HTTPS URL to the XML label on pds-atmospheres.nmsu.edu. "
            "Must be under /PDS/data/jnomwr_1100/DATA/ with correct IRDR/GRDR "
            "year/day sub-directory and matching basename."
        )
    )

    @field_validator("lidvid", mode="after")
    @classmethod
    def _validate_lidvid(cls, v: str) -> str:
        """Validate LIDVID against MWR calibrated cross-binding pattern."""
        if not _LIDVID_CROSS_RE.match(v):
            raise ValueError(
                "lidvid does not match the expected MWR calibrated LIDVID pattern. "
                "Expected: urn:nasa:pds:juno_mwr:data_calibrated:mwr<PJ>r<role>"
                "<YYYYDDDhhmmss>_<reccode>_<localver>::<pdsver>"
            )
        return v

    @field_validator("label_url", mode="after")
    @classmethod
    def _validate_label_url_scheme_host(cls, v: str) -> str:
        """Validate label_url scheme and host (trusted origin pre-check)."""
        try:
            parsed = urlparse(v)
        except Exception as exc:
            raise ValueError("label_url could not be parsed as a URL.") from exc

        if parsed.scheme != _ARCHIVE_SCHEME:
            raise ValueError(
                f"label_url must use HTTPS scheme; got {parsed.scheme!r}."
            )
        if parsed.netloc != _ARCHIVE_HOST:
            raise ValueError(
                "label_url must point to the trusted PDS Atmospheres Node host. "
                "Only pds-atmospheres.nmsu.edu is accepted."
            )
        if not parsed.path.startswith(_ARCHIVE_PATH_PREFIX):
            raise ValueError(
                "label_url path must begin with "
                f"{_ARCHIVE_PATH_PREFIX!r}."
            )
        if not parsed.path.lower().endswith(".xml"):
            raise ValueError(
                "label_url must point to an XML file (must end with .xml)."
            )
        return v

    @model_validator(mode="after")
    def _cross_validate(self) -> "PdsArchiveLabelRequest":
        """Cross-validate label_url against LIDVID-derived expected path."""
        m = _LIDVID_CROSS_RE.match(self.lidvid)
        if m is None:
            # Already rejected by field validator; this should not happen.
            raise ValueError("LIDVID failed cross-binding regex (unexpected).")

        _pj, role, timestamp, reccode, localver, _pdsver = m.groups()

        # Determine expected sub-directory from role.
        # role 'i' → IRDR, role 'g' → GRDR
        expected_dir = "IRDR" if role == "i" else "GRDR"

        # Extract year and day-of-year from timestamp: YYYYDDDhhmmss
        # timestamp is exactly 13 digits: YYYY (4) + DDD (3) + hhmmss (6)
        year = timestamp[:4]     # YYYY
        yday = timestamp[4:7]    # DDD (day-of-year, zero-padded to 3 digits)
        day_dir = year + yday    # YYYYDDD

        # Build expected local product token from LIDVID components.
        # Format: mwr<PJ>r<role><timestamp>_<reccode>_<localver>
        _pj_str = _pj
        local_token = f"mwr{_pj_str}r{role}{timestamp}_{reccode}_{localver}"

        # Validate label_url path structure.
        parsed = urlparse(self.label_url)
        path = parsed.path  # e.g. /PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/...

        # Expected path components: PREFIX / DIR / YYYY / YYYYDDD / basename.xml
        expected_path_prefix = (
            f"{_ARCHIVE_PATH_PREFIX}{expected_dir}/{year}/{day_dir}/"
        )

        # Case-insensitive path prefix check (the URL path casing on the server
        # may vary, but the directory names are uppercase per spec).
        if not path.upper().startswith(expected_path_prefix.upper()):
            raise ValueError(
                "label_url path does not match the expected LIDVID-derived "
                f"archive path. Expected path prefix: {expected_path_prefix!r}."
            )

        # Basename must match local product token (case-insensitive) + .xml
        basename = path.rsplit("/", 1)[-1]
        expected_basename = f"{local_token}.xml"
        if basename.lower() != expected_basename.lower():
            raise ValueError(
                "label_url basename does not match the expected LIDVID-derived "
                f"label filename. Expected: {expected_basename!r} (case-insensitive)."
            )

        return self


# ---------------------------------------------------------------------------
# B. PdsArchiveLabelCapture
# ---------------------------------------------------------------------------


class PdsArchiveLabelCapture(BaseModel):
    """Immutable capture binding a validated PDS archive label to its raw bytes.

    This is the archive-label equivalent of :class:`PdsScienceProductCapture`.
    It bundles:

    - the original :class:`PdsArchiveLabelRequest` that produced this label,
    - the derived :class:`PdsScienceProduct` normalized metadata,
    - the associated :class:`~backend.app.provenance.models.ProvenanceRecord`,
    - the **exact** raw XML label bytes.

    The raw bytes are the authoritative capture evidence.

    Capture invariants
    ------------------
    The model enforces the following self-consistency properties:

    1. ``product.lidvid == request.lidvid``
    2. ``provenance.source_record_id == request.lidvid``
    3. ``provenance.source_uri == request.label_url``
    4. ``provenance.kind == EXTERNAL_AUTHORITATIVE``
    5. ``provenance.validation_status == VALIDATED``
    6. ``provenance.retrieved_at`` is present and timezone-aware.
    7. ``SHA-256(raw_label) == provenance.content_sha256``

    Notes
    -----
    - This model does NOT re-run the XML validator.
      Self-consistency is checked here; full re-derivation happens in the
      archive snapshot store.
    - ``raw_label`` is stored as ``bytes``; ``arbitrary_types_allowed``
      is set ``False`` because ``bytes`` is a native Pydantic type.
    - This adapter authenticates XML label bytes ONLY.  It does NOT
      authenticate CSV science payload bytes.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=False,
    )

    request: PdsArchiveLabelRequest
    product: PdsScienceProduct
    provenance: ProvenanceRecord
    raw_label: bytes

    @model_validator(mode="after")
    def _validate_capture_consistency(self) -> "PdsArchiveLabelCapture":
        """Enforce capture self-consistency invariants."""
        # 1. product LIDVID must match request LIDVID.
        if self.product.lidvid != self.request.lidvid:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                f"product.lidvid ({self.product.lidvid!r}) != "
                f"request.lidvid ({self.request.lidvid!r})."
            )

        # 2. provenance source_record_id must match request LIDVID.
        if self.provenance.source_record_id != self.request.lidvid:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                f"provenance.source_record_id ({self.provenance.source_record_id!r}) "
                f"!= request.lidvid ({self.request.lidvid!r})."
            )

        # 3. provenance source_uri must match request label_url.
        if self.provenance.source_uri != self.request.label_url:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                "provenance.source_uri must equal request.label_url."
            )

        # 4. provenance kind must be EXTERNAL_AUTHORITATIVE.
        if self.provenance.kind != ProvenanceKind.EXTERNAL_AUTHORITATIVE:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                f"provenance.kind must be EXTERNAL_AUTHORITATIVE; "
                f"got {self.provenance.kind!r}."
            )

        # 5. provenance validation_status must be VALIDATED.
        if self.provenance.validation_status != ProvenanceValidationStatus.VALIDATED:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                f"provenance.validation_status must be VALIDATED; "
                f"got {self.provenance.validation_status!r}."
            )

        # 6. provenance retrieved_at must be present and timezone-aware.
        ret = self.provenance.retrieved_at
        if ret is None:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                "provenance.retrieved_at must be present (not None)."
            )
        if ret.tzinfo is None or ret.utcoffset() is None:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                "provenance.retrieved_at must be timezone-aware."
            )

        # 7. SHA-256(raw_label) must equal provenance.content_sha256.
        computed = hashlib.sha256(self.raw_label).hexdigest()
        if self.provenance.content_sha256 is None:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                "provenance.content_sha256 must be present."
            )
        if computed != self.provenance.content_sha256:
            raise ValueError(
                "PdsArchiveLabelCapture invariant violated: "
                "SHA-256(raw_label) does not match provenance.content_sha256."
            )

        return self
