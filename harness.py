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

# ─── MODEL SELECTION ─────────────────────────────────────────────────────────
# Default model used as a fallback for any role not listed in MODEL_BY_ROLE,
# and for direct callers that don't pass a role (e.g. one-off API calls).
MODEL = "deepseek-v4-pro"

# Per-role model overrides.
# Assign heavier/more expensive models only where reasoning quality matters most.
# Assign lighter/cheaper models to roles that do structured or mechanical work.
#
# Guidelines:
#   "pro"   — use when the agent must reason about ambiguous requirements,
#             design architecture, or write non-trivial code from scratch.
#   "flash" — use when the agent follows a clear template, reads existing files,
#             runs commands, or produces structured output (JSON, Markdown).
#
# To change a role's model, edit the value here — no other code needs to change.
# To add a new role, insert a new key; it will be picked up automatically by run_agent.
MODEL_BY_ROLE: dict[str, str] = {
    "leader":      "deepseek-v4-pro",    # orchestration requires multi-step reasoning
    "spec_writer": "deepseek-v4-flash",  # structured output from a clear template
    "implementer": "deepseek-v4-pro",    # code generation benefits from the best model
    "reviewer":    "deepseek-v4-flash",  # reads files, runs tests — no deep reasoning needed
    "e2e_tester":  "deepseek-v4-flash",  # executes existing test scripts mechanically
    "compaction":  "deepseek-v4-flash",  # context summarization: fast and cheap
}

VERBOSE = True

# ─── ROBUSTNESS SETTINGS ─────────────────────────────────────────────────────
MAX_RETRIES_API    = 3   # Retries on transient API errors (rate limit, timeout)
MAX_RETRIES_IMPL   = 3   # How many times the implementer can retry a feature
MAX_RETRIES_REVIEW = 2   # How many times the impl→review cycle repeats before marking "failed"
MAX_ITER_LEADER    = 30  # Max iterations for the leader loop
MAX_ITER_AGENT     = 30  # Default — e2e_tester
MAX_ITER_IMPL      = 50  # Implementer: read context + write code + tests
MAX_ITER_REVIEWER  = 40  # Reviewer: read reports + run tests + mutation testing
RETRY_BACKOFF      = [2, 4, 8]  # seconds between API retries

# Context compaction — 2025 best practices:
# 64K token models: compact when history exceeds ~30% of context.
# Conservative: trigger at 24 messages (~12 exchanges), keep last 8.
COMPACT_THRESHOLD  = 24  # accumulated messages before compacting
COMPACT_KEEP_TAIL  = 8   # recent messages to preserve intact after compacting

# DeepSeek pricing (USD per million tokens, cache miss):
_PRICE_INPUT  = 0.27 / 1_000_000
_PRICE_OUTPUT = 1.10 / 1_000_000

# ─── STRUCTURED LOGGING ──────────────────────────────────────────────────────
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

