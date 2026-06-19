import os, re, json, time, logging, datetime, subprocess, sys
from typing import Optional

# Load .env as early as possible — before any module (e.g. tools.py) reads
# os.environ at import time. If this ran after `from tools import ...`,
# per-project overrides like SAFE_WRITE_DIRS would silently fall back to
# their defaults unless the shell had already exported the .env vars itself.
from dotenv import load_dotenv
load_dotenv()

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
from tools import execute_tool, SAFE_WRITE_DIRS
from stack_layout import resolve_layout

print(f"[CONFIG] SAFE_WRITE_DIRS = {SAFE_WRITE_DIRS}")

# Single source of truth for stack-dependent layout — same resolver tools.py
# uses for SAFE_WRITE_DIRS, so the two can never drift apart. Derived from
# stack_config.json + stack_profiles.json (falls back to a hardcoded default;
# never raises). The CODE_TREE_DIRS / SAFE_WRITE_DIRS env vars still work as a
# highest-precedence emergency override — that logic lives inside
# resolve_layout() itself, not here.
_LAYOUT = resolve_layout()
CODE_TREE_DIRS = _LAYOUT["code_tree_dirs"]
print(f"[CONFIG] CODE_TREE_DIRS = {CODE_TREE_DIRS}")

# ─── MODEL SELECTION ─────────────────────────────────────────────────────────
# Default model used as a fallback for any role not listed in MODEL_BY_ROLE,
# and for direct callers that don't pass a role (e.g. one-off API calls).
#
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
# To change a role's model, edit the default here — or override at runtime via
# .env without touching code: MODEL_DEFAULT for the fallback, and MODEL_<ROLE>
# (e.g. MODEL_LEADER, MODEL_SPEC_WRITER) for a specific role.
# To add a new role, insert a new key; it will be picked up automatically by run_agent.
MODEL = os.getenv("MODEL_DEFAULT", "deepseek-v4-pro")

MODEL_BY_ROLE: dict[str, str] = {
    "leader":      os.getenv("MODEL_LEADER",      "deepseek-v4-pro"),    # orchestration requires multi-step reasoning
    "spec_writer": os.getenv("MODEL_SPEC_WRITER",  "deepseek-v4-flash"), # structured output from a clear template
    "implementer": os.getenv("MODEL_IMPLEMENTER",  "deepseek-v4-pro"),   # code generation benefits from the best model
    "reviewer":    os.getenv("MODEL_REVIEWER",     "deepseek-v4-flash"), # reads files, runs tests — no deep reasoning needed
    "e2e_tester":  os.getenv("MODEL_E2E_TESTER",   "deepseek-v4-flash"), # executes existing test scripts mechanically
    "compaction":  os.getenv("MODEL_COMPACTION",   "deepseek-v4-flash"), # unused since deterministic compaction (no LLM call) — kept for plugins that may still want a cheap model for this role
}

VERBOSE = True

# ─── ORCHESTRATOR SELECTION ──────────────────────────────────────────────────
# Controls whether the harness runs in plain Python mode or wraps execution
# in Prefect for dashboard observability, scheduling, and future parallelism.
#
#   "local"   — default, no external dependencies, identical runtime behavior.
#   "prefect" — each REPL command becomes a tracked Prefect flow run;
#               each feature cycle becomes a Prefect task visible in the dashboard.
#               Requires: pip install prefect
#               To activate: add ORCHESTRATOR=prefect to your .env
#               Optional: run `prefect cloud login` to stream runs to Prefect Cloud.
#               Without login, runs are tracked locally via the Prefect API
#               (start with `prefect server start`).
#
# No other code changes are needed when switching modes — the decorators are
# no-ops in local mode, so all logic, tools, and agent prompts stay identical.
ORCHESTRATOR = os.getenv("ORCHESTRATOR", "local")

# ─── COST BUDGET ──────────────────────────────────────────────────────────────
# Maximum USD spend per session. Set in .env as COST_BUDGET_USD=1.50
# When the budget is reached, no new features are started; the current agent
# finishes its step and then the harness stops gracefully.
# Set to 0 (default) to disable budget enforcement.
COST_BUDGET_USD = float(os.getenv("COST_BUDGET_USD", "0"))

# Module-level flag set by _track_usage when the budget is exceeded.
# Checked at the start of run_feature_cycle to skip new work without raising exceptions.
_BUDGET_EXCEEDED: bool = False

if ORCHESTRATOR == "prefect":
    from prefect import task, flow
else:
    # No-op decorators — make @task and @flow transparent in local mode.
    # Supports both bare usage (@task) and parameterized usage (@task(name="...")).
    def task(fn=None, **kwargs):  # type: ignore[misc]
        return fn if fn is not None else lambda f: f

    def flow(fn=None, **kwargs):  # type: ignore[misc]
        return fn if fn is not None else lambda f: f

# ─── ROBUSTNESS SETTINGS ─────────────────────────────────────────────────────
MAX_RETRIES_API    = 3   # Retries on transient API errors (rate limit, timeout)
MAX_RETRIES_IMPL   = 3   # How many times the implementer can retry a feature
MAX_RETRIES_REVIEW = 2   # How many times the impl→review cycle repeats before marking "failed"
MAX_ITER_LEADER    = 30  # Max iterations for the leader loop
MAX_ITER_AGENT     = 30  # Default — e2e_tester
MAX_ITER_IMPL      = 50  # Implementer: read context + write code + tests
MAX_ITER_REVIEWER  = 40  # Reviewer: read reports + run tests + mutation testing
MAX_ITER_SPEC      = int(os.getenv("MAX_ITER_SPEC", "35"))  # Spec writer: override via .env if specs need more iterations
RETRY_BACKOFF      = [2, 4, 8]  # seconds between API retries

# ─── FEATURE DESIGN RULE ─────────────────────────────────────────────────────
# Features should touch at most 4-5 files. Larger features should be split by
# the Leader into smaller sequential features (using depends_on) instead of
# being implemented as one oversized change. This keeps each implementer/
# reviewer cycle's context small and makes failures easier to localize and retry.

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

# ─── LLM PROVIDER RESILIENCE ─────────────────────────────────────────────────
# The harness supports an ordered fallback chain of OpenAI-compatible providers.
# On provider-level failures (auth errors, capacity overload) it automatically
# advances to the next configured provider and retries — transparent to all agents.
#
# Configuration (.env):
#
#   LLM_FALLBACK_CHAIN   Comma-separated provider names in priority order.
#                        Built-in names: deepseek, openai, groq
#                        Add a "custom" entry for any other OpenAI-compatible URL.
#                        Default: deepseek (single provider — existing behavior)
#
#   OPENAI_API_KEY       Required if "openai" is in the chain.
#   OPENAI_BASE_URL      Optional override (default: https://api.openai.com/v1)
#   GROQ_API_KEY         Required if "groq" is in the chain.
#   GROQ_BASE_URL        Optional override (default: https://api.groq.com/openai/v1)
#   CUSTOM_API_KEY       Required if "custom" is in the chain.
#   CUSTOM_BASE_URL      Required if "custom" is in the chain.
#
#   LLM_MODEL_MAP        JSON dict mapping canonical model names (DeepSeek names)
#                        to per-provider equivalents.
#                        Example:
#                          {"deepseek-v4-pro":{"openai":"gpt-4o","groq":"llama-3.3-70b-versatile"},
#                           "deepseek-v4-flash":{"openai":"gpt-4o-mini","groq":"llama-3.1-8b-instant"}}
#                        Models not listed fall through unchanged (useful when a
#                        fallback provider shares the same model name).

