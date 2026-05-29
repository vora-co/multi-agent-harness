import os, json, subprocess, datetime, re

# ─── SEGURIDAD ───────────────────────────────────────────────────────────────

# Directorios donde los agentes pueden escribir (relativo al CWD del proyecto)
SAFE_WRITE_DIRS = ("src/", "tests/", "progress/", "docs/", "tests/e2e/", "tests/screenshots/")

# Patrones de comandos bash bloqueados — evita destrucción accidental
BLOCKED_BASH_PATTERNS = [
    r"rm\s+-rf\s+/",          # rm -rf /
    r"rm\s+-rf\s+\.\.",       # rm -rf ..
    r">\s*/dev/sd",           # sobreescribir disco
    r"mkfs",                  # formatear partición
    r"dd\s+if=",              # copia raw de disco
    r"chmod\s+-R\s+777\s+/",  # permisos globales
    r":()\{.*\};:",           # fork bomb
]

# Estados válidos para features
VALID_FEATURE_STATUSES = {"pending", "in_progress", "done", "failed"}

def _is_safe_path(path: str) -> bool:
    """Verifica que el path esté dentro de los directorios permitidos."""
    normalized = os.path.normpath(path).replace("\\", "/")
    # Bloquea traversal
    if ".." in normalized:
        return False
    return any(normalized.startswith(d) for d in SAFE_WRITE_DIRS)

def _is_safe_command(command: str) -> tuple[bool, str]:
    """Retorna (es_seguro, razón_si_no_lo_es)."""
    for pattern in BLOCKED_BASH_PATTERNS:
        if re.search(pattern, command):
            return False, f"Comando bloqueado por patrón de seguridad: '{pattern}'"
    return True, ""

# ─── IMPLEMENTACIONES ───────────────────────────────────────────────────────

def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.dumps({"content": f.read(), "path": path})
    except Exception as e:
        return json.dumps({"error": str(e)})

def write_file(path: str, content: str) -> str:
    if not _is_safe_path(path):
        return json.dumps({
            "error": f"Path '{path}' fuera de los directorios permitidos: {SAFE_WRITE_DIRS}. "
                     "Revisa que el archivo esté en src/, tests/, progress/ o docs/."
        })
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"status": "ok", "path": path})
    except Exception as e:
        return json.dumps({"error": str(e)})

