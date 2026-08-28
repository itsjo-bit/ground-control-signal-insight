# Juno PJ62 Historical Replay — Operator & Judge Demo Guide

> **This guide is self-contained.** It does not require reading the Phase 6E reports or
> any other internal engineering documents to follow.

---

## What This Demo Is

GCSI's historical replay mode constructs a real decision scenario from verified archival
mission data. This demonstration uses NASA Juno's 62nd Jupiter perijove (PJ62) flyby as
its mission anchor.

**What it shows:**
- A real spacecraft, at a real epoch, at a verified real distance from the Sun
- Two real archival science products whose metadata is frozen from NASA's Planetary Data System
- A modeled communication scenario created by GCSI — not reconstructed from any NASA DSN record
- Deterministic telecom analysis, plan generation, and AI advisory — all operating on the
  reconstructed scenario exactly as they do in the synthetic demo

**What it does not show:**
- Live spacecraft telemetry
- Any reconstruction of what NASA actually transmitted during PJ62
- NASA-reported SNR, data rate, or link quality
- The actual transmission queue from the Juno mission
- Any NASA-originated priority score or anomaly classification

This distinction is critical and explicitly preserved throughout the UI, API, and documentation.

---

## Source Artifact Inventory

All source data is committed to the repository as read-only verified snapshots. No network
access is required or used during replay.

### JPL Horizons geometry snapshot

```
data/verified_snapshots/horizons/juno/
juno_spk_-61_2024-06-14T035955.483000Z.json
```

Acquired from the JPL Horizons ephemeris system via the official SBDB/Horizons API.
Frozen at epoch 2024-06-14T03:59:55.483000Z — the PJ62 decision epoch.

### NASA PDS archive — IRDR product

```
data/verified_snapshots/pds_archive/juno_mwr/pj62/
mwr62ri2024166030000_r04112_v04_3.0.json
```

Metadata snapshot of the Juno MWR PJ62 Instrument Reduced Data Record (IRDR).
The IRDR is the primary radiometric science product for PJ62.

### NASA PDS archive — GRDR product

```
data/verified_snapshots/pds_archive/juno_mwr/pj62/
mwr62rg2024166030000_r04112_v04_3.0.json
```

Metadata snapshot of the Juno MWR PJ62 Geometry Reduced Data Record (GRDR).
The GRDR is the companion geometry and ancillary data product for PJ62.

### Replay descriptor

```
data/replays/juno_pj62_mwr_v1.json
```

GCSI replay descriptor that references the three snapshots above and defines
the modeled communication policy parameters. This is the file passed to
`GCSI_REPLAY_DESCRIPTOR` at startup.

---

## Verified Authoritative Facts

The following values are frozen from the verified archival snapshots. They are not
estimated, modeled, or rounded.

| Field | Value | Source |
|---|---|---|
| Decision epoch | 2024-06-14T03:59:55.483000Z | JPL Horizons |
| Juno–Sun distance | 893,345,396.8038701 km | JPL Horizons |
| IRDR file size | 6,694,664 bytes (53,557,312 bits) | NASA PDS |
| GRDR file size | 5,093,997 bytes (40,751,976 bits) | NASA PDS |
| IRDR LIDVID | `urn:nasa:pds:juno-mwr:data-raw:mwr62ri2024166030000_r04112_v04::3.0` | NASA PDS |
| GRDR LIDVID | `urn:nasa:pds:juno-mwr:data-raw:mwr62rg2024166030000_r04112_v04::3.0` | NASA PDS |

**What IRDR and GRDR mean:**
- **IRDR** — Instrument Reduced Data Record: the primary calibrated microwave radiometry
  science data for a perijove pass. This is the high-value science product.
- **GRDR** — Geometry Reduced Data Record: the companion ancillary product containing
  geometry and orientation data used to interpret the IRDR.

