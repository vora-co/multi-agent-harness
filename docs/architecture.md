# Arquitectura del proyecto

## Descripción general
App web de reservas de sesiones de yoga y bienestar.
Stack: FastAPI (backend) + React + Tailwind CSS (frontend) + JSON (persistencia).

## Estructura de carpetas

```
src/
  models/         # Clases de dominio puras (sin I/O)
    user.py       # User
    session.py    # Session
    booking.py    # Booking
    credit_transaction.py
  repositories/   # Acceso a datos (usan storage.py)
    users.py
    sessions.py
    bookings.py
  storage.py      # load(entity) / save(entity, records) — JSON atómico
  auth.py         # JWT + bcrypt
  api.py          # Rutas FastAPI
  main.py         # Entrypoint uvicorn
  static/         # Frontend compilado (index.html + assets)

frontend/         # Proyecto Vite + React + Tailwind
  src/
    pages/
    components/
    hooks/
    api/          # Funciones fetch hacia el backend

tests/            # pytest — unit tests
tests/e2e/        # pytest-playwright — tests de interfaz
data/             # Archivos JSON de persistencia (gitignored)
docs/             # Este directorio
progress/         # Reportes de agentes
```

## Capas y dependencias

```
API (api.py)
  └── repositories/  ← única capa que llama a storage
        └── storage.py  ← lee/escribe data/*.json
  └── models/        ← nunca hacen I/O, solo lógica de dominio
  └── auth.py        ← valida JWT, usa repositories/users
```

## Persistencia
Cada entidad tiene su propio archivo JSON en `data/`:
- `data/users.json`
- `data/sessions.json`
- `data/bookings.json`
- `data/credit_transactions.json`

La escritura es atómica: se escribe en un `.tmp` y luego se hace `os.replace()`.

## Autenticación
JWT stateless con python-jose. Token expira en 24h.
Payload: `{user_id, role, exp}`.
Roles: `client` | `admin`.
Passwords: hash bcrypt via passlib.

## Frontend
Vite + React 18 + Tailwind CSS. Diseño mobile-first.
Proxy en `vite.config.js` apunta a `http://localhost:8000`.
El build compilado se sirve como archivos estáticos desde FastAPI (`/`).
