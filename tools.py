import os, json, subprocess, datetime, re

# ─── SEGURIDAD ───────────────────────────────────────────────────────────────

# Directorios donde los agentes pueden escribir (relativo al CWD del proyecto)
SAFE_WRITE_DIRS = ("src/", "tests/", "progress/", "docs/", "tests/e2e/", "tests/screenshots/", "frontend/")

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
    """Verifica que el path esté dentro de los directorios permitidos.
    Acepta rutas absolutas convirtiéndolas a relativas respecto al cwd.
    """
    normalized = os.path.normpath(path).replace("\\", "/")
    # Convertir ruta absoluta a relativa si apunta al cwd
    cwd = os.getcwd().replace("\\", "/")
    if normalized.startswith(cwd + "/"):
        normalized = normalized[len(cwd) + 1:]
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
        return json.dumps({"error": "Se requiere el argumento 'path' o 'file_path'"})
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

def append_file(path: str = None, content: str = "", file_path: str = None,
                file: str = None, filename: str = None) -> str:
    path = path or file_path or file or filename
    if not path:
        return json.dumps({"error": "Se requiere el argumento 'path' o 'file_path'"})
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
    # En macOS 'python' no existe por defecto — normalizar a python3
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
    Corre mutation testing con mutmut 3.x sobre el path indicado.
    Mutmut 3.x usa pyproject.toml para configuración — esta función lo genera
    automáticamente si no existe. Retorna resumen con score.
    """
    # Asegurar que mutmut esté instalado
    check = subprocess.run("python3 -m mutmut --version", shell=True,
                           capture_output=True, text=True)
    if check.returncode != 0:
        install = subprocess.run(
            "pip3 install mutmut --quiet --break-system-packages",
            shell=True, capture_output=True, text=True, timeout=60
        )
        if install.returncode != 0:
            return json.dumps({"error": "No se pudo instalar mutmut", "stderr": install.stderr})

    # mutmut 3.x requiere configuración en pyproject.toml
    pyproject_path = "pyproject.toml"
    mutmut_config = f"""
[tool.mutmut]
paths_to_mutate = ["{paths_to_mutate}"]
runner = "python3 -m pytest {tests_dir} -x -q"
"""
    # Agregar config solo si no existe sección [tool.mutmut]
    existing = ""
    if os.path.exists(pyproject_path):
        with open(pyproject_path, "r") as f:
            existing = f.read()
    if "[tool.mutmut]" not in existing:
        with open(pyproject_path, "a") as f:
            f.write(mutmut_config)

    try:
        # Correr mutmut (ignorar returncode — 1 significa mutantes sobrevivieron, no error)
        subprocess.run(
            "python3 -m mutmut run 2>/dev/null",
            shell=True, capture_output=True, text=True, timeout=300
        )

        # Obtener resumen estructurado
        results_cmd = subprocess.run(
            "python3 -m mutmut results 2>&1",
            shell=True, capture_output=True, text=True, timeout=30
        )

        # Obtener conteo de killed/survived/total
        junk_cmd = subprocess.run(
            "python3 -m mutmut junk 2>&1 || true",
            shell=True, capture_output=True, text=True, timeout=30
        )

        # Parsear totales desde el output de results
        results_text = results_cmd.stdout or ""
        survived = results_text.lower().count("survived") or results_text.count("⏰") or results_text.count("🙁")
        killed_markers = results_cmd.stdout.count("killed") if results_cmd.stdout else 0

        # Intentar leer .mutmut-cache para estadísticas
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
            "results": results_text[-1000:] or "(sin resultados — mutmut puede no haber encontrado mutantes)",
            "stats": stats,
            "tip": "Score ideal >= 80%. Si stats muestra score, úsalo. Si dice 'no-cache', los tests probablemente mataron todos los mutantes (buena señal).",
            "status": "completed"
        })
    except subprocess.TimeoutExpired:
        return json.dumps({
            "error": "Timeout: mutation testing tomó más de 5 minutos.",
            "tip": "Reporta en el progress file que mutation testing fue omitido por timeout y continúa.",
            "status": "timeout"
        })
    except Exception as e:
        return json.dumps({"error": str(e), "status": "error"})

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
        {
            "path":   {"type": "string",  "description": "Ruta del archivo"},
            "limit":  {"type": "integer", "description": "Número máximo de líneas a leer (opcional)"},
            "offset": {"type": "integer", "description": "Línea desde la que empezar (opcional, default 0)"}
        }, ["path"]),

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

def _normalize_args(args: dict) -> dict:
    """
    Normaliza claves camelCase a snake_case para tolerar variaciones del LLM.
    Ej: filePath → file_path, fileName → file_name, featureId → feature_id
    """
    import re
    def to_snake(key: str) -> str:
        return re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower()
    return {to_snake(k): v for k, v in args.items()}


def execute_tool(tool_name: str, args: dict) -> str:
    fn = TOOLS_FN.get(tool_name)
    if fn:
        return fn(**_normalize_args(args))
    return json.dumps({"error": f"Herramienta '{tool_name}' no encontrada"})
