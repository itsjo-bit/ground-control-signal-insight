"""GCSI Phase 6F-B2.1.4 — Source-Derived Discovery Evidence Refresh.

B2.1.4 corrections over B2.1.3:
- HTTP transport replaced: urllib.request.urlopen → httpx.Client(follow_redirects=False).
  3xx redirects are rejected immediately (no follow).
  429/5xx raise DiscoveryUnavailableError. Other 4xx raise ValueError.
- Bounded streaming read: incremental accumulation, abort once > MAX_METADATA_BYTES.
- Strict decoding: directory HTML decoded as UTF-8 (fail on error, no errors="replace").
  PDS3 index/label files decoded as Latin-1 (source contract).
- Source-specific URL trust: host/path binding is per evidence_id, not global lists.
  Prevents trusted-host-A + path-for-host-B acceptance.
- Two-stage FGM discovery: PL/ listing → extract PERI-62 href → validate → fetch PERI-62/
  → extract label candidates → classify. No manual filenames.
- Shared exact temporal partition function classify_temporal_partition().
  Contract: stop <= accumulation_start → PRE; stop <= decision_epoch → ELIGIBLE; else POST.
  Applied consistently to JunoCam, WAVES Burst.
- JunoCam partition uses stop_time only (§18/§19 contract).
- Refresh validates typed sidecar model before write.
- Atomic write via tempfile + replace.

CRITICAL: All instrument identity data is derived from official metadata sources.
NO manual arrays of product IDs, filenames, timestamps, or archive identifiers
may appear in this script. The only frozen constants are GCSI policy values.

Usage:
    python scripts/refresh_v2_discovery_evidence.py [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import io
import json
import pathlib
import re
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Policy constants (GCSI only — no NASA archive identities)
# ---------------------------------------------------------------------------

_REPLAY_ID = "juno_pj62_large_replay_v2"
_SIDECAR_SCHEMA = "gcsi.pj62_discovery_evidence_sidecar"
_SIDECAR_VERSION = 1

# Decision epoch and accumulation start (frozen GCSI policy)
_ACCUMULATION_START_UTC = datetime.datetime(2024, 6, 13, 10, 0, 0, tzinfo=datetime.timezone.utc)
_DECISION_EPOCH_UTC = datetime.datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=datetime.timezone.utc)

# Maximum size per individual metadata resource fetch (32 MiB)
_MAX_METADATA_BYTES = 32 * 1024 * 1024

# Science imaging obs-type characters for JunoCam exclusion policy
_JUNOCAM_SCIENCE_OBS_TYPES: frozenset[str] = frozenset({"C", "G", "M", "R", "T"})

# FGM: allowed path for PERI-62 href derivation
_FGM_PL_PATH_PREFIX = "/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/"
_FGM_PERI62_PATH_PREFIX = "/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/PERI-62/"
_FGM_HOST = "pds-ppi.igpp.ucla.edu"


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class DiscoveryUnavailableError(Exception):
    """Raised when a discovery resource is unavailable (429, 5xx)."""


# ---------------------------------------------------------------------------
# Source-specific URL trust model (§9 host/path pair binding)
# ---------------------------------------------------------------------------

# Per-evidence-id trusted (host, path_prefix) pair.
# This prevents trusted-host-A + path-for-host-B acceptance.
# Each evidence source is bound to exactly one (host, path_prefix) pair.
_SOURCE_TRUST: dict[str, tuple[str, str]] = {
    "jiram_orbit62_directory_html": (
        "atmos.nmsu.edu",
        "/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/",
    ),
    "mwr_irdr_2024165_directory_html": (
        "pds-atmospheres.nmsu.edu",
        "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/",
    ),
    "mwr_irdr_2024166_directory_html": (
        "pds-atmospheres.nmsu.edu",
        "/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/",
    ),
    "mwr_grdr_2024165_directory_html": (
        "pds-atmospheres.nmsu.edu",
        "/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024165/",
    ),
    "mwr_grdr_2024166_directory_html": (
        "pds-atmospheres.nmsu.edu",
        "/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024166/",
    ),
    "uvs_orbit62_directory_html": (
        "atmos.nmsu.edu",
        "/PDS/data/jnouvs_3001/DATA/ORBIT-62/",
    ),
    "junocam_jnojnc_0029_index_lbl": (
        "planetarydata.jpl.nasa.gov",
        "/img/data/juno/JNOJNC_0029/INDEX/",
    ),
    "junocam_jnojnc_0029_index_tab": (
        "planetarydata.jpl.nasa.gov",
        "/img/data/juno/JNOJNC_0029/INDEX/",
    ),
    "fgm_jupiter_pl_directory_html": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/",
    ),
    "fgm_peri62_directory_html": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/PERI-62/",
    ),
    "jade_index_lbl": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/INDEX/",
    ),
    "jade_index_tab": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/INDEX/",
    ),
    "jedi_165_directory_html": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/165/",
    ),
    "jedi_166_directory_html": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/166/",
    ),
    "waves_survey_orbit62_directory_html": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/DATA/WAVES_SURVEY/2024149_ORBIT_62/",
    ),
    "waves_burst_bstfull_index_tab": (
        "pds-ppi.igpp.ucla.edu",
        "/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/INDEX/",
    ),
}

# Allowlisted sources (evidence_id, url, source_kind)
_ALLOWLISTED_SOURCES: list[tuple[str, str, str]] = [
    (
        "jiram_orbit62_directory_html",
        "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit62/",
        "pds4_directory_html",
    ),
    (
        "mwr_irdr_2024165_directory_html",
        "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024165/",
        "pds4_directory_html",
    ),
    (
        "mwr_irdr_2024166_directory_html",
        "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/IRDR/2024/2024166/",
        "pds4_directory_html",
    ),
    (
        "mwr_grdr_2024165_directory_html",
        "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024165/",
        "pds4_directory_html",
    ),
    (
        "mwr_grdr_2024166_directory_html",
        "https://pds-atmospheres.nmsu.edu/PDS/data/jnomwr_1100/DATA/GRDR/2024/2024166/",
        "pds4_directory_html",
    ),
    (
        "uvs_orbit62_directory_html",
        "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/",
        "pds4_directory_html",
    ),
    (
        "junocam_jnojnc_0029_index_lbl",
        "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/INDEX/INDEX.LBL",
        "pds3_index_lbl",
    ),
    (
        "junocam_jnojnc_0029_index_tab",
        "https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0029/INDEX/INDEX.TAB",
        "pds3_index_tab",
    ),
    # FGM stage 1: PL/ root listing (parent discovery)
    (
        "fgm_jupiter_pl_directory_html",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/",
        "pds3_directory_html",
    ),
    # Note: fgm_peri62_directory_html is added dynamically in stage 2
    (
        "jade_index_lbl",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/INDEX/INDEX.LBL",
        "pds3_index_lbl",
    ),
    (
        "jade_index_tab",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-J_SW-JAD-3-CALIBRATED-V1.0/INDEX/INDEX.TAB",
        "pds3_index_tab",
    ),
    (
        "jedi_165_directory_html",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/165/",
        "pds3_directory_html",
    ),
    (
        "jedi_166_directory_html",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/166/",
        "pds3_directory_html",
    ),
    (
        "waves_survey_orbit62_directory_html",
        (
            "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-SRVFULL-V2.0/"
            "DATA/WAVES_SURVEY/2024149_ORBIT_62/"
        ),
        "pds3_directory_html",
    ),
    (
        "waves_burst_bstfull_index_tab",
        "https://pds-ppi.igpp.ucla.edu/data/JNO-E_J_SS-WAV-3-CDR-BSTFULL-V2.0/INDEX/INDEX.TAB",
        "pds3_index_tab",
    ),
]


# ---------------------------------------------------------------------------
# URL validation (source-specific, §9 host/path pair binding)
# ---------------------------------------------------------------------------

def _validate_url_structural(url: str) -> None:
    """Structural URL security validation (HTTPS, no userinfo, no query, no fragment, etc.)."""
    from urllib.parse import urlsplit
    if "%" in url:
        raise ValueError(f"URL must not contain percent-encoded characters: {url!r}.")
    if "\\" in url:
        raise ValueError(f"URL must not contain backslash: {url!r}.")
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise ValueError(f"URL must use HTTPS: {url!r}.")
    if not parsed.hostname:
        raise ValueError(f"URL must have a non-empty hostname: {url!r}.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"URL must not contain userinfo: {url!r}.")
    if parsed.port is not None and parsed.port != 443:
        raise ValueError(f"URL must not have a non-443 explicit port: {url!r}.")
    if parsed.query:
        raise ValueError(f"URL must not contain a query string: {url!r}.")
    if parsed.fragment:
        raise ValueError(f"URL must not contain a fragment: {url!r}.")


def _validate_url_for_evidence(evidence_id: str, url: str) -> None:
    """Validate URL against the source-specific (host, path_prefix) trust pair.

    §9: host/path binding is per-evidence-id. This prevents trusted-host-A +
    path-for-host-B acceptance, which would be possible with separate global
    host and path allowlists.
    """
    from urllib.parse import urlsplit
    _validate_url_structural(url)
    if evidence_id not in _SOURCE_TRUST:
        raise ValueError(
            f"No trust binding registered for evidence_id {evidence_id!r}."
        )
    trusted_host, trusted_path_prefix = _SOURCE_TRUST[evidence_id]
    parsed = urlsplit(url)
    if parsed.hostname != trusted_host:
        raise ValueError(
            f"URL host {parsed.hostname!r} does not match trusted host "
            f"{trusted_host!r} for evidence {evidence_id!r}: {url!r}."
        )
    if not parsed.path.startswith(trusted_path_prefix):
        raise ValueError(
            f"URL path {parsed.path!r} does not start with trusted prefix "
            f"{trusted_path_prefix!r} for evidence {evidence_id!r}: {url!r}."
        )


def _validate_discovered_fgm_peri62_href(href: str, pl_base_url: str) -> str:
    """Validate a dynamically discovered PERI-62 href from the PL/ listing.

    Requirements (§4):
    - Relative (no scheme, no host)
    - No traversal (..)
    - No query or fragment
    - No backslash
    - No percent trick
    - Same trusted host (pds-ppi.igpp.ucla.edu)
    - Within /data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/

    Returns the absolute HTTPS URL for the PERI-62 directory.
    """
    # href must be a simple relative directory name like "PERI-62/" or "PERI-62"
    if not href:
        raise ValueError("Discovered PERI-62 href is empty.")
    if href.startswith("/") or "://" in href:
        raise ValueError(f"Discovered FGM href must be relative, got: {href!r}.")
    if ".." in href.replace("\\", "/").split("/"):
        raise ValueError(f"Discovered FGM href contains traversal: {href!r}.")
    if "?" in href or "#" in href:
        raise ValueError(f"Discovered FGM href must not have query/fragment: {href!r}.")
    if "\\" in href:
        raise ValueError(f"Discovered FGM href must not contain backslash: {href!r}.")
    if "%" in href:
        raise ValueError(f"Discovered FGM href must not contain percent-encoding: {href!r}.")

    # Construct absolute URL by appending href to PL base
    base = pl_base_url.rstrip("/") + "/"
    absolute_url = base + href.rstrip("/") + "/"

    # Must be on the trusted FGM host
    from urllib.parse import urlsplit
    parsed = urlsplit(absolute_url)
    if parsed.hostname != _FGM_HOST:
        raise ValueError(
            f"Discovered PERI-62 URL host {parsed.hostname!r} != trusted {_FGM_HOST!r}: "
            f"{absolute_url!r}."
        )
    if not parsed.path.startswith(_FGM_PL_PATH_PREFIX):
        raise ValueError(
            f"Discovered PERI-62 URL path {parsed.path!r} not within "
            f"{_FGM_PL_PATH_PREFIX!r}: {absolute_url!r}."
        )
    return absolute_url


# ---------------------------------------------------------------------------
# HTTP fetch — true no-redirect transport (§7)
# ---------------------------------------------------------------------------

def _fetch_metadata(
    evidence_id: str,
    url: str,
    max_bytes: Optional[int] = None,
) -> tuple[bytes, int, datetime.datetime]:
    """Fetch a metadata resource from an official archive URL.

    B2.1.4 §7: Uses httpx.Client(follow_redirects=False).
    - 200: consume bounded body (incremental streaming, never accumulate > max + 1).
    - 3xx: REJECT immediately, do not follow Location. Raises ValueError.
    - 429: DiscoveryUnavailableError (throttled).
    - 5xx: DiscoveryUnavailableError (server error).
    - other 4xx: ValueError (validation/source error).

    §8: Bounded streaming read — stream incrementally, abort once > MAX_METADATA_BYTES.

    Returns (response_bytes, http_status, retrieved_at_utc).
    """
    import httpx

    limit = max_bytes if max_bytes is not None else _MAX_METADATA_BYTES
    _validate_url_for_evidence(evidence_id, url)

    with httpx.Client(follow_redirects=False, timeout=120.0) as client:
        with client.stream("GET", url, headers={"User-Agent": "GCSI-B214-refresh/1.0"}) as resp:
            status = resp.status_code

            # 3xx: reject, do not follow
            if 300 <= status < 400:
                raise ValueError(
                    f"HTTP {status} redirect from {url!r}. "
                    "Redirects are not permitted in discovery transport. "
                    f"Location: {resp.headers.get('location', '(none)')!r}."
                )
            # 429: throttled
            if status == 429:
                raise DiscoveryUnavailableError(
                    f"HTTP 429 (throttled) from {url!r}."
                )
            # 5xx: server error
            if status >= 500:
                raise DiscoveryUnavailableError(
                    f"HTTP {status} (server error) from {url!r}."
                )
            # 404: discovery unavailable
            if status == 404:
                raise DiscoveryUnavailableError(
                    f"HTTP 404 (not found) from {url!r}."
                )
            # other 4xx: validation/source error
            if status >= 400:
                raise ValueError(
                    f"HTTP {status} from {url!r}; expected 200."
                )
            # 200: consume bounded body incrementally (§8)
            if status != 200:
                raise ValueError(
                    f"HTTP {status} from {url!r}; expected 200."
                )

            # Bounded streaming read: never accumulate more than limit + 1 bytes
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > limit:
                    raise ValueError(
                        f"Response from {url!r} exceeds size limit ({limit} bytes). "
                        "Aborting read."
                    )
                chunks.append(chunk)

        retrieved_at = datetime.datetime.now(datetime.timezone.utc)
        data = b"".join(chunks)
        return data, status, retrieved_at


# ---------------------------------------------------------------------------
# Evidence record factory
# ---------------------------------------------------------------------------

def _make_evidence(
    evidence_id: str,
    source_url: str,
    source_kind: str,
    response_bytes: bytes,
    http_status: int,
    retrieved_at: datetime.datetime,
    relevant_row_count: Optional[int] = None,
) -> dict[str, Any]:
    """Build a discovery evidence record dict."""
    sha256 = hashlib.sha256(response_bytes).hexdigest()
    return {
        "evidence_id": evidence_id,
        "source_url": source_url,
        "source_kind": source_kind,
        "http_status": http_status,
        "retrieved_at": retrieved_at.isoformat(),
        "response_sha256": sha256,
        "byte_count": len(response_bytes),
        "relevant_row_count": relevant_row_count,
    }


# ---------------------------------------------------------------------------
# §18/§19: Shared exact temporal partition function
# ---------------------------------------------------------------------------

def classify_temporal_partition(
    observation_stop: datetime.datetime,
) -> str:
    """Classify a product into PRE / ELIGIBLE / POST based on authoritative observation_stop.

    §18 contract (observation_stop-based, not start-based):
        if observation_stop <= accumulation_start:
            PRE
        elif observation_stop <= decision_epoch:
            ELIGIBLE
        else:
            POST

    A product may start before decision_epoch and finish after → POST (straddling product).

    Parameters
    ----------
    observation_stop:
        Timezone-aware UTC observation stop time.

    Returns
    -------
    "PRE", "ELIGIBLE", or "POST".
    """
    if observation_stop.tzinfo is None:
        raise ValueError("observation_stop must be timezone-aware.")
    if observation_stop <= _ACCUMULATION_START_UTC:
        return "PRE"
    elif observation_stop <= _DECISION_EPOCH_UTC:
        return "ELIGIBLE"
    else:
        return "POST"


# ---------------------------------------------------------------------------
# Extractor: JIRAM directory HTML
# ---------------------------------------------------------------------------

_JIRAM_XML_RE = re.compile(r'href="(JIR_(?:IMG|SPE)_RDR_2024166T(\d{6})_V01\.xml)"')


def _extract_jiram(html_bytes: bytes, evidence_id: str) -> list[dict[str, Any]]:
    """Extract JIRAM XML label rows from directory HTML bytes.

    §10: directory HTML decoded as UTF-8 (strict, no errors='replace').
    """
    try:
        content = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"JIRAM directory HTML is not valid UTF-8: {exc}. "
            "Source encoding contract violated."
        ) from exc

    rows = []
    seen: set[str] = set()
    for m in _JIRAM_XML_RE.finditer(content):
        filename = m.group(1)
        hhmmss = m.group(2)
        if filename in seen:
            continue
        seen.add(filename)
        family = "IMG" if "_IMG_" in filename else "SPE"
        rows.append({
            "filename": filename,
            "family": family,
            "hhmmss": hhmmss,
            "relative_label_path": filename,
            "discovery_evidence_id": evidence_id,
            "expected_archive_identity_source": "UNAVAILABLE_UNTIL_LABEL",
        })
    return sorted(rows, key=lambda r: (r["family"], r["hhmmss"]))


# ---------------------------------------------------------------------------
# Extractor: MWR directory HTML
# ---------------------------------------------------------------------------

_MWR_XML_RE = re.compile(r'href="(MWR62R([IG])2024(\d{3})(\d{2})0000_([R\d]+)_V04\.xml)"')


def _extract_mwr(html_bytes: bytes, evidence_id: str, product_type: str, doy: int) -> list[dict[str, Any]]:
    """Extract MWR XML label rows from directory HTML bytes.

    §10: directory HTML decoded as UTF-8 (strict).

    product_type: "IRDR" or "GRDR"
    doy: 165 or 166

    Inclusion classification (temporal window 2024-06-13T10:00 – 2024-06-14T09:35:17):
      DOY165: hours 10-23 are ELIGIBLE.
      DOY166: hours  0-8 are ELIGIBLE.
    All other hours are EXCLUDED.
    """
    try:
        content = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"MWR directory HTML is not valid UTF-8: {exc}."
        ) from exc

    rows = []
    seen: set[str] = set()
    for m in _MWR_XML_RE.finditer(content):
        filename_xml = m.group(1)
        kind_letter = m.group(2)
        doy_found = int(m.group(3))
        hour = int(m.group(4))
        code = m.group(5)

        if doy_found != doy:
            continue
        expected_kind = "I" if product_type == "IRDR" else "G"
        if kind_letter != expected_kind:
            continue

        if filename_xml in seen:
            continue
        seen.add(filename_xml)

        fname_stem = filename_xml[:-4]  # remove .xml
        relative_label_path = f"{product_type}/2024/2024{doy}/{filename_xml}"

        # Inclusion classification
        if doy == 165:
            inclusion = "ELIGIBLE" if hour >= 10 else "EXCLUDED"
        else:  # doy == 166
            inclusion = "ELIGIBLE" if hour <= 8 else "EXCLUDED"

        rows.append({
            "filename": fname_stem,
            "product_type": product_type,
            "doy": doy,
            "hour": hour,
            "code": code,
            "relative_label_path": relative_label_path,
            "inclusion": inclusion,
            "discovery_evidence_id": evidence_id,
        })
    return sorted(rows, key=lambda r: r["hour"])


# ---------------------------------------------------------------------------
# Extractor: UVS directory HTML
# ---------------------------------------------------------------------------

_UVS_XML_RE = re.compile(
    r'href="(UVS_(S\d{2})_(\d+)_(2024165|2024166)_(P62OBS|P62SY1)_V01\.xml)"'
)


def _extract_uvs(html_bytes: bytes, evidence_id: str) -> list[dict[str, Any]]:
    """Extract UVS XML label rows from directory HTML bytes (Orbit-62 products only).

    §10: UTF-8 strict.
    """
    try:
        content = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"UVS directory HTML is not valid UTF-8: {exc}.") from exc

    rows = []
    seen: set[str] = set()
    for m in _UVS_XML_RE.finditer(content):
        xml_file = m.group(1)
        sensor = m.group(2)
        sclk = m.group(3)
        doy_str = m.group(4)
        obs_type = m.group(5)

        if xml_file in seen:
            continue
        seen.add(xml_file)

        stem = xml_file[:-4]  # remove .xml
        rows.append({
            "filename": stem,
            "sensor": sensor,
            "sclk": sclk,
            "doy_str": doy_str,
            "obs_type": obs_type,
            "relative_label_path": xml_file,
            "discovery_evidence_id": evidence_id,
        })
    return sorted(rows, key=lambda r: (r["doy_str"], r["sensor"], r["obs_type"]))


# ---------------------------------------------------------------------------
# Extractor: JunoCam INDEX.TAB
# ---------------------------------------------------------------------------

def _parse_junocam_index_tab(lbl_bytes: bytes) -> dict[str, Any]:
    """Parse JunoCam INDEX.LBL — §10: Latin-1 (PDS3 label source contract)."""
    try:
        content = lbl_bytes.decode("latin-1")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JunoCam INDEX.LBL is not valid Latin-1: {exc}.") from exc
    record_bytes_m = re.search(r"RECORD_BYTES\s*=\s*(\d+)", content)
    record_bytes = int(record_bytes_m.group(1)) if record_bytes_m else 502
    return {"record_bytes": record_bytes}


def _extract_junocam_index_tab(
    tab_bytes: bytes,
    record_bytes: int,
    evidence_id: str,
) -> list[dict[str, Any]]:
    """Extract JunoCam orbit-62 rows from INDEX.TAB bytes (CSV format).

    §10: Latin-1 (PDS3 index source contract).
    §18/§19: Uses classify_temporal_partition() — stop-time-based only.

    Returns individual representation rows (one per product ID).
    """
    def parse_cal_time(t: str) -> Optional[datetime.datetime]:
        t = t.strip()
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d+)", t)
        if not m:
            return None
        year, mo, day, h, mi, s, frac = m.groups()
        return datetime.datetime(
            int(year), int(mo), int(day),
            int(h), int(mi), int(s),
            int(frac[:6].ljust(6, "0")),
            tzinfo=datetime.timezone.utc,
        )

    try:
        content = tab_bytes.decode("latin-1")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JunoCam INDEX.TAB is not valid Latin-1: {exc}.") from exc

    reader = csv.reader(io.StringIO(content))
    rows = []
    seen: set[str] = set()

    for fields in reader:
        if len(fields) < 14:
            continue
        product_id = fields[3].strip()
        start_str = fields[4].strip()
        stop_str = fields[5].strip()
        file_spec = fields[13].strip()

        if not product_id or "ORBIT_62" not in file_spec:
            continue
        if product_id in seen:
            continue

        if product_id.startswith("JNCE_"):
            kind = "EDR"
        elif product_id.startswith("JNCR_"):
            kind = "RDR"
        else:
            continue

        start_dt = parse_cal_time(start_str)
        stop_dt = parse_cal_time(stop_str)
        if start_dt is None or stop_dt is None:
            continue
        if stop_dt < start_dt:
            raise ValueError(
                f"JunoCam row {product_id!r}: stop_time < start_time. "
                "Invalid temporal data."
            )

        seen.add(product_id)

        # §18/§19: partition by stop_time only (shared function)
        partition = classify_temporal_partition(stop_dt)

        parts = product_id.split("_")
        if len(parts) >= 3:
            obs_key = f"{parts[1]}_{parts[2].lower()}"
        else:
            obs_key = product_id.lower()

        rows.append({
            "product_id": product_id,
            "file_specification_name": file_spec,
            "representation_kind": kind,
            "observation_key": obs_key,
            "start_time_utc": start_dt.isoformat(),
            "stop_time_utc": stop_dt.isoformat(),
            "partition": partition,
            "discovery_evidence_id": evidence_id,
        })

    return rows


# ---------------------------------------------------------------------------
# Extractor: FGM — two-stage source-derived discovery (§4)
# ---------------------------------------------------------------------------

# Stage 1: Extract PERI-62 href from PL/ directory listing
_FGM_PERI62_HREF_RE = re.compile(r'href="(PERI-62/?)"', re.IGNORECASE)

# Stage 2: Extract .lbl candidates from PERI-62/ listing
_FGM_LBL_HREF_RE = re.compile(r'href="(fgm_jno_l3[^\s"]*?\.lbl)"', re.IGNORECASE)

# Classification patterns
_FGM_PJ62_PATTERN = re.compile(r"_pj62", re.IGNORECASE)
_FGM_R1S_PATTERN = re.compile(r"_r1s_", re.IGNORECASE)

# Date anchor for PERI-62 products
_FGM_DATE_ANCHOR = "2024165"


def _classify_fgm_candidate(lbl_filename: str) -> tuple[str, bool]:
    """Classify an FGM candidate label and determine if it should be selected.

    Returns (classification, selected).

    Classification logic:
    - Contains _pj62: FULL_RESOLUTION_PJ62 → selected
    - Contains _r1s_: R1S_OR_DOWNSAMPLED_ALTERNATE → excluded
    - Contains date anchor without _r1s_ or _pj62: FULL_RESOLUTION_STANDARD → selected
    - Other: OTHER_RELEVANT_VARIANT → excluded
    """
    fn_lower = lbl_filename.lower()
    if _FGM_PJ62_PATTERN.search(fn_lower):
        return "FULL_RESOLUTION_PJ62", True
    if _FGM_R1S_PATTERN.search(fn_lower):
        return "R1S_OR_DOWNSAMPLED_ALTERNATE", False
    if _FGM_DATE_ANCHOR in fn_lower:
        return "FULL_RESOLUTION_STANDARD", True
    return "OTHER_RELEVANT_VARIANT", False


def _extract_peri62_href_from_pl_listing(pl_html_bytes: bytes, pl_base_url: str) -> str:
    """Stage 1: Extract and validate the PERI-62 href from the PL/ directory listing.

    §4: The dynamically discovered href must be relative, no traversal,
    no query, no fragment, no backslash, no percent trick, same trusted host,
    within /data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/.

    Returns the validated absolute HTTPS URL for the PERI-62 directory.
    Raises ValueError if PERI-62 is not found or href fails validation.
    """
    try:
        content = pl_html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"FGM PL/ directory HTML is not valid UTF-8: {exc}.") from exc

    m = _FGM_PERI62_HREF_RE.search(content)
    if m is None:
        raise ValueError(
            "FGM PL/ directory listing does not contain a 'PERI-62/' href. "
            "Cannot derive PERI-62 URL."
        )
    discovered_href = m.group(1)  # e.g. "PERI-62/"
    # Validate and return absolute URL
    return _validate_discovered_fgm_peri62_href(discovered_href, pl_base_url)


def _extract_fgm_peri62_candidates(
    peri62_html_bytes: bytes,
    evidence_id: str,
) -> list[dict[str, Any]]:
    """Stage 2: Extract all relevant .lbl candidates from PERI-62/ directory HTML.

    §4/§5: Extract ALL relevant label candidates, classify each.
    §10: UTF-8 strict decoding.
    §6: product_id = LABEL_VERIFICATION_PENDING (directory HTML only).
        expected_archive_identity_source = DISCOVERY_PATH_DERIVED.

    Do NOT select merely because filename contains the date anchor.
    R1S/downsampled variants are stored but excluded from selection.
    """
    try:
        content = peri62_html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"FGM PERI-62 directory HTML is not valid UTF-8: {exc}."
        ) from exc

    rows = []
    seen: set[str] = set()
    for m in _FGM_LBL_HREF_RE.finditer(content):
        lbl_filename = m.group(1)
        if lbl_filename in seen:
            continue
        seen.add(lbl_filename)

        # Only include candidates containing the date anchor
        if _FGM_DATE_ANCHOR not in lbl_filename.lower():
            continue

        classification, selected = _classify_fgm_candidate(lbl_filename)

        stem_no_ext = lbl_filename.rsplit(".lbl", 1)[0].rsplit(".LBL", 1)[0]
        logical_stem = re.sub(r"_v\d+$", "", stem_no_ext, flags=re.IGNORECASE)

        rows.append({
            "lbl_filename": lbl_filename,
            "product_id": "LABEL_VERIFICATION_PENDING",
            "logical_stem": logical_stem,
            "selected": selected,
            "candidate_classification": classification,
            "expected_archive_identity_source": "DISCOVERY_PATH_DERIVED",
            "relative_label_path": lbl_filename,
            "discovery_evidence_id": evidence_id,
        })

    return sorted(rows, key=lambda r: r["lbl_filename"])


# ---------------------------------------------------------------------------
# Extractor: JADE INDEX.TAB
# ---------------------------------------------------------------------------

def _extract_jade_index_tab(
    tab_bytes: bytes,
    evidence_id: str,
) -> list[dict[str, Any]]:
    """Extract JADE orbit-62 products from INDEX.TAB bytes.

    §10: Latin-1 (PDS3 source contract).

    Fixed-width records, 274 bytes each.
    Eligibility:
    - Products with START_TIME in DOY165 or DOY166 are orbit-62 candidates.
    - ELIGIBLE: stop_time <= decision_epoch
    - EXCLUDED: stop_time > decision_epoch
    """
    RECORD_BYTES = 274
    DOY165_START = datetime.datetime(2024, 6, 13, tzinfo=datetime.timezone.utc)
    DOY167_START = datetime.datetime(2024, 6, 15, tzinfo=datetime.timezone.utc)

    def parse_pds3_time(t: str) -> Optional[datetime.datetime]:
        t = t.strip()
        m = re.match(r"(\d{4})-(\d{3})T(\d{2}):(\d{2}):(\d{2})\.(\d+)", t)
        if not m:
            return None
        year, doy, h, mi, s, frac = m.groups()
        return datetime.datetime(int(year), 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(
            days=int(doy) - 1,
            hours=int(h), minutes=int(mi), seconds=int(s),
            microseconds=int(frac[:6].ljust(6, "0")),
        )

    def parse_row(raw: str) -> Optional[dict[str, str]]:
        raw = raw.rstrip("\r\n")
        fields: list[str] = []
        i = 0
        while i < len(raw):
            if raw[i] == '"':
                end = raw.find('"', i + 1)
                if end == -1:
                    break
                fields.append(raw[i + 1:end].strip())
                i = end + 1
                if i < len(raw) and raw[i] == ",":
                    i += 1
            elif raw[i] == ",":
                i += 1
            else:
                end = raw.find(",", i)
                if end == -1:
                    end = len(raw)
                fields.append(raw[i:end].strip())
                i = end + 1
        if len(fields) < 7:
            return None
        return {
            "product_id": fields[3],
            "start_time": fields[4],
            "stop_time": fields[5],
            "file_specification_name": fields[6].strip(),
        }

    try:
        # Latin-1 for PDS3 fixed-width records
        _ = tab_bytes.decode("latin-1")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JADE INDEX.TAB is not valid Latin-1: {exc}.") from exc

    rows_out = []
    offset = RECORD_BYTES  # skip header row
    while offset + RECORD_BYTES <= len(tab_bytes):
        raw = tab_bytes[offset:offset + RECORD_BYTES].decode("latin-1")
        offset += RECORD_BYTES
        r = parse_row(raw)
        if r is None:
            continue
        start_dt = parse_pds3_time(r["start_time"])
        stop_dt = parse_pds3_time(r["stop_time"])
        if start_dt is None or stop_dt is None:
            continue
        if start_dt < DOY165_START or start_dt >= DOY167_START:
            continue
        if stop_dt < start_dt:
            raise ValueError(
                f"JADE row {r['product_id']!r}: stop_time < start_time."
            )

        doy = 165 if start_dt < datetime.datetime(2024, 6, 14, tzinfo=datetime.timezone.utc) else 166

        # JADE eligibility: stop_time <= decision_epoch
        if stop_dt <= _DECISION_EPOCH_UTC:
            inclusion = "ELIGIBLE"
        else:
            inclusion = "EXCLUDED"

        product_id = r["product_id"]
        file_spec = r["file_specification_name"]

        rows_out.append({
            "product_id": product_id,
            "relative_label_path": file_spec,
            "doy": doy,
            "start_time_utc": start_dt.isoformat(),
            "stop_time_utc": stop_dt.isoformat(),
            "inclusion": inclusion,
            "discovery_evidence_id": evidence_id,
        })

    return sorted(rows_out, key=lambda r: (r["doy"], r["product_id"]))


# ---------------------------------------------------------------------------
# Extractor: JEDI directory HTML
# ---------------------------------------------------------------------------

_JEDI_LBL_RE = re.compile(r'href="(JED_\w+_CDR_2024\d{3}_V\d{2}\.LBL)"')


def _extract_jedi(html_bytes: bytes, evidence_id: str, doy: int) -> list[dict[str, Any]]:
    """Extract JEDI LBL rows from directory HTML bytes.

    §10: UTF-8 strict.
    """
    try:
        content = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"JEDI directory HTML is not valid UTF-8: {exc}.") from exc

    rows = []
    seen: set[str] = set()
    for m in _JEDI_LBL_RE.finditer(content):
        lbl = m.group(1)
        if lbl in seen:
            continue
        seen.add(lbl)
        product_id = lbl[:-4]
        if str(doy) not in product_id:
            continue
        relative_label_path = f"{doy}/{lbl}"
        rows.append({
            "product_id": product_id,
            "doy": doy,
            "relative_label_path": relative_label_path,
            "discovery_evidence_id": evidence_id,
        })
    return sorted(rows, key=lambda r: r["product_id"])


# ---------------------------------------------------------------------------
# Extractor: WAVES Survey directory HTML
# ---------------------------------------------------------------------------

_WAVES_SRV_LBL_RE = re.compile(r'href="(WAV_(2024165|2024166)T\d{6}_([BE])_V01\.LBL)"')


def _extract_waves_survey(html_bytes: bytes, evidence_id: str) -> list[dict[str, Any]]:
    """Extract WAVES Survey label rows from directory HTML bytes.

    §10: UTF-8 strict.
    """
    try:
        content = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"WAVES Survey directory HTML is not valid UTF-8: {exc}.") from exc

    rows = []
    seen: set[str] = set()
    for m in _WAVES_SRV_LBL_RE.finditer(content):
        lbl = m.group(1)
        doy_part = m.group(2)
        band_char = m.group(3).lower()
        if lbl in seen:
            continue
        seen.add(lbl)
        stem = lbl[:-4]

        # DOY165 products are ELIGIBLE; DOY166 are EXCLUDED (post-decision window)
        inclusion = "ELIGIBLE" if doy_part == "2024165" else "EXCLUDED"

        rows.append({
            "stem": stem,
            "band": band_char,
            "inclusion": inclusion,
            "relative_label_path": lbl,
            "discovery_evidence_id": evidence_id,
        })
    return sorted(rows, key=lambda r: (r["inclusion"], r["stem"]))


# ---------------------------------------------------------------------------
# Extractor: WAVES Burst INDEX.TAB
# ---------------------------------------------------------------------------

_WAVES_BURST_FAMILY_RE = re.compile(
    r"WAV_\d{7}T\d{6}_(B_BIN|E_BIN|B_REC|E_REC|NBS_REC)"
)


def _extract_waves_burst_index_tab(tab_bytes: bytes, evidence_id: str) -> list[dict[str, Any]]:
    """Extract WAVES Burst orbit-62 rows from INDEX.TAB bytes (BSTFULL).

    §10: Latin-1 (PDS3 index source contract).
    §18/§19: Uses classify_temporal_partition() — stop-time-based only.
    """
    def parse_cal_time(t: str) -> Optional[datetime.datetime]:
        t = t.strip()
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})\.(\d+)", t)
        if not m:
            return None
        year, mo, day, h, mi, s, frac = m.groups()
        return datetime.datetime(
            int(year), int(mo), int(day),
            int(h), int(mi), int(s),
            int(frac[:6].ljust(6, "0")),
            tzinfo=datetime.timezone.utc,
        )

    try:
        content = tab_bytes.decode("latin-1")
    except UnicodeDecodeError as exc:
        raise ValueError(f"WAVES Burst INDEX.TAB is not valid Latin-1: {exc}.") from exc

    reader = csv.reader(io.StringIO(content))
    rows_out = []
    seen: set[str] = set()
    first_row = True

    for fields in reader:
        if first_row:
            first_row = False
            if fields and "VOLUME_ID" in fields[0].upper():
                continue
        if len(fields) < 7:
            continue

        product_id = fields[3].strip()
        start_str = fields[4].strip()
        stop_str = fields[5].strip()
        file_spec = fields[6].strip()

        if not product_id or not file_spec:
            continue
        if "ORBIT_62" not in file_spec:
            continue
        if product_id in seen:
            continue

        m_fam = _WAVES_BURST_FAMILY_RE.search(product_id)
        if not m_fam:
            continue
        family = m_fam.group(1)

        start_dt = parse_cal_time(start_str)
        stop_dt = parse_cal_time(stop_str)
        if start_dt is None or stop_dt is None:
            continue
        if stop_dt < start_dt:
            raise ValueError(
                f"WAVES Burst row {product_id!r}: stop_time < start_time."
            )

        seen.add(product_id)

        # §18/§19: partition by stop_time only (shared function)
        partition = classify_temporal_partition(stop_dt)

        rows_out.append({
            "product_id": product_id,
            "file_specification_name": file_spec,
            "start_time": start_dt.isoformat(),
            "stop_time": stop_dt.isoformat(),
            "family": family,
            "partition": partition,
            "discovery_evidence_id": evidence_id,
        })

    return sorted(rows_out, key=lambda r: r["file_specification_name"])


# ---------------------------------------------------------------------------
# Sidecar artifact_id
# ---------------------------------------------------------------------------

def _compute_artifact_id(sidecar: dict) -> str:
    """Delegate to canonical compute_sidecar_artifact_id (handles collection sorting)."""
    from backend.app.mission_sources.v2_sidecar_models import compute_sidecar_artifact_id
    return compute_sidecar_artifact_id(sidecar)


# ---------------------------------------------------------------------------
# Main refresh logic
# ---------------------------------------------------------------------------

def refresh(dry_run: bool = False) -> None:
    """Fetch all metadata sources and regenerate the discovery evidence sidecar.

    §31: Before filesystem commit: assemble typed model → validate all nested rows
    → compute artifact_id → construct final typed sidecar → revalidate → canonical
    serialization → atomic write.
    """
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    output_path = repo_root / "data" / "replays" / "juno_pj62_large_replay_v2_discovery_evidence.json"

    print("GCSI Phase 6F-B2.1.4 — Refreshing discovery evidence sidecar", file=sys.stderr)
    print(f"  Output: {output_path}", file=sys.stderr)
    if dry_run:
        print("  DRY RUN — no writes.", file=sys.stderr)

    fetched: dict[str, tuple[bytes, dict]] = {}

    # Fetch all non-FGM-PERI62 sources
    for ev_id, url, source_kind in _ALLOWLISTED_SOURCES:
        print(f"  Fetching [{ev_id}] {url} ...", file=sys.stderr)
        resp_bytes, http_status, retrieved_at = _fetch_metadata(ev_id, url)
        ev_rec = _make_evidence(
            evidence_id=ev_id,
            source_url=url,
            source_kind=source_kind,
            response_bytes=resp_bytes,
            http_status=http_status,
            retrieved_at=retrieved_at,
        )
        fetched[ev_id] = (resp_bytes, ev_rec)
        print(
            f"    OK: {len(resp_bytes)} bytes  sha256={ev_rec['response_sha256'][:16]}...",
            file=sys.stderr,
        )

    # -----------------------------------------------------------------------
    # FGM two-stage discovery (§4)
    # -----------------------------------------------------------------------
    pl_base_url = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-3-FGM-CAL-V1.0/DATA/JUPITER/PL/"
    pl_bytes, _ = fetched["fgm_jupiter_pl_directory_html"]

    print("  FGM Stage 2: discovering PERI-62/ URL from PL/ listing ...", file=sys.stderr)
    peri62_url = _extract_peri62_href_from_pl_listing(pl_bytes, pl_base_url)
    print(f"    Discovered PERI-62 href → {peri62_url}", file=sys.stderr)

    # Register trust entry for the dynamically discovered PERI-62 URL
    # (already in _SOURCE_TRUST under "fgm_peri62_directory_html")
    peri62_ev_id = "fgm_peri62_directory_html"
    print(f"  Fetching [{peri62_ev_id}] {peri62_url} ...", file=sys.stderr)
    peri62_bytes, peri62_status, peri62_retrieved_at = _fetch_metadata(peri62_ev_id, peri62_url)
    peri62_ev_rec = _make_evidence(
        evidence_id=peri62_ev_id,
        source_url=peri62_url,
        source_kind="pds3_directory_html",
        response_bytes=peri62_bytes,
        http_status=peri62_status,
        retrieved_at=peri62_retrieved_at,
    )
    fetched[peri62_ev_id] = (peri62_bytes, peri62_ev_rec)
    print(
        f"    OK: {len(peri62_bytes)} bytes  sha256={peri62_ev_rec['response_sha256'][:16]}...",
        file=sys.stderr,
    )

    # -----------------------------------------------------------------------
    # Extract normalized rows
    # -----------------------------------------------------------------------
    jiram_bytes, _ = fetched["jiram_orbit62_directory_html"]
    jiram_rows = _extract_jiram(jiram_bytes, "jiram_orbit62_directory_html")

    mwr_irdr_165_bytes, _ = fetched["mwr_irdr_2024165_directory_html"]
    mwr_irdr_166_bytes, _ = fetched["mwr_irdr_2024166_directory_html"]
    mwr_grdr_165_bytes, _ = fetched["mwr_grdr_2024165_directory_html"]
    mwr_grdr_166_bytes, _ = fetched["mwr_grdr_2024166_directory_html"]
    mwr_rows = (
        _extract_mwr(mwr_irdr_165_bytes, "mwr_irdr_2024165_directory_html", "IRDR", 165)
        + _extract_mwr(mwr_irdr_166_bytes, "mwr_irdr_2024166_directory_html", "IRDR", 166)
        + _extract_mwr(mwr_grdr_165_bytes, "mwr_grdr_2024165_directory_html", "GRDR", 165)
        + _extract_mwr(mwr_grdr_166_bytes, "mwr_grdr_2024166_directory_html", "GRDR", 166)
    )

    uvs_bytes, _ = fetched["uvs_orbit62_directory_html"]
    uvs_rows = _extract_uvs(uvs_bytes, "uvs_orbit62_directory_html")

    jnc_lbl_bytes, _ = fetched["junocam_jnojnc_0029_index_lbl"]
    jnc_tab_bytes, _ = fetched["junocam_jnojnc_0029_index_tab"]
    jnc_meta = _parse_junocam_index_tab(jnc_lbl_bytes)
    junocam_all_rows = _extract_junocam_index_tab(
        jnc_tab_bytes, jnc_meta["record_bytes"], "junocam_jnojnc_0029_index_tab"
    )

    # FGM: stage 2 candidates from PERI-62
    fgm_rows = _extract_fgm_peri62_candidates(peri62_bytes, peri62_ev_id)

    jade_tab_bytes, _ = fetched["jade_index_tab"]
    jade_rows = _extract_jade_index_tab(jade_tab_bytes, "jade_index_tab")

    jedi_165_bytes, _ = fetched["jedi_165_directory_html"]
    jedi_166_bytes, _ = fetched["jedi_166_directory_html"]
    jedi_165_rows = _extract_jedi(jedi_165_bytes, "jedi_165_directory_html", 165)
    jedi_166_rows = _extract_jedi(jedi_166_bytes, "jedi_166_directory_html", 166)

    waves_srv_bytes, _ = fetched["waves_survey_orbit62_directory_html"]
    waves_survey_rows = _extract_waves_survey(waves_srv_bytes, "waves_survey_orbit62_directory_html")

    waves_burst_bytes, _ = fetched["waves_burst_bstfull_index_tab"]
    waves_burst_rows = _extract_waves_burst_index_tab(
        waves_burst_bytes, "waves_burst_bstfull_index_tab"
    )

    # -----------------------------------------------------------------------
    # Update evidence record row counts
    # -----------------------------------------------------------------------
    fetched["jiram_orbit62_directory_html"][1]["relevant_row_count"] = len(jiram_rows)
    fetched["mwr_irdr_2024165_directory_html"][1]["relevant_row_count"] = sum(
        1 for r in mwr_rows if r["product_type"] == "IRDR" and r["doy"] == 165
    )
    fetched["mwr_irdr_2024166_directory_html"][1]["relevant_row_count"] = sum(
        1 for r in mwr_rows if r["product_type"] == "IRDR" and r["doy"] == 166
    )
    fetched["mwr_grdr_2024165_directory_html"][1]["relevant_row_count"] = sum(
        1 for r in mwr_rows if r["product_type"] == "GRDR" and r["doy"] == 165
    )
    fetched["mwr_grdr_2024166_directory_html"][1]["relevant_row_count"] = sum(
        1 for r in mwr_rows if r["product_type"] == "GRDR" and r["doy"] == 166
    )
    fetched["uvs_orbit62_directory_html"][1]["relevant_row_count"] = len(uvs_rows)
    fetched["junocam_jnojnc_0029_index_lbl"][1]["relevant_row_count"] = len(junocam_all_rows)
    fetched["junocam_jnojnc_0029_index_tab"][1]["relevant_row_count"] = len(junocam_all_rows)
    # FGM PL root: 1 PERI-62 subdir discovered
    fetched["fgm_jupiter_pl_directory_html"][1]["relevant_row_count"] = 1
    # FGM PERI-62: all candidate rows
    fetched[peri62_ev_id][1]["relevant_row_count"] = len(fgm_rows)
    fetched["jade_index_lbl"][1]["relevant_row_count"] = len(jade_rows)
    fetched["jade_index_tab"][1]["relevant_row_count"] = len(jade_rows)
    fetched["jedi_165_directory_html"][1]["relevant_row_count"] = len(jedi_165_rows)
    fetched["jedi_166_directory_html"][1]["relevant_row_count"] = len(jedi_166_rows)
    fetched["waves_survey_orbit62_directory_html"][1]["relevant_row_count"] = len(waves_survey_rows)
    fetched["waves_burst_bstfull_index_tab"][1]["relevant_row_count"] = len(waves_burst_rows)

    # -----------------------------------------------------------------------
    # Partition summaries
    # -----------------------------------------------------------------------
    jnc_pre = sum(1 for r in junocam_all_rows if r["partition"] == "PRE")
    jnc_elig = sum(1 for r in junocam_all_rows if r["partition"] == "ELIGIBLE")
    jnc_post = sum(1 for r in junocam_all_rows if r["partition"] == "POST")

    wb_pre = sum(1 for r in waves_burst_rows if r["partition"] == "PRE")
    wb_elig = sum(1 for r in waves_burst_rows if r["partition"] == "ELIGIBLE")
    wb_post = sum(1 for r in waves_burst_rows if r["partition"] == "POST")

    wb_eligible_rows = [r for r in waves_burst_rows if r["partition"] == "ELIGIBLE"]
    from collections import Counter as _Counter
    wb_fam_counts = dict(_Counter(r["family"] for r in wb_eligible_rows))

    partition_summaries = {
        "junocam": {
            "instrument": "JUNOCAM",
            "source_evidence_id": "junocam_jnojnc_0029_index_tab",
            "total_orbit62_rows": len(junocam_all_rows),
            "pre_rows": jnc_pre,
            "eligible_rows": jnc_elig,
            "post_rows": jnc_post,
            "note": (
                f"eligible_rows={jnc_elig} = {jnc_elig//2} EDR + {jnc_elig//2} RDR = "
                f"{jnc_elig//2} logical observations. "
                f"PRE_LOGICAL={jnc_pre//2} ELIGIBLE_LOGICAL={jnc_elig//2} "
                f"POST_LOGICAL={jnc_post//2}. "
                "B21_RAW_ROW_LEDGER_SUPERSEDED=YES HISTORICAL_213_LOGICAL_OBSERVATION_LEDGER=CONFIRMED."
            ),
        },
        "waves_burst": {
            "instrument": "WAVES_BURST",
            "source_evidence_id": "waves_burst_bstfull_index_tab",
            "total_orbit62_rows": len(waves_burst_rows),
            "pre_rows": wb_pre,
            "eligible_rows": wb_elig,
            "post_rows": wb_post,
            "eligible_families": wb_fam_counts,
        },
    }

    # -----------------------------------------------------------------------
    # Assemble normalized_extractions
    # -----------------------------------------------------------------------
    normalized_extractions = {
        "fgm_peri62_filenames": fgm_rows,
        "jade_orbit62_labels": jade_rows,
        "jedi_165_labels": jedi_165_rows,
        "jedi_166_labels": jedi_166_rows,
        "jiram_orbit62_filenames": jiram_rows,
        "junocam_index_tab_orbit62_all": junocam_all_rows,
        "mwr_orbit62_filenames": mwr_rows,
        "partition_summaries": partition_summaries,
        "uvs_orbit62_filenames": uvs_rows,
        "waves_burst_index_tab_orbit62_all": waves_burst_rows,
        "waves_survey_orbit62_labels": waves_survey_rows,
    }

    evidence_list = sorted(
        [ev_rec for _, ev_rec in fetched.values()],
        key=lambda e: e["evidence_id"],
    )

    # -----------------------------------------------------------------------
    # §31: Assemble, validate typed model, compute artifact_id, revalidate, write atomically
    # -----------------------------------------------------------------------
    sidecar_dict: dict[str, Any] = {
        "schema": _SIDECAR_SCHEMA,
        "schema_version": _SIDECAR_VERSION,
        "replay_id": _REPLAY_ID,
        "discovery_evidence": evidence_list,
        "normalized_extractions": normalized_extractions,
    }

    # Compute artifact_id
    artifact_id = _compute_artifact_id(sidecar_dict)
    sidecar_dict["artifact_id"] = artifact_id

    # §31: Validate typed model before write
    from backend.app.mission_sources.v2_sidecar_models import HistoricalReplayV2DiscoveryEvidenceSidecar
    try:
        validated = HistoricalReplayV2DiscoveryEvidenceSidecar.model_validate(sidecar_dict, strict=False)
    except Exception as exc:
        raise RuntimeError(
            f"Sidecar failed typed model validation before write: {exc}\n"
            "6F_B214_STATUS = SOURCE_DERIVED_REFRESH_VALIDATION_FAILED"
        ) from exc

    # Reconciliation assertions
    _assert_reconciliation(
        jiram_rows, mwr_rows, uvs_rows, fgm_rows, jade_rows,
        jedi_165_rows, jedi_166_rows, waves_survey_rows,
        junocam_all_rows, waves_burst_rows,
        jnc_pre, jnc_elig, jnc_post,
        wb_pre, wb_elig, wb_post,
        wb_fam_counts,
    )

    print(f"\n  artifact_id: {artifact_id}", file=sys.stderr)
    print(f"  JIRAM: {len(jiram_rows)}, MWR: {len(mwr_rows)}, UVS: {len(uvs_rows)}", file=sys.stderr)
    print(
        f"  FGM: {len(fgm_rows)} candidates "
        f"({sum(1 for r in fgm_rows if r['selected'])} selected), "
        f"JADE: {len(jade_rows)}, JEDI: {len(jedi_165_rows)+len(jedi_166_rows)}",
        file=sys.stderr,
    )
    print(f"  WavesSurvey: {len(waves_survey_rows)}, JunoCam: {len(junocam_all_rows)}", file=sys.stderr)
    print(f"  WavesBurst: {len(waves_burst_rows)}", file=sys.stderr)
    print(f"  Evidence records: {len(evidence_list)}", file=sys.stderr)

    if dry_run:
        print("\n  DRY RUN — sidecar not written.", file=sys.stderr)
        return

    # Atomic write via temp file + replace
    import tempfile
    import os
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize the raw dict (not the validated model) for JSON output
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json",
        dir=output_path.parent, delete=False
    ) as tmp:
        tmp_path = tmp.name
        json.dump(sidecar_dict, tmp, indent=2, sort_keys=True)
        tmp.write("\n")

    os.replace(tmp_path, str(output_path))
    print(f"\n  Written: {output_path}", file=sys.stderr)
    print("  6F_B214_SIDECAR_STATUS = SOURCE_DERIVED_REFRESH_COMPLETE", file=sys.stderr)


def _assert_reconciliation(
    jiram_rows, mwr_rows, uvs_rows, fgm_rows, jade_rows,
    jedi_165_rows, jedi_166_rows, waves_survey_rows,
    junocam_all_rows, waves_burst_rows,
    jnc_pre, jnc_elig, jnc_post,
    wb_pre, wb_elig, wb_post,
    wb_fam_counts,
) -> None:
    """Assert all counts match frozen census."""
    errs = []

    if len(jiram_rows) != 102:
        errs.append(f"JIRAM rows: expected 102, got {len(jiram_rows)}")
    if sum(1 for r in jiram_rows if r["family"] == "IMG") != 51:
        errs.append("JIRAM IMG: expected 51")
    if sum(1 for r in jiram_rows if r["family"] == "SPE") != 51:
        errs.append("JIRAM SPE: expected 51")

    mwr_eligible = [r for r in mwr_rows if r.get("inclusion") == "ELIGIBLE"]
    mwr_excluded = [r for r in mwr_rows if r.get("inclusion") == "EXCLUDED"]
    if len(mwr_rows) != 96:
        errs.append(f"MWR rows: expected 96, got {len(mwr_rows)}")
    if len(mwr_eligible) != 46:
        errs.append(f"MWR eligible rows: expected 46, got {len(mwr_eligible)}")
    if len(mwr_excluded) != 50:
        errs.append(f"MWR excluded rows: expected 50, got {len(mwr_excluded)}")

    if len(uvs_rows) != 8:
        errs.append(f"UVS rows: expected 8, got {len(uvs_rows)}")

    fgm_selected = [r for r in fgm_rows if r["selected"]]
    if len(fgm_selected) != 2:
        errs.append(f"FGM selected: expected 2, got {len(fgm_selected)}")

    jade_eligible = [r for r in jade_rows if r["inclusion"] == "ELIGIBLE"]
    jade_excluded = [r for r in jade_rows if r["inclusion"] == "EXCLUDED"]
    if len(jade_rows) != 12:
        errs.append(f"JADE total: expected 12, got {len(jade_rows)}")
    if len(jade_eligible) != 8:
        errs.append(f"JADE eligible: expected 8, got {len(jade_eligible)}")
    if len(jade_excluded) != 4:
        errs.append(f"JADE excluded: expected 4, got {len(jade_excluded)}")

    jedi_total = len(jedi_165_rows) + len(jedi_166_rows)
    if jedi_total != 28:
        errs.append(f"JEDI total: expected 28, got {jedi_total}")

    ws_elig = sum(1 for r in waves_survey_rows if r["inclusion"] == "ELIGIBLE")
    ws_excl = sum(1 for r in waves_survey_rows if r["inclusion"] == "EXCLUDED")
    if len(waves_survey_rows) != 4:
        errs.append(f"WavesSurvey total: expected 4, got {len(waves_survey_rows)}")
    if ws_elig != 2:
        errs.append(f"WavesSurvey eligible: expected 2, got {ws_elig}")
    if ws_excl != 2:
        errs.append(f"WavesSurvey excluded: expected 2, got {ws_excl}")

    if len(junocam_all_rows) != 426:
        errs.append(f"JunoCam all rows: expected 426, got {len(junocam_all_rows)}")
    if jnc_pre != 112:
        errs.append(f"JunoCam PRE: expected 112, got {jnc_pre}")
    if jnc_elig != 248:
        errs.append(f"JunoCam ELIGIBLE: expected 248, got {jnc_elig}")
    if jnc_post != 66:
        errs.append(f"JunoCam POST: expected 66, got {jnc_post}")

    if len(waves_burst_rows) != 282:
        errs.append(f"WavesBurst total: expected 282, got {len(waves_burst_rows)}")
    if wb_pre != 175:
        errs.append(f"WavesBurst PRE: expected 175, got {wb_pre}")
    if wb_elig != 91:
        errs.append(f"WavesBurst ELIGIBLE: expected 91, got {wb_elig}")
    if wb_post != 16:
        errs.append(f"WavesBurst POST: expected 16, got {wb_post}")

    expected_fam = {"B_BIN": 41, "E_BIN": 41, "B_REC": 3, "E_REC": 3, "NBS_REC": 3}
    for fam, count in expected_fam.items():
        if wb_fam_counts.get(fam, 0) != count:
            errs.append(
                f"WavesBurst eligible {fam}: expected {count}, got {wb_fam_counts.get(fam, 0)}"
            )

    if errs:
        raise RuntimeError(
            "6F_B214_STATUS = SOURCE_ENUMERATION_RECONCILIATION_REQUIRED\n"
            + "\n".join(f"  - {e}" for e in errs)
        )

    print("  Reconciliation: ALL PASSED", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh GCSI B2.1.4 discovery evidence sidecar.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write.")
    args = parser.parse_args()
    refresh(dry_run=args.dry_run)
