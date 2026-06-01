# Spec — Feature #8: Auto-promoción desde waitlist al cancelar una reserva confirmed

## Archivos a crear o modificar

| Archivo | Acción |
|---|---|
| `src/core.py` | MODIFICACIÓN — función `promote_from_waitlist` |
| `tests/test_bookings.py` | MODIFICACIÓN — clase `TestCancelBooking`: añadir 3 tests + helper de patching de notificaciones |

---

## Implementación

### src/core.py — modificar `promote_from_waitlist`

La función ya existe y hace casi todo lo requerido: busca el primer waitlist ordenado por `created_at` ASC, verifica créditos, deduce 1 crédito, cambia status a confirmed y notifica.

**Lo que falta**: al promover exitosamente, NO se actualiza `session.enrolled`. El endpoint `cancel_booking` ya decrementa `enrolled` por la cancelación del confirmed; `promote_from_waitlist` debe incrementarlo en 1 al promover a alguien para que el neto sea correcto.

**Cambio puntual**: después de `booking_repo.save_one(booking)` y antes de `notify_user(...)`, insertar:

```python
# Increment enrolled — one waitlisted user now occupies the freed spot
session = session_repo.find_by_id(session_id)
if session is not None:
    session.enrolled += 1
    session_repo.save_one(session)
```

**Firma resultante** (sin cambios en firma externa, solo en comportamiento):

```python
def promote_from_waitlist(
    session_id: int,
    booking_repo: BookingRepository,
    session_repo: SessionRepository,
    user_repo: UserRepository,
) -> bool:
    """Find the first waitlisted booking (ordered by created_at ASC) for the
    given session and promote it to 'confirmed' if the user has >= 1 credit.

    On successful promotion:
      - Deduct 1 credit from the user.
      - Change booking status to 'confirmed'.
      - Increment session.enrolled by 1.
      - Send a notification via notify_user().

    If the first waitlisted user has no credits, skip to the next.
    Returns True if a promotion occurred, False otherwise.
    """
```

**Cambio exacto en el código** (dentro del `for booking in waitlisted:` loop, reemplazar el bloque de éxito actual):

Antes:
```python
            user.credits -= 1
            user_repo.save_one(user)

            booking.status = Booking.CONFIRMED
            booking_repo.save_one(booking)

            # enrolled stays the same (one left, one joined)
            notify_user(...)
            return True
```

Después:
```python
            user.credits -= 1
            user_repo.save_one(user)

            booking.status = Booking.CONFIRMED
            booking_repo.save_one(booking)

            # Increment enrolled — freed spot is now taken by promoted user
            session = session_repo.find_by_id(session_id)
            if session is not None:
                session.enrolled += 1
                session_repo.save_one(session)

            notify_user(
                booking.user_id,
                f"You have been promoted from the waitlist for session {session_id}!"
            )
            return True
```

### src/api.py — sin cambios

El endpoint `DELETE /api/v1/bookings/{booking_id}` (`cancel_booking`) ya llama a `promote_from_waitlist(...)` cuando `was_confirmed` es True (línea ~458). No se requiere ninguna modificación.

---

## Tests a escribir

### tests/test_bookings.py — añadir a la clase `TestCancelBooking`

**Preparación común para los 3 tests nuevos**: `promote_from_waitlist` llama a `notify_user`, que instancia `NotificationRepository()` con `data_dir="data"`. Para que los tests no escriban en el filesystem real, hay que parchear `notify_user` o `NotificationRepository.__init__`. Se usará la misma técnica que en `tests/test_feature10.py`: parchear `src.core.notify_user` con una versión que use `tmp_path`.

**Helper a añadir al módulo** (al inicio del archivo, junto con los otros helpers):

```python
def _patch_notify_user_for_tmp(monkeypatch, tmp_path):
    """Patch notify_user so notifications are written to tmp_path."""
    import src.core as core_mod

    def patched_notify_user(user_id: int, message: str) -> None:
        from src.repositories.notifications import NotificationRepository
        from src.models.notification import Notification
        repo = NotificationRepository(data_dir=str(tmp_path))
        all_notifications = repo.find_all()
        next_id = max((n.id for n in all_notifications), default=0) + 1
        notification = Notification(
            id=next_id,
            user_id=user_id,
            message=message,
        )
        repo.save_one(notification)

    monkeypatch.setattr(core_mod, "notify_user", patched_notify_user)
```

---

#### Test 1: `test_cancel_confirmed_promotes_first_waitlisted_with_credits`

