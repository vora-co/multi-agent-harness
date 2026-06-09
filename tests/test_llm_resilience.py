"""
tests/test_llm_resilience.py — Tests for LLM provider resilience.

Covers: provider chain construction, model resolution, error classification,
_call_api_with_fallback retry/fallback logic. No live API calls are made.

Run with:
    python3 -m pytest tests/test_llm_resilience.py -v
"""

import importlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_response(content="ok"):
    """Minimal object that looks like an OpenAI ChatCompletion response."""
    msg   = SimpleNamespace(content=content, tool_calls=None)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice], usage=usage)


def _api_error(msg: str):
    """Raise an Exception with the given message (simulates an API error)."""
    raise Exception(msg)


def _load_harness(monkeypatch, env: dict = None):
    """
    Import harness with a controlled environment. Patches OpenAI so no real
    HTTP client is created, and injects env vars before import.
    """
    base_env = {
        "DEEPSEEK_API_KEY": "sk-test-deepseek",
        "LLM_FALLBACK_CHAIN": "deepseek",
        "LLM_MODEL_MAP": "{}",
    }
    if env:
        base_env.update(env)

    for k, v in base_env.items():
        monkeypatch.setenv(k, v)

    # Patch OpenAI to avoid real connections
    fake_openai = MagicMock()
    fake_client = MagicMock()
    fake_openai.return_value = fake_client
    monkeypatch.setitem(sys.modules, "openai", MagicMock(OpenAI=fake_openai))

    # Stub out every heavy import the harness needs at module level
    for mod in ["dotenv", "rich", "rich.console", "rich.panel", "rich.table",
                "rich.markdown", "rich", "playwright", "playwright.sync_api",
                "agents.leader", "agents.implementer", "agents.reviewer",
                "agents.e2e_tester", "agents.spec_writer"]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    # Force fresh import
    for key in list(sys.modules.keys()):
        if key == "harness" or key.startswith("harness."):
            del sys.modules[key]

    harness_root = Path(__file__).parent.parent
    if str(harness_root) not in sys.path:
        sys.path.insert(0, str(harness_root))

    return importlib.import_module("harness")


# ── _classify_error ───────────────────────────────────────────────────────────

class TestClassifyError:
    @pytest.fixture(autouse=True)
    def harness(self, monkeypatch):
        self.h = _load_harness(monkeypatch)

    def test_transient_rate_limit(self):
        assert self.h._classify_error("rate limit exceeded") == "TRANSIENT"

    def test_transient_429(self):
        assert self.h._classify_error("HTTP 429 Too Many Requests") == "TRANSIENT"

    def test_transient_timeout(self):
        assert self.h._classify_error("connection timeout") == "TRANSIENT"

    def test_transient_503(self):
        assert self.h._classify_error("503 service unavailable") == "TRANSIENT"

    def test_provider_failure_401(self):
        assert self.h._classify_error("401 unauthorized") == "PROVIDER_FAILURE"

    def test_provider_failure_auth(self):
        assert self.h._classify_error("authentication failed: invalid api key") == "PROVIDER_FAILURE"

    def test_provider_failure_529(self):
        assert self.h._classify_error("529 overloaded") == "PROVIDER_FAILURE"

    def test_provider_failure_capacity(self):
        assert self.h._classify_error("server capacity exceeded") == "PROVIDER_FAILURE"

    def test_logical_error(self):
        assert self.h._classify_error("max_iter reached") == "LOGICAL"

    def test_fatal_fallthrough(self):
        assert self.h._classify_error("some unknown problem") == "FATAL"


# ── _build_provider_chain ─────────────────────────────────────────────────────

class TestBuildProviderChain:
    def test_single_deepseek(self, monkeypatch):
        h = _load_harness(monkeypatch, {"LLM_FALLBACK_CHAIN": "deepseek"})
        assert len(h._PROVIDERS) == 1
        assert h._PROVIDERS[0].name == "deepseek"

    def test_skips_provider_without_key(self, monkeypatch):
        h = _load_harness(monkeypatch, {
            "LLM_FALLBACK_CHAIN": "deepseek,openai",
            # OPENAI_API_KEY intentionally not set
        })
        names = [p.name for p in h._PROVIDERS]
        assert "openai" not in names
        assert "deepseek" in names

    def test_two_providers_when_both_keyed(self, monkeypatch):
        h = _load_harness(monkeypatch, {
            "LLM_FALLBACK_CHAIN": "deepseek,openai",
            "OPENAI_API_KEY": "sk-test-openai",
        })
        names = [p.name for p in h._PROVIDERS]
        assert names == ["deepseek", "openai"]

    def test_order_preserved(self, monkeypatch):
        h = _load_harness(monkeypatch, {
            "LLM_FALLBACK_CHAIN": "deepseek,openai,groq",
            "OPENAI_API_KEY": "sk-openai",
            "GROQ_API_KEY":   "gsk-groq",
        })
        assert [p.name for p in h._PROVIDERS] == ["deepseek", "openai", "groq"]

    def test_fallback_to_deepseek_when_chain_empty(self, monkeypatch):
        h = _load_harness(monkeypatch, {"LLM_FALLBACK_CHAIN": ""})
        assert len(h._PROVIDERS) == 1
        assert h._PROVIDERS[0].name == "deepseek"

    def test_custom_provider(self, monkeypatch):
        h = _load_harness(monkeypatch, {
            "LLM_FALLBACK_CHAIN": "custom",
            "CUSTOM_API_KEY":     "sk-custom",
            "CUSTOM_BASE_URL":    "https://my-llm.example.com/v1",
        })
        assert h._PROVIDERS[0].name == "custom"


