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


class TestPerFeatureCostTracking:
    """
    COST_BUDGET_USD is global — one pathological feature (feature #74's 2
    full 50-iteration E2E cycles) can consume the whole session budget with
    nothing per-feature ever noticing. _track_usage now also accumulates
    into _FEATURE_COSTS, keyed by _CURRENT_FEATURE_ID (the same contextvar
    already set for the duration of run_feature_cycle for structured
    logging).
    """

    def _make_usage(self, prompt_tokens=0, completion_tokens=0):
        from types import SimpleNamespace
        return SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    def test_accumulates_under_current_feature_id(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        token = h._CURRENT_FEATURE_ID.set(74)
        try:
            h._track_usage("e2e_tester", self._make_usage(1000, 500))
        finally:
            h._CURRENT_FEATURE_ID.reset(token)

        assert h._FEATURE_COSTS["74"]["calls"] == 1
        assert h._FEATURE_COSTS["74"]["prompt_tokens"] == 1000
        assert h._FEATURE_COSTS["74"]["completion_tokens"] == 500
        assert h._FEATURE_COSTS["74"]["cost_usd"] > 0

    def test_multiple_calls_for_the_same_feature_accumulate(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        token = h._CURRENT_FEATURE_ID.set(74)
        try:
            h._track_usage("implementer", self._make_usage(100, 50))
            h._track_usage("e2e_tester", self._make_usage(200, 100))
        finally:
            h._CURRENT_FEATURE_ID.reset(token)

        assert h._FEATURE_COSTS["74"]["calls"] == 2
        assert h._FEATURE_COSTS["74"]["prompt_tokens"] == 300

    def test_no_current_feature_id_does_not_populate(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        h._track_usage("leader", self._make_usage(100, 50))
        assert h._FEATURE_COSTS == {}

    def test_feature_cost_usd_helper(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._feature_cost_usd(999) == 0.0
        token = h._CURRENT_FEATURE_ID.set(5)
        try:
            h._track_usage("implementer", self._make_usage(1000, 1000))
        finally:
            h._CURRENT_FEATURE_ID.reset(token)
        assert h._feature_cost_usd(5) > 0
        assert h._feature_cost_usd(5) == h._FEATURE_COSTS["5"]["cost_usd"]

    def test_write_session_costs_includes_per_feature(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        token = h._CURRENT_FEATURE_ID.set(74)
        try:
            h._track_usage("e2e_tester", self._make_usage(1000, 500))
        finally:
            h._CURRENT_FEATURE_ID.reset(token)

        h._write_session_costs()

        summary = json.loads((tmp_path / "progress" / "session_costs.json").read_text())
        assert "per_feature" in summary
        assert summary["per_feature"]["74"]["calls"] == 1


class TestFeatureBudgetCutoff:
    """
    FEATURE_BUDGET_USD (optional, disabled by default): once a single
    feature's accumulated cost crosses this limit, run_feature_cycle stops
    its remaining retries instead of paying for another full
    impl -> review -> E2E attempt, independent of the global session budget.
    """

    def _patch_cycle(self, h, monkeypatch):
        calls = []
        monkeypatch.setattr(h, "spawn_spec_writer", MagicMock(return_value="progress/spec_1.md"))
        monkeypatch.setattr(h, "spawn_implementer",
                             MagicMock(side_effect=lambda *a, **kw: calls.append(1) or "ok"))
        monkeypatch.setattr(h, "spawn_e2e_tester", MagicMock(return_value="E2E_PASSED"))
        monkeypatch.setattr(h, "spawn_reviewer", MagicMock(return_value="APPROVED"))
        monkeypatch.setattr(h, "_fire", MagicMock())
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=None))
        return calls

    def test_stops_remaining_retries_once_over_budget(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"FEATURE_BUDGET_USD": "0.01"})
        (tmp_path / "progress").mkdir(exist_ok=True)
        impl_calls = self._patch_cycle(h, monkeypatch)
        h._FEATURE_COSTS["1"] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 3, "cost_usd": 0.05}

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is False
        assert "FEATURE_BUDGET_EXCEEDED" in result["final_verdict"]
        assert impl_calls == []  # never even attempted

    def test_disabled_by_default(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)  # FEATURE_BUDGET_USD unset -> 0 -> disabled
        (tmp_path / "progress").mkdir(exist_ok=True)
        impl_calls = self._patch_cycle(h, monkeypatch)
        h._FEATURE_COSTS["1"] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 3, "cost_usd": 999.0}

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        assert impl_calls == [1]

    def test_below_threshold_proceeds_normally(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"FEATURE_BUDGET_USD": "10.00"})
        (tmp_path / "progress").mkdir(exist_ok=True)
        impl_calls = self._patch_cycle(h, monkeypatch)
        h._FEATURE_COSTS["1"] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 1, "cost_usd": 0.001}

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        assert impl_calls == [1]


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


class TestImplReviewE2eOrder:
    """
    ARCHITECTURE_REVIEW §8.C: the cycle used to run impl -> E2E -> review,
    so every ordinary reviewer rejection wasted a full Playwright cycle
    (force-recreate + cold compile + browser) it never needed. Now
    impl -> review -> E2E, with the before_approval_finalized gate moved to
    fire only after BOTH have passed. All tests here use e2e=True — the
    only way to observe step order — unlike most other cycle tests in this
    file, which use e2e=False and are therefore order-agnostic.
    """

    def _patch_cycle(self, h, monkeypatch, *, review_results, e2e_results=None, gate_result=None):
        monkeypatch.setattr(h, "spawn_spec_writer", MagicMock(return_value="progress/spec_1.md"))
        monkeypatch.setattr(h, "spawn_implementer", MagicMock(return_value="ok"))
        monkeypatch.setattr(h, "spawn_e2e_tester",
                             MagicMock(side_effect=e2e_results or ["E2E_PASSED"] * len(review_results)))
        monkeypatch.setattr(h, "spawn_reviewer", MagicMock(side_effect=review_results))
        monkeypatch.setattr(h, "_fire", MagicMock())
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=gate_result))
        monkeypatch.setattr(h, "_track_usage", MagicMock())

    def test_review_rejection_never_calls_e2e_tester(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"MAX_RETRIES_REVIEW": "2"})
        self._patch_cycle(h, monkeypatch, review_results=["REJECTED: bad code", "APPROVED"])

        result = h.run_feature_cycle(1, "desc", e2e=True)

        assert result["approved"] is True
        assert h.spawn_reviewer.call_count == 2
        # E2E only ran once — for the attempt whose review actually passed.
        assert h.spawn_e2e_tester.call_count == 1

    def test_gate_fires_only_after_e2e_passes(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch, review_results=["APPROVED"])

        result = h.run_feature_cycle(1, "desc", e2e=True)

        assert result["approved"] is True
        h.spawn_e2e_tester.assert_called_once()  # E2E ran before the gate could finalize
        h._fire_gate.assert_called_once()
        assert h._fire_gate.call_args.kwargs["review_result"] == "APPROVED"

    def test_gate_veto_happens_after_e2e_already_ran(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"MAX_RETRIES_REVIEW": "1"})
        self._patch_cycle(h, monkeypatch, review_results=["APPROVED"],
                           gate_result={"plugin": "governance", "reason": "policy block"})

        result = h.run_feature_cycle(1, "desc", e2e=True)

        assert result["approved"] is False
        h.spawn_e2e_tester.assert_called_once()  # already paid for by the time the gate vetoed

    def test_e2e_failure_retries_and_repays_review(self, monkeypatch, tmp_path):
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path, {"MAX_RETRIES_REVIEW": "2"})
        self._patch_cycle(
            h, monkeypatch,
            review_results=["APPROVED", "APPROVED"],
            e2e_results=["E2E_FAILED: timeout", "E2E_PASSED"],
        )

        result = h.run_feature_cycle(1, "desc", e2e=True)

        assert result["approved"] is True
        assert h.spawn_reviewer.call_count == 2  # re-reviewed after the E2E-driven retry
        assert h.spawn_e2e_tester.call_count == 2


