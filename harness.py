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
from tools import execute_tool

load_dotenv()

MODEL   = "deepseek-v4-pro"   # opciones: deepseek-v4-flash | deepseek-v4-pro
VERBOSE = True

# ─── CONFIGURACIÓN DE ROBUSTEZ ───────────────────────────────────────────────
MAX_RETRIES_API    = 3   # Reintentos ante errores transitorios de la API (rate limit, timeout)
MAX_RETRIES_IMPL   = 3   # Cuántas veces el implementer puede reintentar una feature
MAX_RETRIES_REVIEW = 2   # Cuántas veces el ciclo impl→review puede repetirse antes de marcar "failed"
MAX_ITER_LEADER    = 20  # Iteraciones máximas del loop del leader
MAX_ITER_AGENT     = 15  # Iteraciones máximas de run_agent (implementer/reviewer)
RETRY_BACKOFF      = [2, 4, 8]  # segundos entre retries de API

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

        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log(role, "DONE", (msg.content or "")[:120])
            return msg.content or ""

        messages.append(msg)

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


def run_feature_cycle(feature_id: int, description: str) -> dict:
    """
    Ciclo completo impl → review con reintentos.
    Retorna dict con: approved (bool), attempts (int), final_verdict (str).
    """
    rejection_reason = ""
    for attempt in range(1, MAX_RETRIES_REVIEW + 1):
        # Implementar
        impl_result = spawn_implementer(
            feature_id, description,
            attempt=attempt,
            rejection_reason=rejection_reason
        )

        # Si el implementer mismo reportó bloqueo
        if "[ERROR" in impl_result.upper():
            err_type = _classify_error(impl_result)
            _log("harness", "IMPL_ERROR", f"feature={feature_id} type={err_type} detail={impl_result[:200]}", level="error")
            if err_type == "FATAL":
                return {"approved": False, "attempts": attempt, "final_verdict": impl_result}
            # LOGICAL/TRANSIENT → reintentar con contexto
            rejection_reason = impl_result
            continue

        # Revisar
        review_result = spawn_reviewer(feature_id)

        if review_result.strip().startswith("APPROVED"):
            return {"approved": True, "attempts": attempt, "final_verdict": review_result}

        # REJECTED — extraer razón y reintentar si quedan intentos
        rejection_reason = review_result.replace("REJECTED:", "").strip()
        _log("harness", "CYCLE_RETRY",
             f"feature={feature_id} attempt={attempt}/{MAX_RETRIES_REVIEW} reason={rejection_reason[:100]}",
             level="warning")

        if attempt < MAX_RETRIES_REVIEW:
            console.print(Panel(
                f"[yellow]Reintento {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n[dim]{rejection_reason[:200]}[/]",
                title=f"[yellow]↻ Ciclo impl→review — feature #{feature_id}[/]",
                border_style="yellow",
                padding=(0, 1)
            ))

    return {"approved": False, "attempts": MAX_RETRIES_REVIEW, "final_verdict": f"REJECTED tras {MAX_RETRIES_REVIEW} intentos: {rejection_reason}"}


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

        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log("leader", "DONE", (msg.content or "")[:120])
            return msg.content or ""

        messages.append(msg)

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
        f"Roles:  [green]Leader[/] → [blue]Implementer[/] → [magenta]Reviewer[/]\n"
        f"[dim]Comandos: /salir | /estado | /features[/]",
        border_style="white",
        padding=(1, 2)
    ))

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

        result = run_leader(user_input)
        console.print(Panel(
            f"{result}",
            title="[green]Leader — resultado final[/]",
            border_style="green",
            padding=(0, 1)
        ))


if __name__ == "__main__":
    main()