**What is NOT frozen from NASA:**
- The PDS archive preserves product labels and file sizes. It does not record or publish
  historical transmission queue membership, DSN pass records, or ground-commanded
  downlink priority. Those do not exist in the public PDS archive for this product.
- CSV science payload bytes are not authenticated. Only the label metadata and file size
  are preserved in the snapshot.

---

## Provenance Categories

Every value in the API response is classified into one of three categories:

| Category | Meaning | Examples |
|---|---|---|
| **external_authoritative** | Directly from a verified NASA/JPL external source | Distance, product sizes, epoch, LIDVIDs |
| **derived** | Computed deterministically from authoritative inputs (GCSI formulas only) | Propagation delay, capacity calculation |
| **modeled** | GCSI communication policy assumption — not from NASA | SNR, data rate, link stability, decision window |

The `/state` API response includes `provenance_kind_counts`:
```json
{
  "external_authoritative": 3,
  "derived": 13,
  "modeled": 1
}
```

---

## Modeled Communication Policy

These values are **GCSI assumptions**. They are never presented as NASA-reported data.

| Parameter | Value | Notes |
|---|---|---|
| `snr_db` | 3.0 dB | GCSI modeled parameter |
| `rssi_dbm` | -95.0 dBm | GCSI modeled parameter |
| `nominal_data_rate_bps` | 100,000 bps | GCSI modeled parameter |
| `link_stability` | 0.8 | GCSI modeled parameter |
| `protocol_latency_s` | 1.5 s | Modeled link-stack overhead |
| `decision_window_s` | 900 s | GCSI modeled parameter |
| `risk_score` | 0.35 | GCSI modeled parameter |
| IRDR priority attributes | criticality, scientific_value, etc. | GCSI policy |
| GRDR priority attributes | criticality, scientific_value, etc. | GCSI policy |

**Protocol latency vs. signal propagation — important distinction:**

| Field | Value | Meaning |
|---|---|---|
| `latency_s` | 1.5 s | Modeled link-stack protocol overhead (ARQ, processing) |
| `propagation_delay_s` | ~2979.879 s (~49.7 min) | Physical one-way signal travel time from authoritative distance |

These are intentionally different values representing different physical quantities.
The UI displays them separately and they must not be conflated.

---

## Why There Is a Decision Problem

Using the modeled communication window and GCSI goodput:

```
available_capacity_bits = 900 s × 90,000 bps goodput = 81,000,000 bits

IRDR queued: 53,557,312 bits  (fits individually)
GRDR queued: 40,751,976 bits  (fits individually)
Combined:    94,309,288 bits  > 81,000,000 bits  ← does NOT fit
```

Both products cannot be transmitted sequentially within the modeled window.
The operator must choose which product to prioritize or accept partial delivery.
This capacity pressure is intentional — it is the core decision the scenario demonstrates.

---

## Starting the Historical Replay

### Environment configuration

```bash
# Required
GCSI_SOURCE_MODE=historical_replay
GCSI_REPLAY_DESCRIPTOR=data/replays/juno_pj62_mwr_v1.json

# Recommended for offline demo
GCSI_AI_PROVIDER=local

# Explicitly clear external providers (process-local only — do not modify .env)
GCSI_GRANITE_API_KEY=
GCSI_GEMINI_API_KEY=
GCSI_OLLAMA_ENABLED=
```

### Backend startup (from project root)

**Linux / macOS:**
```bash
export GCSI_SOURCE_MODE=historical_replay
export GCSI_REPLAY_DESCRIPTOR=data/replays/juno_pj62_mwr_v1.json
export GCSI_AI_PROVIDER=local
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

**Windows PowerShell:**
```powershell
$env:GCSI_SOURCE_MODE = "historical_replay"
$env:GCSI_REPLAY_DESCRIPTOR = "data/replays/juno_pj62_mwr_v1.json"
$env:GCSI_AI_PROVIDER = "local"
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

### Verify startup banner

