"""
tests/test_llm_cache.py — Tests for the opt-in on-disk LLM response cache.

Covers: LLM_CACHE_ENABLED default/parsing, cache key composition (model /
messages / tools sensitivity + canonicalization), cache miss-then-hit
behavior inside _call_api_with_fallback (no second provider call on a hit),
_SESSION_COSTS isolation from cache hits (_CACHE_STATS instead), and disk
persistence across a fresh process import. No live API calls are made —
same mocking style as tests/test_llm_resilience.py.

Run with:
    python3 -m pytest tests/test_llm_cache.py -v
"""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_response(content="ok", model="deepseek-v4-pro", prompt_tokens=10, completion_tokens=5):
    """Minimal object that looks like an OpenAI ChatCompletion response."""
    msg    = SimpleNamespace(role="assistant", content=content, tool_calls=None)
    usage  = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def _load_harness(monkeypatch, tmp_path: Path, extra_env: dict = None):
    """Import a fresh harness module chdir'd into an isolated tmp_path, so
    LLM_CACHE_DIR's default (progress/.llm_cache) and any cache writes never
    touch the real repo. Mirrors tests/test_harness_core.py's _load_harness."""
    monkeypatch.chdir(tmp_path)
    env = {
        "DEEPSEEK_API_KEY":   "sk-test-deepseek",
        "LLM_FALLBACK_CHAIN": "deepseek",
        "LLM_MODEL_MAP":      "{}",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    fake_openai = MagicMock()
    fake_client = MagicMock()
    fake_openai.return_value = fake_client
    monkeypatch.setitem(sys.modules, "openai", MagicMock(OpenAI=fake_openai))

    for mod in ["dotenv", "rich", "rich.console", "rich.panel", "rich.table",
                "rich.markdown", "playwright", "playwright.sync_api",
                "agents.leader", "agents.implementer", "agents.reviewer",
                "agents.e2e_tester", "agents.spec_writer"]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    for key in list(sys.modules.keys()):
        if key == "harness" or key.startswith("harness."):
            del sys.modules[key]

    harness_root = Path(__file__).parent.parent
    if str(harness_root) not in sys.path:
        sys.path.insert(0, str(harness_root))

    h = importlib.import_module("harness")
    h.console = MagicMock()
    return h


def _setup_providers(harness, provider_mocks: list):
    """Replace harness._PROVIDERS with controlled mock providers (identical
    helper to TestCallApiWithFallback._setup_providers in test_llm_resilience.py)."""
    providers = []
    for name, mock_client in provider_mocks:
        p = MagicMock()
        p.name = name
        p.client = mock_client
        p.resolve_model = lambda m, _n=name: m
        providers.append(p)
    harness._PROVIDERS = providers


# ── LLM_CACHE_ENABLED default/parsing ─────────────────────────────────────────

class TestLlmCacheEnabledDefault:
    def test_default_is_disabled(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h.LLM_CACHE_ENABLED is False
        assert h._llm_cache_enabled() is False

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes"])
    def test_truthy_values_enable(self, monkeypatch, tmp_path, value):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": value})
        assert h.LLM_CACHE_ENABLED is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no"])
    def test_falsy_values_disable(self, monkeypatch, tmp_path, value):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": value})
        assert h.LLM_CACHE_ENABLED is False

    def test_cache_dir_default_under_progress(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h.LLM_CACHE_DIR == str(Path(h.PROGRESS_DIR) / ".llm_cache")

    def test_cache_dir_override(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "somewhere_else")
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_DIR": custom})
        assert h.LLM_CACHE_DIR == custom


# ── _llm_cache_key ─────────────────────────────────────────────────────────────

class TestLlmCacheKey:
    @pytest.fixture(autouse=True)
    def harness(self, monkeypatch, tmp_path):
        self.h = _load_harness(monkeypatch, tmp_path)

    def _msgs(self):
        return [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    def test_identical_inputs_same_key(self):
        k1 = self.h._llm_cache_key("deepseek-v4-pro", self._msgs(), [])
        k2 = self.h._llm_cache_key("deepseek-v4-pro", self._msgs(), [])
        assert k1 == k2

    def test_different_model_different_key(self):
        k1 = self.h._llm_cache_key("deepseek-v4-pro", self._msgs(), [])
        k2 = self.h._llm_cache_key("gpt-4o", self._msgs(), [])
        assert k1 != k2

    def test_different_messages_different_key(self):
        k1 = self.h._llm_cache_key("m", self._msgs(), [])
        k2 = self.h._llm_cache_key("m", [{"role": "user", "content": "different"}], [])
        assert k1 != k2

    def test_different_tools_different_key(self):
        k1 = self.h._llm_cache_key("m", self._msgs(), [])
        k2 = self.h._llm_cache_key("m", self._msgs(), [{"type": "function", "function": {"name": "f"}}])
        assert k1 != k2

    def test_none_and_empty_tools_equivalent(self):
        k1 = self.h._llm_cache_key("m", self._msgs(), None)
        k2 = self.h._llm_cache_key("m", self._msgs(), [])
        assert k1 == k2

    def test_dict_key_order_does_not_matter(self):
        m1 = [{"role": "user", "content": "hi"}]
        m2 = [{"content": "hi", "role": "user"}]
        assert self.h._llm_cache_key("m", m1, []) == self.h._llm_cache_key("m", m2, [])

    def test_same_canonical_model_different_provider_resolution_differs(self):
        # LLM_MODEL_MAP can resolve "deepseek-v4-pro" -> "gpt-4o" on openai but
        # leave it unchanged on deepseek; the two resolved strings must hash
        # differently since they're genuinely different models being called.
        k_deepseek = self.h._llm_cache_key("deepseek-v4-pro", self._msgs(), [])
        k_openai_resolved = self.h._llm_cache_key("gpt-4o", self._msgs(), [])
        assert k_deepseek != k_openai_resolved


# ── _call_api_with_fallback caching behavior ──────────────────────────────────

class TestCallApiWithFallbackCaching:
    def test_disabled_by_default_always_calls_provider(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)  # LLM_CACHE_ENABLED unset -> false
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response()
        _setup_providers(h, [("deepseek", mock_client)])

        h._call_api_with_fallback("model", [{"role": "user", "content": "hi"}], [], "test")
        h._call_api_with_fallback("model", [{"role": "user", "content": "hi"}], [], "test")

        assert mock_client.chat.completions.create.call_count == 2

    def test_miss_then_hit_second_call_skips_provider(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("hello there")
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

        first = h._call_api_with_fallback("model", messages, [], "test")
        second = h._call_api_with_fallback("model", messages, [], "test")

        assert mock_client.chat.completions.create.call_count == 1
        assert first.choices[0].message.content == "hello there"
        assert second.choices[0].message.content == "hello there"

    def test_cache_hit_response_has_no_usage_to_avoid_double_tracking(self, monkeypatch, tmp_path):
        # run_agent/run_leader unconditionally call
        # _track_usage(role, api_response.usage, ...) on whatever this
        # returns — a cache hit must come back with usage=None so that
        # call, made without knowing it hit the cache, is a no-op.
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response()
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]

        h._call_api_with_fallback("model", messages, [], "implementer")
        second = h._call_api_with_fallback("model", messages, [], "implementer")

        assert second.usage is None

    def test_cache_hit_does_not_pollute_session_costs(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response(
            prompt_tokens=100, completion_tokens=50
        )
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]

        first = h._call_api_with_fallback("model", messages, [], "implementer")
        h._track_usage("implementer", first.usage, getattr(first, "model", None))
        before = dict(h._SESSION_COSTS["implementer"])

        second = h._call_api_with_fallback("model", messages, [], "implementer")
        # Mirrors what run_agent actually does with the returned object —
        # must be harmless on a cache hit.
        h._track_usage("implementer", second.usage, getattr(second, "model", None))
        after = h._SESSION_COSTS["implementer"]

        assert after == before  # unchanged: no phantom spend recorded
        assert h._CACHE_STATS["implementer"]["hits"] == 1
        assert h._CACHE_STATS["implementer"]["prompt_tokens_saved"] == 100
        assert h._CACHE_STATS["implementer"]["completion_tokens_saved"] == 50
        assert h._CACHE_STATS["implementer"]["savings_usd"] > 0

    def test_cache_logs_cache_hit_event(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response()
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]

        logged = []
        monkeypatch.setattr(h, "_log", lambda role, event, detail="", level="info": logged.append(event))

        h._call_api_with_fallback("model", messages, [], "test")
        h._call_api_with_fallback("model", messages, [], "test")

        assert "CACHE_HIT" in logged

    def test_different_messages_are_separate_cache_entries(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _fake_response("first"), _fake_response("second"),
        ]
        _setup_providers(h, [("deepseek", mock_client)])

        r1 = h._call_api_with_fallback("model", [{"role": "user", "content": "A"}], [], "test")
        r2 = h._call_api_with_fallback("model", [{"role": "user", "content": "B"}], [], "test")

        assert mock_client.chat.completions.create.call_count == 2
        assert r1.choices[0].message.content == "first"
        assert r2.choices[0].message.content == "second"

    def test_different_models_are_separate_cache_entries(self, monkeypatch, tmp_path):
        # Same messages, different resolved model -> must not collide, since
        # a different model can legitimately answer differently.
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _fake_response("pro answer"), _fake_response("flash answer"),
        ]
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]

        r1 = h._call_api_with_fallback("deepseek-v4-pro", messages, [], "test")
        r2 = h._call_api_with_fallback("deepseek-v4-flash", messages, [], "test")

        assert mock_client.chat.completions.create.call_count == 2
        assert r1.choices[0].message.content == "pro answer"
        assert r2.choices[0].message.content == "flash answer"

    def test_tool_calls_round_trip_through_cache(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        tc = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(name="write_file", arguments='{"path": "a.py"}'),
        )
        msg = SimpleNamespace(role="assistant", content=None, tool_calls=[tc])
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=msg)],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            model="deepseek-v4-pro",
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = response
        _setup_providers(h, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]

        h._call_api_with_fallback("model", messages, [], "test")
        second = h._call_api_with_fallback("model", messages, [], "test")

        assert mock_client.chat.completions.create.call_count == 1
        cached_tc = second.choices[0].message.tool_calls[0]
        assert cached_tc.function.name == "write_file"
        assert cached_tc.function.arguments == '{"path": "a.py"}'

    def test_provider_failure_does_not_write_cache_entry(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("401 unauthorized")
        _setup_providers(h, [("deepseek", mock_client)])

        with patch("time.sleep"):
            result = h._call_api_with_fallback("model", [{"role": "user", "content": "hi"}], [], "test")

        assert result is None
        assert h._llm_cache_entry_count() == 0


# ── Disk persistence across a fresh process import ────────────────────────────

class TestLlmCacheDiskPersistence:
    def test_survives_fresh_module_import(self, monkeypatch, tmp_path):
        h1 = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("persisted answer")
        _setup_providers(h1, [("deepseek", mock_client)])
        messages = [{"role": "user", "content": "hi"}]
        h1._call_api_with_fallback("model", messages, [], "test")

        assert h1._llm_cache_entry_count() == 1

        # Fresh process-equivalent import — a brand-new module object with its
        # own empty in-memory state — reading the same on-disk directory
        # (same tmp_path/cwd, LLM_CACHE_DIR resolves to the same default path).
        h2 = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client2 = MagicMock()  # must never be called: cache should hit
        _setup_providers(h2, [("deepseek", mock_client2)])

        result = h2._call_api_with_fallback("model", messages, [], "test")

        mock_client2.chat.completions.create.assert_not_called()
        assert result.choices[0].message.content == "persisted answer"

    def test_cache_file_written_as_json_per_entry(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response("x")
        _setup_providers(h, [("deepseek", mock_client)])
        h._call_api_with_fallback("model", [{"role": "user", "content": "hi"}], [], "test")

        cache_dir = Path(h.LLM_CACHE_DIR)
        files = list(cache_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["message"]["content"] == "x"
        assert "usage" in data


# ── /cache REPL helpers ────────────────────────────────────────────────────────

class TestCacheHelpers:
    def test_clear_llm_cache_removes_all_entries(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"LLM_CACHE_ENABLED": "true"})
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _fake_response("a"), _fake_response("b"),
        ]
        _setup_providers(h, [("deepseek", mock_client)])
        h._call_api_with_fallback("model", [{"role": "user", "content": "A"}], [], "test")
        h._call_api_with_fallback("model", [{"role": "user", "content": "B"}], [], "test")
        assert h._llm_cache_entry_count() == 2

        removed = h._clear_llm_cache()

        assert removed == 2
        assert h._llm_cache_entry_count() == 0

    def test_clear_on_nonexistent_dir_is_a_noop(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)  # cache disabled, dir never created
        assert h._clear_llm_cache() == 0
        assert h._llm_cache_entry_count() == 0
