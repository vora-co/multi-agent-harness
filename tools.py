import os, json, subprocess, datetime, re, sys

# ─── SECURITY ────────────────────────────────────────────────────────────────

# Directories where agents are allowed to write (relative to project CWD).
# Single source of truth: stack_layout.resolve_layout(), which derives this
# from stack_config.json + stack_profiles.json for the active stack (falling
# back to a hardcoded default if those files are missing). The SAFE_WRITE_DIRS
# env var still works as a highest-precedence emergency override — that logic
# lives inside resolve_layout() itself, not here.
from stack_layout import resolve_layout
SAFE_WRITE_DIRS = resolve_layout()["safe_write_dirs"]

# Blocked bash command patterns — prevents accidental destruction
BLOCKED_BASH_PATTERNS = [
    r"rm\s+-rf\s+/",          # rm -rf /
    r"rm\s+-rf\s+\.\.",       # rm -rf ..
    r">\s*/dev/sd",           # overwrite disk
    r"mkfs",                  # format partition
    r"dd\s+if=",              # raw disk copy
    r"chmod\s+-R\s+777\s+/",  # global permissions
    r":()\{.*\};:",           # fork bomb
]

# Valid feature statuses
VALID_FEATURE_STATUSES = {"pending", "in_progress", "done", "failed"}

# Path to the feature list, single source of truth so harness.py and tools.py
# never drift if this filename ever changes.
FEATURE_LIST_PATH = "feature_list.json"

# Root directory for per-feature agent reports (impl_N.md, spec_N.md, ...).
PROGRESS_DIR = "progress"

# Agent role names — the harness's MODEL_BY_ROLE, _SESSION_COSTS, and
# _AGENT_STYLES dicts are all keyed by these and asserted to match at import
# time (see harness.py), so a typo in a dict's keys fails loudly instead of
# silently misattributing cost/style tracking to the wrong role.
ROLES = ("leader", "spec_writer", "implementer", "reviewer", "e2e_tester")

# Structured "status" field values written to progress/<stage>_<id>.json,
# read back by harness.py's _reviewer_verdict()/_e2e_verdict(). Kept as
# constants and interpolated into both the harness's comparisons and the
# reviewer/e2e_tester SYSTEM_PROMPTs so the two can never drift apart.
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"

# Version of the progress/<stage>_<id>.json shape written by the 4 spawnable
# agents (spec_writer/implementer/reviewer/e2e_tester) — see harness.py's
# "STRUCTURED AGENT STATUS" section (AgentStatusSchema, _read_structured_status)
# for the reader/validator this guards. Lives here, not in harness.py, for the
# same reason STATUS_APPROVED etc. do: every agents/*.py prompt interpolates
# it (so the literal written on disk and the version harness.py validates
# against can never drift apart), and harness.py imports agents/*.py at
# module load time — a harness.py-side constant would be a circular import.
# Bump this whenever the shape changes (field added/renamed/removed, or a
# field's meaning changes); harness.py logs a distinct
# STATUS_SCHEMA_VERSION_MISMATCH instead of misreading an old file as current.
STATUS_SCHEMA_VERSION = 1

# Chat-return verdict markers, parsed case-tolerantly by harness.py's
# _verdict_is(). Same drift-prevention rationale as STATUS_* above.
VERDICT_APPROVED = "APPROVED"
VERDICT_REJECTED = "REJECTED"
VERDICT_E2E_PASSED = "E2E_PASSED"
VERDICT_E2E_FAILED = "E2E_FAILED"

# Where E2E/manual screenshots are written and read from. Not stack-dependent
# (unlike test_runner/server_cmd in stack_layout.py) — every backend profile
# writes screenshots to the same place — so a plain constant here is enough.
SCREENSHOTS_DIR = "tests/screenshots"

# Subprocess timeouts (seconds), kept separate even though they currently
# share a value: E2E and mutation testing are unrelated processes and may
# need to diverge later. The user-facing "N minutes" messages are built from
# these so the number and the message can never drift apart.
E2E_SUBPROCESS_TIMEOUT_S = 300
MUTATION_TEST_TIMEOUT_S = 300

# Secret-holding files agents must never read, regardless of SAFE_WRITE_DIRS —
# read_file/list_files are not otherwise confined the way write_file/append_file
# are, so an agent debugging an API connectivity issue could plausibly try
# read_file(".env") and get DEEPSEEK_API_KEY etc. back as a tool result, which
# then flows straight into the LLM's own context and into logs. Matches ".env",
# ".env.local", ".env.production", etc. — not just a literal ".env".
_SECRET_FILENAME_RE = re.compile(r"^\.env(\..+)?$")

def _is_secret_path(path: str) -> bool:
    """True if `path`'s basename looks like a secrets file (.env, .env.local, ...)."""
    return bool(_SECRET_FILENAME_RE.match(os.path.basename(str(path).rstrip("/"))))

