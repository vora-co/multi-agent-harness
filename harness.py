import os, json, time, logging, datetime
from openai import OpenAI
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich import print as rprint
import agents.leader as leader_cfg
import agents.implementer as impl_cfg
import agents.reviewer as reviewer_cfg
import agents.e2e_tester as e2e_cfg
from tools import execute_tool

load_dotenv()

MODEL   = "deepseek-v4-pro"   # opciones: deepseek-v4-flash | deepseek-v4-pro
VERBOSE = True

# ─── CONFIGURACIÓN DE ROBUSTEZ ───────────────────────────────────────────────
MAX_RETRIES_API    = 3   # Reintentos ante errores transitorios de la API (rate limit, timeout)
MAX_RETRIES_IMPL   = 3   # Cuántas veces el implementer puede reintentar una feature
MAX_RETRIES_REVIEW = 2   # Cuántas veces el ciclo impl→review puede repetirse antes de marcar "failed"
MAX_ITER_LEADER    = 20  # Iteraciones máximas del loop del leader
MAX_ITER_AGENT     = 15  # Iteraciones máximas de run_agent (implementer/reviewer/e2e)
RETRY_BACKOFF      = [2, 4, 8]  # segundos entre retries de API

# Compactación de contexto — mejores prácticas 2025:
# Modelos de 64K tokens: compactar cuando el historial supera ~30% del contexto.
# Conservador: disparar a los 24 mensajes (~12 intercambios), mantener los últimos 8.
COMPACT_THRESHOLD  = 24  # mensajes acumulados antes de compactar
COMPACT_KEEP_TAIL  = 8   # mensajes recientes a preservar intactos tras compactar

# Precios DeepSeek v3 (USD por millón de tokens, cache miss):
_PRICE_INPUT  = 0.27 / 1_000_000
_PRICE_OUTPUT = 1.10 / 1_000_000

# ─── LOGGING ESTRUCTURADO ───────────────────────────────────────────────────
logging.basicConfig(
    filename="progress/harness.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def _log(role: str, event: str, detail: str = "", level: str = "info"):
    msg = f"[{role.upper()}] {event}" + (f" | {detail}" if detail else "")
    getattr(logging, level)(msg)
    if VERBOSE and level in ("warning", "error"):
        console.print(f"  [dim red]{msg}[/]")

console = Console()

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com"
)

# ─── OBSERVABILIDAD DE COSTOS ────────────────────────────────────────────────
_SESSION_COSTS: dict = {
    "leader":       {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "implementer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "reviewer":     {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "e2e_tester":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "compaction":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
}
_SESSION_START = datetime.datetime.now()

def _track_usage(role: str, usage) -> None:
    """Acumula tokens de cada llamada a la API por rol."""
    if usage is None:
        return
    bucket = _SESSION_COSTS.get(role, _SESSION_COSTS["leader"])
    bucket["prompt_tokens"]     += getattr(usage, "prompt_tokens", 0)
    bucket["completion_tokens"] += getattr(usage, "completion_tokens", 0)
    bucket["calls"]             += 1

def _write_session_costs() -> None:
    """Escribe el resumen de costos de la sesión en progress/session_costs.json."""
    total_prompt     = sum(v["prompt_tokens"]     for v in _SESSION_COSTS.values())
    total_completion = sum(v["completion_tokens"] for v in _SESSION_COSTS.values())
    total_cost_usd   = total_prompt * _PRICE_INPUT + total_completion * _PRICE_OUTPUT

    summary = {
        "session_start":      _SESSION_START.isoformat(),
        "session_end":        datetime.datetime.now().isoformat(),
        "model":              MODEL,
        "by_role":            _SESSION_COSTS,
        "totals": {
            "prompt_tokens":     total_prompt,
            "completion_tokens": total_completion,
            "total_tokens":      total_prompt + total_completion,
            "estimated_usd":     round(total_cost_usd, 6),
        }
    }
    os.makedirs("progress", exist_ok=True)
    path = "progress/session_costs.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    console.print(Panel(
        f"Total tokens: [cyan]{total_prompt + total_completion:,}[/]  |  "
        f"Costo estimado: [yellow]USD {total_cost_usd:.4f}[/]",
        title="[dim]Costos de sesión → progress/session_costs.json[/]",
        border_style="dim",
        padding=(0, 1)
    ))

