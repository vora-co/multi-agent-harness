# Feature #8: Auto-promoción de waitlist al cancelar una reserva confirmed

## Archivos modificados

### `tests/test_bookings.py`
- **`client` fixture**: Se agregó el monkey-patch de `NotificationRepository.__init__` para redirigir a `tmp_path`. Esto corrigió el problema de que las notificaciones se escribían en `data/notifications.json` en lugar del directorio temporal de pruebas.
- **`_patch_notify_user_for_tmp`**: Nueva helper que monkeypatchea `src.core.notify_user` para escribir notificaciones en `tmp_path` (necesario porque `notify_user` se importa directamente en `api.py` y usa el `data_dir` por defecto).
- **Tres tests nuevos** en `TestCancelBooking`:
  - `test_cancel_confirmed_promotes_first_waitlisted_with_credits`: Cancela un confirmed (capacidad=1), verifica que el primer waitlisted con créditos es promovido a confirmed, se descuenta 1 crédito, enrolled=1, y recibe notificación.
  - `test_cancel_confirmed_skips_waitlisted_without_credits_and_promotes_next`: Primer waitlisted sin créditos → se salta, promueve al segundo waitlisted con créditos. Verifica que el sin créditos sigue en waitlist.
  - `test_cancel_waitlist_does_not_trigger_promotion`: Cancela un waitlist → no dispara promoción. Todos los bookings mantienen su status y créditos.

### `src/api.py` y `src/core.py`
No se modificaron: la lógica de promoción ya estaba implementada correctamente en el endpoint `DELETE /api/v1/bookings/{id}`.

## Decisiones de diseño

1. **Doble monkey-patching en tests**: Fue necesario parchear tanto `NotificationRepository.__init__` (para que el endpoint API lea/escriba del `tmp_path`) como `notify_user` (porque se importa directamente en `api.py` con `from src.core import notify_user`, y su `NotificationRepository` interno usa el `data_dir` hardcodeado). Sin ambos parches, las notificaciones se filtraban a `data/notifications.json`.

2. **La lógica de negocio ya residía en `api.py`**: El código del endpoint DELETE ya iteraba sobre `waiting_bookings` ordenados por `created_at`, verificaba `user.credits > 0`, y promovía al primero elegible. Los tests validan esta implementación existente.

## Output de pytest

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 205 items

tests/test_bookings.py::TestCancelBooking::test_cancel_confirmed_promotes_first_waitlisted_with_credits PASSED [ 19%]
tests/test_bookings.py::TestCancelBooking::test_cancel_confirmed_skips_waitlisted_without_credits_and_promotes_next PASSED [ 19%]
tests/test_bookings.py::TestCancelBooking::test_cancel_waitlist_does_not_trigger_promotion PASSED [ 20%]

[... all 205 tests passed ...]

============================= 205 passed in 37.53s =============================
```

Todos los tests (205/205) pasan, incluyendo los de features previas.
