import os, json, time, logging, datetime, subprocess, sys

# ─── AUTO-INSTALACIÓN DE DEPENDENCIAS ────────────────────────────────────────
def _ensure_deps():
    """
    Verifica e instala todo lo necesario antes de arrancar.
    Solo corre cuando algo falta — en sesiones normales es instantáneo.
    """
    missing = []
    checks = {
        "fastapi":    "fastapi",
        "uvicorn":    "uvicorn",
        "jose":       "python-jose[cryptography]",
        "passlib":    "passlib[bcrypt]",
        "playwright": "playwright",
        "pytest":     "pytest",
        "httpx":      "httpx",
    }
    for module, package in checks.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"📦 Instalando dependencias faltantes: {', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"]
        )
        print("✓ Dependencias instaladas.\n")

    # Instalar browsers de Playwright si no están disponibles
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
    except Exception:
        print("📦 Instalando Playwright chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True
        )
        print("✓ Playwright listo.\n")

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
import agents.spec_writer as spec_cfg
from tools import execute_tool

load_dotenv()

MODEL   = "deepseek-v4-pro"   # opciones: deepseek-v4-flash | deepseek-v4-pro
VERBOSE = True

# ─── CONFIGURACIÓN DE ROBUSTEZ ───────────────────────────────────────────────
MAX_RETRIES_API    = 3   # Reintentos ante errores transitorios de la API (rate limit, timeout)
MAX_RETRIES_IMPL   = 3   # Cuántas veces el implementer puede reintentar una feature
MAX_RETRIES_REVIEW = 2   # Cuántas veces el ciclo impl→review puede repetirse antes de marcar "failed"
MAX_ITER_LEADER    = 30  # Iteraciones máximas del loop del leader
MAX_ITER_AGENT     = 30  # Default — e2e_tester
MAX_ITER_IMPL      = 50  # Implementer: leer contexto + escribir código + tests
MAX_ITER_REVIEWER  = 40  # Reviewer: leer reportes + correr tests + mutation testing
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
    "spec_writer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "implementer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "reviewer":     {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "e2e_tester":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "compaction":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
}

# ─── UTILIDADES DE CONSOLA ───────────────────────────────────────────────────

_AGENT_STYLES = {
    "leader":      ("green",   "👑"),
    "spec_writer": ("cyan",    "📋"),
    "implementer": ("blue",    "🔨"),
    "e2e_tester":  ("yellow",  "🧪"),
    "reviewer":    ("magenta", "🔍"),
}

def _phase_header(agent: str, action: str, feature_id: int = None,
                  attempt: int = None, total_features: int = None, current_feature: int = None):
    """Imprime un header claro de fase con agente, acción y contexto."""
    color, icon = _AGENT_STYLES.get(agent, ("white", "•"))
    progress = ""
    if total_features and current_feature:
        progress = f" [dim]({current_feature}/{total_features})[/]"
    feat_info = f" → Feature #{feature_id}" if feature_id else ""
    attempt_info = f" [dim](intento {attempt})[/]" if attempt and attempt > 1 else ""

    console.rule(
        f"[{color}]{icon} {agent.upper()} — {action}{feat_info}[/]{attempt_info}{progress}",
        style=color
    )

def _agent_action(agent: str, tool: str, args_preview: str, step: int):
    """Línea compacta mostrando qué herramienta está usando el agente."""
    color, icon = _AGENT_STYLES.get(agent, ("white", "•"))
    console.print(
        f"  [{color}]{icon}[/] [dim]paso {step:02d}[/] "
        f"[bold]{tool}[/] [dim]{args_preview[:80]}[/]"
    )

def _agent_result(result_preview: str, success: bool = True):
    """Resultado compacto de una herramienta."""
    icon = "✓" if success else "✗"
    color = "green" if success else "red"
    console.print(f"         [{color}]{icon}[/] [dim]{result_preview[:120]}[/]")
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

def _msg_field(m, field, default=""):
    """Accede a un campo de un mensaje que puede ser dict o ChatCompletionMessage (Pydantic)."""
    if isinstance(m, dict):
        return m.get(field, default)
    return getattr(m, field, default)

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
    raw_tail     = messages[-COMPACT_KEEP_TAIL:]

    # Garantizar que el tail empiece en un límite seguro: el primer mensaje
    # 'assistant' o 'user'. Un tail que empieza con 'tool' causaría error 400
    # porque la API exige que 'tool' siempre siga a un 'assistant' con tool_calls.
    safe_start = 0
    for i, m in enumerate(raw_tail):
        if _msg_field(m, "role", "") in ("assistant", "user"):
            safe_start = i
            break
    tail   = raw_tail[safe_start:]
    middle = messages[2: len(messages) - COMPACT_KEEP_TAIL + safe_start]

    if not middle:
        return messages

    # Construir texto del bloque medio para resumir.
    middle_text = ""
    for m in middle:
        role_label = (_msg_field(m, "role", "?") or "?").upper()
        content    = _msg_field(m, "content") or ""
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