# ─── CHECKPOINTING ──────────────────────────────────────────────────────────

def recover_stale_features() -> list[int]:
    """
    Al arrancar, detecta features atascadas en 'in_progress' por un crash anterior
    y las resetea a 'pending'. Retorna lista de IDs recuperados.
    """
    try:
        with open("feature_list.json", "r") as f:
            features = json.load(f)
    except FileNotFoundError:
        return []

    recovered = []
    for feat in features:
        if feat.get("status") == "in_progress":
            feat["status"] = "pending"
            feat["updated_at"] = datetime.datetime.now().isoformat()
            feat["recovery_note"] = "Reseteada a pending por harness tras arranque (posible crash previo)"
            recovered.append(feat["id"])

    if recovered:
        with open("feature_list.json", "w") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
        _log("harness", "CHECKPOINT_RECOVERY",
             f"Features reseteadas a pending: {recovered}", level="warning")
        console.print(Panel(
            f"[yellow]Features {recovered} estaban en 'in_progress' — reseteadas a 'pending'[/]\n"
            "[dim]Posible crash en sesión anterior. El leader las retomará.[/]",
            title="[yellow]⚠ Checkpoint Recovery[/]",
            border_style="yellow",
            padding=(0, 1)
        ))
    return recovered

# ─── COMPACTACIÓN DE CONTEXTO ────────────────────────────────────────────────

def _compact_messages(messages: list, role: str) -> list:
    """
    Cuando el historial supera COMPACT_THRESHOLD mensajes, resume el bloque
    intermedio en una sola entrada para evitar exceder el context window.
    Siempre conserva: system (0), tarea inicial (1), y los últimos COMPACT_KEEP_TAIL.
    """
    if len(messages) <= COMPACT_THRESHOLD:
        return messages

    system_msg   = messages[0]
    initial_task = messages[1]
    tail         = messages[-COMPACT_KEEP_TAIL:]
    middle       = messages[2:-COMPACT_KEEP_TAIL]

    if not middle:
        return messages

    # Construir texto del bloque medio para resumir
    middle_text = ""
    for m in middle:
        role_label = m.get("role", "?").upper()
        content = m.get("content") or ""
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        middle_text += f"[{role_label}]: {str(content)[:300]}\n"

    _log(role, "COMPACTING",
         f"Compactando {len(middle)} mensajes intermedios (total={len(messages)})")

    try:
        summary_response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system",
                 "content": "Eres un asistente técnico. Resume de forma concisa el historial de trabajo de un agente de software."},
                {"role": "user",
                 "content": (
                     "Resume este historial en máximo 400 palabras. Preserva: "
                     "decisiones de diseño tomadas, herramientas ejecutadas y sus resultados clave, "
                     "errores encontrados y cómo se resolvieron, estado actual del trabajo.\n\n"
                     f"{middle_text}"
                 )}
            ],
            max_tokens=500,
        )
        _track_usage("compaction", summary_response.usage)
        summary_text = summary_response.choices[0].message.content or "(sin resumen)"
    except Exception as e:
        summary_text = f"(resumen no disponible: {e})"

    compact_msg = {
        "role": "system",
        "content": f"## Resumen de contexto anterior\n{summary_text}"
    }

    compacted = [system_msg, initial_task, compact_msg] + list(tail)
    _log(role, "COMPACTED",
         f"Reducido de {len(messages)} a {len(compacted)} mensajes")
    return compacted

# ─── UTILIDADES ─────────────────────────────────────────────────────────────

def _safe_parse_args(raw: str, tool_name: str) -> tuple[dict | None, str]:
    """Parsea argumentos JSON de una tool call. Retorna (args, error_msg)."""
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as e:
        err = f"JSON inválido en args de '{tool_name}': {e}"
        _log("harness", "PARSE_ERROR", err, level="error")
        return None, err