The startup output must show:
```
[GCSI] Source mode      : HISTORICAL REPLAY
[GCSI] Provider         : GCSI-HistoricalReplayProvider
[GCSI] Mission          : JUNO
[GCSI] Data products    : 2
[GCSI] Geometry         : available
[GCSI] Provenance       : 17 source-lineage records
[GCSI] Replay semantics : reconstructed historical scenario
[GCSI]                    NOT live spacecraft telemetry
[GCSI] Data origin      : NASA/JPL/PDS facts + explicit GCSI
[GCSI]                    modeled communications policy
```

If this banner does not appear, check that `GCSI_SOURCE_MODE` and `GCSI_REPLAY_DESCRIPTOR`
are set correctly in the process environment.

### Frontend (same as any mode)

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in a browser.

---

## API Verification Checklist

After startup, verify these exact values via the API:

### GET /health

Expected:
```json
{
  "status": "ok",
  "source_mode": "historical_replay",
  "historical_replay_active": true,
  "source_provenance_available": true
}
```

### GET /state (key fields)

| Field | Expected Value |
|---|---|
| `source.mode` | `historical_replay` |
| `source.is_historical_replay` | `true` |
| `source.provenance_scope` | `source_baseline` |
| `source.provenance_kind_counts.external_authoritative` | `3` |
| `source.provenance_kind_counts.derived` | `13` |
| `source.provenance_kind_counts.modeled` | `1` |
| `mission_state.mission_id` | `JUNO` |
| `distance_km` | `893345396.8038701` |
| `link_state.snr_db` | `3.0` |
| `link_state.nominal_data_rate_bps` | `100000.0` |
| `link_state.link_goodput_bps` | `90000.0` |
| `link_state.latency_s` | `1.5` |
| `link_state.remaining_window_s` | `900.0` |
| `available_capacity_bits` | `81000000` |
| `queued_data_bits` | `94309288` |
| `propagation_delay_s` | `~2979.879` |

Verify: `queued_data_bits (94,309,288) > available_capacity_bits (81,000,000)` — this is the
capacity pressure that drives the decision.

### GET /data-products

Expected: exactly 2 products in stable order:

| Product ID | Size (bits) | Role |
|---|---|---|
| `JUNO-MWR-PJ62-IRDR` | 53,557,312 | Primary radiometric science product |
| `JUNO-MWR-PJ62-GRDR` | 40,751,976 | Geometry/ancillary companion product |

### POST /plans/generate

Expected: 4 deterministic plans, all with IRDR ranked first.

### POST /agent/recommend (local provider)

Expected: 200 response with `actual_provider: Local` and advisory recommendation.
The AI operates in advisory mode — it does not modify state automatically.
Human approval is required for any transmission.

### POST /state/reset

Expected:
```json
{
  "source_mode": "historical_replay",
  "randomized": false,
  "scenario_path": null
}
```

Historical reset is deterministic — the same exact baseline is restored every time.
This is different from synthetic scenario reset, which introduces random jitter.

---

## 3–5 Minute Demo Script

### 0:00–0:30 — Establish mission context and not-live boundary

> "GCSI's historical replay mode loads a real mission context — Juno's 62nd
> Jupiter flyby, June 2024. The geometry and product sizes come from verified
> archival sources: JPL Horizons and NASA's Planetary Data System.
>
> This is not live telemetry. GCSI is reconstructing a decision scenario from
> archived facts, with communication constraints that GCSI models explicitly.
> We're not claiming this is what NASA transmitted."

*Show: HISTORICAL REPLAY banner and source provenance section in the UI.*

---

### 0:30–1:00 — Show source and provenance context

> "The provenance panel shows exactly what came from NASA and what GCSI models.
> Three records are external-authoritative: the Horizons geometry snapshot and
> the two PDS product label snapshots. The modeled parameters — SNR, data rate,
> decision window — are clearly labeled as GCSI policy."

