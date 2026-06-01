# Feature #4 — Implementación: Capa de almacenamiento JSON

## Archivos creados/modificados

| Archivo | Acción | Descripción |
|---|---|---|
| `src/storage.py` | Ya existía (verificado) | `load()` y `save()` con escritura atómica vía `tempfile.mkstemp` + `os.replace()` |
| `src/repositories/__init__.py` | Ya existía (verificado) | Archivo vacío, convierte `repositories/` en paquete |
| `src/repositories/users.py` | Ya existía (verificado) | `UserRepository` con `find_all`, `find_by_id`, `save_one`, `delete`, `find_by_email` |
| `src/repositories/sessions.py` | Ya existía (verificado) | `SessionRepository` con `find_all`, `find_by_id`, `save_one`, `delete` |
| `src/repositories/bookings.py` | Ya existía (verificado) | `BookingRepository` con `find_all`, `find_by_id`, `save_one`, `delete`, `find_by_user`, `find_by_session` |
| `tests/test_storage.py` | Ya existía (verificado) | 7 tests para `load()` y `save()` |
| `tests/test_repositories.py` | Ya existía (verificado) | 17 tests para los tres repositorios |

## Output completo de pytest

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/fmejiavi/Documents/agentes-harness-prueba/tests
configfile: pytest.ini
plugins: anyio-4.12.1, asyncio-1.2.0, base-url-2.1.0, playwright-0.7.1
collecting ... collected 24 items

tests/test_storage.py::TestLoad::test_load_returns_empty_list_when_file_missing PASSED [  4%]
tests/test_storage.py::TestLoad::test_load_returns_records_when_file_exists PASSED [  8%]
tests/test_storage.py::TestSave::test_save_writes_and_load_reads_back PASSED [ 12%]
tests/test_storage.py::TestSave::test_save_creates_data_directory_if_missing PASSED [ 16%]
tests/test_storage.py::TestSave::test_save_is_atomic_no_partial_writes PASSED [ 20%]
tests/test_storage.py::TestSave::test_save_overwrites_existing_file PASSED [ 25%]
tests/test_storage.py::TestSave::test_save_preserves_indent_and_encoding PASSED [ 29%]
tests/test_repositories.py::TestUserRepository::test_save_and_find_all PASSED [ 33%]
tests/test_repositories.py::TestUserRepository::test_save_and_find_by_id PASSED [ 37%]
tests/test_repositories.py::TestUserRepository::test_find_by_id_returns_none_when_not_found PASSED [ 41%]
tests/test_repositories.py::TestUserRepository::test_find_by_id_returns_none_when_empty PASSED [ 45%]
tests/test_repositories.py::TestUserRepository::test_delete_removes_only_the_correct_record PASSED [ 50%]
tests/test_repositories.py::TestUserRepository::test_delete_returns_false_when_not_found PASSED [ 54%]
tests/test_repositories.py::TestUserRepository::test_save_one_updates_existing_record PASSED [ 58%]
tests/test_repositories.py::TestSessionRepository::test_save_and_find_all PASSED [ 62%]
tests/test_repositories.py::TestSessionRepository::test_find_by_id_returns_none_when_not_found PASSED [ 66%]
tests/test_repositories.py::TestSessionRepository::test_delete_removes_only_the_correct_record PASSED [ 70%]
tests/test_repositories.py::TestSessionRepository::test_delete_returns_false_when_not_found PASSED [ 75%]
tests/test_repositories.py::TestSessionRepository::test_save_one_updates_existing_record PASSED [ 79%]
tests/test_repositories.py::TestBookingRepository::test_save_and_find_all PASSED [ 83%]
tests/test_repositories.py::TestBookingRepository::test_find_by_id_returns_none_when_not_found PASSED [ 87%]
tests/test_repositories.py::TestBookingRepository::test_delete_removes_only_the_correct_record PASSED [ 91%]
tests/test_repositories.py::TestBookingRepository::test_delete_returns_false_when_not_found PASSED [ 95%]
tests/test_repositories.py::TestBookingRepository::test_save_one_updates_existing_record PASSED [100%]

============================== 24 passed in 0.04s ==============================
```

## Decisiones de diseño relevantes

1. **Archivos ya existentes**: Todos los archivos fuente y de tests ya estaban implementados en el repositorio. Se verificó que cumplen con la especificación del spec_4.md.

2. **`storage.py`**:
   - `load()` retorna `[]` si el archivo no existe, sin lanzar error.
   - `save()` usa `tempfile.mkstemp` con sufijo `.json` y prefijo `{entity}_`, escribe con `indent=2` y `default=str`, y aplica `os.replace()` para atomicidad.
   - En caso de excepción durante la escritura, limpia el archivo temporal y relanza.

3. **Repositorios**: Los tres (`UserRepository`, `SessionRepository`, `BookingRepository`) comparten el mismo patrón:
   - `_entity` y `_data_dir` como atributos privados.
   - `save_one()` implementa upsert: reemplaza si el id existe, agrega si no.
   - `delete()` retorna `bool`: `True` si eliminó, `False` si el id no existía.
   - `find_by_id()` retorna `None` si no encuentra, nunca lanza excepción.

4. **Métodos adicionales**: `UserRepository.find_by_email()`, `BookingRepository.find_by_user()`, `BookingRepository.find_by_session()` están incluidos para features posteriores (#5, #7, #8).

5. **Tests**: Todos los tests usan `tmp_path` de pytest como `data_dir`, garantizando aislamiento total sin contaminar el sistema de archivos real.
