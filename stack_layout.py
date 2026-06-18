"""
stack_layout.py — Single source of truth for per-stack file layout.

Resolves which directories an agent may write to (`safe_write_dirs`), which
directories get their file tree pre-injected into agent tasks
(`code_tree_dirs`), and the active stack's test/server commands and prose
directory map (`dirs`) — all from ONE data file (`stack_profiles.json`)
instead of three independent, driftable places (see ARCHITECTURE_REVIEW.md
"single source of truth" finding in the premium repo for the diagnosis).

This module is pure data plumbing: no plugin imports, no side effects beyond
reading two JSON files and env vars. It is safe to import at the very top of
tools.py, before any plugin has had a chance to load — that ordering matters
because tools.py computes the write-guard at import time, earlier than the
plugin system initializes.

Resolution order (highest precedence first):
  1. SAFE_WRITE_DIRS / CODE_TREE_DIRS env vars (emergency override, kept for
     ops — not the normal way to configure a stack).
  2. stack_config.json's "backend" key (or STACK_BACKEND env var, read the
     same way the stack_profile plugin reads it) looked up in
     stack_profiles.json.
  3. _DEFAULT — hardcoded last-resort fallback (python-fastapi shape), used
     when stack_profiles.json is missing, corrupt, or the resolved backend
     key isn't present in it.

Never raises. Any failure reading either JSON file falls back to _DEFAULT
with a logged warning, so a malformed config can never crash the harness.

E2E RESOLUTION:
The same pattern used for backend/frontend now also resolves the e2e runner,
so agents that need to know "is this Python or Node E2E, and where do the
test files live" (the E2E_TESTER, mainly) don't have to guess from a
hardcoded tests/e2e/*.py assumption. Resolution order:
  1. STACK_E2E env var.
  2. stack_config.json's "e2e_runner" key.
  3. stack_profiles.json's "defaults.e2e_runner".
  4. Hardcoded fallback: "playwright" (Python/pytest-playwright).
Looked up in stack_profiles.json's "e2e_runner" map, exposed on the returned
layout dict as e2e_runtime ("python"/"node"/None), e2e_test_dir, e2e_file_ext,
e2e_run_cmd, e2e_notes, and e2e_key (the resolved profile key itself).

PLACEHOLDER SUBSTITUTION:
Some stack profiles (e.g. python-django, which has no fixed source-root
convention like FastAPI's src/) use a generic "<app>" placeholder instead of
a literal directory name inside safe_write_dirs / code_tree_dirs / dirs.
"<app>" is resolved to a concrete app_name with this precedence:
  1. APP_NAME env var.
  2. stack_config.json's "app_name" key.
  3. Hardcoded default: "app".
The substitution always runs (even profiles without "<app>" are unaffected,
since str.replace is a no-op when the placeholder isn't present), so adding
"<app>" to a future profile never requires touching this resolution logic.
"""

import functools
import json
import logging
import os

_log = logging.getLogger(__name__)

_DEFAULT: dict = {
    "safe_write_dirs": (
        "src/", "tests/", "data/", "progress/", "docs/",
        "tests/e2e/", "tests/screenshots/", "frontend/",
    ),
    "code_tree_dirs": ("src", "frontend/src", "tests"),
    "test_runner":    "python3 -m pytest tests/ -v --tb=short",
    "server_cmd":     "python3 -m uvicorn src.main:app --port 8000",
    "dirs":           "src/... (default fallback — stack_profiles.json not found or unreadable)",
    "db_family":      "asyncpg",
    "backend_key":    "python-fastapi",
    "frontend_key":   "react-tailwind",
    "e2e_runtime":    "python",
    "e2e_test_dir":   "tests/e2e/",
    "e2e_file_ext":   ".py",
    "e2e_run_cmd":    "python3 -m pytest tests/e2e/ -v --tb=short",
    "e2e_notes":      "",
    "e2e_key":        "playwright",
}


def _substitute_placeholder(value, app_name: str):
    """
    Replace the literal "<app>" placeholder with app_name inside value.
    Handles str, and recursively list/tuple (preserving the original type).
    Any other type is returned unchanged. A value without "<app>" passes
    through untouched — calling this on profiles that never use the
    placeholder is always a safe no-op.
    """
    if isinstance(value, str):
        return value.replace("<app>", app_name)
    if isinstance(value, tuple):
        return tuple(_substitute_placeholder(v, app_name) for v in value)
    if isinstance(value, list):
        return [_substitute_placeholder(v, app_name) for v in value]
    return value


