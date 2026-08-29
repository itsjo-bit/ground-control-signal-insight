# GCSI Phase 6F-B1 — PDS Source Layer Architecture

## Overview

This document describes the verified inventory source foundation added in Phase 6F-B1.
It is developer-facing documentation explaining the source-layer architecture,
the normalization pipeline, and the boundaries between layers.

---

## Layer Architecture

```
PDS3 / PDS4 archive labels (any instrument family)
        │
        ▼
GenericPds3ObservationalLabelAdapter  (pds3_adapter.py)
GenericPds4ObservationalLabelAdapter  (pds4_adapter.py)
        │
        ▼  ArchiveCaptureRecord (archive_models.py)
        │   raw_label_bytes + ArchiveScienceProduct + ProvenanceRecord
        │
        ▼
ArchiveLabelSnapshotStore  (archive_label_snapshot.py)
        │  optional: persist verified capture to disk; reload offline
        │
        ▼
ArchiveScienceProduct  (archive_models.py)
        │  normalized external source fact
        │
        ▼
VerifiedInventoryEntry  +  ProductRepresentationRelationship
        │
        ▼
VerifiedInventoryManifest
        │
        ▼
ReplayProductPolicy  [future B3]
        │
        ▼
ReplayAssemblerV2  [future B3]
        │
        ▼
DataProduct  (models/data_product.py)
        │  source-agnostic GCSI operational model  ─── UNCHANGED
        │
        ▼
TelecomEngine / Evaluator / AI / Scheduler  (all unchanged)
```

---

## Key Domain Boundaries

### Archive existence / facts = EXTERNAL_AUTHORITATIVE

The fact that a product exists in the PDS archive and its observation timestamps
are **EXTERNAL_AUTHORITATIVE** facts, documented in `ProvenanceRecord` with:
- `kind = ProvenanceKind.EXTERNAL_AUTHORITATIVE`
- `validation_status = ProvenanceValidationStatus.VALIDATED`
- `content_sha256` = SHA-256 of the raw label bytes

### Replay queue membership = MODELED

Membership of a product in the GCSI historical replay queue is a **MODELED**
decision (future `ReplayProductPolicy`, Phase 6F-B3).  The `VerifiedInventoryManifest`
is a source/inventory artifact — it does NOT contain modeled replay priority scores.

### Archive file size ≠ historical downlink bytes

The `total_data_size_bytes` in `ArchiveScienceProduct` represents the archive
file size.  The replay transmission size (downlink bytes including telemetry
framing, compression, and protocol overhead) is a **MODELED** proxy, computed
by `ReplayProductPolicy` in Phase 6F-B3.

### DataProduct remains source-agnostic

`backend/app/models/data_product.py` is **NOT CHANGED** by Phase 6F-B1.
PDS source concerns (`pds_lidvid`, `archive_label_url`, `observation_stop`,
size policy, etc.) belong to the source/inventory/policy layers only.

### V1 is supported unchanged

All Phase 6E V1 infrastructure is preserved:
- `PdsArchiveLabelAdapter` (MWR-specific PDS4 adapter) — unchanged
- `PdsArchiveSnapshotStore` — unchanged
- `HistoricalReplayDescriptorV1` — unchanged
- `ReplayAssembler` V1 — unchanged
- `HistoricalReplayProvider` V1 — unchanged

V2 is **additive only**.

---

## New Modules (Phase 6F-B1)

| Module | Purpose |
|--------|---------|
| `mission_sources/archive_models.py` | Generic source domain models (Parts A–I) |
| `mission_sources/adapters/pds4_adapter.py` | Generic PDS4 observational label adapter |
| `mission_sources/adapters/pds3_adapter.py` | Generic PDS3 observational label adapter + bounded PVL parser |
| `mission_sources/snapshots/archive_label_snapshot.py` | Generic archive label snapshot store (PDS3 + PDS4) |

---

## archive_models.py — Key Types

### ArchiveSourceStandard
Enum: `PDS3` | `PDS4`

### ArchiveDataFileSizeCertainty
Size precision taxonomy:

| Value | Meaning | Scheduler-eligible |
|-------|---------|-------------------|
| `SIZE_DISCOVERED_APPROXIMATE` | From HTML directory listing | **NO** |
| `SIZE_METADATA_EXACT` | From authoritative label keyword | YES |
| `SIZE_SNAPSHOT_VERIFIED` | From verified GCSI snapshot | YES |

### ArchiveDataFile
One data file with name, size, size certainty, optional checksum pair, optional file_ref.

### ArchiveScienceProduct
Normalized external archive fact. Fields include:
- `source_record_id` — deterministic stable identity (see formula below)
- `source_standard` — PDS3 or PDS4
- `observation_start_utc`, `observation_stop_utc` — timezone-aware UTC
- `data_files` — tuple (file names unique within product)
- `total_data_size_bytes` — deterministic sum of data_files sizes

### source_record_id Formula

**PDS4:**
```
"pds4:" + lidvid
```
Example: `"pds4:urn:nasa:pds:juno_jiram:data_calibrated:jir_img_rec_2024165T055551_v01::1.0"`

**PDS3:**
```
"pds3:" + DATA_SET_ID + ":" + PRODUCT_ID [+ ":v" + PRODUCT_VERSION_ID]
```
Example: `"pds3:JNO-E/J/SS-WAV-3-CDR-BSTFULL-V2.0:WAV_2024165T055551_B_BIN:v01"`

Properties:
- Stable (does not depend on retrieval time or local path)
- Standard-distinguishable (`pds3:` / `pds4:`)
- Dataset-distinguishable
- Product-identity-distinguishable
- Version-distinguishable where the archive exposes a version
- Not random

### ArchiveCaptureRecord
Immutable capture of: `raw_label_bytes + ArchiveScienceProduct + ProvenanceRecord`.
Enforces: source_record_id consistency, EXTERNAL_AUTHORITATIVE kind, VALIDATED status,
timezone-aware retrieved_at, SHA-256(raw_bytes) == provenance.content_sha256.

### ProductRepresentationRelationship
Directed relationship between two source products by `source_record_id`.
Kinds: `SAME_OBSERVATION_ALTERNATE_PROCESSING`, `INDEPENDENT_ACQUISITION`,
`INDEPENDENT_TEMPORAL_SEGMENT`, `DERIVED_REPRESENTATION`, `COMPONENT_RELATION`.

Examples:
- JunoCam EDR ↔ RDR: `SAME_OBSERVATION_ALTERNATE_PROCESSING`
- WAVES Survey ↔ Burst: `INDEPENDENT_ACQUISITION`
- FGM standard ↔ PJ62: `INDEPENDENT_TEMPORAL_SEGMENT`

### VerifiedInventoryEntry
One logical GCSI replay candidate (may have multiple archive representations).
- `logical_product_id` — unique within manifest
- `representation_record_ids` — non-empty, no duplicates
- `availability_time_utc` — authoritative `observation_stop` (timezone-aware UTC)

### VerifiedInventoryManifest
Validated manifest of all logical V2 replay candidates.
- Unique `logical_product_id` values
- No cross-entry duplicate `representation_record_ids`
- Deterministic `manifest_id` (SHA-256 over sorted logical_product_ids)
- Maximum 16 MiB serialized size
- Not limited to exactly 411 entries

---

## PDS4 Adapter — Profile-Driven Validation

`GenericPds4ObservationalLabelAdapter` uses `GenericPds4AdapterProfile` to express
instrument-family-specific constraints.  No `if instrument == X` branches exist in
the generic parser.

Profile fields specify: `allowed_hosts`, `allowed_path_prefixes`,
`expected_instrument`, `instrument_lid`, `spacecraft_host_lid`,
`investigation_lid`, `product_family`, `allowed_processing_levels`,
`allowed_information_model_versions`.

Pre-built profiles for B1: `JIRAM_PDS4_PROFILE`, `UVS_PDS4_PROFILE`,
`MWR_GENERIC_PDS4_PROFILE`.