class TestRunAllPending:
    """
    /auto's underlying driver: a code-level alternative to the Leader-LLM
    for the common case of "run every pending feature in dependency order."
    Mocks run_feature_cycle directly (not the sub-agents) since this is
    about the driver's own orchestration, not the cycle internals — those
    are covered by TestDependencyGate and friends.
    """

    def test_processes_pending_features_in_dependency_order(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 2, "title": "T2", "description": "d2", "status": "pending", "e2e": False, "depends_on": [1]},
            {"id": 1, "title": "T1", "description": "d1", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False:
            calls.append(fid) or {"approved": True, "attempts": 1, "final_verdict": "APPROVED"})

        result = h.run_all_pending()

        assert calls == [1, 2]  # dependency order, not declaration order
        assert result["stopped_reason"] == "empty"
        assert [r["feature_id"] for r in result["results"]] == [1, 2]
        assert all(r["approved"] for r in result["results"])

        features = {f["id"]: f for f in h._read_feature_list_raw()}
        assert features[1]["status"] == "done"
        assert features[2]["status"] == "done"

    def test_marks_failed_when_not_approved(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False:
            {"approved": False, "attempts": 3, "final_verdict": "REJECTED: bad code"})

        h.run_all_pending()

        features = {f["id"]: f for f in h._read_feature_list_raw()}
        assert features[1]["status"] == "failed"

    def test_stops_on_budget_exceeded_leaves_feature_pending(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "pending", "e2e": False, "depends_on": []},
            {"id": 2, "title": "T2", "description": "d2", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        calls = []

        def fake_cycle(fid, desc, e2e=False):
            calls.append(fid)
            h._BUDGET_EXCEEDED = True  # simulate budget breach mid-run, like _track_usage would
            return {"approved": True, "attempts": 1, "final_verdict": "APPROVED"}
        monkeypatch.setattr(h, "run_feature_cycle", fake_cycle)

        result = h.run_all_pending()

        assert calls == [1]  # never even attempted #2
        assert result["stopped_reason"] == "budget_exceeded"
        features = {f["id"]: f for f in h._read_feature_list_raw()}
        assert features[1]["status"] == "done"
        assert features[2]["status"] == "pending"  # left untouched, not "failed"

    def test_only_feature_id_runs_just_that_one(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "pending", "e2e": False, "depends_on": []},
            {"id": 2, "title": "T2", "description": "d2", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False:
            calls.append(fid) or {"approved": True, "attempts": 1, "final_verdict": "APPROVED"})

        result = h.run_all_pending(only_feature_id=2)

        assert calls == [2]
        assert result["stopped_reason"] == "single_feature_done"
        features = {f["id"]: f for f in h._read_feature_list_raw()}
        assert features[1]["status"] == "pending"  # untouched
        assert features[2]["status"] == "done"

    def test_only_feature_id_requires_pending_status(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "done", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False: calls.append(fid))

        result = h.run_all_pending(only_feature_id=1)

        assert calls == []
        assert result["stopped_reason"] == "not_pending"

    def test_dependency_errors_abort_without_running_anything(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "pending", "e2e": False, "depends_on": [1]},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        calls = []
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False: calls.append(fid))

        result = h.run_all_pending()

        assert calls == []
        assert result["stopped_reason"] == "dependency_errors"

    def test_empty_when_nothing_pending(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d1", "status": "done", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)

        result = h.run_all_pending()

        assert result == {"results": [], "stopped_reason": "empty"}

    def test_writes_current_and_history_md(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "Add login", "description": "d1", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "run_feature_cycle", lambda fid, desc, e2e=False:
            {"approved": True, "attempts": 1, "final_verdict": "APPROVED"})

        h.run_all_pending()

        current = (tmp_path / "progress" / "current.md").read_text()
        assert "#1" in current and "Add login" in current

        history = (tmp_path / "progress" / "history.md").read_text()
        assert "#1" in history and "Add login" in history
        assert "done" in history


class TestDependencyGate:
    """
    Regression coverage for a real incident: the Leader started feature #72
    via run_feature_cycle while feature #71 (a hard dependency) had status
    "failed", not "done". The only protection was a prose instruction in the
    Leader's injected context — nothing in run_feature_cycle() itself
    stopped it. Same _patch_cycle pattern as TestFeatureCycleVerbosityIntegration.
    """

    def _patch_cycle(self, h, monkeypatch):
        spec_called = []
        monkeypatch.setattr(h, "spawn_spec_writer",
                             MagicMock(side_effect=lambda *a, **kw: spec_called.append(1) or "progress/spec_1.md"))
        monkeypatch.setattr(h, "spawn_implementer", MagicMock(return_value="ok"))
        monkeypatch.setattr(h, "spawn_e2e_tester", MagicMock(return_value="E2E_PASSED"))
        monkeypatch.setattr(h, "spawn_reviewer", MagicMock(return_value="APPROVED"))
        monkeypatch.setattr(h, "_fire", MagicMock())
        monkeypatch.setattr(h, "_fire_gate", MagicMock(return_value=None))
        monkeypatch.setattr(h, "_track_usage", MagicMock())
        return spec_called

    def test_blocked_when_dependency_not_done(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 71, "title": "T71", "description": "d", "status": "failed", "e2e": False, "depends_on": []},
            {"id": 72, "title": "T72", "description": "d", "status": "pending", "e2e": False, "depends_on": [71]},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        spec_called = self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(72, "desc", e2e=False)

        assert result["approved"] is False
        assert "DEPENDENCY_ERROR" in result["final_verdict"]
        assert "71" in result["final_verdict"]
        assert spec_called == []  # blocked before any sub-agent ran

    def test_proceeds_when_dependency_done(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 71, "title": "T71", "description": "d", "status": "done", "e2e": False, "depends_on": []},
            {"id": 72, "title": "T72", "description": "d", "status": "pending", "e2e": False, "depends_on": [71]},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        spec_called = self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(72, "desc", e2e=False)

        assert result["approved"] is True
        assert spec_called == [1]

    def test_proceeds_when_no_depends_on(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 1, "title": "T1", "description": "d", "status": "pending", "e2e": False, "depends_on": []},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        spec_called = self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        assert spec_called == [1]

    def test_lists_all_unmet_dependencies(self, monkeypatch, tmp_path):
        _write_fl(tmp_path, [
            {"id": 70, "title": "T70", "description": "d", "status": "pending", "e2e": False, "depends_on": []},
            {"id": 71, "title": "T71", "description": "d", "status": "failed", "e2e": False, "depends_on": []},
            {"id": 72, "title": "T72", "description": "d", "status": "pending", "e2e": False, "depends_on": [70, 71]},
        ])
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(72, "desc", e2e=False)

        assert "70" in result["final_verdict"]
        assert "71" in result["final_verdict"]

    def test_best_effort_proceeds_when_feature_list_missing(self, monkeypatch, tmp_path):
        # No feature_list.json on disk at all — _read_feature_list_raw()
        # returns [], so _this_feature is None and the gate is a no-op
        # rather than blocking every run_feature_cycle call.
        (tmp_path / "progress").mkdir(exist_ok=True)
        h = _load_harness(monkeypatch, tmp_path)
        spec_called = self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        assert spec_called == [1]


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


class TestE2eRetryEvidenceBlock:
    """
    Regression coverage for feature #74: attempt 2's e2e_tester log showed
    the same 3 files re-read and the same test re-run 3 times before hitting
    max_iter again, because the retry task was identical to attempt 1's —
    the previous failure's evidence (already on disk right up until the
    stale-report cleanup deletes it) was never injected. E2E-side
    counterpart to spawn_implementer's own RETRY #{attempt} block.
    """

    def test_includes_previous_reason_and_files_touched(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_74.json").write_text(json.dumps({
            "schema_version": 1, "status": "failed", "tests_passed": False,
            "files_touched": [], "reason": "TimeoutError waiting for #prof-name after login+submit",
        }))
        (tmp_path / "progress" / "impl_74.md").write_text("# Impl report\nSwitched redirect() call site.")
        (tmp_path / "progress" / "impl_74.json").write_text(json.dumps({
            "schema_version": 1, "status": "done", "tests_passed": True,
            "files_touched": ["frontend/src/app/(dashboard)/layout.tsx"],
        }))

        block = h._e2e_retry_evidence_block(74)

        assert "PREVIOUS E2E ATTEMPT FAILED" in block
        assert "TimeoutError waiting for #prof-name" in block
        assert "WHAT THE IMPLEMENTER CHANGED IN RESPONSE" in block
        assert "layout.tsx" in block
        assert "Switched redirect() call site" in block
        assert "Start by re-running the exact failing test" in block

    def test_empty_when_nothing_on_disk(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        assert h._e2e_retry_evidence_block(74) == ""

    def test_falls_back_gracefully_without_structured_json(self, monkeypatch, tmp_path):
        # No sibling .json files (legacy progress/ dir) — still returns
        # something useful from the .md prose alone rather than "".
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "impl_74.md").write_text("# Impl report\nFixed the redirect bug.")

        block = h._e2e_retry_evidence_block(74)

        assert "Fixed the redirect bug" in block

    def test_spawn_e2e_tester_injects_block_only_on_retry(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_74.json").write_text(json.dumps({
            "schema_version": 1, "status": "failed", "tests_passed": False,
            "files_touched": [], "reason": "TimeoutError waiting for #prof-name",
        }))
        captured_tasks = []

        def fake_run_agent(system_prompt, tools, task, **kw):
            captured_tasks.append(task)
            return "E2E_PASSED"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        h.spawn_e2e_tester(74, attempt=1)
        assert "PREVIOUS E2E ATTEMPT FAILED" not in captured_tasks[0]

        # Re-seed the report attempt 1's own cleanup would have removed —
        # simulates attempt 1 actually having failed and left evidence.
        (tmp_path / "progress" / "e2e_74.json").write_text(json.dumps({
            "schema_version": 1, "status": "failed", "tests_passed": False,
            "files_touched": [], "reason": "TimeoutError waiting for #prof-name",
        }))
        h.spawn_e2e_tester(74, attempt=2)
        assert "PREVIOUS E2E ATTEMPT FAILED" in captured_tasks[1]
        assert "TimeoutError waiting for #prof-name" in captured_tasks[1]

    def test_evidence_captured_before_stale_cleanup_deletes_it(self, monkeypatch, tmp_path):
        # The whole point: on attempt > 1, the "stale" report the cleanup
        # step is about to delete IS the previous attempt's evidence — it
        # must be read first, not lost.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "e2e_74.json").write_text(json.dumps({
            "schema_version": 1, "status": "failed", "tests_passed": False,
            "files_touched": [], "reason": "synthesized-by-harness evidence from max_iter",
        }))
        captured_tasks = []
        monkeypatch.setattr(h, "run_agent", lambda system_prompt, tools, task, **kw:
            captured_tasks.append(task) or "E2E_PASSED")

        h.spawn_e2e_tester(74, attempt=2)

        assert not (tmp_path / "progress" / "e2e_74.json").exists()  # cleanup still ran
        assert "synthesized-by-harness evidence from max_iter" in captured_tasks[0]  # but was captured first


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


