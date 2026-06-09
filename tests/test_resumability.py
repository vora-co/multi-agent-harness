"""
tests/test_resumability.py — Tests for durable state / resumability.

Covers: _save_checkpoint, _load_checkpoint, _clear_checkpoint,
recover_stale_features (checkpoint-preserving), and run_feature_cycle
skip logic for each resumable step.

No live API calls are made — all agent spawns are patched.

Run with:
    python3 -m pytest tests/test_resumability.py -v
"""

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

FEATURE_1 = {
    "id": 1, "title": "Feature one", "description": "desc",
    "status": "pending", "e2e": False, "depends_on": [],
    "created_at": "2026-01-01T00:00:00",
}


def _make_feature_list(tmp_path: Path, features=None, extra_fields=None) -> Path:
    fl = [dict(FEATURE_1)]
    if extra_fields:
        fl[0].update(extra_fields)
    if features is not None:
        fl = features
    p = tmp_path / "feature_list.json"
    p.write_text(json.dumps(fl))
    return p


def _load_harness(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "progress").mkdir(exist_ok=True)

    # Minimal env
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "deepseek")
    monkeypatch.setenv("LLM_MODEL_MAP", "{}")

    # Stub heavy imports
    for mod in ["openai", "dotenv", "rich", "rich.console", "rich.panel",
                "rich.table", "rich.markdown", "playwright", "playwright.sync_api",
                "agents.leader", "agents.implementer", "agents.reviewer",
                "agents.e2e_tester", "agents.spec_writer"]:
        monkeypatch.setitem(sys.modules, mod, MagicMock())

    # openai.OpenAI must be callable and return a mock client
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


def _read_fl(tmp_path: Path):
    return json.loads((tmp_path / "feature_list.json").read_text())


# ── _save_checkpoint ──────────────────────────────────────────────────────────

