# Feature #10: Notifications y Enroll from Waitlist (Reintento #2)

## Archivos creados/modificados

- **Creado**: `tests/test_feature10.py` — 12 tests para todos los endpoints de la feature.
- **Existente (sin modificar)**: Los endpoints ya estaban implementados correctamente en `src/api.py`:
  - `GET /api/v1/users/me/notifications` — `list_my_notifications`
  - `PUT /api/v1/users/me/notifications/{id}/read` — `mark_notification_read`
  - `PUT /api/v1/sessions/{id}/enroll_from_waitlist` — `enroll_from_waitlist`

  La lógica de negocio reside en:
  - `src/core.py` — `notify_user()` y `promote_from_waitlist()`
  - `src/models/notification.py` — Modelo `Notification` con soporte `read_at`
  - `src/repositories/notifications.py` — `NotificationRepository` con `find_by_user()`, `find_by_id()`, `save_one()`

## Output completo de pytest (feature #10)

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/fmejiavi/Documents/agentes-harness-prueba/tests
configfile: pytest.ini
plugins: anyio-4.12.1, asyncio-1.2.0, base-url-2.1.0, playwright-0.7.1
collected 12 items

tests/test_feature10.py::TestListMyNotifications::test_list_notifications_empty PASSED [  8%]
tests/test_feature10.py::TestListMyNotifications::test_list_notifications_ordered_by_created_at_desc PASSED [ 16%]
tests/test_feature10.py::TestListMyNotifications::test_list_notifications_requires_auth PASSED [ 25%]
tests/test_feature10.py::TestMarkNotificationRead::test_mark_own_notification_read PASSED [ 33%]
tests/test_feature10.py::TestMarkNotificationRead::test_mark_other_user_notification_returns_403 PASSED [ 41%]
tests/test_feature10.py::TestMarkNotificationRead::test_mark_nonexistent_notification_returns_404 PASSED [ 50%]
tests/test_feature10.py::TestMarkNotificationRead::test_mark_notification_read_requires_auth PASSED [ 58%]
tests/test_feature10.py::TestEnrollFromWaitlist::test_enroll_from_waitlist_successful PASSED [ 66%]
tests/test_feature10.py::TestEnrollFromWaitlist::test_enroll_from_waitlist_no_waitlist_returns_400 PASSED [ 75%]
tests/test_feature10.py::TestEnrollFromWaitlist::test_enroll_from_waitlist_requires_admin PASSED [ 83%]
tests/test_feature10.py::TestEnrollFromWaitlist::test_enroll_from_waitlist_unauthenticated_returns_401 PASSED [ 91%]
tests/test_feature10.py::TestEnrollFromWaitlist::test_enroll_from_waitlist_nonexistent_session_returns_404 PASSED [100%]

============================== 12 passed in 4.04s ==============================
```

## Output completo de pytest (full suite — 171 tests)

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 171 items

... (todos los tests pasan) ...

============================= 171 passed in 27.75s ==============================
```

## Decisiones de diseño relevantes

1. **Tests autocontenidos con monkeypatch**: Se usa `monkeypatch` para redirigir todos los repositorios (`UserRepository`, `SessionRepository`, `BookingRepository`, `NotificationRepository`) a `tmp_path`. También se parchea `notify_user` en `src.core` para garantizar que las notificaciones se persistan en el directorio temporal.

2. **Verificación de read_at**: El modelo `Notification.to_dict()` solo incluye `read_at` cuando no es `None`. Los tests usan `.get("read_at")` para ser robustos ante ambos casos (ausente o `null`).

3. **Ordenamiento de notificaciones**: `GET /users/me/notifications` ordena por `created_at` descendente con `list.sort(key=lambda n: n.created_at, reverse=True)`.

4. **Flujo enroll_from_waitlist**: `PUT /sessions/{id}/enroll_from_waitlist` delega en `core.promote_from_waitlist()`, que busca el primer waitlist (por `created_at` asc) con créditos >= 1, lo promueve a confirmed, descuenta 1 crédito, y notifica. Devuelve 400 si no hay waitlist o si ningún usuario tiene créditos.

5. **Cobertura de tests**: Los 12 tests cubren todos los requerimientos: leer notificaciones propias (vacías y con datos ordenados), marcar leída propia, marcar leída ajena → 403, notificación inexistente → 404, enroll_from_waitlist exitoso (confirma promoción, descuento de crédito, notificación), enroll_from_waitlist sin waitlist → 400, requiere admin → 403, no autenticado → 401, sesión inexistente → 404.