# ─── COST OBSERVABILITY ──────────────────────────────────────────────────────
_SESSION_COSTS: dict = {
    "leader":       {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "spec_writer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "implementer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "reviewer":     {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "e2e_tester":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
    "compaction":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0},
}

# ─── CONSOLE UTILITIES ───────────────────────────────────────────────────────

_AGENT_STYLES = {
    "leader":      ("green",   "👑"),
    "spec_writer": ("cyan",    "📋"),
    "implementer": ("blue",    "🔨"),
    "e2e_tester":  ("yellow",  "🧪"),
    "reviewer":    ("magenta", "🔍"),
}

def _phase_header(agent: str, action: str, feature_id: int = None,
                  attempt: int = None, total_features: int = None, current_feature: int = None):
    """Print a clear phase header with agent, action and context."""
    color, icon = _AGENT_STYLES.get(agent, ("white", "•"))
    progress = ""
    if total_features and current_feature:
        progress = f" [dim]({current_feature}/{total_features})[/]"
    feat_info = f" → Feature #{feature_id}" if feature_id else ""
    attempt_info = f" [dim](attempt {attempt})[/]" if attempt and attempt > 1 else ""

    console.rule(
        f"[{color}]{icon} {agent.upper()} — {action}{feat_info}[/]{attempt_info}{progress}",
        style=color
    )

def _agent_action(agent: str, tool: str, args_preview: str, step: int):
    """Compact line showing which tool the agent is using."""
    color, icon = _AGENT_STYLES.get(agent, ("white", "•"))
    console.print(
        f"  [{color}]{icon}[/] [dim]step {step:02d}[/] "
        f"[bold]{tool}[/] [dim]{args_preview[:80]}[/]"
    )

def _agent_result(result_preview: str, success: bool = True):
    """Compact tool result."""
    icon = "✓" if success else "✗"
    color = "green" if success else "red"
    console.print(f"         [{color}]{icon}[/] [dim]{result_preview[:120]}[/]")
_SESSION_START = datetime.datetime.now()

def _track_usage(role: str, usage) -> None:
    """Accumulate tokens from each API call by role."""
    if usage is None:
        return
    bucket = _SESSION_COSTS.get(role, _SESSION_COSTS["leader"])
    bucket["prompt_tokens"]     += getattr(usage, "prompt_tokens", 0)
    bucket["completion_tokens"] += getattr(usage, "completion_tokens", 0)
    bucket["calls"]             += 1

def _write_session_costs() -> None:
    """Write session cost summary to progress/session_costs.json."""
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
        f"Estimated cost: [yellow]USD {total_cost_usd:.4f}[/]",
        title="[dim]Session costs → progress/session_costs.json[/]",
        border_style="dim",
        padding=(0, 1)
    ))

# ─── FEATURE DEPENDENCY GRAPH ────────────────────────────────────────────────

def _topological_sort(features: list) -> tuple[list, list]:
    """
    Sort features into a valid execution order that respects all depends_on
    declarations. Uses Kahn's algorithm (BFS-based topological sort).

    Returns:
        ordered_ids  — feature IDs in execution order (safe to process left to right).
        cycle_ids    — IDs involved in a circular dependency; empty list if none.

    Features with no depends_on field (or an empty list) are treated as roots
    and may be scheduled first. Among features at the same depth level, ordering
    is stable (ascending ID within each batch).
    """
    from collections import deque

    id_set = {f["id"] for f in features}
    in_degree: dict[int, int] = {f["id"]: 0 for f in features}
    # dependents[x] = list of feature IDs that require x to be done first
    dependents: dict[int, list[int]] = {f["id"]: [] for f in features}

    for feat in features:
        for dep_id in feat.get("depends_on", []):
            if dep_id not in id_set:
                continue  # missing dep — reported by _validate_dependencies
            in_degree[feat["id"]] += 1
            dependents[dep_id].append(feat["id"])

    # Seed the queue with all features that have no pending dependencies
    queue: deque[int] = deque(sorted(fid for fid, deg in in_degree.items() if deg == 0))
    ordered: list[int] = []

    while queue:
        fid = queue.popleft()
        ordered.append(fid)
        for dependent_id in sorted(dependents[fid]):
            in_degree[dependent_id] -= 1
            if in_degree[dependent_id] == 0:
                queue.append(dependent_id)

    # Any feature still with in_degree > 0 is part of a cycle
    cycle_ids = [fid for fid, deg in in_degree.items() if deg > 0]
    return ordered, cycle_ids


def _validate_dependencies(features: list) -> list[str]:
    """
    Validate the dependency graph for structural errors.

    Checks performed:
      1. Self-dependency — a feature lists its own ID in depends_on.
      2. Missing dependency — depends_on references an ID not in feature_list.json.
      3. Circular dependency — detected via _topological_sort cycle output.

    Returns a list of human-readable error strings (empty list = graph is valid).
    Call this on startup and surface any errors before the Leader runs.
    """
    errors: list[str] = []
    id_set = {f["id"] for f in features}

    for feat in features:
        for dep_id in feat.get("depends_on", []):
            if dep_id == feat["id"]:
                errors.append(
                    f"Feature #{feat['id']} (\"{feat.get('title', '')}\") "
                    f"depends on itself."
                )
            elif dep_id not in id_set:
                errors.append(
                    f"Feature #{feat['id']} (\"{feat.get('title', '')}\") "
                    f"depends on #{dep_id} which does not exist in feature_list.json."
                )

    _, cycle_ids = _topological_sort(features)
    if cycle_ids:
        errors.append(
            f"Circular dependency detected — the following features form a cycle "
            f"and cannot be resolved: {sorted(cycle_ids)}. "
            f"Break the cycle by removing at least one depends_on edge."
        )

    return errors


