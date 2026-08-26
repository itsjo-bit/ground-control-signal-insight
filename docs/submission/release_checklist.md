# GCSI Release Checklist

Complete this checklist before final competition submission.

---

## Repository State

- [ ] `git status` — working tree clean
- [ ] `main` branch pushed to remote
- [ ] No `.env` file committed (check with `git ls-files | grep "\.env$"`)
- [ ] No API keys, IAM tokens, or passwords in tracked files

## CI

- [ ] GitHub Actions CI workflow exists at `.github/workflows/ci.yml`
- [ ] CI passes on `main` (backend tests + frontend typecheck + build)
- [ ] CI does not run `--execute-live` benchmark calls

## Installation (clean test)

- [ ] Fresh Python 3.11+ environment: `python -m venv .venv && .venv\Scripts\Activate.ps1`
- [ ] Backend install: `cd backend && pip install -e ".[dev]"`
- [ ] Frontend install: `cd frontend && npm ci`
- [ ] Backend starts cleanly: `uvicorn app.main:app --reload --port 8000` (from backend/)
- [ ] ASTERIA-7 banner shows: 1,284 products, thermal anomaly, 608.000 s one-way signal
- [ ] `/health` returns `scenario_loaded: true`, `data_products_count: 1284`
- [ ] Frontend starts: `cd frontend && npm run dev`
- [ ] Frontend loads at `http://localhost:5173`

## Local Mode Demo (no API keys)

- [ ] GCSI_AI_PROVIDER=local (or no keys set)
- [ ] ASTERIA-7 mission overview loads
- [ ] Data Products panel shows 1,284 products
- [ ] Click "Analyze" — AI lifecycle runs with Local provider
- [ ] AI recommendation appears with provider label "Local"
- [ ] Fallback banner absent (Local is not a fallback in this test — it's the configured provider)
- [ ] Approve Transmission → plan uplink animation
- [ ] Transmission sequence plays
- [ ] Ground Reception panel updates with evidence

## AI Fallback Test

- [ ] Set an invalid API key: `GCSI_GRANITE_API_KEY=invalid`
- [ ] Click Analyze — provider fails, Local fallback activates
- [ ] Fallback banner visible and clearly labeled
- [ ] Recommendation still available (Local deterministic)
- [ ] Operator approval still works

## Manual Mode Test

- [ ] Switch to Manual mode
- [ ] Browse data products, filter by thermal subsystem
- [ ] Select 3–5 products
- [ ] Click "Evaluate Selection" — deterministic assessment appears
- [ ] Click "Transmit Selected" — transmission plays

## Reset Test

- [ ] After a full AI-assisted run, click Reset
- [ ] Mission resets to initial state
- [ ] AI lifecycle returns to STANDBY
- [ ] Data products count unchanged
- [ ] Transmission panel clears

## Scenario Switch Test

- [ ] Switch to `mission_data_v3.json` from Config
- [ ] Mission state updates (150 products, 3 anomalies)
- [ ] AI triage works with 150 products
- [ ] Switch back to ASTERIA-7

## Backend Tests

- [ ] `python -m pytest tests/ -q` — all tests pass (no live API tests)
- [ ] Zero test failures
- [ ] Record exact counts: X passed, Y skipped, Z warnings

## Frontend Tests

- [ ] `npx tsc --noEmit` — zero TypeScript errors
- [ ] `npm run build` — production build succeeds
- [ ] Build output in `frontend/dist/`

## Documentation

- [ ] README intro accurately describes ASTERIA-7 (1,284 products, 10m08s)
- [ ] README AI boundary section accurate
- [ ] README Scientific Evaluation Status section honest
- [ ] README Project Limitations section present
- [ ] `docs/architecture.md` present with Mermaid diagram
- [ ] `docs/api_overview.md` present
- [ ] `docs/benchmark_methodology.md` frozen (not modified)
- [ ] `docs/asteria7_demo.md` accurate
- [ ] `docs/trust_boundary.md` current
- [ ] `docs/telecom_model.md` current
- [ ] No stale screenshots showing old "150 products" UI
- [ ] No stale screenshots showing broken manual mode

## Benchmark Integrity

- [ ] `benchmarks/configs/gcsi_benchmark_v1.json` — UNCHANGED from original (verify SHA)
- [ ] `data/scenarios/mission_data_v3.json` — UNCHANGED from original
- [ ] `data/scenarios/asteria7_thermal_priority_contact_v1.json` — UNCHANGED from original
- [ ] Failed auth pilot result clearly marked as NOT benchmark evidence
- [ ] README benchmark status correctly states "not yet executed"

## Submission Package

- [ ] `docs/submission/submission_summary.md` present (50/150/300-word versions)
- [ ] `docs/submission/problem_solution.md` present
- [ ] `docs/submission/technical_innovation.md` present
- [ ] `docs/submission/responsible_ai.md` present
- [ ] `docs/submission/demo_script.md` present (90-second + 3-minute)
- [ ] `docs/submission/pitch_outline.md` present
- [ ] `docs/submission/judge_questions.md` present
- [ ] `docs/submission/architecture_one_liner.md` present
- [ ] `docs/submission/granite_benchmark_preflight.md` present
- [ ] `docs/submission/release_checklist.md` present (this file)

## Granite Benchmark Status

- [ ] **PENDING** — official Granite benchmark not yet executed
- [ ] If executed: results in `benchmarks/results/<official-run-id>/`
- [ ] If executed: README Scientific Evaluation Status updated with actual results
- [ ] If executed: no claims beyond what data actually shows

## Demo Recording

- [ ] Demo video captured at 1920×1080, browser zoom 100%
- [ ] No DevTools visible
- [ ] No personal paths visible
- [ ] Provider label visible and correct
- [ ] No API keys visible in any panel
- [ ] Ground reception evidence update captured
- [ ] 90-second version complete
- [ ] 3-minute version complete (optional)

## Final Submission

- [ ] Clean `git status`
- [ ] All Phase 5 commits pushed
- [ ] Submission package reviewed
- [ ] Competition submission form filled

---

*GCSI Release Checklist — Phase 5*
