"""
tests/test_stack_layout.py — Unit tests for stack_layout.resolve_layout().

Focuses on the e2e_* resolution added alongside Node/@playwright/test
support (STACK_E2E env var / stack_config.json's "e2e_runner" key /
stack_profiles.json's "e2e_runner" map / hardcoded fallback) — the backend/
frontend resolution paths already exercised indirectly via test_harness_core.py
are not re-tested here.

Run with:
    python3 -m pytest tests/test_stack_layout.py -v
"""

import importlib
import json
import sys
from pathlib import Path

import pytest


def _load_stack_layout(monkeypatch, tmp_path: Path):
    """Import a fresh stack_layout module with its lru_cache cleared, chdir'd
    into an isolated tmp_path so stack_config.json / stack_profiles.json
    reads are sandboxed per test."""
    monkeypatch.chdir(tmp_path)
    for k in ("SAFE_WRITE_DIRS", "CODE_TREE_DIRS", "STACK_BACKEND",
              "STACK_FRONTEND", "STACK_E2E", "APP_NAME"):
        monkeypatch.delenv(k, raising=False)

    for key in list(sys.modules.keys()):
        if key == "stack_layout":
            del sys.modules[key]

    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    mod = importlib.import_module("stack_layout")
    mod.resolve_layout.cache_clear()
    return mod


def _write_profiles(tmp_path: Path, profiles: dict):
    (tmp_path / "stack_profiles.json").write_text(json.dumps(profiles))


def _write_config(tmp_path: Path, config: dict):
    (tmp_path / "stack_config.json").write_text(json.dumps(config))


_MINI_PROFILES = {
    "backend": {
        "python-fastapi": {
            "name": "Python + FastAPI", "language": "Python 3.9+",
            "dirs": "src/...", "test_runner": "python3 -m pytest tests/ -v",
            "server_cmd": "python3 -m uvicorn src.main:app --port 8000",
            "db_family": "asyncpg",
            "safe_write_dirs": ["src/", "tests/"],
            "code_tree_dirs": ["src", "tests"],
        },
    },
    "frontend": {"react-tailwind": {"name": "React", "dirs": "frontend/", "dev_cmd": "npm run dev"}},
    "database": {"json": {"name": "JSON files", "notes": "n/a"}},
    "e2e_runner": {
        "playwright": {
            "name": "Playwright (Python)", "runtime": "python", "file_ext": ".py",
            "test_dir": "tests/e2e/", "run_cmd": "python3 -m pytest tests/e2e/ -v",
            "notes": "python notes",
        },
        "playwright-node": {
            "name": "Playwright (Node)", "runtime": "node", "file_ext": ".spec.ts",
            "test_dir": "e2e/", "run_cmd": "npx playwright test",
            "notes": "node notes",
        },
        "none": {
            "name": "No E2E tests", "test_dir": None, "run_cmd": None,
            "notes": "E2E testing disabled for this project.",
        },
    },
    "defaults": {
        "backend": "python-fastapi", "frontend": "react-tailwind",
        "database": "json", "e2e_runner": "playwright",
    },
}


class TestE2ELayoutResolution:

    def test_default_e2e_runtime_is_python_with_no_config(self, monkeypatch, tmp_path):
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        assert layout["e2e_runtime"] == "python"
        assert layout["e2e_test_dir"] == "tests/e2e/"
        assert layout["e2e_key"] == "playwright"

    def test_e2e_runner_from_stack_config_json(self, monkeypatch, tmp_path):
        _write_profiles(tmp_path, _MINI_PROFILES)
        _write_config(tmp_path, {"backend": "python-fastapi", "e2e_runner": "playwright-node"})
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        assert layout["e2e_runtime"] == "node"
        assert layout["e2e_test_dir"] == "e2e/"
        assert layout["e2e_file_ext"] == ".spec.ts"
        assert layout["e2e_run_cmd"] == "npx playwright test"
        assert layout["e2e_key"] == "playwright-node"

    def test_stack_e2e_env_var_overrides_config_file(self, monkeypatch, tmp_path):
        _write_profiles(tmp_path, _MINI_PROFILES)
        _write_config(tmp_path, {"backend": "python-fastapi", "e2e_runner": "playwright"})
        sl = _load_stack_layout(monkeypatch, tmp_path)
        monkeypatch.setenv("STACK_E2E", "playwright-node")  # set after the loader's delenv cleanup
        layout = sl.resolve_layout()
        assert layout["e2e_runtime"] == "node"
        assert layout["e2e_key"] == "playwright-node"

    def test_none_e2e_runner_has_no_runtime(self, monkeypatch, tmp_path):
        _write_profiles(tmp_path, _MINI_PROFILES)
        _write_config(tmp_path, {"e2e_runner": "none"})
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        assert layout["e2e_runtime"] is None
        assert layout["e2e_test_dir"] is None
        assert layout["e2e_key"] == "none"

    def test_unknown_e2e_runner_falls_back_to_default_layout(self, monkeypatch, tmp_path):
        _write_profiles(tmp_path, _MINI_PROFILES)
        _write_config(tmp_path, {"e2e_runner": "cucumber-selenium"})
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        # Falls back to the hardcoded _DEFAULT e2e_* values rather than crashing.
        assert layout["e2e_runtime"] == "python"
        assert layout["e2e_test_dir"] == "tests/e2e/"

    def test_missing_stack_profiles_json_falls_back_to_default(self, monkeypatch, tmp_path):
        _write_config(tmp_path, {"e2e_runner": "playwright-node"})  # no stack_profiles.json written
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        assert layout["e2e_runtime"] == "python"
        assert layout["e2e_test_dir"] == "tests/e2e/"

    def test_e2e_notes_and_file_ext_present_for_node(self, monkeypatch, tmp_path):
        _write_profiles(tmp_path, _MINI_PROFILES)
        _write_config(tmp_path, {"e2e_runner": "playwright-node"})
        sl = _load_stack_layout(monkeypatch, tmp_path)
        layout = sl.resolve_layout()
        assert layout["e2e_notes"] == "node notes"
        assert layout["e2e_file_ext"] == ".spec.ts"


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(pytest.main([__file__, "-v"]))
