"""Generic JSON file storage with atomic writes."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


def load(entity: str, data_dir: str = "data") -> List[Dict[str, Any]]:
    """Read all records from data/{entity}.json.

    Args:
        entity: Entity name (e.g. 'users', 'sessions', 'bookings').
        data_dir: Directory where JSON files reside. Defaults to 'data'.

    Returns:
        A list of dictionaries. Returns an empty list if the file
        does not exist.
    """
    filepath = Path(data_dir) / f"{entity}.json"
    if not filepath.exists():
        return []
    with open(filepath, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(entity: str, records: List[Dict[str, Any]], data_dir: str = "data") -> None:
    """Atomically write records to data/{entity}.json.

    Writes to a temporary file and then replaces the target file atomically
    via os.replace, which is atomic on POSIX systems.

    Args:
        entity: Entity name.
        records: List of dictionaries to persist.
        data_dir: Directory where JSON files reside. Defaults to 'data'.
    """
    dir_path = Path(data_dir)
    dir_path.mkdir(parents=True, exist_ok=True)

    target = dir_path / f"{entity}.json"
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".json", prefix=f"{entity}_", dir=dir_path
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, default=str)
        os.replace(tmp_path, target)
    except Exception:
        # Clean up the temp file on failure
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
