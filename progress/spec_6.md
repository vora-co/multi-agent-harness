# Spec — Feature #6: Agregar endpoints a src/api.py para sesiones

## Archivos a crear o modificar

| Archivo | Acción |
|---------|--------|
| `src/sessions.py` | **MODIFICAR** — Quitar `require_admin` de `list_sessions` y `get_session`; ambos pasan a ser públicos. |
| `tests/test_sessions_api.py` | **MODIFICAR** — Actualizar tests de GET para que funcionen sin token (públicos). Agregar tests explícitos de acceso público. Los tests de mutación (POST/PUT/DELETE) se mantienen como admin-only. |

**Nota importante:** `src/api.py` **NO se modifica**. Ya incluye correctamente el router de sesiones (`from src.sessions import router as sessions_router` + `app.include_router(sessions_router)`). Los endpoints ya existen y funcionan; solo hay que cambiar la visibilidad de GET.

---

## Implementación

### `src/sessions.py` (MODIFICACIÓN — 2 funciones)

**Cambio 1: `list_sessions` — remover `require_admin`**

La función `list_sessions` (línea ~96) actualmente tiene:

```python
@router.get("", response_model=List[SessionResponse])
def list_sessions(
    style: Optional[str] = Query(None, description="Filter by session style"),
    date: Optional[str] = Query(None, description="Filter by start date (YYYY-MM-DD)"),
    _admin: User = Depends(require_admin),                              # ← ELIMINAR esta línea
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> List[Dict[str, Any]]:
    """List all sessions, optionally filtered by style and/or date. Admin only."""
```

Debe quedar:

```python
@router.get("", response_model=List[SessionResponse])
def list_sessions(
    style: Optional[str] = Query(None, description="Filter by session style"),
    date: Optional[str] = Query(None, description="Filter by start date (YYYY-MM-DD)"),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> List[Dict[str, Any]]:
    """List all sessions, optionally filtered by style and/or date. Public."""
```

- Se elimina el parámetro `_admin: User = Depends(require_admin)`.
- Se elimina la coma que queda al final de la línea anterior.
- El docstring cambia de `Admin only.` a `Public.`

---

**Cambio 2: `get_session` — remover `require_admin`**

La función `get_session` (línea ~128) actualmente tiene:

```python
@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: int,
    _admin: User = Depends(require_admin),                              # ← ELIMINAR esta línea
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> Dict[str, Any]:
    """Return a single session by its id. Admin only."""
```

Debe quedar:

```python
@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: int,
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> Dict[str, Any]:
    """Return a single session by its id. Public."""
```

- Se elimina el parámetro `_admin: User = Depends(require_admin)`.
- El docstring cambia de `Admin only.` a `Public.`

---

**Lo que NO se modifica en `src/sessions.py`:**

- Los imports se mantienen igual. `require_admin` y `User` siguen usándose en `create_session`, `update_session` y `delete_session`.
- `create_session`, `update_session` y `delete_session` permanecen **admin-only** exactamente como están.
- Los schemas `SessionCreate`, `SessionUpdate`, `SessionResponse` no cambian.
- El prefijo del router (`/api/v1/sessions`) no cambia.

---

### `tests/test_sessions_api.py` (MODIFICACIÓN)

El archivo ya existe con tests que cubren todos los casos requeridos. Sin embargo, los tests de **lectura** (GET) actualmente pasan token de admin. Hay que ajustarlos para reflejar que GET ahora es público.

#### Cambios requeridos en los tests existentes:

**Clase `TestAdminCrud`:**
- `test_admin_get_all_sessions`: quitar `headers={"Authorization": f"Bearer {admin_token}"}` de los GET. El listado debe funcionar sin autenticación.
- `test_admin_get_session_by_id`: quitar el header de auth del GET `/api/v1/sessions/{session_id}`.
- `test_get_nonexistent_session_returns_404`: quitar el header de auth del GET.
- Los tests de POST, PUT, DELETE en esta clase se mantienen con token admin como están.

**Clase `TestSessionFilters`:**
- TODOS los tests de esta clase (`test_filter_by_style`, `test_filter_by_date`, `test_filter_by_style_and_date`, `test_filter_by_style_no_results`, `test_filter_by_date_no_results`, `test_filter_invalid_date_returns_400`): quitar `headers={"Authorization": f"Bearer {admin_token}"}` de las llamadas GET. Los filtros son endpoint público.

**Clase `TestDeleteConflict`:**
- `test_delete_session_with_enrolled_participants_returns_409`: el DELETE mantiene su token admin (correcto). Sin cambios.

**Clase `TestClientForbidden`:**
- Los tests de mutación con cliente (`test_client_cannot_create_session`, `test_client_cannot_update_session`, `test_client_cannot_delete_session`) se mantienen igual.
- `test_unauthenticated_cannot_create_session` se mantiene igual.

#### Tests NUEVOS a agregar:

Agregar una clase `TestPublicAccess` con los siguientes casos:

```python
class TestPublicAccess:
    """Tests that GET endpoints are accessible without authentication."""

    def test_list_sessions_without_auth(self, client, admin_token, session_payload):
        """GET /api/v1/sessions without token returns 200 and session list."""
        # Crear una sesión como admin primero
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Leer sin token
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_get_session_by_id_without_auth(self, client, admin_token, session_payload):
        """GET /api/v1/sessions/{id} without token returns 200."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == session_id

    def test_filter_style_without_auth(self, client, admin_token, session_payload):
        """?style= filter works without authentication."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = client.get("/api/v1/sessions?style=Vinyasa")
        assert resp.status_code == 200
        data = resp.json()
        assert all(s["style"] == "Vinyasa" for s in data)

    def test_filter_date_without_auth(self, client, admin_token, session_payload):
        """?date= filter works without authentication."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = client.get("/api/v1/sessions?date=2025-06-15")
        assert resp.status_code == 200

    def test_client_can_list_sessions(self, client, admin_token, client_token, session_payload):
        """A client (non-admin) can still list sessions (public endpoint)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = client.get("/api/v1/sessions",
                          headers={"Authorization": f"Bearer {client_token}"})
        assert resp.status_code == 200

    def test_client_can_get_session_by_id(self, client, admin_token, client_token, session_payload):
        """A client can GET a single session by id."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]
        resp = client.get(f"/api/v1/sessions/{session_id}",
                          headers={"Authorization": f"Bearer {client_token}"})
        assert resp.status_code == 200
```