_PROVIDER_DEFAULTS = {
    "deepseek": {
        "key_env":  "DEEPSEEK_API_KEY",
        "base_url": "https://api.deepseek.com",
    },
    "openai": {
        "key_env":  "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    },
    "groq": {
        "key_env":  "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
    },
    "custom": {
        "key_env":  "CUSTOM_API_KEY",
        "base_url": "",               # must be set via CUSTOM_BASE_URL
    },
}

# Parse model map once at startup.
_raw_model_map = os.getenv("LLM_MODEL_MAP", "{}")
try:
    _MODEL_MAP: dict = json.loads(_raw_model_map)
except Exception:
    _MODEL_MAP = {}
    logging.warning("LLM_MODEL_MAP is not valid JSON — ignoring, using model names as-is")


def _resolve_model(canonical_name: str, provider_name: str) -> str:
    """Translate a canonical (DeepSeek-style) model name for a specific provider."""
    return _MODEL_MAP.get(canonical_name, {}).get(provider_name, canonical_name)


class _Provider:
    """A single LLM provider: name, OpenAI-compatible client, model resolver."""
    __slots__ = ("name", "client")

    def __init__(self, name: str, api_key: str, base_url: str) -> None:
        self.name   = name
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def resolve_model(self, canonical: str) -> str:
        return _resolve_model(canonical, self.name)

    def __repr__(self) -> str:
        return f"<Provider {self.name}>"


def _build_provider_chain() -> list:
    """
    Build the ordered provider list from LLM_FALLBACK_CHAIN.
    Providers whose API key env var is unset are silently skipped.
    Always returns at least [deepseek] so the harness can start.
    """
    chain_env = os.getenv("LLM_FALLBACK_CHAIN", "deepseek").strip()
    names     = [n.strip().lower() for n in chain_env.split(",") if n.strip()]

    providers = []
    for name in names:
        defaults = _PROVIDER_DEFAULTS.get(name, {"key_env": f"{name.upper()}_API_KEY", "base_url": ""})
        key_env  = defaults["key_env"]
        base_url = os.getenv(f"{name.upper()}_BASE_URL", defaults["base_url"])
        api_key  = os.getenv(key_env, "")

        if not api_key:
            logging.warning(f"[HARNESS] Provider '{name}' skipped — {key_env} not set")
            continue
        if not base_url:
            logging.warning(f"[HARNESS] Provider '{name}' skipped — base URL not configured")
            continue

        providers.append(_Provider(name, api_key, base_url))

    if not providers:
        # Absolute fallback: build DeepSeek provider even if key is empty so the
        # harness at least starts and surfaces a useful auth error on first call.
        defaults = _PROVIDER_DEFAULTS["deepseek"]
        providers.append(_Provider("deepseek", os.getenv(defaults["key_env"], ""), defaults["base_url"]))

    return providers


_PROVIDERS: list = _build_provider_chain()

# Keep a module-level `client` alias pointing at the primary provider so any
# code outside run_agent / the leader loop that still uses `client` directly
# (e.g. third-party plugins written before this change) keeps working.
client = _PROVIDERS[0].client


def _call_api_with_fallback(
    model:    str,
    messages: list,
    tools:    list,
    role:     str,
) -> object:
    """
    Call chat.completions.create with automatic provider fallback.

    Retry strategy per provider:
      TRANSIENT       → retry up to MAX_RETRIES_API times with RETRY_BACKOFF
      PROVIDER_FAILURE → skip remaining retries, try next provider immediately
      other fatal     → skip remaining retries, try next provider

    Returns the raw API response object on success, or None if every provider
    is exhausted (caller is responsible for handling None).
    """
    for p_idx, provider in enumerate(_PROVIDERS):
        resolved = provider.resolve_model(model)
        if p_idx > 0:
            _log(role, "PROVIDER_SWITCH",
                 f"switching to provider '{provider.name}' (model={resolved})", level="warning")

        for attempt in range(MAX_RETRIES_API):
            try:
                response = provider.client.chat.completions.create(
                    model=resolved,
                    messages=messages,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                )
                if p_idx > 0:
                    _log(role, "PROVIDER_FALLBACK_OK",
                         f"succeeded on '{provider.name}' after primary failed")
                return response

            except Exception as exc:
                err_type = _classify_error(str(exc))

                if err_type == "TRANSIENT" and attempt < MAX_RETRIES_API - 1:
                    wait = RETRY_BACKOFF[attempt]
                    _log(role, "API_RETRY",
                         f"provider={provider.name} attempt {attempt+1}/{MAX_RETRIES_API} "
                         f"— wait {wait}s — {exc}", level="warning")
                    time.sleep(wait)

                elif err_type == "PROVIDER_FAILURE":
                    _log(role, "PROVIDER_FAILURE",
                         f"provider='{provider.name}' — {exc}", level="warning")
                    break   # skip to next provider immediately

                else:
                    # TRANSIENT exhausted or FATAL: try next provider
                    _log(role, "API_FATAL",
                         f"provider='{provider.name}' — {exc}", level="error")
                    break

    _log(role, "ALL_PROVIDERS_EXHAUSTED",
         f"all {len(_PROVIDERS)} provider(s) failed for role={role}", level="error")
    return None

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
    """Accumulate tokens from each API call by role. Triggers budget enforcement if enabled."""
    global _BUDGET_EXCEEDED
    if usage is None:
        return
    bucket = _SESSION_COSTS.get(role, _SESSION_COSTS["leader"])
    bucket["prompt_tokens"]     += getattr(usage, "prompt_tokens", 0)
    bucket["completion_tokens"] += getattr(usage, "completion_tokens", 0)
    bucket["calls"]             += 1

    if COST_BUDGET_USD > 0 and not _BUDGET_EXCEEDED:
        total_prompt     = sum(v["prompt_tokens"]     for v in _SESSION_COSTS.values())
        total_completion = sum(v["completion_tokens"] for v in _SESSION_COSTS.values())
        current_usd      = total_prompt * _PRICE_INPUT + total_completion * _PRICE_OUTPUT
        if current_usd >= COST_BUDGET_USD:
            _BUDGET_EXCEEDED = True
            _log("harness", "BUDGET_EXCEEDED",
                 f"USD {current_usd:.4f} >= limit {COST_BUDGET_USD:.2f}", level="warning")
            console.print(Panel(
                f"[yellow]Spent: USD {current_usd:.4f}  ·  Limit: USD {COST_BUDGET_USD:.2f}[/]\n"
                "[dim]Current agent step will finish. No new features will be started.[/]",
                title="[yellow]⚠ Session budget reached[/]",
                border_style="yellow",
                padding=(0, 1)
            ))

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


# ─── CHECKPOINTING & DURABLE RESUMABILITY ────────────────────────────────────
#
# Every feature cycle writes a lightweight checkpoint after each completed step
# so a crash mid-cycle can resume where it stopped rather than starting over.
#
# The checkpoint is stored as a "_checkpoint" field inside the feature's entry
# in feature_list.json — same file, no extra dependencies.
#
# Step progression:
#   (none)      fresh start — run all steps
#   spec_done   spec written; next restart skips spawn_spec_writer
#   impl_done   impl written for attempt N; next restart skips spawn_implementer
#   e2e_done    e2e passed for attempt N; next restart skips spawn_e2e_tester
#
# On approval or final failure the checkpoint is cleared so the field does not
# persist in the completed feature entry.
#
# The storage_backend premium plugin (if loaded) will sync the checkpoint to
# the configured backend automatically via the before_feature hook — no extra
# wiring needed.

def _read_feature_list_raw() -> list:
    """Read feature_list.json. Returns [] on any error."""
    try:
        with open("feature_list.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_feature_list_raw(features: list) -> None:
    """Overwrite feature_list.json. Silently ignores write errors."""
    try:
        with open("feature_list.json", "w", encoding="utf-8") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        _log("harness", "CHECKPOINT_WRITE_ERROR", str(exc), level="warning")


def _save_checkpoint(feature_id: int, step: str, attempt: int = 1) -> None:
    """
    Write a checkpoint for feature_id after completing `step`.
    step must be one of: "spec_done", "impl_done", "e2e_done".
    """
    features = _read_feature_list_raw()
    for feat in features:
        if feat.get("id") == feature_id:
            feat["_checkpoint"] = {
                "step":        step,
                "attempt":     attempt,
                "saved_at":    datetime.datetime.now().isoformat(timespec="seconds"),
            }
            break
    _write_feature_list_raw(features)
    _log("harness", "CHECKPOINT_SAVED", f"feature={feature_id} step={step} attempt={attempt}")


def _load_checkpoint(feature_id: int) -> Optional[dict]:
    """
    Return the stored checkpoint dict for feature_id, or None if absent.
    """
    for feat in _read_feature_list_raw():
        if feat.get("id") == feature_id:
            return feat.get("_checkpoint") or None
    return None


def _clear_checkpoint(feature_id: int) -> None:
    """Remove the _checkpoint field from feature_id's entry."""
    features = _read_feature_list_raw()
    for feat in features:
        if feat.get("id") == feature_id:
            feat.pop("_checkpoint", None)
            break
    _write_feature_list_raw(features)
    _log("harness", "CHECKPOINT_CLEARED", f"feature={feature_id}")


# ─── MID-RUN RESUMABILITY (messages snapshot) ────────────────────────────────
#
# _save_checkpoint above only marks whole steps done (spec_done/impl_done/
# e2e_done) — coarse, feature-level granularity. If the process crashes
# *during* a single run_agent() loop (e.g. mid e2e_tester attempt), that
# entire attempt's tool-call history was lost and the next run restarted the
# role from scratch with only a generic task string. These helpers persist
# the live `messages` list to disk after every iteration, keyed by
# checkpoint_key (built by each spawn_* as f"{role}_{feature_id}_{attempt}"),
# so run_agent can resume an in-progress conversation instead of redoing it.
# The file is deleted on any clean return (verdict reached or max_iter
# exhausted) — it should only ever be found on disk after a genuine crash.

def _message_state_path(checkpoint_key: str) -> str:
    # No "." in the allowed set (deliberately) — checkpoint_key is built
    # internally as f"{role}_{feature_id}_{attempt}" and never needs one;
    # excluding it means a stray ".." can never survive sanitization either.
    safe_key = re.sub(r"[^A-Za-z0-9_-]", "_", checkpoint_key)
    return os.path.join("progress", f"_state_{safe_key}.json")


def _serialize_message(m) -> dict:
    """Convert a message (dict or pydantic ChatCompletionMessage) into a plain
    JSON-safe dict, preserving tool_calls shape so it can be replayed back to
    the API on resume."""
    if isinstance(m, dict):
        return m

    out = {"role": _msg_field(m, "role", "assistant"), "content": _msg_field(m, "content", "") or ""}
    tool_calls = getattr(m, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
        if not out["content"]:
            out["content"] = None
    return out


def _save_message_state(checkpoint_key: str, messages: list) -> None:
    """Best-effort: persist the in-progress conversation. Never raises — a
    failure here must not interrupt the agent loop it's trying to protect."""
    if not checkpoint_key:
        return
    try:
        path = _message_state_path(checkpoint_key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        serialized = [_serialize_message(m) for m in messages]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "checkpoint_key": checkpoint_key,
                "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "messages": serialized,
            }, f, ensure_ascii=False)
    except Exception as exc:
        _log("harness", "MESSAGE_STATE_SAVE_ERROR", f"key={checkpoint_key} err={exc}", level="warning")


def _load_message_state(checkpoint_key: str) -> Optional[list]:
    """Return the persisted messages list for checkpoint_key, or None if
    absent/corrupt. Corrupt state is treated as absent (fresh start) rather
    than raising — resumability must never block a retry."""
    if not checkpoint_key:
        return None
    path = _message_state_path(checkpoint_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        messages = data.get("messages")
        return messages if isinstance(messages, list) and messages else None
    except Exception as exc:
        _log("harness", "MESSAGE_STATE_LOAD_ERROR", f"key={checkpoint_key} err={exc}", level="warning")
        return None


def _clear_message_state(checkpoint_key: str) -> None:
    """Remove the persisted snapshot — called on any clean run_agent return."""
    if not checkpoint_key:
        return
    try:
        path = _message_state_path(checkpoint_key)
        if os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        _log("harness", "MESSAGE_STATE_CLEAR_ERROR", f"key={checkpoint_key} err={exc}", level="warning")


def recover_stale_features() -> list[int]:
    """
    On startup, detect features stuck in 'in_progress' from a previous crash.

    Unlike earlier versions, this does NOT discard the checkpoint — if a feature
    was mid-cycle when the crash happened, the checkpoint records how far it got
    and run_feature_cycle will resume from that step automatically.

    Returns list of recovered feature IDs.
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
            checkpoint = feat.get("_checkpoint")
            if checkpoint:
                feat["recovery_note"] = (
                    f"Reset to pending by harness on startup (crashed at "
                    f"step={checkpoint['step']}, attempt={checkpoint['attempt']}) — "
                    f"will resume from checkpoint"
                )
            else:
                feat["recovery_note"] = (
                    "Reset to pending by harness on startup (possible previous crash)"
                )
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

def _msg_tool_calls(m) -> list:
    """Uniformly read tool_calls off a message that can be dict or pydantic."""
    if isinstance(m, dict):
        return m.get("tool_calls") or []
    return getattr(m, "tool_calls", None) or []


def _tool_call_name_args(tc):
    """Uniformly read (name, arguments) off a tool_call that can be dict or pydantic."""
    if isinstance(tc, dict):
        fn = tc.get("function", {}) or {}
        return fn.get("name", "?"), fn.get("arguments", "")
    return tc.function.name, tc.function.arguments


def _build_deterministic_digest(middle: list) -> str:
    """
    Build a digest of the middle message block by extracting facts directly
    from the messages — no LLM call, nothing paraphrased away.

    This replaces the old "ask a model to summarize in 400 words" approach,
    which was the root cause of the 2026-06-18 incident on feature 26: a
    free-text summary has no guarantee it mentions "X is already confirmed",
    so after every compaction the agent re-explored ground it had already
    covered, burning iterations until max_iter was hit without ever reaching
    a verdict. A deterministic list of tool calls/errors/decisions can't lose
    that information the way a lossy rewrite can.
    """
    decisions: list = []
    calls: list = []
    errors: list = []

    for m in middle:
        role = (_msg_field(m, "role", "") or "").lower()

        if role == "assistant":
            content = _msg_field(m, "content", "") or ""
            if isinstance(content, str) and content.strip():
                decisions.append(content.strip()[:200])
            for tc in _msg_tool_calls(m):
                name, args = _tool_call_name_args(tc)
                calls.append(f"{name}({str(args)[:120]})")

        elif role == "tool":
            content = _msg_field(m, "content", "") or ""
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            content = str(content)
            flagged = False
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "error" in parsed:
                    errors.append(str(parsed["error"])[:200])
                    flagged = True
            except Exception:
                pass
            if not flagged and ("traceback" in content.lower() or "exception" in content.lower()):
                errors.append(content[:200])

    lines = ["## Previous context summary (deterministic — extracted directly from messages, not LLM-rewritten)"]
    lines.append(f"- {len(calls)} tool call(s) already executed in this block, {len(errors)} returned an error.")

    if calls:
        lines.append("### Tool calls already made (do NOT repeat these unless something changed):")
        tail_calls = calls[-30:]
        if len(calls) > len(tail_calls):
            lines.append(f"  - ... {len(calls) - len(tail_calls)} earlier call(s) omitted (oldest-first) ...")
        for c in tail_calls:
            lines.append(f"  - {c}")

    if errors:
        lines.append("### Errors encountered (already known — don't rediscover, fix or work around):")
        for e in errors[-10:]:
            lines.append(f"  - {e}")

    if decisions:
        lines.append("### Notes / decisions already made:")
        for d in decisions[-10:]:
            lines.append(f"  - {d}")

    return "\n".join(lines)


def _compact_messages(messages: list, role: str) -> list:
    """
    When history exceeds COMPACT_THRESHOLD messages, replace the middle block
    with a deterministic digest to avoid exceeding the context window.
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

    _log(role, "COMPACTING",
         f"Compacting {len(middle)} intermediate messages (total={len(messages)})")

    try:
        summary_text = _build_deterministic_digest(middle)
    except Exception as e:
        # Best-effort, never block the pipeline — fall back to a minimal note
        # rather than crash the agent loop over a digest-formatting bug.
        summary_text = f"(digest unavailable: {e})"
        _log(role, "DIGEST_ERROR", str(e), level="warning")

    compact_msg = {
        "role": "system",
        "content": summary_text
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

def _verdict_is(result: str, marker: str) -> bool:
    """
    Case- and punctuation-tolerant check for whether an agent's verdict
    string starts with the expected marker (e.g. "APPROVED", "E2E_PASSED",
    "E2E_FAILED"). LLMs are not perfectly consistent about exact verdict
    formatting — "Approved.", "approved", "APPROVED:" should all count as
    the marker, not be silently misread as a rejection and waste a full
    retry. Only the marker-length prefix is compared, so trailing
    punctuation/text after it (the actual reason, e.g. "REJECTED: ...")
    never affects the match.
    """
    cleaned = result.strip()
    return cleaned[:len(marker)].upper() == marker.upper()


def _classify_error(error_msg: str) -> str:
    """
    Classify an error to decide the retry strategy.
    TRANSIENT        → retryable with backoff on the same provider (rate limit, timeout)
    PROVIDER_FAILURE → provider-level failure; skip to next provider (auth, overloaded)
    LOGICAL          → requires a different approach (logic error, test failure)
    FATAL            → stop (critical file not found, unrecoverable)
    """
    msg = error_msg.lower()
    if any(k in msg for k in ("rate limit", "timeout", "connection", "503", "502", "429")):
        return "TRANSIENT"
    if any(k in msg for k in ("401", "403", "529", "authentication", "unauthorized",
                               "invalid api key", "overloaded", "capacity")):
        return "PROVIDER_FAILURE"
    if any(k in msg for k in ("max_iter", "blocked", "assertion", "error:")):
        return "LOGICAL"
    return "FATAL"

# ─── GENERIC AGENT ENGINE ────────────────────────────────────────────────────

def run_agent(system_prompt: str, tools: list, task: str,
              role: str = "agent", color: str = "white",
              max_iter: int = MAX_ITER_AGENT,
              checkpoint_key: Optional[str] = None) -> str:
    # Mid-run resumability: if a previous run with this exact checkpoint_key
    # crashed before reaching a verdict, its message history was snapshotted
    # to disk — resume from it instead of rebuilding system/user from scratch
    # and re-paying for every tool call that already happened. checkpoint_key
    # is None for any caller that hasn't opted in (back-compat: behaves
    # exactly as before).
    resumed = _load_message_state(checkpoint_key) if checkpoint_key else None
    if resumed:
        messages = resumed
        _log(role, "RESUME",
             f"key={checkpoint_key} resumed {len(messages)} messages from a previous crashed run")
        console.print(
            f"  [dim]↺ {role}: resuming mid-run from a saved snapshot "
            f"({len(messages)} messages, no redo)[/]"
        )
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": task}
        ]
    _log(role, "START", task[:120])
    tool_call_errors: list = []

    # Generic, role-agnostic budget-checkpoint enforcement. Previously this
    # was only advisory prompt text in agents/e2e_tester.py's "BUDGET
    # CHECKPOINT" section — easy for the model to under-attend to as a run
    # grows, since it's static text set once at the start of the conversation.
    # This injects a live, recency-weighted reminder straight into `messages`
    # at fixed points in the iteration budget, for every role, with no
    # role-specific detection needed (it doesn't try to guess whether a test
    # file was written — it just tells the agent how much budget is left and
    # to act on partial progress rather than keep exploring).
    _budget_warn_iters = {int(max_iter * 0.6), int(max_iter * 0.85)} - {max_iter - 1}

    for i in range(max_iter):
        if i in _budget_warn_iters:
            messages.append({
                "role": "user",
                "content": (
                    f"⚠️ BUDGET CHECKPOINT: you have used {i}/{max_iter} iterations. "
                    "If you haven't produced your required output yet (spec/impl/test "
                    "file and progress report), stop exploring now and write it with "
                    "what you already have — a concrete partial result beats running "
                    "out of iterations with nothing written."
                )
            })
        if i == max_iter - 1:
            messages.append({
                "role": "user",
                "content": (
                    "⚠️ FINAL ITERATION: this is your last tool call before the harness "
                    "cuts you off. Write your progress report and return your verdict "
                    "NOW with whatever you have, even if incomplete — do not start any "
                    "new exploration."
                )
            })

        api_response = _call_api_with_fallback(
            model    = MODEL_BY_ROLE.get(role, MODEL),
            messages = messages,
            tools    = tools,
            role     = role,
        )
        if api_response is None:
            # Provider-level failure, not a normal completion — leave any
            # saved snapshot in place so a retry with the same checkpoint_key
            # resumes from here instead of starting the attempt over.
            return f"[ERROR: all LLM providers exhausted for role={role}]"

        _track_usage(role, api_response.usage)
        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log(role, "DONE", (msg.content or "")[:120])
            _clear_message_state(checkpoint_key)
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

            # Best-effort: track tool-call errors so that if this attempt hits
            # max_iter without a verdict, the next retry gets concrete context
            # instead of restarting from scratch with the same generic task.
            try:
                parsed_err = json.loads(result)
                if isinstance(parsed_err, dict) and "error" in parsed_err:
                    tool_call_errors.append(f"{fn_name}({args_preview}) -> Error: {str(parsed_err['error'])[:150]}")
                    tool_call_errors[:] = tool_call_errors[-5:]
            except Exception:
                pass

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

        # Snapshot after this iteration's tool calls are recorded — a crash
        # past this point loses at most the current iteration, not the whole
        # attempt. Best-effort and cheap (local disk write, no LLM call).
        if checkpoint_key:
            _save_message_state(checkpoint_key, messages)

    _log(role, "MAX_ITER", f"Reached iteration limit of {max_iter}", level="warning")
    _clear_message_state(checkpoint_key)
    if tool_call_errors:
        recent_errors = "\n".join(f"  - {e}" for e in tool_call_errors[-5:])
        return f"[ERROR: max_iter {max_iter} reached]\nRecent tool-call errors:\n{recent_errors}"
    return f"[ERROR: max_iter {max_iter} reached]"


# ─── PLUGIN / HOOK SYSTEM ────────────────────────────────────────────────────
#
# The harness fires lifecycle events at key points in the pipeline. Plugins
# register Python callables for these events via register_hook() at import time.
#
# This is the extension mechanism for the open-core model: the public harness
# ships with an empty plugins/ directory; a premium fork drops additional
# modules there without ever touching harness.py or any base file.
#
# All callbacks receive **kwargs so plugins stay compatible when new arguments
# are added to an event in future versions — always add **kwargs to signatures.
# Errors inside individual callbacks are caught and logged; a buggy plugin
# never crashes the harness.

_HOOKS: dict[str, list] = {
    # Fired at the start of run_feature_cycle, before spec or code is written.
    # kwargs: feature_id (int), description (str), e2e (bool)
    "before_feature": [],

    # Fired immediately before each agent is invoked, allowing plugins to
    # override or augment the system_prompt and/or task string for that role.
    # Unlike _fire(), this uses _fire_transform() which threads return values
    # back to the caller. Each callback receives:
    #   role (str)          — "spec_writer" | "implementer" | "reviewer" | "e2e_tester"
    #   system_prompt (str) — the prompt that would be sent as-is
    #   task (str)          — the user-turn task string
    #   feature_id (int)    — current feature being processed
    # Return a dict with any subset of keys to override:
    #   {"system_prompt": "...", "task": "..."}
    # Return None / {} / any falsy value to keep the originals unchanged.
    # Callbacks run in registration order; each sees the output of the previous.
    "before_spawn_agent": [],

    # Fired after spawn_spec_writer finishes and validation runs.
    # kwargs: feature_id (int), spec_path (str), issues (list[str])
    "after_spec_generated": [],

    # Fired right after the Reviewer returns an APPROVED verdict, BEFORE the
    # harness commits to it. This is the one hook where callbacks can change
    # the outcome rather than just observe it: return {"block": True,
    # "reason": "..."} to veto the approval, or None/falsy for "no opinion".
    # A veto is treated exactly like a normal Reviewer rejection — it feeds
    # the existing retry loop, and after_feature_failed fires the usual way
    # once retries are exhausted. Dispatch with _fire_gate(), not _fire().
    # kwargs: feature_id (int), description (str), attempt (int), review_result (str)
    "before_approval_finalized": [],

    # Fired when the Reviewer approves a feature cycle (and no plugin vetoed
    # it via before_approval_finalized).
    # kwargs: feature_id (int), description (str), attempts (int)
    "after_feature_approved": [],

    # Fired when a feature exhausts all retries and is marked failed.
    # kwargs: feature_id (int), description (str), attempts (int), final_verdict (str)
    "after_feature_failed": [],

    # Fired once when the harness exits — even on crash (called from finally).
    # kwargs: session_costs (dict)  — same structure as progress/session_costs.json
    "after_session": [],
}


def register_hook(event: str, fn) -> None:
    """
    Register a callback for a lifecycle event.

    Plugins call this at module import time:

        from harness import register_hook

        def my_callback(feature_id, **kwargs):
            ...

        register_hook("after_feature_approved", my_callback)

    Safe to call multiple times with different functions for the same event.
    Registering an unknown event name logs a warning and is ignored.
    """
    if event not in _HOOKS:
        _log("harness", "UNKNOWN_HOOK",
             f"Plugin tried to register unknown event '{event}'. "
             f"Valid events: {list(_HOOKS)}", level="warning")
        return
    _HOOKS[event].append(fn)


def _fire(event: str, **kwargs) -> None:
    """
    Invoke all callbacks registered for an event.
    Errors in individual callbacks are caught and logged so a buggy plugin
    never interrupts the pipeline.
    """
    for fn in _HOOKS.get(event, []):
        try:
            fn(**kwargs)
        except Exception as exc:
            _log("harness", "HOOK_ERROR",
                 f"event={event} fn={getattr(fn, '__name__', fn)} error={exc}",
                 level="error")
            console.print(f"  [red]✗ plugin error[/] [{event}] {exc}")


def _fire_gate(event: str, **kwargs) -> Optional[dict]:
    """
    Like _fire(), but for the one kind of event where a plugin can change an
    in-flight decision instead of merely observing it (currently just
    "before_approval_finalized").

    Each registered callback receives **kwargs and may return:
      - None / any falsy value         → no opinion, proceed as normal
      - {"block": True, "reason": str} → veto; the harness folds this into
        its existing rejection/retry handling for that stage, so no new
        states or failure paths are introduced

    The FIRST callback that returns a truthy "block" wins the decision —
    every callback still runs (so each plugin's own logging/bookkeeping
    happens regardless), but only the first veto is honored. This keeps the
    contract simple: "any plugin can say no", with predictable attribution
    in the logs.

    Error isolation matches _fire() exactly: an exception in a callback is
    caught, logged, and counts as "no opinion" — a buggy plugin can change
    nothing and crash nothing.
    """
    decision = None
    for fn in _HOOKS.get(event, []):
        try:
            result = fn(**kwargs)
        except Exception as exc:
            _log("harness", "HOOK_ERROR",
                 f"event={event} fn={getattr(fn, '__name__', fn)} error={exc}",
                 level="error")
            console.print(f"  [red]✗ plugin error[/] [{event}] {exc}")
            continue
        if decision is None and isinstance(result, dict) and result.get("block"):
            decision = {
                "block": True,
                "reason": result.get("reason", ""),
                "plugin": getattr(fn, "__module__", "?"),
            }
    return decision


def _fire_transform(event: str, **kwargs) -> dict:
    """
    Like _fire(), but for hooks that can transform their inputs rather than
    just observe them (currently only "before_spawn_agent").

    Each registered callback receives **kwargs and may return:
      - None / {} / any falsy value  → no change, pass inputs through unchanged
      - {"system_prompt": "...", "task": "..."}  → override one or both fields

    Callbacks run in registration order and chain: each callback receives the
    output of the previous one, so multiple plugins can each contribute a
    transformation without stomping on each other.

    The final (possibly modified) values are returned as a dict with the same
    keys as the input kwargs. If no callback modifies a key, its original
    value is preserved unchanged.

    Error isolation matches _fire() exactly.
    """
    current = dict(kwargs)
    for fn in _HOOKS.get(event, []):
        try:
            result = fn(**current)
        except Exception as exc:
            _log("harness", "HOOK_ERROR",
                 f"event={event} fn={getattr(fn, '__name__', fn)} error={exc}",
                 level="error")
            console.print(f"  [red]✗ plugin error[/] [{event}] {exc}")
            continue
        if result and isinstance(result, dict):
            current.update(result)
    return current


def _load_plugins() -> None:
    """
    Auto-load all *.py modules found in the plugins/ directory.

    Each module is imported once at startup. Plugins register their hooks
    via register_hook() at import time — no explicit activation needed beyond
    dropping the file in the directory.

    Naming rules:
      - Any file ending in .py is loaded, in alphabetical order.
      - Files starting with _ are skipped (use _disabled_plugin.py to park code).

    Errors in individual plugins are caught, logged, and skipped — a broken
    plugin never prevents the harness from starting.

    If plugins/ is absent or empty, this function is a no-op.
    """
    import sys
    # Critical fix (2026-06-19): harness.py runs as __main__, so plugins doing
    # `from harness import register_hook` don't find "harness" in sys.modules
    # and Python re-executes this whole file as a SECOND module object with
    # its own empty _HOOKS dict. Every register_hook() call then lands on that
    # phantom copy instead of the one this running process reads from in
    # _fire/_fire_gate/_fire_transform -- so every plugin hook silently never
    # fires. Aliasing "harness" to this already-running module before any
    # plugin loads fixes it.
    sys.modules.setdefault("harness", sys.modules["__main__"])

    import importlib.util

    plugin_dir = "plugins"
    if not os.path.isdir(plugin_dir):
        return

    plugin_files = sorted(
        f for f in os.listdir(plugin_dir)
        if f.endswith(".py") and not f.startswith("_")
    )
    if not plugin_files:
        return

    console.print(
        f"  [dim]Plugins: {', '.join(p[:-3] for p in plugin_files)}[/]"
    )

    for filename in plugin_files:
        module_name = f"harness_plugin_{filename[:-3]}"
        plugin_path = os.path.join(plugin_dir, filename)
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _log("harness", "PLUGIN_LOADED", filename)
        except Exception as exc:
            _log("harness", "PLUGIN_ERROR", f"{filename}: {exc}", level="error")
            console.print(f"  [red]✗ failed to load plugin[/] {filename} — {exc}")


# ─── SPEC VALIDATION ─────────────────────────────────────────────────────────

def _validate_spec(spec_path: str) -> str:
    """
    Cross-check a freshly generated spec against the existing codebase using a
    single cheap LLM call.

    What it catches:
      - References to files that don't exist yet and aren't being created
        by this feature (wrong import paths, missing modules).
      - Interface assumptions that contradict what's already in src/
        (e.g. spec says User.from_dict() but existing code has User.load()).
      - Duplicate work — spec asks to create a file that already exists
        with the same responsibility.

    What it does NOT do:
      - It does not re-run the spec writer or block the pipeline.
      - It does not fail loudly — if the validation call fails for any reason
        (network, timeout, unexpected response), the harness continues normally.
      - It is non-blocking by design: issues are appended to the spec file as
        a warning section so the implementer sees them and can compensate.

    Returns a string with the identified issues, or an empty string if none.
    Uses MODEL_BY_ROLE["spec_writer"] (typically a flash model) to keep cost low.
    """
    try:
        with open(spec_path, "r", encoding="utf-8") as f:
            spec_content = f.read()
    except Exception:
        return ""

    tree_src   = _file_tree("src")
    tree_tests = _file_tree("tests")

    try:
        response = _call_api_with_fallback(
            model    = MODEL_BY_ROLE.get("spec_writer", MODEL),
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a senior engineer doing a quick pre-implementation spec review. "
                        "Your only job is to find concrete contradictions or false assumptions "
                        "between the spec and the existing codebase. "
                        "Be brief and specific — one line per issue. "
                        "If nothing is wrong, reply with exactly the word: OK"
                    )
                },
                {
                    "role": "user",
                    "content": (
                        f"Spec to review:\n{spec_content[:3000]}\n\n"
                        f"Existing files in src/:\n{tree_src}\n\n"
                        f"Existing files in tests/:\n{tree_tests}\n\n"
                        "List only concrete issues: wrong file paths, conflicting interfaces, "
                        "duplicate responsibilities, or missing prerequisite files. "
                        "Ignore style and completeness. If none, reply: OK"
                    )
                }
            ],
            tools = [],
            role  = "spec_writer",
        )
        if response is None:
            return ""
        _track_usage("spec_writer", response.usage)
        result = (response.choices[0].message.content or "").strip()
        return "" if result.upper() == "OK" else result
    except Exception:
        return ""  # validation is best-effort — never block the pipeline


