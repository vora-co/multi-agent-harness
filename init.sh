#!/bin/bash
set -e
echo "=== DeepSeek Harness - init ==="

echo "[1/4] Instalando dependencias de requirements.txt..."
pip3 install -r requirements.txt --quiet
echo "  OK: dependencias instaladas"

echo "[2/4] Instalando browsers de Playwright..."
python3 -m playwright install chromium --with-deps 2>/dev/null || \
  python3 -m playwright install chromium 2>/dev/null || \
  echo "  AVISO: playwright browsers no instalados (solo afecta tests E2E)"

echo "[3/4] Verificando estructura del proyecto..."
for f in feature_list.json AGENTS.md CHECKPOINTS.md progress/current.md; do
  [ -f "$f" ] && echo "  OK: $f" || (echo "  FALTA: $f" && exit 1)
done

echo "[4/4] Corriendo tests existentes..."
if ls tests/test_*.py 2>/dev/null | grep -q .; then
  python3 -m pytest tests/ -q --tb=short
else
  echo "  Sin tests aún — OK para sesión inicial"
fi

echo ""
echo "=== Todo listo. Ejecuta: python3 harness.py ==="
