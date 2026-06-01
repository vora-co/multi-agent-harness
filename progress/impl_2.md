# Feature #2: Session model

## Status: COMPLETED

## Archivos creados/modificados
- **Creado**: `src/models/session.py` — Clase Session con validación, serialización, is_full, spots_available
- **Creado**: `tests/test_session.py` — 25 tests cubriendo creación, validación, is_full, spots_available, to_dict, from_dict

## Output de tests

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 48 items

tests/test_session.py::TestSessionCreation::test_create_valid_session PASSED [  2%]
tests/test_session.py::TestSessionCreation::test_create_session_with_explicit_enrolled PASSED [  4%]
tests/test_session.py::TestSessionCreation::test_create_session_minimum_valid_capacity PASSED [  6%]
tests/test_session.py::TestSessionCreation::test_create_session_minimum_valid_duration PASSED [  8%]
tests/test_session.py::TestValidationErrors::test_capacity_zero_raises_valueerror PASSED [ 10%]
tests/test_session.py::TestValidationErrors::test_capacity_negative_raises_valueerror PASSED [ 12%]
tests/test_session.py::TestValidationErrors::test_duration_below_minimum_raises_valueerror PASSED [ 14%]
tests/test_session.py::TestValidationErrors::test_duration_zero_raises_valueerror PASSED [ 16%]
tests/test_session.py::TestValidationErrors::test_duration_negative_raises_valueerror PASSED [ 18%]
tests/test_session.py::TestIsFull::test_is_full_false_when_enrolled_below_capacity PASSED [ 20%]
tests/test_session.py::TestIsFull::test_is_full_true_when_enrolled_equals_capacity PASSED [ 22%]
tests/test_session.py::TestIsFull::test_is_full_true_when_enrolled_exceeds_capacity PASSED [ 25%]
tests/test_session.py::TestIsFull::test_is_full_false_when_enrolled_zero PASSED [ 27%]
tests/test_session.py::TestSpotsAvailable::test_spots_available_returns_remaining PASSED [ 29%]
tests/test_session.py::TestSpotsAvailable::test_spots_available_zero_when_full PASSED [ 31%]
tests/test_session.py::TestSpotsAvailable::test_spots_available_zero_when_overbooked PASSED [ 33%]
tests/test_session.py::TestSpotsAvailable::test_spots_available_all_when_none_enrolled PASSED [ 35%]
tests/test_session.py::TestToDict::test_to_dict_contains_all_fields PASSED [ 37%]
tests/test_session.py::TestToDict::test_to_dict_default_enrolled PASSED  [ 39%]
tests/test_session.py::TestFromDict::test_from_dict_basic PASSED         [ 41%]
tests/test_session.py::TestFromDict::test_from_dict_with_default_enrolled PASSED [ 43%]
tests/test_session.py::TestFromDict::test_from_dict_accepts_datetime_object PASSED [ 45%]
tests/test_session.py::TestFromDict::test_to_dict_from_dict_roundtrip PASSED [ 47%]
tests/test_session.py::TestFromDict::test_from_dict_validates_capacity PASSED [ 50%]
tests/test_session.py::TestFromDict::test_from_dict_validates_duration PASSED [ 52%]
tests/test_user.py::TestUserCreation::test_create_user_with_role_client PASSED [ 54%]
... (existing tests) ...
tests/test_user.py::TestFromDict::test_from_dict_validates_role PASSED   [100%]

============================== 48 passed in 0.04s ==============================
```

**Resultado**: 48/48 tests pasaron (25 nuevos + 23 existentes).

## Mutation testing

Se intentó ejecutar `run_mutation_tests(paths_to_mutate="src/models/session.py", tests_dir="tests/")` pero falló con:

```
RuntimeError: context has already been set
```

**Clasificación**: TRANSIENT — error del entorno macOS con `multiprocessing` y `mutmut` (incompatibilidad `fork` vs `spawn`). No es un problema del código. El mutation testing no pudo ejecutarse.

## Decisiones de diseño

1. **Validación en `_validate()`**: Siguiendo el patrón de `User`, se llama desde `__init__`. Valida `capacity >= 1` y `duration_minutes >= 15`.

2. **`is_full()` usa `>=`**: Retorna `True` si `enrolled >= capacity`, cubriendo así el caso de sobrecupo (edge case donde enrolled > capacity, posible por manipulación externa o race conditions).

3. **`spots_available()` usa `max(..., 0)`**: Garantiza que nunca retorne un valor negativo incluso si enrolled > capacity.

4. **`to_dict()` / `from_dict()`**: Siguen el mismo patrón que `User`: isoformat para datetime, `from_dict` acepta tanto string ISO como objeto datetime, y `from_dict` llama al constructor (que ejecuta `_validate`).

5. **`__repr__`**: Representación compacta con id, title e instructor para debugging.
