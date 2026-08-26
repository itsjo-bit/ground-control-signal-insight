#!/usr/bin/env bash
# GCSI development startup — Unix/macOS
# Usage: bash scripts/start-dev.sh
#
# Starts the backend in one terminal and prints frontend instructions.
# Run from the ground-control-signal-insight/ project root.

set -euo pipefail

# ── Dependency checks ────────────────────────────────────────────────────────

if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
  echo "ERROR: Python not found. Install Python 3.11+ and retry."
  exit 1
fi

PYTHON=${PYTHON:-python3}
if ! command -v "$PYTHON" &>/dev/null; then
  PYTHON=python
fi

if ! command -v uvicorn &>/dev/null; then
  echo "ERROR: uvicorn not found."
  echo "       Run: cd backend && pip install -e '.[dev]'"
  exit 1
fi

if ! command -v node &>/dev/null; then
  echo "ERROR: Node.js not found. Install Node.js 18+ and retry."
  exit 1
fi

# ── Provider / credential info ───────────────────────────────────────────────

echo ""
echo "  GCSI Development Server"
echo "  ========================"
echo ""
echo "  Default scenario: ASTERIA-7 (1,284 products, thermal anomaly)"
echo "  Default provider: Local (no API key required)"
echo ""

if [ -n "${GCSI_GRANITE_API_KEY:-}" ]; then
  echo "  AI provider: IBM Granite (GCSI_GRANITE_API_KEY is set)"
elif [ -n "${GCSI_GEMINI_API_KEY:-}" ]; then
  echo "  AI provider: Google Gemini (GCSI_GEMINI_API_KEY is set)"
else
  echo "  AI provider: Local deterministic (offline — no API key needed)"
fi

echo ""
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173  (start separately — see below)"
echo "  Health   → http://localhost:8000/health"
echo ""

# ── Backend startup ──────────────────────────────────────────────────────────

echo "  Starting backend..."
echo "  (Run 'cd frontend && npm install && npm run dev' in a second terminal)"
echo ""

cd backend
uvicorn app.main:app --reload --port 8000
