import os, re, json, time, logging, datetime, subprocess, sys, uuid, contextvars, hashlib
from typing import Optional
from types import SimpleNamespace

# Load .env as early as possible — before any module (e.g. tools.py) reads
# os.environ at import time. If this ran after `from tools import ...`,
# per-project overrides like SAFE_WRITE_DIRS would silently fall back to
# their defaults unless the shell had already exported the .env vars itself.
from dotenv import load_dotenv
load_dotenv(override=True)  # .env is the source of truth (documented workflow exports it into the
                            # shell too); without override=True a stale shell-exported var from an
                            # earlier session silently wins over an edited .env value.

# ─── SECRET REDACTION ─────────────────────────────────────────────────────────
# Every *_API_KEY value currently in the environment (DEEPSEEK_API_KEY,
# OPENAI_API_KEY, GROQ_API_KEY, CUSTOM_API_KEY, and any future <PROVIDER>_API_KEY
# from LLM_FALLBACK_CHAIN) — captured once, right after .env loads, before any
# tool call or log line could possibly carry one. Belt-and-suspenders on top of
# blocking .env reads (tools.py) and stripping these from the local sandbox's
# subprocess environment (sandbox.py): if some path I haven't thought of ever
# puts a raw key into a tool result or exception message, this still keeps it
# out of progress/harness.log, the JSON stdout stream, and the LLM's own
# context (redaction runs on tool results before they're appended to messages).
_REDACT_VALUES = tuple(v for k, v in os.environ.items() if k.endswith("_API_KEY") and v)


def _redact(text: str) -> str:
    """Replace any known secret value in `text` with a placeholder."""
    if not text:
        return text
    for secret in _REDACT_VALUES:
        text = text.replace(secret, "***REDACTED***")
    return text

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
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
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
from tools import (
    execute_tool, SAFE_WRITE_DIRS, VALID_FEATURE_STATUSES,
    FEATURE_LIST_PATH, PROGRESS_DIR, ROLES,
    STATUS_APPROVED, STATUS_REJECTED, STATUS_PASSED, STATUS_FAILED, STATUS_SCHEMA_VERSION,
    VERDICT_APPROVED, VERDICT_REJECTED, VERDICT_E2E_PASSED, VERDICT_E2E_FAILED,
)
from stack_layout import resolve_layout, all_e2e_runner_profiles

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

# ─── CONSOLE VERBOSITY ────────────────────────────────────────────────────────
# Three tiers, ascending:
#   summary — feature start, final verdict (approved/rejected), session summary.
#             Nothing else — no per-agent lines.
#   normal  — summary, plus one line per agent step (spawn_spec_writer,
#             spawn_implementer, spawn_e2e_tester, spawn_reviewer, and the
#             retry/skip/resume lines in _run_feature_cycle_impl). Default.
#   verbose — normal, plus per-tool-call detail inside run_agent's and
#             run_leader's loops (which tool, what args, what result).
# Warnings/errors logged via _log() always print regardless of tier — those
# are anomalies, not routine progress chatter, so summary mode doesn't hide
# them. Change at runtime with the /verbosity REPL command, or set
# HARNESS_VERBOSITY in .env for the session default.
_VERBOSITY_LEVELS = ("summary", "normal", "verbose")  # ascending
HARNESS_VERBOSITY = os.getenv("HARNESS_VERBOSITY", "normal").strip().lower()
if HARNESS_VERBOSITY not in _VERBOSITY_LEVELS:
    logging.warning(f"HARNESS_VERBOSITY={HARNESS_VERBOSITY!r} is invalid — falling back to 'normal'")
    HARNESS_VERBOSITY = "normal"


def _verbosity_at_least(min_level: str) -> bool:
    """True if the active HARNESS_VERBOSITY tier is at or above min_level."""
    return _VERBOSITY_LEVELS.index(HARNESS_VERBOSITY) >= _VERBOSITY_LEVELS.index(min_level)

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

# Maximum USD spend on a SINGLE feature (across all its retries), independent
# of COST_BUDGET_USD above. The session budget is global — one pathological
# feature (e.g. one that burns 2 full 50-iteration E2E cycles) can consume
# the entire session's budget with nothing per-feature ever noticing. Set in
# .env as FEATURE_BUDGET_USD=0.50. Set to 0 (default) to disable.
FEATURE_BUDGET_USD = float(os.getenv("FEATURE_BUDGET_USD", "0"))

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
MAX_RETRIES_API    = int(os.getenv("MAX_RETRIES_API", "3"))     # Retries on transient API errors (rate limit, timeout)
MAX_RETRIES_IMPL   = int(os.getenv("MAX_RETRIES_IMPL", "3"))    # How many times the implementer can retry a feature
MAX_RETRIES_REVIEW = int(os.getenv("MAX_RETRIES_REVIEW", "2"))  # How many times the impl→review cycle repeats before marking "failed"
MAX_ITER_LEADER    = int(os.getenv("MAX_ITER_LEADER", "30"))    # Max iterations for the leader loop
MAX_ITER_AGENT     = int(os.getenv("MAX_ITER_AGENT", "30"))  # Default — e2e_tester: override via .env if E2E setup + fix cycles need more iterations
MAX_ITER_IMPL      = int(os.getenv("MAX_ITER_IMPL", "50"))  # Implementer: read context + write code + tests — override via .env
MAX_ITER_REVIEWER  = int(os.getenv("MAX_ITER_REVIEWER", "40"))  # Reviewer: read reports + run tests + mutation testing — override via .env
MAX_ITER_SPEC      = int(os.getenv("MAX_ITER_SPEC", "35"))  # Spec writer: override via .env if specs need more iterations
RETRY_BACKOFF      = [2, 4, 8]  # seconds between API retries

# Convergence streak detector (run_agent): consecutive iterations with tool
# calls but no write (write_file/append_file/update_feature_status, or a
# hallucinated edit-style call _edit_alias translated into one) before a live
# nudge is injected telling the agent to stop exploring and make the edit.
# Fires every CONVERGENCE_STREAK_LIMIT-th iteration of the streak (7, 14, 21,
# ...) rather than once, in case the first nudge doesn't land. 0 disables it.
CONVERGENCE_STREAK_LIMIT = int(os.getenv("CONVERGENCE_STREAK_LIMIT", "7"))
# Hard companion to the soft streak nudge above. Real incident (feature #77,
# round 2, attempt 1): the nudge mechanism fired ~11 times (streak checkpoints
# + the 60%/85% budget checkpoints) and the agent kept reading until iteration
# 80 without writing anything — the watchdog existed; it had no teeth. If an
# attempt reaches this many total iterations with ZERO writes, it is aborted
# early with a message distinct from a normal max_iter cutoff. Combined with
# the investigation digest (v1.53.0), dying at 40 with findings handed to the
# retry is strictly better than dying at 80 just as empty: it leaves half the
# budget for an informed retry. 0 disables the hard cut.
MAX_ITER_WITHOUT_WRITE = int(os.getenv("MAX_ITER_WITHOUT_WRITE", "40"))
_WRITE_TOOL_NAMES = {"write_file", "append_file", "update_feature_status"}

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

# ─── PER-MODEL PRICING ───────────────────────────────────────────────────────
# USD per token (not per million), keyed by the model name that actually
# generated a given response — i.e. api_response.model, which reflects any
# LLM_MODEL_MAP provider translation (e.g. "gpt-4o" after a fallback to
# openai), not necessarily the canonical MODEL_BY_ROLE name that was
# requested. MODEL_BY_ROLE lets each role run a different model, and
# LLM_FALLBACK_CHAIN lets a single role land on different providers across a
# session, so a single global price is only an approximation once more than
# one model is actually in play. Any model not listed here falls back to
# deepseek-v4-pro pricing — see _price_for_model, which also logs a one-time
# warning per unlisted model per session.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "deepseek-v4-pro":         {"input_price": 0.27  / 1_000_000, "output_price": 1.10  / 1_000_000},
    "deepseek-v4-flash":       {"input_price": 0.07  / 1_000_000, "output_price": 0.28  / 1_000_000},
    "gpt-4o":                  {"input_price": 2.50  / 1_000_000, "output_price": 10.00 / 1_000_000},
    "gpt-4o-mini":             {"input_price": 0.15  / 1_000_000, "output_price": 0.60  / 1_000_000},
    "llama-3.3-70b-versatile": {"input_price": 0.59  / 1_000_000, "output_price": 0.79  / 1_000_000},
    "llama-3.1-8b-instant":    {"input_price": 0.05  / 1_000_000, "output_price": 0.08  / 1_000_000},
}

_DEFAULT_PRICING_MODEL = "deepseek-v4-pro"  # fallback pricing for any model not in MODEL_PRICING

# Models already warned about this session — log the fallback once per model,
# not on every call, to avoid spamming progress/harness.log in a long run.
_UNKNOWN_PRICING_MODELS_WARNED: set = set()


def _price_for_model(model_name: str) -> dict[str, float]:
    """
    Look up {input_price, output_price} (USD/token) for a model.
    Falls back to MODEL_PRICING[_DEFAULT_PRICING_MODEL] for any model not
    listed, logging a one-time-per-session warning so silent cost drift in
    mixed-model/mixed-provider runs is noticeable instead of just wrong.
    """
    pricing = MODEL_PRICING.get(model_name)
    if pricing is not None:
        return pricing
    if model_name not in _UNKNOWN_PRICING_MODELS_WARNED:
        _UNKNOWN_PRICING_MODELS_WARNED.add(model_name)
        _log("harness", "UNKNOWN_MODEL_PRICING",
             f"no pricing entry for model='{model_name}' — using "
             f"'{_DEFAULT_PRICING_MODEL}' pricing as a fallback; reported cost "
             f"for this model is approximate", level="warning")
    return MODEL_PRICING[_DEFAULT_PRICING_MODEL]

# ─── STRUCTURED LOGGING ──────────────────────────────────────────────────────
# Two handlers on the root logger, both fed by the same logging.info/warning/
# error() calls (via _log() below, or called directly — e.g. by a plugin):
#
#   1. progress/harness.log — plain text, UNCHANGED from before. Any existing
#      tooling or premium plugin that tails this file, or that just calls
#      logging.getLogger().info(...) and expects a configured root logger,
#      keeps working exactly as before — this handler is never removed.
#   2. stdout — one JSON object per line (timestamp, level, session_id,
#      feature_id, message), for log aggregators / structured-logging
#      pipelines. session_id is a UUID generated once per harness process;
#      feature_id is populated from a contextvar set by run_feature_cycle()
#      while a feature is being processed (None outside that scope, e.g.
#      leader-level orchestration log lines).
#
# Off by default: a human at the terminal is watching Rich panels, and a raw
# JSON line per _log() call interleaved with those panels is noise for that
# audience. Set STRUCTURED_LOG_STDOUT=true in .env to opt in — for CI, or
# when piping this process's stdout to a log aggregator (Vector, Fluent Bit,
# etc.) that wants the machine-readable stream instead. The file handler is
# always on regardless of this flag.
_SESSION_ID = str(uuid.uuid4())

# Set by run_feature_cycle() for the duration of a feature's spec→impl→e2e→
# review cycle; contextvars are isolated per-thread, so this stays correct
# under the premium parallel-feature-execution plugin's ThreadPoolExecutor.
_CURRENT_FEATURE_ID: "contextvars.ContextVar[Optional[int]]" = contextvars.ContextVar(
    "current_feature_id", default=None
)