def _classify_error(error_msg: str) -> str:
    """
    Clasifica un error para decidir la estrategia de retry.
    TRANSIENT → reintentable con backoff (rate limit, timeout de red)
    LOGICAL   → requiere cambio de enfoque (error de lógica, test falla)
    FATAL     → detener (credenciales, archivo no encontrado crítico)
    """
    msg = error_msg.lower()
    if any(k in msg for k in ("rate limit", "timeout", "connection", "503", "502", "429")):
        return "TRANSIENT"
    if any(k in msg for k in ("max_iter", "blocked", "assertion", "error:")):
        return "LOGICAL"
    return "FATAL"

# ─── MOTOR DE AGENTE GENÉRICO ───────────────────────────────────────────────

def run_agent(system_prompt: str, tools: list, task: str,
              role: str = "agente", color: str = "white",
              max_iter: int = MAX_ITER_AGENT) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": task}
    ]
    _log(role, "START", task[:120])

    for i in range(max_iter):
        # Retry ante errores transitorios de API
        api_response = None
        for attempt in range(MAX_RETRIES_API):
            try:
                api_response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                )
                break
            except Exception as e:
                err_type = _classify_error(str(e))
                if err_type == "TRANSIENT" and attempt < MAX_RETRIES_API - 1:
                    wait = RETRY_BACKOFF[attempt]
                    _log(role, "API_RETRY", f"intento {attempt+1}/{MAX_RETRIES_API} — espera {wait}s — {e}", level="warning")
                    time.sleep(wait)
                else:
                    _log(role, "API_FATAL", str(e), level="error")
                    return f"[ERROR API: {e}]"

        if api_response is None:
            return "[ERROR: no se obtuvo respuesta de la API]"

        _track_usage(role, api_response.usage)
        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log(role, "DONE", (msg.content or "")[:120])
            return msg.content or ""

        messages.append(msg)
        messages = _compact_messages(messages, role)

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args, parse_err = _safe_parse_args(tc.function.arguments, fn_name)

            if fn_args is None:
                # Devolver el error al agente para que corrija
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": parse_err})
                })
                continue

            if VERBOSE:
                if msg.content:
                    console.print(f"  [italic dim]{msg.content[:120]}[/]")
                args_preview = json.dumps(fn_args, ensure_ascii=False)[:200]
                console.print(Panel(
                    f"[bold]Action:[/]  [cyan]{fn_name}[/]\n[dim]{args_preview}[/]",
                    title=f"[{color}]Paso {i+1} — {fn_name}[/]",
                    border_style=color,
                    padding=(0, 1)
                ))

            _log(role, "TOOL_CALL", f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")
            result = execute_tool(fn_name, fn_args)
            _log(role, "TOOL_RESULT", result[:200])

            if VERBOSE:
                console.print(Panel(
                    f"[dim]{result[:400]}[/]",
                    title=f"[yellow]Observation — Paso {i+1}[/]",
                    border_style="yellow",
                    padding=(0, 1)
                ))

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result
            })

    _log(role, "MAX_ITER", f"Alcanzado el límite de {max_iter} iteraciones", level="warning")
    return f"[ERROR: max_iter {max_iter} alcanzado]"


# ─── SPAWNERS ────────────────────────────────────────────────────────────────

def spawn_implementer(feature_id: int, description: str, attempt: int = 1,
                      rejection_reason: str = "") -> str:
    """
    Lanza el implementer. Si es un reintento, inyecta el motivo de rechazo
    para que el agente no cometa el mismo error.
    """
    context = ""
    if attempt > 1 and rejection_reason:
        context = (
            f"\n\n⚠️  REINTENTO #{attempt} — El reviewer rechazó el intento anterior.\n"
            f"Razón del rechazo: {rejection_reason}\n"
            f"Debes corregir exactamente esos puntos antes de volver a reportar."
        )

    console.print(Panel(
        f"[bold]Feature #{feature_id}[/] [dim](intento {attempt}/{MAX_RETRIES_IMPL})[/]\n"
        f"[dim]{description[:200]}[/]",
        title="[blue]>> IMPLEMENTER activo[/]",
        border_style="blue",
        padding=(0, 1)
    ))
    _log("implementer", "SPAWN", f"feature={feature_id} attempt={attempt}")

    task = (
        f"Implementa la feature #{feature_id}: {description}{context}\n"
        f"Escribe tu reporte en progress/impl_{feature_id}.md\n"
        f"Devuelve solo la ruta del archivo cuando termines."
    )
    result = run_agent(impl_cfg.SYSTEM_PROMPT, impl_cfg.TOOLS, task,
                       role="implementer", color="blue")
    console.print(Panel(
        f"[dim]{result[:200]}[/]",
        title="[blue]<< IMPLEMENTER terminó[/]",
        border_style="blue",
        padding=(0, 1)
    ))
    return result