*Show: Source context banner. Point to provenance count display.*
*Show: Protocol Latency (1.5 s) and Signal Propagation Delay (~49.7 min) as separate labeled values.*

---

### 1:00–1:30 — Show link geometry and capacity shortfall

> "Juno is 893 million kilometers from the Sun at this epoch — a verified JPL
> value. The physical one-way signal travel time is about 49 minutes 40 seconds.
>
> With the modeled 900-second window and 90,000 bps goodput, we have 81 million
> bits of available capacity. The two queued MWR products total 94 million bits —
> they don't both fit."

*Show: distance_km, available_capacity_bits, queued_data_bits — capacity shortfall visible.*

---

### 1:30–2:15 — Show the two MWR products and their different decision value

> "The IRDR — the Instrument Reduced Data Record — is the primary science product.
> It has higher scientific value and criticality. The GRDR is the companion geometry
> record — important for context, but ranked second.
>
> Both product sizes come directly from NASA PDS metadata — 6.7 MB and 5.1 MB
> respectively. The priority attributes are GCSI's modeling."

*Show: /data-products panel with JUNO-MWR-PJ62-IRDR and JUNO-MWR-PJ62-GRDR.*
*Show: size, scientific_value, criticality for each.*

---

### 2:15–3:00 — Generate deterministic baseline plans

> "GCSI generates four deterministic plans. Every plan ranks IRDR first — it's larger
> and has higher scientific value. The GRDR is deferred in all plans because together
> they exceed the modeled window."

*Click: Generate Plans. Show the 4 plans with IRDR first.*

---

### 3:00–4:00 — Run AI advisory analysis

> "Now the AI advisory layer. With the local provider, GCSI's rule-based reasoner
> evaluates the five candidate plans under the same deterministic metrics.
>
> Notice: the AI makes a recommendation, but does not execute it. Human approval
> is required."

*Click: Analyze / Agent Recommend. Show the recommendation panel.*
*Point out: actual_provider = Local, risk_level, recommended_plan_id.*
*Point out: recommendation is advisory — no transmission has occurred.*

---

### 4:00–4:30 — Emphasize deterministic evaluation and human authority

> "The key point: the telecom analysis is deterministic and authoritative. The AI
> interpretation is advisory. The risk scores, feasibility checks, and capacity
> calculations come from the backend — the AI does not invent them."

*Show: plan evaluation results with deterministic metrics.*
*Show: Approve button visible but not yet clicked — emphasizing human authority.*

---

### 4:30–5:00 — Optional: reset demonstration

> "GCSI's historical reset is deterministic. Unlike a synthetic scenario which
> re-randomizes on reset, the historical replay reloads exactly the same verified
> baseline every time. This is reproducibility by design."

*Click: Reset. Show source_mode = historical_replay, randomized = false.*
*Reload /state — show distance_km = 893345396.8038701 (identical).*

---

## Judge-Safe Claims

These are accurate claims supported by the verified implementation:

> "GCSI is not claiming to reproduce NASA's historical downlink decision.
> We use verified archival facts to anchor the mission context, then clearly
> label the communication constraints that GCSI models for the replay.
> AI helps interpret mission value, deterministic telecom logic decides
> feasibility, and the human operator retains final authority."

> "The distance and product sizes come from NASA's published archives.
> The SNR, data rate, and decision window are GCSI's modeled assumptions,
> clearly labeled as such in the API and UI."

> "Historical reset is deterministic — the same exact verified baseline
> reloads every time. This is intentional: the decision scenario is anchored
> to a specific real-world epoch."

---

## Claims to Avoid

Do NOT say:

> "This is live Juno telemetry."

> "This is what NASA actually chose to transmit."

> "NASA reported this SNR or data rate."

> "The PDS archive proves both files were queued together."

> "The AI controls transmission."

> "These priority values come from NASA."

> "The CSV science payload is authenticated."

---

## Offline Profile

For reliable demonstration without any external network dependency:

```
GCSI_AI_PROVIDER=local
```

