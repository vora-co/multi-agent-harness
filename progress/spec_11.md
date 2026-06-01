# Spec Técnica — Feature #11: Frontend React: agenda pública y reservas (cliente)

## Contexto
El frontend base ya existe en `frontend/` con React + Vite + Tailwind. Hay rutas configuradas en `frontend/src/App.jsx`, hook `useAuth` en `frontend/src/hooks/useAuth.jsx`, y cliente HTTP en `frontend/src/api/client.js`.

## Archivos a crear / modificar

### 1. `frontend/src/api/sessions.js`
Funciones para llamar al backend:
```js
export async function getSessions(filters = {}) // GET /api/v1/sessions?style=&date=
export async function getSession(id)             // GET /api/v1/sessions/{id}
```

### 2. `frontend/src/api/bookings.js`
```js
export async function getMyBookings()            // GET /api/v1/bookings/me
export async function createBooking(sessionId)   // POST /api/v1/bookings {session_id}
export async function cancelBooking(bookingId)   // DELETE /api/v1/bookings/{id}
```

### 3. `frontend/src/pages/SchedulePage.jsx`
- Grilla responsive de sesiones (cards con título, instructor, estilo, fecha, cupos)
- Badge "Lista de espera" si `enrolled >= capacity`
- Botón "Reservar" — llama `createBooking()`, muestra feedback
- Filtros por estilo (select) y fecha (input date)
- Requiere auth (`PrivateRoute`)

### 4. `frontend/src/pages/MyBookingsPage.jsx`
- Tabla de reservas del usuario autenticado
- Columnas: sesión, fecha, estado (confirmed/waitlist/cancelled)
- Botón "Cancelar" solo para confirmed/waitlist — abre modal de confirmación
- Al confirmar cancela con `cancelBooking()` y refresca la lista
- Requiere auth

### 5. `frontend/src/App.jsx` — agregar rutas
```jsx
<Route path="/schedule" element={<PrivateRoute><SchedulePage /></PrivateRoute>} />
<Route path="/my-bookings" element={<PrivateRoute><MyBookingsPage /></PrivateRoute>} />
```

### 6. `frontend/src/components/NavBar.jsx` — agregar links
Agregar "Agenda" → `/schedule` y "Mis Reservas" → `/my-bookings` al nav.

## Convenciones
- Tailwind para estilos — sin CSS custom salvo `index.css` existente
- Fetch via `client.js` existente (ya maneja el token JWT en headers)
- Estados: loading, error, datos — usar `useState` + `useEffect`
- Modal de confirmación: estado local con `useState`, sin librería externa

## Tests
No se requieren tests automáticos (e2e=false). El reviewer validará que los archivos existan y que el código sea correcto estructuralmente.