### PDS4 Security Posture
- HTTPS only (profile `allowed_hosts`)
- No redirects
- Max 2 MiB response
- Strict UTF-8 decode
- NUL byte rejection (before decode)
- UTF-8 BOM rejection
- XML declaration encoding must be UTF-8
- DOCTYPE rejection (case-insensitive, on decoded text)
- ENTITY rejection (case-insensitive, on decoded text)
- No XInclude, no schemaLocation fetch, no network in parser
- SHA-256 of exact raw bytes before normalization

The XML security scanner (`_scan_xml_security`) is **reused from V1**
(`pds_archive.py`) without copy/paste, proving V1 behavior unchanged.

---

## PDS3 Adapter — Bounded PVL Subset Parser

`GenericPds3ObservationalLabelAdapter` uses `GenericPds3AdapterProfile` with the same
profile-driven design.

**PDS3 parser decision:** No external PVL library is available in the dependency set
(`pydantic`, `fastapi`, `httpx`, etc.). A bounded subset parser is implemented that:
- Handles keyword = value assignments (quoted, unquoted, set/list values)
- Handles OBJECT/END_OBJECT blocks at top level
- Handles `^POINTER` keywords for payload file references
- Explicitly rejects NUL bytes
- Never silently misparsing unknown syntax (unknown keywords are skipped)
- Does not require an external dependency

### PDS3 File Size Derivation

Priority order:
1. `FILE_SIZE` keyword → `SIZE_METADATA_EXACT`
2. `RECORD_BYTES × FILE_RECORDS` formula → `SIZE_METADATA_EXACT`
   (only when both keywords are present and ASCII decimal)
3. Neither → `SIZE_DISCOVERED_APPROXIMATE` (0 bytes; NOT scheduler-eligible)

`SIZE_DISCOVERED_APPROXIMATE` must NEVER be silently promoted to `SIZE_METADATA_EXACT`
without explicit re-derivation from authoritative label metadata.

Pre-built profiles for B1: `WAVES_BURST_PDS3_PROFILE`, `WAVES_SURVEY_PDS3_PROFILE`,
`JUNOCAM_PDS3_PROFILE`, `FGM_PDS3_PROFILE`, `JADE_PDS3_PROFILE`, `JEDI_PDS3_PROFILE`.

---

## Snapshot Store — Generic (archive_label_snapshot.py)

`ArchiveLabelSnapshotStore` supports both PDS3 and PDS4 captures.
The V1 `PdsArchiveSnapshotStore` is **preserved unchanged**.

### Provenance ID Formula
```
identity = JSON({
    "adapter": "gcsi:generic_pds4_label:v1",  # or :generic_pds3_label:v1
    "source_record_id": ...,
    "source_ref": ...
}, sort_keys=True)

provenance_id = SHA-256(identity + "|" + content_sha256)
```

### Snapshot ID Formula
```
SHA-256(
    "gcsi.archive_label_snapshot:v1:"
    + source_standard_value + ":"
    + provenance_id + ":"
    + retrieved_at_utc_iso
)
```

### Zero-Network Reload Proof

`ArchiveLabelSnapshotStore.load()`:
1. Reads the local snapshot file (bounded: max 4 MiB + 1 bytes)
2. Decodes UTF-8
3. Parses JSON
4. Validates Pydantic envelope
5. Strict Base64 decodes raw bytes (`validate=True`)
6. Verifies SHA-256(raw bytes) == `raw_label_sha256`
7. Verifies SHA-256(raw bytes) == `provenance.content_sha256`
8. Verifies `retrieved_at` consistency
9. Re-runs the **same** pure parser (zero network)
10. Compares re-derived product == stored product
11. Compares re-derived provenance == stored provenance
12. Recomputes snapshot_id and verifies match

No network calls at any step.  Trust originates from the original validated
live acquisition, not from the stored hash alone.

---

## What Phase 6F-B2/B3 Will Add

**B2 — Bulk acquisition:**
- Acquire all 411 live source labels
- Populate `VerifiedInventoryManifest` with all 411 entries
- Capture new Horizons PJ62 state

**B3 — Replay construction:**
- `ReplayProductPolicy` (modeled size proxy, deadline, criticality)
- `ReplayAssemblerV2` (assembles full 411-product DataProduct list)
- Runtime activation of V2 replay
