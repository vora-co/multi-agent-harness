# Feature #11: Páginas del cliente - Implementación

## Archivos creados/modificados

### Creados
- `frontend/src/pages/SchedulePage.jsx` — Página de agenda de sesiones con:
  - Grilla responsive (1 col mobile, 2 tablet, 3 desktop)
  - Badge "Lista de espera" cuando `enrolled >= capacity`
  - Botón "Reservar" que llama a `POST /api/v1/bookings`
  - Filtros por estilo (dropdown) y fecha (date input)
  - Banner de feedback tras reservar (éxito/error)
  - Estados: loading spinner, error con reintento, vacío

- `frontend/src/pages/MyBookingsPage.jsx` — Página "Mis Reservas" con:
  - Tabla responsive con columnas: Sesión, Instructor, Estilo, Fecha, Estado, Acciones
  - Badges de estado color-coded (Confirmada/Lista de espera/Cancelada)
  - Botón "Cancelar" que abre modal de confirmación
  - Modal con botones "Confirmar Cancelar" y "Volver"
  - Spinner durante la cancelación

### Modificados
- `frontend/src/App.jsx` — Agregadas rutas `/schedule` y `/my-bookings` protegidas con `PrivateRoute`
- `frontend/src/components/NavBar.jsx` — Agregados links "Agenda" y "Mis Reservas" en navbar desktop y mobile

## Output de pytest

```
============================= test session starts ==============================
collected 225 items

(...)

=========================== short test summary info ============================
ERROR tests/test_feature_9.py::SPATest::test_static_build_existe
ERROR tests/test_feature_9.py::SPATest::test_vite_dev_dashboard_redirige_sin_token
ERROR tests/test_feature_9.py::SPATest::test_vite_dev_sirve_home
ERROR tests/test_feature_9.py::SPATest::test_vite_dev_sirve_login
ERROR tests/test_feature_9.py::SPATest::test_vite_dev_sirve_register_navegacion
======================== 220 passed, 5 errors in 50.88s ========================
```

Los 5 errores son preexistentes en `test_feature_9.py::SPATest` (fallo de `npm install` por conflicto de dependencias en un proyecto Vue.js no relacionado con el frontend React actual). Los 220 tests restantes pasan exitosamente.

## Decisiones de diseño

1. **SchedulePage**: Se usó `useCallback` para `fetchSessions` con dependencias de filtros, lo que permite refrescar automáticamente al cambiar filtros y tras una reserva exitosa.

2. **Filtros**: El dropdown de estilos se popula dinámicamente desde las sesiones cargadas. Cuando hay un filtro activo, aparece un botón "Limpiar filtros".

3. **Reserva**: Al hacer clic en "Reservar", se llama a `POST /api/v1/bookings`. Si responde 201, se muestra feedback verde con el estado (Confirmada/Lista de espera). Si 402 (sin créditos) o 400 (duplicada), se muestra feedback rojo.

4. **MyBookingsPage**: La tabla muestra datos enriquecidos (`session_title`, `instructor`, `style`, `starts_at`) que vienen del endpoint `/bookings/me`. El modal de cancelación usa un overlay con `bg-opacity-50` y centra el card. Durante la cancelación se deshabilitan los botones y se muestra un spinner.

5. **NavBar**: Se agregaron links "Agenda" y "Mis Reservas" tanto en la versión desktop (`hidden sm:flex`) como en el menú mobile hamburguesa.