---

## Tests a escribir

### `tests/test_sessions_api.py`

El archivo ya existe. Los cambios son:

| Test | Acción |
|------|--------|
| `TestAdminCrud::test_admin_get_all_sessions` | **MODIFICAR**: quitar header de auth en los GET |
| `TestAdminCrud::test_admin_get_session_by_id` | **MODIFICAR**: quitar header de auth en el GET |
| `TestAdminCrud::test_get_nonexistent_session_returns_404` | **MODIFICAR**: quitar header de auth en el GET |
| `TestSessionFilters` (todos los tests) | **MODIFICAR**: quitar header de auth en todos los GET |
| `TestPublicAccess::test_list_sessions_without_auth` | **NUEVO** |
| `TestPublicAccess::test_get_session_by_id_without_auth` | **NUEVO** |
| `TestPublicAccess::test_filter_style_without_auth` | **NUEVO** |
| `TestPublicAccess::test_filter_date_without_auth` | **NUEVO** |
| `TestPublicAccess::test_client_can_list_sessions` | **NUEVO** |
| `TestPublicAccess::test_client_can_get_session_by_id` | **NUEVO** |

#### Casos cubiertos (todos):

| Caso | Cobertura |
|------|-----------|
| Admin crea sesión → 201 | `TestAdminCrud::test_admin_create_session` (existente, sin cambios) |
| Admin lista todas → 200 | `TestAdminCrud::test_admin_get_all_sessions` (modificado: sin auth) |
| Admin obtiene por id → 200 | `TestAdminCrud::test_admin_get_session_by_id` (modificado: sin auth) |
| Admin actualiza → 200 | `TestAdminCrud::test_admin_update_session` (existente, sin cambios) |
| Admin borra con enrolled=0 → 204 | `TestAdminCrud::test_admin_delete_session_with_no_enrolled` (existente) |
| GET sin auth → 200 (público) | `TestPublicAccess` (nuevo) |
| Cliente crea sesión → 403 | `TestClientForbidden::test_client_cannot_create_session` (existente) |
| Cliente actualiza → 403 | `TestClientForbidden::test_client_cannot_update_session` (existente) |
| Cliente borra → 403 | `TestClientForbidden::test_client_cannot_delete_session` (existente) |
| No autenticado crea → 401 | `TestClientForbidden::test_unauthenticated_cannot_create_session` (existente) |
| Filtro por style → 200 | `TestSessionFilters::test_filter_by_style` (modificado: sin auth) |
| Filtro por date → 200 | `TestSessionFilters::test_filter_by_date` (modificado: sin auth) |
| Filtro por style+date → 200 | `TestSessionFilters::test_filter_by_style_and_date` (modificado: sin auth) |
| Filtro style sin resultados → [] | `TestSessionFilters::test_filter_by_style_no_results` (modificado: sin auth) |
| Filtro date sin resultados → [] | `TestSessionFilters::test_filter_by_date_no_results` (modificado: sin auth) |
| Filtro date inválido → 400 | `TestSessionFilters::test_filter_invalid_date_returns_400` (modificado: sin auth) |
| DELETE con enrolled > 0 → 409 | `TestDeleteConflict::test_delete_session_with_enrolled_participants_returns_409` (existente) |
| GET sesión inexistente → 404 | `TestAdminCrud::test_get_nonexistent_session_returns_404` (modificado: sin auth) |
| PUT sesión inexistente → 404 | `TestAdminCrud::test_update_nonexistent_session_returns_404` (existente) |
| DELETE sesión inexistente → 404 | `TestAdminCrud::test_delete_nonexistent_session_returns_404` (existente) |

---

## Dependencias

No se requieren nuevas librerías. Todo lo necesario ya está instalado y en uso por el código existente.

---

## Notas de implementación

1. **Cambio mínimo:** Solo se tocan 2 líneas de código productivo (los parámetros `_admin` en `list_sessions` y `get_session`). El resto son ajustes de tests. La arquitectura del router, los schemas, y los endpoints de mutación no cambian.

2. **El router ya está incluido en `api.py`**: `from src.sessions import router as sessions_router` y `app.include_router(sessions_router)` ya existen. No hay que tocar `api.py`.

3. **Los endpoints de mutación siguen siendo admin-only**: `create_session`, `update_session` y `delete_session` conservan su dependencia `require_admin` exactamente como están ahora.

4. **Códigos HTTP relevantes:**
   - `200` — GET exitoso (listar, obtener por id, actualizar)
   - `201` — POST exitoso (creación)
   - `204` — DELETE exitoso (sin body)
   - `400` — Fecha de filtro inválida (`?date=mal-formato`)
   - `401` — Sin token / token inválido (solo en POST/PUT/DELETE)
   - `403` — Token válido pero rol no es admin (solo en POST/PUT/DELETE)
   - `404` — Sesión no encontrada
   - `409` — DELETE con enrolled > 0

5. **Verificación**: Ejecutar tras la implementación:
   ```bash
   python3 -m pytest tests/test_sessions_api.py -v
   ```
   Todos los tests deben pasar.
