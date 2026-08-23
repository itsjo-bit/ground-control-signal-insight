# Ground Control Signal Insight (GCSI)

AI-powered communication decision support for spacecraft missions.

> When bandwidth becomes a mission constraint, GCSI decides what matters most.

---

## AI Provider

GCSI uses a **provider-agnostic AI layer** for plan recommendations.  No paid API key is required for the default development/demo path.

| Provider | When used | Requirements |
|----------|-----------|--------------|
| **Local** (default) | `GCSI_GRANITE_API_KEY` is not set | None — works offline, zero dependencies |
| **Ollama** (opt-in) | `GCSI_OLLAMA_ENABLED=true` and Ollama is reachable | [Ollama](https://ollama.com) running locally |
| **IBM Granite** (optional) | `GCSI_GRANITE_API_KEY` is set | IBM watsonx.ai account |

The **Local** provider is a deterministic rule-based reasoner that evaluates all four candidate plans using the same pre-computed `EvaluationResult` metrics and produces a valid, explainable `AIRecommendation` — no fabrication, no mocks, no network calls.

To use IBM Granite, you need **two** credentials from IBM Cloud:

| Variable | Where to find it | Required? |
|---|---|---|
| `GCSI_GRANITE_API_KEY` | [IBM Cloud → IAM → API keys](https://cloud.ibm.com/iam/apikeys) | Yes |
| `GCSI_GRANITE_PROJECT_ID` | watsonx.ai → your project → Manage → General → Project ID | Yes |
| `GCSI_GRANITE_API_URL` | Change region prefix if your project is not in `us-south` | No (default: us-south) |
| `GCSI_GRANITE_MODEL_ID` | Override only if you have a different Granite model | No (default: `ibm/granite-3-3-8b-instruct`) |

```bash
cp .env.example .env
# Edit .env — set GCSI_GRANITE_API_KEY and GCSI_GRANITE_PROJECT_ID.
# Never commit .env to version control.
```

The `?version=2023-05-29` query parameter is added to the endpoint URL automatically; you do not need to include it in `GCSI_GRANITE_API_URL` unless you want to pin a different version.

> **Security note**: keep `GCSI_GRANITE_API_KEY` and `GCSI_GRANITE_PROJECT_ID` secret. Never paste them into chat, logs, or source code.

To use Ollama:
```bash
# Install and start Ollama (https://ollama.com), pull a model, then:
GCSI_OLLAMA_ENABLED=true GCSI_OLLAMA_MODEL=llama3.2 uvicorn app.main:app ...
```

---

## Quick Start

### 1. Install backend dependencies

```bash
cd backend
pip install -e ".[dev]"
```

### 2. Configure credentials (IBM Granite)

Create a `.env` file in the project root (one level above `backend/`).
The server loads it automatically at startup — you do not need to export variables manually.

```bash
# From ground-control-signal-insight/
cp .env.example .env
# Open .env and set:
#   GCSI_GRANITE_API_KEY=<your IBM Cloud API key>
#   GCSI_GRANITE_PROJECT_ID=<your watsonx.ai project UUID>
#   GCSI_GRANITE_MODEL_ID=ibm/granite-4-h-small   # or ibm/granite-3-3-8b-instruct if available
```

Skip this step to run with the **Local** rule-based provider (no credentials required).

### 3. Load a scenario and start the server

```bash
cd backend
# .env is loaded automatically — no need to export variables in the shell.
GCSI_SCENARIO_PATH=../data/scenarios/nominal_pass.json uvicorn app.main:app --reload --port 8000
```

On Windows PowerShell:

```powershell
# From ground-control-signal-insight\backend\
$env:GCSI_SCENARIO_PATH = "..\data\scenarios\nominal_pass.json"
uvicorn app.main:app --reload --port 8000
```

> Granite credentials in `.env` are loaded by the application at startup.
> You do **not** need to set them in the shell.

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

```
Raw inputs (scenario JSON)
       ↓
TelecomEngine  ←  formulas.py  (single source of truth for all RF math)
       ↓
LinkState
       ↓
CandidateGenerator  →  4 CandidatePlans  +  telecom_decisions metadata
       ↓
PlanEvaluator  →  4 EvaluationResults  (deterministic, no RNG)
       ↓
AI Provider  (Local | Ollama | Granite)
       ↓
AIRecommendation  (validated — plan_id, evidence fields, confidence/risk bounds)
       ↓
Human approval
       ↓
TransmissionSimulator  →  SimulationResult  (stochastic, seed-controlled)
```

| Layer | Responsibility |
|-------|---------------|
| **Deterministic Python** | RF link calculations (Eb/N0, BER, goodput), packet scheduling, plan evaluation, stochastic simulation |
| **AI Layer (provider-agnostic)** | Structured reasoning over pre-computed facts; produces an `AIRecommendation` with evidence citations |

The AI layer never performs RF or scheduling calculations.  All metrics are pre-computed
by the deterministic pipeline before the AI provider is invoked.

See [`docs/telecom_model.md`](docs/telecom_model.md) for the telecom model reference.
