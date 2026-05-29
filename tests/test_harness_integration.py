"""
Tests de integración del harness.

Validan la lógica de orquestación (retry, clasificación de errores, compactación,
checkpointing) usando mocks de la API — sin hacer llamadas reales a DeepSeek.
"""
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, mock_open

# ── Helpers para construir respuestas mock de la API ─────────────────────────

def _make_response(content: str = "", tool_calls: list = None, usage=(10, 5)):
    """Construye un objeto de respuesta que imita openai.ChatCompletion."""
    msg = MagicMock()
    msg.content    = content
    msg.tool_calls = tool_calls or []

    usage_obj = MagicMock()
    usage_obj.prompt_tokens     = usage[0]
    usage_obj.completion_tokens = usage[1]

    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]
    resp.usage   = usage_obj
    return resp


def _make_tool_call(name: str, args: dict):
    tc = MagicMock()
    tc.id = f"call_{name}"
    tc.function.name      = name
    tc.function.arguments = json.dumps(args)
    return tc


# ── _classify_error ──────────────────────────────────────────────────────────

def test_classify_transient_rate_limit():
    import harness
    assert harness._classify_error("rate limit exceeded") == "TRANSIENT"

def test_classify_transient_timeout():
    import harness
    assert harness._classify_error("connection timeout after 30s") == "TRANSIENT"

def test_classify_transient_503():
    import harness
    assert harness._classify_error("503 service unavailable") == "TRANSIENT"

def test_classify_logical_max_iter():
    import harness
    assert harness._classify_error("[ERROR: max_iter alcanzado]") == "LOGICAL"

def test_classify_fatal_unknown():
    import harness
    assert harness._classify_error("unexpected kernel panic in matrix multiplier") == "FATAL"


# ── _safe_parse_args ─────────────────────────────────────────────────────────

def test_safe_parse_args_valid():
    import harness
    args, err = harness._safe_parse_args('{"path": "src/foo.py"}', "write_file")
    assert args == {"path": "src/foo.py"}
    assert err == ""

def test_safe_parse_args_invalid_json():
    import harness
    args, err = harness._safe_parse_args("{invalid json}", "write_file")
    assert args is None
    assert "JSON inválido" in err


# ── _compact_messages ────────────────────────────────────────────────────────

def test_compact_messages_below_threshold():
    """Si hay menos mensajes que el umbral, no compacta."""
    import harness
    messages = [{"role": "system", "content": "sys"}] * 5
    result = harness._compact_messages(messages, "test_role")
    assert result == messages

def test_compact_messages_above_threshold():
    """Con historial largo, la lista resultante debe ser más corta."""
    import harness
    # Construir una lista artificial de 30 mensajes
    msgs = [{"role": "system", "content": "sys prompt"},
            {"role": "user",   "content": "tarea inicial"}]
    for i in range(28):
        msgs.append({"role": "assistant" if i % 2 == 0 else "tool",
                     "content": f"mensaje {i} con contenido largo " * 5})

    mock_resp = _make_response(content="Resumen compacto del trabajo realizado.")
    with patch("harness.client") as mock_client:
        mock_client.chat.completions.create.return_value = mock_resp
        result = harness._compact_messages(msgs, "implementer")

    # Debe conservar: system + tarea + summary + tail
    assert len(result) < len(msgs)
    assert result[0]["role"] == "system"   # system preservado
    assert result[1]["role"] == "user"     # tarea inicial preservada
    assert "Resumen" in result[2]["content"]  # bloque de resumen


# ── recover_stale_features ───────────────────────────────────────────────────

def test_recover_stale_features_resets_in_progress(tmp_path, monkeypatch):
    """Features atascadas en in_progress deben resetearse a pending."""
    import harness

    feature_data = [
        {"id": 1, "title": "Feature A", "status": "in_progress"},
        {"id": 2, "title": "Feature B", "status": "done"},
        {"id": 3, "title": "Feature C", "status": "pending"},
    ]
    feature_file = tmp_path / "feature_list.json"
    feature_file.write_text(json.dumps(feature_data))

    monkeypatch.chdir(tmp_path)
    (tmp_path / "progress").mkdir()

    recovered = harness.recover_stale_features()

    assert recovered == [1]
    updated = json.loads(feature_file.read_text())
    assert updated[0]["status"] == "pending"
    assert updated[0].get("recovery_note") is not None
    assert updated[1]["status"] == "done"    # no tocado
    assert updated[2]["status"] == "pending" # ya era pending, no tocado


