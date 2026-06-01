# Spec — Feature #7: Agregar endpoints de bookings (cliente autenticado)

## Archivos a crear o modificar

| Archivo | Acción |
|---------|--------|
| `src/api.py` | **MODIFICAR** — Agregar schemas Pydantic (`BookingCreate`, `BookingResponse`, `SessionDetail`) y 3 endpoints de booking. Agregar dependencias de repositorios `_get_booking_repo`, `_get_session_repo`, `_get_user_repo`. |
| `tests/test_bookings.py` | **CREAR desde cero** — Tests unitarios con `TestClient` y monkeypatching de repos a `tmp_path`. |

**Archivos que NO se modifican** (ya existen con el comportamiento necesario):

- `src/models/booking.py` — Modelo `Booking` con estados `CONFIRMED`, `CANCELLED`, `WAITLIST`, y métodos `to_dict()` / `from_dict()`.
- `src/models/session.py` — Modelo `Session` con `capacity`, `enrolled`, `is_full()`, `spots_available()`.
- `src/models/user.py` — Modelo `User` con campo `credits`.
- `src/repositories/bookings.py` — `BookingRepository` con `find_all()`, `find_by_id()`, `find_by_user()`, `find_by_session()`, `save_one()`, `delete()`.
- `src/repositories/sessions.py` — `SessionRepository` con `find_by_id()`, `save_one()`.
- `src/repositories/users.py` — `UserRepository` con `find_by_id()`, `find_by_email()`, `save_one()`.
- `src/auth.py` — `get_current_user` (FastAPI dependency que retorna `User` o 401).
- `src/storage.py` — `load()` / `save()` atómicos.

---

## Implementación

### `src/api.py` (MODIFICACIÓN — agregar schemas, dependencias de repo, y 3 endpoints)

#### 1. Nuevos schemas Pydantic (agregar junto a los existentes, antes o después de los schemas ya definidos)

```python
class BookingCreate(BaseModel):
    """Schema for creating a new booking (POST /bookings)."""
    session_id: int


class SessionDetail(BaseModel):
    """Nested session info inside a booking response."""
    id: int
    title: str
    instructor: str
    style: str
    starts_at: str
    duration_minutes: int
    capacity: int
    enrolled: int


class BookingResponse(BaseModel):
    """Booking response including nested session info."""
    id: int
    user_id: int
    session_id: int
    status: str
    created_at: str
    session: Optional[SessionDetail] = None
```

#### 2. Nuevas dependencias de repositorio

Agregar funciones `_get_booking_repo`, `_get_session_repo`, `_get_user_repo` si no existen ya. Deben retornar instancias de `BookingRepository`, `SessionRepository` y `UserRepository` respectivamente.

```python
def _get_booking_repo() -> BookingRepository:
    """Dependency that provides a BookingRepository instance."""
    return BookingRepository()


def _get_session_repo() -> SessionRepository:
    """Dependency that provides a SessionRepository instance."""
    return SessionRepository()


def _get_user_repo() -> UserRepository:
    """Dependency that provides a UserRepository instance."""
    return UserRepository()
```

> **Nota**: si alguna de estas funciones helper ya existe en `src/api.py`, reutilizarla. No duplicar.

#### 3. Endpoint: `POST /api/v1/bookings` (crear reserva)

```python
@app.post("/api/v1/bookings", response_model=BookingResponse, status_code=201)
def create_booking(
    payload: BookingCreate,
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> Dict[str, Any]:
    """Create a booking for the authenticated user.

    Business rules:
    1. Session must exist → 404 "Session not found"
    2. No duplicate active booking for same session/user → 400
    3. If spots available (enrolled < capacity):
       a. User must have credits > 0 → 402 "Insufficient credits"
       b. Status = 'confirmed', deduct 1 credit, increment enrolled
    4. If session full (enrolled >= capacity):
       a. Status = 'waitlist', no credit deduction, no enrolled change
    5. Response includes nested session object with updated enrolled count.
    """
```

**Comportamiento paso a paso:**