class TestSpecWriterStaleE2eCache:
    """
    Regression coverage for a real incident: spec_74.md sent E2E tests to
    e2e/biovet.spec.ts — the legacy Node/@playwright/test suite from
    features #27-55 — while this project's resolved e2e runner is Python/
    pytest-playwright (tests/e2e/*.py). The implementer wrote 4 correct
    tests there that the real run_cmd never executes, and because the
    cached spec is injected in full on every retry (and survives any manual
    reset to "pending"), the poisoned path outlived every attempt. A
    prompt-only rule can't catch this because the spec_writer agent isn't
    invoked at all on a cache hit — spawn_spec_writer's own cache check must
    do it.
    """

    _STACK_PROFILES = json.dumps({
        "e2e_runner": {
            "playwright": {
                "name": "Playwright (Python / pytest-playwright)",
                "runtime": "python", "file_ext": ".py", "test_dir": "tests/e2e/",
                "run_cmd": "python3 -m pytest tests/e2e/ -v --tb=short",
            },
            "playwright-node": {
                "name": "Playwright (Node / @playwright/test)",
                "runtime": "node", "file_ext": ".spec.ts", "test_dir": "e2e/",
                "run_cmd": "npx playwright test",
            },
        }
    })

    def _load(self, monkeypatch, tmp_path, extra_env=None):
        # resolve_layout/all_e2e_runner_profiles are both @lru_cache'd on the
        # stack_layout module object, which _load_harness doesn't purge from
        # sys.modules (only "harness"/"harness.*") — same workaround as
        # TestValidateSpecStackAware.
        for key in list(sys.modules.keys()):
            if key in ("tools", "stack_layout"):
                del sys.modules[key]
        h = _load_harness(monkeypatch, tmp_path, extra_env)
        (tmp_path / "stack_profiles.json").write_text(self._STACK_PROFILES)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)  # _validate_spec: no-op
        return h

    def _stub_run_agent(self, h, monkeypatch, spec_path, content="# fresh spec\ntests/e2e/test_feature_74.py"):
        calls = []

        def fake_run_agent(*a, **kw):
            calls.append(1)
            with open(spec_path, "w", encoding="utf-8") as f:
                f.write(content)
            return spec_path
        monkeypatch.setattr(h, "run_agent", fake_run_agent)
        return calls

    def test_reuses_clean_cached_spec_without_calling_agent(self, monkeypatch, tmp_path):
        h = self._load(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_74.md").write_text(
            "## Tests to write\nCreate tests/e2e/test_feature_74.py covering the happy path."
        )
        calls = self._stub_run_agent(h, monkeypatch, "progress/spec_74.md")

        result = h.spawn_spec_writer(74, "desc")

        assert result == "progress/spec_74.md"
        assert calls == []  # cache hit — agent never invoked
        assert not (tmp_path / "progress" / "spec_74.md.stale").exists()

    def test_quarantines_and_regenerates_when_pointed_at_other_runner_test_dir(self, monkeypatch, tmp_path):
        h = self._load(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        poisoned = "## Tests to write\nAppend a new describe block to e2e/biovet.spec.ts."
        (tmp_path / "progress" / "spec_74.md").write_text(poisoned)
        calls = self._stub_run_agent(h, monkeypatch, "progress/spec_74.md")

        result = h.spawn_spec_writer(74, "desc")

        assert result == "progress/spec_74.md"
        assert calls == [1]  # cache miss — agent was invoked to regenerate

        stale_path = tmp_path / "progress" / "spec_74.md.stale"
        assert stale_path.exists()
        assert stale_path.read_text() == poisoned  # original content preserved for debugging

        fresh = (tmp_path / "progress" / "spec_74.md").read_text()
        assert "e2e/biovet.spec.ts" not in fresh
        assert "tests/e2e/test_feature_74.py" in fresh

    def test_no_false_positive_when_stack_profiles_missing(self, monkeypatch, tmp_path):
        # No stack_profiles.json written this time — all_e2e_runner_profiles()
        # returns {} best-effort, so the gate is a no-op rather than blocking
        # (or worse, quarantining) every cached spec in a project without one.
        for key in list(sys.modules.keys()):
            if key in ("tools", "stack_layout"):
                del sys.modules[key]
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_74.md").write_text(
            "## Tests to write\nAppend to e2e/biovet.spec.ts."
        )
        calls = self._stub_run_agent(h, monkeypatch, "progress/spec_74.md")

        result = h.spawn_spec_writer(74, "desc")

        assert result == "progress/spec_74.md"
        assert calls == []  # no stack_profiles.json → no other profiles to compare → reused as-is