# ─── CHECKPOINTING ───────────────────────────────────────────────────────────

def recover_stale_features() -> list[int]:
    """
    On startup, detects features stuck in 'in_progress' from a previous crash
    and resets them to 'pending'. Returns list of recovered IDs.
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
            feat["recovery_note"] = "Reset to pending by harness on startup (possible previous crash)"
            recovered.append(feat["id"])

    if recovered:
        with open("feature_list.json", "w") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
        _log("harness", "CHECKPOINT_RECOVERY",
             f"Features reset to pending: {recovered}", level="warning")
        console.print(Panel(
            f"[yellow]Features {recovered} were 'in_progress' — reset to 'pending'[/]\n"
            "[dim]Possible crash in previous session. The leader will resume them.[/]",
            title="[yellow]⚠ Checkpoint Recovery[/]",
            border_style="yellow",
            padding=(0, 1)
        ))
    return recovered

# ─── CONTEXT COMPACTION ──────────────────────────────────────────────────────

def _msg_field(m, field, default=""):
    """Access a field from a message that can be dict or ChatCompletionMessage (Pydantic)."""
    if isinstance(m, dict):
        return m.get(field, default)
    return getattr(m, field, default)

def _compact_messages(messages: list, role: str) -> list:
    """
    When history exceeds COMPACT_THRESHOLD messages, summarize the middle block
    into a single entry to avoid exceeding the context window.
    Always preserves: system (0), initial task (1), and last COMPACT_KEEP_TAIL messages.
    """
    if len(messages) <= COMPACT_THRESHOLD:
        return messages

    system_msg   = messages[0]
    initial_task = messages[1]
    raw_tail     = messages[-COMPACT_KEEP_TAIL:]

    # Ensure tail starts at a safe boundary: first 'assistant' or 'user' message.
    # A tail starting with 'tool' would cause a 400 error because the API requires
    # 'tool' to always follow an 'assistant' message with tool_calls.
    safe_start = 0
    for i, m in enumerate(raw_tail):
        if _msg_field(m, "role", "") in ("assistant", "user"):
            safe_start = i
            break
    tail   = raw_tail[safe_start:]
    middle = messages[2: len(messages) - COMPACT_KEEP_TAIL + safe_start]

    if not middle:
        return messages

    # Build text of the middle block to summarize.
    middle_text = ""
    for m in middle:
        role_label = (_msg_field(m, "role", "?") or "?").upper()
        content    = _msg_field(m, "content") or ""
        if isinstance(content, list):
            content = json.dumps(content, ensure_ascii=False)
        middle_text += f"[{role_label}]: {str(content)[:300]}\n"

    _log(role, "COMPACTING",
         f"Compacting {len(middle)} intermediate messages (total={len(messages)})")

    try:
        summary_response = client.chat.completions.create(
            model=MODEL_BY_ROLE.get("compaction", MODEL),
            messages=[
                {"role": "system",
                 "content": "You are a technical assistant. Concisely summarize the work history of a software agent."},
                {"role": "user",
                 "content": (
                     "Summarize this history in at most 400 words. Preserve: "
                     "design decisions made, tools executed and their key results, "
                     "errors encountered and how they were resolved, current state of work.\n\n"
                     f"{middle_text}"
                 )}
            ],
            max_tokens=500,
        )
        _track_usage("compaction", summary_response.usage)
        summary_text = summary_response.choices[0].message.content or "(no summary)"
    except Exception as e:
        summary_text = f"(summary unavailable: {e})"

    compact_msg = {
        "role": "system",
        "content": f"## Previous context summary\n{summary_text}"
    }

    compacted = [system_msg, initial_task, compact_msg] + list(tail)
    _log(role, "COMPACTED",
         f"Reduced from {len(messages)} to {len(compacted)} messages")
    return compacted

# ─── UTILITIES ───────────────────────────────────────────────────────────────

def _safe_parse_args(raw: str, tool_name: str):
    """Parse JSON arguments from a tool call. Returns (args, error_msg)."""
    try:
        return json.loads(raw), ""
    except json.JSONDecodeError as e:
        err = f"Invalid JSON in args of '{tool_name}': {e}"
        _log("harness", "PARSE_ERROR", err, level="error")
        return None, err

def _classify_error(error_msg: str) -> str:
    """
    Classify an error to decide the retry strategy.
    TRANSIENT → retryable with backoff (rate limit, network timeout)
    LOGICAL   → requires a different approach (logic error, test failure)
    FATAL     → stop (credentials, critical file not found)
    """
    msg = error_msg.lower()
    if any(k in msg for k in ("rate limit", "timeout", "connection", "503", "502", "429")):
        return "TRANSIENT"
    if any(k in msg for k in ("max_iter", "blocked", "assertion", "error:")):
        return "LOGICAL"
    return "FATAL"

# ─── GENERIC AGENT ENGINE ────────────────────────────────────────────────────

def run_agent(system_prompt: str, tools: list, task: str,
              role: str = "agent", color: str = "white",
              max_iter: int = MAX_ITER_AGENT) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": task}
    ]
    _log(role, "START", task[:120])

    for i in range(max_iter):
        # Retry on transient API errors
        api_response = None
        for attempt in range(MAX_RETRIES_API):
            try:
                api_response = client.chat.completions.create(
                    model=MODEL_BY_ROLE.get(role, MODEL),
                    messages=messages,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                )
                break
            except Exception as e:
                err_type = _classify_error(str(e))
                if err_type == "TRANSIENT" and attempt < MAX_RETRIES_API - 1:
                    wait = RETRY_BACKOFF[attempt]
                    _log(role, "API_RETRY", f"attempt {attempt+1}/{MAX_RETRIES_API} — wait {wait}s — {e}", level="warning")
                    time.sleep(wait)
                else:
                    _log(role, "API_FATAL", str(e), level="error")
                    return f"[ERROR API: {e}]"

        if api_response is None:
            return "[ERROR: no response received from API]"

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
                # Return the error to the agent so it can correct
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

    _log(role, "MAX_ITER", f"Reached iteration limit of {max_iter}", level="warning")
    return f"[ERROR: max_iter {max_iter} reached]"


# ─── SPAWNERS ────────────────────────────────────────────────────────────────

def _file_tree(path: str, max_files: int = 60) -> str:
    """Compact snapshot of the relevant file tree (without node_modules)."""
    try:
        result = subprocess.run(
            ["find", path, "-type", "f",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/__pycache__/*",
             "-not", "-path", "*/.git/*"],
            capture_output=True, text=True, timeout=5
        )
        lines = sorted(result.stdout.strip().splitlines())[:max_files]
        return "\n".join(lines) or "(empty)"
    except Exception:
        return "(not available)"


def spawn_implementer(feature_id: int, description: str, attempt: int = 1,
                      rejection_reason: str = "", spec_path: str = None) -> str:
    """
    Launch the implementer. On first attempt, reuses existing impl if tests passed.
    On retry, injects the rejection reason so the agent doesn't repeat the same mistake.
    """
    impl_path = f"progress/impl_{feature_id}.md"

    # Reuse existing impl if it already exists and shows passing tests
    if attempt == 1 and os.path.exists(impl_path):
        try:
            with open(impl_path, "r", encoding="utf-8") as f:
                content = f.read()
            if "passed" in content and "[ERROR" not in content:
                _log("implementer", "SKIP", f"Existing impl with passing tests: {impl_path}")
                console.print(f"  [blue]🔨 IMPLEMENTER[/] [dim]↩ reusing existing impl →[/] {impl_path}")
                return impl_path
        except Exception:
            pass

    context = ""
    if attempt > 1 and rejection_reason:
        context = (
            f"\n\n⚠️  RETRY #{attempt} — The reviewer rejected the previous attempt.\n"
            f"Rejection reason: {rejection_reason}\n"
            f"You must fix exactly those points before reporting again."
        )

    _phase_header("implementer", "Implementing", feature_id, attempt)
    _log("implementer", "SPAWN", f"feature={feature_id} attempt={attempt}")

    cwd = os.getcwd()

    # Pre-inject file tree to avoid exploratory reads
    tree_src      = _file_tree("src")
    tree_frontend = _file_tree("frontend/src") if os.path.exists("frontend/src") else "(not created yet)"
    tree_tests    = _file_tree("tests")

    spec_content = ""
    if spec_path and os.path.exists(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_content = f"\n## Technical specification ({spec_path}):\n{f.read()}\n"
        except Exception:
            spec_content = f"\nRead the technical specification at {spec_path} BEFORE writing code.\n"

    task = (
        f"WORKING DIRECTORY: {cwd}\n"
        f"All bash commands must be run from this directory.\n\n"
        f"## Current file tree (src/):\n{tree_src}\n\n"
        f"## Current file tree (frontend/src/):\n{tree_frontend}\n\n"
        f"## Current file tree (tests/):\n{tree_tests}\n"
        f"{spec_content}\n"
        f"Implement feature #{feature_id}: {description}{context}\n"
        f"Write your report to {impl_path}\n"
        f"Return only the file path when done."
    )
    result = run_agent(impl_cfg.SYSTEM_PROMPT, impl_cfg.TOOLS, task,
                       role="implementer", color="blue", max_iter=MAX_ITER_IMPL)
    done = not result.startswith("[ERROR")
    console.print(f"  [blue]🔨 IMPLEMENTER[/] {'[green]✓ done[/]' if done else '[red]✗ error[/]'} → {result[:80]}")
    return result


def spawn_spec_writer(feature_id: int, description: str) -> str:
    """Generate the detailed technical spec before implementing.
    If the spec already exists on disk, reuse it without calling the agent.
    """
    spec_path = f"progress/spec_{feature_id}.md"

    # Reuse existing spec — avoids spending iterations regenerating
    if os.path.exists(spec_path):
        _log("spec_writer", "SKIP", f"Spec already exists: {spec_path}")
        console.print(f"  [cyan]📋 SPEC_WRITER[/] [dim]↩ reusing existing spec →[/] {spec_path}")
        return spec_path

    _phase_header("spec_writer", "Writing spec", feature_id)
    cwd = os.getcwd()
    task = (
        f"WORKING DIRECTORY: {cwd}\n\n"
        f"Write the technical specification for feature #{feature_id}: {description}\n"
        f"Save the spec to {spec_path}\n"
        f"Return ONLY the path: {spec_path}"
    )
    result = run_agent(spec_cfg.SYSTEM_PROMPT, spec_cfg.TOOLS, task,
                       role="spec_writer", color="cyan", max_iter=35)
    done = not result.startswith("[ERROR")
    console.print(f"  [cyan]📋 SPEC_WRITER[/] {'[green]✓ spec ready[/]' if done else '[red]✗ error[/]'} → {result[:80]}")
    return result


def spawn_reviewer(feature_id: int, e2e: bool = True) -> str:
    _phase_header("reviewer", "Reviewing", feature_id)
    _log("reviewer", "SPAWN", f"feature={feature_id} e2e={e2e}")

    cwd = os.getcwd()

    # Pre-inject relevant file tree
    tree_src      = _file_tree("src")
    tree_frontend = _file_tree("frontend/src") if os.path.exists("frontend/src") else "(not present)"
    tree_tests    = _file_tree("tests")

    # Validation mode depends on feature type
    if not e2e:
        validation_mode = (
            "FRONTEND REVIEW MODE (e2e=false):\n"
            "- Read the implementer report at progress/impl_{fid}.md\n"
            "- Verify that the files listed in the report exist on disk (use run_bash with 'ls')\n"
            "- Check that JSX/JS code has no obvious syntax errors (use run_bash with 'node --check' if applicable)\n"
            "- DO NOT attempt to start the dev server\n"
            "- DO NOT attempt to run Playwright or E2E tests\n"
            "- DO NOT run 'npm run dev' or 'npm run build'\n"
            "- If files exist and the report indicates success, approve.\n"
        ).format(fid=feature_id)
        max_iter = 15  # lightweight review — doesn't need more
    else:
        validation_mode = (
            "Review the implementer's work for feature #{fid}.\n"
            "Run tests with pytest and validate that they pass.\n"
        ).format(fid=feature_id)
        max_iter = MAX_ITER_REVIEWER

    task = (
        f"WORKING DIRECTORY: {cwd}\n\n"
        f"## Current file tree (src/):\n{tree_src}\n\n"
        f"## Current file tree (frontend/src/):\n{tree_frontend}\n\n"
        f"## Current file tree (tests/):\n{tree_tests}\n\n"
        f"{validation_mode}\n"
        f"The implementer report is at progress/impl_{feature_id}.md\n"
        f"Write your verdict to progress/review_{feature_id}.md\n"
        f"Return ONLY: 'APPROVED' or 'REJECTED: <reason>'"
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
        f"WORKING DIRECTORY: {cwd}\n"
        f"All bash commands must be run from this directory.\n\n"
        f"Run E2E tests for feature #{feature_id}.\n"
        f"The implementer report is at progress/impl_{feature_id}.md\n"
        f"Write your report to progress/e2e_{feature_id}.md\n"
        f"Return ONLY: 'E2E_PASSED' or 'E2E_FAILED: <reason>'"
    )
    result = run_agent(e2e_cfg.SYSTEM_PROMPT, e2e_cfg.TOOLS, task,
                       role="e2e_tester", color="yellow")

    passed = result.strip().startswith("E2E_PASSED")
    color  = "green" if passed else "red"
    _log("e2e_tester", "VERDICT", result[:200], level="info" if passed else "warning")
    console.print(Panel(
        f"[bold]{result[:200]}[/]",
        title=f"[{color}]<< E2E_TESTER verdict[/]",
        border_style=color,
        padding=(0, 1)
    ))
    return result


def run_feature_cycle(feature_id: int, description: str, e2e: bool = True) -> dict:
    """
    Full cycle: spec → impl → (e2e) → review with retries.
    Flow:
      1. Spec Writer produces the detailed technical specification.
      2. Implementer writes code + tests following the spec.
      3. E2E Tester (only if e2e=True) validates with Playwright.
      4. Reviewer validates tests + checkpoints.
    If the reviewer rejects, retries impl→e2e→review with the injected reason.
    Returns dict with: approved (bool), attempts (int), final_verdict (str).
    """
    # ── Step 1: Spec (only on first attempt) ─────────────────────────────────
    spec_result = spawn_spec_writer(feature_id, description)
    spec_path = spec_result.strip() if not spec_result.startswith("[ERROR") else None

    rejection_reason = ""
    for attempt in range(1, MAX_RETRIES_REVIEW + 1):

        # ── Step 2: Implement ────────────────────────────────────────────────
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

        # ── Step 3: E2E Testing (only if the feature requires it) ────────────
        if not e2e:
            e2e_result = "E2E_PASSED"  # not applicable — skip silently
        else:
            e2e_result = spawn_e2e_tester(feature_id)
        if e2e_result.strip().startswith("E2E_FAILED"):
            e2e_reason = e2e_result.replace("E2E_FAILED:", "").strip()
            _log("harness", "E2E_FAILED",
                 f"feature={feature_id} attempt={attempt} reason={e2e_reason[:100]}", level="warning")
            # E2E failure counts as rejection — implementer fixes it
            rejection_reason = f"E2E failed: {e2e_reason}"
            if attempt < MAX_RETRIES_REVIEW:
                console.print(Panel(
                    f"[red]E2E failed — retrying impl (attempt {attempt+1}/{MAX_RETRIES_REVIEW})[/]\n"
                    f"[dim]{e2e_reason[:200]}[/]",
                    title=f"[red]↻ E2E → impl — feature #{feature_id}[/]",
                    border_style="red", padding=(0, 1)
                ))
            continue

        # ── Step 4: Review ───────────────────────────────────────────────────
        review_result = spawn_reviewer(feature_id, e2e=e2e)
        if review_result.strip().startswith("APPROVED"):
            return {"approved": True, "attempts": attempt, "final_verdict": review_result}

        rejection_reason = review_result.replace("REJECTED:", "").strip()
        _log("harness", "CYCLE_RETRY",
             f"feature={feature_id} attempt={attempt}/{MAX_RETRIES_REVIEW} reason={rejection_reason[:100]}",
             level="warning")
        if attempt < MAX_RETRIES_REVIEW:
            console.print(Panel(
                f"[yellow]Reviewer rejected — retry {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n"
                f"[dim]{rejection_reason[:200]}[/]",
                title=f"[yellow]↻ impl→e2e→review cycle — feature #{feature_id}[/]",
                border_style="yellow", padding=(0, 1)
            ))

    return {
        "approved": False,
        "attempts": MAX_RETRIES_REVIEW,
        "final_verdict": f"REJECTED after {MAX_RETRIES_REVIEW} attempts: {rejection_reason}"
    }


# ─── LEADER LOOP ─────────────────────────────────────────────────────────────

def _build_leader_task(user_task: str) -> str:
    """
    Pre-inject feature_list.json, dependency execution order, and
    progress/current.md into the leader's message.

    Injecting the resolved execution order eliminates the need for the Leader
    to infer ordering from depends_on fields itself, and makes dependency
    violations immediately visible in logs.
    """
    features = []
    features_json = "(not available)"
    try:
        with open("feature_list.json", "r", encoding="utf-8") as f:
            features = json.load(f)
        features_json = json.dumps(features, indent=2, ensure_ascii=False)
    except Exception as e:
        features_json = f"(not available: {e})"

    try:
        with open("progress/current.md", "r", encoding="utf-8") as f:
            current_md = f.read().strip()
    except Exception:
        current_md = "(no previous state)"

    # Build and inject the dependency-resolved execution order
    dep_section = ""
    if features:
        dep_errors = _validate_dependencies(features)
        if dep_errors:
            dep_section = (
                "\n## ⚠ Dependency graph errors (fix before running)\n"
                + "\n".join(f"- {e}" for e in dep_errors)
                + "\n"
            )
            for err in dep_errors:
                _log("harness", "DEP_ERROR", err, level="error")
                console.print(f"  [bold red]⚠ DEP ERROR:[/] {err}")
        else:
            ordered_ids, _ = _topological_sort(features)
            id_to_title = {f["id"]: f.get("title", f"Feature #{f['id']}") for f in features}
            order_lines = " → ".join(
                f"#{fid} ({id_to_title[fid]})" for fid in ordered_ids
            )
            dep_section = (
                f"\n## Resolved execution order (respects depends_on)\n"
                f"{order_lines}\n"
                f"Process features in exactly this order. "
                f"Do not start a feature until all its depends_on features are 'done'.\n"
            )

    return (
        f"## feature_list.json (current state)\n```json\n{features_json}\n```\n"
        f"{dep_section}\n"
        f"## progress/current.md\n{current_md}\n\n"
        f"## User instruction\n{user_task}"
    )


def run_leader(user_task: str) -> str:
    enriched_task = _build_leader_task(user_task)
    console.print(Panel(
        f"[dim]{user_task}[/]",
        title="[green]>> LEADER active[/]",
        border_style="green",
        padding=(0, 1)
    ))

    LEADER_TOOLS = leader_cfg.TOOLS + [
        {
            "type": "function",
            "function": {
                "name": "run_feature_cycle",
                "description": (
                    "Runs the full implement → review cycle for a feature. "
                    f"Automatically retries up to {MAX_RETRIES_REVIEW} times if the reviewer rejects. "
                    "Returns JSON with: approved (bool), attempts (int), final_verdict (str). "
                    "Pass e2e=false for features without a web UI (models, storage, pure API)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "feature_id":  {"type": "integer", "description": "Feature ID"},
                        "description": {"type": "string",  "description": "Full task description"},
                        "e2e":         {"type": "boolean", "description": "true if the feature has a web UI to test with Playwright. false for backend/domain only. Read the 'e2e' field from feature_list.json."}
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
        # Retry on transient API errors
        api_response = None
        for attempt in range(MAX_RETRIES_API):
            try:
                api_response = client.chat.completions.create(
                    model=MODEL_BY_ROLE.get("leader", MODEL),
                    messages=messages,
                    tools=LEADER_TOOLS,
                    tool_choice="auto",
                )
                break
            except Exception as e:
                err_type = _classify_error(str(e))
                if err_type == "TRANSIENT" and attempt < MAX_RETRIES_API - 1:
                    wait = RETRY_BACKOFF[attempt]
                    _log("leader", "API_RETRY", f"attempt {attempt+1} — wait {wait}s — {e}", level="warning")
                    time.sleep(wait)
                else:
                    _log("leader", "API_FATAL", str(e), level="error")
                    return f"[ERROR API leader: {e}]"

        if api_response is None:
            return "[ERROR: leader received no response from API]"

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

    _log("leader", "MAX_ITER", f"Reached iteration limit of {MAX_ITER_LEADER}", level="error")
    return f"[ERROR: leader max_iter {MAX_ITER_LEADER} reached]"


# ─── REPL ─────────────────────────────────────────────────────────────────────

def print_features():
    with open("feature_list.json", "r") as f:
        features = json.load(f)
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID",     style="dim", width=4)
    table.add_column("Status", width=14)
    table.add_column("Title")
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
    # Verify and install dependencies before showing any UI
    _ensure_deps()

    console.rule("Multi-Agent Harness", style="white")
    console.print(
        f"  Model: [cyan]{MODEL}[/]  |  "
        f"Flow: [green]👑 Leader[/] → [cyan]📋 Spec[/] → [blue]🔨 Impl[/] → [yellow]🧪 E2E[/] → [magenta]🔍 Reviewer[/]\n"
        f"  [dim]Commands: /quit | /status | /features | /costs[/]"
    )
    console.rule(style="dim")

    # Checkpointing: recover features stuck from previous sessions
    recover_stale_features()

    # Validate the dependency graph on startup and warn immediately if broken.
    # This catches cycles and missing IDs before the Leader wastes tokens on them.
    try:
        with open("feature_list.json", "r", encoding="utf-8") as f:
            _startup_features = json.load(f)
        _dep_errors = _validate_dependencies(_startup_features)
        if _dep_errors:
            console.print(Panel(
                "\n".join(f"[red]• {e}[/]" for e in _dep_errors),
                title="[red]⚠ Dependency graph errors — fix feature_list.json before running[/]",
                border_style="red",
                padding=(0, 1)
            ))
        else:
            _ordered, _ = _topological_sort(_startup_features)
            _id_to_title = {f["id"]: f.get("title", f"#{f['id']}") for f in _startup_features}
            _order_str = " → ".join(f"#{fid}" for fid in _ordered)
            console.print(
                f"  [dim]Execution order (depends_on resolved): {_order_str}[/]"
            )
    except FileNotFoundError:
        pass  # feature_list.json doesn't exist yet — that's fine

    try:
        while True:
            try:
                user_input = console.input("[bold white]You →[/] ").strip()
            except (KeyboardInterrupt, EOFError):
                console.print("\n[dim]Exiting...[/]")
                break

            if not user_input:
                continue

            if user_input in ("/quit", "/salir"):
                break
            elif user_input in ("/status", "/estado"):
                with open("progress/current.md", "r") as f:
                    console.print(Markdown(f.read()))
                continue
            elif user_input == "/features":
                print_features()
                continue
            elif user_input in ("/costs", "/costos"):
                _write_session_costs()
                continue

            result = run_leader(user_input)
            console.rule("[green]✅ Session complete[/]", style="green")
            console.print(f"  [green]👑 LEADER[/] {result}")
    finally:
        # Always write costs on exit, even if there's a crash
        _write_session_costs()


if __name__ == "__main__":
    main()