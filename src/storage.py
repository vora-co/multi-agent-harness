import json
import os
import tempfile

DATA_DIR = "data"
NOTES_FILE = os.path.join(DATA_DIR, "notes.json")


def load():
    """Lee y devuelve la lista de notas desde data/notes.json."""
    if not os.path.exists(NOTES_FILE):
        return []
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save(notes: list):
    """Escribe la lista de notas en data/notes.json de forma atómica."""
    os.makedirs(DATA_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(notes, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, NOTES_FILE)
    except Exception:
        os.unlink(tmp_path)
        raise
