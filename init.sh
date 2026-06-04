#!/bin/bash
set -e
echo "=== Multi-Agent Harness - init ==="

echo "[1/3] Installing dependencies from requirements.txt..."
pip3 install -r requirements.txt --quiet
echo "  OK: dependencies installed"

echo "[2/3] Installing Playwright browsers..."
python3 -m playwright install chromium --with-deps 2>/dev/null || \
  python3 -m playwright install chromium 2>/dev/null || \
  echo "  WARNING: playwright browsers not installed (only affects E2E tests)"

echo "[3/3] Running existing tests..."
if ls tests/test_*.py 2>/dev/null | grep -q .; then
  python3 -m pytest tests/ -q --tb=short
else
  echo "  No tests yet — OK for initial session"
fi

echo ""
echo "=== All set. Run: python3 harness.py ==="