class TestBugfixReproEnforcement:
    """
    Regression coverage for the feature #77 incident (biovet-harness,
    2026-07-14/15): a bug-fix spec whose "reproduction" existed only as
    confident prose ("toggle the sede, save, the switch flips back") asserted
    a backend persistence bug that didn't exist; the implementer burned
    2 rounds × 2 attempts × 80 iterations in a healthy layer. Harness-side
    enforcement added: bug-fix features get the repro-script path injected
    into the spec_writer task, a bug-fix spec generated without a repro
    script is annotated non-blockingly (same philosophy as _validate_spec),
    and an existing repro script is surfaced in the implementer task with
    the run-first/run-last protocol.
    """

    def _capture_run_agent(self, h, monkeypatch, result_path, spec_body=None, on_call=None):
        h.impl_cfg.TOOLS = []  # real list — spawn_implementer's tool-exposure path iterates it
        captured = {"tasks": []}

        def fake_run_agent(system_prompt, tools, task, **kw):
            captured["tasks"].append(task)
            captured["task"] = task
            captured["tools"] = tools
            if spec_body is not None:
                with open(result_path, "w", encoding="utf-8") as f:
                    f.write(spec_body)
            if on_call is not None:
                on_call(len(captured["tasks"]))
            return result_path
        monkeypatch.setattr(h, "run_agent", fake_run_agent)
        return captured

    # ── _is_bugfix_feature heuristic ─────────────────────────────────────────
    def test_is_bugfix_feature_positives(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        for desc in [
            "Fix: sede switch flips back to true after save",
            "bug: toggle state lost on reload",
            "is_active no persiste al guardar la sede",
            "endpoint returns the wrong value for tenant_id",
            "el endpoint devuelve el valor incorrecto",
            "regression in professionals list ordering",
        ]:
            assert h._is_bugfix_feature(desc), desc

    def test_is_bugfix_feature_negatives(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        for desc in [
            "Add CSV export to the reports page",
            "Implement pagination for the professionals list",
            "New login page with role-based redirect",
            "",
        ]:
            assert not h._is_bugfix_feature(desc), desc

    # ── _existing_repro_script ───────────────────────────────────────────────
    def test_existing_repro_script_finds_py_and_sh(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        assert h._existing_repro_script(5) is None
        (tmp_path / "progress" / "repro_5.py").write_text("assert False")
        assert h._existing_repro_script(5) == "progress/repro_5.py"
        (tmp_path / "progress" / "repro_6.sh").write_text("exit 1")
        assert h._existing_repro_script(6) == "progress/repro_6.sh"

    # ── spec_writer task injection ───────────────────────────────────────────
    def test_spec_writer_task_names_repro_path_for_bugfix(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._capture_run_agent(h, monkeypatch, "progress/spec_9.md")

        h.spawn_spec_writer(9, "Fix: is_active no persiste al togglear la sede")

        assert "progress/repro_9.py" in captured["task"]
        assert "CONFIRMED or HYPOTHESIS" in captured["task"]

    def test_spec_writer_task_has_no_repro_hint_for_regular_feature(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._capture_run_agent(h, monkeypatch, "progress/spec_9.md")

        h.spawn_spec_writer(9, "Add CSV export to the reports page")

        assert "repro_9" not in captured["task"]

    # ── blocking repro gate: quarantine + ONE regeneration, then fallback ────
    def test_bugfix_gate_regenerates_once_then_falls_back_to_annotation(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        body = "# Spec\nRoot cause: the backend does not persist is_active."
        captured = self._capture_run_agent(h, monkeypatch, "progress/spec_9.md", spec_body=body)

        h.spawn_spec_writer(9, "Fix: is_active no persiste al togglear la sede")

        # Exactly one regeneration — never a loop.
        assert len(captured["tasks"]) == 2
        assert "REPRO GATE" in captured["tasks"][1]
        assert "NOT_FEASIBLE" in captured["tasks"][1]  # escape valve offered explicitly
        # First (rejected) spec quarantined for debugging, same mechanism as .stale.
        assert (tmp_path / "progress" / "spec_9.md.norepro").exists()
        # Second spec still has neither → fallback: annotate and continue.
        spec = (tmp_path / "progress" / "spec_9.md").read_text()
        assert "Missing reproduction script" in spec
        assert "HYPOTHESIS" in spec  # tells the implementer to downgrade the claims

    def test_bugfix_gate_passes_when_regeneration_writes_repro(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)

        def write_repro_on_second_call(n_calls):
            if n_calls == 2:
                (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        captured = self._capture_run_agent(
            h, monkeypatch, "progress/spec_9.md",
            spec_body="# Spec\nRoot cause: the backend does not persist is_active.",
            on_call=write_repro_on_second_call)

        h.spawn_spec_writer(9, "Fix: is_active no persiste al togglear la sede")

        assert len(captured["tasks"]) == 2
        spec = (tmp_path / "progress" / "spec_9.md").read_text()
        assert "Missing reproduction script" not in spec  # gate satisfied on retry

    def test_not_feasible_declaration_satisfies_gate(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._capture_run_agent(
            h, monkeypatch, "progress/spec_9.md",
            spec_body="# Spec\nREPRO: NOT_FEASIBLE — purely visual hover glitch, "
                      "no state change observable at the API layer.\nRoot cause: HYPOTHESIS ...")

        h.spawn_spec_writer(9, "Fix: tooltip flickers on hover")

        # Consciously absent, with a visible reason — no regeneration, no annotation.
        assert len(captured["tasks"]) == 1
        assert not (tmp_path / "progress" / "spec_9.md.norepro").exists()
        spec = (tmp_path / "progress" / "spec_9.md").read_text()
        assert "Missing reproduction script" not in spec

    def test_bugfix_spec_with_repro_is_not_annotated(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "repro_9.py").write_text("assert get_sede().is_active is False")
        captured = self._capture_run_agent(h, monkeypatch, "progress/spec_9.md",
                                           spec_body="# Spec\nRoot cause (CONFIRMED via progress/repro_9.py): ...")

        h.spawn_spec_writer(9, "Fix: is_active no persiste al togglear la sede")

        assert len(captured["tasks"]) == 1  # gate satisfied first try — no regeneration
        spec = (tmp_path / "progress" / "spec_9.md").read_text()
        assert "Missing reproduction script" not in spec

    def test_regular_spec_without_repro_is_not_annotated(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        self._capture_run_agent(h, monkeypatch, "progress/spec_9.md",
                                spec_body="# Spec\nCreate the CSV export endpoint.")

        h.spawn_spec_writer(9, "Add CSV export to the reports page")

        spec = (tmp_path / "progress" / "spec_9.md").read_text()
        assert "Missing reproduction script" not in spec

    # ── implementer task injection ───────────────────────────────────────────
    def test_implementer_task_surfaces_existing_repro(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        captured = self._capture_run_agent(h, monkeypatch, "progress/impl_9.md")

        h.spawn_implementer(9, "Fix: is_active no persiste al togglear la sede")

        assert "progress/repro_9.py" in captured["task"]
        assert "run it FIRST" in captured["task"]
        assert "PREMISE DISCREPANCY" in captured["task"]

    def test_implementer_task_without_repro_has_no_protocol_block(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._capture_run_agent(h, monkeypatch, "progress/impl_9.md")

        h.spawn_implementer(9, "Fix: is_active no persiste al togglear la sede")

        assert "Reproduction script (mandatory protocol)" not in captured["task"]

    # ── CONFIRMED→HYPOTHESIS downgrade invariant (independent of the gate) ───
    def test_downgrade_unbacked_confirmed_unit(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        text = ("Root cause (CONFIRMED): backend drops is_active on save. "
                "UNCONFIRMED user reports also mention ordering.")

        out = h._downgrade_unbacked_confirmed(text, 9)
        assert "auto-downgraded" in out
        assert "(CONFIRMED)" not in out
        assert "UNCONFIRMED user reports" in out  # word boundary — UNCONFIRMED untouched

        # No CONFIRMED label at all → same object back, no-op.
        plain = "Root cause: HYPOTHESIS — inferred from reading the router."
        assert h._downgrade_unbacked_confirmed(plain, 9) is plain

        # With a repro attached the label is trustworthy and preserved.
        (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        assert h._downgrade_unbacked_confirmed(text, 9) is text

    def test_implementer_injection_downgrades_confirmed_without_repro(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_9.md").write_text(
            "# Spec\nRoot cause (CONFIRMED): the backend does not persist is_active.")
        captured = self._capture_run_agent(h, monkeypatch, "progress/impl_9.md")

        h.spawn_implementer(9, "Fix: is_active no persiste al togglear la sede",
                            spec_path="progress/spec_9.md")

        # The unverified premise never travels with the confidence of a verified one.
        assert "auto-downgraded" in captured["task"]
        assert "(CONFIRMED)" not in captured["task"]
        # The spec file on disk is untouched — the downgrade happens at injection only.
        assert "(CONFIRMED)" in (tmp_path / "progress" / "spec_9.md").read_text()

    def test_implementer_injection_preserves_confirmed_with_repro(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        (tmp_path / "progress" / "spec_9.md").write_text(
            "# Spec\nRoot cause (CONFIRMED via progress/repro_9.py): array reordering.")
        captured = self._capture_run_agent(h, monkeypatch, "progress/impl_9.md")

        h.spawn_implementer(9, "Fix: is_active no persiste al togglear la sede",
                            spec_path="progress/spec_9.md")

        assert "(CONFIRMED via progress/repro_9.py)" in captured["task"]
        assert "auto-downgraded" not in captured["task"]


class TestRefutedPremiseSpecCache:
    """
    Regression coverage for the second half of the feature #77 incident: the
    poisoned spec (confidently asserting a backend persistence bug that
    attempt 1's own passing tests had already refuted) was reinjected
    verbatim on every retry and re-run — verified in the log, the round-2
    implementer spawned 2 seconds after run_feature_cycle and the spec_writer
    was never re-invoked. spawn_spec_writer's cache branch now quarantines a
    spec whose diagnosis was refuted by direct verification, using the same
    .stale mechanism as the stale-e2e-path check, and injects the refutation
    evidence into the regeneration task.
    """

    def _setup(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        monkeypatch.setattr(h, "_call_api_with_fallback", lambda *a, **kw: None)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_77.md").write_text(
            "# cached spec\nRoot cause: the backend does not persist is_active.")
        calls = {"tasks": []}

        def fake_run_agent(system_prompt, tools, task, **kw):
            calls["tasks"].append(task)
            with open("progress/spec_77.md", "w", encoding="utf-8") as f:
                f.write("# fresh spec")
            return "progress/spec_77.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)
        return h, calls

    def test_diagnosis_wrong_premise_quarantines_and_regenerates(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "diagnosis_77.json").write_text(json.dumps({
            "cause": "wrong_premise",
            "explanation": "pytest tests/test_branches.py passed on attempt 1 — is_active DOES persist",
        }))

        result = h.spawn_spec_writer(77, "some feature")

        assert result == "progress/spec_77.md"
        assert len(calls["tasks"]) == 1  # cache miss — regenerated
        stale = tmp_path / "progress" / "spec_77.md.stale"
        assert stale.exists()
        assert "does not persist is_active" in stale.read_text()  # original preserved
        # The regeneration task carries the refutation as a constraint.
        assert "REFUTED" in calls["tasks"][0]
        assert "is_active DOES persist" in calls["tasks"][0]
        assert "Do NOT reassert that premise" in calls["tasks"][0]

    def test_impl_json_premise_check_failed_regenerates(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "impl_77.json").write_text(json.dumps({
            "schema_version": 1, "status": "done", "tests_passed": True,
            "files_touched": [], "premise_check": "failed",
        }))
        (tmp_path / "progress" / "impl_77.md").write_text(
            "# Impl report\n\nPREMISE_CHECK: FAILED\nSpec claimed the backend drops "
            "is_active; ran pytest tests/test_branches.py — 14 passed.")

        h.spawn_spec_writer(77, "some feature")

        assert len(calls["tasks"]) == 1
        assert (tmp_path / "progress" / "spec_77.md.stale").exists()
        # Evidence is the PREMISE_CHECK section from the .md, not a generic line.
        assert "14 passed" in calls["tasks"][0]

    def test_md_fallback_for_reports_without_structured_field(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)
        # Pre-schema report: no premise_check in the .json, only the .md marker.
        (tmp_path / "progress" / "impl_77.json").write_text(json.dumps({
            "schema_version": 1, "status": "done", "tests_passed": True, "files_touched": [],
        }))
        (tmp_path / "progress" / "impl_77.md").write_text(
            "# Impl report\n\nPREMISE_CHECK: FAILED\nDirect curl to the endpoint "
            "returned the persisted value.")

        h.spawn_spec_writer(77, "some feature")

        assert len(calls["tasks"]) == 1
        assert "Direct curl to the endpoint" in calls["tasks"][0]

    def test_other_diagnosis_cause_reuses_cache(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "diagnosis_77.json").write_text(json.dumps({
            "cause": "flaky_e2e", "explanation": "timeout on wait_for_url",
        }))

        result = h.spawn_spec_writer(77, "some feature")

        assert result == "progress/spec_77.md"
        assert calls["tasks"] == []  # cache hit — agent never invoked
        assert "does not persist is_active" in (tmp_path / "progress" / "spec_77.md").read_text()

    def test_absent_files_reuse_cache(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)

        result = h.spawn_spec_writer(77, "some feature")

        assert result == "progress/spec_77.md"
        assert calls["tasks"] == []
        assert not (tmp_path / "progress" / "spec_77.md.stale").exists()

    def test_corrupt_diagnosis_reuses_cache(self, monkeypatch, tmp_path):
        h, calls = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "diagnosis_77.json").write_text("{not valid json[[")

        h.spawn_spec_writer(77, "some feature")

        assert calls["tasks"] == []  # best-effort: corrupt file = no-op, never a block

    def test_impl_cache_never_reuses_a_premise_check_exit(self, monkeypatch, tmp_path):
        # A PREMISE CHECK EXIT report can carry tests_passed=true — the passing
        # tests ARE the refutation evidence — but it is not a completed
        # implementation and must never be reused as one.
        h, _ = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "impl_1.md").write_text("PREMISE_CHECK: FAILED\n14 passed")
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps({
            "schema_version": 1, "status": "done", "tests_passed": True,
            "files_touched": [], "premise_check": "failed",
        }))
        calls = []

        def fake_run_agent(*a, **kw):
            calls.append(1)
            return "progress/impl_1.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        h.spawn_implementer(1, "desc")

        assert calls == [1]  # cache miss — agent was invoked despite tests_passed=true

    def test_read_structured_status_accepts_premise_check_field(self, monkeypatch, tmp_path):
        h, _ = self._setup(monkeypatch, tmp_path)
        (tmp_path / "progress" / "impl_1.md").write_text("report")
        (tmp_path / "progress" / "impl_1.json").write_text(json.dumps({
            "schema_version": 1, "status": "done", "tests_passed": True,
            "files_touched": [], "premise_check": "failed",
        }))
        status = h._read_structured_status("progress/impl_1.md")
        assert status is not None  # extra="forbid" schema knows the new field
        assert status.get("premise_check") == "failed"


class TestTruncateHeadTail:
    """
    Regression coverage: _validate_spec used to send only spec_content[:3000]
    to the review call, cutting the tests/notes section out of any
    non-trivial spec entirely — spec_74.md's wrong E2E test directory lived
    exactly there, in the section the flat head-only truncation discarded.
    """

    def test_short_text_is_returned_unchanged(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        text = "short spec content"
        assert h._truncate_head_tail(text) == text

    def test_text_at_exact_combined_limit_is_unchanged(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        text = "x" * 12000
        assert h._truncate_head_tail(text, head_chars=6000, tail_chars=6000) == text

    def test_long_text_keeps_head_and_tail_with_marker(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        text = "HEAD_MARKER" + ("m" * 20000) + "TAIL_MARKER"

        result = h._truncate_head_tail(text, head_chars=6000, tail_chars=6000)

        assert result.startswith("HEAD_MARKER")
        assert result.endswith("TAIL_MARKER")
        assert "[...middle truncated...]" in result
        assert len(result) < len(text)

    def test_custom_bounds_are_respected(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        text = "a" * 100 + "b" * 100
        result = h._truncate_head_tail(text, head_chars=10, tail_chars=10)
        assert result.startswith("a" * 10)
        assert result.endswith("b" * 10)
        assert "aaaaaaaaaaa" not in result.split("[...middle truncated...]")[0]


class TestValidateSpecStackAware:
    def test_sends_head_and_tail_of_long_spec(self, monkeypatch, tmp_path):
        # Regression: the old spec_content[:3000] cutoff would have dropped
        # the "## Tests" section of any spec longer than 3000 chars entirely.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        spec_path = tmp_path / "progress" / "spec_1.md"
        long_body = "x" * 20000
        spec_path.write_text(f"## Files to touch\nHEADER_SECTION\n{long_body}\n## Tests\nTAIL_SECTION")

        captured = {}

        def fake_call(model, messages, tools, role):
            captured["messages"] = messages
            return None
        monkeypatch.setattr(h, "_call_api_with_fallback", fake_call)

        h._validate_spec(str(spec_path))

        user_msg = captured["messages"][1]["content"]
        assert "HEADER_SECTION" in user_msg
        assert "TAIL_SECTION" in user_msg
        assert "[...middle truncated...]" in user_msg

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


class TestE2eMaxIterReportSynthesis:
    """
    Regression coverage for feature #74: the e2e_tester hit max_iter twice
    with no report written at all — not even a partial .md (unlike feature
    #71's v1.35.0 case, where a .md existed but the .json didn't). The real
    cause (a TimeoutError on #prof-name after a successful login+submit) was
    sitting in the run_playwright_tests tool results and was discarded,
    leaving the retry and the diagnostician with only the generic
    "[ERROR: max_iter ... reached]" message. run_agent now captures the last
    run_playwright_tests evidence and, if e2e_tester hits max_iter with no
    progress/e2e_<id>.json on disk, synthesizes one (plus a minimal .md)
    from it.
    """

    def _fake_tool_call(self, call_id, name, args_json):
        from types import SimpleNamespace
        fn = SimpleNamespace(name=name, arguments=args_json)
        return SimpleNamespace(id=call_id, function=fn)

    def _fake_response(self, tool_calls):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=None, tool_calls=tool_calls)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=usage, model="deepseek-v4-pro")

    def _pw_call_response(self, i):
        return self._fake_response([
            self._fake_tool_call(f"c{i}", "run_playwright_tests", '{"test_path": "tests/e2e/"}')
        ])

    def test_synthesizes_report_from_captured_playwright_output_on_max_iter(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._pw_call_response(i) for i in range(4)]
        ))
        monkeypatch.setattr(h, "execute_tool", MagicMock(side_effect=[
            json.dumps({"output": "old evidence, should be overwritten", "returncode": 1, "success": False}),
            json.dumps({"output": "second call, also overwritten"}),
            json.dumps({"output": "third call, also overwritten"}),
            json.dumps({
                "output": "FAILED tests/e2e/test_feature_74.py::test_create_professional - "
                          "TimeoutError: waiting for locator(\"#prof-name\") after login+submit",
                "returncode": 1, "success": False,
            }),
        ]))

        result = h.run_agent("sys", [], "task", role="e2e_tester", max_iter=4, feature_id=74)

        assert result.startswith("[ERROR: max_iter 4 reached]")
        json_path = tmp_path / "progress" / "e2e_74.json"
        md_path = tmp_path / "progress" / "e2e_74.md"
        assert json_path.exists()
        assert md_path.exists()

        payload = json.loads(json_path.read_text())
        assert payload["status"] == "failed"
        assert payload["tests_passed"] is False
        assert payload["files_touched"] == []
        assert "TimeoutError" in payload["reason"]
        assert "#prof-name" in payload["reason"]
        assert "old evidence" not in payload["reason"]  # only the LAST captured call's output

        assert "synthesized by harness after max_iter" in md_path.read_text()

    def test_does_not_overwrite_an_existing_json(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        json_path = tmp_path / "progress" / "e2e_74.json"
        json_path.write_text(json.dumps({
            "schema_version": 1, "status": "failed", "tests_passed": False,
            "files_touched": [], "reason": "agent's own real reason",
        }))

        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._pw_call_response(i) for i in range(2)]
        ))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"output": "some evidence"})
        ))

        h.run_agent("sys", [], "task", role="e2e_tester", max_iter=2, feature_id=74)

        assert json.loads(json_path.read_text())["reason"] == "agent's own real reason"

    def test_placeholder_when_no_playwright_evidence_captured(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._fake_response([self._fake_tool_call("c1", "read_file", '{"path": "x"}')])] * 2
        ))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"content": "irrelevant file contents"})
        ))

        h.run_agent("sys", [], "task", role="e2e_tester", max_iter=2, feature_id=74)

        payload = json.loads((tmp_path / "progress" / "e2e_74.json").read_text())
        assert "no run_playwright_tests output was captured" in payload["reason"]

    def test_no_synthesis_without_feature_id(self, monkeypatch, tmp_path):
        # Other run_agent callers (spec_writer/implementer/reviewer) don't
        # pass feature_id — must be a pure no-op for them, not a crash.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._pw_call_response(i) for i in range(2)]
        ))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"output": "some evidence"})
        ))

        h.run_agent("sys", [], "task", role="e2e_tester", max_iter=2)

        assert list((tmp_path / "progress").iterdir()) == []

    def test_no_synthesis_for_non_e2e_tester_role(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._pw_call_response(i) for i in range(2)]
        ))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"output": "some evidence"})
        ))

        h.run_agent("sys", [], "task", role="implementer", max_iter=2, feature_id=74)

        assert not (tmp_path / "progress" / "e2e_74.json").exists()