# ── _resolve_model ────────────────────────────────────────────────────────────

class TestResolveModel:
    def test_no_map_returns_unchanged(self, monkeypatch):
        h = _load_harness(monkeypatch)
        assert h._resolve_model("deepseek-v4-pro", "openai") == "deepseek-v4-pro"

    def test_mapped_model(self, monkeypatch):
        model_map = json.dumps({
            "deepseek-v4-pro": {"openai": "gpt-4o"},
            "deepseek-v4-flash": {"openai": "gpt-4o-mini"},
        })
        h = _load_harness(monkeypatch, {"LLM_MODEL_MAP": model_map})
        assert h._resolve_model("deepseek-v4-pro", "openai")   == "gpt-4o"
        assert h._resolve_model("deepseek-v4-flash", "openai") == "gpt-4o-mini"

    def test_unmapped_provider_falls_through(self, monkeypatch):
        model_map = json.dumps({"deepseek-v4-pro": {"openai": "gpt-4o"}})
        h = _load_harness(monkeypatch, {"LLM_MODEL_MAP": model_map})
        # groq not in map — should return canonical name unchanged
        assert h._resolve_model("deepseek-v4-pro", "groq") == "deepseek-v4-pro"

    def test_invalid_json_map_treated_as_empty(self, monkeypatch):
        h = _load_harness(monkeypatch, {"LLM_MODEL_MAP": "not-json"})
        assert h._resolve_model("deepseek-v4-pro", "openai") == "deepseek-v4-pro"


# ── _call_api_with_fallback ───────────────────────────────────────────────────

class TestCallApiWithFallback:
    def _setup_providers(self, harness, provider_mocks: list):
        """Replace harness._PROVIDERS with controlled mock providers."""
        providers = []
        for name, mock_client in provider_mocks:
            p = MagicMock()
            p.name = name
            p.client = mock_client
            p.resolve_model = lambda m, _n=name: m
            providers.append(p)
        harness._PROVIDERS = providers

    def test_success_on_first_provider(self, monkeypatch):
        h = _load_harness(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _fake_response()
        self._setup_providers(h, [("deepseek", mock_client)])

        result = h._call_api_with_fallback("model", [], [], "test")

        assert result is not None
        mock_client.chat.completions.create.assert_called_once()

    def test_transient_retries_same_provider(self, monkeypatch):
        h = _load_harness(monkeypatch)
        mock_client = MagicMock()
        # Fail twice with TRANSIENT, succeed on third attempt
        mock_client.chat.completions.create.side_effect = [
            Exception("rate limit exceeded"),
            Exception("rate limit exceeded"),
            _fake_response(),
        ]
        self._setup_providers(h, [("deepseek", mock_client)])

        with patch("time.sleep"):  # don't actually wait
            result = h._call_api_with_fallback("model", [], [], "test")

        assert result is not None
        assert mock_client.chat.completions.create.call_count == 3

    def test_provider_failure_skips_to_next(self, monkeypatch):
        h = _load_harness(monkeypatch)
        primary = MagicMock()
        primary.chat.completions.create.side_effect = Exception("401 unauthorized")
        fallback = MagicMock()
        fallback.chat.completions.create.return_value = _fake_response("fallback ok")
        self._setup_providers(h, [("deepseek", primary), ("openai", fallback)])

        with patch("time.sleep"):
            result = h._call_api_with_fallback("model", [], [], "test")

        assert result is not None
        assert result.choices[0].message.content == "fallback ok"
        # Primary tried once, then gave up immediately on PROVIDER_FAILURE
        primary.chat.completions.create.assert_called_once()

    def test_exhausted_retries_falls_to_next_provider(self, monkeypatch):
        h = _load_harness(monkeypatch)
        primary = MagicMock()
        primary.chat.completions.create.side_effect = Exception("503 service unavailable")
        fallback = MagicMock()
        fallback.chat.completions.create.return_value = _fake_response()
        self._setup_providers(h, [("deepseek", primary), ("openai", fallback)])

        with patch("time.sleep"):
            result = h._call_api_with_fallback("model", [], [], "test")

        assert result is not None
        assert primary.chat.completions.create.call_count == h.MAX_RETRIES_API

    def test_all_providers_exhausted_returns_none(self, monkeypatch):
        h = _load_harness(monkeypatch)
        p1 = MagicMock()
        p1.chat.completions.create.side_effect = Exception("401 unauthorized")
        p2 = MagicMock()
        p2.chat.completions.create.side_effect = Exception("401 unauthorized")
        self._setup_providers(h, [("deepseek", p1), ("openai", p2)])

        with patch("time.sleep"):
            result = h._call_api_with_fallback("model", [], [], "test")

        assert result is None

    def test_second_provider_not_called_on_first_success(self, monkeypatch):
        h = _load_harness(monkeypatch)
        p1 = MagicMock()
        p1.chat.completions.create.return_value = _fake_response()
        p2 = MagicMock()
        self._setup_providers(h, [("deepseek", p1), ("openai", p2)])

        h._call_api_with_fallback("model", [], [], "test")

        p2.chat.completions.create.assert_not_called()
