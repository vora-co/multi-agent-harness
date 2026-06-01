# Review #8: Auto-promoción de waitlist al cancelar una reserva confirmed

## Checklist de CHECKPOINTS.md

### Código
- [x] **Los archivos nuevos están en src/ o tests/ según corresponda** — PASS. La feature no introduce archivos nuevos; modifica `tests/test_bookings.py` (3 tests nuevos + helper `_patch_notify_user_for_tmp` + ajuste al fixture `client`). `src/api.py` y `src/core.py` no requirieron cambios porque la lógica ya residía en el endpoint `DELETE /api/v1/bookings/{id}` y en `promote_from_waitlist`.
- [x] **No hay print() de debug sin comentario explicativo** — PASS. `grep` sobre los archivos modificados no encontró `print()`.
- [x] **No hay TODOs sin contexto (fecha + razón)** — PASS. `grep -rin TODO` no encontró coincidencias.
- [x] **Sigue la convención de nombres en docs/conventions.md** — PASS. Tests en `tests/test_bookings.py`, clase `TestCancelBooking`, nombres descriptivos, uso de `tmp_path` sin mockear storage.

### Tests
- [x] **Existe al menos un test por función pública nueva** — PASS. No hay funciones públicas nuevas. Se añadieron 3 tests que cubren el comportamiento de auto-promoción: (1) promoción del primer waitlisted con créditos, (2) skip de waitlisted sin créditos y promoción del siguiente, (3) cancelación de waitlist no dispara promoción.
- [x] **`python -m pytest tests/ -v` termina con 0 errores y 0 failures** — PASS. 205/205 tests pasan (ver output abajo).
- [x] **Los tests no dependen de estado externo sin limpiarlo en teardown** — PASS. Usan `tmp_path` + monkeypatching de `NotificationRepository.__init__` y `notify_user`.

### Documentación
- [x] **Cada función nueva tiene docstring de una línea** — PASS. `_patch_notify_user_for_tmp` y los 3 nuevos métodos de test tienen docstrings.
- [x] **progress/impl_8.md existe y lista los archivos tocados** — PASS.

### Integración
- [x] **El código nuevo no rompe tests de features anteriores (pytest sobre todo tests/)** — PASS. Full suite: 205 passed.
- [x] **No hay imports circulares** — PASS. Sin imports nuevos; los existentes no forman ciclos.

---

## Output de pytest

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
collected 205 items

... 205 passed in 37.46s ...
============================= 205 passed in 37.46s =============================
```

0 errores, 0 failures.

---

## Veredicto

**APPROVED**
