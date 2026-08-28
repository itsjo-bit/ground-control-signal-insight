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
from urllib.parse import urlsplit

import pydantic
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
        - No userinfo, no non-443 port, empty query, empty fragment
        - Path: exactly ``/PDS/data/jnomwr_1100/DATA/<DIR>/<YYYY>/<YYYYDDD>/<basename>.xml``
        - Basename must be the local product token from the LIDVID (case-insensitive)

    Constraints
    -----------
    - LIDVID must first pass PdsProductRequest baseline validation.
    - LIDVID must match the MWR calibrated cross-binding regex.
    - label_url must be from the trusted archive origin.
    - label_url actual path must exactly equal the LIDVID-derived expected path.
    - label_url must have empty query and empty fragment.
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
            "Must be the exact LIDVID-derived path with no query, fragment, or traversal."
        )
    )

    @field_validator("lidvid", mode="after")
    @classmethod
    def _validate_lidvid(cls, v: str) -> str:
        """Validate LIDVID: first apply PdsProductRequest baseline, then MWR pattern."""
        # PART A: Reuse PdsProductRequest baseline validation contract.
        try:
            PdsProductRequest(lidvid=v)
        except pydantic.ValidationError as exc:
            raise ValueError(
                "lidvid does not satisfy the PdsProductRequest baseline safety rules."
            ) from exc

        # Then independently require the MWR calibrated archive pattern.
        if not _LIDVID_CROSS_RE.match(v):
            raise ValueError(
                "lidvid does not match the expected MWR calibrated LIDVID pattern. "
                "Expected: urn:nasa:pds:juno_mwr:data_calibrated:mwr<PJ>r<role>"
                "<YYYYDDDhhmmss>_<reccode>_<localver>::<pdsver>"
            )
        return v

    @field_validator("label_url", mode="after")
    @classmethod
    def _validate_label_url_structure(cls, v: str) -> str:
        """Validate label_url structure: scheme, host, no userinfo, no query/fragment.

        PART B: Strict URL validation using urlsplit.
        - scheme must be "https"
        - hostname must be exactly "pds-atmospheres.nmsu.edu"
        - username must be None
        - password must be None
        - port must be None or 443
        - query must be empty
        - fragment must be empty
        - path must not contain % or backslash
        - path must end with .xml (case-insensitive)
        """
        # Reject % and backslash in raw URL string before parsing.
        if "%" in v:
            raise ValueError(
                "label_url must not contain percent-encoded characters."
            )
        if "\\" in v:
            raise ValueError(
                "label_url must not contain backslash characters."
            )

        try:
            parsed = urlsplit(v)
        except Exception as exc:
            raise ValueError("label_url could not be parsed as a URL.") from exc

        # Scheme must be https.
        if parsed.scheme != _ARCHIVE_SCHEME:
            raise ValueError(
                f"label_url must use HTTPS scheme; got {parsed.scheme!r}."
            )

        # Hostname must be exactly the trusted archive host (no subdomains, no suffix matching).
        if parsed.hostname != _ARCHIVE_HOST:
            raise ValueError(
                "label_url must point to the trusted PDS Atmospheres Node host. "
                "Only pds-atmospheres.nmsu.edu is accepted."
            )

        # No userinfo.
        if parsed.username is not None:
            raise ValueError(
                "label_url must not contain userinfo (username)."
            )
        if parsed.password is not None:
            raise ValueError(
                "label_url must not contain userinfo (password)."
            )

        # Port must be absent or 443.
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(
                "label_url contains an invalid port specification."
            ) from exc
        if port is not None and port != 443:
            raise ValueError(
                f"label_url port must be absent or 443; got {port!r}."
            )

        # Query must be empty.
        if parsed.query:
            raise ValueError(
                "label_url must not contain a query string."
            )

        # Fragment must be empty.
        if parsed.fragment:
            raise ValueError(
                "label_url must not contain a fragment."
            )

        # Path must begin with the trusted archive prefix.
        path = parsed.path
        if not path.upper().startswith(_ARCHIVE_PATH_PREFIX.upper()):
            raise ValueError(
                "label_url path must begin with "
                f"{_ARCHIVE_PATH_PREFIX!r}."
            )

        # Path must end with .xml.
        if not path.lower().endswith(".xml"):
            raise ValueError(
                "label_url must point to an XML file (must end with .xml)."
            )

        return v

    @model_validator(mode="after")
    def _cross_validate(self) -> "PdsArchiveLabelRequest":
        """Cross-validate label_url against LIDVID-derived EXACT expected path.

        PART B: Exact path equality check.
        Constructs the one exact expected path from LIDVID components and
        compares actual path.casefold() == expected path.casefold().
        No startswith(), no endswith(), no substring matching.
        """
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
        local_token = f"mwr{_pj}r{role}{timestamp}_{reccode}_{localver}"

        # Build the ONE exact expected path.
        # /PDS/data/jnomwr_1100/DATA/<DIR>/<YYYY>/<YYYYDDD>/<local_token>.xml
        expected_path = (
            f"{_ARCHIVE_PATH_PREFIX}{expected_dir}/{year}/{day_dir}/{local_token}.xml"
        )

        # Parse actual URL path (already validated above).
        parsed = urlsplit(self.label_url)
        actual_path = parsed.path

        # EXACT equality check (case-insensitive via casefold).
        # This automatically rejects: ../, extra dirs, duplicate dirs,
        # wrong basename, encoded alternatives, extra segments.
        if actual_path.casefold() != expected_path.casefold():
            raise ValueError(
                "label_url path does not exactly match the expected LIDVID-derived "
                f"archive path. Expected: {expected_path!r} (case-insensitive). "
                f"Got: {actual_path!r}."
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
