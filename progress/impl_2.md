# Reporte de implementación: Feature 2 - Modelo de dominio Note

## Archivos creados/modificados
- `src/notes.py` — Creado
- `tests/test_notes.py` — Creado

## Resumen de lo implementado
Se creó `src/notes.py` con la clase `Note` que incluye:

### Atributos
- `id` (Union[int, str]) — identificador único
- `title` (str) — título de la nota
- `body` (str) — cuerpo o contenido de la nota (default: `""`)
- `created_at` (datetime) — fecha de creación con timezone UTC

### Métodos
- `__init__(id, title, body="", created_at=None)` — acepta `created_at` como `datetime`, string ISO, o `None` (genera UTC actual)
- `to_dict()` — devuelve dict serializable a JSON con `created_at` en formato ISO
- `from_dict(data: dict)` — método de clase que construye `Note` desde un dict (compatible con el formato de `storage.load()`)
- `__eq__` — permite comparar notas por valor
- `__repr__` — representación legible para debugging

### Compatibilidad con storage
- `to_dict()` genera dicts que `storage.save()` puede serializar sin problema
- `from_dict()` puede consumir los dicts que `storage.load()` devuelve
- Soporta campos opcionales (`body`, `created_at`) para manejar datos parciales

## Resultado de los tests
```
tests/test_notes.py::TestNoteCreation::test_minimal_creation PASSED      [  5%]
tests/test_notes.py::TestNoteCreation::test_full_creation PASSED         [ 10%]
tests/test_notes.py::TestNoteCreation::test_creation_with_iso_string PASSED [ 15%]
tests/test_notes.py::TestNoteCreation::test_default_created_at_is_utc_aware PASSED [ 20%]
tests/test_notes.py::TestNoteToDict::test_to_dict_returns_serializable_dict PASSED [ 25%]
tests/test_notes.py::TestNoteToDict::test_to_dict_roundtrip_with_json PASSED [ 30%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_minimal PASSED     [ 35%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_full PASSED        [ 40%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_roundtrip PASSED   [ 45%]
tests/test_notes.py::TestNoteEquality::test_notes_equal PASSED           [ 50%]
tests/test_notes.py::TestNoteEquality::test_notes_not_equal_different_id PASSED [ 55%]
tests/test_notes.py::TestNoteEquality::test_note_not_equal_to_dict PASSED [ 60%]
tests/test_notes.py::TestNoteCompatibilityWithStorage::test_note_dict_can_be_saved_and_loaded PASSED [ 65%]
tests/test_notes.py::TestNoteCompatibilityWithStorage::test_from_dict_works_with_storage_format PASSED [ 70%]
tests/test_storage.py::test_load_returns_empty_list_when_file_missing PASSED [ 75%]
tests/test_storage.py::test_load_returns_notes_when_file_exists PASSED   [ 80%]
tests/test_storage.py::test_save_creates_file PASSED                     [ 85%]
tests/test_storage.py::test_save_overwrites_existing_file PASSED         [ 90%]
tests/test_storage.py::test_save_is_atomic_does_not_corrupt_on_failure PASSED [ 95%]
tests/test_storage.py::test_data_dir_created_if_not_exists PASSED        [100%]

============================== 20 passed in 0.03s ===============================
```

## Decisiones de diseño
- Se usó `Union[int, str]` en lugar de `int | str` (PEP 604) para mantener compatibilidad con Python 3.9.
- `created_at` por defecto se genera con `datetime.now(timezone.utc)` para ser timezone-aware y evitar ambigüedades.
- Se usa `data.get("body", "")` y `data.get("created_at", None)` en `from_dict()` para manejar datos incompletos que pudieran venir de storage (por ejemplo, notas guardadas antes de agregar el campo `body`).
- `__eq__` devuelve `NotImplemented` cuando se compara con no-`Note` para seguir el protocolo de Python y permitir que la otra clase decida.