class _JsonLogFormatter(logging.Formatter):
    """Renders a LogRecord as a single-line JSON object for the stdout handler."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp":  datetime.datetime.utcfromtimestamp(record.created)
                              .isoformat(timespec="milliseconds") + "Z",
            "level":      record.levelname,
            "session_id": _SESSION_ID,
            "feature_id": _CURRENT_FEATURE_ID.get(),
            "message":    record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


logging.basicConfig(
    filename=f"{PROGRESS_DIR}/harness.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def _structured_log_stdout_enabled() -> bool:
    """
    Whether the JSON stdout handler should be active. Pulled out as its own
    function (rather than inlined in the `if` below) so the default can be
    unit-tested directly — actual handler registration is process-global and
    only-configures-once (same quirk as progress/harness.log's handler), so
    testing that side effect directly is order-dependent across a test run.
    """
    return os.getenv("STRUCTURED_LOG_STDOUT", "false").strip().lower() not in ("false", "0", "no")


_JSON_STDOUT_HANDLER_NAME = "harness_json_stdout"
_root_logger = logging.getLogger()
if (
    _structured_log_stdout_enabled()
    # Guard against duplicate handlers if this module is ever re-imported in
    # the same process (e.g. the test suite reloads harness.py per test).
    and not any(h.name == _JSON_STDOUT_HANDLER_NAME for h in _root_logger.handlers)
):
    _json_handler = logging.StreamHandler(sys.stdout)
    _json_handler.set_name(_JSON_STDOUT_HANDLER_NAME)
    _json_handler.setLevel(logging.INFO)
    _json_handler.setFormatter(_JsonLogFormatter())
    _root_logger.addHandler(_json_handler)

def _log(role: str, event: str, detail: str = "", level: str = "info"):
    msg = _redact(f"[{role.upper()}] {event}" + (f" | {detail}" if detail else ""))
    getattr(logging, level)(msg)
    # Warnings/errors always print, independent of HARNESS_VERBOSITY — they're
    # anomalies, not routine per-agent progress chatter, so summary mode
    # doesn't hide them.
    if level in ("warning", "error"):
        console.print(f"  [dim red]{msg}[/]")

console = Console()


def _vprint(min_level: str, *args, **kwargs) -> None:
    """console.print(...), only emitted if the active HARNESS_VERBOSITY tier
    is at or above min_level. Thin wrapper so the tier-comparison logic lives
    in exactly one place (_verbosity_at_least) instead of being duplicated at
    every call site."""
    if _verbosity_at_least(min_level):
        console.print(*args, **kwargs)

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


# Provider-specific extra message fields that must never cross into a
# different provider's request. Currently just DeepSeek's thinking-mode
# reasoning_content (see _serialize_message's comment) — it's required on
# DeepSeek turns but meaningless (and unvalidated) elsewhere, so on fallback
# to any other provider it gets stripped rather than forwarded as-is.
_PROVIDER_ONLY_MSG_FIELDS = {
    "deepseek": {"reasoning_content"},
}


def _sanitize_messages_for_provider(messages: list, provider_name: str) -> list:
    """
    Return a copy of `messages` with any field that's exclusive to a
    *different* provider than `provider_name` stripped out. Cheap no-op
    (returns the same list unchanged) when nothing needs stripping, which
    is the common single-provider case.

    Messages can be plain dicts (user/tool turns, or assistant turns
    reloaded from a crash checkpoint) or raw ChatCompletionMessage pydantic
    objects (assistant turns appended live in the same run) — both are
    handled via _msg_field's generic accessor.
    """
    strip_fields = set()
    for owner, fields in _PROVIDER_ONLY_MSG_FIELDS.items():
        if owner != provider_name:
            strip_fields |= fields
    if not strip_fields:
        return messages

    needs_copy = any(
        any(_msg_field(m, f, None) for f in strip_fields) for m in messages
    )
    if not needs_copy:
        return messages

    sanitized = []
    for m in messages:
        if any(_msg_field(m, f, None) for f in strip_fields):
            # _serialize_message returns the SAME dict reference when m is
            # already a dict (cheap no-op path used elsewhere) — copy first
            # so popping fields here never mutates the caller's stored
            # history, which still needs the field if a later call falls
            # back to the owning provider again.
            d = dict(_serialize_message(m))
            for f in strip_fields:
                d.pop(f, None)
            sanitized.append(d)
        else:
            sanitized.append(m)
    return sanitized


# ─── LLM RESPONSE CACHE ──────────────────────────────────────────────────────
# Opt-in, on-disk cache for chat-completion calls, keyed on a canonical hash
# of the exact (resolved_model, outgoing wire messages, tools) tuple sent to
# a given provider. Off by default — see _llm_cache_enabled() below.
#
# Why this helps: retrying a whole feature cycle from scratch after a failure
# re-sends an identical first turn (system prompt + task, no tool-call
# history yet) to the LLM, paying and waiting for a response the harness has
# already seen. Same for repeated compaction/spec-validation calls over an
# identical conversation slice. This is a *different* problem than the
# existing mid-run resumability (_save_message_state/_load_message_state):
# that avoids redoing a crashed run; this avoids redoing a fresh run that
# happens to reconstruct a prompt already seen in a previous run (or earlier
# in this one).
#
# Multi-provider/multi-model correctness: the cache key is built from
# provider.resolve_model(model) — the provider-resolved model name — not the
# caller-facing canonical `model` argument, since LLM_MODEL_MAP can resolve
# the same canonical name to a different real model per provider (e.g.
# "deepseek-v4-pro" -> "gpt-4o" on openai). The key is computed *inside* the
# per-provider loop in _call_api_with_fallback, using that provider's own
# resolved model and its own sanitized outgoing_messages (see
# _sanitize_messages_for_provider), so a cache entry is only ever reused for
# the exact provider+model+messages+tools combination that produced it. This
# also means a cache entry recorded for a fallback provider (e.g. openai,
# because deepseek was down when it was written) is correctly reused later
# only if that same provider ends up serving the request again — never
# silently applied to a different provider's response for the same prompt.
#
# Nondeterminism caveat (documented, not "fixed"): no call site sets a
# `temperature`/`seed`, so two real calls with byte-identical input aren't
# guaranteed to return the same output either — caching just pins one drawn
# sample instead of drawing a fresh one on retry. No caller in this codebase
# depends on getting a *different* response from an identical prompt (the
# existing spec/impl report caching already treats a prior successful
# attempt as reusable without re-running the LLM at all), so this is a safe
# opt-in tradeoff, not a correctness regression — but it's why the feature
# defaults off and is documented in the README rather than silently always-on.
def _llm_cache_enabled() -> bool:
    """
    Whether the on-disk LLM response cache is active. Pulled out as its own
    function (same pattern as _structured_log_stdout_enabled) so the default
    is unit-testable without depending on module-import-time state.
    """
    return os.getenv("LLM_CACHE_ENABLED", "false").strip().lower() not in ("false", "0", "no")


LLM_CACHE_ENABLED = _llm_cache_enabled()

# Harness-internal state, not something agents are ever instructed to read
# or write — same convention as the _state_*.json checkpoint files already
# living under progress/ (see _message_state_path). Overridable via .env for
# projects that want the cache to live outside progress/ entirely.
LLM_CACHE_DIR = os.getenv("LLM_CACHE_DIR", os.path.join(PROGRESS_DIR, ".llm_cache"))


def _llm_cache_key(resolved_model: str, messages: list, tools: list) -> str:
    """
    Canonical sha256 hash of (resolved_model, messages, tools). Messages are
    passed through _serialize_message first so a live pydantic
    ChatCompletionMessage and a checkpoint-reloaded plain dict for the same
    logical turn hash identically. sort_keys + compact separators make the
    JSON serialization canonical (stable across key order / whitespace).
    """
    payload = {
        "model":    resolved_model,
        "messages": [_serialize_message(m) for m in messages],
        "tools":    tools or [],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _llm_cache_path(cache_key: str) -> str:
    return os.path.join(LLM_CACHE_DIR, f"{cache_key}.json")


def _llm_cache_get(cache_key: str) -> Optional[dict]:
    """
    Best-effort disk cache lookup. A missing or corrupt entry is treated as a
    miss (mirrors _load_message_state's "corrupt state == absent" rule)
    rather than raising — a cache must never be able to block or crash a
    call it only exists to skip.
    """
    path = _llm_cache_path(cache_key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        _log("harness", "LLM_CACHE_READ_ERROR", f"key={cache_key} err={exc}", level="warning")
        return None


def _llm_cache_put(cache_key: str, entry: dict) -> None:
    """Best-effort disk cache write — never raises, matching _save_message_state."""
    try:
        os.makedirs(LLM_CACHE_DIR, exist_ok=True)
        with open(_llm_cache_path(cache_key), "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False)
    except Exception as exc:
        _log("harness", "LLM_CACHE_WRITE_ERROR", f"key={cache_key} err={exc}", level="warning")


def _llm_cache_entry_from_response(response) -> dict:
    """Build the on-disk cache entry for a successful API response."""
    usage = getattr(response, "usage", None)
    return {
        "message": _serialize_message(response.choices[0].message),
        "model":   getattr(response, "model", None),
        "usage": {
            "prompt_tokens":     getattr(usage, "prompt_tokens", 0) if usage else 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
        },
    }


def _llm_response_from_cache_entry(entry: dict) -> SimpleNamespace:
    """
    Reconstruct a response object shaped like the real OpenAI-compatible
    response (.choices[0].message.{content,tool_calls,reasoning_content},
    .usage.{prompt_tokens,completion_tokens}, .model) from a stored cache
    entry, so a cache hit is indistinguishable to every caller of
    _call_api_with_fallback from a live API response.
    """
    msg_data = entry.get("message", {}) or {}
    tool_calls = None
    raw_tool_calls = msg_data.get("tool_calls")
    if raw_tool_calls:
        tool_calls = [
            SimpleNamespace(
                id=tc.get("id"),
                type=tc.get("type", "function"),
                function=SimpleNamespace(
                    name=(tc.get("function") or {}).get("name"),
                    arguments=(tc.get("function") or {}).get("arguments"),
                ),
            )
            for tc in raw_tool_calls
        ]
    msg = SimpleNamespace(
        role=msg_data.get("role", "assistant"),
        content=msg_data.get("content"),
        tool_calls=tool_calls,
    )
    if msg_data.get("reasoning_content"):
        msg.reasoning_content = msg_data["reasoning_content"]

    # usage is deliberately None, not the cached token counts: callers (run_agent,
    # run_leader) unconditionally do `_track_usage(role, api_response.usage, ...)`
    # on whatever _call_api_with_fallback returns, without knowing whether it was
    # a cache hit. _track_usage() no-ops on usage=None (see its own early return),
    # so this is what keeps a cache hit out of _SESSION_COSTS without having to
    # touch any of the 3 call sites. The real token counts were already recorded
    # separately by _track_cache_hit() before this object was returned.
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None, model=entry.get("model"))


def _clear_llm_cache() -> int:
    """Delete every on-disk cache entry. Returns the count removed. Best-effort."""
    if not os.path.isdir(LLM_CACHE_DIR):
        return 0
    removed = 0
    for fname in os.listdir(LLM_CACHE_DIR):
        if fname.endswith(".json"):
            try:
                os.remove(os.path.join(LLM_CACHE_DIR, fname))
                removed += 1
            except Exception as exc:
                _log("harness", "LLM_CACHE_CLEAR_ERROR", f"file={fname} err={exc}", level="warning")
    _log("harness", "LLM_CACHE_CLEARED", f"removed={removed}")
    return removed


def _llm_cache_entry_count() -> int:
    if not os.path.isdir(LLM_CACHE_DIR):
        return 0
    return sum(1 for f in os.listdir(LLM_CACHE_DIR) if f.endswith(".json"))


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

    Before each call, messages are sanitized for the target provider via
    _sanitize_messages_for_provider — see _PROVIDER_ONLY_MSG_FIELDS. This
    only affects the wire payload; the caller's own `messages` list (and
    any checkpoint saved from it) is left untouched, so a later fallback
    back to the original provider still has the field available.

    If LLM_CACHE_ENABLED, each provider attempt first checks the on-disk
    cache for this exact (provider-resolved model, outgoing messages, tools)
    tuple before making a real call, and stores a successful response before
    returning it. See the "LLM RESPONSE CACHE" section above.
    """
    cache_enabled = LLM_CACHE_ENABLED

    for p_idx, provider in enumerate(_PROVIDERS):
        resolved = provider.resolve_model(model)
        if p_idx > 0:
            _log(role, "PROVIDER_SWITCH",
                 f"switching to provider '{provider.name}' (model={resolved})", level="warning")

        outgoing_messages = _sanitize_messages_for_provider(messages, provider.name)

        cache_key = None
        if cache_enabled:
            cache_key = _llm_cache_key(resolved, outgoing_messages, tools)
            cached_entry = _llm_cache_get(cache_key)
            if cached_entry is not None:
                _log(role, "CACHE_HIT",
                     f"provider={provider.name} model={resolved} key={cache_key[:12]}")
                _track_cache_hit(role, cached_entry.get("usage") or {}, cached_entry.get("model"))
                return _llm_response_from_cache_entry(cached_entry)

        for attempt in range(MAX_RETRIES_API):
            try:
                response = provider.client.chat.completions.create(
                    model=resolved,
                    messages=outgoing_messages,
                    tools=tools if tools else None,
                    tool_choice="auto" if tools else None,
                )
                if p_idx > 0:
                    _log(role, "PROVIDER_FALLBACK_OK",
                         f"succeeded on '{provider.name}' after primary failed")
                if cache_enabled:
                    _llm_cache_put(cache_key, _llm_cache_entry_from_response(response))
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
    "leader":       {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
    "spec_writer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
    "implementer":  {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
    "reviewer":     {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
    "e2e_tester":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
    "compaction":   {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0},
}

# Per-feature cost accumulation, keyed by feature_id (str, since JSON object
# keys are always strings — see _write_session_costs). Populated by
# _track_usage from _CURRENT_FEATURE_ID (set for the duration of each
# feature's cycle — see run_feature_cycle), so any call made while a feature
# is being processed — including nested agent calls — is attributed to it.
# Calls made outside a feature cycle (e.g. the Leader's own coordination
# turns, or _validate_spec) have _CURRENT_FEATURE_ID unset and are not
# attributed to any feature, same as they're not attributed to one in the
# by-role breakdown either. Real motivation: COST_BUDGET_USD is global — one
# pathological feature (e.g. one that burns 2 full 50-iteration E2E cycles)
# can consume the entire session's budget with no per-feature mechanism ever
# noticing. See also FEATURE_BUDGET_USD above.
_FEATURE_COSTS: dict = {}

# Cache hits are tracked separately from _SESSION_COSTS — a cache hit costs
# nothing, so folding it into _SESSION_COSTS would make /costs overstate the
# session's real spend. "savings_usd" is what the same tokens would have
# cost at the cached response's own model's pricing (see _track_cache_hit).
_CACHE_STATS: dict = {
    "leader":       {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
    "spec_writer":  {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
    "implementer":  {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
    "reviewer":     {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
    "e2e_tester":   {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
    "compaction":   {"hits": 0, "prompt_tokens_saved": 0, "completion_tokens_saved": 0, "savings_usd": 0.0},
}

# ─── CONSOLE UTILITIES ───────────────────────────────────────────────────────

_AGENT_STYLES = {
    "leader":      ("green",   "👑"),
    "spec_writer": ("cyan",    "📋"),
    "implementer": ("blue",    "🔨"),
    "e2e_tester":  ("yellow",  "🧪"),
    "reviewer":    ("magenta", "🔍"),
}

# A typo in any of the role-keyed dicts above/below would otherwise fail
# silently — e.g. _track_usage()'s _SESSION_COSTS.get(role, _SESSION_COSTS["leader"])
# fallback would misattribute cost tracking to "leader" with no error. Fail
# loudly at import time instead. MODEL_BY_ROLE/_SESSION_COSTS also carry a
# "compaction" pseudo-role beyond the five spawnable agents, so those two
# are checked as a superset of ROLES rather than an exact match.
assert set(ROLES) <= set(MODEL_BY_ROLE), f"MODEL_BY_ROLE missing role(s): {set(ROLES) - set(MODEL_BY_ROLE)}"
assert set(ROLES) <= set(_SESSION_COSTS), f"_SESSION_COSTS missing role(s): {set(ROLES) - set(_SESSION_COSTS)}"
assert set(ROLES) <= set(_CACHE_STATS), f"_CACHE_STATS missing role(s): {set(ROLES) - set(_CACHE_STATS)}"
assert set(ROLES) == set(_AGENT_STYLES), f"_AGENT_STYLES out of sync with ROLES: {set(ROLES) ^ set(_AGENT_STYLES)}"

def _phase_header(agent: str, action: str, feature_id: int = None,
                  attempt: int = None, total_features: int = None, current_feature: int = None):
    """Print a clear phase header with agent, action and context."""
    color, icon = _AGENT_STYLES.get(agent, ("white", "•"))
    progress = ""
    if total_features and current_feature:
        progress = f" [dim]({current_feature}/{total_features})[/]"
    feat_info = f" → Feature #{feature_id}" if feature_id else ""
    attempt_info = f" [dim](attempt {attempt})[/]" if attempt and attempt > 1 else ""

    if _verbosity_at_least("normal"):
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

def _track_usage(role: str, usage, model: str = None) -> None:
    """
    Accumulate tokens and cost from each API call by role.

    `model` should be the model that actually generated this response (e.g.
    api_response.model) so cost reflects per-model pricing in mixed-model
    (MODEL_BY_ROLE) / mixed-provider (LLM_FALLBACK_CHAIN) runs instead of a
    single global price. Defaults to the role's configured MODEL_BY_ROLE
    entry when the caller doesn't have a more specific value.

    Triggers budget enforcement if enabled.
    """
    global _BUDGET_EXCEEDED
    if usage is None:
        return
    if model is None:
        model = MODEL_BY_ROLE.get(role, MODEL)

    prompt_tokens     = getattr(usage, "prompt_tokens", 0)
    completion_tokens = getattr(usage, "completion_tokens", 0)
    pricing       = _price_for_model(model)
    call_cost_usd = prompt_tokens * pricing["input_price"] + completion_tokens * pricing["output_price"]

    bucket = _SESSION_COSTS.get(role, _SESSION_COSTS["leader"])
    bucket["prompt_tokens"]     += prompt_tokens
    bucket["completion_tokens"] += completion_tokens
    bucket["calls"]             += 1
    bucket["cost_usd"]          += call_cost_usd

    feature_id = _CURRENT_FEATURE_ID.get()
    if feature_id is not None:
        # JSON object keys are always strings — keyed as str here (not int)
        # so _write_session_costs can json.dump _FEATURE_COSTS directly
        # without a str(k) conversion pass, and so a lookup with either an
        # int or str feature_id (e.g. from feature_list.json vs. a REPL arg)
        # finds the same bucket after going through str() once, consistently.
        fid_key = str(feature_id)
        feat_bucket = _FEATURE_COSTS.setdefault(
            fid_key, {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cost_usd": 0.0}
        )
        feat_bucket["prompt_tokens"]     += prompt_tokens
        feat_bucket["completion_tokens"] += completion_tokens
        feat_bucket["calls"]             += 1
        feat_bucket["cost_usd"]          += call_cost_usd

    if COST_BUDGET_USD > 0 and not _BUDGET_EXCEEDED:
        current_usd = _session_total_cost_usd()
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

def _track_cache_hit(role: str, usage: dict, model: str = None) -> None:
    """
    Record an LLM_CACHE hit's token/cost savings in _CACHE_STATS — deliberately
    NOT _SESSION_COSTS, since a cache hit makes no API call and costs nothing;
    folding it into _SESSION_COSTS would make /costs overstate real spend.

    `model` is the model that actually generated the cached response (stored
    in the cache entry itself), so the savings estimate uses that model's own
    pricing — same rationale as _track_usage's `model` parameter.
    """
    if model is None:
        model = MODEL_BY_ROLE.get(role, MODEL)

    prompt_tokens     = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    pricing        = _price_for_model(model)
    saved_usd      = prompt_tokens * pricing["input_price"] + completion_tokens * pricing["output_price"]

    bucket = _CACHE_STATS.get(role, _CACHE_STATS["leader"])
    bucket["hits"]                     += 1
    bucket["prompt_tokens_saved"]      += prompt_tokens
    bucket["completion_tokens_saved"]  += completion_tokens
    bucket["savings_usd"]              += saved_usd

def _session_total_cost_usd() -> float:
    """Sum of accumulated per-call cost (already priced per-model) across all roles."""
    return sum(v["cost_usd"] for v in _SESSION_COSTS.values())

def _feature_cost_usd(feature_id: int) -> float:
    """Accumulated cost for one feature this session (0.0 if it hasn't made any tracked calls yet)."""
    return _FEATURE_COSTS.get(str(feature_id), {}).get("cost_usd", 0.0)

def _session_cache_hits_total() -> int:
    return sum(v["hits"] for v in _CACHE_STATS.values())

def _session_cache_savings_usd() -> float:
    """Estimated USD not spent this session thanks to cache hits — informational
    only, never subtracted from _session_total_cost_usd (that reflects real spend)."""
    return sum(v["savings_usd"] for v in _CACHE_STATS.values())

def _write_session_costs() -> None:
    """Write session cost summary to progress/session_costs.json."""
    total_prompt     = sum(v["prompt_tokens"]     for v in _SESSION_COSTS.values())
    total_completion = sum(v["completion_tokens"] for v in _SESSION_COSTS.values())
    total_cost_usd   = _session_total_cost_usd()
    total_cache_hits = _session_cache_hits_total()
    total_savings    = _session_cache_savings_usd()

    summary = {
        "session_start":      _SESSION_START.isoformat(),
        "session_end":        datetime.datetime.now().isoformat(),
        "model":              MODEL,
        "by_role":            _SESSION_COSTS,
        # Per-feature breakdown (see _FEATURE_COSTS / FEATURE_BUDGET_USD above)
        # — keyed by feature_id as a string, JSON-object-key convention.
        "per_feature":        _FEATURE_COSTS,
        "totals": {
            "prompt_tokens":     total_prompt,
            "completion_tokens": total_completion,
            "total_tokens":      total_prompt + total_completion,
            "estimated_usd":     round(total_cost_usd, 6),
        },
        # Cache hits are reported separately from the totals above by design —
        # they are NOT part of estimated_usd (real spend). See LLM_CACHE_ENABLED
        # / _track_cache_hit and the README's "LLM response cache" section.
        "cache": {
            "enabled":               LLM_CACHE_ENABLED,
            "by_role":               _CACHE_STATS,
            "hits":                  total_cache_hits,
            "estimated_savings_usd": round(total_savings, 6),
        },
    }
    os.makedirs(PROGRESS_DIR, exist_ok=True)
    path = f"{PROGRESS_DIR}/session_costs.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    cache_line = ""
    if total_cache_hits > 0:
        cache_line = (
            f"\nCache hits: [cyan]{total_cache_hits}[/]  |  "
            f"Est. savings: [yellow]USD {total_savings:.4f}[/] [dim](not spent, excluded from cost above)[/]"
        )

    console.print(Panel(
        f"Total tokens: [cyan]{total_prompt + total_completion:,}[/]  |  "
        f"Estimated cost: [yellow]USD {total_cost_usd:.4f}[/]{cache_line}",
        title=f"[dim]Session costs → {path}[/]",
        border_style="dim",
        padding=(0, 1)
    ))

def _print_per_feature_costs() -> None:
    """
    /costs helper: a per-feature cost breakdown, sorted highest-spend first —
    the view that would have made feature #74 (2 full 50-iteration E2E
    cycles) visibly stand out mid-session instead of only being noticeable
    after it had already consumed the whole session budget. No-op (prints
    nothing) if no feature has made a tracked call yet this session.
    """
    if not _FEATURE_COSTS:
        return
    titles = {str(f.get("id")): f.get("title", "") for f in _read_feature_list_raw()}
    table = Table(show_header=True, header_style="bold", title="Cost by feature")
    table.add_column("ID",     style="dim", width=4)
    table.add_column("Title")
    table.add_column("Calls",  justify="right", width=6)
    table.add_column("Tokens", justify="right", width=10)
    table.add_column("Cost (USD)", justify="right", width=12)
    for fid, bucket in sorted(_FEATURE_COSTS.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True):
        tokens = bucket["prompt_tokens"] + bucket["completion_tokens"]
        cost_style = "red" if (FEATURE_BUDGET_USD > 0 and bucket["cost_usd"] >= FEATURE_BUDGET_USD) else "yellow"
        table.add_row(
            fid, titles.get(fid, "(unknown)"), str(bucket["calls"]),
            f"{tokens:,}", f"[{cost_style}]{bucket['cost_usd']:.4f}[/]"
        )
    console.print(table)

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


# ─── FEATURE_LIST.JSON SCHEMA ────────────────────────────────────────────────
# Versioned schema for a single feature_list.json entry. Bump
# FEATURE_SCHEMA_VERSION whenever a field is added, renamed, or removed —
# it's written into progress/session_costs.json-style metadata is not needed
# here, but it's surfaced in error messages and the README so a malformed
# feature_list.json can be traced back to the schema revision that rejected it.
#
# extra="forbid" is the whole point of this validator: today a misspelled
# field (e.g. "depnds_on" instead of "depends_on") is silently ignored by
# every `.get(...)` call in this file and just sits there as dead JSON,
# producing no error and no dependency enforcement. Rejecting unknown fields
# turns that into a startup-time error instead of a silent no-op.
#
# Known optional fields beyond the "Feature fields" table in the README:
#   - updated_at, recovery_note   written by the harness itself
#                                  (update_feature_status in tools.py,
#                                  recover_stale_features() in this file)
#   - _checkpoint                 written by _save_checkpoint() in this file
#                                  for crash resumability (see "CHECKPOINTING"
#                                  below)
#   - requires_human_gate         read by the premium human-in-the-loop-gates
#                                  plugin (see README "Premium modules") —
#                                  the public core never sets or reads it,
#                                  but must not reject it either.
FEATURE_SCHEMA_VERSION = "1.0"


class _CheckpointSchema(BaseModel):
    """Shape of the "_checkpoint" field written by _save_checkpoint()."""
    model_config = ConfigDict(extra="forbid")

    step:     str
    attempt:  int
    saved_at: str


class FeatureSchema(BaseModel):
    """
    A single feature_list.json entry — see FEATURE_SCHEMA_VERSION above.
    `id`, `title`, `description`, and `status` are required because harness.py
    indexes them directly (e.g. f["id"] in _topological_sort). The rest have
    defaults matching how the rest of the codebase already treats a missing
    field (e.g. agents/leader.py: "[e2e] If not present, use false").
    """
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id:          int
    title:       str
    description: str
    status:      str
    e2e:         bool = False
    depends_on:  list[int] = Field(default_factory=list)
    created_at:  Optional[str] = None

    # Harness-written fields (see module-level comment above).
    updated_at:     Optional[str] = None
    recovery_note:  Optional[str] = None
    checkpoint:     Optional[_CheckpointSchema] = Field(default=None, alias="_checkpoint")

    # Premium plugin field (human-in-the-loop gates) — see module-level comment above.
    requires_human_gate: Optional[bool] = None

    @field_validator("status")
    @classmethod
    def _status_must_be_valid(cls, v: str) -> str:
        if v not in VALID_FEATURE_STATUSES:
            raise ValueError(f"must be one of {sorted(VALID_FEATURE_STATUSES)}, got '{v}'")
        return v


def _validate_feature_schema(features: list) -> list[str]:
    """
    Validate every feature_list.json entry against FeatureSchema.

    Returns a list of human-readable error strings (empty list = every entry
    is valid). Never raises — a malformed entry produces an error message,
    not a crash, so the harness can still start and the file can be fixed
    without losing the rest of the session. Same non-fatal pattern as
    _validate_dependencies().
    """
    errors: list[str] = []
    for i, raw in enumerate(features):
        label = f"#{raw['id']}" if isinstance(raw, dict) and "id" in raw else f"at index {i}"
        try:
            FeatureSchema.model_validate(raw)
        except ValidationError as exc:
            for err in exc.errors():
                field = ".".join(str(p) for p in err["loc"]) or "(root)"
                errors.append(f"Feature {label}: field '{field}' — {err['msg']}")
    return errors


# ─── CHECKPOINTING & DURABLE RESUMABILITY ────────────────────────────────────
#
# Every feature cycle writes a lightweight checkpoint after each completed step
# so a crash mid-cycle can resume where it stopped rather than starting over.
#
# The checkpoint is stored as a "_checkpoint" field inside the feature's entry
# in feature_list.json — same file, no extra dependencies.
#
# Step progression (impl -> review -> E2E, see ARCHITECTURE_REVIEW §8.C: E2E
# is the most expensive step — force-recreate + cold compile + browser — so
# it runs last, after the cheap review check has already approved):
#   (none)       fresh start — run all steps
#   spec_done    spec written; next restart skips spawn_spec_writer
#   impl_done    impl written for attempt N; next restart skips spawn_implementer
#   review_done  review approved attempt N; next restart skips spawn_reviewer
#   e2e_done     e2e passed attempt N (review already approved too); next
#                restart skips straight to the before_approval_finalized gate
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
        with open(FEATURE_LIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_feature_list_raw(features: list) -> None:
    """Overwrite feature_list.json. Silently ignores write errors."""
    try:
        with open(FEATURE_LIST_PATH, "w", encoding="utf-8") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        _log("harness", "CHECKPOINT_WRITE_ERROR", str(exc), level="warning")


# Checkpoint step markers written to feature_list.json's "_checkpoint" field
# by _save_checkpoint() and compared against by run_feature_cycle() to decide
# what to skip on resume. Internal to this file only (never sent to an agent
# prompt), unlike the STATUS_*/VERDICT_* constants in tools.py.
CKPT_SPEC_DONE = "spec_done"
CKPT_IMPL_DONE = "impl_done"
CKPT_REVIEW_DONE = "review_done"
CKPT_E2E_DONE = "e2e_done"


def _save_checkpoint(feature_id: int, step: str, attempt: int = 1) -> None:
    """
    Write a checkpoint for feature_id after completing `step`.
    step must be one of: CKPT_SPEC_DONE, CKPT_IMPL_DONE, CKPT_REVIEW_DONE, CKPT_E2E_DONE.
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
    # Some providers' "thinking"/extended-reasoning modes (e.g. DeepSeek's
    # deepseek-reasoner) return a reasoning_content field on the assistant
    # message alongside content, and require it to be echoed back verbatim
    # on the next turn or the API rejects the request with 400 ("The
    # reasoning_content in the thinking mode must be passed back to the
    # API"). _msg_field's getattr fallback makes this a no-op for any
    # message/provider that doesn't set the field, so this stays generic
    # rather than tied to one provider.
    reasoning_content = _msg_field(m, "reasoning_content", None)
    if reasoning_content:
        out["reasoning_content"] = reasoning_content
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
        with open(FEATURE_LIST_PATH, "r") as f:
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
        with open(FEATURE_LIST_PATH, "w") as f:
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


def _msg_tool_call_id(tc) -> Optional[str]:
    """Uniformly read a tool_call's id off a call that can be dict or pydantic."""
    if isinstance(tc, dict):
        return tc.get("id")
    return getattr(tc, "id", None)


_DIGEST_MAX_CHARS = 4000  # hard cap on the whole digest string — see _build_deterministic_digest


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

    That first fix (calls-only) turned out to have the same failure mode one
    level down: it told the agent "don't repeat these calls" but discarded
    every result, so there was nothing to act on except repeating them
    anyway — confirmed on feature 74's log, where every compaction was
    followed by re-reading the same 3 files. This version keeps bounded,
    deterministic content per result alongside the call list: the most
    recent read_file excerpt per unique path (a re-read of the same path
    overwrites the earlier excerpt — only the latest content matters), the
    tail of the last run_playwright_tests output, and the head of any
    grep-shaped run_bash output. Still no LLM call, still nothing
    paraphrased — just more of the real bytes survive compaction. The whole
    digest is capped at _DIGEST_MAX_CHARS regardless, since a richer digest
    still has to fit the context window it was built to protect.
    """
    decisions: list = []
    calls: list = []
    errors: list = []
    file_excerpts: dict = {}   # path -> most recent read_file content excerpt
    playwright_tail = ""       # tail of the last run_playwright_tests output
    grep_heads: list = []      # (command preview, first ~5 stdout lines) per grep-shaped run_bash call

    # tool_call_id -> (fn_name, fn_args) from the assistant message that made
    # the call, so the "tool" result message that follows (correlated via its
    # own tool_call_id) can be attributed back to what actually produced it.
    call_by_id: dict = {}

    for m in middle:
        role = (_msg_field(m, "role", "") or "").lower()

        if role == "assistant":
            content = _msg_field(m, "content", "") or ""
            if isinstance(content, str) and content.strip():
                decisions.append(content.strip()[:200])
            for tc in _msg_tool_calls(m):
                name, args = _tool_call_name_args(tc)
                calls.append(f"{name}({str(args)[:120]})")
                tc_id = _msg_tool_call_id(tc)
                if tc_id:
                    call_by_id[tc_id] = (name, args)

        elif role == "tool":
            content = _msg_field(m, "content", "") or ""
            if isinstance(content, list):
                content = json.dumps(content, ensure_ascii=False)
            content = str(content)
            flagged = False
            parsed = None
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "error" in parsed:
                    errors.append(str(parsed["error"])[:200])
                    flagged = True
            except Exception:
                pass
            if not flagged and ("traceback" in content.lower() or "exception" in content.lower()):
                errors.append(content[:200])

            fn_name, fn_args = call_by_id.get(_msg_field(m, "tool_call_id", None), (None, None))
            if flagged or fn_name is None:
                continue
            try:
                parsed_args = json.loads(fn_args) if isinstance(fn_args, str) else (fn_args or {})
            except Exception:
                parsed_args = {}

            if fn_name == "read_file" and isinstance(parsed, dict) and "content" in parsed:
                path = (parsed_args.get("path") or parsed_args.get("file_path")
                        or parsed_args.get("file") or parsed_args.get("filename") or parsed.get("path"))
                if path:
                    file_excerpts[path] = str(parsed["content"])[:300]

            elif fn_name == "run_playwright_tests" and isinstance(parsed, dict) and "output" in parsed:
                playwright_tail = str(parsed["output"])[-500:]

            elif fn_name == "run_bash" and "grep" in str(parsed_args.get("command", "")):
                stdout = parsed.get("stdout", "") if isinstance(parsed, dict) else ""
                head = "\n".join(str(stdout).splitlines()[:5])
                if head:
                    grep_heads.append((str(parsed_args.get("command", ""))[:80], head))

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

    if file_excerpts:
        lines.append("### Key file contents already seen (most recent read per path — do NOT re-read unless the file may have changed):")
        for path, excerpt in file_excerpts.items():
            lines.append(f"  - {path}:\n    {excerpt}")

    if playwright_tail:
        lines.append("### Last run_playwright_tests output (tail):")
        lines.append(f"  {playwright_tail}")

    if grep_heads:
        lines.append("### Recent run_bash grep results (first lines):")
        for cmd, head in grep_heads[-5:]:
            lines.append(f"  - {cmd}:\n    {head}")

    digest = "\n".join(lines)
    if len(digest) > _DIGEST_MAX_CHARS:
        digest = digest[:_DIGEST_MAX_CHARS] + "\n... [digest truncated at ~4KB cap] ..."
    return digest


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


# ─── STRUCTURED AGENT STATUS (progress/<stage>_<id>.json) ────────────────────
# Agents write a sibling JSON file next to their prose report (e.g.
# progress/impl_3.json next to progress/impl_3.md) with a minimal structured
# status: {schema_version, status, tests_passed, files_touched, reason}. This
# replaces the substring/prefix heuristics below (_verdict_is + .replace(...)
# on a returned chat string, "passed" in content on impl_N.md) with an exact
# field read, while falling back to the old heuristic when the sibling file
# is absent — so progress/ directories from before this schema existed keep
# working unmodified. A sibling file (not a block embedded in the .md) was
# chosen deliberately: agents/spec_writer.py's own template instructs writing
# example response shapes like {data, total, page, page_size} directly into
# the prose, and impl/review reports embed raw pytest output — both can
# contain JSON-looking text, so scanning the .md for "the" JSON block would
# be ambiguous. A sibling file has no such collision risk and needs no
# extraction/regex — just json.load() the whole file.
#
# Versioning (STATUS_SCHEMA_VERSION, tools.py): the four spawnable agents all
# write this same shape, but only checking that a "schema_version" *key* is
# present (as the original version of this reader did) can't tell "current
# shape" apart from "some past or future shape that happens to also carry a
# schema_version key" — a real risk once this shape's first change ships,
# since a stale progress/ directory from before that change would otherwise
# be silently read as if it were current. AgentStatusSchema below validates
# the shape (same pydantic style as FeatureSchema for feature_list.json,
# extra="forbid" so a drifted field is a loud validation error, not a silent
# .get()-returns-None no-op); a schema_version that doesn't match
# STATUS_SCHEMA_VERSION is checked and logged *before* that validation runs,
# as its own distinct outcome (STATUS_SCHEMA_VERSION_MISMATCH) rather than
# falling through to a generic validation-failure log — it's not that the
# file is malformed, it's that this code doesn't know that version's shape
# yet (or anymore). Either way the never-raise contract holds: any mismatch
# or validation failure still returns None, and every caller already treats
# None as "fall back to the prose heuristic" — this is purely additive
# detection on top of the exact same fallback behavior as before.
#
# status vocabulary: "status" means something different per writer — "ok"
# (spec_writer), "done" (implementer), STATUS_APPROVED/STATUS_REJECTED
# (reviewer), STATUS_PASSED/STATUS_FAILED (e2e_tester) — and this reader is
# deliberately role-agnostic (it's handed a bare report_path, not told which
# agent wrote it; see _reviewer_verdict/_e2e_verdict/spawn_implementer's call
# sites). Rather than plumb a role parameter through all 3 call sites just to
# validate each file against its own role's narrower vocabulary, _AGENT_STATUS_VALUES
# below is every value any of the 4 writers can legitimately produce today —
# enough to catch what actually matters (a hallucinated/typo'd status that
# belongs to no role, e.g. "complete" instead of "done") without that plumbing.
_AGENT_STATUS_VALUES = {"ok", "done", STATUS_APPROVED, STATUS_REJECTED, STATUS_PASSED, STATUS_FAILED}


class AgentStatusSchema(BaseModel):
    """
    Shape of progress/<stage>_<id>.json — see the "STRUCTURED AGENT STATUS"
    comment block above and STATUS_SCHEMA_VERSION (tools.py) for the version
    this validates against.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    status:         str
    tests_passed:   Optional[bool] = None
    files_touched:  Optional[list[str]] = None
    reason:         Optional[str] = None
    # Implementer-only, optional: "failed" when the implementer ended via the
    # sanctioned PREMISE CHECK EXIT (its direct verification refuted the
    # spec's diagnosis). Omitted entirely when not applicable.
    premise_check:  Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status_must_be_known(cls, v: str) -> str:
        if v not in _AGENT_STATUS_VALUES:
            raise ValueError(f"must be one of {sorted(_AGENT_STATUS_VALUES)}, got '{v}'")
        return v

    @field_validator("premise_check")
    @classmethod
    def _premise_check_must_be_known(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("failed", "passed"):
            raise ValueError(f"must be 'failed' or 'passed' when present, got '{v}'")
        return v


def _read_structured_status(report_path: str) -> Optional[dict]:
    """
    Read the sibling <report_path minus extension>.json written alongside an
    agent's prose report. Returns None (never raises) if the file is absent,
    unreadable, not valid JSON, not a dict, missing "schema_version", on a
    schema_version mismatch, or on any other AgentStatusSchema validation
    failure — every one of those collapses to the same "no structured data
    available" signal every caller already handles by falling back to the
    prose heuristic. A version mismatch is logged as its own distinct event
    (see the module comment above) before that fallback, so it's visible in
    progress/harness.log instead of looking identical to "file never existed".
    """
    json_path = os.path.splitext(report_path)[0] + ".json"
    if not os.path.exists(json_path):
        return None
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or "schema_version" not in data:
        return None

    if data.get("schema_version") != STATUS_SCHEMA_VERSION:
        _log("harness", "STATUS_SCHEMA_VERSION_MISMATCH",
             f"path={json_path} found_version={data.get('schema_version')!r} "
             f"expected_version={STATUS_SCHEMA_VERSION} — ignoring, falling back "
             f"to the prose heuristic for this file", level="warning")
        return None

    try:
        AgentStatusSchema.model_validate(data)
    except ValidationError as exc:
        errs = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors())
        _log("harness", "STATUS_SCHEMA_VALIDATION_ERROR",
             f"path={json_path} — {errs}", level="warning")
        return None

    return data


def _reviewer_verdict(result: str, review_path: str) -> tuple[bool, str]:
    """
    Decide reviewer approval and extract the rejection reason.
    Prefers the structured progress/review_<id>.json ("status": "approved"/
    "rejected", "reason") over parsing the returned chat string; falls back
    to _verdict_is(result, "APPROVED") + stripping the "REJECTED:" prefix
    when no structured file exists.
    """
    status = _read_structured_status(review_path)
    if status is not None:
        approved = status.get("status") == STATUS_APPROVED
        reason = "" if approved else (status.get("reason") or "")
        return approved, reason
    approved = _verdict_is(result, VERDICT_APPROVED)
    reason = "" if approved else result.replace(f"{VERDICT_REJECTED}:", "").strip()
    return approved, reason


def _e2e_verdict(result: str, e2e_path: str) -> tuple[bool, str]:
    """
    Decide E2E pass/fail and extract the failure reason.
    Prefers the structured progress/e2e_<id>.json ("status": "passed"/
    "failed", "reason") over parsing the returned chat string; falls back
    to _verdict_is(result, "E2E_PASSED") + stripping the "E2E_FAILED:"
    prefix when no structured file exists.
    """
    status = _read_structured_status(e2e_path)
    if status is not None:
        passed = status.get("status") == STATUS_PASSED
        reason = "" if passed else (status.get("reason") or "")
        return passed, reason
    passed = _verdict_is(result, VERDICT_E2E_PASSED)
    reason = "" if passed else result.replace(f"{VERDICT_E2E_FAILED}:", "").strip()
    return passed, reason


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
    if any(k in msg for k in ("max_iter", "zero writes", "blocked", "assertion", "error:")):
        return "LOGICAL"
    return "FATAL"


def _is_write_call(fn_name: str, result: str) -> bool:
    """
    Did this tool call actually mutate a file? True for a direct write_file/
    append_file/update_feature_status call. Also true for a hallucinated
    edit-style call (edit_file, str_replace, ...) that _edit_alias
    (tools.py) transparently translated into a real write_file — recognized
    by that translation's exact success marker, so a *failed* alias attempt
    (path not found, old_string not unique, etc.) does not count.
    """
    if fn_name in _WRITE_TOOL_NAMES:
        return True
    return "auto-translated to a real read_file + write_file" in result


# ─── GENERIC AGENT ENGINE ────────────────────────────────────────────────────

# ─── Implementer investigation digest (max_iter handoff) ────────────────────
# Real incident (feature #77, round 2): tool_call_errors below already hands a
# retry the last 5 tool ERRORS (and did its job in round 1, re-feeding the
# edit_file failure) — but round 2's attempt 1 had ZERO tool errors: 107 clean
# read_file + 68 clean run_bash, including the key finding that
# `pytest tests/test_branches.py` passed in full. Attempt 2 started blind and
# repeated essentially the same investigation (51 reads before writing
# anything). The existing mechanism re-feeds errors; it does not re-feed
# KNOWLEDGE. This digest does — deterministic, no LLM call, same philosophy
# as _build_deterministic_digest and the e2e max_iter report synthesis.

_INVESTIGATION_MAX_FILES = 30
_INVESTIGATION_MAX_CMDS  = 12
_INVESTIGATION_VERIFY_RE = re.compile(r"pytest|npm (test|run)|npx |curl|psql|python3? -m|node ")


def _investigation_digest_path(feature_id: int) -> str:
    # Underscore prefix: harness-internal working file, same convention as
    # the _state_*.json message snapshots living under progress/.
    return f"{PROGRESS_DIR}/_investigation_impl_{feature_id}.md"


def _bash_outcome_line(result: str) -> str:
    """One-line summary of a run_bash result (e.g. pytest's '29 passed in
    1.2s' closing line). Best-effort, bounded, never raises."""
    try:
        parsed = json.loads(result)
    except Exception:
        stripped = result.strip()
        return stripped.splitlines()[-1][:120] if stripped else "(no output)"
    if isinstance(parsed, dict):
        if parsed.get("error"):
            return f"error: {str(parsed['error'])[:110]}"
        out = str(parsed.get("stdout") or "").strip()
        if not out:
            out = str(parsed.get("stderr") or "").strip()
        if out:
            lines = [line for line in out.splitlines() if line.strip()]
            if lines:
                return lines[-1][:120]
    return "(no output)"


def _build_investigation_digest(files_read: list, bash_outcomes: dict,
                                last_assistant_text: str, cutoff_note: str) -> str:
    """
    Short, deterministic context block handed to the next implementer attempt
    when this one was cut off without a verdict (max_iter, or the
    MAX_ITER_WITHOUT_WRITE zero-write abort — cutoff_note says which):
    (a) deduplicated files already read, (b) commands already run with their
    one-line outcome, (c) the last assistant reasoning text before the cutoff
    (usually contains the active hypothesis). Bounded so it informs the retry
    without eating the budget it exists to protect.
    """
    parts = [f"(previous attempt was cut off — {cutoff_note} — without finishing; "
             f"its investigation so far:)"]
    if files_read:
        shown = files_read[:_INVESTIGATION_MAX_FILES]
        parts.append("## Files already read (deduplicated)\n"
                     + "\n".join(f"- {p}" for p in shown)
                     + (f"\n(+{len(files_read) - len(shown)} more)" if len(files_read) > len(shown) else ""))
    cmds = list(bash_outcomes.items())
    if cmds:
        if len(cmds) > _INVESTIGATION_MAX_CMDS:
            # Verification runs (pytest, curl, psql, ...) carry the real
            # findings — keep those over greps when the list must be cut.
            prioritized = [c for c in cmds if _INVESTIGATION_VERIFY_RE.search(c[0])]
            rest        = [c for c in cmds if not _INVESTIGATION_VERIFY_RE.search(c[0])]
            cmds = (prioritized + rest)[:_INVESTIGATION_MAX_CMDS]
        parts.append("## Commands already run → last output line\n"
                     + "\n".join(f"- {cmd} → {outcome}" for cmd, outcome in cmds))
    if last_assistant_text:
        parts.append("## Last reasoning before the cutoff (likely the active hypothesis)\n"
                     + last_assistant_text.strip()[-600:])
    return "\n\n".join(parts)[:4000]


def run_agent(system_prompt: str, tools: list, task: str,
              role: str = "agent", color: str = "white",
              max_iter: int = MAX_ITER_AGENT,
              checkpoint_key: Optional[str] = None,
              feature_id: Optional[int] = None) -> str:
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
    _no_write_streak = 0
    # e2e_tester only: evidence from the most recent run_playwright_tests
    # call, kept so a max_iter cutoff with no report at all still leaves a
    # real traceback for the next retry/diagnostician instead of nothing.
    # See the synthesis block after the main loop below.
    _last_playwright_evidence = ""
    # implementer only: investigation trackers for the max_iter digest (see
    # _build_investigation_digest above). Live trackers rather than a walk of
    # `messages` at cutoff time, deliberately: _compact_messages can discard
    # early history, but these survive compaction. (They do NOT survive a
    # crash+resume via _load_message_state — acceptable, that's the rare
    # corner and the digest is best-effort.)
    _impl_files_read: list = []
    _impl_bash_outcomes: dict = {}
    _impl_last_assistant_text = ""
    # Zero-write hard cut (MAX_ITER_WITHOUT_WRITE): _no_write_streak resets on
    # every write, so it can't answer "has this attempt written ANYTHING yet";
    # this flag can. Set once, never reset.
    _any_write_this_attempt = False
    _zero_write_abort_iters: Optional[int] = None

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
        if CONVERGENCE_STREAK_LIMIT and _no_write_streak and _no_write_streak % CONVERGENCE_STREAK_LIMIT == 0:
            # Escalate from the second firing on. Real incident (feature #77,
            # round 2, attempt 1): ~11 identical soft nudges fired and the
            # agent kept reading until iteration 80 without writing — the
            # same polite text repeated is easy to under-attend to.
            _nudge_number = _no_write_streak // CONVERGENCE_STREAK_LIMIT
            if _nudge_number >= 2:
                _nudge_text = (
                    f"⚠️ CONVERGENCE CHECKPOINT #{_nudge_number} — ESCALATED: you have now gone "
                    f"{_no_write_streak} iterations without writing anything, ignoring at least "
                    "one previous checkpoint. Your NEXT tool call MUST be write_file — your best "
                    "partial fix, or a reproduction script capturing what you have verified so "
                    "far. Anything else counts as a protocol violation."
                )
                if MAX_ITER_WITHOUT_WRITE > 0 and not _any_write_this_attempt:
                    _nudge_text += (
                        f" The harness will abort this attempt outright at "
                        f"{MAX_ITER_WITHOUT_WRITE} total iterations with zero writes."
                    )
            else:
                _nudge_text = (
                    f"⚠️ CONVERGENCE CHECKPOINT: you have gone {_no_write_streak} tool call "
                    "iteration(s) without writing or editing anything. If you already know "
                    "what to change and where, stop exploring and make the edit now — do not "
                    "keep re-verifying something you've already confirmed. If you genuinely "
                    "don't know yet, re-read the task description itself rather than more of "
                    "the codebase."
                )
            messages.append({"role": "user", "content": _nudge_text})
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

        _track_usage(role, api_response.usage, getattr(api_response, "model", None))
        msg = api_response.choices[0].message

        if not msg.tool_calls:
            _log(role, "DONE", (msg.content or "")[:120])
            _clear_message_state(checkpoint_key)
            return msg.content or ""

        messages.append(msg)
        messages = _compact_messages(messages, role)

        if role == "implementer" and msg.content:
            _impl_last_assistant_text = str(msg.content)

        made_write_this_iter = False
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
            if _verbosity_at_least("verbose"):
                _agent_action(role, fn_name, args_preview, i + 1)

            _log(role, "TOOL_CALL", f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")
            result = _redact(execute_tool(fn_name, fn_args, role=role))
            _log(role, "TOOL_RESULT", result[:200])

            if _is_write_call(fn_name, result):
                made_write_this_iter = True

            # implementer only: feed the investigation trackers (files read,
            # command outcomes) for the max_iter digest.
            if role == "implementer":
                if fn_name == "read_file":
                    _read_path = (fn_args.get("path") or fn_args.get("file_path")
                                  or fn_args.get("file") or fn_args.get("filename"))
                    if _read_path and _read_path not in _impl_files_read:
                        _impl_files_read.append(_read_path)
                elif fn_name == "run_bash":
                    _cmd = str(fn_args.get("command", ""))[:160]
                    if _cmd:
                        # dict preserves insertion order; a re-run updates the
                        # outcome in place. Bounded: drop oldest beyond 3x the
                        # digest cap (the digest builder re-prioritizes anyway).
                        _impl_bash_outcomes[_cmd] = _bash_outcome_line(result)
                        while len(_impl_bash_outcomes) > _INVESTIGATION_MAX_CMDS * 3:
                            _impl_bash_outcomes.pop(next(iter(_impl_bash_outcomes)))

            # e2e_tester only: keep the most recent run_playwright_tests
            # evidence (captured post-_redact, same as everything else that
            # lands in messages/reports). Prefer "output" (pytest/Playwright
            # stdout+stderr incl. traceback); fall back to "error" (e.g. an
            # install failure or subprocess timeout, which never produces an
            # "output" field) so a max_iter cutoff still has something better
            # than nothing even if the run never got as far as producing test
            # output.
            if role == "e2e_tester" and fn_name == "run_playwright_tests":
                try:
                    parsed_pw = json.loads(result)
                    evidence = parsed_pw.get("output") or parsed_pw.get("error")
                    if evidence:
                        _last_playwright_evidence = str(evidence)
                except Exception:
                    pass

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

            if _verbosity_at_least("verbose"):
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

        _no_write_streak = 0 if made_write_this_iter else _no_write_streak + 1
        if made_write_this_iter:
            _any_write_this_attempt = True

        # Snapshot after this iteration's tool calls are recorded — a crash
        # past this point loses at most the current iteration, not the whole
        # attempt. Best-effort and cheap (local disk write, no LLM call).
        if checkpoint_key:
            _save_message_state(checkpoint_key, messages)

        # Hard cut: the soft nudges above are advisory and were ignored ~11
        # times in a row in the feature #77 incident. An attempt that has
        # made ZERO writes by this point is not going to converge — abort
        # early so (combined with the investigation digest below) the retry
        # starts informed with half the budget still unspent.
        if (MAX_ITER_WITHOUT_WRITE > 0 and not _any_write_this_attempt
                and (i + 1) >= MAX_ITER_WITHOUT_WRITE):
            _zero_write_abort_iters = i + 1
            break

    if _zero_write_abort_iters is not None:
        _log(role, "ZERO_WRITE_ABORT",
             f"Attempt aborted after {_zero_write_abort_iters} iterations with zero writes "
             f"(MAX_ITER_WITHOUT_WRITE={MAX_ITER_WITHOUT_WRITE})", level="warning")
        _cutoff_note = f"aborted after {_zero_write_abort_iters} iterations with zero writes"
    else:
        _log(role, "MAX_ITER", f"Reached iteration limit of {max_iter}", level="warning")
        _cutoff_note = f"hit max_iter {max_iter}"
    _clear_message_state(checkpoint_key)

    # e2e_tester only: if this attempt never wrote its own progress/e2e_<id>.json
    # (the agent may not have written ANY report at all — the case this
    # covers that the .md-recovery fallback in spawn_e2e_tester cannot, since
    # that one requires an .md to already exist), synthesize one from the
    # last captured run_playwright_tests evidence rather than leaving the
    # next retry / diagnostician with only the generic max_iter message.
    # Real incident, feature #74: the e2e_tester hit max_iter twice with no
    # report; the actual cause (a TimeoutError waiting on #prof-name after a
    # successful login+submit — a redirect() bug in layout.tsx) was sitting
    # in the tool results the whole time and was thrown away, so the retry
    # and the diagnostician both started blind.
    if role == "e2e_tester" and feature_id is not None:
        json_path = f"{PROGRESS_DIR}/e2e_{feature_id}.json"
        if not os.path.exists(json_path):
            tail = (_last_playwright_evidence[-1500:] if _last_playwright_evidence
                    else "(no run_playwright_tests output was captured this attempt)")
            try:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "schema_version": STATUS_SCHEMA_VERSION,
                        "status": STATUS_FAILED,
                        "tests_passed": False,
                        "files_touched": [],
                        "reason": tail,
                    }, f, ensure_ascii=False)
                with open(f"{PROGRESS_DIR}/e2e_{feature_id}.md", "w", encoding="utf-8") as f:
                    f.write(
                        f"# E2E report for feature #{feature_id}\n\n"
                        f"(synthesized by harness after max_iter — the agent never wrote its own report)\n\n"
                        f"## Last run_playwright_tests evidence (tail)\n```\n{tail}\n```\n\n"
                        f"- Verdict: {VERDICT_E2E_FAILED}: {_cutoff_note} with no agent-written "
                        f"report; see the captured Playwright evidence above.\n"
                    )
                _log("harness", "E2E_MAX_ITER_REPORT_SYNTHESIZED",
                     f"feature={feature_id} — wrote a synthetic e2e_{feature_id}.json/.md from the "
                     f"last captured run_playwright_tests evidence")
            except OSError as exc:
                _log("harness", "E2E_MAX_ITER_REPORT_SYNTHESIZE_ERROR",
                     f"feature={feature_id}: {exc}", level="warning")

    # implementer only: persist the investigation digest so the next attempt
    # spends its budget on what this one did NOT reach, instead of repeating
    # the same reads/commands. Counterpart to tool_call_errors below, which
    # only re-feeds ERRORS — an attempt with 175 clean tool calls (feature
    # #77, round 2, attempt 1) left the retry nothing at all.
    if role == "implementer" and feature_id is not None \
            and (_impl_files_read or _impl_bash_outcomes or _impl_last_assistant_text):
        try:
            with open(_investigation_digest_path(feature_id), "w", encoding="utf-8") as f:
                f.write(_build_investigation_digest(
                    _impl_files_read, _impl_bash_outcomes,
                    _impl_last_assistant_text, _cutoff_note))
            _log("harness", "IMPL_MAX_ITER_INVESTIGATION_SAVED",
                 f"feature={feature_id} — saved investigation digest "
                 f"({len(_impl_files_read)} files read, {len(_impl_bash_outcomes)} commands) "
                 f"for the next attempt")
        except OSError as exc:
            _log("harness", "IMPL_MAX_ITER_INVESTIGATION_SAVE_ERROR",
                 f"feature={feature_id}: {exc}", level="warning")

    if _zero_write_abort_iters is not None:
        _err_head = (f"[ERROR: attempt aborted: {_zero_write_abort_iters} iterations "
                     f"with zero writes (MAX_ITER_WITHOUT_WRITE={MAX_ITER_WITHOUT_WRITE})]")
    else:
        _err_head = f"[ERROR: max_iter {max_iter} reached]"
    if tool_call_errors:
        recent_errors = "\n".join(f"  - {e}" for e in tool_call_errors[-5:])
        return f"{_err_head}\nRecent tool-call errors:\n{recent_errors}"
    return _err_head


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

    # Fired each time the Reviewer rejects a feature cycle (before deciding
    # whether to retry or give up). Unlike after_feature_failed, this fires
    # on EVERY rejection, including ones that will still be retried.
    # kwargs: feature_id (int), description (str), attempt (int),
    #         max_attempts (int), rejection_reason (str)
    "after_reviewer_rejected": [],

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

def _truncate_head_tail(text: str, head_chars: int = 6000, tail_chars: int = 6000) -> str:
    """
    Truncate text to its first head_chars + last tail_chars, joined by a
    "[...middle truncated...]" marker, when it exceeds head_chars + tail_chars
    combined. Returns text unchanged otherwise (never inserts the marker into
    text short enough to not need truncating).

    Used by _validate_spec on the spec content it sends for review: a bare
    head-only [:3000] truncation cut the tests/notes section out of any
    non-trivial spec entirely — spec_74.md's wrong E2E test directory lived
    exactly there. The header (files to touch) and the tail (tests, notes)
    are the sections with the most detectable issues, so keeping both ends
    catches far more than extending a single head-only cutoff would.
    """
    if len(text) <= head_chars + tail_chars:
        return text
    return f"{text[:head_chars]}\n\n[...middle truncated...]\n\n{text[-tail_chars:]}"


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

    # Stack-aware (CODE_TREE_DIRS, resolved from stack_layout — same source
    # of truth spawn_implementer/spawn_reviewer already use), not a
    # hardcoded "src"/"tests" — a project whose stack profile names its
    # source dir something else (e.g. backend/) would otherwise get an
    # empty/wrong tree here, silently making this validation useless for it.
    tree_sections = "\n\n".join(
        f"Existing files in {d}/:\n{_file_tree(d) if os.path.exists(d) else '(not created yet)'}"
        for d in CODE_TREE_DIRS
    )

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
                        f"Spec to review:\n{_truncate_head_tail(spec_content)}\n\n"
                        f"{tree_sections}\n\n"
                        "List only concrete issues: wrong file paths, conflicting interfaces, "
                        "duplicate responsibilities, or missing prerequisite files. "
                        "The file lists above may be truncated (noted inline when they are) — "
                        "never report a file as missing/nonexistent solely because it doesn't "
                        "appear in a truncated list; only flag a file path issue you can "
                        "actually confirm is wrong (e.g. references a directory that doesn't "
                        "exist at all, or clearly contradicts a file you CAN see). "
                        "Ignore style and completeness. If none, reply: OK"
                    )
                }
            ],
            tools = [],
            role  = "spec_writer",
        )
        if response is None:
            return ""
        _track_usage("spec_writer", response.usage, getattr(response, "model", None))
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
    """
    Compact snapshot of the relevant file tree (without node_modules).

    Sorts alphabetically and truncates at max_files. When truncated, appends
    a note saying so — without it, a file whose path sorts past the cutoff
    (e.g. tests/test_migrations.py in a large tests/ dir) reads as "absent"
    to anything consuming this string, which _validate_spec previously
    misread as "doesn't exist" and reported as a spec issue even though the
    file was on disk the whole time.
    """
    try:
        result = subprocess.run(
            ["find", path, "-type", "f",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/__pycache__/*",
             "-not", "-path", "*/.git/*"],
            capture_output=True, text=True, timeout=5
        )
        all_lines = sorted(result.stdout.strip().splitlines())
        lines = all_lines[:max_files]
        tree = "\n".join(lines) or "(empty)"
        if len(all_lines) > max_files:
            tree += (
                f"\n... ({len(all_lines)} files total, showing first {max_files} alphabetically — "
                f"this list is truncated, absence from it is not proof a file doesn't exist)"
            )
        return tree
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
    impl_path = f"{PROGRESS_DIR}/impl_{feature_id}.md"

    # Reuse existing impl if it already exists and shows passing tests.
    # Prefers the structured progress/impl_<id>.json ("tests_passed": bool,
    # "status" != "error") over the substring fallback below — the fallback
    # is exact-match-fragile (e.g. pytest output containing "2 failed, 1
    # passed" matches "passed" in content even though the run failed) but is
    # kept for progress/ directories written before this schema existed.
    if attempt == 1 and os.path.exists(impl_path):
        status = _read_structured_status(impl_path)
        if status is not None:
            # premise_check == "failed" means the report is a sanctioned
            # PREMISE CHECK EXIT, not a completed implementation — and its
            # tests_passed can legitimately be True (the passing tests ARE
            # the evidence that refuted the spec). Never reuse it as an impl.
            if (status.get("tests_passed") and status.get("status") != "error"
                    and status.get("premise_check") != "failed"):
                _log("implementer", "SKIP", f"Existing impl with passing tests: {impl_path}")
                _vprint("normal", f"  [blue]🔨 IMPLEMENTER[/] [dim]↩ reusing existing impl →[/] {impl_path}")
                return impl_path
        else:
            try:
                with open(impl_path, "r", encoding="utf-8") as f:
                    content = f.read()
                if "passed" in content and "[ERROR" not in content:
                    _log("implementer", "SKIP", f"Existing impl with passing tests: {impl_path}")
                    _vprint("normal", f"  [blue]🔨 IMPLEMENTER[/] [dim]↩ reusing existing impl →[/] {impl_path}")
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
                _spec_text = f.read()
            # Invariant, independent of the spec-side repro gate: CONFIRMED
            # claims without an attached repro script are downgraded to
            # HYPOTHESIS before the implementer ever sees them — an unverified
            # premise must never travel with the confidence of a verified one
            # (the exact mechanism of the feature #77 incident). Covers cached
            # and fallback-annotated specs the gate never re-checked.
            _downgraded = _downgrade_unbacked_confirmed(_spec_text, feature_id)
            if _downgraded is not _spec_text:
                _log("harness", "SPEC_CONFIRMED_DOWNGRADED",
                     f"feature={feature_id}: CONFIRMED claim(s) in {spec_path} downgraded to "
                     f"HYPOTHESIS at injection — no repro script attached", level="warning")
                _vprint("normal", f"  [blue]🔨 IMPLEMENTER[/] [yellow]⚠ spec's CONFIRMED claims "
                                  f"downgraded to HYPOTHESIS — no repro script attached[/]")
            spec_content = f"\n## Technical specification ({spec_path}):\n{_downgraded}\n"
        except Exception:
            spec_content = f"\nRead the technical specification at {spec_path} BEFORE writing code.\n"

    # If a reproduction script exists for this feature, surface it explicitly —
    # the REPRO SCRIPT PROTOCOL hard rule (agents/implementer.py) triggers off
    # "your task includes a repro script", so the harness must actually say so.
    repro_context = ""
    _repro = _existing_repro_script(feature_id)
    if _repro:
        repro_context = (
            f"\n## Reproduction script (mandatory protocol)\n"
            f"An executable reproduction script exists at {_repro}. Per your REPRO SCRIPT "
            f"PROTOCOL hard rule: run it FIRST to confirm the baseline failure, and run it "
            f"again LAST to confirm the fix before writing your report. If it does NOT fail "
            f"the way the spec claims, report a PREMISE DISCREPANCY at the top of your "
            f"report instead of hunting the bug where the spec points.\n"
        )

    # If an earlier attempt hit max_iter, its investigation digest (files
    # read + command outcomes + last hypothesis — see
    # _build_investigation_digest) is on disk: hand it over so this attempt
    # doesn't re-derive knowledge that already cost a full budget to gather.
    investigation_context = ""
    _inv_path = _investigation_digest_path(feature_id)
    if os.path.exists(_inv_path):
        try:
            with open(_inv_path, "r", encoding="utf-8") as f:
                investigation_context = (
                    f"\n## PREVIOUS ATTEMPT'S INVESTIGATION — do not re-derive this\n"
                    f"A previous attempt ran out of iterations before finishing, but its "
                    f"investigation survives below. Spend your budget on what it did NOT "
                    f"reach: do not re-read the files listed, and do not re-run commands "
                    f"whose outcome is already recorded here.\n{f.read()}\n"
                )
        except OSError:
            pass  # best-effort — a missing/unreadable digest never blocks the spawn

    task = (
        f"{_workdir_banner(cwd)}"
        f"{tree_sections}\n"
        f"{arch_context}"
        f"{layout_context}"
        f"{spec_content}"
        f"{repro_context}"
        f"{investigation_context}\n"
        f"Implement feature #{feature_id}: {description}{context}\n"
        f"Write your report to {impl_path}\n"
        f"Return only the file path when done."
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="implementer",
                                 system_prompt=impl_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], impl_cfg.TOOLS, _agent_ctx["task"],
                       role="implementer", color="blue", max_iter=MAX_ITER_IMPL,
                       checkpoint_key=f"implementer_{feature_id}_{attempt}",
                       feature_id=feature_id)
    done = not result.startswith("[ERROR")
    _vprint("normal", f"  [blue]🔨 IMPLEMENTER[/] {'[green]✓ done[/]' if done else '[red]✗ error[/]'} → {result[:80]}")
    return result


def _spec_references_stale_e2e_test_dir(spec_text: str) -> Optional[str]:
    """
    Detect a spec that sends E2E tests to another e2e_runner profile's
    test_dir/file_ext combo instead of the one actually resolved for this
    project (e.g. tests/e2e/*.py for Python/pytest-playwright vs e2e/*.spec.ts
    for Node/@playwright/test).

    Real incident: spec_74.md sent tests to e2e/biovet.spec.ts — the legacy
    Node suite from features #27-55 — while this project's resolved e2e
    runner is Python/pytest-playwright (tests/e2e/*.py). The implementer
    wrote 4 correct tests there that the real e2e run_cmd never executes.
    spawn_spec_writer's cache check must call this on every reuse (not just
    at generation time) — the spec_writer agent isn't even invoked on a
    cache hit, so a prompt-level rule can't catch this once poisoned.

    Returns a human-readable description of the conflicting reference, or
    None if the spec is clean. Best-effort: any resolution issue (missing
    stack_profiles.json, an unresolvable active profile) makes this a no-op
    rather than a false positive.
    """
    layout = resolve_layout()
    active_key = layout.get("e2e_key")
    for other_key, entry in all_e2e_runner_profiles().items():
        if other_key == active_key:
            continue
        other_dir = entry.get("test_dir")
        other_ext = entry.get("file_ext")
        if not other_dir or not other_ext:
            continue  # e.g. the "none" profile has no test_dir/file_ext
        match = re.search(re.escape(other_dir) + r"[\w./-]*" + re.escape(other_ext), spec_text)
        if match:
            return (
                f"references '{match.group(0)}', which belongs to the '{other_key}' e2e runner "
                f"profile ({other_dir}*{other_ext}), but this project's active e2e runner is "
                f"'{active_key}' ({layout.get('e2e_test_dir')}*{layout.get('e2e_file_ext')})"
            )
    return None


_BUGFIX_HINT_RE = re.compile(
    r"(?i)(\bfix(es|ed)?\b|\bbug(fix)?\b|\bregress?i[oó]n\b|\bdefecto\b"
    r"|no (se )?persiste|not persist|doesn'?t persist|isn'?t persisted"
    r"|wrong value|valor (equivocado|incorrecto)"
    r"|devuelve .{0,40}(incorrect|equivocad|err[oó]ne)"
    r"|returns? .{0,40}(wrong|incorrect))"
)


def _is_bugfix_feature(description: str) -> bool:
    """
    Heuristic used by the BUG-FIX RULE enforcement (see agents/spec_writer.py):
    does this feature's title/description read like a bug fix rather than new
    functionality? Deliberately keyword-based and cheap — a false positive
    only adds a non-blocking nudge/warning, never blocks the pipeline.
    """
    return bool(_BUGFIX_HINT_RE.search(description or ""))


def _existing_repro_script(feature_id: int) -> Optional[str]:
    """Path of progress/repro_<id>.py or .sh if one exists, else None."""
    for ext in (".py", ".sh"):
        path = f"{PROGRESS_DIR}/repro_{feature_id}{ext}"
        if os.path.exists(path):
            return path
    return None


_REPRO_NOT_FEASIBLE_RE = re.compile(r"(?i)\bREPRO:\s*NOT_FEASIBLE\b")


def _bugfix_spec_missing_repro(feature_id: int, spec_text: str) -> bool:
    """
    True when a bug-fix spec is *silently* missing its reproduction: no
    progress/repro_<id>.py/.sh on disk AND no explicit
    `REPRO: NOT_FEASIBLE — <reason>` declaration in the spec text.

    The declaration is the escape valve: it turns "silently absent" into
    "consciously absent, with the reason visible to the implementer" — which
    is what keeps the repro gate from looping forever or coercing the
    spec_writer into fake repros written only to pass the gate.
    """
    if _existing_repro_script(feature_id):
        return False
    if _REPRO_NOT_FEASIBLE_RE.search(spec_text or ""):
        return False
    return True


_CONFIRMED_LABEL_RE = re.compile(r"\bCONFIRMED\b")


def _downgrade_unbacked_confirmed(spec_text: str, feature_id: int) -> str:
    """
    Invariant, independent of the repro gate: a CONFIRMED root-cause label is
    only allowed to reach the implementer when an executable repro script is
    actually attached (progress/repro_<id>.py/.sh on disk). Otherwise every
    CONFIRMED in the spec is rewritten to HYPOTHESIS before injection, so an
    unverified premise never travels with the confidence of a verified one —
    even on the gate's fallback path (annotate-and-continue) or for cached
    specs the gate never saw. That confidence transfer was the exact
    mechanism of the feature #77 incident: confident prose treated as a
    confirmed diagnosis by CONVERGENCE_RULE's apply-directly clause.

    Returns spec_text unchanged (same object) when no downgrade applies, so
    callers can detect a downgrade by identity.
    """
    if _existing_repro_script(feature_id):
        return spec_text
    if not _CONFIRMED_LABEL_RE.search(spec_text or ""):
        return spec_text
    return _CONFIRMED_LABEL_RE.sub(
        "HYPOTHESIS (auto-downgraded: labeled CONFIRMED but no executable repro script is attached)",
        spec_text,
    )


def _refuted_premise_evidence(feature_id: int) -> Optional[str]:
    """
    Best-effort: evidence that the cached spec's diagnosis was refuted by
    direct verification, or None. Real incident (feature #77): a cached spec
    confidently blamed backend persistence; attempt 1 ran the exact suite the
    spec pointed at and everything PASSED — but the poisoned spec was
    reinjected verbatim on every retry and re-run (the spec_writer is never
    invoked on a cache hit), so nothing could act on that refutation.

    Sources, in priority order:
      1. progress/diagnosis_<id>.json with "cause": "wrong_premise" — written
         by an external plugin (e.g. the premium failure_diagnostician) after
         a feature's definitive failure. Absent/corrupt = no-op: base ships
         the consumer and waits for its feeder, the same pattern
         CONVERGENCE_RULE followed in agents/shared_rules.py.
      2. progress/impl_<id>.json with "premise_check": "failed" — the
         implementer's own sanctioned PREMISE CHECK EXIT verdict (read raw,
         not via _read_structured_status, so it still counts even if some
         other field in that file wouldn't validate).
      3. Fallback for reports written before the structured field existed:
         the literal "PREMISE_CHECK: FAILED" in progress/impl_<id>.md.

    Returns a bounded, human-readable evidence string for injection into the
    regenerated spec_writer's task.
    """
    try:
        with open(f"{PROGRESS_DIR}/diagnosis_{feature_id}.json", "r", encoding="utf-8") as f:
            diag = json.load(f)
        if isinstance(diag, dict) and diag.get("cause") == "wrong_premise":
            explanation = diag.get("explanation")
            if explanation:
                return str(explanation)[:800]
            return (f"a failure diagnosis recorded cause=wrong_premise for feature "
                    f"{feature_id} (no explanation field)")
    except Exception:
        pass

    premise_failed = False
    try:
        with open(f"{PROGRESS_DIR}/impl_{feature_id}.json", "r", encoding="utf-8") as f:
            impl_status = json.load(f)
        premise_failed = isinstance(impl_status, dict) and impl_status.get("premise_check") == "failed"
    except Exception:
        pass

    md_text = ""
    try:
        with open(f"{PROGRESS_DIR}/impl_{feature_id}.md", "r", encoding="utf-8") as f:
            md_text = f.read()
    except Exception:
        pass
    if not premise_failed and "PREMISE_CHECK: FAILED" not in md_text:
        return None
    idx = md_text.find("PREMISE_CHECK")
    if idx != -1:
        return md_text[idx:idx + 800]
    return (f"the implementer's premise check failed: its direct verification contradicted "
            f"the spec's diagnosis (see progress/impl_{feature_id}.md)")


def spawn_spec_writer(feature_id: int, description: str) -> str:
    """Generate the detailed technical spec before implementing.
    If the spec already exists on disk, reuse it without calling the agent.
    """
    spec_path = f"{PROGRESS_DIR}/spec_{feature_id}.md"

    # Reuse existing spec — avoids spending iterations regenerating. But
    # first check it doesn't point E2E tests at another e2e_runner profile's
    # test_dir/file_ext (see _spec_references_stale_e2e_test_dir above) — a
    # poisoned cached spec is injected in full on every implementer retry
    # (and survives any manual reset to "pending"), and the spec_writer
    # agent's own prompt rules can't catch it because it's never invoked on
    # a cache hit in the first place.
    _refuted_evidence: Optional[str] = None
    if os.path.exists(spec_path):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                _cached_spec_text = f.read()
        except OSError:
            _cached_spec_text = ""
        _stale_reason = _spec_references_stale_e2e_test_dir(_cached_spec_text)
        # Second anti-poisoning check, same spirit: a cached spec whose
        # diagnosis was refuted by direct verification (see
        # _refuted_premise_evidence) must not be reinjected verbatim into
        # every retry. Computed even when _stale_reason already fired, so the
        # regeneration task below carries the evidence either way.
        _refuted_evidence = _refuted_premise_evidence(feature_id)
        if _stale_reason:
            _stale_path = f"{spec_path}.stale"
            try:
                os.replace(spec_path, _stale_path)
            except OSError:
                pass  # best-effort quarantine — regenerate below either way;
                       # the fresh write overwrites spec_path regardless
            _log("spec_writer", "SPEC_STALE_E2E_PATH",
                 f"feature={feature_id}: {_stale_reason} — quarantined to {_stale_path}, regenerating",
                 level="warning")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ cached spec pointed at the wrong "
                              f"e2e runner's test dir — quarantined, regenerating[/]")
        elif _refuted_evidence:
            _stale_path = f"{spec_path}.stale"
            try:
                os.replace(spec_path, _stale_path)
            except OSError:
                pass  # best-effort quarantine — same rationale as above
            _log("spec_writer", "SPEC_PREMISE_REFUTED",
                 f"feature={feature_id}: cached spec's diagnosis was refuted by direct "
                 f"verification — quarantined to {_stale_path}, regenerating",
                 level="warning")
            _vprint("normal", "  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ cached spec's diagnosis was "
                              "refuted by direct verification — quarantined, regenerating[/]")
        else:
            _log("spec_writer", "SKIP", f"Spec already exists: {spec_path}")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [dim]↩ reusing existing spec →[/] {spec_path}")
            return spec_path

    _phase_header("spec_writer", "Writing spec", feature_id)
    cwd = os.getcwd()
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()
    # Bug-fix features: make the BUG-FIX RULE's target path explicit in the
    # task, so the repro requirement doesn't depend solely on the agent
    # matching the feature title against its prompt rule.
    repro_hint = ""
    if _is_bugfix_feature(description):
        repro_hint = (
            f"This feature is a BUG FIX. Per your BUG-FIX RULE you MUST also write an "
            f"executable reproduction script at {PROGRESS_DIR}/repro_{feature_id}.py "
            f"(or .sh) that fails while the bug exists and passes once fixed, and label "
            f"every root-cause claim in the spec CONFIRMED or HYPOTHESIS.\n"
        )
    refuted_context = ""
    if _refuted_evidence:
        refuted_context = (
            f"IMPORTANT: the previous spec's diagnosis for this feature was REFUTED by "
            f"direct verification. Evidence: {_refuted_evidence}\n"
            f"Do NOT reassert that premise; verify the actual behavior before asserting "
            f"where the defect is.\n"
        )
    task = (
        f"{_workdir_banner(cwd)}"
        f"{arch_context}"
        f"{layout_context}"
        f"{refuted_context}"
        f"{repro_hint}"
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
    _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] {'[green]✓ spec ready[/]' if done else '[red]✗ error[/]'} → {result[:80]}")

    # ── Bug-fix repro gate (blocking, ONE regeneration max) ──────────────────
    # A bug-fix spec with neither an executable repro script nor an explicit
    # `REPRO: NOT_FEASIBLE — <reason>` declaration is quarantined and
    # regenerated once — same quarantine mechanism as the stale-e2e-path check
    # in the cache branch above. Strict on the common path, best-effort at the
    # edge: if the second spec still has neither, we fall through to the
    # non-blocking annotation below rather than looping — the pipeline is
    # never left hanging on this gate, and _downgrade_unbacked_confirmed()
    # still guards the fallback path at implementer-injection time.
    if done and os.path.exists(spec_path) and _is_bugfix_feature(description):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                _gate_text = f.read()
        except OSError:
            _gate_text = ""
        if _bugfix_spec_missing_repro(feature_id, _gate_text):
            _quarantine_path = f"{spec_path}.norepro"
            try:
                os.replace(spec_path, _quarantine_path)
            except OSError:
                pass  # best-effort quarantine — the regeneration overwrites spec_path anyway
            _log("spec_writer", "SPEC_REPRO_GATE",
                 f"feature={feature_id}: bug-fix spec has no repro script and no "
                 f"'REPRO: NOT_FEASIBLE' declaration — quarantined to {_quarantine_path}, "
                 f"regenerating (1 retry max)", level="warning")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ bug-fix spec has no repro "
                              f"script and no NOT_FEASIBLE declaration — regenerating once[/]")
            regen_task = task + (
                f"\n\n⚠️ REPRO GATE — your previous spec for this feature was rejected and "
                f"quarantined because it contained neither an executable reproduction script "
                f"nor an explicit non-feasibility declaration. This feature is a BUG FIX: you "
                f"MUST either write {PROGRESS_DIR}/repro_{feature_id}.py (or .sh) that FAILS "
                f"while the bug exists, OR explicitly declare in the spec "
                f"`REPRO: NOT_FEASIBLE — <concrete reason>` if scripting it is genuinely not "
                f"viable (e.g. a purely visual bug, an infrastructure configuration issue). "
                f"Do NOT write a fake repro that doesn't actually exercise the bug just to "
                f"pass this gate — an honest NOT_FEASIBLE declaration is the correct choice "
                f"in that case."
            )
            _agent_ctx = _fire_transform("before_spawn_agent", role="spec_writer",
                                         system_prompt=spec_cfg.SYSTEM_PROMPT,
                                         task=regen_task, feature_id=feature_id)
            result = run_agent(_agent_ctx["system_prompt"], spec_cfg.TOOLS, _agent_ctx["task"],
                               role="spec_writer", color="cyan", max_iter=MAX_ITER_SPEC,
                               checkpoint_key=f"spec_writer_{feature_id}_2")
            done = not result.startswith("[ERROR")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] "
                              f"{'[green]✓ spec regenerated[/]' if done else '[red]✗ error[/]'} → {result[:80]}")

    # Validate the freshly generated spec against the current codebase.
    # Skipped if the spec failed to generate or the agent returned an error.
    spec_issues: list[str] = []
    if done and os.path.exists(spec_path):
        issues_text = _validate_spec(spec_path)
        if issues_text:
            spec_issues = [l for l in issues_text.splitlines() if l.strip()]
            _log("spec_writer", "SPEC_VALIDATION_ISSUES", issues_text[:300], level="warning")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ validation issues found — annotating spec[/]")
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
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [dim]✓ spec validated — no issues[/]")

    # Fallback after the repro gate is exhausted (non-blocking, same philosophy
    # as _validate_spec): if even the regenerated spec has neither a repro
    # script nor a NOT_FEASIBLE declaration, annotate and continue — never
    # leave the pipeline hanging on this gate. The annotation tells the
    # implementer to downgrade every claim to HYPOTHESIS and layer-isolate
    # first; _downgrade_unbacked_confirmed() enforces the CONFIRMED part of
    # that mechanically at injection time regardless.
    if done and os.path.exists(spec_path) and _is_bugfix_feature(description):
        try:
            with open(spec_path, "r", encoding="utf-8") as f:
                _final_text = f.read()
        except OSError:
            _final_text = ""
        if _bugfix_spec_missing_repro(feature_id, _final_text):
            _log("spec_writer", "SPEC_MISSING_REPRO",
                 f"feature={feature_id}: bug-fix spec still has no {PROGRESS_DIR}/repro_{feature_id}.py/.sh "
                 f"and no 'REPRO: NOT_FEASIBLE' declaration after regeneration — annotating spec "
                 f"and continuing", level="warning")
            _vprint("normal", f"  [cyan]📋 SPEC_WRITER[/] [yellow]⚠ regenerated spec still has no "
                              f"repro script — annotating spec and continuing[/]")
            try:
                with open(spec_path, "a", encoding="utf-8") as f:
                    f.write(
                        f"\n\n---\n"
                        f"## ⚠ Missing reproduction script\n"
                        f"This is a bug-fix feature, but no executable reproduction script was "
                        f"written to {PROGRESS_DIR}/repro_{feature_id}.py (or .sh) and no "
                        f"`REPRO: NOT_FEASIBLE` declaration was made, even after one regeneration. "
                        f"Implementer: treat every root-cause claim above as HYPOTHESIS regardless "
                        f"of how it is phrased, and apply your LAYER ISOLATION rule (direct "
                        f"curl/httpx + DB check) before trusting the spec's stated location of "
                        f"the bug.\n"
                    )
            except Exception:
                pass

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
            f"- Read the implementer report at {PROGRESS_DIR}/impl_{{fid}}.md\n"
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
        max_iter = int(os.getenv("MAX_ITER_REVIEWER_LITE", "15"))  # lightweight review — doesn't need more, override via .env
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
        f"The implementer report is at {PROGRESS_DIR}/impl_{feature_id}.md\n"
        f"Write your verdict to {PROGRESS_DIR}/review_{feature_id}.md\n"
        f"Return ONLY: '{VERDICT_APPROVED}' or '{VERDICT_REJECTED}: <reason>'"
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="reviewer",
                                 system_prompt=reviewer_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], reviewer_cfg.TOOLS, _agent_ctx["task"],
                       role="reviewer", color="magenta", max_iter=max_iter,
                       checkpoint_key=f"reviewer_{feature_id}_{attempt}")

    approved = _verdict_is(result, VERDICT_APPROVED)
    verdict_color = "green" if approved else "red"
    verdict_icon  = "✅" if approved else "❌"
    _log("reviewer", "VERDICT", result[:200], level="info" if approved else "warning")
    _vprint("normal", f"  [magenta]🔍 REVIEWER[/] [{verdict_color}]{verdict_icon} {result[:100]}[/]")
    return result


def _e2e_retry_evidence_block(feature_id: int) -> str:
    """
    Build the retry-evidence block injected into spawn_e2e_tester's task on
    attempt > 1 — the E2E-side counterpart to spawn_implementer's own
    RETRY #{attempt} block (_extract_retry_context()). Must be called BEFORE
    spawn_e2e_tester's own stale-report cleanup deletes progress/e2e_<id>.json
    — at that point in the call, whatever is on disk is guaranteed to be the
    PREVIOUS attempt's (real or harness-synthesized by the max_iter fallback
    above), which is exactly the evidence a retry needs.

    Two deterministic, bounded sources:
      1. The previous attempt's e2e_<id>.json "reason" field.
      2. This retry's own progress/impl_<id>.md — already regenerated by the
         implementer in response to that failure — as files_touched (from
         the structured sibling .json, most reliable) plus a bounded tail of
         the prose report (design decisions, written last per the
         implementer's own PROTOCOL step 5).

    Returns "" if neither source has anything (never blocks task-building).
    """
    prev_status = _read_structured_status(f"{PROGRESS_DIR}/e2e_{feature_id}.json")
    prev_reason = (prev_status or {}).get("reason") or ""

    impl_path = f"{PROGRESS_DIR}/impl_{feature_id}.md"
    impl_status = _read_structured_status(impl_path)
    files_touched = (impl_status or {}).get("files_touched") or []
    impl_tail = ""
    if os.path.exists(impl_path):
        try:
            with open(impl_path, "r", encoding="utf-8") as f:
                impl_tail = f.read()[-1200:]
        except OSError:
            pass

    if not prev_reason and not files_touched and not impl_tail:
        return ""

    files_line = ", ".join(files_touched) if files_touched else "(not recorded)"
    return (
        f"\n\n⚠️ PREVIOUS E2E ATTEMPT FAILED — evidence:\n{prev_reason or '(no evidence captured)'}\n\n"
        f"WHAT THE IMPLEMENTER CHANGED IN RESPONSE:\n"
        f"Files touched: {files_line}\n"
        f"{impl_tail}\n\n"
        f"Start by re-running the exact failing test — do not re-derive context the evidence above already gives you."
    )


def spawn_e2e_tester(feature_id: int, attempt: int = 1) -> str:
    _phase_header("e2e_tester", "Tests E2E", feature_id)
    _log("e2e_tester", "SPAWN", f"feature={feature_id} attempt={attempt}")

    # On a retry, capture the previous attempt's evidence BEFORE the cleanup
    # below deletes it. Real incident, feature #74: attempt 2's log shows the
    # same 3 files re-read and the same test re-run 3 times before hitting
    # max_iter again — CONVERGENCE_RULE already tells the agent to apply an
    # injected diagnosis directly instead of re-deriving it, but nothing on
    # the E2E side was actually injecting one; this is that missing feeder.
    _retry_evidence = _e2e_retry_evidence_block(feature_id) if attempt > 1 else ""

    # Clear any e2e report left over from a DIFFERENT attempt/spawn. The
    # report filename carries no attempt number (always progress/e2e_<id>.md
    # + .json), so if this attempt is cut short by max_iter before writing
    # its own report, the file left on disk is whatever an earlier attempt
    # wrote — possibly hours before, describing a cause that no longer
    # applies to the current code. _e2e_verdict() (above) prefers that JSON
    # over the "[ERROR: max_iter reached]" string run_agent actually
    # returned, so a stale file isn't just a misleading log — it can flip
    # the real pass/fail verdict (e.g. a prior attempt's "status": "passed"
    # silently reused for an attempt that was never actually verified).
    # Real incident, feature #71: the Leader's summary cited a stale
    # payload-mismatch cause from an e2e_71.md written ~2 hours earlier by a
    # different spawn.
    #
    # Guarded by the resume check below: if the harness process itself
    # crashed mid-attempt (not a clean max_iter exhaustion — see run_agent,
    # which clears its own message-state checkpoint on every clean return,
    # verdict or max_iter), _load_message_state returns non-None only for
    # that in-flight same-attempt resume. In that case the model's resumed
    # conversation history may reference a partial report it already wrote
    # this attempt, so skip the delete — only a fresh (non-resuming) spawn,
    # which can only see a report from some other attempt, clears it.
    _checkpoint_key = f"e2e_tester_{feature_id}_{attempt}"
    if _load_message_state(_checkpoint_key) is None:
        for stale in (f"{PROGRESS_DIR}/e2e_{feature_id}.md", f"{PROGRESS_DIR}/e2e_{feature_id}.json"):
            if os.path.exists(stale):
                os.remove(stale)

    cwd = os.getcwd()
    arch_context = _load_project_architecture(cwd)
    layout_context = _layout_context()
    task = (
        f"{_workdir_banner(cwd)}"
        f"{arch_context}"
        f"{layout_context}"
        f"Run E2E tests for feature #{feature_id}.\n"
        f"The implementer report is at {PROGRESS_DIR}/impl_{feature_id}.md\n"
        f"Write your report to {PROGRESS_DIR}/e2e_{feature_id}.md\n"
        f"Return ONLY: '{VERDICT_E2E_PASSED}' or '{VERDICT_E2E_FAILED}: <reason>'"
        f"{_retry_evidence}"
    )
    _agent_ctx = _fire_transform("before_spawn_agent", role="e2e_tester",
                                 system_prompt=e2e_cfg.SYSTEM_PROMPT,
                                 task=task, feature_id=feature_id)
    result = run_agent(_agent_ctx["system_prompt"], e2e_cfg.TOOLS, _agent_ctx["task"],
                       role="e2e_tester", color="yellow",
                       checkpoint_key=_checkpoint_key, feature_id=feature_id)

    # Defensive fallback: if this call ended in the generic max_iter error
    # and never got to write the structured .json, but DID leave a fresh
    # .md report on disk (guaranteed fresh — see the cleanup at the top of
    # this function), pull the real diagnosis out of that .md instead of
    # discarding it in favor of a handful of unrelated tool-call errors.
    # Real incident, feature #71: the report correctly named a backend 500
    # in list_professionals (found via page.request), but the agent ran out
    # of iterations before writing e2e_71.json — without this fallback the
    # next implementer attempt and the failure-diagnostician only ever see
    # "[ERROR: max_iter 50 reached]\nRecent tool-call errors: ...".
    md_path = f"{PROGRESS_DIR}/e2e_{feature_id}.md"
    json_path = f"{PROGRESS_DIR}/e2e_{feature_id}.json"
    if result.startswith(("[ERROR: max_iter", "[ERROR: attempt aborted")) \
            and not os.path.exists(json_path) and os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                report_text = f.read()
            verdict_idx = report_text.rfind("Verdict:")
            if verdict_idx != -1:
                verdict_section = report_text[verdict_idx:].strip()
                result = f"{VERDICT_E2E_FAILED}: (recovered from e2e_{feature_id}.md after max_iter)\n{verdict_section}"
                _log("e2e_tester", "MAX_ITER_REPORT_RECOVERED",
                     f"feature={feature_id} — used e2e_{feature_id}.md's Verdict section "
                     f"instead of the generic max_iter message")
        except OSError:
            pass

    passed = _verdict_is(result, VERDICT_E2E_PASSED)
    color  = "green" if passed else "red"
    _log("e2e_tester", "VERDICT", result[:200], level="info" if passed else "warning")
    _vprint("normal", Panel(
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

    Sets _CURRENT_FEATURE_ID for the duration of the cycle so every log line
    emitted while this feature is being processed — including from nested
    agent calls — carries feature_id in the structured JSON log on stdout.
    """
    _feature_id_token = _CURRENT_FEATURE_ID.set(feature_id)
    try:
        return _run_feature_cycle_impl(feature_id, description, e2e)
    finally:
        _CURRENT_FEATURE_ID.reset(_feature_id_token)


def _run_feature_cycle_impl(feature_id: int, description: str, e2e: bool = True) -> dict:
    # ── Lifecycle hook ───────────────────────────────────────────────────────
    _fire("before_feature", feature_id=feature_id, description=description, e2e=e2e)

    # summary-tier: always shown, even at HARNESS_VERBOSITY=summary — this is
    # the "a feature started" signal that tier is meant to preserve.
    console.print(f"[bold]▶ Feature #{feature_id}[/] {description[:80]}")

    # ── Budget guard ─────────────────────────────────────────────────────────
    if _BUDGET_EXCEEDED:
        msg = f"[BUDGET_EXCEEDED] Feature #{feature_id} skipped — session budget of USD {COST_BUDGET_USD:.2f} was reached."
        _log("harness", "BUDGET_SKIP", msg, level="warning")
        console.print(f"  [yellow]⚠ skipping feature #{feature_id} — budget exhausted[/]")
        return {"approved": False, "attempts": 0, "final_verdict": msg}

    # ── Dependency gate (mandatory, code-level — do not rely on the Leader's
    # own judgment) ───────────────────────────────────────────────────────
    # Real incident: the Leader started feature #72 via run_feature_cycle
    # while feature #71 (a hard dependency) had status "failed", not "done".
    # The Leader is told the correct execution order in its injected context
    # ("Do not start a feature until all its depends_on features are done"),
    # but that's a prose instruction, not an enforced one — a weaker model
    # (or one under pressure after repeated failures) can and did ignore it.
    _all_features = _read_feature_list_raw()  # best-effort — returns [] on any read/parse error
    _this_feature = next((f for f in _all_features if f["id"] == feature_id), None)
    if _this_feature:
        _id_to_status = {f["id"]: f.get("status") for f in _all_features}
        _unmet = [dep for dep in _this_feature.get("depends_on", [])
                  if _id_to_status.get(dep) != "done"]
        if _unmet:
            msg = (f"[DEPENDENCY_ERROR] Feature #{feature_id} cannot start — "
                   f"depends_on {_unmet} not yet 'done' "
                   f"(status: {[_id_to_status.get(d) for d in _unmet]}).")
            _log("harness", "DEPENDENCY_BLOCKED", msg, level="error")
            console.print(f"  [bold red]⚠ {msg}[/]")
            return {"approved": False, "attempts": 0, "final_verdict": msg}

    # ── Resumability: load checkpoint from a previous (crashed) run ──────────
    _ckpt = _load_checkpoint(feature_id)
    if _ckpt:
        _ckpt_step    = _ckpt.get("step", "")
        _ckpt_attempt = int(_ckpt.get("attempt", 1))
        _vprint(
            "normal",
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
    if _ckpt_step in (CKPT_SPEC_DONE, CKPT_IMPL_DONE, CKPT_REVIEW_DONE, CKPT_E2E_DONE):
        spec_path = f"{PROGRESS_DIR}/spec_{feature_id}.md"
        _vprint("normal", f"  [dim]↺ skipping spec (already done)[/]")
    else:
        spec_result = spawn_spec_writer(feature_id, description)
        spec_path = spec_result.strip() if not spec_result.startswith("[ERROR") else None
        _save_checkpoint(feature_id, CKPT_SPEC_DONE, attempt=1)

    rejection_reason = ""
    # Resume from the attempt that was in progress when the crash happened.
    start_attempt = (
        _ckpt_attempt if _ckpt_step in (CKPT_IMPL_DONE, CKPT_REVIEW_DONE, CKPT_E2E_DONE) else 1
    )

    for attempt in range(start_attempt, MAX_RETRIES_REVIEW + 1):

        # ── Per-feature budget guard ─────────────────────────────────────────
        # COST_BUDGET_USD is global — one pathological feature (e.g. one that
        # burns 2 full 50-iteration E2E cycles) can consume the entire
        # session's budget with no per-feature mechanism ever noticing.
        # Checked at the top of each attempt (not just once, the way the
        # session guard is checked at the top of the whole cycle) so a
        # feature that blows the budget mid-retry is cut immediately instead
        # of paying for one more full impl->review->E2E attempt first.
        if FEATURE_BUDGET_USD > 0 and _feature_cost_usd(feature_id) >= FEATURE_BUDGET_USD:
            completed_attempts = attempt - 1
            final_verdict = (
                f"[FEATURE_BUDGET_EXCEEDED] Feature #{feature_id} stopped after {completed_attempts} "
                f"attempt(s) — spent USD {_feature_cost_usd(feature_id):.4f} >= per-feature limit "
                f"USD {FEATURE_BUDGET_USD:.2f}."
            )
            _log("harness", "FEATURE_BUDGET_EXCEEDED", final_verdict, level="warning")
            console.print(f"  [yellow]⚠ {final_verdict}[/]")
            _clear_checkpoint(feature_id)
            _fire("after_feature_failed", feature_id=feature_id, description=description,
                  attempts=completed_attempts, final_verdict=final_verdict)
            return {"approved": False, "attempts": completed_attempts, "final_verdict": final_verdict}

        # ── Step 2: Implement ─────────────────────────────────────────────────
        # Skip if impl was already done for this attempt in a previous run.
        _skip_impl = (
            _ckpt_step in (CKPT_IMPL_DONE, CKPT_REVIEW_DONE, CKPT_E2E_DONE)
            and _ckpt_attempt == attempt
        )
        if _skip_impl:
            _vprint("normal", f"  [dim]↺ skipping impl attempt {attempt} (already done)[/]")
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
                    console.print(f"[red]❌ Feature #{feature_id} failed[/]: {impl_result[:100]}")
                    return {"approved": False, "attempts": attempt, "final_verdict": impl_result}
                rejection_reason = impl_result
                continue
            _save_checkpoint(feature_id, CKPT_IMPL_DONE, attempt=attempt)

        # ── Step 3: Review ────────────────────────────────────────────────────
        # Moved ahead of E2E (was step 4) — ARCHITECTURE_REVIEW §8.C: E2E is
        # by far the most expensive step in the cycle (force-recreate + cold
        # compile + browser), but used to run BEFORE this cheap, purely-static
        # check — every ordinary reviewer rejection wasted a full Playwright
        # cycle it never needed. Skip if review already approved this attempt
        # in a previous run.
        _skip_review = (
            _ckpt_step in (CKPT_REVIEW_DONE, CKPT_E2E_DONE)
            and _ckpt_attempt == attempt
        )
        if _skip_review:
            _vprint("normal", f"  [dim]↺ skipping review attempt {attempt} (already done)[/]")
            approved, review_result = True, VERDICT_APPROVED
        else:
            # _reviewer_verdict prefers the structured progress/review_<id>.json
            # written alongside the report, falling back to parsing the returned
            # chat string (_verdict_is + stripping "REJECTED:") when absent.
            review_result = spawn_reviewer(feature_id, e2e=e2e, attempt=attempt)
            approved, rejection_reason = _reviewer_verdict(review_result, f"{PROGRESS_DIR}/review_{feature_id}.md")

        if not approved:
            # rejection_reason was already set above by _reviewer_verdict()
            _log("harness", "CYCLE_RETRY",
                 f"feature={feature_id} attempt={attempt}/{MAX_RETRIES_REVIEW} reason={rejection_reason[:100]}",
                 level="warning")
            _fire("after_reviewer_rejected",
                  feature_id=feature_id, description=description,
                  attempt=attempt, max_attempts=MAX_RETRIES_REVIEW,
                  rejection_reason=rejection_reason)
            if attempt < MAX_RETRIES_REVIEW:
                _vprint("normal", Panel(
                    f"[yellow]Reviewer rejected — retry {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n"
                    f"[dim]{rejection_reason[:200]}[/]",
                    title=f"[yellow]↻ impl→review cycle — feature #{feature_id}[/]",
                    border_style="yellow", padding=(0, 1)
                ))
            continue
        _save_checkpoint(feature_id, CKPT_REVIEW_DONE, attempt=attempt)

        # ── Step 4: E2E Testing ───────────────────────────────────────────────
        # Now runs only after the cheap review check has already approved —
        # a rejected review never pays for a Playwright cycle. Skip if e2e
        # was already done for this attempt in a previous run.
        _skip_e2e = (
            _ckpt_step == CKPT_E2E_DONE
            and _ckpt_attempt == attempt
        )
        if not e2e:
            e2e_result = VERDICT_E2E_PASSED  # not applicable — skip silently
        elif _skip_e2e:
            _vprint("normal", f"  [dim]↺ skipping e2e attempt {attempt} (already done)[/]")
            e2e_result = VERDICT_E2E_PASSED
        else:
            e2e_result = spawn_e2e_tester(feature_id, attempt=attempt)

        # Allowlist, not denylist: only an explicit "E2E_PASSED" counts as a
        # pass. Anything else — "E2E_FAILED: ...", a max_iter timeout error,
        # malformed/empty output, etc. — is treated as a failure. Previously
        # this only rejected strings starting with "E2E_FAILED", so a timeout
        # or any other unexpected verdict silently fell through as an
        # implicit pass. _e2e_verdict prefers the structured
        # progress/e2e_<id>.json written alongside the report, falling back
        # to this same string parsing.
        e2e_passed, e2e_reason = _e2e_verdict(e2e_result, f"{PROGRESS_DIR}/e2e_{feature_id}.md")
        if not e2e_passed:
            _log("harness", "E2E_FAILED",
                 f"feature={feature_id} attempt={attempt} reason={e2e_reason[:100]}", level="warning")
            # E2E failure counts as rejection — implementer fixes it on the
            # next attempt, which re-pays review too (a code change made in
            # response to an E2E failure can itself introduce something
            # review would have caught, so re-running it is deliberate).
            rejection_reason = f"E2E failed: {e2e_reason}"
            if attempt < MAX_RETRIES_REVIEW:
                _vprint("normal", Panel(
                    f"[red]E2E failed — retrying impl (attempt {attempt+1}/{MAX_RETRIES_REVIEW})[/]\n"
                    f"[dim]{e2e_reason[:200]}[/]",
                    title=f"[red]↻ E2E → impl — feature #{feature_id}[/]",
                    border_style="red", padding=(0, 1)
                ))
            continue
        _save_checkpoint(feature_id, CKPT_E2E_DONE, attempt=attempt)

        # ── Gate: finalize only after BOTH review and E2E have passed ─────────
        # Moved from immediately after review (its old position, step 4) to
        # here — so a governance plugin (e.g. the premium Human-in-the-loop
        # gates module) never finalizes a feature whose E2E never ran.
        gate_block = _fire_gate(
            "before_approval_finalized",
            feature_id=feature_id, description=description,
            attempt=attempt, review_result=review_result,
        )
        if not gate_block:
            # Flip the status to "done" HERE, before after_feature_approved
            # fires: sdlc_governance's hook synchronously snapshots the
            # working tree onto the feature branch (and then checks the base
            # branch back out), while the Leader's own update_feature_status
            # tool call only lands a full LLM turn later — so the branch/PR
            # captured the stale status and the late flip sat uncommitted on
            # the base branch, discarded by the next feature's checkout.
            # Same for /auto's post-cycle _set_feature_status(fid, "done").
            # Both callers' later writes become harmless no-ops; the Leader
            # tool still owns "failed" and every other status. Done before
            # _clear_checkpoint so a crash in between can't resurrect an
            # already-shipped feature as stale (recovery skips "done").
            _set_feature_status(feature_id, "done")
            _clear_checkpoint(feature_id)
            _fire("after_feature_approved",
                  feature_id=feature_id, description=description, attempts=attempt)
            console.print(f"[green]✅ Feature #{feature_id} approved[/] ({attempt} attempt(s))")
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
            _vprint("normal", Panel(
                f"[red]Approval blocked by a governance plugin — retry {attempt+1}/{MAX_RETRIES_REVIEW}[/]\n"
                f"[dim]{rejection_reason[:200]}[/]",
                title=f"[red]🚧 gate vetoed verdict — feature #{feature_id}[/]",
                border_style="red", padding=(0, 1)
            ))

    final_verdict = f"{VERDICT_REJECTED} after {MAX_RETRIES_REVIEW} attempts: {rejection_reason}"
    _clear_checkpoint(feature_id)
    _fire("after_feature_failed",
          feature_id=feature_id, description=description,
          attempts=MAX_RETRIES_REVIEW, final_verdict=final_verdict)
    console.print(f"[red]❌ Feature #{feature_id} failed[/]: {final_verdict[:100]}")
    return {"approved": False, "attempts": MAX_RETRIES_REVIEW, "final_verdict": final_verdict}


# ─── DETERMINISTIC BATCH DRIVER (/auto) ──────────────────────────────────────
#
# run_all_pending() is a code-level alternative to the Leader-LLM for the one
# thing it does most often: process every "pending" feature in dependency
# order. It does not replace run_leader() — natural-language requests
# ("only run feature 3 and the ones after it", ad-hoc questions about state)
# still go through the Leader-LLM via the REPL's default input path. This is
# a parallel, zero-inference path for the common case of "run everything
# that's ready," which an LLM orchestrator executes slower, more expensively,
# and under a hard MAX_ITER_LEADER ceiling — a pending batch larger than that
# budget forces a human re-prompt mid-run today, which works against
# unattended/autonomous processing. The Leader's own two real incidents this
# session (starting a feature whose dependency had failed, editing files
# outside its role) already forced code-level guards — _run_feature_cycle_impl's
# dependency gate (v1.37.0) and execute_tool's Leader write confinement
# (v1.36.0) — both of which this driver benefits from for free, since it
# calls the exact same run_feature_cycle() the Leader calls.

def _set_feature_status(feature_id: int, status: str) -> None:
    """
    Directly update a feature's status + updated_at in feature_list.json —
    same shape tools.update_feature_status() writes when an LLM calls it as
    a tool, but invoked straight from harness-internal code (no tool-call
    dispatch, no LLM involved). Best-effort: a missing/malformed
    feature_list.json is silently a no-op, same discipline as
    _read_feature_list_raw()/_write_feature_list_raw().
    """
    features = _read_feature_list_raw()
    for feat in features:
        if feat.get("id") == feature_id:
            feat["status"] = status
            feat["updated_at"] = datetime.datetime.now().isoformat()
            break
    _write_feature_list_raw(features)


def _write_auto_current_md(feature_id: int, title: str, description: str) -> None:
    """
    Overwrite progress/current.md with a fixed template — the same fields
    agents/leader.py's own PROTOCOL step 2b instructs the Leader-LLM to
    compose freely by hand (chosen feature, timestamp, brief plan).
    """
    try:
        os.makedirs(PROGRESS_DIR, exist_ok=True)
        with open(f"{PROGRESS_DIR}/current.md", "w", encoding="utf-8") as f:
            f.write(
                f"# Current status\n\n"
                f"**Feature:** #{feature_id} — {title}\n"
                f"**Started:** {datetime.datetime.now().isoformat()}\n"
                f"**Plan:** {description[:300]}\n"
            )
    except OSError as exc:
        _log("harness", "AUTO_CURRENT_MD_WRITE_ERROR", f"feature={feature_id}: {exc}", level="warning")


def _append_auto_history_md(feature_id: int, title: str, approved: bool,
                             attempts: int, final_verdict: str) -> None:
    """
    Append to progress/history.md with a fixed template — the same fields
    agents/leader.py's own PROTOCOL step 2d instructs the Leader-LLM to
    compose freely by hand once run_feature_cycle returns.
    """
    status_label = "✅ done" if approved else "❌ failed"
    try:
        os.makedirs(PROGRESS_DIR, exist_ok=True)
        with open(f"{PROGRESS_DIR}/history.md", "a", encoding="utf-8") as f:
            f.write(
                f"\n## Feature #{feature_id} — {title}\n"
                f"- Status: {status_label}\n"
                f"- Attempts: {attempts}\n"
                f"- Timestamp: {datetime.datetime.now().isoformat()}\n"
                f"- Verdict: {final_verdict[:500]}\n"
            )
    except OSError as exc:
        _log("harness", "AUTO_HISTORY_MD_WRITE_ERROR", f"feature={feature_id}: {exc}", level="warning")


def run_all_pending(only_feature_id: Optional[int] = None) -> dict:
    """
    Deterministic, code-level driver: reads feature_list.json, orders every
    "pending" feature via _topological_sort (full-graph dependency order,
    then filtered down to the pending subset — so a pending feature is only
    scheduled after everything it transitively depends on, whatever those
    dependencies' own status), and calls run_feature_cycle() for each in
    turn — updating status and progress/current.md / progress/history.md
    the same way the Leader's own PROTOCOL does, with no LLM in the loop for
    the orchestration itself.

    If only_feature_id is given, runs just that one feature (it must
    currently be "pending" — same contract as the rest of this function)
    instead of the whole queue.

    Stops when:
      - the pending queue (after dependency ordering / only_feature_id
        filtering) is empty — "empty".
      - the session budget is exhausted (_BUDGET_EXCEEDED, checked BEFORE
        each feature) — "budget_exceeded". The about-to-run feature is left
        "pending" (never marked "in_progress"/"failed"), so a future session
        picks it up rather than losing it to a spurious failure.
      - feature_list.json has structural dependency errors (self-dep,
        missing dep, cycle) — "dependency_errors". Same check
        _validate_dependencies() already runs at startup, re-run here since
        /auto can be invoked mid-session after the file changes.
      - only_feature_id was given and that one feature finished —
        "single_feature_done".
    A human-in-the-loop gate, if a premium plugin registers one on
    before_approval_finalized, already intercepts inside run_feature_cycle
    itself — nothing extra needed here.

    Returns {"results": [{"feature_id", "title", "approved", "attempts",
    "final_verdict"}, ...], "stopped_reason": str}.
    """
    features = _read_feature_list_raw()
    if not features:
        console.print("[yellow]No feature_list.json found or it's empty — nothing to run.[/]")
        return {"results": [], "stopped_reason": "empty"}

    dep_errors = _validate_dependencies(features)
    if dep_errors:
        console.print(Panel(
            "\n".join(f"[red]• {e}[/]" for e in dep_errors),
            title="[red]⚠ Dependency graph errors — fix feature_list.json before running /auto[/]",
            border_style="red", padding=(0, 1)
        ))
        return {"results": [], "stopped_reason": "dependency_errors"}

    ordered, _ = _topological_sort(features)
    id_to_feature = {f["id"]: f for f in features}
    queue = [fid for fid in ordered if id_to_feature[fid].get("status") == "pending"]

    if only_feature_id is not None:
        if only_feature_id not in id_to_feature:
            console.print(f"[red]Feature #{only_feature_id} not found in feature_list.json.[/]")
            return {"results": [], "stopped_reason": "not_found"}
        if id_to_feature[only_feature_id].get("status") != "pending":
            console.print(
                f"[yellow]Feature #{only_feature_id} is not 'pending' "
                f"(status={id_to_feature[only_feature_id].get('status')!r}) — nothing to run.[/]"
            )
            return {"results": [], "stopped_reason": "not_pending"}
        queue = [only_feature_id]

    if not queue:
        console.print("[dim]No pending features to run.[/]")
        return {"results": [], "stopped_reason": "empty"}

    console.print(
        f"  [bold]▶ /auto[/] processing {len(queue)} pending feature(s) in dependency order: "
        + " → ".join(f"#{fid}" for fid in queue)
    )

    results: list = []
    stopped_reason = "empty"
    for fid in queue:
        if _BUDGET_EXCEEDED:
            console.print(f"  [yellow]⚠ session budget exhausted — stopping before feature #{fid}[/]")
            stopped_reason = "budget_exceeded"
            break

        feat = id_to_feature[fid]
        title = feat.get("title", f"#{fid}")
        description = feat.get("description", "")
        e2e = bool(feat.get("e2e", False))

        _set_feature_status(fid, "in_progress")
        _write_auto_current_md(fid, title, description)

        cycle_result = run_feature_cycle(fid, description, e2e=e2e)
        approved = bool(cycle_result.get("approved"))
        attempts = cycle_result.get("attempts", 0)
        final_verdict = cycle_result.get("final_verdict", "")

        _set_feature_status(fid, "done" if approved else "failed")
        _append_auto_history_md(fid, title, approved, attempts, final_verdict)

        results.append({
            "feature_id": fid, "title": title, "approved": approved,
            "attempts": attempts, "final_verdict": final_verdict,
        })

        if only_feature_id is not None:
            stopped_reason = "single_feature_done"
            break
    else:
        stopped_reason = "empty"  # queue exhausted naturally, no break hit

    approved_count = sum(1 for r in results if r["approved"])
    console.print(
        f"  [bold]✅ /auto finished[/] — {approved_count} approved, "
        f"{len(results) - approved_count} failed "
        f"({len(results)} processed, stopped: {stopped_reason})"
    )
    return {"results": results, "stopped_reason": stopped_reason}


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
        with open(FEATURE_LIST_PATH, "r", encoding="utf-8") as f:
            features = json.load(f)
        features_json = json.dumps(features, indent=2, ensure_ascii=False)
    except Exception as e:
        features_json = f"(not available: {e})"

    try:
        with open(f"{PROGRESS_DIR}/current.md", "r", encoding="utf-8") as f:
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
        f"## {FEATURE_LIST_PATH} (current state)\n```json\n{features_json}\n```\n"
        f"{dep_section}\n"
        f"## {PROGRESS_DIR}/current.md\n{current_md}\n\n"
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

        _track_usage("leader", api_response.usage, getattr(api_response, "model", None))
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

            if _verbosity_at_least("verbose") and fn_name != "run_feature_cycle":
                args_preview = json.dumps(fn_args, ensure_ascii=False)[:200]
                console.print(Panel(
                    f"[bold]Action:[/]  [cyan]{fn_name}[/]\n[dim]{args_preview}[/]",
                    title=f"[green]leader — {fn_name}[/] iter {iteration+1}",
                    border_style="green",
                    padding=(0, 1)
                ))

            _log("leader", "TOOL_CALL", f"{fn_name}({json.dumps(fn_args, ensure_ascii=False)[:100]})")

            if fn_name == "run_feature_cycle":
                # run_feature_cycle orchestrates 4 sub-agents (spec/impl/e2e/
                # review) plus checkpoint I/O — by far the largest surface
                # area of any single call in the leader loop. Unlike the
                # generic execute_tool() path (guarded at its own dispatch
                # choke point), this is a special-cased direct call, so an
                # uncaught exception here would propagate straight out of
                # run_leader and kill the entire session — every other
                # pending feature included, not just this one. Guard it the
                # same way: report the failure as this feature's result
                # (same return shape run_feature_cycle already uses) so the
                # leader can react and move on instead of the run dying.
                try:
                    cycle_result = run_feature_cycle(**fn_args)
                except Exception as e:
                    _log("harness", "FEATURE_CYCLE_CRASH",
                         f"feature={fn_args.get('feature_id')}: {e}", level="error")
                    cycle_result = {
                        "approved": False,
                        "attempts": 0,
                        "final_verdict": f"[ERROR] run_feature_cycle raised an unhandled exception: {e}",
                    }
                result = json.dumps(cycle_result, ensure_ascii=False)
            else:
                result = _redact(execute_tool(fn_name, fn_args, role="leader"))
                if _verbosity_at_least("verbose"):
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
    with open(FEATURE_LIST_PATH, "r") as f:
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
    global HARNESS_VERBOSITY  # /verbosity REPL command mutates this below

    # Verify and install dependencies before showing any UI
    _ensure_deps()

    # Load plugins before the banner so hooks are registered before anything runs
    _load_plugins()

    console.rule("Vora Engine", style="white")
    orch_label   = "[cyan]Prefect[/]" if ORCHESTRATOR == "prefect" else "[dim]local[/]"
    budget_label = f"[yellow]USD {COST_BUDGET_USD:.2f} limit[/]" if COST_BUDGET_USD > 0 else "[dim]no limit[/]"
    console.print(
        f"  Model: [cyan]{MODEL}[/]  |  Orchestrator: {orch_label}  |  Budget: {budget_label}  |  Verbosity: [cyan]{HARNESS_VERBOSITY}[/]\n"
        f"  Flow: [green]👑 Leader[/] → [cyan]📋 Spec[/] → [blue]🔨 Impl[/] → [magenta]🔍 Reviewer[/] → [yellow]🧪 E2E[/]\n"
        f"  [dim]Commands: /quit | /status | /features | /costs | /budget | /verbosity | /auto [id][/]"
    )
    console.rule(style="dim")

    # Checkpointing: recover features stuck from previous sessions
    recover_stale_features()

    # Validate feature_list.json against FeatureSchema and the dependency graph
    # on startup, and warn immediately if either is broken. Schema errors are
    # checked first since a missing "id" field would otherwise crash the
    # dependency-graph check below.
    try:
        with open(FEATURE_LIST_PATH, "r", encoding="utf-8") as f:
            _startup_features = json.load(f)

        _schema_errors = _validate_feature_schema(_startup_features)
        if _schema_errors:
            for _err in _schema_errors:
                _log("harness", "SCHEMA_ERROR", _err, level="error")
            console.print(Panel(
                "\n".join(f"[red]• {e}[/]" for e in _schema_errors),
                title=f"[red]⚠ feature_list.json schema errors (v{FEATURE_SCHEMA_VERSION}) — fix before running[/]",
                border_style="red",
                padding=(0, 1)
            ))

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
                with open(f"{PROGRESS_DIR}/current.md", "r") as f:
                    console.print(Markdown(f.read()))
                continue
            elif user_input == "/features":
                print_features()
                continue
            elif user_input in ("/costs", "/costos"):
                _write_session_costs()
                _print_per_feature_costs()
                continue
            elif user_input == "/budget":
                current_usd = _session_total_cost_usd()
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
            elif user_input.startswith("/verbosity"):
                arg = user_input[len("/verbosity"):].strip().lower()
                if not arg:
                    console.print(f"  Verbosity: [cyan]{HARNESS_VERBOSITY}[/]")
                elif arg in _VERBOSITY_LEVELS:
                    HARNESS_VERBOSITY = arg
                    console.print(f"  Verbosity set to [cyan]{HARNESS_VERBOSITY}[/]")
                else:
                    console.print(
                        f"  [red]Unknown level '{arg}'[/] — choose one of: {', '.join(_VERBOSITY_LEVELS)}"
                    )
                continue
            elif user_input.startswith("/cache"):
                arg = user_input[len("/cache"):].strip().lower()
                if arg == "clear":
                    removed = _clear_llm_cache()
                    console.print(f"  [green]LLM cache cleared[/] — {removed} entr{'y' if removed == 1 else 'ies'} removed")
                else:
                    status = "[green]enabled[/]" if LLM_CACHE_ENABLED else "[dim]disabled[/]"
                    console.print(
                        f"  Cache: {status}  |  Entries on disk: [cyan]{_llm_cache_entry_count()}[/]  |  "
                        f"Hits this session: [cyan]{_session_cache_hits_total()}[/]  |  "
                        f"Est. savings: [yellow]USD {_session_cache_savings_usd():.4f}[/]\n"
                        f"  [dim]Use '/cache clear' to delete all entries. Enable with LLM_CACHE_ENABLED=true in .env.[/]"
                    )
                continue
            elif user_input == "/auto":
                run_all_pending()
                continue
            elif user_input.startswith("/auto "):
                arg = user_input[len("/auto "):].strip()
                if arg.isdigit():
                    run_all_pending(only_feature_id=int(arg))
                else:
                    console.print(f"  [red]Usage: /auto or /auto <feature_id>[/]")
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