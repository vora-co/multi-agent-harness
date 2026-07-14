"""
tests/test_compaction_resumability.py — Tests for the engine fix that replaced
the LLM-based compaction summary with a deterministic digest, and added
mid-run resumability to run_agent via on-disk message snapshots.

Background (2026-06-18 incident, feature 26 / e2e_tester): the old
_compact_messages asked an LLM to summarize the dropped history in 400 words.
That summary had no guarantee of mentioning "this was already confirmed", so
after every compaction the agent re-explored ground it had already covered,
repeatedly hitting max_iter without ever reaching a verdict. Separately,
run_agent had no way to resume an in-progress conversation after a crash —
the whole attempt's tool-call history was lost and the next run started that
role from scratch.

Covers: _build_deterministic_digest, _message_state_path,
_save_message_state / _load_message_state / _clear_message_state, and
run_agent's checkpoint_key resume/save/clear behavior.

No live API calls are made — _call_api_with_fallback is patched throughout.

Run with:
    python3 -m pytest tests/test_compaction_resumability.py -v
"""

import importlib
import json
import os
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

    return importlib.import_module("harness")


def _fake_tool_call(call_id: str, name: str, args_json: str):
    fn = SimpleNamespace(name=name, arguments=args_json)
    return SimpleNamespace(id=call_id, function=fn)


def _fake_response(content=None, tool_calls=None):
    """Minimal object that looks like an OpenAI ChatCompletion response."""
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice], usage=usage)


# ── _build_deterministic_digest ────────────────────────────────────────────

