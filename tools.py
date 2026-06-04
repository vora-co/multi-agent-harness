import os, json, subprocess, datetime, re

# ─── SECURITY ────────────────────────────────────────────────────────────────

# Directories where agents are allowed to write (relative to project CWD)
SAFE_WRITE_DIRS = ("src/", "tests/", "progress/", "docs/", "tests/e2e/", "tests/screenshots/", "frontend/")

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

def _is_safe_path(path: str) -> bool:
    """Check that the path is within the allowed directories.
    Accepts absolute paths by converting them to relative paths from cwd.
    """
    normalized = os.path.normpath(path).replace("\\", "/")
    # Convert absolute path to relative if it points to cwd
    cwd = os.getcwd().replace("\\", "/")
    if normalized.startswith(cwd + "/"):
        normalized = normalized[len(cwd) + 1:]
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if offset:
            lines = lines[offset:]
        if limit:
            lines = lines[:limit]
        return json.dumps({"content": "".join(lines), "path": path})
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
                     "Make sure the file is in src/, tests/, progress/ or docs/."
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

def list_files(directory: str = ".") -> str:
    try:
        result = []
        for root, dirs, files in os.walk(directory):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "mutants", "node_modules", ".venv", "venv")]
            for file in files:
                result.append(os.path.join(root, file))
        return json.dumps({"files": result})
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_bash(command: str, timeout: int = 60) -> str:
    safe, reason = _is_safe_command(command)
    if not safe:
        return json.dumps({"error": reason, "blocked": True})
    # On macOS 'python' may not exist — normalize to python3
    command = command.replace("python -m", "python3 -m").replace("python3 -m mutmut", "python3 -m mutmut")
    if command.strip().startswith("python ") and not command.strip().startswith("python3"):
        command = "python3" + command[len("python"):]
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return json.dumps({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "success": result.returncode == 0
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"Timeout after {timeout}s", "timeout": True})
    except Exception as e:
        return json.dumps({"error": str(e)})

def update_feature_status(feature_id: int, status: str) -> str:
    if status not in VALID_FEATURE_STATUSES:
        return json.dumps({
            "error": f"Invalid status '{status}'. Allowed values: {sorted(VALID_FEATURE_STATUSES)}"
        })
    try:
        with open("feature_list.json", "r") as f:
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
        with open("feature_list.json", "w") as f:
            json.dump(features, f, indent=2, ensure_ascii=False)
        return json.dumps({"status": "ok", "feature_id": feature_id, "new_status": status})
    except Exception as e:
        return json.dumps({"error": str(e)})

def read_feature_list() -> str:
    try:
        with open("feature_list.json", "r") as f:
            return json.dumps(json.load(f), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_playwright_tests(test_path: str = "tests/e2e/", base_url: str = "http://localhost:8000",
                         headed: bool = False, timeout_ms: int = 30000) -> str:
    """
    Run E2E tests with Playwright/pytest-playwright.
    Installs dependencies if not available.
    Automatically captures screenshots on failures.
    """
    # Check/install pytest-playwright
    check = subprocess.run("python -m pytest --co -q tests/e2e/ 2>&1 | head -5",
                           shell=True, capture_output=True, text=True)
    if "No module named" in check.stdout or "playwright" not in check.stdout.lower():
        install = subprocess.run(
            "pip install pytest-playwright playwright --quiet --break-system-packages && "
            "playwright install chromium --with-deps",
            shell=True, capture_output=True, text=True, timeout=120
        )
        if install.returncode != 0:
            return json.dumps({"error": "Failed to install playwright", "stderr": install.stderr[:500]})

    os.makedirs("tests/screenshots", exist_ok=True)

    headed_flag = "--headed" if headed else ""
    cmd = (
        f"python -m pytest {test_path} -v --tb=short "
        f"--base-url={base_url} "
        f"--screenshot=only-on-failure "
        f"--output=tests/screenshots "
        f"--timeout={timeout_ms // 1000} "
        f"{headed_flag} 2>&1"
    )
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        output = result.stdout + result.stderr

        # List generated screenshots if there were failures
        screenshots = []
        if os.path.exists("tests/screenshots"):
            screenshots = [f for f in os.listdir("tests/screenshots") if f.endswith(".png")]

        return json.dumps({
            "output": output[-3000:],
            "returncode": result.returncode,
            "success": result.returncode == 0,
            "screenshots": screenshots,
            "tip": "If there are screenshots, read them with read_file to see the UI state at failure."
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Timeout: E2E tests took more than 5 minutes."})
    except Exception as e:
        return json.dumps({"error": str(e)})


def take_screenshot(url: str, output_path: str = "tests/screenshots/manual.png") -> str:
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
        result = subprocess.run(
            f'python -c "{script}"', shell=True, capture_output=True, text=True, timeout=30
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
            shell=True, capture_output=True, text=True, timeout=300
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
            "error": "Timeout: mutation testing took more than 5 minutes.",
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
            "base_url":    {"type": "string", "description": "App base URL. Default: 'http://localhost:8000'"},
            "headed":      {"type": "boolean","description": "Show browser. Default: false (headless)"},
            "timeout_ms":  {"type": "integer","description": "Timeout per test in ms. Default: 30000"}
        }, []),

    "take_screenshot": _schema(
        "take_screenshot",
        "Take a screenshot of a URL with Playwright (headless). Useful for verifying visual state.",
        {
            "url":         {"type": "string", "description": "URL to capture"},
            "output_path": {"type": "string", "description": "Output .png path. Default: 'tests/screenshots/manual.png'"}
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


def execute_tool(tool_name: str, args: dict) -> str:
    fn = TOOLS_FN.get(tool_name)
    if fn:
        return fn(**_normalize_args(args))
    return json.dumps({"error": f"Tool '{tool_name}' not found"})
