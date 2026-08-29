#!/usr/bin/env python3
"""B2.2.1: Rebuild verified inventory from temporal reconciliation manifest."""
from __future__ import annotations

import sys
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

import json

from backend.app.mission_sources.v2_acquisition_plan_builder import build_plan
from backend.app.mission_sources.v2_inventory_acquisition import load_ledger
from backend.app.mission_sources.v2_verified_inventory import V2VerifiedInventoryBuilder
from backend.app.mission_sources.v2_temporal_reconciliation import load_reconciliation_manifest

_LEDGER_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_acquisition_ledger.json"
_RECONCILIATION_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_temporal_reconciliation.json"
_INVENTORY_PATH = _REPO_ROOT / "data" / "replays" / "juno_pj62_large_replay_v2_verified_inventory.json"
_SNAPSHOT_ROOT = _REPO_ROOT / "data" / "verified_snapshots" / "pds_archive" / "juno_pj62_large_replay_v2"

print("Building plan...")
plan = build_plan()
print(f"  plan_id: {plan.plan_id}")
print(f"  logical: {len(plan.logical_entries)}")

print("Loading ledger...")
ledger = load_ledger(_LEDGER_PATH)
print(f"  rows: {len(ledger.rows)}")

print("Loading reconciliation...")
reconciliation = load_reconciliation_manifest(_RECONCILIATION_PATH)
print(f"  eligible_logical: {reconciliation.eligible_logical_count}")
print(f"  ineligible_logical: {reconciliation.ineligible_logical_count}")

print("Building verified inventory...")
builder = V2VerifiedInventoryBuilder()
manifest = builder.build(plan, ledger, _SNAPSHOT_ROOT, reconciliation)
print(f"  entries: {len(manifest.entries)}")
print(f"  source_records: {len(manifest.source_records)}")
print(f"  manifest_id: {manifest.manifest_id}")

builder.save_manifest(manifest, _INVENTORY_PATH)
print(f"Saved inventory to {_INVENTORY_PATH}")
