"""
tests/test_harness_core.py — Unit tests for harness core logic.

Covers gaps not addressed by test_llm_resilience.py / test_resumability.py:

  - Dependency graph  (_topological_sort, _validate_dependencies)
  - Feature list I/O  (_read_feature_list_raw, _write_feature_list_raw)
  - Budget enforcement (_track_usage → _BUDGET_EXCEEDED, run_feature_cycle skip)
  - tools.py          (_is_safe_path, update_feature_status, execute_tool)

No live API calls — all LLM/agent paths are patched.

Run with:
    python3 -m pytest tests/test_harness_core.py -v
"""

import importlib
import json
import logging
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Shared harness loader ─────────────────────────────────────────────────────

def _load_harness(monkeypatch, tmp_path: Path, extra_env: dict = None):
    monkeypatch.chdir(tmp_path)
    env = {
        "DEEPSEEK_API_KEY": "sk-test",
        "LLM_FALLBACK_CHAIN": "deepseek",
        "LLM_MODEL_MAP": "{}",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    for mod in [
        "openai", "dotenv", "rich", "rich.console", "rich.panel",
        "rich.table", "rich.markdown", "playwright", "playwright.sync_api",
        "agents.leader", "agents.implementer", "agents.reviewer",
        "agents.e2e_tester", "agents.spec_writer",
    ]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    fake_openai = MagicMock()
    fake_openai.OpenAI.return_value = MagicMock()
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    for key in list(sys.modules.keys()):
        if key == "harness" or key.startswith("harness."):
            del sys.modules[key]

    root = Path(__file__).parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    h = importlib.import_module("harness")
    h.console = MagicMock()
    return h


def _write_fl(tmp_path: Path, features: list) -> None:
    (tmp_path / "feature_list.json").write_text(json.dumps(features))


# ── _topological_sort ─────────────────────────────────────────────────────────

class TestTopologicalSort:
    """Tests for the Kahn-BFS dependency graph sorter."""

    def _feat(self, fid, deps=None):
        return {"id": fid, "title": f"F{fid}", "depends_on": deps or []}

    def test_no_dependencies_returns_all_ids(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(1), self._feat(2), self._feat(3)]
        ordered, cycles = h._topological_sort(features)
        assert set(ordered) == {1, 2, 3}
        assert cycles == []

    def test_linear_chain_respects_order(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # 1 → 2 → 3 means 1 must come before 2, 2 before 3
        features = [self._feat(1), self._feat(2, [1]), self._feat(3, [2])]
        ordered, cycles = h._topological_sort(features)
        assert cycles == []
        assert ordered.index(1) < ordered.index(2)
        assert ordered.index(2) < ordered.index(3)

    def test_diamond_dependency(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # 1 → 2 & 3 → 4
        features = [
            self._feat(1),
            self._feat(2, [1]),
            self._feat(3, [1]),
            self._feat(4, [2, 3]),
        ]
        ordered, cycles = h._topological_sort(features)
        assert cycles == []
        assert ordered.index(1) < ordered.index(2)
        assert ordered.index(1) < ordered.index(3)
        assert ordered.index(2) < ordered.index(4)
        assert ordered.index(3) < ordered.index(4)

    def test_cycle_detected(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # 1 → 2 → 3 → 1 (cycle)
        features = [self._feat(1, [3]), self._feat(2, [1]), self._feat(3, [2])]
        ordered, cycles = h._topological_sort(features)
        assert set(cycles) == {1, 2, 3}

    def test_partial_cycle_leaves_roots_in_ordered(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # Feature 10 is independent; 1 → 2 → 1 is a cycle
        features = [self._feat(1, [2]), self._feat(2, [1]), self._feat(10)]
        ordered, cycles = h._topological_sort(features)
        assert 10 in ordered
        assert set(cycles) == {1, 2}

    def test_missing_dep_id_ignored_in_sort(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # Feature 2 depends on 99 which doesn't exist — sort should not crash
        features = [self._feat(1), self._feat(2, [99])]
        ordered, cycles = h._topological_sort(features)
        # Both features appear in ordered (missing dep skipped silently)
        assert set(ordered) == {1, 2}
        assert cycles == []

    def test_roots_sorted_ascending(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(5), self._feat(2), self._feat(8)]
        ordered, _ = h._topological_sort(features)
        assert ordered == [2, 5, 8]


# ── _validate_dependencies ────────────────────────────────────────────────────

class TestValidateDependencies:
    def _feat(self, fid, deps=None):
        return {"id": fid, "title": f"F{fid}", "depends_on": deps or []}

    def test_valid_graph_returns_no_errors(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(1), self._feat(2, [1]), self._feat(3, [1, 2])]
        assert h._validate_dependencies(features) == []

    def test_self_dependency_reported(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(1, [1])]
        errors = h._validate_dependencies(features)
        assert any("itself" in e for e in errors)

    def test_missing_dependency_reported(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(1, [999])]
        errors = h._validate_dependencies(features)
        assert any("999" in e for e in errors)

    def test_circular_dependency_reported(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [self._feat(1, [2]), self._feat(2, [1])]
        errors = h._validate_dependencies(features)
        assert any("Circular" in e or "cycle" in e.lower() for e in errors)

    def test_empty_feature_list_valid(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._validate_dependencies([]) == []


# ── _read_feature_list_raw / _write_feature_list_raw ─────────────────────────

class TestFeatureListIO:
    def test_write_then_read_roundtrip(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [{"id": 1, "title": "A", "status": "pending"}]
        h._write_feature_list_raw(features)
        loaded = h._read_feature_list_raw()
        assert loaded[0]["id"] == 1
        assert loaded[0]["title"] == "A"

    def test_read_returns_empty_list_when_missing(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        # feature_list.json does not exist
        result = h._read_feature_list_raw()
        assert result == []

    def test_read_returns_empty_list_on_corrupt_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "feature_list.json").write_text("NOT JSON {{{{")
        result = h._read_feature_list_raw()
        assert result == []

    def test_write_creates_file(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        h._write_feature_list_raw([{"id": 42}])
        assert (tmp_path / "feature_list.json").exists()


# ── FeatureSchema validation ────────────────────────────────────────────────────

class TestFeatureSchema:
    def _feat(self, **overrides):
        base = {
            "id": 1,
            "title": "Some feature",
            "description": "Do the thing.",
            "status": "pending",
            "e2e": False,
            "depends_on": [],
            "created_at": "2026-01-01T00:00:00",
        }
        base.update(overrides)
        return base

    def test_valid_feature_has_no_errors(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        errors = h._validate_feature_schema([self._feat()])
        assert errors == []

    def test_minimal_feature_omitting_optional_fields_is_valid(self, monkeypatch, tmp_path):
        # e2e / depends_on / created_at are optional with defaults — see
        # README "Feature fields" and agents/leader.py ("[e2e] If not
        # present, use false").
        h = _load_harness(monkeypatch, tmp_path)
        minimal = {"id": 1, "title": "T", "description": "D", "status": "pending"}
        errors = h._validate_feature_schema([minimal])
        assert errors == []

    def test_misspelled_field_is_rejected(self, monkeypatch, tmp_path):
        # The whole point of this schema: "depnds_on" must no longer be
        # silently ignored the way a bare dict.get("depends_on", []) would.
        h = _load_harness(monkeypatch, tmp_path)
        feat = self._feat()
        feat["depnds_on"] = [2]
        del feat["depends_on"]

        errors = h._validate_feature_schema([feat])

        assert len(errors) == 1
        assert "depnds_on" in errors[0]
        assert "Feature #1" in errors[0]

    def test_missing_required_field_is_rejected(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        feat = self._feat()
        del feat["title"]

        errors = h._validate_feature_schema([feat])

        assert len(errors) == 1
        assert "title" in errors[0]

    def test_invalid_status_value_is_rejected(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        errors = h._validate_feature_schema([self._feat(status="bogus")])

        assert len(errors) == 1
        assert "status" in errors[0]

    def test_harness_written_fields_are_accepted(self, monkeypatch, tmp_path):
        # updated_at / recovery_note (recover_stale_features, tools.update_feature_status)
        # and _checkpoint (_save_checkpoint) are written by the harness itself,
        # not by feature authors — they must validate cleanly.
        h = _load_harness(monkeypatch, tmp_path)
        feat = self._feat(
            updated_at="2026-01-02T00:00:00",
            recovery_note="Reset to pending by harness on startup (possible previous crash)",
        )
        feat["_checkpoint"] = {"step": "impl_done", "attempt": 2, "saved_at": "2026-01-02T00:00:00"}

        errors = h._validate_feature_schema([feat])
        assert errors == []

    def test_premium_requires_human_gate_field_is_accepted(self, monkeypatch, tmp_path):
        # Premium "Human-in-the-loop gates" module sets this field — the
        # public core must not reject it even though it never reads it.
        h = _load_harness(monkeypatch, tmp_path)
        errors = h._validate_feature_schema([self._feat(requires_human_gate=True)])
        assert errors == []

    def test_multiple_features_report_errors_for_each(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        features = [
            self._feat(id=1),
            self._feat(id=2, status="not_a_real_status"),
            {**self._feat(id=3), "unexpected_field": "oops"},
        ]
        errors = h._validate_feature_schema(features)
        assert len(errors) == 2
        assert any("#2" in e for e in errors)
        assert any("#3" in e for e in errors)

    def test_non_dict_entry_does_not_crash(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        errors = h._validate_feature_schema(["not a feature dict"])
        assert len(errors) == 1
        assert "index 0" in errors[0]


# ── Budget enforcement ────────────────────────────────────────────────────────

class TestBudgetEnforcement:
    def _make_usage(self, prompt_tokens=0, completion_tokens=0):
        from types import SimpleNamespace
        return SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def test_budget_not_exceeded_below_limit(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"COST_BUDGET_USD": "1.00"})
        h._BUDGET_EXCEEDED = False
        h._track_usage("leader", self._make_usage(10, 5))
        assert h._BUDGET_EXCEEDED is False

    def test_budget_exceeded_when_tokens_breach_limit(self, monkeypatch, tmp_path):
        # Set a very low budget (0.00001 USD) so even a few tokens breach it
        h = _load_harness(monkeypatch, tmp_path, {"COST_BUDGET_USD": "0.000001"})
        h._BUDGET_EXCEEDED = False
        # Reset session costs to a clean state
        for v in h._SESSION_COSTS.values():
            v["prompt_tokens"] = 0
            v["completion_tokens"] = 0
        h._track_usage("leader", self._make_usage(prompt_tokens=10000, completion_tokens=10000))
        assert h._BUDGET_EXCEEDED is True

    def test_run_feature_cycle_skips_when_budget_exceeded(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [{"id": 1, "title": "T", "description": "d",
                              "status": "pending", "e2e": False, "depends_on": []}])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)

        # Force budget exceeded
        h._BUDGET_EXCEEDED = True

        spec_called = []
        monkeypatch.setattr(h, "spawn_spec_writer", lambda *a, **kw: spec_called.append(1) or "ok")
        monkeypatch.setattr(h, "_fire",      MagicMock())
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=None))

        result = h.run_feature_cycle(1, "desc", e2e=False)

        # Spec writer must NOT have been called
        assert spec_called == []
        assert result.get("approved") is False

    def test_budget_disabled_when_zero(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"COST_BUDGET_USD": "0"})
        h._BUDGET_EXCEEDED = False
        for v in h._SESSION_COSTS.values():
            v["prompt_tokens"] = 0
            v["completion_tokens"] = 0
        h._track_usage("leader", self._make_usage(prompt_tokens=10**9, completion_tokens=10**9))
        # With budget=0 enforcement is disabled — flag must stay False
        assert h._BUDGET_EXCEEDED is False


# ── Per-model pricing ──────────────────────────────────────────────────────────

class TestPerModelPricing:
    """
    MODEL_BY_ROLE allows mixing models, and LLM_FALLBACK_CHAIN allows mixing
    providers — _track_usage must price each call with the model that
    actually generated it, not a single global price.
    """

    def _make_usage(self, prompt_tokens=0, completion_tokens=0):
        from types import SimpleNamespace
        return SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    def _reset_costs(self, h):
        for v in h._SESSION_COSTS.values():
            v["prompt_tokens"] = v["completion_tokens"] = v["calls"] = 0
            v["cost_usd"] = 0.0

    def test_known_model_uses_its_own_pricing(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        self._reset_costs(h)

        h._track_usage("spec_writer", self._make_usage(1_000_000, 1_000_000), model="deepseek-v4-flash")

        flash = h.MODEL_PRICING["deepseek-v4-flash"]
        expected = flash["input_price"] * 1_000_000 + flash["output_price"] * 1_000_000
        assert h._SESSION_COSTS["spec_writer"]["cost_usd"] == pytest.approx(expected)

        # Sanity: flash and pro pricing differ, so this proves the model-specific
        # lookup actually ran rather than always pricing as deepseek-v4-pro.
        pro = h.MODEL_PRICING["deepseek-v4-pro"]
        pro_cost = pro["input_price"] * 1_000_000 + pro["output_price"] * 1_000_000
        assert expected != pro_cost

    def test_unknown_model_falls_back_to_default_pricing_and_warns_once(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        self._reset_costs(h)
        h._UNKNOWN_PRICING_MODELS_WARNED.clear()

        log_calls = []
        monkeypatch.setattr(h, "_log", lambda *a, **kw: log_calls.append((a, kw)))

        h._track_usage("implementer", self._make_usage(1_000_000, 1_000_000), model="some-future-model")

        default_pricing = h.MODEL_PRICING[h._DEFAULT_PRICING_MODEL]
        expected = default_pricing["input_price"] * 1_000_000 + default_pricing["output_price"] * 1_000_000
        assert h._SESSION_COSTS["implementer"]["cost_usd"] == pytest.approx(expected)

        # Warned exactly once, with the unknown model name in the message.
        assert len(log_calls) == 1
        assert log_calls[0][1].get("level") == "warning"
        assert "some-future-model" in log_calls[0][0][2]

        # A second call for the same unknown model must not log again.
        h._track_usage("implementer", self._make_usage(10, 10), model="some-future-model")
        assert len(log_calls) == 1

    def test_mixed_run_tracks_correct_cost_per_role(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        self._reset_costs(h)

        # Simulate a mixed-model, mixed-provider run: leader on deepseek-v4-pro,
        # spec_writer on deepseek-v4-flash, implementer falling back to an
        # OpenAI model via LLM_MODEL_MAP.
        h._track_usage("leader",      self._make_usage(2_000_000, 1_000_000), model="deepseek-v4-pro")
        h._track_usage("spec_writer", self._make_usage(2_000_000, 1_000_000), model="deepseek-v4-flash")
        h._track_usage("implementer", self._make_usage(2_000_000, 1_000_000), model="gpt-4o")

        pro   = h.MODEL_PRICING["deepseek-v4-pro"]
        flash = h.MODEL_PRICING["deepseek-v4-flash"]
        gpt4o = h.MODEL_PRICING["gpt-4o"]

        assert h._SESSION_COSTS["leader"]["cost_usd"] == pytest.approx(
            2_000_000 * pro["input_price"] + 1_000_000 * pro["output_price"])
        assert h._SESSION_COSTS["spec_writer"]["cost_usd"] == pytest.approx(
            2_000_000 * flash["input_price"] + 1_000_000 * flash["output_price"])
        assert h._SESSION_COSTS["implementer"]["cost_usd"] == pytest.approx(
            2_000_000 * gpt4o["input_price"] + 1_000_000 * gpt4o["output_price"])

        total_expected = (
            h._SESSION_COSTS["leader"]["cost_usd"]
            + h._SESSION_COSTS["spec_writer"]["cost_usd"]
            + h._SESSION_COSTS["implementer"]["cost_usd"]
        )
        assert h._session_total_cost_usd() == pytest.approx(total_expected)

        # The three models have meaningfully different blended prices — a
        # single global price applied across roles would not reproduce this.
        assert h._SESSION_COSTS["leader"]["cost_usd"] != h._SESSION_COSTS["implementer"]["cost_usd"]

    def test_track_usage_defaults_model_from_role_when_not_given(self, monkeypatch, tmp_path):
        # Existing callers that don't pass `model` (e.g. older plugin code)
        # must keep working, falling back to the role's MODEL_BY_ROLE entry.
        h = _load_harness(monkeypatch, tmp_path)
        self._reset_costs(h)

        h._track_usage("reviewer", self._make_usage(1_000_000, 1_000_000))

        role_model = h.MODEL_BY_ROLE.get("reviewer", h.MODEL)
        pricing = h.MODEL_PRICING.get(role_model, h.MODEL_PRICING[h._DEFAULT_PRICING_MODEL])
        expected = pricing["input_price"] * 1_000_000 + pricing["output_price"] * 1_000_000
        assert h._SESSION_COSTS["reviewer"]["cost_usd"] == pytest.approx(expected)


# ── Structured JSON logging ─────────────────────────────────────────────────────
#
# The formatter is tested directly (not by capturing real stdout through the
# root logger) because harness.py is reloaded fresh by _load_harness() in
# every test in this file, but logging.basicConfig()/addHandler() are only
# effective on the *first* successful call in the pytest process — exactly
# like the pre-existing progress/harness.log FileHandler. Testing the
# formatter directly against the freshly-imported module's own _SESSION_ID /
# _CURRENT_FEATURE_ID sidesteps that process-global quirk entirely.

class TestStructuredLogging:
    def _record(self, msg="[HARNESS] TEST_EVENT | detail here", level=logging.INFO):
        return logging.LogRecord(
            name="harness", level=level, pathname=__file__,
            lineno=1, msg=msg, args=(), exc_info=None,
        )

    def test_formatter_output_is_valid_single_line_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        line = h._JsonLogFormatter().format(self._record())

        assert "\n" not in line
        parsed = json.loads(line)  # must not raise
        assert set(parsed.keys()) == {"timestamp", "level", "session_id", "feature_id", "message"}
        assert parsed["level"] == "INFO"
        assert parsed["message"] == "[HARNESS] TEST_EVENT | detail here"

    def test_session_id_present_and_is_a_valid_uuid(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        formatter = h._JsonLogFormatter()

        parsed_a = json.loads(formatter.format(self._record()))
        parsed_b = json.loads(formatter.format(self._record(msg="second event")))

        uuid.UUID(parsed_a["session_id"])  # raises ValueError if malformed
        assert parsed_a["session_id"] == parsed_b["session_id"] == h._SESSION_ID

    def test_feature_id_is_null_outside_a_feature_cycle(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._CURRENT_FEATURE_ID.get() is None
        parsed = json.loads(h._JsonLogFormatter().format(self._record()))
        assert parsed["feature_id"] is None

    def test_feature_id_set_during_run_feature_cycle_and_reset_after(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        seen = {}

        def fake_impl(feature_id, description, e2e=True):
            seen["feature_id_during_cycle"] = h._CURRENT_FEATURE_ID.get()
            return {"approved": True, "attempts": 1, "final_verdict": "ok"}

        monkeypatch.setattr(h, "_run_feature_cycle_impl", fake_impl)

        result = h.run_feature_cycle(7, "desc", e2e=False)

        assert seen["feature_id_during_cycle"] == 7
        assert result["approved"] is True
        assert h._CURRENT_FEATURE_ID.get() is None  # reset once the cycle returns

    def test_feature_id_reset_even_if_cycle_raises(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)

        def boom(feature_id, description, e2e=True):
            raise RuntimeError("boom")

        monkeypatch.setattr(h, "_run_feature_cycle_impl", boom)

        with pytest.raises(RuntimeError):
            h.run_feature_cycle(5, "desc", e2e=False)

        assert h._CURRENT_FEATURE_ID.get() is None

    def test_plain_text_file_handler_is_preserved(self, monkeypatch, tmp_path):
        # progress/harness.log must stay intact — any plugin/tool that tails
        # it, or that just calls logging.getLogger().info(...) expecting a
        # configured root logger, must keep working unchanged.
        h = _load_harness(monkeypatch, tmp_path)
        assert any(
            isinstance(handler, h.logging.FileHandler)
            for handler in h.logging.getLogger().handlers
        )

    def test_exactly_one_json_stdout_handler_is_registered(self, monkeypatch, tmp_path):
        # STRUCTURED_LOG_STDOUT defaults to off (see TestStructuredLogStdoutDefault
        # below), so this test — which is specifically about the opt-in "enabled"
        # path — must request it explicitly. Otherwise this would be order-dependent:
        # it'd only pass if some earlier test in the same pytest process happened to
        # enable it first (the handler-registration guard is process-global, same
        # "only configures once" quirk as progress/harness.log's handler).
        h = _load_harness(monkeypatch, tmp_path, {"STRUCTURED_LOG_STDOUT": "true"})
        json_handlers = [
            handler for handler in h.logging.getLogger().handlers
            if handler.name == h._JSON_STDOUT_HANDLER_NAME
        ]
        assert len(json_handlers) == 1

        # Behavioral check rather than isinstance: harness.py is reloaded
        # fresh per test, so a handler registered by an earlier test's module
        # instance carries a formatter class object from that earlier
        # instance — a genuinely different (if identically-defined) class,
        # so isinstance against *this* test's h._JsonLogFormatter would be a
        # false negative. What matters is that it behaves like the JSON
        # formatter, which this confirms directly.
        line = json_handlers[0].formatter.format(self._record())
        parsed = json.loads(line)
        assert set(parsed.keys()) == {"timestamp", "level", "session_id", "feature_id", "message"}


class TestStructuredLogStdoutDefault:
    def test_unset_env_var_is_disabled(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._structured_log_stdout_enabled() is False

    @pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes", "on"])
    def test_truthy_values_enable_it(self, monkeypatch, tmp_path, value):
        h = _load_harness(monkeypatch, tmp_path, {"STRUCTURED_LOG_STDOUT": value})
        assert h._structured_log_stdout_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "No"])
    def test_falsy_values_disable_it(self, monkeypatch, tmp_path, value):
        h = _load_harness(monkeypatch, tmp_path, {"STRUCTURED_LOG_STDOUT": value})
        assert h._structured_log_stdout_enabled() is False


# ── Console verbosity tiers (summary/normal/verbose) ────────────────────────────

class TestVerbosity:
    def test_default_is_normal(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h.HARNESS_VERBOSITY == "normal"

    @pytest.mark.parametrize("level", ["summary", "normal", "verbose"])
    def test_explicit_valid_value_is_used(self, monkeypatch, tmp_path, level):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": level})
        assert h.HARNESS_VERBOSITY == level

    def test_invalid_value_falls_back_to_normal(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "extremely_loud"})
        assert h.HARNESS_VERBOSITY == "normal"

    @pytest.mark.parametrize("active,min_level,expected", [
        ("summary", "summary", True), ("summary", "normal", False), ("summary", "verbose", False),
        ("normal",  "summary", True), ("normal",  "normal",  True),  ("normal",  "verbose", False),
        ("verbose", "summary", True), ("verbose", "normal",  True),  ("verbose", "verbose", True),
    ])
    def test_verbosity_at_least(self, monkeypatch, tmp_path, active, min_level, expected):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": active})
        assert h._verbosity_at_least(min_level) is expected

    def test_vprint_emits_when_tier_sufficient(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "normal"})
        h._vprint("normal", "hello")
        h.console.print.assert_called_once_with("hello")

    def test_vprint_suppressed_when_tier_insufficient(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "summary"})
        h._vprint("normal", "hello")
        h.console.print.assert_not_called()


class TestFeatureCycleVerbosityIntegration:
    """
    End-to-end: with spawn_* mocked (same pattern as
    test_resumability.py::TestRunFeatureCycleResume._patch_cycle), verify
    HARNESS_VERBOSITY actually gates the retry Panel emitted directly by
    _run_feature_cycle_impl, while the summary-tier start/verdict lines
    always show regardless of tier.
    """

    def _patch_cycle(self, h, monkeypatch, review_results):
        monkeypatch.setattr(h, "spawn_spec_writer", MagicMock(return_value="progress/spec_1.md"))
        monkeypatch.setattr(h, "spawn_implementer", MagicMock(return_value="ok"))
        monkeypatch.setattr(h, "spawn_e2e_tester", MagicMock(return_value="E2E_PASSED"))
        monkeypatch.setattr(h, "spawn_reviewer", MagicMock(side_effect=review_results))
        monkeypatch.setattr(h, "_fire", MagicMock())
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=None))
        monkeypatch.setattr(h, "_track_usage", MagicMock())

    def _printed_args(self, h):
        return [a for call in h.console.print.call_args_list for a in call.args]

    def test_normal_tier_shows_retry_panel(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "normal"})
        self._patch_cycle(h, monkeypatch, review_results=["REJECTED: bad code", "APPROVED"])

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        printed = self._printed_args(h)
        assert any("Feature #1" in str(a) for a in printed)   # summary: start line
        assert any("approved" in str(a) for a in printed)     # summary: verdict line
        # The one Panel() construction in this scenario is the reviewer-retry
        # panel; since rich.panel is mocked, Panel(...) always returns the
        # same MagicMock singleton (Panel.return_value) — checking whether
        # that singleton reached console.print is the tier-gating signal,
        # since str(a) on a bare MagicMock wouldn't contain the panel's text.
        assert h.Panel.return_value in printed

    def test_summary_tier_hides_retry_panel(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "summary"})
        self._patch_cycle(h, monkeypatch, review_results=["REJECTED: bad code", "APPROVED"])

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        printed = self._printed_args(h)
        assert any("Feature #1" in str(a) for a in printed)   # summary line still shows
        assert any("approved" in str(a) for a in printed)     # summary line still shows
        assert h.Panel.return_value not in printed             # normal-tier panel suppressed


class TestAfterReviewerRejectedHook:
    """
    after_reviewer_rejected must fire once per genuine Reviewer rejection —
    including attempts that go on to be retried — with feature_id, attempt,
    max_attempts, and rejection_reason. Same _patch_cycle pattern as
    TestFeatureCycleVerbosityIntegration, except _fire is a spying MagicMock
    instead of a silencing one, since this test asserts on its calls.
    """

    def _patch_cycle(self, h, monkeypatch, review_results):
        monkeypatch.setattr(h, "spawn_spec_writer", MagicMock(return_value="progress/spec_1.md"))
        monkeypatch.setattr(h, "spawn_implementer", MagicMock(return_value="ok"))
        monkeypatch.setattr(h, "spawn_e2e_tester", MagicMock(return_value="E2E_PASSED"))
        monkeypatch.setattr(h, "spawn_reviewer", MagicMock(side_effect=review_results))
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=None))
        monkeypatch.setattr(h, "_track_usage", MagicMock())
        fire_mock = MagicMock()
        monkeypatch.setattr(h, "_fire", fire_mock)
        return fire_mock

    def _calls_for(self, fire_mock, event):
        return [call for call in fire_mock.call_args_list if call.args[0] == event]

    def test_fires_on_every_rejected_attempt(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"MAX_RETRIES_REVIEW": "2"})
        fire_mock = self._patch_cycle(
            h, monkeypatch,
            review_results=["REJECTED: bad code", "REJECTED: still bad"],
        )

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is False
        rejected_calls = self._calls_for(fire_mock, "after_reviewer_rejected")
        assert len(rejected_calls) == 2

        first_kwargs = rejected_calls[0].kwargs
        assert first_kwargs["feature_id"] == 1
        assert first_kwargs["description"] == "desc"
        assert first_kwargs["attempt"] == 1
        assert first_kwargs["max_attempts"] == 2
        assert "bad code" in first_kwargs["rejection_reason"]

        second_kwargs = rejected_calls[1].kwargs
        assert second_kwargs["attempt"] == 2
        assert second_kwargs["max_attempts"] == 2
        assert "still bad" in second_kwargs["rejection_reason"]

        # after_feature_failed still fires once retries are exhausted —
        # this hook is additive, not a replacement.
        assert len(self._calls_for(fire_mock, "after_feature_failed")) == 1

    def test_does_not_fire_on_approval(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"MAX_RETRIES_REVIEW": "2"})
        fire_mock = self._patch_cycle(
            h, monkeypatch,
            review_results=["REJECTED: bad code", "APPROVED"],
        )

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        rejected_calls = self._calls_for(fire_mock, "after_reviewer_rejected")
        assert len(rejected_calls) == 1
        assert rejected_calls[0].kwargs["attempt"] == 1
        assert len(self._calls_for(fire_mock, "after_feature_approved")) == 1


# ── Structured agent status (progress/<stage>_<id>.json) ───────────────────────

class TestReadStructuredStatus:
    def test_missing_file_returns_none(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.md").write_text("report body")
        assert h._read_structured_status("progress/impl_1.md") is None

    def test_invalid_json_returns_none(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.json").write_text("NOT JSON {{{")
        assert h._read_structured_status("progress/impl_1.md") is None

    def test_json_missing_schema_version_returns_none(self, monkeypatch, tmp_path):
        # Foreign/unrelated JSON that happens to share the sibling path must
        # be treated the same as "no structured status" — not crash, not be
        # half-trusted.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps({"status": "done"}))
        assert h._read_structured_status("progress/impl_1.md") is None

    def test_valid_status_is_parsed(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "status": "done", "tests_passed": True, "files_touched": ["src/x.py"]}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") == payload

    def test_current_schema_version_constant_matches_shipped_value(self, monkeypatch, tmp_path):
        # AgentStatusSchema/_read_structured_status validate against this
        # constant — pin it so a future bump is a deliberate, visible change
        # to this test, not an accidental drift.
        h = _load_harness(monkeypatch, tmp_path)
        assert h.STATUS_SCHEMA_VERSION == 1

    def test_future_schema_version_is_detected_as_mismatch_not_silently_accepted(self, monkeypatch, tmp_path):
        # A file from a future (not-yet-written) schema version must not be
        # half-trusted as if it were current — falls back to None/prose
        # heuristic exactly like a missing file, but is logged distinctly.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 2, "status": "done", "tests_passed": True, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))

        logged = []
        monkeypatch.setattr(h, "_log", lambda role, event, detail="", level="info": logged.append(event))

        result = h._read_structured_status("progress/impl_1.md")

        assert result is None
        assert "STATUS_SCHEMA_VERSION_MISMATCH" in logged

    def test_older_schema_version_is_also_detected_as_mismatch(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 0, "status": "done", "tests_passed": True, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))

        logged = []
        monkeypatch.setattr(h, "_log", lambda role, event, detail="", level="info": logged.append(event))

        assert h._read_structured_status("progress/impl_1.md") is None
        assert "STATUS_SCHEMA_VERSION_MISMATCH" in logged

    def test_current_version_but_wrong_field_type_fails_validation_gracefully(self, monkeypatch, tmp_path):
        # Same schema_version, but "tests_passed" is an object instead of a
        # bool (pydantic v2's lax bool parsing accepts some strings like
        # "yes"/"no", so use a type with no such coercion) — AgentStatusSchema
        # must reject it (not crash, not half-trust it), falling back to None
        # like any other invalid structured file.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "status": "done", "tests_passed": {"nested": True}, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))

        logged = []
        monkeypatch.setattr(h, "_log", lambda role, event, detail="", level="info": logged.append(event))

        assert h._read_structured_status("progress/impl_1.md") is None
        assert "STATUS_SCHEMA_VALIDATION_ERROR" in logged

    def test_current_version_missing_required_field_fails_validation(self, monkeypatch, tmp_path):
        # "status" is required by AgentStatusSchema even though the raw
        # schema_version check alone wouldn't have caught its absence.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "tests_passed": True, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") is None

    def test_current_version_unknown_extra_field_fails_validation(self, monkeypatch, tmp_path):
        # extra="forbid", same as FeatureSchema — a drifted/renamed field is
        # a loud validation error instead of a silently-ignored .get() miss.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {
            "schema_version": 1, "status": "done", "tests_passed": True,
            "files_touched": [], "unexpected_field": "oops",
        }
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") is None

    def test_current_version_unknown_status_value_fails_validation(self, monkeypatch, tmp_path):
        # "complete" belongs to no role's real vocabulary (done/ok/approved/
        # rejected/passed/failed) — a hallucinated status must not pass.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "status": "complete", "tests_passed": True, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") is None

    @pytest.mark.parametrize("status_value", ["ok", "done", "approved", "rejected", "passed", "failed"])
    def test_every_real_role_status_value_is_accepted(self, monkeypatch, tmp_path, status_value):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "status": status_value, "tests_passed": None, "files_touched": []}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") == payload

    def test_reason_and_optional_fields_may_be_omitted(self, monkeypatch, tmp_path):
        # reviewer/e2e_tester write "reason": null on success, but a minimal
        # file omitting optional fields entirely must still validate.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        payload = {"schema_version": 1, "status": "ok"}
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(payload))
        assert h._read_structured_status("progress/impl_1.md") == payload


class TestImplCacheStructuredVsLegacy:
    def _stub_run_agent(self, h, monkeypatch, calls):
        def fake_run_agent(*a, **kw):
            calls.append(1)
            return "progress/impl_1.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

    def test_structured_tests_passed_true_reuses_without_calling_agent(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.md").write_text("Full pytest output:\n2 failed, 1 passed")
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "done", "tests_passed": True, "files_touched": []}
        ))
        calls = []
        self._stub_run_agent(h, monkeypatch, calls)

        result = h.spawn_implementer(1, "desc")

        assert result == "progress/impl_1.md"
        assert calls == []  # cache hit — agent never invoked

    def test_structured_tests_passed_false_does_not_reuse(self, monkeypatch, tmp_path):
        # This is the regression case: pytest output containing "2 failed, 1
        # passed" would fool the old "passed" in content heuristic, but the
        # structured tests_passed field is exact and correctly forces a
        # fresh implementer run instead of reusing a failing attempt.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.md").write_text("Full pytest output:\n2 failed, 1 passed")
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "done", "tests_passed": False, "files_touched": []}
        ))
        calls = []
        self._stub_run_agent(h, monkeypatch, calls)

        h.spawn_implementer(1, "desc")

        assert calls == [1]  # cache miss — agent was invoked to redo the work

    def test_legacy_no_json_falls_back_to_substring_heuristic(self, monkeypatch, tmp_path):
        # No sibling .json (progress/ predates this schema) — old behavior
        # is preserved exactly, bug and all: "passed" in content matches
        # even inside "2 failed, 1 passed", so this legacy path still
        # reuses. Documents that the fix only applies going forward, not to
        # pre-existing progress/ directories, by design (no migration).
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.md").write_text("Full pytest output:\n2 failed, 1 passed")
        calls = []
        self._stub_run_agent(h, monkeypatch, calls)

        result = h.spawn_implementer(1, "desc")

        assert result == "progress/impl_1.md"
        assert calls == []

    def test_legacy_error_marker_prevents_reuse(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_1.md").write_text("[ERROR: something broke]\n0 passed")
        calls = []
        self._stub_run_agent(h, monkeypatch, calls)

        h.spawn_implementer(1, "desc")

        assert calls == [1]


class TestReviewerAndE2eVerdict:
    def test_reviewer_structured_approved(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "review_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "approved", "tests_passed": True, "files_touched": [], "reason": None}
        ))
        approved, reason = h._reviewer_verdict("irrelevant raw text", "progress/review_1.md")
        assert approved is True
        assert reason == ""

    def test_reviewer_structured_rejected_uses_reason_field(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "review_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "rejected", "tests_passed": False,
             "files_touched": [], "reason": "missing null check"}
        ))
        approved, reason = h._reviewer_verdict("Approved.", "progress/review_1.md")
        # Structured status wins even though the raw string looks like an approval.
        assert approved is False
        assert reason == "missing null check"

    def test_reviewer_falls_back_when_no_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        approved, reason = h._reviewer_verdict("REJECTED: bad code", "progress/review_1.md")
        assert approved is False
        assert reason == "bad code"

    def test_reviewer_falls_back_on_malformed_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "review_1.json").write_text("NOT JSON")
        approved, reason = h._reviewer_verdict("APPROVED", "progress/review_1.md")
        assert approved is True

    def test_e2e_structured_passed(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "passed", "tests_passed": True, "files_touched": [], "reason": None}
        ))
        passed, reason = h._e2e_verdict("irrelevant", "progress/e2e_1.md")
        assert passed is True
        assert reason == ""

    def test_e2e_structured_failed_uses_reason_field(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "failed", "tests_passed": False,
             "files_touched": [], "reason": "login button not found"}
        ))
        passed, reason = h._e2e_verdict("E2E_PASSED", "progress/e2e_1.md")
        # Structured status wins even though the raw string looks like a pass.
        assert passed is False
        assert reason == "login button not found"

    def test_e2e_falls_back_when_no_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        passed, reason = h._e2e_verdict("E2E_FAILED: timeout waiting for selector", "progress/e2e_1.md")
        assert passed is False
        assert reason == "timeout waiting for selector"

    def test_e2e_falls_back_on_malformed_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_1.json").write_text("[1, 2, 3]")  # valid JSON, not a dict
        passed, reason = h._e2e_verdict("E2E_PASSED", "progress/e2e_1.md")
        assert passed is True


