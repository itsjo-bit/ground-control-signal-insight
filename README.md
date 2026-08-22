# Ground Control Signal Insight (GCSI)

AI-powered communication decision support for spacecraft missions.

> When bandwidth becomes a mission constraint, GCSI decides what matters most.

---

## Quick Start

### 1. Install backend dependencies

```bash
cd backend
pip install -e ".[dev]"
```

### 2. Configure Granite credentials

```bash
cp .env.example .env
# Edit .env and set GCSI_GRANITE_API_KEY to your IBM watsonx.ai API key.
```

See [`.env.example`](.env.example) for all available environment variables and
how to obtain an IBM watsonx.ai API key.

The `/agent/recommend` endpoint requires a valid `GCSI_GRANITE_API_KEY`.
All other endpoints (`/state`, `/queue`, `/plans/*`, `/simulate*`, `/approve`)
are fully operational without it.

### 3. Load a scenario and start the server

```bash
cd backend
GCSI_SCENARIO_PATH=../data/scenarios/nominal_pass.json uvicorn app.main:app --reload --port 8000
```

### 4. Run tests

```bash
# From ground-control-signal-insight/
python -m pytest                          # all tests (live Granite test skipped)
python -m pytest -m "not granite"         # skip tests requiring API key
python -m pytest tests/unit/test_agent.py # Phase 6 agent tests only
```

### 5. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

---

## Architecture summary

| Layer | Responsibility |
|-------|---------------|
| **Deterministic Python** | RF link calculations (Eb/N0, BER, goodput), packet scheduling, plan evaluation, stochastic simulation |
| **IBM Granite** | Structured reasoning over pre-computed facts; produces an `AIRecommendation` with evidence citations |

Granite receives serialized `LinkState`, `MissionState`, `CandidatePlans`, and `EvaluationResults` as
context. It reasons over these facts to recommend a plan. It never performs RF or scheduling
calculations — those remain entirely in deterministic Python code.

See [`docs/telecom_model.md`](docs/telecom_model.md) for the telecom model reference.