def _normalize_agent_path(path: str) -> str:
    """Normalize a tool-call path the same way for every caller that needs
    to compare it against a specific directory: convert an absolute path
    that points to cwd into a relative one, and strip a "/workspace/" prefix
    (agents are told that's the project root *inside run_bash*, and some
    generalize that to these host-side tools too). Shared by _is_safe_path
    (checked against SAFE_WRITE_DIRS) and execute_tool's Leader-role write
    restriction (checked against PROGRESS_DIR alone) so neither one
    incorrectly rejects a path the other would accept.
    """
    normalized = os.path.normpath(path).replace("\\", "/")
    cwd = os.getcwd().replace("\\", "/")
    if normalized.startswith(cwd + "/"):
        normalized = normalized[len(cwd) + 1:]
    if normalized == "/workspace":
        normalized = "."
    elif normalized.startswith("/workspace/"):
        normalized = normalized[len("/workspace/"):]
    return normalized


def _is_safe_path(path: str) -> bool:
    """Check that the path is within the allowed directories.
    Accepts absolute paths by converting them to relative paths from cwd.
    """
    normalized = _normalize_agent_path(path)
    # Block path traversal
    if ".." in normalized:
        return False
    return any(normalized.startswith(d) for d in SAFE_WRITE_DIRS)

def _is_safe_command(command: str) -> tuple[bool, str]:
    """Returns (is_safe, reason_if_not)."""
    for pattern in BLOCKED_BASH_PATTERNS:
        if re.search(pattern, command):
            return False, f"Command blocked by security pattern: '{pattern}'"
    return True, ""

# ─── TOOL IMPLEMENTATIONS ───────────────────────────────────────────────────

def read_file(path: str = None, limit: int = None, offset: int = 0,
              file_path: str = None, file: str = None, filename: str = None) -> str:
    path = path or file_path or file or filename
    if _is_secret_path(path):
        return json.dumps({
            "error": f"Refusing to read '{path}' — it matches a secrets file pattern (.env*). "
                     f"Credentials are never readable by agents, regardless of the reason."
        })
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if offset:
            lines = lines[offset:]
        if limit:
            lines = lines[:limit]
        return json.dumps({"content": "".join(lines), "path": path})
    except FileNotFoundError as e:
        parent = os.path.dirname(path) or "."
        try:
            siblings = sorted(os.listdir(parent))
        except Exception:
            siblings = []
        return json.dumps({
            "error": str(e),
            "hint": f"'{path}' does not exist. Files actually in '{parent}': {siblings}. "
                    f"Do not guess another extension/spelling — pick the exact name from this list."
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

def write_file(path: str = None, content: str = "", file_path: str = None,
               file: str = None, filename: str = None) -> str:
    path = path or file_path or file or filename
    if not path:
        return json.dumps({"error": "Required argument 'path' or 'file_path' is missing"})
    if not _is_safe_path(path):
        return json.dumps({
            "error": f"Path '{path}' is outside the allowed directories: {SAFE_WRITE_DIRS}. "
                     f"Make sure the file is inside one of: {', '.join(SAFE_WRITE_DIRS)}."
        })
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"status": "ok", "path": path})
    except Exception as e:
        return json.dumps({"error": str(e)})

def append_file(path: str = None, content: str = "", file_path: str = None,
                file: str = None, filename: str = None) -> str:
    path = path or file_path or file or filename
    if not path:
        return json.dumps({"error": "Required argument 'path' or 'file_path' is missing"})
    if not _is_safe_path(path):
        return json.dumps({
            "error": f"Path '{path}' is outside the allowed directories: {SAFE_WRITE_DIRS}."
        })
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + content)
        return json.dumps({"status": "ok", "path": path})
    except Exception as e:
        return json.dumps({"error": str(e)})

