# GCSI development startup — Windows PowerShell
# Usage: .\scripts\start-dev.ps1
#
# Starts the backend and prints frontend instructions.
# Run from the ground-control-signal-insight\ project root.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Dependency checks ─────────────────────────────────────────────────────────

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "ERROR: Python not found. Install Python 3.11+ and retry."
    exit 1
}

if (-not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "ERROR: uvicorn not found." -ForegroundColor Red
    Write-Host "       Run: cd backend; pip install -e '.[dev]'" -ForegroundColor Yellow
    exit 1
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "ERROR: Node.js not found. Install Node.js 18+ and retry."
    exit 1
}

# ── Provider / credential info ────────────────────────────────────────────────

Write-Host ""
Write-Host "  GCSI Development Server" -ForegroundColor Cyan
Write-Host "  ========================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Default scenario: ASTERIA-7 (1,284 products, thermal anomaly)"
Write-Host "  Default provider: Local (no API key required)"
Write-Host ""

if ($env:GCSI_GRANITE_API_KEY) {
    Write-Host "  AI provider: IBM Granite (GCSI_GRANITE_API_KEY is set)" -ForegroundColor Green
} elseif ($env:GCSI_GEMINI_API_KEY) {
    Write-Host "  AI provider: Google Gemini (GCSI_GEMINI_API_KEY is set)" -ForegroundColor Green
} else {
    Write-Host "  AI provider: Local deterministic (offline — no API key needed)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Backend  -> http://localhost:8000"
Write-Host "  Frontend -> http://localhost:5173  (start separately — see below)"
Write-Host "  Health   -> http://localhost:8000/health"
Write-Host ""
Write-Host "  To start the frontend in a second terminal:" -ForegroundColor Cyan
Write-Host "    cd frontend; npm install; npm run dev"
Write-Host ""

# ── Backend startup ───────────────────────────────────────────────────────────

Write-Host "  Starting backend..." -ForegroundColor Cyan
Write-Host ""

Set-Location backend
uvicorn app.main:app --reload --port 8000
