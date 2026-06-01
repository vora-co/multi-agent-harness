# Spec Técnica — Feature #12: Frontend React: panel de administración

## Contexto
El frontend base existe en `frontend/` con auth, SchedulePage y MyBookingsPage ya implementadas. El hook `useAuth` expone `user.role`.

## Archivos a crear / modificar

### 1. `frontend/src/api/admin.js`
```js
export async function adminGetSessions()                    // GET /api/v1/sessions
export async function adminCreateSession(data)              // POST /api/v1/sessions
export async function adminUpdateSession(id, data)          // PUT /api/v1/sessions/{id}
export async function adminDeleteSession(id)                // DELETE /api/v1/sessions/{id}
export async function adminGetUsers()                       // GET /api/v1/admin/users
export async function adminAddCredits(userId, amount)       // POST /api/v1/users/{id}/credits
export async function adminGetAttendees(sessionId)          // GET /api/v1/admin/sessions/{id}/attendees
```

### 2. `frontend/src/pages/AdminSessionsPage.jsx`
- Tabla CRUD de sesiones: título, instructor, estilo, fecha, capacidad, enrolled
- Botón "Nueva sesión" → modal con formulario (título, instructor, estilo, starts_at, duration_minutes, capacity)
- Botón "Editar" por fila → mismo modal precargado
- Botón "Eliminar" → confirmación inline, llama `adminDeleteSession()`
- Solo accesible con `role === 'admin'`

### 3. `frontend/src/pages/AdminUsersPage.jsx`
- Tabla de usuarios: nombre, email, rol, créditos
- Botón "Agregar créditos" por fila → modal con input de amount (1-100) y razón
- Llama `adminAddCredits(userId, amount)`
- Solo accesible con `role === 'admin'`

### 4. `frontend/src/pages/AdminAttendeesPage.jsx`
- Recibe `sessionId` por URL param (`/admin/sessions/:id/attendees`)
- Lista de asistentes confirmados: nombre, email, fecha de reserva
- Botón "Volver" → `/admin/sessions`

### 5. `frontend/src/App.jsx` — agregar rutas admin
```jsx
<Route path="/admin/sessions" element={<AdminRoute><AdminSessionsPage /></AdminRoute>} />
<Route path="/admin/sessions/:id/attendees" element={<AdminRoute><AdminAttendeesPage /></AdminRoute>} />
<Route path="/admin/users" element={<AdminRoute><AdminUsersPage /></AdminRoute>} />
```

### 6. `frontend/src/components/AdminRoute.jsx`
Wrapper que redirige a `/` si `user.role !== 'admin'`.

### 7. `frontend/src/components/NavBar.jsx` — link Admin condicional
Mostrar enlace "Admin" → `/admin/sessions` solo si `user?.role === 'admin'`.

## Convenciones
- Tailwind para estilos
- Modales: estado local con `useState`, sin librería externa
- Fetch via `client.js` existente

## Tests
No se requieren tests automáticos (e2e=false). El reviewer validará que los archivos existan y que el código sea correcto estructuralmente.