class TestImplMaxIterInvestigationDigest:
    """
    Regression coverage for feature #77, round 2: tool_call_errors already
    re-feeds the last 5 tool ERRORS to a retry (and worked in round 1), but
    round 2's attempt 1 had zero tool errors — 107 clean read_file + 68 clean
    run_bash, including the key finding that pytest tests/test_branches.py
    passed in full — and attempt 2 started blind, repeating essentially the
    same investigation (51 reads before writing anything). run_agent now
    synthesizes an investigation digest (files read, command outcomes, last
    hypothesis) when an implementer attempt hits max_iter, and
    spawn_implementer injects it into the next attempt's task.
    """

    def _fake_tool_call(self, call_id, name, args_json):
        from types import SimpleNamespace
        fn = SimpleNamespace(name=name, arguments=args_json)
        return SimpleNamespace(id=call_id, function=fn)

    def _fake_response(self, tool_calls, content=None):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=usage, model="deepseek-v4-pro")

    def test_digest_written_on_max_iter_dedup_outcomes_and_hypothesis(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)

        responses = [
            self._fake_response([self._fake_tool_call("c1", "read_file", '{"path": "src/api/branches.py"}')]),
            self._fake_response([
                self._fake_tool_call("c2", "read_file", '{"path": "src/api/branches.py"}'),  # duplicate
                self._fake_tool_call("c3", "read_file", '{"path": "frontend/pages/sedes.tsx"}'),
            ]),
            self._fake_response([self._fake_tool_call("c4", "run_bash", '{"command": "pytest tests/test_branches.py"}')]),
            self._fake_response(
                [self._fake_tool_call("c5", "read_file", '{"path": "src/models/branch.py"}')],
                content="Backend persistence checks out; suspecting the frontend renders by array position.",
            ),
        ]
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=responses))
        monkeypatch.setattr(h, "execute_tool", MagicMock(side_effect=[
            json.dumps({"content": "...", "path": "src/api/branches.py"}),
            json.dumps({"content": "...", "path": "src/api/branches.py"}),
            json.dumps({"content": "...", "path": "frontend/pages/sedes.tsx"}),
            json.dumps({"stdout": "collected 29 items\n...\n29 passed in 1.2s", "success": True}),
            json.dumps({"content": "...", "path": "src/models/branch.py"}),
        ]))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=4, feature_id=77)

        assert result.startswith("[ERROR: max_iter 4 reached]")
        digest_path = tmp_path / "progress" / "_investigation_impl_77.md"
        assert digest_path.exists()
        digest = digest_path.read_text()
        # (a) deduplicated file list — the twice-read path appears once
        assert digest.count("- src/api/branches.py") == 1
        assert "- frontend/pages/sedes.tsx" in digest
        # (b) verification command with its one-line outcome
        assert "pytest tests/test_branches.py → 29 passed in 1.2s" in digest
        # (c) the last assistant reasoning before the cutoff
        assert "suspecting the frontend renders by array position" in digest

    def test_no_digest_when_verdict_reached(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        responses = [
            self._fake_response([self._fake_tool_call("c1", "read_file", '{"path": "src/a.py"}')]),
            self._fake_response(None, content="progress/impl_77.md"),  # clean verdict
        ]
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=responses))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"content": "..."})))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=10, feature_id=77)

        assert result == "progress/impl_77.md"
        assert not (tmp_path / "progress" / "_investigation_impl_77.md").exists()

    def test_no_digest_for_other_roles(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        responses = [
            self._fake_response([self._fake_tool_call(f"c{i}", "read_file", '{"path": "src/a.py"}')])
            for i in range(3)
        ]
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=responses))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"content": "..."})))

        h.run_agent("sys", [], "task", role="reviewer", max_iter=3, feature_id=77)

        assert not (tmp_path / "progress" / "_investigation_impl_77.md").exists()

    def test_bash_outcome_line_variants(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        assert h._bash_outcome_line(json.dumps(
            {"stdout": "collected 29 items\n\n29 passed in 1.2s\n", "success": True}
        )) == "29 passed in 1.2s"
        assert h._bash_outcome_line(json.dumps({"error": "command timed out"})).startswith("error: command timed out")
        assert h._bash_outcome_line(json.dumps({"stdout": ""})) == "(no output)"

    def test_spawn_implementer_injects_digest_into_task(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        h.impl_cfg.TOOLS = []
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "_investigation_impl_9.md").write_text(
            "## Commands already run → last output line\n"
            "- pytest tests/test_branches.py → 29 passed in 1.2s")
        captured = {}

        def fake_run_agent(system_prompt, tools, task, **kw):
            captured["task"] = task
            return "progress/impl_9.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        h.spawn_implementer(9, "Fix: is_active no persiste")

        assert "PREVIOUS ATTEMPT'S INVESTIGATION — do not re-derive this" in captured["task"]
        assert "pytest tests/test_branches.py → 29 passed in 1.2s" in captured["task"]

    def test_spawn_implementer_without_digest_has_no_header(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        h.impl_cfg.TOOLS = []
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = {}

        def fake_run_agent(system_prompt, tools, task, **kw):
            captured["task"] = task
            return "progress/impl_9.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)

        h.spawn_implementer(9, "Fix: is_active no persiste")

        assert "PREVIOUS ATTEMPT'S INVESTIGATION" not in captured["task"]


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


class TestConvergenceEscalationAndZeroWriteCut:
    """
    Regression coverage for feature #77, round 2, attempt 1: the convergence
    watchdog existed and FIRED — CONVERGENCE_STREAK_LIMIT=7 plus the 60%/85%
    budget checkpoints injected ~11 nudges — and the agent kept reading until
    iteration 80 without writing anything. The watchdog wasn't missing; it had
    no teeth. Two additions: from the second streak firing on, the nudge
    escalates to an imperative ("your NEXT tool call MUST be write_file"), and
    MAX_ITER_WITHOUT_WRITE (default 40, 0 disables) aborts an attempt early
    if it reaches that many total iterations with zero writes — distinct
    message from a normal max_iter cutoff, and the investigation digest
    (v1.53.0) still gets written, so the retry starts informed with budget
    left.
    """

    def _fake_tool_call(self, call_id, name, args_json):
        from types import SimpleNamespace
        fn = SimpleNamespace(name=name, arguments=args_json)
        return SimpleNamespace(id=call_id, function=fn)

    def _fake_response(self, tool_calls, content=None):
        from types import SimpleNamespace
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5)
        choice = SimpleNamespace(message=msg)
        return SimpleNamespace(choices=[choice], usage=usage, model="deepseek-v4-pro")

    def _read_response(self, i):
        return self._fake_response([self._fake_tool_call(f"c{i}", "read_file", '{"path": "src/a.py"}')])

    def _write_response(self, i):
        return self._fake_response([self._fake_tool_call(f"c{i}", "write_file", '{"path": "src/a.py", "content": "x"}')])

    def _recording_call(self, responses):
        captured = []

        def fake_call(**kwargs):
            captured.append(list(kwargs["messages"]))
            return responses[len(captured) - 1]
        return fake_call, captured

    def _user_texts(self, snapshot):
        return [m.get("content", "") for m in snapshot if isinstance(m, dict) and m.get("role") == "user"]

    def test_second_streak_firing_escalates_to_imperative(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path,
                          {"CONVERGENCE_STREAK_LIMIT": "2", "MAX_ITER_WITHOUT_WRITE": "0"})
        responses = [self._read_response(i) for i in range(6)]
        fake_call, captured = self._recording_call(responses)
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=fake_call))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"content": "..."})))

        h.run_agent("sys", [], "task", role="implementer", max_iter=6)

        # Streak hits 2 before iteration 2 (first firing, soft text) and 4
        # before iteration 4 (second firing — escalated).
        first = "\n".join(self._user_texts(captured[2]))
        assert "CONVERGENCE CHECKPOINT" in first
        assert "ESCALATED" not in first
        second = "\n".join(self._user_texts(captured[4]))
        assert "ESCALATED" in second
        assert "MUST be write_file" in second
        assert "protocol violation" in second

    def test_escalated_nudge_announces_hard_cut_only_before_any_write(self, monkeypatch, tmp_path):
        # With a write already made, the zero-write abort can no longer fire —
        # the escalated nudge must not threaten it.
        h = _load_harness(monkeypatch, tmp_path,
                          {"CONVERGENCE_STREAK_LIMIT": "1", "MAX_ITER_WITHOUT_WRITE": "30"})
        responses = [self._write_response(0)] + [self._read_response(i) for i in range(1, 4)]
        fake_call, captured = self._recording_call(responses)
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=fake_call))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"status": "ok"})))

        h.run_agent("sys", [], "task", role="implementer", max_iter=4)

        # With limit=1, streak=2 before iteration 3 → escalated firing #2.
        escalated = "\n".join(t for t in self._user_texts(captured[3]) if "ESCALATED" in t)
        assert "MUST be write_file" in escalated
        assert "abort this attempt outright" not in escalated

    def test_zero_write_abort_fires_at_threshold(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"MAX_ITER_WITHOUT_WRITE": "3"})
        api = MagicMock(side_effect=[self._read_response(i) for i in range(10)])
        monkeypatch.setattr(h, "_call_api_with_fallback", api)
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"content": "..."})))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=10)

        assert result.startswith("[ERROR: attempt aborted: 3 iterations with zero writes")
        assert api.call_count == 3  # half the budget saved for the informed retry

    def test_a_single_write_prevents_the_abort(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"MAX_ITER_WITHOUT_WRITE": "3"})
        responses = [self._write_response(0)] + [self._read_response(i) for i in range(1, 5)]
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(side_effect=responses))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"status": "ok"})))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=5)

        assert result.startswith("[ERROR: max_iter 5 reached]")  # normal cutoff, not the abort

    def test_abort_still_writes_the_investigation_digest(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"MAX_ITER_WITHOUT_WRITE": "2"})
        (tmp_path / "progress").mkdir(exist_ok=True)
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._read_response(i) for i in range(2)]))
        monkeypatch.setattr(h, "execute_tool", MagicMock(
            return_value=json.dumps({"content": "...", "path": "src/a.py"})))

        h.run_agent("sys", [], "task", role="implementer", max_iter=10, feature_id=9)

        digest = (tmp_path / "progress" / "_investigation_impl_9.md").read_text()
        assert "aborted after 2 iterations with zero writes" in digest
        assert "- src/a.py" in digest

    def test_zero_disables_the_hard_cut(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path, {"MAX_ITER_WITHOUT_WRITE": "0"})
        monkeypatch.setattr(h, "_call_api_with_fallback", MagicMock(
            side_effect=[self._read_response(i) for i in range(4)]))
        monkeypatch.setattr(h, "execute_tool", MagicMock(return_value=json.dumps({"content": "..."})))

        result = h.run_agent("sys", [], "task", role="implementer", max_iter=4)

        assert result.startswith("[ERROR: max_iter 4 reached]")


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

    # Edit-tool aliases (feature 77 incident, 2026-07-14): an agent called the
    # bare tool name "edit" (not in _EDIT_TOOL_ALIASES at the time) and, on a
    # separate attempt, called "edit_file" with a "search"/"replace" argument
    # pair instead of old_string/new_string — neither translated, so both
    # attempts errored with a generic "Tool not found" and burned max_iter
    # without ever writing the fix.

    def test_execute_tool_bare_edit_tool_name_translates(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.ts").write_text("export function old() {}\n")
        result = json.loads(t.execute_tool("edit", {
            "path": "src/api.ts", "old_string": "old", "new_string": "new",
        }))
        assert "error" not in result
        assert (tmp_path / "src" / "api.ts").read_text() == "export function new() {}\n"

    def test_execute_tool_edit_file_search_replace_args_translate(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.ts").write_text("export function old() {}\n")
        result = json.loads(t.execute_tool("edit_file", {
            "path": "src/api.ts", "search": "old", "replace": "new",
        }))
        assert "error" not in result
        assert (tmp_path / "src" / "api.ts").read_text() == "export function new() {}\n"

    def test_execute_tool_edit_file_search_replacement_args_translate(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "api.ts").write_text("export function old() {}\n")
        result = json.loads(t.execute_tool("edit_file", {
            "path": "src/api.ts", "search": "old", "replacement": "new",
        }))
        assert "error" not in result
        assert (tmp_path / "src" / "api.ts").read_text() == "export function new() {}\n"

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


# ── tools.py: destructive-rewrite warning on write_file ──────────────────────

class TestDestructiveRewriteWarning:
    """
    Regression coverage for the feature #77 (attempt 2, round 2) incident: a
    single write_file regenerated a ~750-line router from memory — invented
    imports, an entire POST endpoint deleted, a security model_validator
    deleted, await session.commit() deleted — and nothing in the pipeline
    inspected what a full-file rewrite REMOVES (the debug-statements gate only
    looks at added lines). write_file now compares new content against the
    existing file and returns a non-blocking "warning" field naming exactly
    what was dropped, so the agent can self-correct on its next turn.
    """

    def _load_tools(self, monkeypatch, tmp_path: Path, extra_env: dict = None):
        monkeypatch.chdir(tmp_path)
        for k, v in (extra_env or {}).items():
            monkeypatch.setenv(k, v)
        for mod in ["sandbox"]:
            monkeypatch.setitem(sys.modules, mod, MagicMock())
        for key in list(sys.modules.keys()):
            if key == "tools":
                del sys.modules[key]
        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module("tools")

    _OLD_ROUTER = (
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n\n"
        "class BranchModel:\n"
        "    pass\n\n"
        "def _validate_is_active_consistency(v):\n"
        "    return v\n\n"
        "@router.post('/tenant/branches')\n"
        "async def create_branch(payload):\n"
        "    return payload\n\n"
        "@router.get('/tenant/branches')\n"
        "async def list_branches():\n"
        "    return []\n"
    )

    def test_removed_python_symbols_are_named_exactly(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "branches.py").write_text(self._OLD_ROUTER)
        # Rewrite keeps the GET endpoint but drops the POST endpoint, its
        # handler, and the validator — the #77 shape.
        new = (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n\n"
            "class BranchModel:\n"
            "    pass\n\n"
            "@router.get('/tenant/branches')\n"
            "async def list_branches():\n"
            "    return []\n"
        )
        result = json.loads(t.write_file(path="src/branches.py", content=new))

        assert result["status"] == "ok"  # never blocks
        assert "def create_branch" in result["warning"]
        assert "def _validate_is_active_consistency" in result["warning"]
        assert "@router.post('/tenant/branches')" in result["warning"]
        assert "list_branches" not in result["warning"]  # kept symbols not reported
        # The write itself still happened — warning, not rejection.
        assert (tmp_path / "src" / "branches.py").read_text() == new

    def test_shrink_beyond_threshold_is_flagged(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big.py").write_text("# filler\n" * 200)
        result = json.loads(t.write_file(path="src/big.py", content="# filler\n" * 50))
        assert result["status"] == "ok"
        assert "shrinks the file by" in result["warning"]

    def test_shrink_threshold_is_env_configurable(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path, {"DESTRUCTIVE_SHRINK_RATIO": "0.9"})
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "big.py").write_text("# filler\n" * 200)
        # 75% shrink, but the configured threshold is 90% — no warning.
        result = json.loads(t.write_file(path="src/big.py", content="# filler\n" * 50))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_pure_addition_produces_no_warning(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "branches.py").write_text(self._OLD_ROUTER)
        new = self._OLD_ROUTER + "\n@router.delete('/tenant/branches')\nasync def delete_branch():\n    return None\n"
        result = json.loads(t.write_file(path="src/branches.py", content=new))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_new_file_produces_no_warning(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.write_file(path="src/fresh.py", content="def brand_new():\n    pass\n"))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_non_source_extension_is_exempt(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir()
        (tmp_path / "progress" / "impl_1.md").write_text("long report " * 100)
        # Reports/specs get legitimately rewritten shorter all the time.
        result = json.loads(t.write_file(path="progress/impl_1.md", content="short"))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_js_removed_exports_are_named(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "frontend").mkdir()
        old = (
            "export const API_BASE = '/api/v1';\n\n"
            "export async function fetchBranches() {\n  return [];\n}\n\n"
            "export function toggleBranch(id) {\n  return id;\n}\n"
        )
        (tmp_path / "frontend" / "api.ts").write_text(old)
        new = "export async function fetchBranches() {\n  return [];\n}\n"
        result = json.loads(t.write_file(path="frontend/api.ts", content=new))
        assert result["status"] == "ok"
        assert "API_BASE" in result["warning"]
        assert "toggleBranch" in result["warning"]
        assert "fetchBranches" not in result["warning"]

    def test_edit_alias_full_content_path_inherits_the_warning(self, monkeypatch, tmp_path):
        # The edit_file alias translation writes through write_file(), so a
        # hallucinated "edit" that hands over full new content with symbols
        # missing gets the same warning as a direct write_file.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "branches.py").write_text(self._OLD_ROUTER)
        new = "from fastapi import APIRouter\nrouter = APIRouter()\n"
        result = json.loads(t.execute_tool("edit_file", {"path": "src/branches.py", "content": new}))
        assert result.get("status") == "ok"
        assert "def create_branch" in result.get("warning", "")

    def test_shrink_floor_exempts_small_files(self, monkeypatch, tmp_path):
        # A 10-line file dropping to 6 is a 40% "shrink" that's usually a
        # legitimate refactor — noise trains agents to ignore warnings, the
        # exact pattern this mechanism exists to fight. (No symbols involved,
        # so the symbol check — which has no floor — stays silent too.)
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "small.py").write_text("# note\n" * 10)
        result = json.loads(t.write_file(path="src/small.py", content="# note\n" * 6))
        assert result["status"] == "ok"
        assert "warning" not in result

    def test_shrink_floor_is_env_configurable(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path, {"DESTRUCTIVE_SHRINK_MIN_LINES": "5"})
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "small.py").write_text("# note\n" * 10)
        result = json.loads(t.write_file(path="src/small.py", content="# note\n" * 6))
        assert "shrinks the file by" in result["warning"]

    def test_symbol_check_has_no_size_floor(self, monkeypatch, tmp_path):
        # _OLD_ROUTER is ~16 lines — well under the 40-line shrink floor — but
        # a dropped symbol is meaningful at any file size, so it still warns.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "branches.py").write_text(self._OLD_ROUTER)
        new = self._OLD_ROUTER.replace(
            "def _validate_is_active_consistency(v):\n    return v\n\n", ""
        )
        result = json.loads(t.write_file(path="src/branches.py", content=new))
        assert "def _validate_is_active_consistency" in result["warning"]
        assert "shrinks the file by" not in result["warning"]  # floor keeps the size check quiet

    def test_shrink_only_language_go_flags_shrink_without_symbol_diff(self, monkeypatch, tmp_path):
        # .go/.rb/.php/.java/.cs get the language-agnostic shrink check, but no
        # symbol diffing — the Python/JS regexes would produce garbage matches
        # on them, and a wrong "removed symbol" warning is worse than none.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        old = "func handler() {\n\t// filler\n}\n" * 60  # 180 lines
        (tmp_path / "src" / "main.go").write_text(old)
        result = json.loads(t.write_file(path="src/main.go", content=old[: len(old) // 3]))
        assert "shrinks the file by" in result["warning"]
        assert "REMOVED top-level symbol" not in result["warning"]

    def test_warning_conditions_the_git_recovery_path(self, monkeypatch, tmp_path):
        # git show HEAD:<path> can't recover a file first created during the
        # same (uncommitted) feature — the warning must offer git only
        # conditionally, with "content you read earlier this run" as the
        # unconditional primary path.
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "branches.py").write_text(self._OLD_ROUTER)
        new = "from fastapi import APIRouter\nrouter = APIRouter()\n"
        result = json.loads(t.write_file(path="src/branches.py", content=new))
        warning = result["warning"]
        assert "content you read earlier this run" in warning
        assert "If the file already existed in the last commit" in warning
        assert "not committed yet" in warning


# ── tools.py: run_repro_script (host-side repro runner) ──────────────────────

class TestRunReproScript:
    """
    Regression coverage for feature #77's structural gap: the symptom was
    browser-only, run_bash's sandbox has no route to the host network and no
    browser, and run_playwright_tests/take_screenshot are e2e_tester-only —
    the implementer could not observe the bug it had to fix. run_repro_script
    executes progress/repro_<id>.py/.sh on the HOST (same mechanism as
    run_playwright_tests: sys.executable, outside the sandbox), with the path
    derived from feature_id alone — no general host execution granted.
    """

    def _load_tools(self, monkeypatch, tmp_path: Path):
        monkeypatch.chdir(tmp_path)
        for mod in ["sandbox"]:
            monkeypatch.setitem(sys.modules, mod, MagicMock())
        for key in list(sys.modules.keys()):
            if key == "tools":
                del sys.modules[key]
        root = Path(__file__).parent.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return importlib.import_module("tools")

    def test_failing_repro_is_the_expected_baseline(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir()
        (tmp_path / "progress" / "repro_77.py").write_text(
            "import sys\nprint('switch reverted to true after save')\nsys.exit(1)\n")

        result = json.loads(t.run_repro_script(feature_id=77))

        assert result["passed"] is False
        assert result["returncode"] == 1
        assert "switch reverted to true after save" in result["output"]
        assert result["script"] == "progress/repro_77.py"
        assert "PREMISE CHECK EXIT" in result["tip"]

    def test_passing_repro_confirms_the_fix(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir()
        (tmp_path / "progress" / "repro_77.py").write_text("print('is_active persisted')\n")

        result = json.loads(t.run_repro_script(feature_id=77))

        assert result["passed"] is True
        assert "is_active persisted" in result["output"]

    def test_sh_repro_supported(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir()
        (tmp_path / "progress" / "repro_5.sh").write_text("echo from-bash; exit 1\n")

        result = json.loads(t.run_repro_script(feature_id=5))

        assert result["passed"] is False
        assert "from-bash" in result["output"]
        assert result["script"] == "progress/repro_5.sh"

    def test_missing_script_errors_with_hint(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir()
        result = json.loads(t.run_repro_script(feature_id=42))
        assert "error" in result
        assert "NOT_FEASIBLE" in result["hint"]

    def test_feature_id_must_be_an_integer(self, monkeypatch, tmp_path):
        # The executed path is derived from feature_id — int() validation is
        # what keeps this from becoming general host execution.
        t = self._load_tools(monkeypatch, tmp_path)
        result = json.loads(t.run_repro_script(feature_id="5; rm -rf /"))
        assert "error" in result
        result = json.loads(t.run_repro_script())
        assert "error" in result

    def test_registered_in_schema_and_dispatch(self, monkeypatch, tmp_path):
        t = self._load_tools(monkeypatch, tmp_path)
        schemas = t.get_schemas("run_repro_script")
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "run_repro_script"
        (tmp_path / "progress").mkdir()
        (tmp_path / "progress" / "repro_3.py").write_text("print('via dispatch')\n")
        result = json.loads(t.execute_tool("run_repro_script", {"feature_id": 3}))
        assert result["passed"] is True


# ── harness.py: scoped exposure of run_repro_script to the implementer ───────

class TestReproToolExposure:
    def _spawn(self, h, monkeypatch, feature_id, description, **spawn_kw):
        h.impl_cfg.TOOLS = []  # real list — the exposure path extends it
        captured = {}

        def fake_run_agent(system_prompt, tools, task, **kw):
            captured["tools"] = tools
            captured["task"] = task
            return f"progress/impl_{feature_id}.md"
        monkeypatch.setattr(h, "run_agent", fake_run_agent)
        h.spawn_implementer(feature_id, description, **spawn_kw)
        return captured

    def _tool_names(self, tools):
        return [s["function"]["name"] for s in tools]

    def test_exposed_for_e2e_bugfix_features(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._spawn(h, monkeypatch, 9, "Fix: is_active no persiste al togglear la sede")
        assert "run_repro_script" in self._tool_names(captured["tools"])

    def test_not_exposed_when_e2e_false(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._spawn(h, monkeypatch, 9, "Fix: is_active no persiste", e2e=False)
        assert "run_repro_script" not in self._tool_names(captured["tools"])

    def test_not_exposed_for_regular_features(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        captured = self._spawn(h, monkeypatch, 9, "Add CSV export to the reports page")
        assert "run_repro_script" not in self._tool_names(captured["tools"])

    def test_repro_context_names_the_tool_when_exposed(self, monkeypatch, tmp_path):
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        captured = self._spawn(h, monkeypatch, 9, "Fix: is_active no persiste al togglear la sede")
        assert "run_repro_script(feature_id=9)" in captured["task"]

    def test_repro_context_omits_the_tool_when_not_exposed(self, monkeypatch, tmp_path):
        # Script on disk but e2e=False (backend-only bug): the protocol block
        # still appears, but must not point at a tool that isn't in the set.
        h = _load_harness(monkeypatch, tmp_path)
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "repro_9.py").write_text("assert False")
        captured = self._spawn(h, monkeypatch, 9, "Fix: is_active no persiste", e2e=False)
        assert "Reproduction script (mandatory protocol)" in captured["task"]
        assert "run_repro_script" not in captured["task"]


# ── agents/shared_rules.py: MINIMAL_DELTA_RULE shared across writer roles ────

class TestMinimalDeltaRuleShared:
    def test_rule_interpolated_into_implementer_and_e2e_tester(self):
        # The two roles that write source-extension files. e2e_tester matters
        # specifically because a test deleted during a from-memory rewrite
        # never fails — coverage shrinks silently, and the write_file warning
        # naming the removed def test_* symbols is the only signal (real
        # evidence: biovet-harness features 34/35/36/39/46/51, where the
        # E2E_TESTER wrote through the edit aliases → write_file).
        import agents.shared_rules as sr
        import agents.implementer as impl
        import agents.e2e_tester as e2e
        assert "MINIMAL-DELTA REWRITES" in sr.MINIMAL_DELTA_RULE
        assert sr.MINIMAL_DELTA_RULE.strip() in impl.SYSTEM_PROMPT
        assert sr.MINIMAL_DELTA_RULE.strip() in e2e.SYSTEM_PROMPT


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