class TestSpawnE2eTesterStaleReportCleanup:
    """
    spawn_e2e_tester's report path (progress/e2e_<id>.md/.json) carries no
    attempt number. Regression coverage for the incident where an attempt
    cut short by max_iter left a prior attempt's report on disk, and
    _e2e_verdict's JSON-first read silently reused it as if it described the
    attempt that just ran.
    """

    def _stub_run_agent(self, h, monkeypatch, result="E2E_PASSED"):
        calls = []

        def fake_run_agent(*a, **kw):
            calls.append(kw.get("checkpoint_key"))
            return result

        monkeypatch.setattr(h, "run_agent", fake_run_agent)
        return calls

    def test_fresh_attempt_deletes_stale_report_from_earlier_attempt(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_1.md").write_text("stale report from attempt 1")
        (tmp_path / "progress" / "e2e_1.json").write_text(json.dumps(
            {"schema_version": 1, "status": "passed", "tests_passed": True, "files_touched": [], "reason": None}
        ))
        self._stub_run_agent(h, monkeypatch, result="[ERROR: max_iter 30 reached]")

        h.spawn_e2e_tester(1, attempt=2)

        assert not (tmp_path / "progress" / "e2e_1.md").exists()
        assert not (tmp_path / "progress" / "e2e_1.json").exists()

    def test_no_stale_report_is_a_no_op(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        self._stub_run_agent(h, monkeypatch, result="E2E_PASSED")

        h.spawn_e2e_tester(1, attempt=1)  # must not raise on a missing report

        assert not (tmp_path / "progress" / "e2e_1.md").exists()

    def test_pending_same_attempt_resume_preserves_report(self, monkeypatch, tmp_path):
        # Simulates a harness-process crash mid-attempt: run_agent's own
        # message-state checkpoint for this exact attempt is still on disk
        # (never cleared, since a clean return — verdict or max_iter — always
        # clears it). The resumed conversation may reference the partial
        # report it already wrote this attempt, so it must survive.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_1.md").write_text("partial report from this same attempt")
        checkpoint_key = "e2e_tester_1_2"
        h._save_message_state(checkpoint_key, [{"role": "user", "content": "hi"}])
        self._stub_run_agent(h, monkeypatch, result="E2E_PASSED")

        h.spawn_e2e_tester(1, attempt=2)

        assert (tmp_path / "progress" / "e2e_1.md").read_text() == "partial report from this same attempt"


class TestSpawnE2eTesterMaxIterRecovery:
    """
    Regression coverage for feature #71: the agent correctly diagnosed a
    real backend 500 in its .md report but ran out of iterations before
    writing the sibling .json. Without recovery, the harness only sees
    run_agent's generic "[ERROR: max_iter ... reached]" tool-call-errors
    message, discarding the correct diagnosis.
    """

    def _stub_run_agent_writes_md_then_times_out(self, h, monkeypatch, tmp_path, md_content):
        def fake_run_agent(*a, **kw):
            (tmp_path / "progress" / "e2e_1.md").write_text(md_content)
            return "[ERROR: max_iter 50 reached]\nRecent tool-call errors: read_file(...) -> not found"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

    def test_recovers_verdict_from_md_when_json_missing_after_max_iter(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        self._stub_run_agent_writes_md_then_times_out(
            h, monkeypatch, tmp_path,
            "## Scenarios\n...\n- Verdict: E2E_FAILED: backend 500 in list_professionals "
            "(confirmed via page.request)",
        )

        result = h.spawn_e2e_tester(1, attempt=1)

        assert "backend 500 in list_professionals" in result
        assert "Recent tool-call errors" not in result

    def test_does_not_recover_when_json_already_exists(self, monkeypatch, tmp_path):
        # A structured .json means the agent did finish — the generic
        # max_iter string (if any) should never be second-guessed here;
        # _e2e_verdict's own JSON-first preference handles this case.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        def fake_run_agent(*a, **kw):
            (tmp_path / "progress" / "e2e_1.md").write_text("- Verdict: E2E_PASSED")
            (tmp_path / "progress" / "e2e_1.json").write_text(json.dumps(
                {"schema_version": 1, "status": "passed", "tests_passed": True,
                 "files_touched": [], "reason": None}
            ))
            return "[ERROR: max_iter 50 reached]"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        result = h.spawn_e2e_tester(1, attempt=1)

        assert result == "[ERROR: max_iter 50 reached]"

    def test_no_recovery_when_md_also_missing(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        def fake_run_agent(*a, **kw):
            return "[ERROR: max_iter 50 reached]"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        result = h.spawn_e2e_tester(1, attempt=1)

        assert result == "[ERROR: max_iter 50 reached]"

    def test_no_recovery_when_md_has_no_verdict_line(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        self._stub_run_agent_writes_md_then_times_out(
            h, monkeypatch, tmp_path, "## Scenarios\nstill exploring, nothing conclusive yet",
        )

        result = h.spawn_e2e_tester(1, attempt=1)

        assert result == "[ERROR: max_iter 50 reached]\nRecent tool-call errors: read_file(...) -> not found"

    def test_passing_result_is_untouched(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        monkeypatch.setattr(h, "run_agent", lambda *a, **kw: "E2E_PASSED")

        result = h.spawn_e2e_tester(1, attempt=1)

        assert result == "E2E_PASSED"


# ── _file_tree truncation + _validate_spec stack-awareness ─────────────────────

class TestFileTree:
    def _make_files(self, tmp_path, n):
        d = tmp_path / "many"
        d.mkdir()
        for i in range(n):
            (d / f"file_{i:03d}.py").write_text("")
        return d

    def test_no_truncation_note_under_cap(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        d = self._make_files(tmp_path, 10)
        tree = h._file_tree(str(d), max_files=60)
        assert "truncated" not in tree

    def test_no_truncation_note_exactly_at_cap(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        d = self._make_files(tmp_path, 60)
        tree = h._file_tree(str(d), max_files=60)
        assert "truncated" not in tree

    def test_truncation_note_over_cap(self, monkeypatch, tmp_path):
        # Regression: a file whose path sorts past the cutoff (e.g.
        # tests/test_migrations.py in a large tests/ dir) used to look
        # "absent" to anything consuming this string, which _validate_spec
        # misread as "doesn't exist" — a false positive on a real file.
        h = _load_harness(monkeypatch, tmp_path)
        d = self._make_files(tmp_path, 75)
        tree = h._file_tree(str(d), max_files=60)
        assert "75 files total, showing first 60" in tree
        assert "not proof a file doesn't exist" in tree
        # The 75th file (alphabetically last) must not silently look absent —
        # it's summarized by the truncation note instead of just missing.
        assert "file_074.py" not in tree


class TestValidateSpecStackAware:
    def test_uses_code_tree_dirs_not_hardcoded_src_tests(self, monkeypatch, tmp_path):
        # _validate_spec previously hardcoded _file_tree("src") / _file_tree("tests")
        # instead of the stack-resolved CODE_TREE_DIRS every other spawn_*
        # function already uses — silently useless for a project whose stack
        # profile names its source dir something else (e.g. backend/).
        #
        # resolve_layout() is @lru_cache'd on the stack_layout module object,
        # which the shared _load_harness() helper doesn't purge from
        # sys.modules (only "harness"/"harness.*") — an earlier test's
        # resolution would otherwise stick for the rest of the process, same
        # as TestRunPlaywrightTests._load_tools already works around.
        for key in list(sys.modules.keys()):
            if key in ("tools", "stack_layout"):
                del sys.modules[key]
        h = _load_harness(monkeypatch, tmp_path, {"CODE_TREE_DIRS": "backend,backend/tests"})
        (tmp_path / "progress").mkdir(exist_ok=True)
        spec_path = tmp_path / "progress" / "spec_1.md"
        spec_path.write_text("# spec")

        captured = {}

        def fake_call(model, messages, tools, role):
            captured["messages"] = messages
            return None

        monkeypatch.setattr(h, "_call_api_with_fallback", fake_call)

        h._validate_spec(str(spec_path))

        user_msg = captured["messages"][1]["content"]
        assert "Existing files in backend/:" in user_msg
        assert "Existing files in backend/tests/:" in user_msg
        assert "Existing files in src/:" not in user_msg


# ── Secret redaction ─────────────────────────────────────────────────────────

class TestRedact:
    def test_replaces_known_secret_value(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_REDACT_VALUES", ("sk-super-secret",))
        assert h._redact("here is sk-super-secret in the middle") == "here is ***REDACTED*** in the middle"

    def test_leaves_unrelated_text_untouched(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_REDACT_VALUES", ("sk-super-secret",))
        assert h._redact("nothing sensitive here") == "nothing sensitive here"

    def test_handles_empty_text(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._redact("") == ""

    def test_redacts_multiple_distinct_secrets(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_REDACT_VALUES", ("secret-one", "secret-two"))
        assert h._redact("secret-one and secret-two") == "***REDACTED*** and ***REDACTED***"


class TestToolResultRedactionInRunAgent:
    """
    Belt-and-suspenders check: even if a tool result somehow contains a raw
    secret (e.g. the .env-read block in tools.py were ever bypassed), it must
    never reach the LLM's own conversation history — not just the logs —
    since that history gets written into progress/*.md reports too.
    """

    def _fake_tool_call(self, call_id, name, args_json):
        from types import SimpleNamespace
        fn = SimpleNamespace(name=name, arguments=args_json)
        return SimpleNamespace(id=call_id, function=fn)

    def _fake_response(self, content=None, tool_calls=None):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=usage, model="deepseek-v4-pro")

    def test_secret_stripped_before_appended_to_messages(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_REDACT_VALUES", ("sk-leaked-secret-value",))

        tool_call_response = self._fake_response(
            content=None,
            tool_calls=[self._fake_tool_call("c1", "read_file", '{"path": "x"}')],
        )
        final_response = self._fake_response(content="done")

        monkeypatch.setattr(h, "_call_api_with_fallback",
                             MagicMock(side_effect=[tool_call_response, final_response]))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"content": "DEEPSEEK_API_KEY=sk-leaked-secret-value"})
        ))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=5)

        assert result == "done"
        second_call_messages = h._call_api_with_fallback.call_args_list[1].kwargs["messages"]
        tool_messages = [m for m in second_call_messages if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "sk-leaked-secret-value" not in tool_messages[0]["content"]
        assert "***REDACTED***" in tool_messages[0]["content"]

    def test_secret_not_printed_in_verbose_tier(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"HARNESS_VERBOSITY": "verbose"})
        monkeypatch.setattr(h, "_REDACT_VALUES", ("sk-leaked-secret-value",))

        tool_call_response = self._fake_response(
            content=None,
            tool_calls=[self._fake_tool_call("c1", "read_file", '{"path": "x"}')],
        )
        final_response = self._fake_response(content="done")

        monkeypatch.setattr(h, "_call_api_with_fallback",
                             MagicMock(side_effect=[tool_call_response, final_response]))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"content": "DEEPSEEK_API_KEY=sk-leaked-secret-value"})
        ))

        h.run_agent("sys", [], "task", role="implementer", max_iter=5)

        printed = [str(a) for call in h.console.print.call_args_list for a in call.args]
        assert not any("sk-leaked-secret-value" in p for p in printed)


class TestIsWriteCall:
    """Unit tests for _is_write_call, the signal behind the convergence
    streak detector: did this tool call actually mutate a file?"""

    def test_direct_write_tools_count_as_writes(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._is_write_call("write_file", '{"status": "ok"}') is True
        assert h._is_write_call("append_file", '{"status": "ok"}') is True
        assert h._is_write_call("update_feature_status", '{"status": "ok"}') is True

    def test_read_only_tools_do_not_count(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._is_write_call("read_file", '{"content": "..."}') is False
        assert h._is_write_call("list_files", '{"files": []}') is False
        assert h._is_write_call("run_bash", '{"stdout": "..."}') is False

    def test_successful_edit_alias_translation_counts_as_write(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        note = (
            "Tool 'edit_file' doesn't exist in this harness — auto-translated "
            "to a real read_file + write_file replacement."
        )
        assert h._is_write_call("edit_file", json.dumps({"status": "ok", "note": note})) is True

    def test_failed_edit_alias_attempt_does_not_count(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        error = json.dumps({"error": "Tool 'edit_file' not found. Use read_file/write_file instead."})
        assert h._is_write_call("edit_file", error) is False


class TestConvergenceStreakDetector:
    """
    run_agent must inject a live "CONVERGENCE CHECKPOINT" nudge once the
    agent has gone CONVERGENCE_STREAK_LIMIT consecutive iterations without
    a write — mirroring the existing BUDGET CHECKPOINT mechanism — and must
    reset the streak the moment a write happens.
    """

    def _fake_tool_call(self, call_id, name, args_json):
        from types import SimpleNamespace
        fn = SimpleNamespace(name=name, arguments=args_json)
        return SimpleNamespace(id=call_id, function=fn)

    def _fake_response(self, content=None, tool_calls=None):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=usage, model="deepseek-v4-pro")

    def _checkpoint_present(self, snapshot):
        return any(
            isinstance(m, dict) and "CONVERGENCE CHECKPOINT" in m.get("content", "")
            for m in snapshot
        )

    def _recording_call(self, responses):
        """
        A side_effect that snapshots (shallow-copies) `messages` at the exact
        moment of each call, since `messages` is one mutable list appended to
        in place across iterations — MagicMock's own call_args_list would
        otherwise have every recorded call alias the same, final list.
        """
        captured = []

        def fake_call(**kwargs):
            captured.append(list(kwargs["messages"]))
            return responses[len(captured) - 1]

        return fake_call, captured

    def test_fires_after_streak_limit_of_reads_with_no_write(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"CONVERGENCE_STREAK_LIMIT": "3"})

        read_call = self._fake_response(
            content=None,
            tool_calls=[self._fake_tool_call("c1", "read_file", '{"path": "x"}')],
        )
        responses = [read_call] * 5 + [self._fake_response(content="done")]
        fake_call, captured = self._recording_call(responses)
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=fake_call))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"content": "irrelevant file contents"})
        ))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=6)

        assert result == "done"
        # Streak reaches 3 only after the 3rd read (recorded at the end of
        # iteration index 2), so the checkpoint must first appear in the
        # request for iteration index 3 — not any earlier.
        assert not self._checkpoint_present(captured[0])
        assert not self._checkpoint_present(captured[1])
        assert not self._checkpoint_present(captured[2])
        assert self._checkpoint_present(captured[3])

    def test_write_call_resets_the_streak(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"CONVERGENCE_STREAK_LIMIT": "2"})

        read_call = self._fake_response(
            content=None,
            tool_calls=[self._fake_tool_call("c1", "read_file", '{"path": "x"}')],
        )
        write_call = self._fake_response(
            content=None,
            tool_calls=[self._fake_tool_call("c2", "write_file", '{"path": "x", "content": "y"}')],
        )
        # Alternating read/write never lets the no-write streak reach 2.
        responses = [read_call, write_call, read_call, write_call, self._fake_response(content="done")]
        fake_call, captured = self._recording_call(responses)
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=fake_call))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"status": "ok"})
        ))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=5)

        assert result == "done"
        assert not any(self._checkpoint_present(snap) for snap in captured)


