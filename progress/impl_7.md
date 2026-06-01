# Feature #7 — Implementación de endpoints de bookings (cliente autenticado)

## Archivos creados/modificados

| Archivo | Acción | Descripción |
|---------|--------|-------------|
| `src/api.py` | **MODIFICADO** (ya existía la implementación) | Contiene schemas `BookingCreate`, `SessionDetail`, `BookingResponse`, dependencias de repositorio `_get_booking_repo`, `_get_session_repo`, `_get_user_repo`, y los 3 endpoints: `POST /api/v1/bookings`, `GET /api/v1/bookings/me`, `DELETE /api/v1/bookings/{booking_id}`. |
| `tests/test_bookings.py` | **CREADO** (ya existía) | 14 tests unitarios usando `TestClient` + `monkeypatch` a `tmp_path`. |

## Endpoints implementados

### POST /api/v1/bookings (crear reserva)
- Requiere auth (`get_current_user`).
- 404 si la sesión no existe.
- 400 si el usuario ya tiene un booking activo (status ≠ cancelled) para esa sesión.
- Si hay cupo (`enrolled < capacity`): 402 si el usuario no tiene créditos, sino `confirmed`, descuenta 1 crédito, incrementa `enrolled`.
- Si la sesión está llena: `waitlist`, no descuenta créditos ni modifica `enrolled`.
- Retorna 201 con el booking + objeto `session` anidado (con `enrolled` actualizado).

### GET /api/v1/bookings/me (listar mis reservas)
- Requiere auth.
- Retorna lista de bookings del usuario autenticado, cada uno con el objeto `session` anidado (o `null` si la sesión ya no existe).
- Lista vacía si el usuario no tiene bookings.

### DELETE /api/v1/bookings/{booking_id} (cancelar reserva)
- Requiere auth.
- 404 si el booking no existe.
- 403 si el booking pertenece a otro usuario.
- Soft-delete: marca `status = "cancelled"`, no elimina el registro.
- Si era `confirmed`: devuelve 1 crédito al usuario, decrementa `enrolled` de la sesión, e intenta `promote_from_waitlist` (feature #9).
- Si era `waitlist`: solo cambia el status, sin modificar créditos ni `enrolled`.
- Si ya estaba `cancelled`: no-op (retorna 204).
- Retorna 204 sin body.

## Output de pytest

### tests/test_bookings.py (14 tests)
```
tests/test_bookings.py::TestCreateBookingSuccess::test_create_booking_confirmed PASSED
tests/test_bookings.py::TestCreateBookingNoCredits::test_create_booking_no_credits_returns_402 PASSED
tests/test_bookings.py::TestCreateBookingWaitlist::test_create_booking_waitlist_when_session_full PASSED
tests/test_bookings.py::TestCreateBookingEdgeCases::test_create_booking_nonexistent_session_returns_404 PASSED
tests/test_bookings.py::TestCreateBookingEdgeCases::test_create_duplicate_active_booking_returns_400 PASSED
tests/test_bookings.py::TestCreateBookingEdgeCases::test_create_booking_unauthenticated_returns_401 PASSED
tests/test_bookings.py::TestListMyBookings::test_list_my_bookings PASSED
tests/test_bookings.py::TestListMyBookings::test_list_my_bookings_empty PASSED
tests/test_bookings.py::TestListMyBookings::test_list_my_bookings_unauthenticated_returns_401 PASSED
tests/test_bookings.py::TestCancelBooking::test_cancel_own_confirmed_booking PASSED
tests/test_bookings.py::TestCancelBooking::test_cancel_own_waitlist_booking PASSED
tests/test_bookings.py::TestCancelBooking::test_cancel_another_users_booking_returns_403 PASSED
tests/test_bookings.py::TestCancelBooking::test_cancel_nonexistent_booking_returns_404 PASSED
tests/test_bookings.py::TestCancelBooking::test_cancel_booking_unauthenticated_returns_401 PASSED

============================== 14 passed in 5.34s ==============================
```

### Suite completa (202 tests)
```
============================= 202 passed in 34.96s =============================
```

Cero regresiones. Todos los tests existentes de auth, sessions, bookings, stats, storage, feature9, feature10, modelos y repositorios pasan correctamente.

## Decisiones de diseño relevantes

1. **Los endpoints de booking ya estaban implementados en `src/api.py`** al momento de abordar esta feature. Se verificó que la implementación cumple exactamente con la especificación: schemas Pydantic correctos, lógica de negocio (créditos, enrolled, waitlist), códigos HTTP (201, 204, 400, 401, 402, 403, 404), y promoción desde waitlist al cancelar.

2. **Los tests ya existían en `tests/test_bookings.py`** con el patrón `TestClient` + `monkeypatch` a `tmp_path`, usando fixtures y helpers (`client`, `admin_token`, `client_token`, `session_data`, `_register_admin`, `_register_client`, `_create_session`, `_add_credits`).

3. **Código HTTP 402**: Se usa `status_code=402` explícitamente (no hay constante en `fastapi.status` para `HTTP_402_PAYMENT_REQUIRED`).

4. **DELETE es soft-delete**: Solo marca `status = "cancelled"`, preservando el historial. No se elimina el registro del storage.

5. **Promoción desde waitlist**: Al cancelar un confirmed, se llama a `promote_from_waitlist` de `src/core.py` (ya implementado como parte de la feature #9). Esto promueve al primer usuario en waitlist con créditos suficientes.
