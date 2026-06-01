#!/bin/bash
# run_e2e.sh — Ejecuta los tests E2E de Playwright
#
# REQUISITO: el backend y el frontend deben estar corriendo antes de ejecutar este script.
#
#   Terminal 1: python3 -m uvicorn src.api:app --reload --port 8000
#   Terminal 2: cd frontend && npm run dev
#   Terminal 3: bash run_e2e.sh

set -e

BACKEND_URL="http://localhost:8000"
FRONTEND_URL="http://localhost:5173"

echo "=== Tests E2E — Yoga App ==="
echo ""

# Verificar que el backend esté corriendo
echo "[1/3] Verificando backend ($BACKEND_URL)..."
if curl -s --max-time 3 "$BACKEND_URL/docs" > /dev/null 2>&1; then
    echo "  ✓ Backend activo"
else
    echo "  ✗ Backend no responde en $BACKEND_URL"
    echo "    Levántalo con: python3 -m uvicorn src.api:app --reload --port 8000"
    exit 1
fi

# Verificar que el frontend esté corriendo
echo "[2/3] Verificando frontend ($FRONTEND_URL)..."
if curl -s --max-time 3 "$FRONTEND_URL" > /dev/null 2>&1; then
    echo "  ✓ Frontend activo"
else
    echo "  ✗ Frontend no responde en $FRONTEND_URL"
    echo "    Levántalo con: cd frontend && npm run dev"
    exit 1
fi

# Limpiar datos de corridas anteriores para evitar colisiones
echo "[3/3] Limpiando datos de tests anteriores..."
rm -f data/users.json data/sessions.json data/bookings.json data/credit_transactions.json data/notifications.json
echo "  ✓ data/ limpia"
echo ""
echo "[4/4] Ejecutando tests Playwright..."
echo ""

cd frontend

if [ "$1" == "--headed" ]; then
    npx playwright test --headed
else
    npx playwright test
fi
