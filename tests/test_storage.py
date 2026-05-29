import json
import os
import tempfile
import pytest

from src.storage import load, save, NOTES_FILE, DATA_DIR


@pytest.fixture(autouse=True)
def cleanup_data():
    """Elimina data/ antes y después de cada test."""
    if os.path.exists(DATA_DIR):
        import shutil
        shutil.rmtree(DATA_DIR)
    yield
    if os.path.exists(DATA_DIR):
        import shutil
        shutil.rmtree(DATA_DIR)


def test_load_returns_empty_list_when_file_missing():
    assert load() == []


def test_load_returns_notes_when_file_exists():
    os.makedirs(DATA_DIR, exist_ok=True)
    expected = [{"id": 1, "title": "Test"}]
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump(expected, f)
    assert load() == expected


def test_save_creates_file():
    notes = [{"id": 1, "title": "Hello"}]
    save(notes)
    assert os.path.exists(NOTES_FILE)
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        assert json.load(f) == notes


def test_save_overwrites_existing_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump([{"old": True}], f)

    new_notes = [{"id": 2, "title": "Updated"}]
    save(new_notes)
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        assert json.load(f) == new_notes


def test_save_is_atomic_does_not_corrupt_on_failure():
    os.makedirs(DATA_DIR, exist_ok=True)
    original = [{"id": 1, "title": "Original"}]
    save(original)

    # Simular un fallo forzando un objeto no serializable
    # Guardamos el original primero
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        before = json.load(f)

    # Intentar guardar algo que falle
    try:
        save([{"id": 2, "bad": set()}])  # set() no es serializable
    except Exception:
        pass

    # El archivo original debe permanecer intacto
    with open(NOTES_FILE, "r", encoding="utf-8") as f:
        assert json.load(f) == before


def test_data_dir_created_if_not_exists():
    import shutil
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    assert not os.path.exists(DATA_DIR)

    save([{"id": 1}])
    assert os.path.isdir(DATA_DIR)
