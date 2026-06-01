"""Tests for the generic storage layer."""

import json
import os
from pathlib import Path

from src.storage import load, save


class TestLoad:
    """Tests for the load function."""

    def test_load_returns_empty_list_when_file_missing(self, tmp_path):
        """Should return an empty list when the JSON file does not exist."""
        result = load("users", data_dir=str(tmp_path))
        assert result == []

    def test_load_returns_records_when_file_exists(self, tmp_path):
        """Should return parsed records when the file exists."""
        data_dir = Path(tmp_path)
        data_dir.mkdir(exist_ok=True)
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        with open(data_dir / "users.json", "w", encoding="utf-8") as fh:
            json.dump(records, fh)
        result = load("users", data_dir=str(tmp_path))
        assert result == records


class TestSave:
    """Tests for the save function."""

    def test_save_writes_and_load_reads_back(self, tmp_path):
        """Should persist records that can be read back by load."""
        records = [{"id": 1, "name": "Charlie"}]
        save("users", records, data_dir=str(tmp_path))
        result = load("users", data_dir=str(tmp_path))
        assert result == records

    def test_save_creates_data_directory_if_missing(self, tmp_path):
        """Should create the data directory automatically."""
        data_dir = tmp_path / "new_data"
        assert not data_dir.exists()
        save("sessions", [], data_dir=str(data_dir))
        assert data_dir.exists()
        assert (data_dir / "sessions.json").exists()

    def test_save_is_atomic_no_partial_writes(self, tmp_path):
        """Should not leave .tmp files after a successful write."""
        save("users", [{"id": 1}], data_dir=str(tmp_path))
        tmp_files = list(Path(tmp_path).glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_save_overwrites_existing_file(self, tmp_path):
        """Should replace an existing file with new data."""
        save("users", [{"id": 1}], data_dir=str(tmp_path))
        save("users", [{"id": 2}], data_dir=str(tmp_path))
        result = load("users", data_dir=str(tmp_path))
        assert result == [{"id": 2}]

    def test_save_preserves_indent_and_encoding(self, tmp_path):
        """Should write JSON with indent=2 for human readability."""
        save("sessions", [{"id": 1, "title": "Yoga"}], data_dir=str(tmp_path))
        filepath = Path(tmp_path) / "sessions.json"
        raw = filepath.read_text(encoding="utf-8")
        assert "  " in raw  # indent=2
        assert "\n" in raw