# ─── RETRY CONTEXT EXTRACTION ────────────────────────────────────────────────

def _extract_retry_context(rejection_reason: str) -> str:
    """
    Distill a reviewer rejection into the minimal actionable context needed
    for the next implementation attempt.

    Why this matters: injecting the full rejection reason on every retry bloats
    the implementer's context with stack traces, passing test output, and prose
    that isn't actionable. This function extracts only:
      1. The names of specifically failing tests (pytest FAILED lines).
      2. The first unique assertion / exception message per test.
      3. Any explicit file-level issues mentioned in the rejection.

    Falls back to a truncated version of the raw reason if no structured output
    is detected (e.g. the reviewer returned plain prose instead of pytest output).

    The goal is to reduce per-retry token consumption by 40–70% while making
    the injected context *more* targeted, not less.
    """
    import re

    lines = rejection_reason.splitlines()

    # ── Collect failing test identifiers (pytest format) ─────────────────────
    # Matches: "FAILED tests/test_auth.py::test_login_invalid_password"
    failed_tests = [
        l.strip() for l in lines
        if re.match(r"FAILED\s+\S+::\S+", l.strip())
    ]

    # ── Collect the first unique error/assertion line per block ───────────────
    error_keywords = ("AssertionError", "Error:", "assert ", "TypeError",
                      "ValueError", "AttributeError", "ImportError", "FAILED")
    seen_errors: set[str] = set()
    error_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if any(kw in stripped for kw in error_keywords):
            # Normalize whitespace to deduplicate similar messages
            key = re.sub(r"\s+", " ", stripped)[:120]
            if key not in seen_errors:
                seen_errors.add(key)
                error_lines.append(stripped)
        if len(error_lines) >= 6:  # cap to avoid bloating context
            break

    # ── Build the distilled context ───────────────────────────────────────────
    if failed_tests or error_lines:
        parts: list[str] = []
        if failed_tests:
            parts.append(
                "Failing tests (fix these specifically):\n"
                + "\n".join(f"  - {t}" for t in failed_tests[:10])
            )
        if error_lines:
            parts.append(
                "Key errors:\n"
                + "\n".join(f"  {e}" for e in error_lines)
            )
        return "\n".join(parts)

    # ── Fallback: no structured output detected ───────────────────────────────
    # Truncate to avoid bloating the implementer's context on prose rejections.
    if len(rejection_reason) > 600:
        return rejection_reason[:600] + "\n[...truncated — fix the issues above]"
    return rejection_reason


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


