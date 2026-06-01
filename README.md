# App de Reservas de Yoga — Multi-Agent Harness

Aplicación web de reservas de clases de yoga construida automáticamente por un sistema multi-agente sobre DeepSeek API.

## Arquitectura general

```
Frontend  (React + Vite + Tailwind — :5173)
    ↓  proxy /api → :8000
Backend   (FastAPI — :8000)
    ↓
Storage   (JSON files en data/)
```

**Stack:**
- Backend: Python 3.10+, FastAPI, JWT (python-jose), bcrypt
- Frontend: React 18, Vite, Tailwind CSS
- Storage: archivos JSON (sin base de datos)
- Tests unitarios: pytest + httpx TestClient
- Tests E2E: Playwright

---

## Instalación

### 1. Clonar y configurar entorno

```bash
git clone https://github.com/fmejiavi/agentes-harness
cd agentes-harness-prueba
```

Crea un archivo `.env` en la raíz:

```env
DEEPSEEK_API_KEY=tu_api_key_aqui
```

### 2. Instalar dependencias backend

```bash
bash init.sh
```

Esto instala todas las dependencias de `requirements.txt`, verifica la estructura del proyecto y corre los tests existentes.

### 3. Instalar dependencias frontend

```bash
cd frontend
npm install
cd ..
```

---

## Ejecutar la aplicación

### Backend

```bash
python3 -m uvicorn src.api:app --reload --port 8000
```

- API disponible en: `http://localhost:8000`
- Documentación Swagger: `http://localhost:8000/docs`
- Documentación Redoc: `http://localhost:8000/redoc`

### Frontend

En una segunda terminal:

```bash
cd frontend
npm run dev
```

- App disponible en: `http://localhost:5173`
- El proxy de Vite redirige automáticamente `/api/*` → `http://localhost:8000`

---

## Tests

### Tests unitarios (backend)

```bash
# Todos los tests
python3 -m pytest tests/ -v

# Solo un módulo
python3 -m pytest tests/test_credits.py -v
python3 -m pytest tests/test_auth.py -v
python3 -m pytest tests/test_bookings.py -v
```

### Tests E2E con Playwright

> **Requisito:** el backend Y el frontend deben estar corriendo antes de ejecutar los tests E2E.

**Paso 1 — Levanta el backend (terminal 1):**
```bash
python3 -m uvicorn src.api:app --reload --port 8000
```

**Paso 2 — Levanta el frontend (terminal 2):**
```bash
cd frontend && npm run dev
```

**Paso 3 — Ejecuta los tests E2E (terminal 3):**
```bash
bash run_e2e.sh
```

O directamente con pytest:
```bash
python3 -m pytest tests/e2e/ -v --headed   # con navegador visible
python3 -m pytest tests/e2e/ -v            # headless
```

> Los tests E2E **no corren automáticamente** en el harness de agentes. Se ejecutan manualmente después de que el frontend esté construido y verificado.

---

## Harness multi-agente

El harness construye la aplicación automáticamente feature por feature.

### Ejecutar el harness

```bash
python3 harness.py
```

### Comandos disponibles en el REPL

| Comando | Descripción |
|---|---|
| `continúa con las features pendientes` | Procesa todas las features en orden |
| `Ejecuta solo la feature 10 y detente` | Procesa únicamente esa feature |
| `/features` | Muestra el estado de todas las features |
| `/costos` | Muestra el costo de tokens de la sesión |
| `/estado` | Muestra el estado actual (progress/current.md) |
| `/salir` | Sale del harness |

### Estado de features

| # | Feature | Estado |
|---|---|---|
| 1 | Modelo User | ✅ done |
| 2 | Modelo Session | ✅ done |
| 3 | Modelo Booking | ✅ done |
| 4 | Storage + Repositories | ✅ done |
| 5 | Auth JWT | ✅ done |
| 6 | API sesiones (admin) | ✅ done |
| 7 | API reservas (cliente) | ✅ done |
| 8 | Promoción waitlist | ✅ done |
| 9 | Créditos y panel admin | ✅ done |
| 10 | Frontend base + auth | ⏳ pending |
| 11 | Frontend agenda + reservas | ⏳ pending |
| 12 | Frontend panel admin | ⏳ pending |

### Retomar tras un crash

El harness tiene checkpointing automático. Features en `in_progress` se resetean a `pending` al arrancar. Si una feature quedó en `failed` y quieres reintentarla, edita `feature_list.json` y cambia su `status` a `pending`.

---

## Estructura del proyecto

```
agentes-harness-prueba/
├── harness.py              # Motor principal del harness
├── tools.py                # Herramientas disponibles para los agentes
├── feature_list.json       # Definición y estado de las features
├── requirements.txt
├── init.sh                 # Setup inicial
├── run_e2e.sh              # Ejecutar tests E2E (requiere servidores activos)
├── agents/
│   ├── leader.py           # Coordina features
│   ├── spec_writer.py      # Genera especificaciones técnicas
│   ├── implementer.py      # Escribe código y tests
│   ├── reviewer.py         # Valida implementaciones
│   └── e2e_tester.py       # Tests Playwright (solo features con e2e=true)
├── src/
│   ├── api.py              # Endpoints FastAPI
│   ├── auth.py             # JWT + bcrypt
│   ├── core.py             # Lógica de negocio (waitlist, notificaciones)
│   ├── storage.py          # Lectura/escritura JSON atómica
│   ├── models/             # User, Session, Booking, CreditTransaction
│   └── repositories/       # UserRepo, SessionRepo, BookingRepo, etc.
├── frontend/               # React + Vite + Tailwind (generado por harness)
├── tests/                  # Tests unitarios pytest
│   └── e2e/                # Tests Playwright por feature
├── progress/               # Reportes del harness por feature
└── data/                   # Archivos JSON de datos (generados en runtime)
```

---

## Variables de entorno

| Variable | Descripción |
|---|---|
| `DEEPSEEK_API_KEY` | API key de DeepSeek (requerida para el harness) |

---

## Endpoints principales

### Auth
| Método | Ruta | Acceso |
|---|---|---|
| POST | `/api/v1/auth/register` | Público |
| POST | `/api/v1/auth/login` | Público |
| GET | `/api/v1/auth/me` | Autenticado |

### Sesiones
| Método | Ruta | Acceso |
|---|---|---|
| GET | `/api/v1/sessions` | Público (`?style=` `?date=`) |
| GET | `/api/v1/sessions/{id}` | Público |
| POST | `/api/v1/sessions` | Admin |
| PUT | `/api/v1/sessions/{id}` | Admin |
| DELETE | `/api/v1/sessions/{id}` | Admin |

### Reservas
| Método | Ruta | Acceso |
|---|---|---|
| POST | `/api/v1/bookings` | Autenticado |
| GET | `/api/v1/bookings/me` | Autenticado |
| DELETE | `/api/v1/bookings/{id}` | Autenticado (solo owner) |

### Admin / Créditos
| Método | Ruta | Acceso |
|---|---|---|
| POST | `/api/v1/users/{id}/credits` | Admin |
| GET | `/api/v1/users/{id}/credits/history` | Admin o propio usuario |
| GET | `/api/v1/admin/users` | Admin |
| GET | `/api/v1/admin/sessions/{id}/attendees` | Admin |
