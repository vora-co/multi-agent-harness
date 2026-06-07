#!/bin/bash
set -e
echo "=== Multi-Agent Harness - init ==="

echo "[1/4] Installing dependencies from requirements.txt..."
pip3 install -r requirements.txt --quiet
echo "  OK: dependencies installed"

echo "[2/4] Installing Playwright browsers..."
python3 -m playwright install chromium --with-deps 2>/dev/null || \
  python3 -m playwright install chromium 2>/dev/null || \
  echo "  WARNING: playwright browsers not installed (only affects E2E tests)"

echo "[3/4] Checking sandbox runtime (SANDBOX_MODE=docker is the default — see README)..."
SANDBOX_MODE_VAL="$(grep -m1 '^SANDBOX_MODE=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
SANDBOX_MODE_VAL="${SANDBOX_MODE_VAL:-docker}"

if [ "$SANDBOX_MODE_VAL" = "local" ]; then
  echo "  SANDBOX_MODE=local in .env — skipping Docker setup. Agent shell commands"
  echo "  will run directly on this machine. (Remove that line, or set it to"
  echo "  'docker', to get container isolation — recommended.)"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "  OK: Docker daemon reachable ($(docker --version))"
  echo "  Building sandbox image 'harness-sandbox:latest' (first run only — installs Node 18,"
  echo "  Python deps, and Playwright's Chromium; can take 3-10 min depending on your connection)."
  echo "  Showing live build output below so you can see it's progressing:"
  echo ""
  docker build -t harness-sandbox:latest . && echo "  OK: sandbox image ready"
else
  echo "  No Docker daemon detected. The harness defaults to SANDBOX_MODE=docker,"
  echo "  which keeps agent-issued shell commands isolated in a container — without"
  echo "  it, those commands fall back to running directly on your machine with a warning."
  echo ""
  if command -v brew >/dev/null 2>&1; then
    echo "  Install one of these (pick one — all work as drop-in Docker CLIs):"
    echo "    brew install --cask orbstack     # fastest startup, free for commercial use"
    echo "    brew install colima docker       # CLI-only, fully open source"
    echo "    brew install --cask docker       # official Docker Desktop"
    echo ""
    echo "  Then open it once (OrbStack/Docker Desktop) or run 'colima start',"
    echo "  and re-run 'bash init.sh' to build the sandbox image."
  else
    echo "  Install Docker Desktop, OrbStack, or Colima, then re-run 'bash init.sh'."
    echo "  See: https://orbstack.dev  /  https://github.com/abiosoft/colima  /  https://docker.com/products/docker-desktop"
  fi
  echo ""
  echo "  (Or set SANDBOX_MODE=local in .env to opt out of sandboxing — not recommended.)"
fi

echo "[4/4] Running existing tests..."
if ls tests/test_*.py 2>/dev/null | grep -q .; then
  python3 -m pytest tests/ -q --tb=short
else
  echo "  No tests yet — OK for initial session"
fi

echo ""
echo "=== All set. Run: python3 harness.py ==="