def _load_project_architecture(cwd: str) -> str:
    """
    Best-effort load of a project-supplied architecture override.

    If docs/architecture.md exists in the project root, its content describes
    the REAL stack/layout of this specific project and is injected into every
    agent's task as an authoritative section that overrides the generic
    example architecture baked into each agent's system prompt (which is a
    fallback for projects that haven't supplied their own docs/architecture.md,
    and will not match every stack — e.g. SQL+ORM projects vs JSON-storage ones).

    Returns "" if the file is absent, empty, or unreadable — never blocks the
    pipeline on a missing/malformed docs file.
    """
    path = os.path.join(cwd, "docs", "architecture.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return ""
        return (
            "\n## PROJECT ARCHITECTURE (authoritative — from docs/architecture.md)\n"
            "This describes the REAL architecture of this project. It overrides any\n"
            "generic example architecture mentioned in your system prompt.\n\n"
            f"{content}\n"
        )
    except Exception:
        return ""


def _layout_context() -> str:
    """
    Authoritative test/server commands for the active stack, from the single
    source of truth (_LAYOUT, via stack_layout.resolve_layout()). Injected into
    every agent's task so agents/*.py prompts can stay stack-neutral instead of
    hardcoding 'python3 -m pytest backend/tests/' or similar literal commands
    that only match the python-fastapi+backend/app/ example shape.
    """
    return (
        "\n## STACK COMMANDS (authoritative — from stack_profiles.json)\n"
        f"Run tests with: {_LAYOUT['test_runner']}\n"
        f"Start the server with: {_LAYOUT['server_cmd']}\n"
        f"Project layout: {_LAYOUT['dirs']}\n"
    )


def _workdir_banner(cwd: str) -> str:
    """
    Single source of truth for the WORKING DIRECTORY text injected into every
    agent's task. Replaces 4 slightly-drifted copies that all told agents to
    `cd <WORKING_DIR>` or "use it in EVERY bash command" — which is wrong
    under the default SANDBOX_MODE=docker, where run_bash's project root is
    bind-mounted at /workspace and working_dir is already set there; the host
    absolute path given below doesn't exist inside the container at all. Under
    SANDBOX_MODE=local it "worked" only because cd-ing into the cwd you're
    already in is a no-op — never because the instruction was actually correct.

    The fix that's true in BOTH modes: run_bash already starts in the project
    root, so agents never need to cd or prefix a command with this path —
    just run relative commands directly. Also flags that run_bash is stateless
    across calls (a fresh container in Docker mode, a fresh subprocess in
    local mode) — a cd in one call has zero effect on the next one, which was
    silently causing "wrong directory" confusion and burned retries.
    """
    return (
        f"WORKING DIRECTORY: {cwd}\n"
        "This is informational only (e.g. for your reports) — do NOT cd into it and do NOT "
        "prefix commands or paths with it. run_bash already starts in the project root in "
        "every mode; just run relative commands directly, e.g. run_bash(\"pytest tests/ -v\"). "
        "Each run_bash call is independent — a cd in one call does NOT carry over to the next "
        "one, so to work inside a subdirectory, chain it in a single command: "
        "run_bash(\"cd frontend && npm test\"). read_file/write_file/list_files/append_file also "
        "take paths relative to this directory — never prefix those with it or with /workspace "
        "either.\n\n"
    )


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
        retry_context = _extract_retry_context(rejection_reason)
        context = (
            f"\n\n⚠️  RETRY #{attempt} — The reviewer rejected the previous attempt.\n"
            f"Fix exactly these issues (do not rewrite unrelated code):\n"
            f"{retry_context}"
        )

    _phase_header("implementer", "Implementing", feature_id, attempt)
    _log("implementer", "SPAWN", f"feature={feature_id} attempt={attempt}")

    cwd = os.getcwd()

    # Pre-inject file tree to avoid exploratory reads (dirs configurable via CODE_TREE_DIRS)
    tree_sections = "\n\n".join(
        f"## Current file tree ({d}/):\n{_file_tree(d) if os.path.exists(d) else '(not created yet)'}"
        for d in CODE_TREE_DIRS
    )
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()

    spec_content = ""
    if spec_path and os.path.exists(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                spec_content = f"\n## Technical specification ({spec_path}):\n{f.read()}\n"
        except Exception:
            spec_content = f"\nRead the technical specification at {spec_path} BEFORE writing code.\n"

    task = (
        f"{_workdir_banner(cwd)}"
        f"{tree_sections}\n"
        f"{arch_context}"
        f"{layout_context}"
        f"{spec_content}\n"
        f"Implement feature #{feature_id}: {description}{context}\n"
        f"Write your report to {impl_path}\n"
        f"Return only the file path when done."
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="implementer",
                                 system_prompt=impl_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], impl_cfg.TOOLS, _agent_ctx["task"],
                       role="implementer", color="blue", max_iter=MAX_ITER_IMPL,
                       checkpoint_key=f"implementer_{feature_id}_{attempt}")
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
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()
    task = (
        f"{_workdir_banner(cwd)}"
        f"{arch_context}"
        f"{layout_context}"
        f"Write the technical specification for feature #{feature_id}: {description}\n"
        f"Save the spec to {spec_path}\n"
        f"Return ONLY the path: {spec_path}"
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="spec_writer",
                                 system_prompt=spec_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], spec_cfg.TOOLS, _agent_ctx["task"],
                       role="spec_writer", color="cyan", max_iter=MAX_ITER_SPEC,
                       checkpoint_key=f"spec_writer_{feature_id}_1")
    done = not result.startswith("[ERROR")
    console.print(f"  [cyan]📋 SPEC_WRITER[/] {'[green]✓ spec ready[/]' if done else '[red]✗ error[/]'} → {result[:80]}")

    # Validate the freshly generated spec against the current codebase.
    # Skipped if the spec failed to generate or the agent returned an error.
    spec_issues: list[str] = []
    if done and os.path.exists(spec_path):
        issues_text = _validate_spec(spec_path)
        if issues_text:
            spec_issues = [l for l in issues_text.splitlines() if l.strip()]
            _log("spec_writer", "SPEC_VALIDATION_ISSUES", issues_text[:300], level="warning")
            console.print(f"  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ validation issues found — annotating spec[/]")
            try:
                with open(spec_path, "a", encoding="utf-8") as f:
                    f.write(
                        f"\n\n---\n"
                        f"## ⚠ Spec validation warnings\n"
                        f"The following issues were detected when cross-checking this spec "
                        f"against the existing codebase. Review and adjust before implementing:\n\n"
                        f"{issues_text}\n"
                    )
            except Exception:
                pass
        else:
            console.print(f"  [cyan]📋 SPEC_WRITER[/] [dim]✓ spec validated — no issues[/]")

    _fire("after_spec_generated",
          feature_id=feature_id, spec_path=spec_path, issues=spec_issues)

    return result