def append_file(path: str, content: str) -> str:
    if not _is_safe_path(path):
        return json.dumps({
            "error": f"Path '{path}' fuera de los directorios permitidos: {SAFE_WRITE_DIRS}."
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
            dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
            for file in files:
                result.append(os.path.join(root, file))
        return json.dumps({"files": result})
    except Exception as e:
        return json.dumps({"error": str(e)})

def run_bash(command: str, timeout: int = 60) -> str:
    safe, reason = _is_safe_command(command)
    if not safe:
        return json.dumps({"error": reason, "blocked": True})
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
        return json.dumps({"error": f"Timeout después de {timeout}s", "timeout": True})
    except Exception as e:
        return json.dumps({"error": str(e)})

def update_feature_status(feature_id: int, status: str) -> str:
    if status not in VALID_FEATURE_STATUSES:
        return json.dumps({
            "error": f"Status '{status}' inválido. Valores permitidos: {sorted(VALID_FEATURE_STATUSES)}"
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
            return json.dumps({"error": f"Feature #{feature_id} no encontrada en feature_list.json"})
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
    Corre tests E2E con Playwright/pytest-playwright.
    Instala dependencias si no están disponibles.
    Captura screenshots en failures automáticamente.
    """
    # Verificar/instalar pytest-playwright
    check = subprocess.run("python -m pytest --co -q tests/e2e/ 2>&1 | head -5",
                           shell=True, capture_output=True, text=True)
    if "No module named" in check.stdout or "playwright" not in check.stdout.lower():
        install = subprocess.run(
            "pip install pytest-playwright playwright --quiet --break-system-packages && "
            "playwright install chromium --with-deps",
            shell=True, capture_output=True, text=True, timeout=120
        )
        if install.returncode != 0:
            return json.dumps({"error": "No se pudo instalar playwright", "stderr": install.stderr[:500]})

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

        # Listar screenshots generados si hubo fallos
        screenshots = []
        if os.path.exists("tests/screenshots"):
            screenshots = [f for f in os.listdir("tests/screenshots") if f.endswith(".png")]

        return json.dumps({
            "output": output[-3000:],
            "returncode": result.returncode,
            "success": result.returncode == 0,
            "screenshots": screenshots,
            "tip": "Si hay screenshots, léelos con read_file para ver el estado de la UI en el fallo."
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Timeout: los tests E2E tardaron más de 5 minutos."})
    except Exception as e:
        return json.dumps({"error": str(e)})


def take_screenshot(url: str, output_path: str = "tests/screenshots/manual.png") -> str:
    """
    Toma un screenshot de una URL usando Playwright (headless).
    Útil para verificar el estado visual de la app en un punto específico.
    """
    if not _is_safe_path(output_path):
        return json.dumps({"error": f"Path '{output_path}' fuera de los directorios permitidos."})
    script = (
        f"from playwright.sync_api import sync_playwright; "
        f"p = sync_playwright().start(); "
        f"b = p.chromium.launch(); "
        f"page = b.new_page(); "
        f"page.goto('{url}'); "
        f"page.screenshot(path='{output_path}', full_page=True); "
        f"b.close(); p.stop(); "
        f"print('screenshot guardado en {output_path}')"
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
    Corre mutation testing con mutmut sobre el path indicado.
    Instala mutmut si no está disponible.
    Retorna resumen: mutantes totales, muertos, sobrevivientes y score.
    """
    # Asegurar que mutmut esté instalado
    check = subprocess.run("mutmut --version", shell=True, capture_output=True, text=True)
    if check.returncode != 0:
        install = subprocess.run(
            "pip install mutmut --quiet --break-system-packages",
            shell=True, capture_output=True, text=True, timeout=60
        )
        if install.returncode != 0:
            return json.dumps({"error": "No se pudo instalar mutmut", "stderr": install.stderr})

    # Correr mutmut
    cmd = f"mutmut run --paths-to-mutate {paths_to_mutate} --tests-dir {tests_dir} 2>&1"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        output = result.stdout + result.stderr

        # Obtener resumen
        results_cmd = subprocess.run(
            "mutmut results", shell=True, capture_output=True, text=True, timeout=30
        )

        return json.dumps({
            "run_output": output[-2000:],  # últimas 2000 chars para no saturar contexto
            "results": results_cmd.stdout,
            "returncode": result.returncode,
            "tip": "Score ideal >= 80%. Si hay mutantes sobrevivientes, los tests no validan ese comportamiento."
        })
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "Timeout: mutation testing tomó más de 5 minutos. Reduce el scope con paths_to_mutate."})
    except Exception as e:
        return json.dumps({"error": str(e)})

# ─── REGISTRO DE SCHEMAS ────────────────────────────────────────────────────

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
    "read_file": _schema("read_file", "Lee un archivo de texto.",
        {"path": {"type": "string", "description": "Ruta del archivo"}}, ["path"]),

    "write_file": _schema("write_file", "Escribe o sobreescribe un archivo.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),

    "append_file": _schema("append_file", "Agrega contenido al final de un archivo.",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),

    "list_files": _schema("list_files", "Lista todos los archivos de un directorio.",
        {"directory": {"type": "string", "description": "Directorio a listar. Default: '.'"}}, []),

    "run_bash": _schema("run_bash",
        "Ejecuta un comando bash. Usa para correr tests, instalar deps, etc. "
        "Comandos destructivos (rm -rf /, mkfs, etc.) están bloqueados.",
        {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "Timeout en segundos. Default: 60"}
        }, ["command"]),

    "update_feature_status": _schema("update_feature_status",
        "Actualiza el estado de una feature en feature_list.json. Estados: pending, in_progress, done, failed.",
        {"feature_id": {"type": "integer"}, "status": {"type": "string"}}, ["feature_id", "status"]),

    "read_feature_list": _schema("read_feature_list", "Lee feature_list.json completo.", {}, []),

    "run_playwright_tests": _schema(
        "run_playwright_tests",
        "Corre tests E2E con Playwright. Ejecutar DESPUÉS de que los tests unitarios pasen. "
        "Captura screenshots automáticamente en fallos. Retorna output, success y lista de screenshots.",
        {
            "test_path":   {"type": "string", "description": "Carpeta o archivo de tests E2E. Default: 'tests/e2e/'"},
            "base_url":    {"type": "string", "description": "URL base de la app. Default: 'http://localhost:8000'"},
            "headed":      {"type": "boolean","description": "Mostrar navegador. Default: false (headless)"},
            "timeout_ms":  {"type": "integer","description": "Timeout por test en ms. Default: 30000"}
        }, []),

    "take_screenshot": _schema(
        "take_screenshot",
        "Toma un screenshot de una URL con Playwright (headless). Útil para verificar estado visual.",
        {
            "url":         {"type": "string", "description": "URL a capturar"},
            "output_path": {"type": "string", "description": "Ruta de salida .png. Default: 'tests/screenshots/manual.png'"}
        }, ["url"]),

    "run_mutation_tests": _schema(
        "run_mutation_tests",
        "Corre mutation testing con mutmut. Verifica que los tests realmente validen comportamiento, "
        "no solo cobertura. Retorna: mutantes totales, muertos, sobrevivientes y score. Score ideal >= 80%.",
        {
            "paths_to_mutate": {"type": "string", "description": "Directorio o archivo a mutar. Default: 'src/'"},
            "tests_dir":       {"type": "string", "description": "Directorio de tests. Default: 'tests/'"}
        }, []),
}

def get_schemas(*names):
    return [TOOLS_SCHEMA[n] for n in names if n in TOOLS_SCHEMA]

def execute_tool(tool_name: str, args: dict) -> str:
    fn = TOOLS_FN.get(tool_name)
    if fn:
        return fn(**args)
    return json.dumps({"error": f"Herramienta '{tool_name}' no encontrada"})
