# Reporte de implementación: Feature 3 - CLI básico con comandos add y list

## Archivos creados/modificados
- `src/cli.py` — Creado
- `tests/test_cli.py` — Creado

## Resumen de lo implementado

Se creó `src/cli.py` con un CLI basado en `argparse` que expone dos subcomandos:

### Comando `add <title> [--body TEXT]`
- Usa `storage.load()` para obtener las notas existentes.
- Calcula un id autoincremental mediante la función `_next_id()`: busca el máximo id numérico en los datos y suma 1. Si la lista está vacía o todos los ids son strings, comienza en 1.
- Crea una instancia `Note` con el id calculado, el título y el body opcional.
- Convierte la nota a dict con `to_dict()`, la agrega a la lista y guarda atómicamente con `storage.save()`.
- Muestra confirmación: `Nota agregada: [<id>] <title>`.

### Comando `list`
- Carga las notas con `storage.load()`.
- Si la lista está vacía, muestra `No hay notas guardadas.`
- Si hay notas, itera sobre cada dict, reconstruye `Note` con `from_dict()`, y muestra:
  - `[<id>] <title>`
  - `<body>` (solo si tiene contenido, con indentación de 4 espacios)
  - `Creada: <created_at ISO>`

### Manejo de casos especiales
- `data/notes.json` inexistente: `storage.load()` devuelve `[]`, ambos comandos funcionan correctamente.
- Archivo JSON vacío (lista `[]`): se maneja igual que el caso inexistente.
- `storage.save()` crea automáticamente el directorio `data/` si no existe y escribe atómicamente.

### Arquitectura
- `build_parser()` construye el parser con subcomandos.
- Cada subcomando asigna su `func` correspondiente (`cmd_add`, `cmd_list`) vía `set_defaults`.
- `main(argv)` acepta una lista de argumentos (para testing) o usa `sys.argv[1:]` por defecto.

## Resultado de los tests

```
tests/test_cli.py::test_next_id_empty_list PASSED                        [  2%]
tests/test_cli.py::test_next_id_sequential PASSED                        [  5%]
tests/test_cli.py::test_next_id_with_string_ids_ignored PASSED           [  8%]
tests/test_cli.py::test_next_id_all_string_ids PASSED                    [ 11%]
tests/test_cli.py::test_add_creates_note_and_confirms PASSED             [ 14%]
tests/test_cli.py::test_add_with_body PASSED                             [ 17%]
tests/test_cli.py::test_add_increments_id PASSED                         [ 20%]
tests/test_cli.py::test_add_persists_to_file_system PASSED               [ 22%]
tests/test_cli.py::test_list_empty_shows_message PASSED                  [ 25%]
tests/test_cli.py::test_list_shows_single_note PASSED                    [ 28%]
tests/test_cli.py::test_list_shows_multiple_notes PASSED                 [ 31%]
tests/test_cli.py::test_list_shows_note_without_body PASSED              [ 34%]
tests/test_cli.py::test_add_when_data_dir_missing PASSED                 [ 37%]
tests/test_cli.py::test_list_when_data_dir_missing PASSED                [ 40%]
tests/test_cli.py::test_list_with_empty_json_file PASSED                 [ 42%]
tests/test_notes.py::TestNoteCreation::test_minimal_creation PASSED      [ 45%]
tests/test_notes.py::TestNoteCreation::test_full_creation PASSED         [ 48%]
tests/test_notes.py::TestNoteCreation::test_creation_with_iso_string PASSED [ 51%]
tests/test_notes.py::TestNoteCreation::test_default_created_at_is_utc_aware PASSED [ 54%]
tests/test_notes.py::TestNoteToDict::test_to_dict_returns_serializable_dict PASSED [ 57%]
tests/test_notes.py::TestNoteToDict::test_to_dict_roundtrip_with_json PASSED [ 60%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_minimal PASSED     [ 62%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_full PASSED        [ 65%]
tests/test_notes.py::TestNoteFromDict::test_from_dict_roundtrip PASSED   [ 68%]
tests/test_notes.py::TestNoteEquality::test_notes_equal PASSED           [ 71%]
tests/test_notes.py::TestNoteEquality::test_notes_not_equal_different_id PASSED [ 74%]
tests/test_notes.py::TestNoteEquality::test_note_not_equal_to_dict PASSED [ 77%]
tests/test_notes.py::TestNoteCompatibilityWithStorage::test_note_dict_can_be_saved_and_loaded PASSED [ 80%]
tests/test_notes.py::TestNoteCompatibilityWithStorage::test_from_dict_works_with_storage_format PASSED [ 82%]
tests/test_storage.py::test_load_returns_empty_list_when_file_missing PASSED [ 85%]
tests/test_storage.py::test_load_returns_notes_when_file_exists PASSED   [ 88%]
tests/test_storage.py::test_save_creates_file PASSED                     [ 91%]
tests/test_storage.py::test_save_overwrites_existing_file PASSED         [ 94%]
tests/test_storage.py::test_save_is_atomic_does_not_corrupt_on_failure PASSED [ 97%]
tests/test_storage.py::test_data_dir_created_if_not_exists PASSED        [100%]

============================== 35 passed in 0.06s ==============================
```

## Decisiones de diseño
- **Id autoincremental numérico**: `_next_id()` itera sobre los dicts crudos de storage y busca el máximo `id` que sea convertible a `int`. Si hay ids string (por compatibilidad con `Note`), se ignoran para el cómputo. Si no hay ninguno, arranca en 1.
- **Separación comando/lógica**: cada subcomando tiene su propia función (`cmd_add`, `cmd_list`) y se asigna al parser vía `set_defaults(func=...)`. Esto mantiene `main()` limpio y facilita el testing.
- **Testing con `capsys`**: se usa el fixture `capsys` de pytest para capturar stdout y verificar la salida de los comandos.
- **Fixture `cleanup_data`**: cada test limpia el directorio `data/` antes y después, igual que en `test_storage.py`, para asegurar aislamiento total.
- **Sin dependencias externas**: solo se usan `argparse` (stdlib), `src.notes` y `src.storage`.