def spawn_reviewer(feature_id: int, e2e: bool = True, attempt: int = 1) -> str:
    _phase_header("reviewer", "Reviewing", feature_id)
    _log("reviewer", "SPAWN", f"feature={feature_id} e2e={e2e} attempt={attempt}")

    cwd = os.getcwd()

    # Pre-inject relevant file tree (dirs configurable via CODE_TREE_DIRS)
    tree_sections = "\n\n".join(
        f"## Current file tree ({d}/):\n{_file_tree(d) if os.path.exists(d) else '(not present)'}"
        for d in CODE_TREE_DIRS
    )
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()

    # Validation mode depends on feature type. NOTE: e2e=false ("no browser/
    # Playwright needed") is NOT the same thing as "no automated tests to run" —
    # it previously was treated as such (a pure file-existence + syntax check),
    # which let backend-only features (the documented use of e2e=false) get
    # approved without ever running their test suite. LIGHTWEIGHT REVIEW MODE
    # below always still runs the real test command; it only skips the
    # browser/server/E2E parts that e2e=false is meant to skip.
    if not e2e:
        validation_mode = (
            "LIGHTWEIGHT REVIEW MODE (e2e=false — skip browser/E2E, NOT skip tests):\n"
            "- Read the implementer report at progress/impl_{fid}.md\n"
            "- Verify that the files listed in the report exist on disk (use run_bash with 'ls')\n"
            "- Run tests with: run_bash(\"{test_runner}\")  # already runs from the project root, no cd needed\n"
            "  - If a test suite exists for this feature, it MUST pass — do not approve on syntax checks alone.\n"
            "  - If there is genuinely no test suite to run (e.g. a pure frontend-only change with no\n"
            "    backend tests touched), fall back to a syntax check (run_bash with 'node --check' for\n"
            "    JS/JSX) and note in your verdict why no test run applies.\n"
            "- DO NOT attempt to start the dev server\n"
            "- DO NOT attempt to run Playwright or E2E tests\n"
            "- DO NOT run 'npm run dev' or 'npm run build'\n"
            "- Approve only if the report indicates success AND the test run (or, where genuinely\n"
            "  inapplicable, the syntax check) confirms it.\n"
        ).format(fid=feature_id, test_runner=_LAYOUT["test_runner"])
        max_iter = 15  # lightweight review — doesn't need more
    else:
        validation_mode = (
            "Review the implementer's work for feature #{fid}.\n"
            "Run tests with: run_bash(\"{test_runner}\") (already runs from the project root, no cd needed) and validate that they pass.\n"
        ).format(fid=feature_id, test_runner=_LAYOUT["test_runner"])
        max_iter = MAX_ITER_REVIEWER

    task = (
        f"{_workdir_banner(cwd)}"
        f"{tree_sections}\n\n"
        f"{arch_context}"
        f"{layout_context}"
        f"{validation_mode}\n"
        f"The implementer report is at progress/impl_{feature_id}.md\n"
        f"Write your verdict to progress/review_{feature_id}.md\n"
        f"Return ONLY: 'APPROVED' or 'REJECTED: <reason>'"
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="reviewer",
                                 system_prompt=reviewer_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], reviewer_cfg.TOOLS, _agent_ctx["task"],
                       role="reviewer", color="magenta", max_iter=max_iter,
                       checkpoint_key=f"reviewer_{feature_id}_{attempt}")

    approved = _verdict_is(result, "APPROVED")
    verdict_color = "green" if approved else "red"
    verdict_icon  = "✅" if approved else "❌"
    _log("reviewer", "VERDICT", result[:200], level="info" if approved else "warning")
    console.print(f"  [magenta]🔍 REVIEWER[/] [{verdict_color}]{verdict_icon} {result[:100]}[/]")
    return result


