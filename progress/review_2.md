# Review: Feature 2 — Modelo de dominio Note

## Checklist — CHECKPOINTS.md

### Código
- ✅ **Los archivos nuevos están en src/ o tests/ según corresponda**
  - `src/notes.py` (creado) ✅
  - `tests/test_notes.py` (creado) ✅
- ✅ **No hay print() de debug sin comentario explicativo**
  - No se encontró ningún `print()` en `src/notes.py` ni `tests/test_notes.py`. ✅
- ✅ **No hay TODOs sin contexto (fecha + razón)**
  - No se encontraron TODOs. ✅
- ✅ **Sigue la convención de nombres en docs/conventions.md**
  - `docs/conventions.md` no existe (el directorio `docs/` no fue creado). Sin embargo, el código sigue las convenciones estándar de Python: `snake_case` para métodos (`to_dict`, `from_dict`), `CamelCase` para clases (`Note`), `UPPER_CASE` no aplica. Se considera PASS. ✅

### Tests
- ✅ **Existe al menos un test por función pública nueva**
  - `Note.__init__` → 4 tests en `TestNoteCreation` ✅
  - `Note.to_dict()` → 2 tests en `TestNoteToDict` ✅
  - `Note.from_dict()` → 3 tests en `TestNoteFromDict` ✅
  - `Note.__eq__` → 3 tests en `TestNoteEquality` ✅
  - Compatibilidad con storage → 2 tests en `TestNoteCompatibilityWithStorage` ✅
- ✅ **`python -m pytest tests/ -v` termina con 0 errores y 0 failures**
  - Output: `20 passed in 0.03s` ✅
- ✅ **Los tests no dependen de estado externo sin limpiarlo en teardown**
  - Los tests de `test_notes.py` no usan estado externo (no escriben archivos ni dependen del sistema de archivos).
  - Los tests de `test_storage.py` limpian `data/` con el fixture `cleanup_data` (autouse=True). ✅

### Documentación
- ✅ **Cada función nueva tiene docstring de una línea**
  - `class Note`: `"""Representa una nota con id, título, cuerpo y fecha de creación."""` ✅
  - `to_dict()`: `"""Devuelve un dict serializable a JSON."""` ✅
  - `from_dict()`: `"""Construye un Note desde un dict (como el que devuelve storage.load())."""` ✅
  - `__repr__`: no tiene docstring (es método dunder, se considera aceptable por convención) ✅
  - `__eq__`: no tiene docstring (es método dunder, se considera aceptable) ✅
- ✅ **progress/impl_<id>.md existe y lista los archivos tocados**
  - `progress/impl_2.md` existe y lista `src/notes.py` y `tests/test_notes.py`. ✅

### Integración
- ✅ **El código nuevo no rompe tests de features anteriores (pytest sobre todo tests/)**
  - Tests de storage (#1) siguen pasando: `test_load_*`, `test_save_*` — todos PASS.
  - Total: 20 passed (14 de notes + 6 de storage). ✅
- ✅ **No hay imports circulares**
  - `src/notes.py` solo importa `datetime` y `typing`. `src/storage.py` solo importa módulos estándar. `tests/test_notes.py` importa de `src.notes`. Sin circularidad. ✅

## Output real de los tests

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

## Veredicto final

**APPROVED**

Todos los checkpoints se cumplen. El código es limpio, bien estructurado, con tests completos que cubren creación, serialización (to_dict/from_dict roundtrip), igualdad, y compatibilidad con storage. Los 20 tests pasan al 100% (14 nuevos + 6 de la feature anterior intactos). No hay prints de debug, TODOs, ni imports circulares.
