"""
conftest.py — Configuración compartida para todos los tests.

Limpia variables de proxy del entorno y mockea el cliente OpenAI
antes de que cualquier test importe harness.py, evitando errores de
conexión en entornos de CI o con proxies SOCKS configurados.
"""
import os
import sys
from unittest.mock import MagicMock, patch

# ── 1. Limpiar proxies que bloquean httpx en CI ──────────────────────────────
for _var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
             "ALL_PROXY", "all_proxy", "SOCKS_PROXY", "socks_proxy"):
    os.environ.pop(_var, None)

# ── 2. Asegurar que DEEPSEEK_API_KEY exista (valor dummy para tests) ─────────
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy-key-for-unit-tests")

# ── 3. Mockear openai.OpenAI ANTES de que harness.py lo instancie ────────────
#    Esto evita cualquier conexión real a la red durante los tests.
_mock_openai_instance = MagicMock()
_openai_patcher = patch("openai.OpenAI", return_value=_mock_openai_instance)
_openai_patcher.start()

# No llamamos _openai_patcher.stop() intencionalmente —
# el mock debe persistir durante toda la sesión de tests.