def spawn_e2e_tester(feature_id: int, attempt: int = 1) -> str:
    _phase_header("e2e_tester", "Tests E2E", feature_id)
    _log("e2e_tester", "SPAWN", f"feature={feature_id} attempt={attempt}")

    cwd = os.getcwd()
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()
    task = (
        f"{_workdir_banner(cwd)}"
        f"{arch_context}"
        f"{layout_context}"
        f"Run E2E tests for feature #{feature_id}.\n"
        f"The implementer report is at progress/impl_{feature_id}.md\n"
        f"Write your report to progress/e2e_{feature_id}.md\n"
        f"Return ONLY: 'E2E_PASSED' or 'E2E_FAILED: <reason>'"
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="e2e_tester",
                                 system_prompt=e2e_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], e2e_cfg.TOOLS, _agent_ctx["task"],
                       role="e2e_tester", color="yellow",
                       checkpoint_key=f"e2e_tester_{feature_id}_{attempt}")

    passed = _verdict_is(result, "E2E_PASSED")
    color  = "green" if passed else "red"
    _log("e2e_tester", "VERDICT", result[:200], level="info" if passed else "warning")
    console.print(Panel(
        f"[bold]{result[:200]}[/]",
        title=f"[{color}]<< E2E_TESTER verdict[/]",
        border_style=color,
        padding=(0, 1)
    ))
    return result


