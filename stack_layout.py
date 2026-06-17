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
}


@functools.lru_cache(maxsize=1)
def resolve_layout() -> dict:
    """
    Single source of truth for stack-dependent file layout.

    Returns a dict with keys: safe_write_dirs (tuple[str]), code_tree_dirs
    (tuple[str]), test_runner (str), server_cmd (str), dirs (str),
    db_family (str), backend_key (str), frontend_key (str).

    Cached after first call — the stack doesn't change mid-run, and re-reading
    two JSON files on every call would be wasted I/O. Call
    resolve_layout.cache_clear() in tests that need to re-resolve.
    """
    layout = dict(_DEFAULT)

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

        entry = prof.get("backend", {}).get(bkey)
        if entry:
            layout.update({
                "safe_write_dirs": tuple(entry["safe_write_dirs"]),
                "code_tree_dirs":  tuple(entry["code_tree_dirs"]),
                "test_runner":     entry["test_runner"],
                "server_cmd":      entry["server_cmd"],
                "dirs":            entry["dirs"],
                "db_family":       entry.get("db_family", "none"),
                "backend_key":     bkey,
                "frontend_key":    fkey,
            })
        else:
            _log.warning(
                "stack_layout: backend %r not found in stack_profiles.json — "
                "falling back to default layout", bkey,
            )
    except Exception as exc:
        _log.warning("stack_layout: falling back to default layout (%s)", exc)

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