def spawn_reviewer(feature_id: int) -> str:
    console.print(Panel(
        f"Revisando feature [bold]#{feature_id}[/]",
        title="[magenta]>> REVIEWER activo[/]",
        border_style="magenta",
        padding=(0, 1)
    ))
    _log("reviewer", "SPAWN", f"feature={feature_id}")

    task = (
        f"Revisa el trabajo del implementer para la feature #{feature_id}.\n"
        f"El reporte del implementer está en progress/impl_{feature_id}.md\n"
        f"Escribe tu veredicto en progress/review_{feature_id}.md\n"
        f"Devuelve SOLO: 'APPROVED' o 'REJECTED: <razón>'"
    )
    result = run_agent(reviewer_cfg.SYSTEM_PROMPT, reviewer_cfg.TOOLS, task,
                       role="reviewer", color="magenta")

    approved = result.strip().startswith("APPROVED")
    color = "green" if approved else "red"
    _log("reviewer", "VERDICT", result[:200], level="info" if approved else "warning")
    console.print(Panel(
        f"[bold]{result[:200]}[/]",
        title=f"[{color}]<< REVIEWER veredicto[/]",
        border_style=color,
        padding=(0, 1)
    ))
    return result


def spawn_e2e_tester(feature_id: int) -> str:
    console.print(Panel(
        f"Tests E2E para feature [bold]#{feature_id}[/]",
        title="[yellow]>> E2E_TESTER activo[/]",
        border_style="yellow",
        padding=(0, 1)
    ))
    _log("e2e_tester", "SPAWN", f"feature={feature_id}")

    task = (
        f"Ejecuta los tests E2E para la feature #{feature_id}.\n"
        f"El reporte del implementer está en progress/impl_{feature_id}.md\n"
        f"Escribe tu reporte en progress/e2e_{feature_id}.md\n"
        f"Devuelve SOLO: 'E2E_PASSED' o 'E2E_FAILED: <razón>'"
    )
    result = run_agent(e2e_cfg.SYSTEM_PROMPT, e2e_cfg.TOOLS, task,
                       role="e2e_tester", color="yellow")

    passed = result.strip().startswith("E2E_PASSED")
    color  = "green" if passed else "red"
    _log("e2e_tester", "VERDICT", result[:200], level="info" if passed else "warning")
    console.print(Panel(
        f"[bold]{result[:200]}[/]",
        title=f"[{color}]<< E2E_TESTER veredicto[/]",
        border_style=color,
        padding=(0, 1)
    ))
    return result


