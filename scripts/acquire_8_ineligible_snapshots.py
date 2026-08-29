#!/usr/bin/env python3
"""B2.2.1: Acquire 8 temporally ineligible label snapshots.

These are the 8 products excluded from the eligible inventory by B2.2 temporal
reconciliation. They must be preserved as authoritative source evidence.

JEDI (6 products):
  - JED_270_LOERSISP_CDR_2024165_V04 (PRE-epoch: stop=2024-06-13T09:53:07)
  - JED_090_LOERSESP_CDR_2024166_V04 (POST-epoch: stop=2024-06-14T23:59:57)
  - JED_090_LOERSISP_CDR_2024166_V04 (POST-epoch: stop=2024-06-14T23:59:57)
  - JED_180_LOERSESP_CDR_2024166_V04 (POST-epoch: stop=2024-06-14T23:59:57)
  - JED_180_LOERSISP_CDR_2024166_V04 (POST-epoch: stop=2024-06-14T23:59:57)
  - JED_270_LOERSESP_CDR_2024166_V04 (POST-epoch: stop=2024-06-14T23:59:57)

UVS (2 products):
  - UVS_S02_771613347_2024166_P62SY1_V01 (POST-epoch)
  - UVS_S03_771613347_2024166_P62SY1_V01 (POST-epoch)
"""
from __future__ import annotations

import sys
import pathlib

# Add repo root to path
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import hashlib
import json
import time
from datetime import datetime, timezone

import httpx

from backend.app.mission_sources.adapters.pds3_adapter import (
    JEDI_PDS3_PROFILE,
    parse_generic_pds3_label,
)
from backend.app.mission_sources.adapters.pds4_adapter import (
    UVS_PDS4_PROFILE,
    parse_generic_pds4_label,
)
from backend.app.mission_sources.snapshots.archive_label_snapshot import (
    ArchiveLabelSnapshotStore,
    ArchiveCaptureRecord,
)

_SNAPSHOT_ROOT = _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"

_JEDI_BASE_URL = "https://pds-ppi.igpp.ucla.edu/data/JNO-J-JED-3-CDR-V1.0/DATA/2024/"
_UVS_BASE_URL = "https://atmos.nmsu.edu/PDS/data/jnouvs_3001/DATA/ORBIT-62/"


# 8 ineligible products from B2.2 temporal reconciliation
_INELIGIBLE_PRODUCTS = [
    # JEDI PRE-epoch
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}165/JED_270_LOERSISP_CDR_2024165_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    # JEDI POST-epoch
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}166/JED_090_LOERSESP_CDR_2024166_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}166/JED_090_LOERSISP_CDR_2024166_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}166/JED_180_LOERSESP_CDR_2024166_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}166/JED_180_LOERSISP_CDR_2024166_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    {
        "instrument": "JEDI",
        "url": f"{_JEDI_BASE_URL}166/JED_270_LOERSESP_CDR_2024166_V04.LBL",
        "source_standard": "pds3",
        "normalizer_id": "gcsi.generic_pds3_label.v1",
        "profile_id": "jedi_pds3",
    },
    # UVS POST-epoch
    {
        "instrument": "UVS",
        "url": f"{_UVS_BASE_URL}UVS_S02_771613347_2024166_P62SY1_V01.xml",
        "source_standard": "pds4",
        "normalizer_id": "gcsi.generic_pds4_label.v1",
        "profile_id": "uvs_pds4",
    },
    {
        "instrument": "UVS",
        "url": f"{_UVS_BASE_URL}UVS_S03_771613347_2024166_P62SY1_V01.xml",
        "source_standard": "pds4",
        "normalizer_id": "gcsi.generic_pds4_label.v1",
        "profile_id": "uvs_pds4",
    },
]

_PDS3_PROFILES = {
    "jedi_pds3": JEDI_PDS3_PROFILE,
}
_PDS4_PROFILES = {
    "uvs_pds4": UVS_PDS4_PROFILE,
}

_MAX_BYTES = 2 * 1024 * 1024


def _snapshot_path_for_url(instrument: str, label_url: str) -> pathlib.Path:
    url_hash = hashlib.sha256(label_url.encode("utf-8")).hexdigest()
    return _SNAPSHOT_ROOT / instrument.lower() / f"{url_hash}.json"


