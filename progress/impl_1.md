# Reporte de implementación: Feature 1 - Módulo de almacenamiento de notas en JSON

## Archivos creados/modificados
- `src/storage.py` — Creado
- `tests/test_storage.py` — Creado

## Resumen de lo implementado
Se creó `src/storage.py` con dos funciones:
- `load()`: Lee `data/notes.json` y devuelve la lista de diccionarios. Si el archivo no existe, retorna lista vacía.
- `save(notes: list)`: Escribe la lista en `data/notes.json` de forma atómica:
  1. Crea `data/` si no existe.
  2. Escribe a un archivo temporal con `tempfile.mkstemp`.
  3. Renombra con `os.replace` (atómico en el mismo filesystem).
  4. Si ocurre una excepción, elimina el temporal.

Se creó `tests/test_storage.py` con 6 tests unitarios que cubren:
- `test_load_returns_empty_list_when_file_missing`
- `test_load_returns_notes_when_file_exists`
- `test_save_creates_file`
- `test_save_overwrites_existing_file`
- `test_save_is_atomic_does_not_corrupt_on_failure`
- `test_data_dir_created_if_not_exists`

## Resultado de los tests
```
tests/test_storage.py::test_load_returns_empty_list_when_file_missing PASSED [ 16%]
tests/test_storage.py::test_load_returns_notes_when_file_exists PASSED   [ 33%]
tests/test_storage.py::test_save_creates_file PASSED                     [ 50%]
tests/test_storage.py::test_save_overwrites_existing_file PASSED         [ 66%]
tests/test_storage.py::test_save_is_atomic_does_not_corrupt_on_failure PASSED [ 83%]
tests/test_storage.py::test_data_dir_created_if_not_exists PASSED        [100%]

============================== 6 passed in 0.02s ===============================
```

## Decisiones de diseño
- Se usa `os.replace()` en lugar de `shutil.move()` porque es atómico cuando origen y destino están en el mismo filesystem.
- Se usa `tempfile.mkstemp()` con `dir=DATA_DIR` para garantizar que el temporal esté en el mismo filesystem que el destino.
- El `DATA_DIR` se define como constante a nivel de módulo para que pueda ser usado por tests y otras partes del código.
- Se eliminó el archivo temporal en caso de error para no dejar basura.