1. **Buscar sesión**: `session = session_repo.find_by_id(payload.session_id)`. Si `None` → `raise HTTPException(404, detail="Session not found")`.
2. **Verificar duplicado activo**: `existing = booking_repo.find_by_user(current_user.id)`. Filtrar aquellos donde `b.session_id == payload.session_id and b.status != Booking.CANCELLED`. Si existe alguno → `raise HTTPException(400, detail="User already has an active booking for this session")`.
3. **Calcular próximo ID**: `all_bookings = booking_repo.find_all()`; `next_id = max((b.id for b in all_bookings), default=0) + 1`.
4. **Determinar status y ajustar estado**:
   - Si `session.enrolled < session.capacity` (hay cupo):
     - Si `current_user.credits <= 0` → `raise HTTPException(402, detail="Insufficient credits to confirm booking")`.
     - `booking_status = Booking.CONFIRMED`
     - `current_user.credits -= 1` → `user_repo.save_one(current_user)`
     - `session.enrolled += 1` → `session_repo.save_one(session)`
   - Sino (sesión llena):
     - `booking_status = Booking.WAITLIST`
     - (No se descuentan créditos, no se modifica enrolled)
5. **Crear y persistir booking**:
   ```python
   booking = Booking(
       id=next_id,
       user_id=current_user.id,
       session_id=payload.session_id,
       status=booking_status,
   )
   booking_repo.save_one(booking)
   ```
6. **Construir respuesta**:
   ```python
   result = booking.to_dict()
   result["session"] = session.to_dict()
   return result
   ```

**Excepciones:**
| Código | Condición | `detail` |
|--------|-----------|----------|
| `404` | `session_repo.find_by_id(...)` retorna `None` | `"Session not found"` |
| `400` | Ya existe booking activo (misma sesión, mismo usuario, status ≠ cancelled) | `"User already has an active booking for this session"` |
| `402` | Hay cupo pero `current_user.credits <= 0` | `"Insufficient credits to confirm booking"` |
| `401` | No autenticado (heredado de `get_current_user`) | (generado por `get_current_user`) |

---

#### 4. Endpoint: `GET /api/v1/bookings/me` (listar mis reservas)

```python
@app.get("/api/v1/bookings/me", response_model=List[BookingResponse])
def list_my_bookings(
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> List[Dict[str, Any]]:
    """List all bookings of the authenticated user.

    Each booking includes the full session object nested under 'session'.
    If a session no longer exists, 'session' is null.
    """
```

**Comportamiento paso a paso:**

1. `bookings = booking_repo.find_by_user(current_user.id)`.
2. Para cada `b` en `bookings`:
   - `b_dict = b.to_dict()`
   - `session = session_repo.find_by_id(b.session_id)`
   - `b_dict["session"] = session.to_dict() if session else None`
   - Agregar `b_dict` a `result`.
3. Retornar `result`.

**Excepciones:**
| Código | Condición |
|--------|-----------|
| `401` | No autenticado (heredado de `get_current_user`) |

---

#### 5. Endpoint: `DELETE /api/v1/bookings/{booking_id}` (cancelar reserva)

```python
@app.delete("/api/v1/bookings/{booking_id}", status_code=204)
def cancel_booking(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> None:
    """Cancel a booking. Only the owner can cancel.

    - If 'confirmed': return 1 credit, decrement enrolled, then try to promote from waitlist.
    - If 'waitlist': only change status to cancelled (no credit/enrolled change).
    - If already cancelled: no-op (still returns 204).
    - If booking belongs to another user: 403.
    """
```

**Comportamiento paso a paso:**

1. `booking = booking_repo.find_by_id(booking_id)`. Si `None` → `raise HTTPException(404, detail="Booking not found")`.
2. Si `booking.user_id != current_user.id` → `raise HTTPException(403, detail="Cannot cancel another user's booking")`.
3. Si `booking.status == Booking.CANCELLED` → `return` (no-op, 204 implícito).
4. Guardar `was_confirmed = (booking.status == Booking.CONFIRMED)`.
5. Si `was_confirmed`:
   - `current_user.credits += 1` → `user_repo.save_one(current_user)`
   - `session = session_repo.find_by_id(booking.session_id)`
   - Si `session is not None and session.enrolled > 0`:
     - `session.enrolled -= 1` → `session_repo.save_one(session)`
6. Marcar booking: `booking.status = Booking.CANCELLED` → `booking_repo.save_one(booking)`.
7. Si `was_confirmed`: intentar `promote_from_waitlist(booking.session_id, booking_repo, session_repo, user_repo)` (si existe en `src/core.py`; si no, omitir o capturar `ImportError` silenciosamente).