def _safe_parse_args(raw: str, tool_name: str):
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

            args_preview = json.dumps(fn_args, ensure_ascii=False)[:80]
            if VERBOSE:
                _agent_action(role, fn_name, args_preview, i + 1)

            _log(role, "TOOL_CALL", f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")
            result = execute_tool(fn_name, fn_args)
            _log(role, "TOOL_RESULT", result[:200])

            if VERBOSE:
                try:
                    parsed = json.loads(result)
                    success = not ("error" in parsed) and parsed.get("success", True) is not False
                    preview = parsed.get("stdout") or parsed.get("content") or parsed.get("status") or result
                    if isinstance(preview, str):
                        preview = preview.strip()[:120]
                except Exception:
                    success = True
                    preview = result[:120]
                _agent_result(str(preview), success)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result
            })

    _log(role, "MAX_ITER", f"Alcanzado el límite de {max_iter} iteraciones", level="warning")
    return f"[ERROR: max_iter {max_iter} alcanzado]"


# ─── SPAWNERS ────────────────────────────────────────────────────────────────

def _file_tree(path: str, max_files: int = 60) -> str:
    """Snapshot compacto del árbol de archivos relevantes (sin node_modules)."""
    try:
        result = subprocess.run(
            ["find", path, "-type", "f",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/__pycache__/*",
             "-not", "-path", "*/.git/*"],
            capture_output=True, text=True, timeout=5
        )
        lines = sorted(result.stdout.strip().splitlines())[:max_files]
        return "\n".join(lines) or "(vacío)"
    except Exception:
        return "(no disponible)"


def spawn_implementer(feature_id: int, description: str, attempt: int = 1,
                      rejection_reason: str = "", spec_path: str = None) -> str:
    """
    Lanza el implementer. Si es un primer intento y el impl anterior pasó tests,
    lo reutiliza. Si es reintento, inyecta el motivo de rechazo.
    """
    impl_path = f"progress/impl_{feature_id}.md"

    # Fix 2: Reutilizar impl si ya existe y muestra tests pasando
    if attempt == 1 and os.path.exists(impl_path):
        try:
            with open(impl_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "passed" in content and "[ERROR" not in content:
                _log("implementer", "SKIP", f"Impl existente con tests OK: {impl_path}")
                console.print(f"  [blue]🔨 IMPLEMENTER[/] [dim]↩ reutilizando impl existente →[/] {impl_path}")
                return impl_path
        except Exception:
            pass

    context = ""
    if attempt > 1 and rejection_reason:
        context = (
            f"\n\n⚠️  REINTENTO #{attempt} — El reviewer rechazó el intento anterior.\n"
            f"Razón del rechazo: {rejection_reason}\n"
            f"Debes corregir exactamente esos puntos antes de volver a reportar."
        )

    _phase_header("implementer", "Implementando", feature_id, attempt)
    _log("implementer", "SPAWN", f"feature={feature_id} attempt={attempt}")

    cwd = os.getcwd()

    # Fix 1: Pre-inyectar árbol de archivos para evitar reads exploratorios
    tree_src     = _file_tree("src")
    tree_frontend = _file_tree("frontend/src") if os.path.exists("frontend/src") else "(no existe aún)"
    tree_tests   = _file_tree("tests")

    spec_content = ""
    if spec_path and os.path.exists(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_content = f"\n## Especificación técnica ({spec_path}):\n{f.read()}\n"
        except Exception:
            spec_content = f"\nLee la especificación técnica en {spec_path} ANTES de escribir código.\n"

    task = (
        f"DIRECTORIO DE TRABAJO: {cwd}\n"
        f"Todos los comandos bash deben ejecutarse desde este directorio.\n\n"
        f"## Árbol de archivos actual (src/):\n{tree_src}\n\n"
        f"## Árbol de archivos actual (frontend/src/):\n{tree_frontend}\n\n"
        f"## Árbol de archivos actual (tests/):\n{tree_tests}\n"
        f"{spec_content}\n"
        f"Implementa la feature #{feature_id}: {description}{context}\n"
        f"Escribe tu reporte en {impl_path}\n"
        f"Devuelve solo la ruta del archivo cuando termines."
    )
    result = run_agent(impl_cfg.SYSTEM_PROMPT, impl_cfg.TOOLS, task,
                       role="implementer", color="blue", max_iter=MAX_ITER_IMPL)
    done = not result.startswith("[ERROR")
    console.print(f"  [blue]🔨 IMPLEMENTER[/] {'[green]✓ terminó[/]' if done else '[red]✗ error[/]'} → {result[:80]}")
    return result


def spawn_spec_writer(feature_id: int, description: str) -> str:
    """Genera la spec técnica detallada antes de implementar.
    Si la spec ya existe en disco, la reutiliza sin llamar al agente.
    """
    spec_path = f"progress/spec_{feature_id}.md"

    # Reutilizar spec existente — evita gastar iteraciones regenerando
    if os.path.exists(spec_path):
        _log("spec_writer", "SKIP", f"Spec ya existe: {spec_path}")
        console.print(f"  [cyan]📋 SPEC_WRITER[/] [dim]↩ reutilizando spec existente →[/] {spec_path}")
        return spec_path

    _phase_header("spec_writer", "Escribiendo spec", feature_id)
    cwd = os.getcwd()
    task = (
        f"DIRECTORIO DE TRABAJO: {cwd}\n\n"
        f"Escribe la especificación técnica para la feature #{feature_id}: {description}\n"
        f"Guarda la spec en {spec_path}\n"
        f"Devuelve SOLO la ruta: {spec_path}"
    )
    result = run_agent(spec_cfg.SYSTEM_PROMPT, spec_cfg.TOOLS, task,
                       role="spec_writer", color="cyan", max_iter=35)
    done = not result.startswith("[ERROR")
    console.print(f"  [cyan]📋 SPEC_WRITER[/] {'[green]✓ spec lista[/]' if done else '[red]✗ error[/]'} → {result[:80]}")
    return result


def spawn_reviewer(feature_id: int, e2e: bool = True) -> str:
    _phase_header("reviewer", "Revisando", feature_id)
    _log("reviewer", "SPAWN", f"feature={feature_id} e2e={e2e}")

    cwd = os.getcwd()

    # Fix 1: Pre-inyectar árbol de archivos relevante
    tree_src      = _file_tree("src")
    tree_frontend = _file_tree("frontend/src") if os.path.exists("frontend/src") else "(no existe)"
    tree_tests    = _file_tree("tests")

    # Fix 3: Instrucción de validación según tipo de feature
    if not e2e:
        validation_mode = (
            "MODO REVISIÓN FRONTEND (e2e=false):\n"
            "- Lee el reporte del implementer en progress/impl_{fid}.md\n"
            "- Verifica que los archivos listados en el reporte existan en disco (usa run_bash con 'ls')\n"
            "- Verifica que el código JSX/JS no tenga errores de sintaxis obvios (usa run_bash con 'node --check' si aplica)\n"
            "- NO intentes levantar el servidor de desarrollo\n"
            "- NO intentes correr Playwright ni tests E2E\n"
            "- NO corras 'npm run dev' ni 'npm run build'\n"
            "- Si los archivos existen y el reporte indica éxito, aprueba.\n"
        ).format(fid=feature_id)
        max_iter = 15  # revisión liviana — no necesita más
    else:
        validation_mode = (
            "Revisa el trabajo del implementer para la feature #{fid}.\n"
            "Corre los tests con pytest y valida que pasen.\n"
        ).format(fid=feature_id)
        max_iter = MAX_ITER_REVIEWER

    task = (
        f"DIRECTORIO DE TRABAJO: {cwd}\n\n"
        f"## Árbol de archivos actual (src/):\n{tree_src}\n\n"
        f"## Árbol de archivos actual (frontend/src/):\n{tree_frontend}\n\n"
        f"## Árbol de archivos actual (tests/):\n{tree_tests}\n\n"
        f"{validation_mode}\n"
        f"El reporte del implementer está en progress/impl_{feature_id}.md\n"
        f"Escribe tu veredicto en progress/review_{feature_id}.md\n"
        f"Devuelve SOLO: 'APPROVED' o 'REJECTED: <razón>'"
    )
    result = run_agent(reviewer_cfg.SYSTEM_PROMPT, reviewer_cfg.TOOLS, task,
                       role="reviewer", color="magenta", max_iter=max_iter)

    approved = result.strip().startswith("APPROVED")
    verdict_color = "green" if approved else "red"
    verdict_icon  = "✅" if approved else "❌"
    _log("reviewer", "VERDICT", result[:200], level="info" if approved else "warning")
    console.print(f"  [magenta]🔍 REVIEWER[/] [{verdict_color}]{verdict_icon} {result[:100]}[/]")
    return result


def spawn_e2e_tester(feature_id: int) -> str:
    _phase_header("e2e_tester", "Tests E2E", feature_id)
    _log("e2e_tester", "SPAWN", f"feature={feature_id}")

    cwd = os.getcwd()
    task = (
        f"DIRECTORIO DE TRABAJO: {cwd}\n"
        f"Todos los comandos bash deben ejecutarse desde este directorio.\n\n"
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


def run_feature_cycle(feature_id: int, description: str, e2e: bool = True) -> dict:
    """
    Ciclo completo: spec → impl → (e2e) → review con reintentos.
    Flujo:
      1. Spec Writer produce la especificación técnica detallada.
      2. Implementer escribe código + tests siguiendo la spec.
      3. E2E Tester (solo si e2e=True) valida con Playwright.
      4. Reviewer verifica tests + checkpoints.
    Si el reviewer rechaza, reintenta impl→e2e→review con el motivo inyectado.
    Retorna dict con: approved (bool), attempts (int), final_verdict (str).
    """
    # ── Paso 1: Spec (solo en el primer intento) ─────────────────────────────
    spec_result = spawn_spec_writer(feature_id, description)
    spec_path = spec_result.strip() if not spec_result.startswith("[ERROR") else None

    rejection_reason = ""
    for attempt in range(1, MAX_RETRIES_REVIEW + 1):

        # ── Paso 2: Implementar ──────────────────────────────────────────────
        impl_result = spawn_implementer(
            feature_id, description,
            attempt=attempt,
            rejection_reason=rejection_reason,
            spec_path=spec_path
        )
        if "[ERROR" in impl_result.upper():
            err_type = _classify_error(impl_result)
            _log("harness", "IMPL_ERROR",
                 f"feature={feature_id} type={err_type} detail={impl_result[:200]}", level="error")
            if err_type == "FATAL":
                return {"approved": False, "attempts": attempt, "final_verdict": impl_result}
            rejection_reason = impl_result
            continue

        # ── Paso 2: E2E Testing (solo si la feature lo requiere) ────────────
        if not e2e:
            e2e_result = "E2E_PASSED"  # no aplica — saltear silenciosamente
        else:
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
        review_result = spawn_reviewer(feature_id, e2e=e2e)
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

def _build_leader_task(user_task: str) -> str:
    """
    Pre-inyecta feature_list.json y progress/current.md en el mensaje del leader
    para eliminar 2-3 tool calls de overhead por sesión.
    """
    try:
        with open("feature_list.json", "r", encoding="utf-8") as f:
            features = json.load(f)
        features_json = json.dumps(features, indent=2, ensure_ascii=False)
    except Exception as e:
        features_json = f"(no disponible: {e})"

    try:
        with open("progress/current.md", "r", encoding="utf-8") as f:
            current_md = f.read().strip()
    except Exception:
        current_md = "(sin estado previo)"

    return (
        f"## feature_list.json (estado actual)\n```json\n{features_json}\n```\n\n"
        f"## progress/current.md\n{current_md}\n\n"
        f"## Instrucción del usuario\n{user_task}"
    )


def run_leader(user_task: str) -> str:
    enriched_task = _build_leader_task(user_task)
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
                    "Pasa e2e=false para features sin interfaz web (modelos, storage, API pura)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "feature_id":  {"type": "integer", "description": "ID de la feature"},
                        "description": {"type": "string",  "description": "Descripción completa de la tarea"},
                        "e2e":         {"type": "boolean", "description": "true si la feature tiene UI web que probar con Playwright. false para backend/dominio puro. Leer el campo 'e2e' de feature_list.json."}
                    },
                    "required": ["feature_id", "description"]
                }
            }
        }
    ]

    messages = [
        {"role": "system", "content": leader_cfg.SYSTEM_PROMPT},
        {"role": "user",   "content": enriched_task}
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
    # Verificar e instalar dependencias antes de mostrar cualquier UI
    _ensure_deps()

    console.rule("DeepSeek Multi-Agent Harness", style="white")
    console.print(
        f"  Modelo: [cyan]{MODEL}[/]  |  "
        f"Flujo: [green]👑 Leader[/] → [cyan]📋 Spec[/] → [blue]🔨 Impl[/] → [yellow]🧪 E2E[/] → [magenta]🔍 Reviewer[/]\n"
        f"  [dim]Comandos: /salir | /estado | /features | /costos[/]"
    )
    console.rule(style="dim")

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
            console.rule("[green]✅ Sesión completada[/]", style="green")
            console.print(f"  [green]👑 LEADER[/] {result}")
    finally:
        # Siempre escribir costos al salir, incluso si hay crash
        _write_session_costs()


if __name__ == "__main__":
    main()