@functools.lru_cache(maxsize=1)
def resolve_layout() -> dict:
    """
    Single source of truth for stack-dependent file layout.

    Returns a dict with keys: safe_write_dirs (tuple[str]), code_tree_dirs
    (tuple[str]), test_runner (str), server_cmd (str), dirs (str),
    db_family (str), backend_key (str), frontend_key (str), app_name (str),
    e2e_runtime (str | None), e2e_test_dir (str | None),
    e2e_file_ext (str | None), e2e_run_cmd (str | None), e2e_notes (str),
    e2e_key (str). See "E2E RESOLUTION" above for how the e2e_* keys resolve.

    Cached after first call — the stack doesn't change mid-run, and re-reading
    two JSON files on every call would be wasted I/O. Call
    resolve_layout.cache_clear() in tests that need to re-resolve.
    """
    layout = dict(_DEFAULT)
    app_name = "app"  # last-resort default, see PLACEHOLDER SUBSTITUTION above

    try:
        cfg = (
            json.load(open("stack_config.json", encoding="utf-8"))
            if os.path.exists("stack_config.json") else {}
        )
        prof = (
            json.load(open("stack_profiles.json", encoding="utf-8"))
            if os.path.exists("stack_profiles.json") else {}
        )

        backend_defaults = prof.get("defaults", {})
        bkey = os.getenv("STACK_BACKEND") or cfg.get(
            "backend", backend_defaults.get("backend", "python-fastapi")
        )
        bkey = bkey.strip().lower()

        fkey = os.getenv("STACK_FRONTEND") or cfg.get(
            "frontend", backend_defaults.get("frontend", "react-tailwind")
        )
        fkey = fkey.strip().lower()

        ekey = os.getenv("STACK_E2E") or cfg.get(
            "e2e_runner", backend_defaults.get("e2e_runner", "playwright")
        )
        ekey = ekey.strip().lower()

        app_name = (os.getenv("APP_NAME") or cfg.get("app_name") or app_name).strip() or "app"

        entry = prof.get("backend", {}).get(bkey)
        if entry:
            layout.update({
                "safe_write_dirs": _substitute_placeholder(tuple(entry["safe_write_dirs"]), app_name),
                "code_tree_dirs":  _substitute_placeholder(tuple(entry["code_tree_dirs"]), app_name),
                "test_runner":     entry["test_runner"],
                "server_cmd":      entry["server_cmd"],
                "dirs":            _substitute_placeholder(entry["dirs"], app_name),
                "db_family":       entry.get("db_family", "none"),
                "backend_key":     bkey,
                "frontend_key":    fkey,
            })
        else:
            _log.warning(
                "stack_layout: backend %r not found in stack_profiles.json — "
                "falling back to default layout", bkey,
            )

        e2e_entry = prof.get("e2e_runner", {}).get(ekey)
        if e2e_entry:
            layout.update({
                "e2e_runtime":  e2e_entry.get("runtime"),
                "e2e_test_dir": e2e_entry.get("test_dir"),
                "e2e_file_ext": e2e_entry.get("file_ext"),
                "e2e_run_cmd":  e2e_entry.get("run_cmd"),
                "e2e_notes":    e2e_entry.get("notes", ""),
                "e2e_key":      ekey,
            })
        else:
            _log.warning(
                "stack_layout: e2e_runner %r not found in stack_profiles.json — "
                "falling back to default e2e layout", ekey,
            )
    except Exception as exc:
        _log.warning("stack_layout: falling back to default layout (%s)", exc)

    layout["app_name"] = app_name

    # Emergency env overrides still win — kept for ops, not the normal path.
    if os.environ.get("SAFE_WRITE_DIRS"):
        layout["safe_write_dirs"] = tuple(
            d.strip() for d in os.environ["SAFE_WRITE_DIRS"].split(",") if d.strip()
        )
    if os.environ.get("CODE_TREE_DIRS"):
        layout["code_tree_dirs"] = tuple(
            d.strip() for d in os.environ["CODE_TREE_DIRS"].split(",") if d.strip()
        )

    return layout