This is the recommended demo configuration. The local provider is a deterministic
rule-based reasoner that produces a valid, explainable recommendation with no network
calls and no API keys.

The historical replay source acquisition (JPL Horizons geometry, PDS product metadata)
is already complete — the data is frozen in committed snapshots. No NASA API call is
made at runtime regardless of AI provider.

---

## Optional: Granite AI Profile

IBM Granite can be used as an alternative AI advisory provider:

```bash
GCSI_SOURCE_MODE=historical_replay
GCSI_REPLAY_DESCRIPTOR=data/replays/juno_pj62_mwr_v1.json
GCSI_AI_PROVIDER=granite
GCSI_GRANITE_API_KEY=<your IBM Cloud IAM key>
GCSI_GRANITE_PROJECT_ID=<your watsonx.ai project ID>
```

Granite is **not required** for:
- Historical source loading
- Telecom analysis
- Plan generation and evaluation
- Offline reproducibility

If Granite credentials are unavailable, use `GCSI_AI_PROVIDER=local` rather than
changing the historical replay data or modeled communication parameters.

Do not include real credentials in documentation, logs, or source code.

---

## Reset Semantics

| Reset type | Deterministic? | Notes |
|---|---|---|
| Historical replay reset | **Yes** — always identical | Reloads verified baseline; no jitter |
| Synthetic scenario reset | No — randomized | Introduces simulation jitter |

Historical reset verified: `POST /state/reset` returns `randomized: false, scenario_path: null`.
Re-reading `/state` after reset shows identical `distance_km: 893345396.8038701`.

---

## Switching from Historical to Synthetic

The scenario selector allows switching to a synthetic scenario during a session:

```
POST /scenarios/switch  {"filename": "mission_data_v3.json"}
```

After switching:
- `source.mode` = `synthetic_scenario`
- `source.is_historical_replay` = `false`
- `source.provenance_available` = `false`

The historical provenance banner disappears. The synthetic UI operates normally.

To return to historical replay, restart the server with the historical replay environment.
No in-session switch back to historical is needed for demo purposes.

---

## Known Limitations

These are documentation-level limitations, not defects:

- **No CSV science payload acquisition**: GCSI stores product label metadata and file
  sizes from PDS. The actual data files (CSV science content) are not downloaded or
  authenticated.
- **No DSN pass record**: The public PDS archive does not publish DSN pass records
  or historical downlink manifests. No historical transmission queue was reconstructed.
- **No original spacecraft packetization**: Juno MWR internal packetization is not
  publicly documented. GCSI models each PDS product as a single logical data unit.
- **Modeled communication constraints**: SNR, data rate, link stability, and decision
  window are GCSI policy — not NASA-reported values.
- **One PJ62 replay frozen**: Currently only the MWR PJ62 scenario is implemented.
  No other Juno instrument or pass is included.
- **No live NASA feed**: GCSI does not connect to any live NASA API at runtime.
- **Single decision epoch**: The replay is anchored to one specific epoch. No orbital
  propagation is performed at runtime.

---

## Files Added or Modified by Historical Replay Implementation

| Path | Role |
|---|---|
| `data/verified_snapshots/horizons/juno/juno_spk_-61_2024-06-14T035955.483000Z.json` | JPL Horizons geometry snapshot |
| `data/verified_snapshots/pds_archive/juno_mwr/pj62/mwr62ri2024166030000_r04112_v04_3.0.json` | PDS IRDR snapshot |
| `data/verified_snapshots/pds_archive/juno_mwr/pj62/mwr62rg2024166030000_r04112_v04_3.0.json` | PDS GRDR snapshot |
| `data/replays/juno_pj62_mwr_v1.json` | Replay descriptor |
| `backend/app/mission_sources/` | HistoricalReplayProvider, ReplayAssembler, snapshot stores |

---

*Juno PJ62 Historical Replay Demo Guide — Phase 6E-C8*
