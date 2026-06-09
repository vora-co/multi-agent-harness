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
import sys
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