def run_feature_cycle(feature_id: int, description: str) -> dict:
    """
    Ciclo completo: impl → e2e → review con reintentos.
    Flujo:
      1. Implementer escribe código + tests unitarios.
      2. E2E Tester valida con Playwright (flujos de usuario reales).
      3. Reviewer verifica todo: unit tests, mutation score, e2e y checkpoints.
    Si el reviewer rechaza, reintenta desde el paso 1 con el motivo inyectado.
    Retorna dict con: approved (bool), attempts (int), final_verdict (str).
    """
    rejection_reason = ""
    for attempt in range(1, MAX_RETRIES_REVIEW + 1):

        # ── Paso 1: Implementar ──────────────────────────────────────────────
        impl_result = spawn_implementer(
            feature_id, description,
            attempt=attempt,
            rejection_reason=rejection_reason
        )
        if "[ERROR" in impl_result.upper():
            err_type = _classify_error(impl_result)
            _log("harness", "IMPL_ERROR",
                 f"feature={feature_id} type={err_type} detail={impl_result[:200]}", level="error")
            if err_type == "FATAL":
                return {"approved": False, "attempts": attempt, "final_verdict": impl_result}
            rejection_reason = impl_result
            continue

        # ── Paso 2: E2E Testing ──────────────────────────────────────────────
        e2e_result = spawn_e2e_tester(feature_id)
        if e2e_result.strip().startswith("E2E_FAILED"):
            e2e_reason = e2e_result.replace("E2E_FAILED:", "").strip()
            _log("harness", "E2E_FAILED",
                 f"feature={feature_id} attempt={attempt} reason={e2e_reason[:100]}", level="warning")
            # E2E failure cuenta como rejection — el implementer corrige
            rejection_reason = f"E2E falló: {e2e_reason}"
            if attempt < MAX_RETRIES_REVIEW:
                console.print(Panel(
                    f"[red]E2E falló — reintentando impl (intento {attempt+1}/{MAX_RETRIES_REVIEW})[/]\n"
                    f"[dim]{e2e_reason[:200]}[/]",
                    title=f"[red]↻ E2E → impl — feature #{feature_id}[/]",
                    border_style="red", padding=(0, 1)
                ))
            continue

        # ── Paso 3: Revisar ──────────────────────────────────────────────────
        review_result = spawn_reviewer(feature_id)
        if review_result.strip().startswith("APPROVED"):
            return {"approved": True, "attempts": attempt, "final_verdict": review_result}

        rejection_reason = review_result.replace("REJECTED:", "").strip()
        _log("harness", "CYCLE_RETRY",
             f"feature={feature_id} attempt={attempt}/{MAX_RETRIES_REVIEW} reason={rejection_reason[:100]}",
             level="warning")
        if attempt < MAX_RETRIES_REVIEW:
            console.print(Panel(
                f"[yellow]Reviewer rechazó — reintento {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n"
                f"[dim]{rejection_reason[:200]}[/]",
                title=f"[yellow]↻ Ciclo impl→e2e→review — feature #{feature_id}[/]",
                border_style="yellow", padding=(0, 1)
            ))

    return {
        "approved": False,
        "attempts": MAX_RETRIES_REVIEW,
        "final_verdict": f"REJECTED tras {MAX_RETRIES_REVIEW} intentos: {rejection_reason}"
    }


# ─── LOOP DEL LEADER ─────────────────────────────────────────────────────────