def list_files(directory: str = ".", depth: int = None, limit: int = None, **kwargs) -> str:
    try:
        result = []
        base_depth = directory.rstrip("/").count("/") + (0 if directory == "." else 1)
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "mutants", "node_modules", ".venv", "venv")]
            if depth is not None:
                current_depth = root.rstrip("/").count("/") + 1 - base_depth
                if current_depth >= depth:
                    dirs[:] = []
            for file in files:
                if _is_secret_path(file):
                    continue  # never surface .env* — see _is_secret_path
                result.append(os.path.join(root, file))
                if limit is not None and len(result) >= limit:
                    return json.dumps({"files": result, "truncated": True})
        return json.dumps({"files": result})
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_bash(command: str, timeout: int = 60) -> str:
    """
    Execute a bash command on behalf of an agent.

    SECURITY: this delegates to sandbox.get_runner(), which by default (SANDBOX_MODE=docker)
    runs the command inside a locked-down container — read-only root filesystem, only
    SAFE_WRITE_DIRS mounted read-write, non-root, capabilities dropped, resource limits,
    and a wall-clock kill switch. That confinement happens at the OS/mount-namespace
    boundary, which is what actually closes the old write-confinement bypass (the regex
    blocklist below is kept only as a fast first-pass filter for obviously destructive
    intent — it is NOT the security boundary).

    Falls back to running directly on the host (with a one-time warning) if no
    container runtime is available, or if SANDBOX_MODE=local is set explicitly.
    """
    safe, reason = _is_safe_command(command)
    if not safe:
        return json.dumps({"error": reason, "blocked": True})
    # On macOS 'python' may not exist — normalize to python3
    command = command.replace("python -m", "python3 -m").replace("python3 -m mutmut", "python3 -m mutmut")
    if command.strip().startswith("python ") and not command.strip().startswith("python3"):
        command = "python3" + command[len("python"):]

    from sandbox import get_runner
    result = get_runner().run(command, timeout=timeout, cwd=os.getcwd(), safe_write_dirs=SAFE_WRITE_DIRS)
    return json.dumps(result)

def update_feature_status(feature_id: int, status: str) -> str:
    if status not in VALID_FEATURE_STATUSES:
        return json.dumps({
            "error": f"Invalid status '{status}'. Allowed values: {sorted(VALID_FEATURE_STATUSES)}"
        })
    try:
        with open(FEATURE_LIST_PATH, "r") as f:
            features = json.load(f)
        updated = False
        for feat in features:
            if feat["id"] == feature_id:
                feat["status"] = status
                feat["updated_at"] = datetime.datetime.now().isoformat()
                updated = True
                break
        if not updated:
            return json.dumps({"error": f"Feature #{feature_id} not found in feature_list.json"})
        with open(FEATURE_LIST_PATH, "w") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
        return json.dumps({"status": "ok", "feature_id": feature_id, "new_status": status})
    except Exception as e:
        return json.dumps({"error": str(e)})

