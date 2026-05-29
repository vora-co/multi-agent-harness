"""Tests para el modelo Note."""

import json
from datetime import datetime, timezone

import pytest

from src.notes import Note


class TestNoteCreation:
    """Tests de creación de instancias Note."""

    def test_minimal_creation(self):
        """Se puede crear una nota solo con id y title."""
        note = Note(id=1, title="Mi nota")
        assert note.id == 1
        assert note.title == "Mi nota"
        assert note.body == ""
        assert isinstance(note.created_at, datetime)

    def test_full_creation(self):
        """Se puede crear una nota con todos los atributos."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        note = Note(id="abc", title="Título", body="Cuerpo", created_at=dt)
        assert note.id == "abc"
        assert note.title == "Título"
        assert note.body == "Cuerpo"
        assert note.created_at == dt

    def test_creation_with_iso_string(self):
        """created_at puede pasarse como string ISO."""
        note = Note(id=1, title="Test", created_at="2024-06-01T12:00:00+00:00")
        expected = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert note.created_at == expected

    def test_default_created_at_is_utc_aware(self):
        """Si no se pasa created_at, se genera datetime con timezone UTC."""
        note = Note(id=1, title="Test")
        assert note.created_at.tzinfo is not None
        # Debe ser cercano al momento actual (con margen de 5 segundos)
        now = datetime.now(timezone.utc)
        diff = abs((now - note.created_at).total_seconds())
        assert diff < 5


class TestNoteToDict:
    """Tests del método to_dict()."""

    def test_to_dict_returns_serializable_dict(self):
        """to_dict() devuelve un dict que puede serializarse a JSON."""
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        note = Note(id=1, title="Test", body="Cuerpo", created_at=dt)
        d = note.to_dict()
        assert d == {
            "id": 1,
            "title": "Test",
            "body": "Cuerpo",
            "created_at": "2024-01-15T10:30:00+00:00",
        }
        # Verificar que es serializable
        json.dumps(d)

    def test_to_dict_roundtrip_with_json(self):
        """to_dict() produce algo que json.dumps acepta sin error."""
        note = Note(id="a1", title="Hola", body="Mundo")
        json_str = json.dumps(note.to_dict())
        restored = json.loads(json_str)
        assert restored["id"] == "a1"
        assert restored["title"] == "Hola"
        assert restored["body"] == "Mundo"
        assert "created_at" in restored


class TestNoteFromDict:
    """Tests del método de clase from_dict()."""

    def test_from_dict_minimal(self):
        """from_dict() con campos mínimos."""
        data = {"id": 1, "title": "Nota"}
        note = Note.from_dict(data)
        assert note.id == 1
        assert note.title == "Nota"
        assert note.body == ""
        assert isinstance(note.created_at, datetime)

    def test_from_dict_full(self):
        """from_dict() con todos los campos."""
        data = {
            "id": 42,
            "title": "Completa",
            "body": "Cuerpo de la nota",
            "created_at": "2024-03-20T15:00:00+00:00",
        }
        note = Note.from_dict(data)
        assert note.id == 42
        assert note.title == "Completa"
        assert note.body == "Cuerpo de la nota"
        expected_dt = datetime(2024, 3, 20, 15, 0, 0, tzinfo=timezone.utc)
        assert note.created_at == expected_dt

    def test_from_dict_roundtrip(self):
        """to_dict() -> from_dict() -> to_dict() produce el mismo dict."""
        original = Note(id=7, title="Roundtrip", body="Test")
        d = original.to_dict()
        restored = Note.from_dict(d)
        assert restored == original
        assert restored.to_dict() == d


class TestNoteEquality:
    """Tests de igualdad entre notas."""

    def test_notes_equal(self):
        """Dos notas con mismos atributos son iguales."""
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        a = Note(id=1, title="X", body="Y", created_at=dt)
        b = Note(id=1, title="X", body="Y", created_at=dt)
        assert a == b

    def test_notes_not_equal_different_id(self):
        """Notas con distinto id no son iguales."""
        a = Note(id=1, title="X")
        b = Note(id=2, title="X")
        assert a != b

    def test_note_not_equal_to_dict(self):
        """Note no es igual a un dict."""
        note = Note(id=1, title="X")
        assert note != {"id": 1, "title": "X", "body": "", "created_at": note.created_at.isoformat()}


class TestNoteCompatibilityWithStorage:
    """Tests que verifican compatibilidad con src/storage.py."""

    def test_note_dict_can_be_saved_and_loaded(self):
        """El dict de Note es compatible con lo que storage.save() espera."""
        note = Note(id=1, title="Storage test", body="Contenido")
        d = note.to_dict()
        # Debe ser una lista de dicts, como lo que maneja storage
        notes_list = [d]
        # Verificar que es serializable (storage hace json.dump)
        json_str = json.dumps(notes_list)
        restored_list = json.loads(json_str)
        restored_note = Note.from_dict(restored_list[0])
        assert restored_note == note

    def test_from_dict_works_with_storage_format(self):
        """from_dict() puede consumir los dicts que storage.load() devuelve."""
        # Simula el formato que storage.load() devuelve
        data_from_storage = [
            {"id": 1, "title": "Nota 1", "body": "Cuerpo 1", "created_at": "2024-01-01T00:00:00+00:00"},
            {"id": 2, "title": "Nota 2"},
        ]
        notes = [Note.from_dict(item) for item in data_from_storage]
        assert len(notes) == 2
        assert notes[0].id == 1
        assert notes[0].body == "Cuerpo 1"
        assert notes[1].id == 2
        assert notes[1].body == ""  # body por defecto