**Excepciones:**
| Código | Condición | `detail` |
|--------|-----------|----------|
| `404` | `booking_repo.find_by_id(...)` retorna `None` | `"Booking not found"` |
| `403` | `booking.user_id != current_user.id` | `"Cannot cancel another user's booking"` |
| `401` | No autenticado (heredado de `get_current_user`) | (generado por `get_current_user`) |

---

## Tests a escribir

### `tests/test_bookings.py`

El archivo usa el patrón existente en el proyecto: `TestClient` + `monkeypatch` para redirigir los repositorios a `tmp_path`. Se requieren las fixtures `client`, `admin_token`, `client_token`, y los helpers `_register_admin`, `_register_client`, `_create_session`, `_add_credits`.

#### Fixtures y helpers requeridos

```python
import pytest
from fastapi.testclient import TestClient
from src.api import app
from src.repositories.users import UserRepository
from src.repositories.sessions import SessionRepository
from src.repositories.bookings import BookingRepository
from src.models.user import User
from src.models.session import Session


@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with repos redirected to tmp_path."""
    # Monkeypatch UserRepository, SessionRepository, BookingRepository __init__
    # para usar str(tmp_path) como base directory

@pytest.fixture
def admin_token(client):
    """Registers admin@example.com / admin123 and returns access_token."""

@pytest.fixture
def client_token(client):
    """Registers client@example.com / client123 and returns access_token."""

@pytest.fixture
def session_data(client, admin_token):
    """Creates a session (Morning Yoga, capacity=20) via admin and returns it."""


def _register_admin(client: TestClient) -> tuple[str, str]:
    """Register admin user and return (token, email)."""

def _register_client(client: TestClient,
                     email="client@example.com",
                     password="client123") -> tuple[str, str]:
    """Register client user and return (token, email)."""

def _create_session(client, admin_token, title="Morning Yoga",
                     starts_at="2025-06-15T09:00:00",
                     capacity=20) -> dict:
    """Create a session via admin and return its data dict."""

def _add_credits(client, email, tmp_path, amount=5) -> None:
    """Directly modify user credits in the JSON storage."""
```

---

#### Clase `TestCreateBookingSuccess` — Happy path

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 1 | `test_create_booking_confirmed` | Cliente registrado con 5 créditos; sesión con capacity=20, enrolled=0 | `POST /api/v1/bookings {"session_id": <id>}` con `Authorization: Bearer <client_token>` | **status 201**; `data["status"] == "confirmed"`; `data["session"]["enrolled"] == 1`; `data["session"]["id"] == session_data["id"]`; créditos del usuario ahora son 4 (verificar con `GET /auth/me`) |

---

#### Clase `TestCreateBookingNoCredits` — Sin créditos → 402

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 2 | `test_create_booking_no_credits_returns_402` | Cliente con 0 créditos; sesión con cupo disponible | `POST /api/v1/bookings {"session_id": <id>}` con token cliente | **status 402**; `"credits"` aparece en `detail` (case-insensitive) |

---

#### Clase `TestCreateBookingWaitlist` — Sesión llena → waitlist

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 3 | `test_create_booking_waitlist_when_session_full` | Sesión capacity=1; usuario A (con 5 créditos) crea booking → confirmed (ocupa cupo). Usuario B tiene 5 créditos. | `POST /api/v1/bookings {"session_id": <id>}` con token de B | **status 201**; `data["status"] == "waitlist"`; créditos de B siguen siendo 5 (verificar `GET /auth/me`) |

---

#### Clase `TestCreateBookingEdgeCases` — Casos borde

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 4 | `test_create_booking_nonexistent_session_returns_404` | Ninguna | `POST /api/v1/bookings {"session_id": 99999}` con token cliente | **status 404**; `"Session not found"` en detail |
| 5 | `test_create_duplicate_active_booking_returns_400` | Cliente tiene booking confirmed en sesión X | Segundo `POST /api/v1/bookings {"session_id": <X>}` mismo cliente | **status 400**; `"already has an active booking"` en detail |
| 6 | `test_create_booking_unauthenticated_returns_401` | Sesión existente | `POST /api/v1/bookings {"session_id": <id>}` **sin** token | **status 401** |

---