class TestSandboxLocalEnvSanitization:
    def test_api_keys_stripped_from_local_subprocess_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-be-stripped")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-also-stripped")
        monkeypatch.setenv("HARNESS_TEST_KEEP_ME", "keep-me")

        for key in list(sys.modules.keys()):
            if key == "sandbox":
                del sys.modules[key]
        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        sandbox = importlib.import_module("sandbox")

        env = sandbox._sanitized_host_env()

        assert "DEEPSEEK_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env
        assert env.get("HARNESS_TEST_KEEP_ME") == "keep-me"


# ── agents/leader.py: TOOLS surface ────────────────────────────────────────────

class TestLeaderToolsSurface:
    def test_run_bash_is_not_in_leader_tools(self):
        # The Leader's own documented PROTOCOL never runs pytest, starts
        # servers, or shells out — real incident: it used run_bash to try
        # `docker ps`, start uvicorn manually, and connect to Postgres with
        # raw asyncpg while chasing a repeated E2E failure, none of which is
        # part of its coordinator role.
        import agents.leader as leader
        names = [t["function"]["name"] for t in leader.TOOLS]
        assert "run_bash" not in names


# ── tools.py ─────────────────────────────────────────────────────────────────

class TestTools:
    def _load_tools(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "feature_list.json").write_text(json.dumps([
            {"id": 1, "title": "T", "status": "pending", "description": "d",
             "e2e": False, "depends_on": []}
        ]))

        for mod in ["sandbox"]:
            monkeypatch.setitem(sys.modules, mod, MagicMock())

        for key in list(sys.modules.keys()):
            if key == "tools":
                del sys.modules[key]

        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        return importlib.import_module("tools")

    # _is_safe_path

    def test_safe_path_inside_src(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("src/models/user.py") is True

    def test_safe_path_inside_tests(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("tests/test_foo.py") is True

    def test_unsafe_path_outside_safe_dirs(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("secrets/api_key.txt") is False

    def test_path_traversal_blocked(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_safe_path("src/../secrets/key.txt") is False

    # _is_secret_path / read_file / list_files blocking .env*

    @pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", "a/b/.env"])
    def test_is_secret_path_matches_env_variants(self, monkeypatch, tmp_path, name):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_secret_path(name) is True

    @pytest.mark.parametrize("name", ["main.py", "environment.py", ".envrc", "config/.env-example.md"])
    def test_is_secret_path_does_not_match_unrelated_names(self, monkeypatch, tmp_path, name):
        t = self._load_tools(monkeypatch, tmp_path)
        assert t._is_secret_path(name) is False

    def test_read_file_refuses_env_file(self, monkeypatch, tmp_path):
        # read_file has no SAFE_WRITE_DIRS-style confinement (unlike write_file),
        # so this is the only thing stopping an agent debugging a connectivity
        # issue from doing read_file(".env") and getting DEEPSEEK_API_KEY back
        # as a tool result.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-super-secret-value")
        result = json.loads(t.read_file(".env"))
        assert "error" in result
        assert "sk-super-secret-value" not in json.dumps(result)

    def test_list_files_omits_env_file(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=sk-super-secret-value")
        (tmp_path / "README.md").write_text("hi")
        result = json.loads(t.list_files("."))
        assert not any(f.endswith(".env") for f in result["files"])
        assert any(f.endswith("README.md") for f in result["files"])

    # update_feature_status

    def test_update_feature_status_valid(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.update_feature_status(1, "done"))
        assert result.get("success") is True or "error" not in result

    def test_update_feature_status_invalid_status(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.update_feature_status(1, "flying"))
        assert "error" in result

    def test_update_feature_status_unknown_id(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.update_feature_status(999, "done"))
        assert "error" in result

    # execute_tool

    def test_execute_tool_unknown_tool(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool("nonexistent_tool", {}))
        assert "error" in result

    def test_execute_tool_grep_hallucination_with_pattern_runs_real_search(self, monkeypatch, tmp_path):
        # Agents sometimes hallucinate a "grep" tool that doesn't exist in this
        # harness. Root-cause fix (2026-06-18, feature 26 incident): instead of
        # just erroring and letting the agent burn iterations retrying the same
        # intent under a different name, translate the call into a real
        # pure-Python search and return actual matches.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.ts").write_text("export function uploadPetPhoto() {}\n")
        result = json.loads(t.execute_tool("grep", {"pattern": "uploadPetPhoto", "path": "src"}))
        assert "error" not in result
        assert any("uploadPetPhoto" in m for m in result["matches"])
        assert "auto-translated" in result["note"]

    def test_execute_tool_grep_on_a_file_path_searches_just_that_file(self, monkeypatch, tmp_path):
        # The actual failing call from the incident passed a file path (not a
        # directory) as "path" — make sure that's handled directly rather than
        # silently falling back to a whole-tree walk.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "api.ts").write_text("export function uploadPetPhoto() {}\n")
        result = json.loads(t.execute_tool("grep", {"pattern": "uploadPetPhoto", "path": "api.ts"}))
        assert "error" not in result
        assert len(result["matches"]) == 1
        assert "api.ts" in result["matches"][0]

    def test_execute_tool_find_alias_searches_filenames_not_contents(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "test_feature_26.py").write_text("# irrelevant content\n")
        (tmp_path / "other.py").write_text("# also irrelevant\n")
        result = json.loads(t.execute_tool("find", {"pattern": "feature_26", "path": "."}))
        assert "error" not in result
        assert any("test_feature_26.py" in m for m in result["matches"])
        assert not any("other.py" in m and "test_feature_26.py" not in m for m in result["matches"])

    def test_execute_tool_grep_hallucination_without_pattern_falls_back_to_hint(self, monkeypatch, tmp_path):
        # No pattern arg means there's nothing to translate — fall back to the
        # old hint-only error rather than guessing.
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool("grep", {}))
        assert "error" in result
        assert "run_bash" in result["error"]
        assert "grep" in result["error"].lower()

    def test_execute_tool_unrelated_unknown_tool_has_no_search_hint(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool("nonexistent_tool", {}))
        assert "run_bash" not in result["error"]

    def test_execute_tool_update_feature_status(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(
            t.execute_tool("update_feature_status", {"feature_id": 1, "status": "done"})
        )
        # Should not be an error
        assert "error" not in result or result.get("success")

    def test_execute_tool_normalises_camel_case_args(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        # featureId → feature_id normalisation
        result = json.loads(
            t.execute_tool("update_feature_status", {"featureId": 1, "status": "in_progress"})
        )
        assert "error" not in result or result.get("success")

    # execute_tool: Leader role confined to progress/ for write_file/append_file
    #
    # Regression coverage for a real incident: the Leader rewrote
    # backend/app/api/v1/professionals.py and backend/tests/test_professionals.py
    # end-to-end while chasing a repeated E2E failure, introducing a real
    # regression, because write_file() only checked SAFE_WRITE_DIRS (shared by
    # every role) with nothing role-specific stopping it.

    def test_leader_write_file_inside_progress_succeeds(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool(
            "write_file", {"path": "progress/current.md", "content": "plan"}, role="leader"
        ))
        assert "error" not in result
        assert (tmp_path / "progress" / "current.md").read_text() == "plan"

    def test_leader_write_file_outside_progress_is_blocked(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool(
            "write_file", {"path": "src/app.py", "content": "malicious rewrite"}, role="leader"
        ))
        assert "error" in result
        assert "Leader" in result["error"]
        assert not (tmp_path / "src" / "app.py").exists()

    def test_leader_append_file_outside_progress_is_blocked(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool(
            "append_file", {"path": "tests/test_app.py", "content": "extra"}, role="leader"
        ))
        assert "error" in result
        assert "Leader" in result["error"]
        assert not (tmp_path / "tests" / "test_app.py").exists()

    def test_leader_write_file_absolute_path_into_progress_still_allowed(self, monkeypatch, tmp_path):
        # _normalize_agent_path must convert an absolute-but-inside-cwd path
        # the same way _is_safe_path already does, so this narrower
        # progress/-only check doesn't reject a legitimate write just
        # because it wasn't given as a plain relative path.
        t = self._load_tools(monkeypatch, tmp_path)
        abs_path = str(tmp_path / "progress" / "current.md")
        result = json.loads(t.execute_tool(
            "write_file", {"path": abs_path, "content": "plan"}, role="leader"
        ))
        assert "error" not in result

    def test_non_leader_role_is_not_restricted_to_progress(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool(
            "write_file", {"path": "src/app.py", "content": "real impl"}, role="implementer"
        ))
        assert "error" not in result
        assert (tmp_path / "src" / "app.py").read_text() == "real impl"

    def test_no_role_defaults_to_unrestricted(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.execute_tool("write_file", {"path": "src/app.py", "content": "x"}))
        assert "error" not in result


# ── tools.py: run_playwright_tests (Python/Node stack branching) ─────────────

class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_MINI_E2E_PROFILES = {
    "backend": {
        "python-fastapi": {
            "name": "Python + FastAPI", "language": "Python 3.9+",
            "dirs": "src/...", "test_runner": "python3 -m pytest tests/ -v",
            "server_cmd": "python3 -m uvicorn src.main:app --port 8000",
            "db_family": "asyncpg",
            "safe_write_dirs": ["src/", "tests/", "e2e/"],
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
    },
    "defaults": {
        "backend": "python-fastapi", "frontend": "react-tailwind",
        "database": "json", "e2e_runner": "playwright",
    },
}


class TestRunPlaywrightTests:
    def _load_tools(self, monkeypatch, tmp_path: Path, stack_config: dict = None):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "feature_list.json").write_text(json.dumps([
            {"id": 1, "title": "T", "status": "pending", "description": "d",
             "e2e": False, "depends_on": []}
        ]))
        for k in ("STACK_BACKEND", "STACK_FRONTEND", "STACK_E2E", "APP_NAME",
                  "SAFE_WRITE_DIRS", "CODE_TREE_DIRS"):
            monkeypatch.delenv(k, raising=False)

        if stack_config is not None:
            (tmp_path / "stack_profiles.json").write_text(json.dumps(_MINI_E2E_PROFILES))
            (tmp_path / "stack_config.json").write_text(json.dumps(stack_config))

        for mod in ["sandbox"]:
            monkeypatch.setitem(sys.modules, mod, MagicMock())

        # Drop both tools and stack_layout so resolve_layout's lru_cache starts
        # fresh per test — otherwise an earlier test's resolved layout (cached
        # on the function object) would leak into this one.
        for key in list(sys.modules.keys()):
            if key in ("tools", "stack_layout"):
                del sys.modules[key]

        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))

        return importlib.import_module("tools")

    def test_default_stack_uses_python_pytest_playwright(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "pytest --co" in cmd:
                return _FakeCompleted(returncode=0, stdout="playwright fixture collected")
            return _FakeCompleted(returncode=0, stdout="1 passed", stderr="")

        monkeypatch.setattr(t.subprocess, "run", fake_run)
        result = json.loads(t.run_playwright_tests(base_url="http://localhost:8000"))

        assert result["success"] is True
        # Not "python -m pytest": the command is built from sys.executable
        # (see tools.py::_run_playwright_tests_python), which is an absolute
        # interpreter path (e.g. "/usr/bin/python3.9") on every real machine,
        # never the literal string "python". Match runtime-agnostically.
        assert any("-m pytest tests/e2e/" in c for c in calls)
        assert not any("npx playwright test" in c for c in calls)

    def test_node_stack_uses_npx_playwright_test(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path,
                             stack_config={"backend": "python-fastapi", "e2e_runner": "playwright-node"})
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "playwright --version" in cmd:
                return _FakeCompleted(returncode=0, stdout="Version 1.40.0")
            return _FakeCompleted(returncode=0, stdout="1 passed", stderr="")

        monkeypatch.setattr(t.subprocess, "run", fake_run)
        result = json.loads(t.run_playwright_tests(base_url="http://localhost:8000"))

        assert result["success"] is True
        assert any("npx playwright test e2e/" in c for c in calls)
        assert any("PLAYWRIGHT_BASE_URL=http://localhost:8000" in c for c in calls)
        # Same sys.executable reasoning as above — "python -m pytest" would
        # never match even if the python branch ran by mistake, making this
        # assertion vacuous. "-m pytest" is the runtime-agnostic marker.
        assert not any("-m pytest" in c for c in calls)

    def test_node_stack_installs_playwright_when_missing(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path,
                             stack_config={"backend": "python-fastapi", "e2e_runner": "playwright-node"})
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "playwright --version" in cmd:
                return _FakeCompleted(returncode=1, stdout="", stderr="command not found")
            if "npm install" in cmd:
                return _FakeCompleted(returncode=0, stdout="installed")
            return _FakeCompleted(returncode=0, stdout="1 passed", stderr="")

        monkeypatch.setattr(t.subprocess, "run", fake_run)
        result = json.loads(t.run_playwright_tests(base_url="http://localhost:8000"))

        assert any("npm install -D @playwright/test" in c for c in calls)
        assert result["success"] is True

    def test_explicit_test_path_overrides_stack_default(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path,
                             stack_config={"backend": "python-fastapi", "e2e_runner": "playwright-node"})
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(returncode=0, stdout="1 passed", stderr="")

        monkeypatch.setattr(t.subprocess, "run", fake_run)
        t.run_playwright_tests(test_path="e2e/biovet.spec.ts", base_url="http://localhost:8000")

        assert any("npx playwright test e2e/biovet.spec.ts" in c for c in calls)