class TestBuildDeterministicDigest:
    def test_no_llm_call_made(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        with patch.object(harness, "_call_api_with_fallback") as mock_call:
            harness._build_deterministic_digest([{"role": "user", "content": "hi"}])
            mock_call.assert_not_called()

    def test_captures_tool_calls_from_assistant_messages(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = [
            SimpleNamespace(
                role="assistant", content="Reading the spec first.",
                tool_calls=[_fake_tool_call("c1", "read_file", '{"path": "progress/spec_1.md"}')],
            ),
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"content": "spec text"})},
        ]
        digest = harness._build_deterministic_digest(middle)
        assert "read_file" in digest
        assert "spec_1.md" in digest
        assert "Reading the spec first." in digest

    def test_captures_errors_from_tool_results(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = [
            {"role": "tool", "tool_call_id": "c1",
             "content": json.dumps({"error": "file not found: foo.py"})},
        ]
        digest = harness._build_deterministic_digest(middle)
        assert "file not found: foo.py" in digest
        assert "Errors encountered" in digest

    def test_empty_middle_does_not_crash(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        digest = harness._build_deterministic_digest([])
        assert "0 tool call(s)" in digest

    def test_many_tool_calls_truncated_with_omission_note(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = [
            SimpleNamespace(
                role="assistant", content="",
                tool_calls=[_fake_tool_call(f"c{i}", "run_bash", f'{{"cmd": "step{i}"}}')],
            )
            for i in range(40)
        ]
        digest = harness._build_deterministic_digest(middle)
        assert "omitted" in digest
        assert "step39" in digest          # most recent call always kept
        assert "step0" not in digest        # oldest dropped once over the cap


class TestDigestRetainsBoundedContent:
    """
    Regression coverage for feature 74: the digest listed call signatures
    ("read_file(...)") but discarded every result, so "don't repeat this
    call" left the agent nothing to act on except repeating it — confirmed
    on the feature 74 log, where every compaction was followed by re-reading
    the same 3 files. This version keeps bounded, deterministic content per
    result alongside the call list.
    """

    def _read_pair(self, call_id, path, content):
        return [
            SimpleNamespace(
                role="assistant", content="",
                tool_calls=[self._call(call_id, "read_file", json.dumps({"path": path}))],
            ),
            {"role": "tool", "tool_call_id": call_id,
             "content": json.dumps({"content": content, "path": path})},
        ]

    def _call(self, call_id, name, args_json):
        return _fake_tool_call(call_id, name, args_json)

    def test_read_file_excerpt_is_kept(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = self._read_pair("c1", "src/models/user.py", "class User:\n    id: int\n")

        digest = harness._build_deterministic_digest(middle)

        assert "Key file contents already seen" in digest
        assert "src/models/user.py" in digest
        assert "class User" in digest

    def test_only_most_recent_read_of_a_path_is_kept(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = (
            self._read_pair("c1", "src/models/user.py", "OLD CONTENT before edit")
            + self._read_pair("c2", "src/models/user.py", "NEW CONTENT after edit")
        )

        digest = harness._build_deterministic_digest(middle)

        assert "NEW CONTENT after edit" in digest
        assert "OLD CONTENT before edit" not in digest

    def test_read_file_excerpt_is_bounded_to_300_chars(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = self._read_pair("c1", "src/big.py", "x" * 5000)

        digest = harness._build_deterministic_digest(middle)

        assert "x" * 300 in digest
        assert "x" * 301 not in digest

    def test_playwright_output_tail_is_kept(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = [
            SimpleNamespace(
                role="assistant", content="",
                tool_calls=[self._call("c1", "run_playwright_tests", '{"test_path": "tests/e2e/"}')],
            ),
            {"role": "tool", "tool_call_id": "c1",
             "content": json.dumps({"output": "old irrelevant stuff " + "y" * 600 + "TimeoutError: #prof-name"})},
        ]

        digest = harness._build_deterministic_digest(middle)

        assert "Last run_playwright_tests output" in digest
        assert "TimeoutError: #prof-name" in digest  # tail survives
        assert "old irrelevant stuff" not in digest   # head was truncated away

    def test_grep_run_bash_head_lines_are_kept(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        stdout = "\n".join([f"match{i}" for i in range(20)])
        middle = [
            SimpleNamespace(
                role="assistant", content="",
                tool_calls=[self._call("c1", "run_bash", json.dumps({"command": "grep -rn foo src/"}))],
            ),
            {"role": "tool", "tool_call_id": "c1",
             "content": json.dumps({"stdout": stdout, "returncode": 0})},
        ]

        digest = harness._build_deterministic_digest(middle)

        assert "match0" in digest and "match4" in digest
        assert "match5" not in digest  # only first ~5 lines kept

    def test_non_grep_run_bash_is_not_captured(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = [
            SimpleNamespace(
                role="assistant", content="",
                tool_calls=[self._call("c1", "run_bash", json.dumps({"command": "python3 -m pytest tests/"}))],
            ),
            {"role": "tool", "tool_call_id": "c1",
             "content": json.dumps({"stdout": "2 passed", "returncode": 0})},
        ]

        digest = harness._build_deterministic_digest(middle)

        assert "Recent run_bash grep results" not in digest

    def test_digest_is_capped_at_roughly_4kb(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        middle = []
        for i in range(30):
            middle += self._read_pair(f"c{i}", f"src/file_{i}.py", "z" * 300)

        digest = harness._build_deterministic_digest(middle)

        assert len(digest) <= harness._DIGEST_MAX_CHARS + len("\n... [digest truncated at ~4KB cap] ...")
        assert "truncated at ~4KB cap" in digest


# ── _compact_messages no longer calls the LLM ────────────────────────────────

class TestCompactMessagesIsDeterministic:
    def test_compact_messages_never_calls_llm(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
        for i in range(40):
            messages.append({"role": "assistant", "content": f"step {i}"})
            messages.append({"role": "user", "content": f"ack {i}"})

        with patch.object(harness, "_call_api_with_fallback") as mock_call:
            compacted = harness._compact_messages(messages, "implementer")
            mock_call.assert_not_called()

        assert len(compacted) < len(messages)
        assert "deterministic" in compacted[2]["content"]


# ── Message-state snapshot helpers ───────────────────────────────────────────

class TestMessageStatePath:
    def test_sanitizes_unsafe_characters(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        path = harness._message_state_path("e2e_tester/../../etc:passwd")
        assert ".." not in path
        assert "progress" in path
        assert path.endswith(".json")


class TestSaveLoadClearMessageState:
    def test_roundtrip(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "e2e_tester_26_1"
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
            SimpleNamespace(role="assistant", content=None,
                             tool_calls=[_fake_tool_call("c1", "run_bash", '{"cmd": "ls"}')]),
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        harness._save_message_state(key, messages)
        loaded = harness._load_message_state(key)
        assert loaded is not None
        assert loaded[0] == {"role": "system", "content": "sys"}
        assert loaded[2]["role"] == "assistant"
        assert loaded[2]["tool_calls"][0]["function"]["name"] == "run_bash"

        harness._clear_message_state(key)
        assert harness._load_message_state(key) is None

    def test_load_returns_none_when_absent(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        assert harness._load_message_state("nonexistent_key") is None

    def test_load_returns_none_on_corrupt_file(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        path = harness._message_state_path("broken_key")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")
        assert harness._load_message_state("broken_key") is None

    def test_save_is_noop_without_checkpoint_key(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        harness._save_message_state(None, [{"role": "user", "content": "x"}])
        harness._save_message_state("", [{"role": "user", "content": "x"}])
        # Neither call should have created any _state_*.json file.
        assert not any(Path("progress").glob("_state_*.json"))

    def test_clear_is_noop_when_nothing_saved(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        harness._clear_message_state("never_saved_key")  # must not raise


# ── run_agent: checkpoint_key resume / save / clear ──────────────────────────

class TestRunAgentResumability:
    def test_without_checkpoint_key_behaves_as_before(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        with patch.object(harness, "_call_api_with_fallback",
                           return_value=_fake_response(content="done")):
            result = harness.run_agent("sys prompt", [], "do the task", role="implementer")
        assert result == "done"
        assert not any(Path("progress").glob("_state_*.json"))

    def test_resumes_from_saved_snapshot_instead_of_rebuilding(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "implementer_26_1"
        prior_messages = [
            {"role": "system", "content": "sys prompt"},
            {"role": "user", "content": "do the task"},
            {"role": "assistant", "content": "already made progress"},
            {"role": "user", "content": "continue"},
        ]
        harness._save_message_state(key, prior_messages)

        with patch.object(harness, "_call_api_with_fallback",
                           return_value=_fake_response(content="done")) as mock_call:
            result = harness.run_agent("sys prompt", [], "do the task",
                                        role="implementer", checkpoint_key=key)

        assert result == "done"
        sent_messages = mock_call.call_args.kwargs["messages"]
        # The resumed 4-message history was sent, not a fresh 2-message one —
        # proof the prior progress was not discarded and redone.
        assert len(sent_messages) == 4
        assert sent_messages[2]["content"] == "already made progress"

    def test_state_cleared_on_clean_completion(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "spec_writer_5_1"
        with patch.object(harness, "_call_api_with_fallback",
                           return_value=_fake_response(content="path/to/spec.md")):
            harness.run_agent("sys", [], "task", role="spec_writer", checkpoint_key=key)
        assert harness._load_message_state(key) is None

    def test_state_cleared_on_max_iter_exhaustion(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "e2e_tester_9_1"
        looping_call = _fake_response(
            content=None,
            tool_calls=[_fake_tool_call("c1", "run_bash", '{"cmd": "ls"}')],
        )
        with patch.object(harness, "_call_api_with_fallback", return_value=looping_call), \
             patch.object(harness, "execute_tool", return_value=json.dumps({"stdout": "ok"})):
            result = harness.run_agent("sys", [], "task", role="e2e_tester",
                                        max_iter=2, checkpoint_key=key)
        assert result.startswith("[ERROR: max_iter")
        assert harness._load_message_state(key) is None

    def test_state_saved_mid_run_then_cleared_on_finish(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "e2e_tester_9_2"
        tool_call_response = _fake_response(
            content=None,
            tool_calls=[_fake_tool_call("c1", "run_bash", '{"cmd": "ls"}')],
        )
        final_response = _fake_response(content="E2E_PASSED")

        saved_snapshots = []
        real_save = harness._save_message_state

        def _spy_save(checkpoint_key, messages):
            real_save(checkpoint_key, messages)
            if checkpoint_key == key:
                saved_snapshots.append(harness._load_message_state(key))

        with patch.object(harness, "_call_api_with_fallback",
                           side_effect=[tool_call_response, final_response]), \
             patch.object(harness, "execute_tool", return_value=json.dumps({"stdout": "ok"})), \
             patch.object(harness, "_save_message_state", side_effect=_spy_save):
            result = harness.run_agent("sys", [], "task", role="e2e_tester",
                                        max_iter=5, checkpoint_key=key)

        assert result == "E2E_PASSED"
        # A snapshot was written after the first (tool-call) iteration...
        assert len(saved_snapshots) == 1
        assert saved_snapshots[0] is not None
        # ...and cleared once the agent reached a final verdict.
        assert harness._load_message_state(key) is None

    def test_provider_exhaustion_leaves_snapshot_intact_for_retry(self, monkeypatch, tmp_path):
        harness = _load_harness(monkeypatch, tmp_path)
        key = "implementer_3_1"
        prior_messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ]
        harness._save_message_state(key, prior_messages)

        with patch.object(harness, "_call_api_with_fallback", return_value=None):
            result = harness.run_agent("sys", [], "task", role="implementer", checkpoint_key=key)

        assert "all LLM providers exhausted" in result
        # Not a clean return — the snapshot must still be there for a retry
        # with the same checkpoint_key to resume from instead of restarting.
        assert harness._load_message_state(key) is not None