#### Clase `TestListMyBookings` — GET /bookings/me

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 7 | `test_list_my_bookings` | Cliente con 5 créditos; 1 booking confirmed creado en sesión "Morning Yoga" | `GET /api/v1/bookings/me` con token cliente | **status 200**; `isinstance(data, list)`; `len(data) == 1`; `data[0]["status"] == "confirmed"`; `data[0]["session_id"]` coincide; `data[0]["session"] is not None`; `data[0]["session"]["title"] == "Morning Yoga"` |
| 8 | `test_list_my_bookings_empty` | Cliente sin bookings | `GET /api/v1/bookings/me` con token cliente | **status 200**; `resp.json() == []` |
| 9 | `test_list_my_bookings_unauthenticated_returns_401` | Ninguna | `GET /api/v1/bookings/me` **sin** token | **status 401** |

---

#### Clase `TestCancelBooking` — DELETE /bookings/{id}

| # | Nombre del test | Precondición | Acción | Assertion |
|---|----------------|--------------|--------|-----------|
| 10 | `test_cancel_own_confirmed_booking` | Cliente con 3 créditos; booking confirmed creado (créditos bajan a 2 tras crear) | `DELETE /api/v1/bookings/{booking_id}` con token cliente | **status 204**; créditos restaurados a 3 (`GET /auth/me`); `session.enrolled` vuelve a 0; booking status == `"cancelled"` en `GET /bookings/me` |
| 11 | `test_cancel_own_waitlist_booking` | Sesión capacity=1; usuario A ocupa cupo (confirmed); usuario B en waitlist con 5 créditos | `DELETE /api/v1/bookings/{booking_id_de_B}` con token B | **status 204**; créditos de B siguen siendo 5 |
| 12 | `test_cancel_another_users_booking_returns_403` | Usuario A tiene booking confirmed en sesión X | Usuario B (distinto cliente) hace `DELETE /api/v1/bookings/{booking_id_de_A}` con token B | **status 403**; `"another user"` en detail (case-insensitive) |
| 13 | `test_cancel_nonexistent_booking_returns_404` | Ninguna | `DELETE /api/v1/bookings/99999` con token cliente | **status 404** |
| 14 | `test_cancel_booking_unauthenticated_returns_401` | Ninguna | `DELETE /api/v1/bookings/1` **sin** token | **status 401** |

---

## Dependencias

No se requieren nuevas librerías. Todo lo necesario ya está disponible en el proyecto:

- `fastapi`, `pydantic` — endpoints y schemas
- `pytest`, `httpx` — `TestClient` para tests unitarios
- `python-jose`, `passlib[bcrypt]` — autenticación (ya implementada)

---

## Notas de implementación

1. **Los endpoints de booking van en `src/api.py`** — no en `src/sessions.py`. Operan sobre el recurso `/api/v1/bookings`, distinto de `/api/v1/sessions`.

2. **Código HTTP 402**: FastAPI no incluye `HTTP_402_PAYMENT_REQUIRED` en `fastapi.status`. Usar explícitamente `status_code=402` en la llamada a `HTTPException`.

3. **DELETE es soft-delete**: El booking no se elimina del storage. Solo se marca `status = "cancelled"`. Esto preserva el historial.

4. **DELETE retorna 204 con body vacío**: No usar `response_model` en este endpoint.

5. **POST incluye `session` anidada**: La respuesta debe contener el objeto session con `enrolled` actualizado (si fue confirmed).

6. **Promoción desde waitlist**: Al cancelar un confirmed, se llama a `promote_from_waitlist` de `src/core` (si existe). Esto es parte de la feature #9. Si `promote_from_waitlist` no existe al implementar #7, envolver la llamada en `try/except ImportError` y omitir silenciosamente. Los tests de #7 no verifican el comportamiento de promoción.

7. **Orden de implementación sugerido**:
   - Agregar schemas Pydantic (`BookingCreate`, `SessionDetail`, `BookingResponse`)
   - Agregar dependencias de repositorio (`_get_booking_repo`, `_get_session_repo`, `_get_user_repo`) si no existen
   - `POST /api/v1/bookings`
   - `GET /api/v1/bookings/me`
   - `DELETE /api/v1/bookings/{booking_id}`
   - Escribir y ejecutar tests

8. **Verificación**:
   ```bash
   python3 -m pytest tests/test_bookings.py -v
   ```
   Los 14 tests deben pasar. Luego ejecutar la suite completa para asegurar cero regresiones:
   ```bash
   python3 -m pytest tests/ -v
   ```
