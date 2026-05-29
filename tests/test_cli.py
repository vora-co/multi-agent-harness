"""Tests para el CLI (src/cli.py)."""

import os
import pytest

from src.cli import main, _next_id
from src.storage import DATA_DIR, NOTES_FILE, load


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


# ---------------------------------------------------------------------------
# Tests de _next_id
# ---------------------------------------------------------------------------

def test_next_id_empty_list():
    """Con lista vacía devuelve 1."""
    assert _next_id([]) == 1


def test_next_id_sequential():
    """Con datos existentes devuelve max(id) + 1."""
    assert _next_id([{"id": 1}, {"id": 3}, {"id": 2}]) == 4


def test_next_id_with_string_ids_ignored():
    """IDs no enteros se ignoran al calcular el máximo."""
    assert _next_id([{"id": "abc"}, {"id": 5}]) == 6


def test_next_id_all_string_ids():
    """Si todos los ids son strings, devuelve 1."""
    assert _next_id([{"id": "abc"}, {"id": "xyz"}]) == 1


# ---------------------------------------------------------------------------
# Tests del comando add
# ---------------------------------------------------------------------------

def test_add_creates_note_and_confirms(capsys):
    """'add' crea una nota, la guarda y muestra confirmación."""
    main(["add", "Mi Nota"])

    captured = capsys.readouterr()
    assert "Nota agregada: [1] Mi Nota" in captured.out

    notes = load()
    assert len(notes) == 1
    assert notes[0]["id"] == 1
    assert notes[0]["title"] == "Mi Nota"
    assert notes[0]["body"] == ""


def test_add_with_body(capsys):
    """'add' con --body guarda el cuerpo de la nota."""
    main(["add", "Con cuerpo", "--body", "Contenido aquí"])

    captured = capsys.readouterr()
    assert "Nota agregada: [1] Con cuerpo" in captured.out

    notes = load()
    assert notes[0]["body"] == "Contenido aquí"


def test_add_increments_id(capsys):
    """Varias invocaciones de 'add' generan ids autoincrementales."""
    main(["add", "Primera"])
    main(["add", "Segunda"])

    notes = load()
    ids = [n["id"] for n in notes]
    assert ids == [1, 2]


def test_add_persists_to_file_system(capsys):
    """Después de 'add', el archivo data/notes.json existe y tiene la nota."""
    main(["add", "Persistente"])

    assert os.path.exists(NOTES_FILE)
    notes = load()
    assert len(notes) == 1
    assert notes[0]["title"] == "Persistente"


# ---------------------------------------------------------------------------
# Tests del comando list
# ---------------------------------------------------------------------------

def test_list_empty_shows_message(capsys):
    """'list' sin notas muestra mensaje apropiado."""
    main(["list"])

    captured = capsys.readouterr()
    assert "No hay notas guardadas" in captured.out


def test_list_shows_single_note(capsys):
    """'list' con una nota la muestra en formato legible."""
    main(["add", "Nota única"])
    # Limpiar el stdout de add
    capsys.readouterr()

    main(["list"])
    captured = capsys.readouterr()
    assert "[1] Nota única" in captured.out
    assert "Creada:" in captured.out


def test_list_shows_multiple_notes(capsys):
    """'list' muestra todas las notas agregadas."""
    main(["add", "Primera", "--body", "Body 1"])
    main(["add", "Segunda"])
    capsys.readouterr()  # Limpiar

    main(["list"])
    captured = capsys.readouterr()
    assert "[1] Primera" in captured.out
    assert "Body 1" in captured.out
    assert "[2] Segunda" in captured.out


def test_list_shows_note_without_body(capsys):
    """Nota sin body se muestra sin la línea de cuerpo."""
    main(["add", "Sin cuerpo"])
    capsys.readouterr()

    main(["list"])
    captured = capsys.readouterr()
    # Verificamos que no hay una línea con indentación para body vacío
    lines = captured.out.splitlines()
    # Debe tener: [1] Sin cuerpo, Creada: ..., y línea vacía
    assert any("[1] Sin cuerpo" in line for line in lines)


# ---------------------------------------------------------------------------
# Tests de manejo de data/notes.json inexistente o vacío
# ---------------------------------------------------------------------------

def test_add_when_data_dir_missing(capsys):
    """'add' funciona aunque data/notes.json no exista."""
    assert not os.path.exists(DATA_DIR)
    main(["add", "Sin directorio previo"])

    captured = capsys.readouterr()
    assert "Nota agregada: [1] Sin directorio previo" in captured.out
    assert os.path.exists(NOTES_FILE)


def test_list_when_data_dir_missing(capsys):
    """'list' funciona aunque data/notes.json no exista."""
    assert not os.path.exists(DATA_DIR)
    main(["list"])

    captured = capsys.readouterr()
    assert "No hay notas guardadas" in captured.out


def test_list_with_empty_json_file(capsys):
    """'list' funciona si data/notes.json contiene una lista vacía."""
    os.makedirs(DATA_DIR, exist_ok=True)
    import json
    with open(NOTES_FILE, "w", encoding="utf-8") as f:
        json.dump([], f)

    main(["list"])
    captured = capsys.readouterr()
    assert "No hay notas guardadas" in captured.out