def acquire_one(product: dict, client: httpx.Client) -> dict:
    """Fetch, parse, and store one label snapshot. Returns result dict."""
    url = product["url"]
    instrument = product["instrument"]
    source_standard = product["source_standard"]
    normalizer_id = product["normalizer_id"]
    profile_id = product["profile_id"]

    snapshot_path = _snapshot_path_for_url(instrument, url)

    # Check if already exists
    if snapshot_path.exists():
        print(f"  [REUSED] {url.split('/')[-1]}")
        existing_product, existing_prov = ArchiveLabelSnapshotStore.load(snapshot_path)
        env = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return {
            "url": url,
            "instrument": instrument,
            "status": "REUSED_VERIFIED_SNAPSHOT",
            "snapshot_path": str(snapshot_path),
            "snapshot_id": env.get("snapshot_id"),
            "raw_sha256": existing_prov.content_sha256,
            "observation_stop_utc": existing_product.observation_stop_utc.isoformat() if existing_product.observation_stop_utc else None,
        }

    retrieved_at = datetime.now(tz=timezone.utc)
    with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} for {url}")
        chunks = []
        total = 0
        for chunk in resp.iter_bytes(chunk_size=65536):
            total += len(chunk)
            if total > _MAX_BYTES:
                raise RuntimeError(f"Response too large for {url}")
            chunks.append(chunk)
        raw_bytes = b"".join(chunks)
    retrieved_at = datetime.now(tz=timezone.utc)

    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    if source_standard == "pds3":
        profile = _PDS3_PROFILES[profile_id]
        product_obj, prov = parse_generic_pds3_label(
            raw_bytes=raw_bytes,
            source_ref=url,
            profile=profile,
            retrieved_at=retrieved_at,
        )
    else:
        profile = _PDS4_PROFILES[profile_id]
        product_obj, prov = parse_generic_pds4_label(
            raw_bytes=raw_bytes,
            label_url=url,
            profile=profile,
            retrieved_at=retrieved_at,
        )

    capture = ArchiveCaptureRecord(
        source_label_ref=url,
        product=product_obj,
        provenance=prov,
        raw_label_bytes=raw_bytes,
    )

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    ArchiveLabelSnapshotStore.write(capture, snapshot_path, normalizer_id, profile_id)

    # Verify reload
    reloaded_product, reloaded_prov = ArchiveLabelSnapshotStore.load(snapshot_path)

    env = json.loads(snapshot_path.read_text(encoding="utf-8"))

    print(f"  [ACQUIRED] {url.split('/')[-1]} stop={reloaded_product.observation_stop_utc}")

    return {
        "url": url,
        "instrument": instrument,
        "status": "ACQUIRED_VERIFIED",
        "snapshot_path": str(snapshot_path),
        "snapshot_id": env.get("snapshot_id"),
        "raw_sha256": raw_sha256,
        "observation_stop_utc": reloaded_product.observation_stop_utc.isoformat() if reloaded_product.observation_stop_utc else None,
    }


def main():
    print(f"Acquiring 8 ineligible label snapshots...")
    results = []
    with httpx.Client(follow_redirects=False, timeout=30.0) as client:
        for i, product in enumerate(_INELIGIBLE_PRODUCTS):
            print(f"[{i+1}/8] {product['url'].split('/')[-1]}")
            result = acquire_one(product, client)
            results.append(result)
            if i < len(_INELIGIBLE_PRODUCTS) - 1:
                time.sleep(0.5)

    print("\nResults:")
    for r in results:
        fname = r['url'].split('/')[-1]
        print(f"  {r['status']:30s} {fname}")
        if r.get("observation_stop_utc"):
            print(f"    stop={r['observation_stop_utc']}")

    acquired = sum(1 for r in results if r["status"] in ("ACQUIRED_VERIFIED", "REUSED_VERIFIED_SNAPSHOT"))
    print(f"\n{acquired}/8 snapshots ready")

    if acquired == 8:
        print("SUCCESS: All 8 ineligible snapshots acquired/verified.")
    else:
        print("FAILURE: Not all snapshots were acquired.")
        sys.exit(1)


if __name__ == "__main__":
    main()
