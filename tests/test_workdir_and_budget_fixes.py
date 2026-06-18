"""
tests/test_workdir_and_budget_fixes.py — Tests for the 2026-06-18 follow-up fix.

Background: a grep-hallucination fix (see test_harness_core.py::TestTools) was
shipped first, but Felipe flagged that two other suspected root causes of
`max_iter` exhaustion were still untouched:

  1. WORKING DIRECTORY contradiction — every spawn_* task injected the host
     absolute cwd and told agents to `cd <WORKING_DIR>` / "use it in EVERY
     bash command", which is wrong under SANDBOX_MODE=docker (run_bash's
     project root is bind-mounted at /workspace; the host path doesn't exist
     inside the container at all). Fixed via a single `_workdir_banner()`
     helper used by all 4 spawn_* functions, plus matching edits in the 4
     agents/*.py prompts and in spawn_reviewer's validation_mode templates.
  2. budget-checkpoint rules were purely advisory prompt text (only in
     agents/e2e_tester.py) with no code-level enforcement. Fixed via a
     generic, role-agnostic reminder injected into run_agent's own messages
     list at ~60%/~85% of max_iter, plus a final-iteration warning.

Also covers a related tools.py fix: read_file/write_file/list_files/
append_file run on the host (never inside the Docker sandbox), so a path
mistakenly prefixed with "/workspace/" (echoing the run_bash-only convention)
must still resolve correctly against SAFE_WRITE_DIRS instead of being
rejected.

No live API calls — _call_api_with_fallback is patched throughout.

Run with:
    python3 -m pytest tests/test_workdir_and_budget_fixes.py -v
"""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_harness(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "progress").mkdir(exist_ok=True)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "deepseek")
    monkeypatch.setenv("LLM_MODEL_MAP", "{}")

    for mod in ["openai", "dotenv", "rich", "rich.console", "rich.panel",
                "rich.table", "rich.markdown", "playwright", "playwright.sync_api",
                "agents.leader", "agents.implementer", "agents.reviewer",
                "agents.e2e_tester", "agents.spec_writer"]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    fake_openai_mod = MagicMock()
    fake_openai_mod.OpenAI.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

    for key in list(sys.modules.keys()):
        if key == "harness" or key.startswith("harness."):
            del sys.modules[key]

    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    h = importlib.import_module("harness")
    h.console = MagicMock()
    return h


def _load_tools(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "feature_list.json").write_text(json.dumps([
        {"id": 1, "title": "T", "status": "pending", "description": "d",
         "e2e": False, "depends_on": []}
    ]))
    monkeypatch.setitem(sys.modules, "sandbox", MagicMock())
    for key in list(sys.modules.keys()):
        if key == "tools":
            del sys.modules[key]
    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return importlib.import_module("tools")


def _fake_tool_call(call_id: str, name: str, args_json: str):
    fn = SimpleNamespace(name=name, arguments=args_json)
    return SimpleNamespace(id=call_id, function=fn)


def _fake_response(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice], usage=usage)


def _looping_tool_call_response(i: int):
    return _fake_response(
        content=None,
        tool_calls=[_fake_tool_call(f"c{i}", "run_bash", '{"command": "ls"}')],
    )


# ── tools.py: _is_safe_path / workspace prefix ────────────────────────────────

class TestSafePathWorkspacePrefix:
    def test_workspace_prefixed_path_resolves_like_relative(self, monkeypatch, tmp_path):
        t = _load_tools(monkeypatch, tmp_path)
        # Agents told elsewhere that /workspace is the run_bash project root
        # sometimes generalize that to read_file/write_file too — make sure
        # it's still recognized as the safe relative path it actually means.
        assert t._is_safe_path("/workspace/src/models/user.py") is True

    def test_bare_workspace_root_resolves_to_cwd(self, monkeypatch, tmp_path):
        t = _load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("/workspace") is False  # "." is not itself inside a safe dir
        # Note: SAFE_WRITE_DIRS entries have a trailing slash (e.g. "src/"), so a
        # bare dir name with no subpath was already False before this fix too —
        # this isn't something the /workspace stripping changes, just confirming
        # the stripped path is handled identically to the equivalent relative one.
        assert t._is_safe_path("/workspace/src") == t._is_safe_path("src")
        assert t._is_safe_path("/workspace/src/foo.py") is True

    def test_workspace_prefixed_traversal_still_blocked(self, monkeypatch, tmp_path):
        t = _load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("/workspace/src/../../etc/passwd") is False

    def test_workspace_prefixed_path_outside_safe_dirs_still_rejected(self, monkeypatch, tmp_path):
        t = _load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("/workspace/secrets/api_key.txt") is False

    def test_plain_relative_path_unaffected(self, monkeypatch, tmp_path):
        t = _load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("src/models/user.py") is True


