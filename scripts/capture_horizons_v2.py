#!/usr/bin/env python3
"""Capture exact Juno Horizons geometry for the B2.2 V2 decision epoch.

Target:  -61 (Juno spacecraft)
Center:  500@399 (Earth geocenter)
Epoch:   2024-06-14T09:35:17.546000Z  (V2 decision epoch — NOT V1)

Output:
    data/verified_snapshots/horizons/juno/
    juno_spk_-61_2024-06-14T093517.546000Z.json

Usage:
    python scripts/capture_horizons_v2.py
"""
from __future__ import annotations

import logging
import pathlib
import sys
from datetime import datetime, timezone

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from backend.app.mission_sources.adapters.horizons import HorizonsAdapter
from backend.app.mission_sources.adapters.horizons_models import HorizonsGeometryRequest
from backend.app.mission_sources.snapshots.horizons_snapshot import HorizonsSnapshotStore

# ---------------------------------------------------------------------------
# B2.2 frozen constants
# ---------------------------------------------------------------------------

# Juno spacecraft SPK ID
TARGET_SPK_ID = "-61"

# EXACT V2 decision epoch — must NOT be the V1 epoch
# V1 epoch was 2024-06-14T03:59:55.483000Z
# V2 epoch is  2024-06-14T09:35:17.546000Z
DECISION_EPOCH_UTC = datetime(2024, 6, 14, 9, 35, 17, 546000, tzinfo=timezone.utc)

# Output path (matching existing naming convention)
_HORIZONS_SNAP_DIR = _REPO_ROOT / "data" / "verified_snapshots" / "horizons" / "juno"
_SNAPSHOT_FILENAME = "juno_spk_-61_2024-06-14T093517.546000Z.json"
_SNAPSHOT_PATH = _HORIZONS_SNAP_DIR / _SNAPSHOT_FILENAME


def main() -> None:
    logger.info("B2.2 Horizons Geometry Capture")
    logger.info("  Target: %s (Juno spacecraft)", TARGET_SPK_ID)
    logger.info("  Center: 500@399 (Earth geocenter)")
    logger.info("  Epoch:  %s", DECISION_EPOCH_UTC.isoformat())
    logger.info("  Output: %s", _SNAPSHOT_PATH.relative_to(_REPO_ROOT))

    # Check if snapshot already exists
    if _SNAPSHOT_PATH.exists():
        logger.info("Snapshot already exists — loading for verification...")
        try:
            result = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)
            logger.info("  ✓ Snapshot loaded and verified successfully")
            logger.info("  snapshot_id       = %s", result.provenance.provenance_id[:16] + "...")
            logger.info("  range_km          = %.3f", result.geometry.range_km)
            logger.info("  range_rate_km_s   = %.6f", result.geometry.range_rate_km_s)
            logger.info("  one_way_lt_s      = %.6f", result.geometry.one_way_light_time_s)
            logger.info("  Existing snapshot is valid — no re-fetch needed.")
            return
        except Exception as exc:
            logger.warning("Existing snapshot failed validation: %s", exc)
            logger.info("Will re-fetch from Horizons.")

    # Build request
    request = HorizonsGeometryRequest(
        target_spk_id=TARGET_SPK_ID,
        epoch_utc=DECISION_EPOCH_UTC,
    )

    logger.info("Fetching from JPL Horizons API...")
    with HorizonsAdapter() as adapter:
        try:
            capture = adapter.fetch_capture(request)
        except Exception as exc:
            logger.error("Horizons fetch failed: %s", exc)
            sys.exit(1)

    result = capture.result
    geometry = result.geometry
    provenance = result.provenance
    raw_bytes = capture.raw_response

    logger.info("\n--- Horizons Geometry ---")
    logger.info("  epoch_utc         = %s", request.epoch_utc.isoformat())
    logger.info("  target_spk_id     = %s", request.target_spk_id)
    logger.info("  center            = 500@399 (Earth geocenter)")
    logger.info("  range_km          = %.6f", geometry.range_km)
    logger.info("  range_rate_km_s   = %.9f", geometry.range_rate_km_s)
    logger.info("  one_way_lt_s      = %.9f", geometry.one_way_light_time_s)
    logger.info("  raw_bytes         = %d bytes", len(raw_bytes))
    logger.info("  raw_sha256        = %s", provenance.content_sha256)
    logger.info("  provenance_id     = %s", provenance.provenance_id)
    logger.info("  retrieved_at      = %s", provenance.retrieved_at.isoformat())

    # Verify light-time consistency: one_way_lt_s ≈ range_km / c
    _C_KM_S = 299792.458  # speed of light in km/s
    expected_lt = geometry.range_km / _C_KM_S
    discrepancy = abs(geometry.one_way_light_time_s - expected_lt)
    logger.info("\n  Light-time consistency check:")
    logger.info("    computed = %.9f s", expected_lt)
    logger.info("    reported = %.9f s", geometry.one_way_light_time_s)
    logger.info("    |delta|  = %.6f s", discrepancy)
    if discrepancy > 1.0:
        logger.error("Light-time discrepancy > 1 second — unexpected!")
        sys.exit(1)
    logger.info("    ✓ consistent")

    # Write snapshot
    _HORIZONS_SNAP_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("\nWriting snapshot to: %s", _SNAPSHOT_PATH.relative_to(_REPO_ROOT))
    HorizonsSnapshotStore.write(capture, _SNAPSHOT_PATH)
    logger.info("  ✓ Snapshot written")

    # Immediately reload to verify
    logger.info("Zero-network reload verification...")
    reloaded = HorizonsSnapshotStore.load(_SNAPSHOT_PATH)
    assert reloaded.geometry == geometry, "Reload geometry mismatch!"
    assert reloaded.provenance == provenance, "Reload provenance mismatch!"
    logger.info("  ✓ Reload verified")

    # Read snapshot_id from envelope
    import json
    env = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    snapshot_id = env["snapshot_id"]
    logger.info("\n--- Final Horizons Snapshot ---")
    logger.info("  snapshot_id       = %s", snapshot_id)
    logger.info("  snapshot_path     = %s", _SNAPSHOT_PATH.relative_to(_REPO_ROOT))
    logger.info("  range_km          = %.6f", geometry.range_km)
    logger.info("  range_rate_km_s   = %.9f", geometry.range_rate_km_s)
    logger.info("  one_way_lt_s      = %.9f", geometry.one_way_light_time_s)
    logger.info("  raw_sha256        = %s", provenance.content_sha256)
    logger.info("  raw_bytes         = %d", len(raw_bytes))
    logger.info("\nHorizons capture COMPLETE ✓")


if __name__ == "__main__":
    main()