def read_feature_list() -> str:
    try:
        with open(FEATURE_LIST_PATH, "r") as f:
            return json.dumps(json.load(f), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_playwright_tests(test_path: str = None, base_url: str = None,
                         headed: bool = False, timeout_ms: int = 30000) -> str:
    """
    Run E2E tests with Playwright. Branches on the configured e2e runtime
    (stack_layout.resolve_layout()'s "e2e_runtime"): "python" runs
    pytest-playwright (the original/default behavior), "node" runs
    @playwright/test via npx. Falls back to the Python runner if e2e_runtime
    is unset or unrecognized, so existing projects with no e2e_runner
    configured keep working exactly as before.

    If test_path is not given, it defaults to the resolved stack's
    e2e_test_dir (e.g. "tests/e2e/" for Python, "e2e/" for Node) instead of
    a hardcoded Python-only path.

    If base_url is not given, it's derived from the resolved stack's "port"
    (stack_profiles.json's backend entry) instead of a hardcoded
    "localhost:8000" — so a project whose backend listens on a different
    port doesn't silently get E2E tests pointed at the wrong URL.
    """
    layout = resolve_layout()
    runtime = layout.get("e2e_runtime") or "python"
    resolved_test_path = test_path or layout.get("e2e_test_dir") or "tests/e2e/"
    base_url = base_url or f"http://localhost:{layout.get('port', 8000)}"

    if runtime == "node":
        return _run_playwright_tests_node(resolved_test_path, base_url, headed, timeout_ms)
    return _run_playwright_tests_python(resolved_test_path, base_url, headed, timeout_ms)


def _run_playwright_tests_python(test_path: str, base_url: str, headed: bool, timeout_ms: int) -> str:
    """
    Run E2E tests with pytest-playwright. Installs dependencies if not
    available. Automatically captures screenshots on failures.
    """
    # Use sys.executable rather than a hardcoded "python"/"pip" — the name of
    # the interpreter/pip binary varies by machine (some only have python3,
    # some only python, some neither on PATH), but sys.executable is always
    # the exact interpreter already running this process, in the right venv.
    # Same fix already applied to take_screenshot() below.
    py = f'"{sys.executable}"'

    # Check/install pytest-playwright
    check = subprocess.run(f"{py} -m pytest --co -q {test_path} 2>&1 | head -5",
                           shell=True, capture_output=True, text=True)
    if "No module named" in check.stdout or "playwright" not in check.stdout.lower():
        install = subprocess.run(
            f"{py} -m pip install pytest-playwright playwright --quiet --break-system-packages && "
            f"{py} -m playwright install chromium --with-deps",
            shell=True, capture_output=True, text=True, timeout=120
        )
        if install.returncode != 0:
            return json.dumps({"error": "Failed to install playwright", "stderr": install.stderr[:500]})

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    headed_flag = "--headed" if headed else ""
    cmd = (
        f"{py} -m pytest {test_path} -v --tb=short "
        f"--base-url={base_url} "
        f"--screenshot=only-on-failure "
        f"--output={SCREENSHOTS_DIR} "
        f"--timeout={timeout_ms // 1000} "
        f"{headed_flag} 2>&1"
    )
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=E2E_SUBPROCESS_TIMEOUT_S)
        output = result.stdout + result.stderr

        # List generated screenshots if there were failures
        screenshots = []
        if os.path.exists(SCREENSHOTS_DIR):
            screenshots = [f for f in os.listdir(SCREENSHOTS_DIR) if f.endswith(".png")]

        return json.dumps({
            "output": output[-3000:],
            "returncode": result.returncode,
            "success": result.returncode == 0,
            "screenshots": screenshots,
            "tip": ("pytest-playwright does not generate error-context.md (that's a Node "
                    "@playwright/test artifact) — the authoritative source for a failure is the "
                    "'output' field above (last ~3000 chars of pytest stdout/stderr, including the "
                    "traceback). If the traceback only shows a generic timeout (e.g. wait_for_url/"
                    "wait_for_selector) without explaining the cause, the page likely rendered a "
                    "visible error (e.g. a validation/uniqueness banner) that the test never checked "
                    "for — inspect the test's own assertions/selectors rather than assuming the "
                    "feature is broken.")
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Timeout: E2E tests took more than {E2E_SUBPROCESS_TIMEOUT_S // 60} minutes."})
    except Exception as e:
        return json.dumps({"error": str(e)})


def _run_playwright_tests_node(test_path: str, base_url: str, headed: bool, timeout_ms: int) -> str:
    """
    Run E2E tests with Node's @playwright/test via `npx playwright test`.
    Installs @playwright/test and the Chromium browser if not already
    available. Resolves the project's own playwright.config.ts/.js from the
    resolved e2e_test_dir and passes it explicitly via --config — relying on
    npx auto-discovery breaks when the repo has more than one
    playwright.config/spec tree (e.g. e2e/ and frontend/), since Playwright
    can load specs from both in the same process and raise "Requiring
    @playwright/test second time".
    """
    check = subprocess.run("npx playwright --version", shell=True, capture_output=True, text=True)
    if check.returncode != 0:
        install = subprocess.run(
            "npm install -D @playwright/test --silent && "
            "npx playwright install --with-deps chromium",
            shell=True, capture_output=True, text=True, timeout=180
        )
        if install.returncode != 0:
            return json.dumps({"error": "Failed to install @playwright/test", "stderr": install.stderr[:500]})

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    layout = resolve_layout()
    e2e_test_dir = layout.get("e2e_test_dir") or ""
    config_flag = ""
    for ext in (".ts", ".js"):
        candidate = os.path.join(e2e_test_dir, f"playwright.config{ext}")
        if os.path.exists(candidate):
            config_flag = f"--config {candidate} "
            break

    headed_flag = "--headed" if headed else ""
    cmd = (
        f"PLAYWRIGHT_BASE_URL={base_url} npx playwright test {test_path} "
        f"{config_flag}--reporter=line --timeout={timeout_ms} "
        f"{headed_flag} 2>&1"
    )
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=E2E_SUBPROCESS_TIMEOUT_S)
        output = result.stdout + result.stderr

        # @playwright/test writes failure artifacts under test-results/ by
        # default; also check tests/screenshots/ in case the spec takes its
        # own explicit page.screenshot() calls there.
        screenshots = []
        if os.path.exists(SCREENSHOTS_DIR):
            screenshots += [f for f in os.listdir(SCREENSHOTS_DIR) if f.endswith(".png")]
        if os.path.exists("test-results"):
            for root, _, files in os.walk("test-results"):
                screenshots += [os.path.join(root, f) for f in files if f.endswith(".png")]

        return json.dumps({
            "output": output[-3000:],
            "returncode": result.returncode,
            "success": result.returncode == 0,
            "screenshots": screenshots,
            "tip": ("If the test failed, read error-context.md in the same test-results subfolder for "
                    "the full stack trace and code context. PLAYWRIGHT_BASE_URL is set as a convenience "
                    "env var, but an existing playwright.config.ts that already sets baseURL explicitly "
                    "takes precedence.")
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Timeout: E2E tests took more than {E2E_SUBPROCESS_TIMEOUT_S // 60} minutes."})
    except Exception as e:
        return json.dumps({"error": str(e)})


def take_screenshot(url: str, output_path: str = f"{SCREENSHOTS_DIR}/manual.png") -> str:
    """
    Takes a screenshot of a URL using Playwright (headless).
    Useful for verifying the visual state of the app at a specific point.
    """
    if not _is_safe_path(output_path):
        return json.dumps({"error": f"Path '{output_path}' is outside the allowed directories."})
    script = (
        f"from playwright.sync_api import sync_playwright; "
        f"p = sync_playwright().start(); "
        f"b = p.chromium.launch(); "
        f"page = b.new_page(); "
        f"page.goto('{url}'); "
        f"page.screenshot(path='{output_path}', full_page=True); "
        f"b.close(); p.stop(); "
        f"print('screenshot saved to {output_path}')"
    )
    try:
        # Use sys.executable rather than a hardcoded "python"/"python3" — the
        # name of the interpreter binary varies by machine (some only have
        # python3, some only python, some neither on PATH), but sys.executable
        # is always the exact interpreter already running this process, in
        # the right venv, with playwright already installed.
        result = subprocess.run(
            f'"{sys.executable}" -c "{script}"', shell=True, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return json.dumps({"status": "ok", "path": output_path})
        return json.dumps({"error": result.stderr[:300]})
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_mutation_tests(paths_to_mutate: str = "src/", tests_dir: str = "tests/") -> str:
    """
    Run mutation testing with mutmut 3.x on the specified path.
    mutmut 3.x uses pyproject.toml for configuration — this function generates it
    automatically if it doesn't exist. Returns summary with score.
    """
    # Ensure mutmut is installed
    check = subprocess.run("python3 -m mutmut --version", shell=True,
                           capture_output=True, text=True)
    if check.returncode != 0:
        install = subprocess.run(
            "pip3 install mutmut --quiet --break-system-packages",
            shell=True, capture_output=True, text=True, timeout=60
        )
        if install.returncode != 0:
            return json.dumps({"error": "Failed to install mutmut", "stderr": install.stderr})

    # mutmut 3.x requires configuration in pyproject.toml
    pyproject_path = "pyproject.toml"
    mutmut_config = f"""
[tool.mutmut]
paths_to_mutate = ["{paths_to_mutate}"]
runner = "python3 -m pytest {tests_dir} -x -q"
"""
    # Add config only if [tool.mutmut] section doesn't exist
    existing = ""
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "r") as f:
            existing = f.read()
    if "[tool.mutmut]" not in existing:
        with open(pyproject_path, "a") as f:
            f.write(mutmut_config)

    try:
        # Run mutmut (ignore returncode — 1 means mutants survived, not an error)
        subprocess.run(
            "python3 -m mutmut run 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=MUTATION_TEST_TIMEOUT_S
        )

        # Get structured summary
        results_cmd = subprocess.run(
            "python3 -m mutmut results 2>&1",
            shell=True, capture_output=True, text=True, timeout=30
        )

        # Get killed/survived/total counts
        junk_cmd = subprocess.run(
            "python3 -m mutmut junk 2>&1 || true",
            shell=True, capture_output=True, text=True, timeout=30
        )

        # Parse totals from results output
        results_text = results_cmd.stdout or ""
        survived = results_text.lower().count("survived") or results_text.count("⏰") or results_text.count("🙁")
        killed_markers = results_cmd.stdout.count("killed") if results_cmd.stdout else 0

        # Try reading .mutmut-cache for statistics
        stats_cmd = subprocess.run(
            "python3 -c \""
            "import sqlite3, os; "
            "db = '.mutmut-cache'; "
            "conn = sqlite3.connect(db) if os.path.exists(db) else None; "
            "if conn: "
            "  c = conn.cursor(); "
            "  total = c.execute(\\\"SELECT COUNT(*) FROM mutant\\\").fetchone()[0]; "
            "  killed = c.execute(\\\"SELECT COUNT(*) FROM mutant WHERE status='killed'\\\").fetchone()[0]; "
            "  survived = c.execute(\\\"SELECT COUNT(*) FROM mutant WHERE status='survived'\\\").fetchone()[0]; "
            "  print(f'total={total} killed={killed} survived={survived} score={round(killed/total*100) if total else 0}%'); "
            "  conn.close() "
            "else: print('no-cache') "
            "\" 2>&1",
            shell=True, capture_output=True, text=True, timeout=10
        )
        stats = stats_cmd.stdout.strip()

        return json.dumps({
            "results": results_text[-1000:] or "(no results — mutmut may not have found any mutants)",
            "stats": stats,
            "tip": "Ideal score >= 80%. If stats shows a score, use it. If it says 'no-cache', tests likely killed all mutants (good sign).",
            "status": "completed"
        })
    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": f"Timeout: mutation testing took more than {MUTATION_TEST_TIMEOUT_S // 60} minutes.",
            "tip": "Report in the progress file that mutation testing was skipped due to timeout and continue.",
            "status": "timeout"
        })
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"})

# ─── SCHEMA REGISTRY ────────────────────────────────────────────────────────

def _schema(name, desc, props, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}
        }
    }

TOOLS_FN = {
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "list_files": list_files,
    "run_bash": run_bash,
    "update_feature_status": update_feature_status,
    "read_feature_list": read_feature_list,
    "run_mutation_tests": run_mutation_tests,
    "run_playwright_tests": run_playwright_tests,
    "take_screenshot": take_screenshot,
}

TOOLS_SCHEMA = {
    "read_file": _schema("read_file", "Read a text file.",
        {
            "path":   {"type": "string",  "description": "File path"},
            "limit":  {"type": "integer", "description": "Maximum number of lines to read (optional)"},
            "offset": {"type": "integer", "description": "Line to start from (optional, default 0)"}
        }, ["path"]),

    "write_file": _schema("write_file", "Write or overwrite a file.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),

    "append_file": _schema("append_file", "Append content to the end of a file.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),

    "list_files": _schema("list_files", "List all files in a directory.",
        {"directory": {"type": "string", "description": "Directory to list. Default: '.'"}}, []),

    "run_bash": _schema("run_bash",
        "Execute a bash command. Use to run tests, install deps, etc. "
        "Destructive commands (rm -rf /, mkfs, etc.) are blocked.",
        {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Timeout in seconds. Default: 60"}
        }, ["command"]),

    "update_feature_status": _schema("update_feature_status",
        "Update the status of a feature in feature_list.json. Statuses: pending, in_progress, done, failed.",
        {"feature_id": {"type": "integer"}, "status": {"type": "string"}}, ["feature_id", "status"]),

    "read_feature_list": _schema("read_feature_list", "Read the full feature_list.json.", {}, []),

    "run_playwright_tests": _schema(
        "run_playwright_tests",
        "Run E2E tests with Playwright. Execute AFTER unit tests pass. "
        "Automatically captures screenshots on failures. Returns output, success, and screenshot list.",
        {
            "test_path":   {"type": "string", "description": "E2E test folder or file. Default: 'tests/e2e/'"},
            "base_url":    {"type": "string", "description": "App base URL. Default: derived from the resolved stack's port (e.g. 'http://localhost:8000')."},
            "headed":      {"type": "boolean","description": "Show browser. Default: false (headless)"},
            "timeout_ms":  {"type": "integer","description": "Timeout per test in ms. Default: 30000"}
        }, []),

    "take_screenshot": _schema(
        "take_screenshot",
        "Take a screenshot of a URL with Playwright (headless). Useful for verifying visual state.",
        {
            "url":         {"type": "string", "description": "URL to capture"},
            "output_path": {"type": "string", "description": f"Output .png path. Default: '{SCREENSHOTS_DIR}/manual.png'"}
        }, ["url"]),

    "run_mutation_tests": _schema(
        "run_mutation_tests",
        "Run mutation testing with mutmut. Verifies that tests actually validate behavior, "
        "not just coverage. Returns: total mutants, killed, survived and score. Ideal score >= 80%.",
        {
            "paths_to_mutate": {"type": "string", "description": "Directory or file to mutate. Default: 'src/'"},
            "tests_dir":       {"type": "string", "description": "Tests directory. Default: 'tests/'"}
        }, []),
}

def get_schemas(*names):
    return [TOOLS_SCHEMA[n] for n in names if n in TOOLS_SCHEMA]

def _normalize_args(args: dict) -> dict:
    """
    Normalize camelCase keys to snake_case to tolerate LLM variations.
    E.g.: filePath → file_path, fileName → file_name, featureId → feature_id
    """
    import re
    def to_snake(key: str) -> str:
        return re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
    return {to_snake(k): v for k, v in args.items()}


_NO_SEARCH_TOOL_HINT = (
    " This harness has no dedicated search tool — provide a 'pattern' argument and it "
    "will be auto-translated into a real search, or use run_bash with grep/rg yourself, "
    "e.g. run_bash(\"grep -rn 'pattern' path/\"). For listing files, use list_files."
)

# Hallucinated tool names agents commonly reach for instead of run_bash.
_SEARCH_TOOL_ALIASES = {"grep", "rg", "search", "search_files", "find", "glob", "ripgrep"}

_NO_EDIT_TOOL_HINT = (
    " This harness has no dedicated edit tool — provide 'old_string' and 'new_string' "
    "(or 'content' for a full overwrite) and it will be auto-translated into a real "
    "read_file + write_file, or use read_file + write_file yourself with the full content."
)

# Hallucinated edit-tool names agents commonly reach for (Cursor/Claude Code
# style) instead of read_file + write_file.
_EDIT_TOOL_ALIASES = {"edit_file", "str_replace_editor", "str_replace", "edit"}

# Filename-search aliases (look for matching paths) vs content-search aliases
# (look for matching lines inside files).
_FILENAME_SEARCH_ALIASES = {"find", "glob"}

# Skip these directories and extensions when walking the tree for a search —
# same exclusion list as list_files() plus common binary/asset extensions
# that are never useful to grep and just waste time reading.
_SEARCH_EXCLUDE_DIRS = {"__pycache__", "mutants", "node_modules", ".venv", "venv"}
_SEARCH_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".tar",
    ".gz", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".db", ".sqlite",
    ".lock", ".pyc",
}
_SEARCH_MAX_MATCHES = 100
_SEARCH_MAX_FILES_SCANNED = 3000


def _search_alias(tool_name: str, args: dict) -> str:
    """
    Best-effort fix for a recurring failure mode: agents hallucinate a
    "grep"/"search"/"find" tool instead of using run_bash (despite the HARD
    RULES telling them not to — see commit 66f1ad8). Returning a plain "tool
    not found" error burns iterations, because the agent just retries the
    same intent under a slightly different name until max_iter is exhausted
    without ever writing or running its actual test (this is exactly what
    happened on feature 26's e2e_tester run: 30 iterations consumed almost
    entirely by repeated grep-name variants, "[ERROR: max_iter 30 reached]").

    So instead of only hinting, translate the call's *intent* into a real,
    pure-Python recursive search (no subprocess/sandbox needed) and return
    real results. The agent's underlying need — find where a symbol/string
    is defined or used — gets satisfied even though the literal tool name
    it guessed doesn't exist. Falls back to the old hint-only error if no
    usable pattern argument is present, so it's never a hard dependency.
    """
    pattern = args.get("pattern") or args.get("query") or args.get("name") or args.get("text")
    path = args.get("path") or args.get("directory") or args.get("dir") or "."
    if not pattern:
        return json.dumps({"error": f"Tool '{tool_name}' not found.{_NO_SEARCH_TOOL_HINT}"})

    try:
        regex = re.compile(pattern)
    except re.error:
        regex = re.compile(re.escape(pattern))

    is_filename_search = tool_name in _FILENAME_SEARCH_ALIASES
    matches: list = []
    files_scanned = 0
    truncated = False

    if os.path.isfile(path):
        candidates = [path]
    else:
        candidates = None  # signal: walk the tree below
        base = path if os.path.isdir(path) else "."

    def _scan(fpath: str) -> bool:
        """Returns True if the match cap was hit (caller should stop)."""
        if is_filename_search:
            if regex.search(os.path.basename(fpath)):
                matches.append(fpath)
            return len(matches) >= _SEARCH_MAX_MATCHES
        if os.path.splitext(fpath)[1].lower() in _SEARCH_BINARY_EXTS:
            return False
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    if regex.search(line):
                        matches.append(f"{fpath}:{lineno}: {line.strip()[:200]}")
                        if len(matches) >= _SEARCH_MAX_MATCHES:
                            return True
        except (OSError, IsADirectoryError):
            pass
        return False

    if candidates is not None:
        for fpath in candidates:
            if _scan(fpath):
                truncated = True
    else:
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SEARCH_EXCLUDE_DIRS]
            stop = False
            for fname in files:
                files_scanned += 1
                if _scan(os.path.join(root, fname)):
                    stop = True
                    truncated = True
                    break
                if files_scanned >= _SEARCH_MAX_FILES_SCANNED:
                    stop = True
                    truncated = True
                    break
            if stop:
                break

    return json.dumps({
        "matches": matches,
        "truncated": truncated,
        "note": (
            f"Tool '{tool_name}' doesn't exist in this harness — auto-translated to a real "
            f"{'filename' if is_filename_search else 'content'} search for pattern "
            f"'{pattern}' under '{path}'."
        ),
    })


def _edit_alias(tool_name: str, args: dict) -> str:
    """
    Best-effort fix for the same failure mode _search_alias addresses, but for
    edit-style tools. Agents (especially ones trained on Cursor/Claude Code
    tool conventions) reach for an `edit_file`/`str_replace_editor`/`str_replace`/
    `edit` tool that doesn't exist in this harness — which only exposes
    read_file/write_file/append_file. Returning a bare "tool not found" burns
    iterations, because the agent retries name variants instead of switching
    strategy (observed: 25 occurrences of this in a single implementer run,
    exhausting MAX_ITER_IMPL without writing anything; also feature 77,
    2026-07-14 — one attempt called the bare `edit` tool name, a second used
    `edit_file` with a `search`/`replace` argument pair instead of
    old_string/new_string, and both burned max_iter with no fix written). So
    translate the call's intent into a real read_file + write_file pair and
    return real results.

    Falls back to the old hint-only error if the args don't carry enough to
    act on (no path, or no old_string/new_string/content), so it's never a
    hard dependency — same spirit as _search_alias's pattern-less fallback.
    """
    path = args.get("path") or args.get("file_path") or args.get("file") or args.get("filename")
    old_string = args.get("old_string") or args.get("old_str") or args.get("old_text") or args.get("search")
    new_string = (args.get("new_string") or args.get("new_str") or args.get("new_text")
                  or args.get("replace") or args.get("replacement"))
    full_content = args.get("content")

    if not path:
        return json.dumps({"error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT}"})

    # Full-overwrite intent (content given, no old/new pair) — delegate
    # straight to write_file rather than erroring.
    if full_content is not None and old_string is None and new_string is None:
        return write_file(path=path, content=full_content)

    if old_string is None or new_string is None:
        return json.dumps({
            "error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT}",
            "hint": "Missing 'old_string'/'new_string' (or 'content'). Use read_file to "
                    "get the current content, then write_file with the full updated content.",
        })

    read_result = json.loads(read_file(path=path))
    if "error" in read_result:
        return json.dumps({
            "error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT} "
                     f"Also failed to read '{path}': {read_result['error']}",
        })

    current_content = read_result["content"]
    occurrences = current_content.count(old_string)
    if occurrences == 0:
        return json.dumps({
            "error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT} "
                     f"'old_string' was not found in '{path}' — use read_file to confirm the "
                     f"exact current content before retrying.",
        })
    if occurrences > 1:
        return json.dumps({
            "error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT} "
                     f"'old_string' appears {occurrences} times in '{path}', which is ambiguous — "
                     f"include more surrounding context in 'old_string' to make it unique, or use "
                     f"read_file + write_file with the full content.",
        })

    updated_content = current_content.replace(old_string, new_string, 1)
    write_result = json.loads(write_file(path=path, content=updated_content))
    if "error" in write_result:
        return json.dumps({"error": write_result["error"]})

    return json.dumps({
        "status": "ok",
        "path": path,
        "note": (
            f"Tool '{tool_name}' doesn't exist in this harness — auto-translated to a real "
            f"read_file + write_file replacement."
        ),
    })


def execute_tool(tool_name: str, args: dict, role: str = "") -> str:
    fn = TOOLS_FN.get(tool_name)
    if fn:
        # The Leader coordinates — it never legitimately needs to write
        # outside progress/. Its own system prompt says "You NEVER write
        # code in src/ or tests/", but that's prose, not enforcement:
        # write_file()/append_file() only check the tool-call args against
        # the global SAFE_WRITE_DIRS (shared by every role, since
        # implementer/e2e_tester/reviewer DO need backend/frontend/tests
        # access). Real incident: the Leader rewrote
        # backend/app/api/v1/professionals.py and
        # backend/tests/test_professionals.py end-to-end while chasing a
        # repeated E2E failure, introducing a real regression (called a
        # password-hashing function that doesn't exist), because nothing in
        # code stopped it. _normalize_agent_path matches _is_safe_path's own
        # normalization so an absolute or "/workspace/"-prefixed progress/
        # path isn't incorrectly rejected here just because this check is
        # narrower (PROGRESS_DIR alone, not all of SAFE_WRITE_DIRS).
        if role == "leader" and tool_name in ("write_file", "append_file"):
            normalized_args = _normalize_args(args)
            path = normalized_args.get("path") or normalized_args.get("file_path") \
                or normalized_args.get("file") or normalized_args.get("filename") or ""
            if not _normalize_agent_path(path).startswith(PROGRESS_DIR.rstrip("/") + "/"):
                return json.dumps({
                    "error": f"Path '{path}' is outside what the Leader role may write. "
                             f"The Leader may only write inside '{PROGRESS_DIR}/' — code and "
                             f"test changes belong to the implementer/e2e_tester, coordinated "
                             f"via run_feature_cycle(), never written directly by the Leader."
                })
        # Most individual tool functions already guard their own bodies and
        # return a json {"error": ...} string on failure, but not all do
        # (e.g. run_bash delegates straight to sandbox.get_runner().run()
        # with no try/except at this level). This is the single dispatch
        # choke point for every tool, current and future, so the safety net
        # belongs here rather than relying on each tool to add its own —
        # an uncaught exception must never propagate up into the agent
        # loop and abort the whole run (best-effort, never block the
        # pipeline). Mirrors the existing alias-fallback try/except below.
        try:
            return fn(**_normalize_args(args))
        except Exception as e:
            return json.dumps({"error": f"Tool '{tool_name}' raised an unhandled exception: {e}"})
    if tool_name in _SEARCH_TOOL_ALIASES:
        try:
            return _search_alias(tool_name, _normalize_args(args))
        except Exception as e:
            return json.dumps({
                "error": f"Tool '{tool_name}' not found.{_NO_SEARCH_TOOL_HINT}",
                "alias_error": str(e),
            })
    if tool_name in _EDIT_TOOL_ALIASES:
        try:
            return _edit_alias(tool_name, _normalize_args(args))
        except Exception as e:
            return json.dumps({
                "error": f"Tool '{tool_name}' not found.{_NO_EDIT_TOOL_HINT}",
                "alias_error": str(e),
            })
    return json.dumps({"error": f"Tool '{tool_name}' not found"})