def test_recover_stale_features_no_stale(tmp_path, monkeypatch):
    """Si no hay features atascadas, retorna lista vacía sin modificar el archivo."""
    import harness

    feature_data = [{"id": 1, "title": "A", "status": "pending"}]
    feature_file = tmp_path / "feature_list.json"
    feature_file.write_text(json.dumps(feature_data))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "progress").mkdir()

    recovered = harness.recover_stale_features()
    assert recovered == []


# ── _track_usage y costos ────────────────────────────────────────────────────

def test_track_usage_accumulates():
    """Los tokens deben acumularse correctamente por rol."""
    import harness
    # Resetear estado para este test
    harness._SESSION_COSTS["implementer"]["prompt_tokens"]     = 0
    harness._SESSION_COSTS["implementer"]["completion_tokens"] = 0
    harness._SESSION_COSTS["implementer"]["calls"]             = 0

    usage1 = MagicMock(prompt_tokens=100, completion_tokens=50)
    usage2 = MagicMock(prompt_tokens=200, completion_tokens=80)

    harness._track_usage("implementer", usage1)
    harness._track_usage("implementer", usage2)

    bucket = harness._SESSION_COSTS["implementer"]
    assert bucket["prompt_tokens"]     == 300
    assert bucket["completion_tokens"] == 130
    assert bucket["calls"]             == 2

def test_track_usage_none_safe():
    """_track_usage no debe fallar si usage es None."""
    import harness
    harness._track_usage("leader", None)  # no debe lanzar excepción


# ── run_agent — retry ante error TRANSIENT ───────────────────────────────────

def test_run_agent_retries_on_transient_error():
    """
    Si la API lanza un error de rate limit (TRANSIENT) en el primer intento,
    run_agent debe reintentar y eventualmente retornar el contenido.
    """
    import harness

    success_response = _make_response(content="Tarea completada.")
    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("rate limit exceeded")
        return success_response

    with patch("harness.client") as mock_client, \
         patch("harness.time.sleep"):  # acelerar el test
        mock_client.chat.completions.create.side_effect = fake_create
        result = harness.run_agent("sys", [], "tarea", role="implementer")

    assert result == "Tarea completada."
    assert call_count["n"] == 2  # falló 1 vez, reintentó 1 vez


def test_run_agent_fails_after_max_retries():
    """Si todos los retries fallan con TRANSIENT, debe retornar un mensaje de error."""
    import harness

    with patch("harness.client") as mock_client, \
         patch("harness.time.sleep"):
        mock_client.chat.completions.create.side_effect = Exception("429 rate limit")
        result = harness.run_agent("sys", [], "tarea", role="implementer")

    assert "[ERROR API" in result


# ── run_feature_cycle — lógica de reintentos ─────────────────────────────────

def test_run_feature_cycle_approved_on_first_attempt():
    """El ciclo debe retornar approved=True si impl+e2e+reviewer aprueban al primer intento."""
    import harness

    with patch("harness.spawn_implementer", return_value="progress/impl_1.md"), \
         patch("harness.spawn_e2e_tester",  return_value="E2E_PASSED"), \
         patch("harness.spawn_reviewer",    return_value="APPROVED"):
        result = harness.run_feature_cycle(1, "Descripción feature 1")

    assert result["approved"] is True
    assert result["attempts"] == 1


def test_run_feature_cycle_retries_on_e2e_failure():
    """Si E2E falla en el primer intento pero aprueba en el segundo, approved=True."""
    import harness

    e2e_calls = {"n": 0}
    def fake_e2e(feature_id):
        e2e_calls["n"] += 1
        return "E2E_PASSED" if e2e_calls["n"] > 1 else "E2E_FAILED: botón no encontrado"

    with patch("harness.spawn_implementer", return_value="progress/impl_1.md"), \
         patch("harness.spawn_e2e_tester",  side_effect=fake_e2e), \
         patch("harness.spawn_reviewer",    return_value="APPROVED"):
        result = harness.run_feature_cycle(1, "Feature con E2E")

    assert result["approved"] is True
    assert result["attempts"] == 2


def test_run_feature_cycle_fails_after_max_retries():
    """Si el reviewer rechaza todos los intentos, approved=False."""
    import harness

    with patch("harness.spawn_implementer", return_value="progress/impl_1.md"), \
         patch("harness.spawn_e2e_tester",  return_value="E2E_PASSED"), \
         patch("harness.spawn_reviewer",    return_value="REJECTED: cobertura insuficiente"):
        result = harness.run_feature_cycle(1, "Feature que siempre falla")

    assert result["approved"] is False
    assert result["attempts"] == harness.MAX_RETRIES_REVIEW
    assert "REJECTED" in result["final_verdict"]
