# Feature #5: Implementación de Autenticación JWT (src/auth.py)

## Estado: ✅ Todos los tests pasan (8/8)

## Archivos creados/modificados

| Archivo | Acción |
|---|---|
| `src/auth.py` | Ya existía — JWT stateless con python-jose + bcrypt |
| `src/api.py` | Ya tenía código auth — Endpoints `/api/v1/auth/register`, `/api/v1/auth/login`, `/api/v1/auth/me` |
| `tests/test_auth.py` | Ya existía — 8 tests de autenticación |

La feature #5 ya estaba completamente implementada antes de esta tarea. Se verificó que el código cumple con todos los requisitos de la especificación y que todos los tests pasan.

## Decisiones de diseño

### bcrypt directo (sin passlib)
Se usa `bcrypt.hashpw`/`bcrypt.checkpw` directamente, sin la capa de abstracción de `passlib`. Esto simplifica el código, evita problemas de compatibilidad (`passlib` no tiene mantenimiento desde 2020), y cumple con el requisito de "guarda password como hash bcrypt".

### `auto_error=False` en OAuth2PasswordBearer
Permite que `get_current_user` emita manualmente un 401 con el mensaje personalizado `"Not authenticated"` y el header `WWW-Authenticate: Bearer`, en lugar del mensaje estándar de FastAPI.

### Mensaje de error ambiguo en login
Tanto email inexistente como password incorrecto retornan `"Invalid email or password"` para prevenir enumeración de usuarios registrados.

### Password nunca expuesto
`UserResponse` (Pydantic) no incluye `password_hash`. Aunque `current_user.to_dict()` sí lo incluye, Pydantic ignora campos no declarados al serializar la respuesta.

### Aislamiento en tests con monkeypatch
El fixture `client` parchea `UserRepository.__init__` para que todas las instancias (tanto en `api.py` como en `auth.py`) usen `tmp_path`, garantizando aislamiento completo del sistema de archivos real.

## Endpoints implementados

| Endpoint | Autenticación | Función |
|---|---|---|
| `POST /api/v1/auth/register` | Público | Registro con name, email, password, role |
| `POST /api/v1/auth/login` | Público | Login con email + password, retorna JWT |
| `GET /api/v1/auth/me` | `get_current_user` | Perfil del usuario autenticado |

## Dependencias FastAPI

- `get_current_user(token)` — Extrae JWT del header Authorization, decodifica, busca usuario en BD, retorna User o 401
- `require_admin(user)` — Verifica que `user.role == "admin"`, retorna User o 403

## Payload JWT

```json
{"user_id": <int>, "role": "<client|admin>", "exp": <ISO8601 +24h>}
```

## Output completo de pytest

```
============================= test sessions starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/fmejiavi/Documents/agentes-harness-prueba/tests
configfile: pytest.ini

tests/test_auth.py::TestAuthRegister::test_register_success PASSED       [ 12%]
tests/test_auth.py::TestAuthRegister::test_register_admin PASSED         [ 25%]
tests/test_auth.py::TestAuthLogin::test_login_success PASSED             [ 37%]
tests/test_auth.py::TestAuthLogin::test_login_wrong_password_returns_401 PASSED [ 50%]
tests/test_auth.py::TestAuthLogin::test_login_nonexistent_email_returns_401 PASSED [ 62%]
tests/test_auth.py::TestTokenValidation::test_invalid_token_returns_401 PASSED [ 75%]
tests/test_auth.py::TestTokenValidation::test_missing_token_returns_401 PASSED [ 87%]
tests/test_auth.py::TestTokenValidation::test_valid_token_returns_user PASSED [100%]

============================== 8 passed in 2.07s ===============================
```

## Verificación de requisitos de la feature

| Requisito | Estado |
|---|---|
| `src/auth.py` con JWT stateless (python-jose) | ✅ |
| `POST /api/v1/auth/register` — name, email, password, role | ✅ |
| Password como hash bcrypt | ✅ |
| `POST /api/v1/auth/login` — email, password → JWT | ✅ |
| Payload JWT: `{user_id, role, exp: 24h}` | ✅ |
| `get_current_user(token)` — dependencia FastAPI | ✅ |
| `require_admin(user)` — 403 si `role != 'admin'` | ✅ |
| `python-jose` + `bcrypt` en dependencias | ✅ |
| `OAuth2PasswordBearer` con `auto_error=False` | ✅ |
| Test: register exitoso | ✅ |
| Test: login exitoso | ✅ |
| Test: password incorrecto → 401 | ✅ |
| Test: token inválido → 401 | ✅ |
| Test: token ausente → 401 | ✅ |
| Test: token válido retorna usuario | ✅ |
