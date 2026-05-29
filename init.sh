#!/bin/bash
set -e
echo "=== DeepSeek Harness - init check ==="

echo "[1/3] Verificando dependencias..."
python3 -c "import openai, dotenv" && echo "  OK: openai, python-dotenv"

echo "[2/3] Verificando estructura..."
for f in feature_list.json AGENTS.md CHECKPOINTS.md progress/current.md; do
  [ -f "$f" ] && echo "  OK: $f" || (echo "  FALTA: $f" && exit 1)
done

echo "[3/3] Corriendo tests..."
if ls tests/test_*.py 2>/dev/null | grep -q .; then
  python3 -m pytest tests/ -va
else
  echo "  Sin tests aun - OK para sesion inicial"
fi

echo ""
echo "=== Todo verde. Puedes iniciar. ==="