@task(name="feature-cycle")
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
    # ── Lifecycle hook ───────────────────────────────────────────────────────
    _fire("before_feature", feature_id=feature_id, description=description, e2e=e2e)

    # ── Budget guard ─────────────────────────────────────────────────────────
    if _BUDGET_EXCEEDED:
        msg = f"[BUDGET_EXCEEDED] Feature #{feature_id} skipped — session budget of USD {COST_BUDGET_USD:.2f} was reached."
        _log("harness", "BUDGET_SKIP", msg, level="warning")
        console.print(f"  [yellow]⚠ skipping feature #{feature_id} — budget exhausted[/]")
        return {"approved": False, "attempts": 0, "final_verdict": msg}

    # ── Resumability: load checkpoint from a previous (crashed) run ──────────
    _ckpt = _load_checkpoint(feature_id)
    if _ckpt:
        _ckpt_step    = _ckpt.get("step", "")
        _ckpt_attempt = int(_ckpt.get("attempt", 1))
        console.print(
            f"  [cyan]↺ Resuming feature #{feature_id} from checkpoint[/] "
            f"[dim](step={_ckpt_step}, attempt={_ckpt_attempt})[/]"
        )
        _log("harness", "CHECKPOINT_RESUME",
             f"feature={feature_id} step={_ckpt_step} attempt={_ckpt_attempt}")
    else:
        _ckpt_step    = ""
        _ckpt_attempt = 1

    # ── Step 1: Spec ─────────────────────────────────────────────────────────
    # Skip if spec was already written in a previous run.
    if _ckpt_step in ("spec_done", "impl_done", "e2e_done"):
        spec_path = f"progress/spec_{feature_id}.md"
        console.print(f"  [dim]↺ skipping spec (already done)[/]")
    else:
        spec_result = spawn_spec_writer(feature_id, description)
        spec_path = spec_result.strip() if not spec_result.startswith("[ERROR") else None
        _save_checkpoint(feature_id, "spec_done", attempt=1)

    rejection_reason = ""
    # Resume from the attempt that was in progress when the crash happened.
    start_attempt = _ckpt_attempt if _ckpt_step in ("impl_done", "e2e_done") else 1

    for attempt in range(start_attempt, MAX_RETRIES_REVIEW + 1):

        # ── Step 2: Implement ─────────────────────────────────────────────────
        # Skip if impl was already done for this attempt in a previous run.
        _skip_impl = (
            _ckpt_step in ("impl_done", "e2e_done")
            and _ckpt_attempt == attempt
        )
        if _skip_impl:
            console.print(f"  [dim]↺ skipping impl attempt {attempt} (already done)[/]")
            impl_result = "RESUMED"
        else:
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
                    _clear_checkpoint(feature_id)
                    return {"approved": False, "attempts": attempt, "final_verdict": impl_result}
                rejection_reason = impl_result
                continue
            _save_checkpoint(feature_id, "impl_done", attempt=attempt)

        # ── Step 3: E2E Testing ───────────────────────────────────────────────
        # Skip if e2e was already done for this attempt in a previous run.
        _skip_e2e = (
            _ckpt_step == "e2e_done"
            and _ckpt_attempt == attempt
        )
        if not e2e:
            e2e_result = "E2E_PASSED"  # not applicable — skip silently
        elif _skip_e2e:
            console.print(f"  [dim]↺ skipping e2e attempt {attempt} (already done)[/]")
            e2e_result = "E2E_PASSED"
        else:
            e2e_result = spawn_e2e_tester(feature_id, attempt=attempt)

        # Allowlist, not denylist: only an explicit "E2E_PASSED" counts as a
        # pass. Anything else — "E2E_FAILED: ...", a max_iter timeout error,
        # malformed/empty output, etc. — is treated as a failure. Previously
        # this only rejected strings starting with "E2E_FAILED", so a timeout
        # or any other unexpected verdict silently fell through as an
        # implicit pass and reached the Reviewer with no real E2E evidence.
        e2e_passed = _verdict_is(e2e_result, "E2E_PASSED")
        if e2e_passed:
            _save_checkpoint(feature_id, "e2e_done", attempt=attempt)
        else:
            e2e_reason = e2e_result.replace("E2E_FAILED:", "").strip()
            _log("harness", "E2E_FAILED",
                 f"feature={feature_id} attempt={attempt} reason={e2e_reason[:100]}", level="warning")
            # E2E failure counts as rejection — implementer fixes it on next attempt
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
        review_result = spawn_reviewer(feature_id, e2e=e2e, attempt=attempt)
        if _verdict_is(review_result, "APPROVED"):
            gate_block = _fire_gate(
                "before_approval_finalized",
                feature_id=feature_id, description=description,
                attempt=attempt, review_result=review_result,
            )
            if not gate_block:
                _clear_checkpoint(feature_id)
                _fire("after_feature_approved",
                      feature_id=feature_id, description=description, attempts=attempt)
                return {"approved": True, "attempts": attempt, "final_verdict": review_result}

            # A plugin vetoed the approval. Fold it into the existing
            # rejection/retry handling below — same loop, same eventual
            # after_feature_failed if retries run out — so no new states
            # or failure paths are introduced for this to work.
            rejection_reason = gate_block.get("reason") or "Approval blocked by a governance plugin."
            _log("harness", "VERDICT_GATE_BLOCKED",
                 f"feature={feature_id} attempt={attempt}/{MAX_RETRIES_REVIEW} "
                 f"plugin={gate_block.get('plugin', '?')} reason={rejection_reason[:150]}",
                 level="warning")
            if attempt < MAX_RETRIES_REVIEW:
                console.print(Panel(
                    f"[red]Approval blocked by a governance plugin — retry {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n"
                    f"[dim]{rejection_reason[:200]}[/]",
                    title=f"[red]🚧 gate vetoed verdict — feature #{feature_id}[/]",
                    border_style="red", padding=(0, 1)
                ))
            continue

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

    final_verdict = f"REJECTED after {MAX_RETRIES_REVIEW} attempts: {rejection_reason}"
    _clear_checkpoint(feature_id)
    _fire("after_feature_failed",
          feature_id=feature_id, description=description,
          attempts=MAX_RETRIES_REVIEW, final_verdict=final_verdict)
    return {"approved": False, "attempts": MAX_RETRIES_REVIEW, "final_verdict": final_verdict}


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
        api_response = _call_api_with_fallback(
            model    = MODEL_BY_ROLE.get("leader", MODEL),
            messages = messages,
            tools    = LEADER_TOOLS,
            role     = "leader",
        )
        if api_response is None:
            return "[ERROR: all LLM providers exhausted for leader]"

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