def run_leader(user_task: str) -> str:
    console.print(Panel(
        f"[dim]{user_task}[/]",
        title="[green]>> LEADER activo[/]",
        border_style="green",
        padding=(0, 1)
    ))

    LEADER_TOOLS = leader_cfg.TOOLS + [
        {
            "type": "function",
            "function": {
                "name": "run_feature_cycle",
                "description": (
                    "Ejecuta el ciclo completo implementar → revisar para una feature. "
                    f"Reintenta automáticamente hasta {MAX_RETRIES_REVIEW} veces si el reviewer rechaza. "
                    "Devuelve JSON con: approved (bool), attempts (int), final_verdict (str). "
                    "Úsalo en lugar de llamar spawn_implementer y spawn_reviewer por separado."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "feature_id":  {"type": "integer", "description": "ID de la feature"},
                        "description": {"type": "string",  "description": "Descripción completa de la tarea"}
                    },
                    "required": ["feature_id", "description"]
                }
            }
        }
    ]

    messages = [
        {"role": "system", "content": leader_cfg.SYSTEM_PROMPT},
        {"role": "user",   "content": user_task}
    ]

    _log("leader", "START", user_task[:120])

    for iteration in range(MAX_ITER_LEADER):
        # Retry ante errores de API del leader
        api_response = None
        for attempt in range(MAX_RETRIES_API):
            try:
                api_response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    tools=LEADER_TOOLS,
                    tool_choice="auto",
                )
                break
            except Exception as e:
                err_type = _classify_error(str(e))
                if err_type == "TRANSIENT" and attempt < MAX_RETRIES_API - 1:
                    wait = RETRY_BACKOFF[attempt]
                    _log("leader", "API_RETRY", f"intento {attempt+1} — espera {wait}s — {e}", level="warning")
                    time.sleep(wait)
                else:
                    _log("leader", "API_FATAL", str(e), level="error")
                    return f"[ERROR API leader: {e}]"

        if api_response is None:
            return "[ERROR: leader no obtuvo respuesta de la API]"

        _track_usage("leader", api_response.usage)
        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log("leader", "DONE", (msg.content or "")[:120])
            return msg.content or ""

        messages.append(msg)
        messages = _compact_messages(messages, "leader")

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            fn_args, parse_err = _safe_parse_args(tc.function.arguments, fn_name)

            if fn_args is None:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"error": parse_err})
                })
                continue

            if VERBOSE and fn_name != "run_feature_cycle":
                args_preview = json.dumps(fn_args, ensure_ascii=False)[:200]
                console.print(Panel(
                    f"[bold]Action:[/]  [cyan]{fn_name}[/]\n[dim]{args_preview}[/]",
                    title=f"[green]leader — {fn_name}[/] iter {iteration+1}",
                    border_style="green",
                    padding=(0, 1)
                ))

            _log("leader", "TOOL_CALL", f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")

            if fn_name == "run_feature_cycle":
                cycle_result = run_feature_cycle(**fn_args)
                result = json.dumps(cycle_result, ensure_ascii=False)
            else:
                result = execute_tool(fn_name, fn_args)
                if VERBOSE:
                    console.print(Panel(
                        f"[dim]{result[:300]}[/]",
                        title="[yellow]Observation[/]",
                        border_style="yellow",
                        padding=(0, 1)
                    ))

            _log("leader", "TOOL_RESULT", result[:200])
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result
            })

    _log("leader", "MAX_ITER", f"Alcanzado el límite de {MAX_ITER_LEADER} iteraciones", level="error")
    return f"[ERROR: leader max_iter {MAX_ITER_LEADER} alcanzado]"


# ─── REPL ─────────────────────────────────────────────────────────────────────

def print_features():
    with open("feature_list.json", "r") as f:
        features = json.load(f)
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID",     style="dim",    width=4)
    table.add_column("Estado", width=14)
    table.add_column("Título")
    color_map = {"pending": "white", "in_progress": "cyan", "done": "green", "failed": "red"}
    for feat in features:
        status = feat["status"]
        color  = color_map.get(status, "white")
        table.add_row(
            str(feat["id"]),
            f"[{color}]{status}[/]",
            feat["title"]
        )
    console.print(table)


def main():
    console.print(Panel(
        f"[bold white]DeepSeek Multi-Agent Harness[/]\n"
        f"Modelo: [cyan]{MODEL}[/]\n"
        f"Roles:  [green]Leader[/] → [blue]Implementer[/] → [yellow]E2E Tester[/] → [magenta]Reviewer[/]\n"
        f"[dim]Comandos: /salir | /estado | /features | /costos[/]",
        border_style="white",
        padding=(1, 2)
    ))

    # Checkpointing: recuperar features atascadas de sesiones anteriores
    recover_stale_features()

    try:
        while True:
            try:
                user_input = console.input("[bold white]Tú →[/] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Saliendo...[/]")
                break

            if not user_input:
                continue

            if user_input == "/salir":
                break
            elif user_input == "/estado":
                with open("progress/current.md", "r") as f:
                    console.print(Markdown(f.read()))
                continue
            elif user_input == "/features":
                print_features()
                continue
            elif user_input == "/costos":
                _write_session_costs()
                continue

            result = run_leader(user_input)
            console.print(Panel(
                f"{result}",
                title="[green]Leader — resultado final[/]",
                border_style="green",
                padding=(0, 1)
            ))
    finally:
        # Siempre escribir costos al salir, incluso si hay crash
        _write_session_costs()


if __name__ == "__main__":
    main()