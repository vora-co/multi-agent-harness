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
        h = _load_harness(monkeypatch, tmp_path)
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
        assert any("python -m pytest tests/e2e/" in c for c in calls)
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
        assert not any("python -m pytest" in c for c in calls)

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