# ─── FLOW ENTRY POINT ────────────────────────────────────────────────────────

@flow(name="harness-session", log_prints=True)
def _run_leader_flow(user_task: str) -> str:
    """
    Thin Prefect @flow wrapper around run_leader.

    In local mode (ORCHESTRATOR=local) the @flow decorator is a no-op and this
    function is identical to calling run_leader directly.

    In Prefect mode (ORCHESTRATOR=prefect) each REPL command becomes a named
    flow run in the Prefect dashboard. Each run_feature_cycle call inside it
    surfaces as a child task with its own state, logs, and duration.

    run_leader itself is intentionally not decorated — it contains the LLM
    agent loop and should remain a plain Python function. Only the entry point
    and the feature cycle (the unit of work) are Prefect-aware.
    """
    return run_leader(user_task)


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

    # Load plugins before the banner so hooks are registered before anything runs
    _load_plugins()

    console.rule("Multi-Agent Harness", style="white")
    orch_label   = "[cyan]Prefect[/]" if ORCHESTRATOR == "prefect" else "[dim]local[/]"
    budget_label = f"[yellow]USD {COST_BUDGET_USD:.2f} limit[/]" if COST_BUDGET_USD > 0 else "[dim]no limit[/]"
    console.print(
        f"  Model: [cyan]{MODEL}[/]  |  Orchestrator: {orch_label}  |  Budget: {budget_label}\n"
        f"  Flow: [green]👑 Leader[/] → [cyan]📋 Spec[/] → [blue]🔨 Impl[/] → [yellow]🧪 E2E[/] → [magenta]🔍 Reviewer[/]\n"
        f"  [dim]Commands: /quit | /status | /features | /costs | /budget[/]"
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
            elif user_input == "/budget":
                total_prompt     = sum(v["prompt_tokens"]     for v in _SESSION_COSTS.values())
                total_completion = sum(v["completion_tokens"] for v in _SESSION_COSTS.values())
                current_usd      = total_prompt * _PRICE_INPUT + total_completion * _PRICE_OUTPUT
                if COST_BUDGET_USD > 0:
                    pct = min(current_usd / COST_BUDGET_USD * 100, 100)
                    bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
                    status = "[red]EXCEEDED[/]" if _BUDGET_EXCEEDED else "[green]OK[/]"
                    console.print(
                        f"  Budget: [cyan]USD {current_usd:.4f}[/] / [cyan]USD {COST_BUDGET_USD:.2f}[/]  "
                        f"[dim]{bar}[/] {pct:.1f}%  {status}"
                    )
                else:
                    console.print(
                        f"  Spent this session: [cyan]USD {current_usd:.4f}[/]  "
                        f"[dim](no budget limit set — add COST_BUDGET_USD=N to .env to enable)[/]"
                    )
                continue

            result = _run_leader_flow(user_input)
            console.rule("[green]✅ Session complete[/]", style="green")
            console.print(f"  [green]👑 LEADER[/] {result}")
    finally:
        # Always write costs on exit, even if there's a crash
        _write_session_costs()
        _fire("after_session", session_costs=_SESSION_COSTS)


if __name__ == "__main__":
    main()