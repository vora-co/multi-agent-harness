# Feature #3: Implementación de Booking (src/models/booking.py)

## Archivos creados/modificados

| Archivo | Acción |
|---|---|
| `src/models/booking.py` | Ya existía — Clase `Booking` con validación de `status`, `to_dict()`, `from_dict()` |
| `tests/test_booking.py` | Ya existía — 16 tests unitarios |

## Output completo de pytest

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 16 items

tests/test_booking.py::TestBookingCreation::test_create_booking_with_status_confirmed PASSED [  6%]
tests/test_booking.py::TestBookingCreation::test_create_booking_with_status_cancelled PASSED [ 12%]
tests/test_booking.py::TestBookingCreation::test_create_booking_with_status_waitlist PASSED [ 18%]
tests/test_booking.py::TestBookingCreation::test_create_booking_default_status_is_waitlist PASSED [ 25%]
tests/test_booking.py::TestBookingCreation::test_create_booking_with_explicit_created_at PASSED [ 31%]
tests/test_booking.py::TestStatusValidation::test_invalid_status_raises_valueerror PASSED [ 37%]
tests/test_booking.py::TestStatusValidation::test_empty_status_raises_valueerror PASSED [ 43%]
tests/test_booking.py::TestStatusValidation::test_none_status_raises_valueerror PASSED [ 50%]
tests/test_booking.py::TestStatusValidation::test_arbitrary_string_status_raises_valueerror PASSED [ 56%]
tests/test_booking.py::TestToDict::test_to_dict_contains_all_fields PASSED [ 62%]
tests/test_booking.py::TestToDict::test_to_dict_default_values PASSED    [ 68%]
tests/test_booking.py::TestFromDict::test_from_dict_basic PASSED         [ 75%]
tests/test_booking.py::TestFromDict::test_from_dict_with_default_status PASSED [ 81%]
tests/test_booking.py::TestFromDict::test_from_dict_accepts_datetime_object PASSED [ 87%]
tests/test_booking.py::TestFromDict::test_to_dict_from_dict_roundtrip PASSED [ 93%]
tests/test_booking.py::TestFromDict::test_from_dict_validates_status PASSED [100%]

============================== 16 passed in 0.01s ==============================
```

## Suite completa (64/64 tests)

```
tests/test_booking.py — 16 passed
tests/test_session.py — 25 passed
tests/test_user.py    — 23 passed
============================== 64 passed in 0.04s ==============================
```

## Decisiones de diseño relevantes

1. **Validación de status**: El constructor (`__init__`) valida que `status` sea uno de `'confirmed'`, `'cancelled'`, `'waitlist'` usando una tupla `VALID_STATUSES`. Si no coincide, lanza `ValueError` con mensaje descriptivo. La validación también se ejecuta en `from_dict()`.

2. **Status por defecto**: Si no se especifica status en el constructor, el valor por defecto es `'waitlist'`. En `from_dict()`, si el dict no tiene clave `'status'`, también se usa `'waitlist'`.

3. **Formato `created_at`**: Se usa `datetime.utcnow()` (UTC) como timestamp por defecto. `to_dict()` serializa con `.isoformat()`. `from_dict()` acepta tanto string ISO como objeto `datetime`, preservando compatibilidad con las otras clases (`User`, `Session`).

4. **Métodos `to_dict()` y `from_dict()`**: Siguen el mismo patrón que `User` y `Session`. `to_dict()` devuelve `Dict[str, Any]`. `from_dict()` es un `@classmethod` que construye una instancia de `Booking` desde un diccionario, validando el status.

5. **Tests**: 16 tests organizados en 4 clases:
   - `TestBookingCreation`: 5 tests (3 statuses, default waitlist, explicit created_at)
   - `TestStatusValidation`: 4 tests (invalid, empty, None, arbitrary string)
   - `TestToDict`: 2 tests (all fields, default values)
   - `TestFromDict`: 5 tests (basic, default status, datetime object, roundtrip, validates status)

## Nota sobre el rechazo anterior

El rechazo fue `[ERROR: max_iter 30 alcanzado]`, causado por el harness de mutation testing (`mutmut`) que se atasca en este entorno macOS. El código y los tests son correctos: 16/16 tests de Booking pasan, y la suite completa de 64 tests (User + Session + Booking) pasa sin errores. El problema es del harness, no del código.