class TestSaveCheckpoint:
    def test_writes_checkpoint_field(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)

        h._save_checkpoint(1, "spec_done", attempt=1)

        feat = _read_fl(tmp_path)[0]
        assert feat["_checkpoint"]["step"] == "spec_done"
        assert feat["_checkpoint"]["attempt"] == 1

    def test_overwrites_previous_checkpoint(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)

        h._save_checkpoint(1, "spec_done", attempt=1)
        h._save_checkpoint(1, "impl_done", attempt=1)

        feat = _read_fl(tmp_path)[0]
        assert feat["_checkpoint"]["step"] == "impl_done"

    def test_timestamp_is_present(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        h._save_checkpoint(1, "e2e_done", attempt=2)

        feat = _read_fl(tmp_path)[0]
        assert "saved_at" in feat["_checkpoint"]


# ── _load_checkpoint ──────────────────────────────────────────────────────────

class TestLoadCheckpoint:
    def test_returns_none_when_absent(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        assert h._load_checkpoint(1) is None

    def test_returns_checkpoint_dict(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "_checkpoint": {"step": "impl_done", "attempt": 2, "saved_at": "x"}
        })
        h = _load_harness(monkeypatch, tmp_path)
        ckpt = h._load_checkpoint(1)
        assert ckpt["step"] == "impl_done"
        assert ckpt["attempt"] == 2

    def test_returns_none_for_unknown_feature(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        assert h._load_checkpoint(999) is None


# ── _clear_checkpoint ─────────────────────────────────────────────────────────

class TestClearCheckpoint:
    def test_removes_checkpoint_field(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "_checkpoint": {"step": "impl_done", "attempt": 1, "saved_at": "x"}
        })
        h = _load_harness(monkeypatch, tmp_path)
        h._clear_checkpoint(1)

        feat = _read_fl(tmp_path)[0]
        assert "_checkpoint" not in feat

    def test_noop_when_no_checkpoint(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        h._clear_checkpoint(1)  # must not raise

        feat = _read_fl(tmp_path)[0]
        assert "_checkpoint" not in feat


# ── recover_stale_features ────────────────────────────────────────────────────

class TestRecoverStaleFeatures:
    def test_resets_in_progress_to_pending(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={"status": "in_progress"})
        h = _load_harness(monkeypatch, tmp_path)

        recovered = h.recover_stale_features()

        assert recovered == [1]
        assert _read_fl(tmp_path)[0]["status"] == "pending"

    def test_preserves_checkpoint_on_recovery(self, monkeypatch, tmp_path):
        ckpt = {"step": "impl_done", "attempt": 1, "saved_at": "x"}
        _make_feature_list(tmp_path, extra_fields={
            "status": "in_progress",
            "_checkpoint": ckpt,
        })
        h = _load_harness(monkeypatch, tmp_path)
        h.recover_stale_features()

        feat = _read_fl(tmp_path)[0]
        assert feat["_checkpoint"]["step"] == "impl_done"

    def test_recovery_note_mentions_checkpoint(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "status": "in_progress",
            "_checkpoint": {"step": "spec_done", "attempt": 1, "saved_at": "x"},
        })
        h = _load_harness(monkeypatch, tmp_path)
        h.recover_stale_features()

        note = _read_fl(tmp_path)[0]["recovery_note"]
        assert "checkpoint" in note.lower()

    def test_no_checkpoint_gets_generic_note(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={"status": "in_progress"})
        h = _load_harness(monkeypatch, tmp_path)
        h.recover_stale_features()

        note = _read_fl(tmp_path)[0]["recovery_note"]
        assert "pending" in note.lower()

    def test_done_features_not_touched(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={"status": "done"})
        h = _load_harness(monkeypatch, tmp_path)
        recovered = h.recover_stale_features()
        assert recovered == []


# ── run_feature_cycle resume logic ────────────────────────────────────────────

class TestRunFeatureCycleResume:
    """
    Patch all agent spawns + hooks; verify which spawns are called or skipped
    depending on the checkpoint present when the cycle starts.
    """

    def _patch_cycle(self, h, monkeypatch, *,
                     spec_result="progress/spec_1.md",
                     impl_result="ok",
                     e2e_result="E2E_PASSED",
                     review_result="APPROVED"):
        monkeypatch.setattr(h, "spawn_spec_writer",
                            MagicMock(return_value=spec_result))
        monkeypatch.setattr(h, "spawn_implementer",
                            MagicMock(return_value=impl_result))
        monkeypatch.setattr(h, "spawn_e2e_tester",
                            MagicMock(return_value=e2e_result))
        monkeypatch.setattr(h, "spawn_reviewer",
                            MagicMock(return_value=review_result))
        monkeypatch.setattr(h, "_fire",       MagicMock())
        monkeypatch.setattr(h, "_fire_gate",  MagicMock(return_value=None))
        monkeypatch.setattr(h, "_track_usage", MagicMock())
        # Suppress console output
        h.console = MagicMock()

    def test_fresh_run_calls_all_steps(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        result = h.run_feature_cycle(1, "desc", e2e=False)

        assert result["approved"] is True
        h.spawn_spec_writer.assert_called_once()
        h.spawn_implementer.assert_called_once()
        h.spawn_reviewer.assert_called_once()

    def test_spec_done_checkpoint_skips_spec_writer(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "_checkpoint": {"step": "spec_done", "attempt": 1, "saved_at": "x"}
        })
        # Ensure spec file exists so path resolution works
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_1.md").write_text("spec content")

        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        h.run_feature_cycle(1, "desc", e2e=False)

        h.spawn_spec_writer.assert_not_called()
        h.spawn_implementer.assert_called_once()

    def test_impl_done_checkpoint_skips_spec_and_impl(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "_checkpoint": {"step": "impl_done", "attempt": 1, "saved_at": "x"}
        })
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_1.md").write_text("spec")

        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        h.run_feature_cycle(1, "desc", e2e=False)

        h.spawn_spec_writer.assert_not_called()
        h.spawn_implementer.assert_not_called()
        h.spawn_reviewer.assert_called_once()

    def test_e2e_done_checkpoint_skips_to_reviewer(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path, extra_fields={
            "_checkpoint": {"step": "e2e_done", "attempt": 1, "saved_at": "x"}
        })
        (tmp_path / "progress").mkdir(exist_ok=True)
        (tmp_path / "progress" / "spec_1.md").write_text("spec")

        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        h.run_feature_cycle(1, "desc", e2e=True)

        h.spawn_spec_writer.assert_not_called()
        h.spawn_implementer.assert_not_called()
        h.spawn_e2e_tester.assert_not_called()
        h.spawn_reviewer.assert_called_once()

    def test_checkpoint_cleared_on_approval(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch)

        h.run_feature_cycle(1, "desc", e2e=False)

        feat = _read_fl(tmp_path)[0]
        assert "_checkpoint" not in feat

    def test_checkpoint_cleared_on_final_failure(self, monkeypatch, tmp_path):
        _make_feature_list(tmp_path)
        h = _load_harness(monkeypatch, tmp_path)
        self._patch_cycle(h, monkeypatch, review_result="REJECTED: bad code")

        h.run_feature_cycle(1, "desc", e2e=False)

        feat = _read_fl(tmp_path)[0]
        assert "_checkpoint" not in feat