- **Precondición**:
  - Session con `capacity=1`.
  - User A (con 5 créditos) reserva → confirmed (ocupa el único cupo, enrolled=1).
  - User B (con 5 créditos) reserva → waitlist (session llena).
  - Ambos tienen créditos asignados vía `_add_credits`.
  - Se parchea `notify_user` con `_patch_notify_user_for_tmp(monkeypatch, tmp_path)`.

- **Acción**:
  - User A (dueño del confirmed) hace `DELETE /api/v1/bookings/{booking_a_id}`.

- **Assertions**:
  - Status code 204.
  - Booking de User A: status `"cancelled"`.
  - Booking de User B: status `"confirmed"` (fue promovido).
  - Créditos de User A: restaurados a 5 (tenía 4 después del booking, vuelve a 5).
  - Créditos de User B: 4 (tenía 5, se le descontó 1 por la promoción).
  - Session.enrolled: 1 (A salió → 0, B entró → 1; neto = 1).
  - Se generó notificación para User B con mensaje que contiene `"promoted from the waitlist"` (case-insensitive).

---

#### Test 2: `test_cancel_confirmed_skips_waitlisted_without_credits_and_promotes_next`

- **Precondición**:
  - Session con `capacity=1`.
  - User A (con 5 créditos) reserva → confirmed (cupo lleno, enrolled=1).
  - User B (con **0 créditos**, `_add_credits(amount=0)`) reserva → waitlist (primero en la cola).
  - User C (con 5 créditos) reserva → waitlist (segundo en la cola).
  - Se parchea `notify_user`.

- **Acción**:
  - User A hace `DELETE /api/v1/bookings/{booking_a_id}`.

- **Assertions**:
  - Status code 204.
  - Booking de User A: `"cancelled"`.
  - Booking de User B: sigue `"waitlist"` (no tenía créditos, fue saltado).
  - Booking de User C: `"confirmed"` (fue promovido tras saltar a B).
  - Créditos de User B: siguen en 0 (no se tocaron).
  - Créditos de User C: 4 (tenía 5, descontado 1).
  - Session.enrolled: 1.
  - Notificación para User C con `"promoted from the waitlist"`.

---

#### Test 3: `test_cancel_waitlist_does_not_trigger_promotion`

- **Precondición**:
  - Session con `capacity=1`.
  - User A (con 5 créditos) reserva → confirmed (cupo lleno, enrolled=1).
  - User B (con 5 créditos) reserva → waitlist.
  - User C (con 5 créditos) reserva → waitlist.
  - Se parchea `notify_user`.

- **Acción**:
  - User B (dueño de la waitlist) hace `DELETE /api/v1/bookings/{booking_b_id}`.

- **Assertions**:
  - Status code 204.
  - Booking de User B: `"cancelled"`.
  - Booking de User A: sigue `"confirmed"`.
  - Booking de User C: sigue `"waitlist"` (no hubo promoción porque no se liberó cupo).
  - Créditos de User A: 4 (sin cambios, su confirmed no se tocó).
  - Créditos de User B: 5 (nunca se dedujeron por ser waitlist).
  - Créditos de User C: 5 (sin cambios).
  - Session.enrolled: 1 (sin cambios).

---

## Dependencias

Ninguna librería nueva. Todo se implementa con el stack existente: FastAPI, pytest, `monkeypatch`, `tmp_path`.

---

## Notas de implementación

1. **`promote_from_waitlist` se comparte con Feature #10**: el endpoint `enroll_from_waitlist` también llama a esta función. El cambio de incrementar `session.enrolled` beneficia a ambos: en cancel_booking el neto es 0 (uno sale, otro entra), y en enroll_from_waitlist enrolled sube en 1 (sin cancelación previa). El implementer de Feature #10 debe saber que este cambio ya cubre esa parte.

2. **Orden de los waitlist**: la función ordena por `booking.created_at` ASC. Si dos bookings tienen exactamente el mismo timestamp (raro pero posible en tests con `datetime.utcnow()`), el orden entre ellos es indeterminado. Para los tests esto no es problema porque creamos los bookings secuencialmente.

3. **Patching de notificaciones**: los 3 tests nuevos requieren que `notify_user` funcione sin acceso al directorio `data/` real. El helper `_patch_notify_user_for_tmp` debe llamarse al inicio de cada test (no es un fixture con scope de módulo porque necesita el `tmp_path` del test).

4. **No modificar el `client` fixture existente**: los tests antiguos de `TestCancelBooking` no necesitan el patch de notificaciones porque en sus escenarios no hay waitlist que promocionar (solo hay un usuario), así que `promote_from_waitlist` retorna `False` sin llamar a `notify_user`.

5. **El endpoint `cancel_booking` ya está cableado**: la llamada a `promote_from_waitlist` existe en `src/api.py` línea ~458 dentro de `if was_confirmed:`. No tocar esa integración.