# ── harness.py: _workdir_banner ───────────────────────────────────────────────

class TestWorkdirBanner:
    def test_no_cd_instruction(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        banner = harness._workdir_banner("/Users/felipe/BioVet")
        assert "cd <WORKING_DIR>" not in banner
        assert "cd /Users/felipe/BioVet" not in banner

    def test_states_run_bash_starts_at_project_root(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        banner = harness._workdir_banner("/Users/felipe/BioVet")
        assert "already starts in the project root" in banner

    def test_warns_run_bash_is_stateless_across_calls(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        banner = harness._workdir_banner("/Users/felipe/BioVet")
        assert "independent" in banner
        assert "does NOT" in banner

    def test_spawn_implementer_task_uses_banner_not_old_text(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        captured = {}

        def _capture_run_agent(system_prompt, tools, task, **kwargs):
            captured["task"] = task
            return "progress/impl_1.md"

        harness.impl_cfg = SimpleNamespace(SYSTEM_PROMPT="sys", TOOLS=[])
        with patch.object(harness, "run_agent", side_effect=_capture_run_agent), \
             patch.object(harness, "_fire_transform",
                           side_effect=lambda *a, **kw: {"system_prompt": kw["system_prompt"], "task": kw["task"]}), \
             patch.object(harness, "_file_tree", return_value="(empty)"), \
             patch.object(harness, "_load_project_architecture", return_value=""), \
             patch.object(harness, "_layout_context", return_value=""):
            harness.spawn_implementer(1, "do the thing")

        assert "All bash commands must be run from this directory." not in captured["task"]
        assert "cd <WORKING_DIR>" not in captured["task"]
        assert "already starts in the project root" in captured["task"]


# ── harness.py: spawn_reviewer validation_mode templates ─────────────────────

class TestReviewerValidationModeNoCd:
    def test_lightweight_mode_has_no_cd(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        captured = {}

        def _capture_run_agent(system_prompt, tools, task, **kwargs):
            captured["task"] = task
            return "APPROVED"

        harness.reviewer_cfg = SimpleNamespace(SYSTEM_PROMPT="sys", TOOLS=[])
        harness._LAYOUT = {"test_runner": "pytest -q", "dirs": "", "server_cmd": ""}
        with patch.object(harness, "run_agent", side_effect=_capture_run_agent), \
             patch.object(harness, "_fire_transform",
                           side_effect=lambda *a, **kw: {"system_prompt": kw["system_prompt"], "task": kw["task"]}), \
             patch.object(harness, "_file_tree", return_value="(empty)"), \
             patch.object(harness, "_load_project_architecture", return_value=""), \
             patch.object(harness, "_layout_context", return_value=""):
            harness.spawn_reviewer(1, e2e=False)

        assert "cd <WORKING_DIR>" not in captured["task"]
        assert 'run_bash("pytest -q")' in captured["task"]

    def test_full_e2e_mode_has_no_cd(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        captured = {}

        def _capture_run_agent(system_prompt, tools, task, **kwargs):
            captured["task"] = task
            return "APPROVED"

        harness.reviewer_cfg = SimpleNamespace(SYSTEM_PROMPT="sys", TOOLS=[])
        harness._LAYOUT = {"test_runner": "pytest -q", "dirs": "", "server_cmd": ""}
        with patch.object(harness, "run_agent", side_effect=_capture_run_agent), \
             patch.object(harness, "_fire_transform",
                           side_effect=lambda *a, **kw: {"system_prompt": kw["system_prompt"], "task": kw["task"]}), \
             patch.object(harness, "_file_tree", return_value="(empty)"), \
             patch.object(harness, "_load_project_architecture", return_value=""), \
             patch.object(harness, "_layout_context", return_value=""):
            harness.spawn_reviewer(1, e2e=True)

        assert "cd <WORKING_DIR>" not in captured["task"]
        assert 'run_bash("pytest -q")' in captured["task"]


# ── harness.py: run_agent budget-checkpoint enforcement ───────────────────────

class TestBudgetCheckpointEnforcement:
    def test_reminder_injected_at_60_and_85_percent(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        responses = [_looping_tool_call_response(i) for i in range(10)]
        with patch.object(harness, "_call_api_with_fallback", side_effect=responses) as mock_call, \
             patch.object(harness, "execute_tool", return_value=json.dumps({"stdout": "ok"})):
            result = harness.run_agent("sys", [], "task", role="implementer", max_iter=10)

        assert result.startswith("[ERROR: max_iter")

        msgs_at_call_6 = mock_call.call_args_list[6].kwargs["messages"]
        assert any("BUDGET CHECKPOINT" in m.get("content", "") for m in msgs_at_call_6
                   if isinstance(m, dict))
        assert any("6/10" in m.get("content", "") for m in msgs_at_call_6 if isinstance(m, dict))

        msgs_at_call_8 = mock_call.call_args_list[8].kwargs["messages"]
        assert any("BUDGET CHECKPOINT" in m.get("content", "") for m in msgs_at_call_8
                   if isinstance(m, dict))

    def test_final_iteration_warning_injected(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        responses = [_looping_tool_call_response(i) for i in range(10)]
        with patch.object(harness, "_call_api_with_fallback", side_effect=responses) as mock_call, \
             patch.object(harness, "execute_tool", return_value=json.dumps({"stdout": "ok"})):
            harness.run_agent("sys", [], "task", role="implementer", max_iter=10)

        msgs_at_final_call = mock_call.call_args_list[9].kwargs["messages"]
        assert any("FINAL ITERATION" in m.get("content", "") for m in msgs_at_final_call
                   if isinstance(m, dict))

    def test_no_reminder_noise_when_agent_finishes_early(self, monkeypatch, tmp_path):
        # An agent that returns a verdict on iteration 0 should never see any
        # budget-checkpoint text — the mechanism must not fire on attempts
        # that never get anywhere near their iteration budget.
        harness = _load_harness(monkeypatch, tmp_path)
        with patch.object(harness, "_call_api_with_fallback",
                           return_value=_fake_response(content="done")) as mock_call:
            result = harness.run_agent("sys", [], "task", role="implementer", max_iter=10)

        assert result == "done"
        sent_messages = mock_call.call_args.kwargs["messages"]
        assert not any("BUDGET CHECKPOINT" in m.get("content", "") for m in sent_messages
                       if isinstance(m, dict))

    def test_applies_uniformly_to_every_role(self, monkeypatch, tmp_path):
        # The mechanism is role-agnostic by design — no special-casing for
        # e2e_tester (which already had advisory prompt text) vs the other 3
        # roles (which had none at all).
        harness = _load_harness(monkeypatch, tmp_path)
        for role in ["implementer", "reviewer", "spec_writer", "e2e_tester"]:
            responses = [_looping_tool_call_response(i) for i in range(10)]
            with patch.object(harness, "_call_api_with_fallback", side_effect=responses) as mock_call, \
                 patch.object(harness, "execute_tool", return_value=json.dumps({"stdout": "ok"})):
                harness.run_agent("sys", [], "task", role=role, max_iter=10)
            msgs_at_call_6 = mock_call.call_args_list[6].kwargs["messages"]
            assert any("BUDGET CHECKPOINT" in m.get("content", "") for m in msgs_at_call_6
                       if isinstance(m, dict)), f"role={role} got no budget reminder"
