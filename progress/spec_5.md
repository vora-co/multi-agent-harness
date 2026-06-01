# Spec — Feature #5: API REST: autenticación simple con JWT

## Archivos a crear o modificar

| Archivo | Tipo | Descripción |
|---|---|---|
| `src/auth.py` | NUEVO | Módulo de autenticación: hash de passwords, creación/validación de JWT, dependencias FastAPI |
| `src/api.py` | MODIFICACIÓN | Agregar schemas Pydantic (`RegisterRequest`, `LoginRequest`, `TokenResponse`, `UserResponse`) y endpoints `/api/v1/auth/*` |
| `tests/test_auth.py` | NUEVO | Tests con TestClient para registro, login y validación de token |

---

## Implementación

### src/auth.py

Módulo de autenticación stateless con JWT + bcrypt. **No depende de FastAPI** para la lógica criptográfica; la dependencia con FastAPI solo aparece en las funciones `get_current_user` y `require_admin`, que son dependencias de ruta (`Depends`).

```python
# ---------------------------------------------------------------------------
# Configuración (constantes de módulo, leídas de entorno)
# ---------------------------------------------------------------------------

SECRET_KEY: str       # os.getenv("JWT_SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM: str        # "HS256"
ACCESS_TOKEN_EXPIRE_HOURS: int  # 24

# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """
    Retorna el hash bcrypt del password en texto plano.

    Usa bcrypt.hashpw con salt aleatorio (bcrypt.gensalt()).
    El resultado se decodifica de bytes a str (UTF-8).

    Raises:
        ValueError: si password es vacío (el comportamiento exacto depende de bcrypt).
    """

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifica que plain_password coincida con hashed_password.

    Usa bcrypt.checkpw. Ambos argumentos se codifican a UTF-8 internamente.
    Retorna True si coincide, False en caso contrario.
    No lanza excepciones por contraseña incorrecta.
    """

# ---------------------------------------------------------------------------
# JWT: creación y decodificación
# ---------------------------------------------------------------------------

def create_access_token(data: dict[str, Any]) -> str:
    """
    Crea un token JWT con payload = data + {"exp": datetime.utcnow() + 24h}.

    Args:
        data: dict que DEBE contener al menos {"user_id": int, "role": str}.

    Returns:
        str: token JWT codificado con SECRET_KEY y ALGORITHM.

    El campo 'exp' se agrega automáticamente como datetime UTC + 24 horas.
    La librería python-jose se encarga de serializar el datetime correctamente.
    """

def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decodifica y valida un token JWT.

    Args:
        token: string del token JWT.

    Returns:
        dict con el payload decodificado (incluye user_id, role, exp, etc.).

    Raises:
        jose.JWTError: si el token es inválido, expirado, malformado, o
                       fue firmado con otra clave.
    """

# ---------------------------------------------------------------------------
# Esquema OAuth2 para extraer el token del header Authorization
# ---------------------------------------------------------------------------

oauth2_scheme: OAuth2PasswordBearer
#   tokenUrl="/api/v1/auth/login"
#   auto_error=False  ← importante: permite que get_current_user emita 401
#                       con mensaje personalizado en vez del default de FastAPI

# ---------------------------------------------------------------------------
# Dependencias FastAPI
# ---------------------------------------------------------------------------

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    user_repo: UserRepository = Depends(lambda: UserRepository()),
) -> User:
    """
    Dependencia FastAPI que extrae y valida el JWT del request.

    Flujo:
      1. Si token es None → HTTP 401 "Not authenticated"
      2. Decodifica el token con decode_access_token().
         Si JWTError → HTTP 401 "Invalid or expired token"
      3. Extrae 'user_id' del payload.
         Si no existe → HTTP 401 "Invalid token: missing user_id"
      4. Busca el usuario en UserRepository con find_by_id(user_id).
         Si no existe → HTTP 401 "User not found"
      5. Retorna la instancia User.

    Raises:
        HTTPException(401): en cualquiera de los casos de fallo listados arriba.
            Incluye header WWW-Authenticate: Bearer cuando el token es None.

    Uso típico en rutas:
        @app.get("/protected")
        def protected_route(current_user: User = Depends(get_current_user)):
            ...
    """

async def require_admin(
    user: User = Depends(get_current_user),
) -> User:
    """
    Dependencia FastAPI que exige que el usuario autenticado sea admin.

    Llama primero a get_current_user (que valida el JWT).
    Luego verifica user.role.

    Returns:
        User: el usuario autenticado, si su role == "admin".

    Raises:
        HTTPException(403): "Admin privileges required" si user.role != "admin".
        HTTPException(401): si el token es inválido (heredado de get_current_user).

    Uso típico:
        @app.post("/admin/thing")
        def admin_thing(admin: User = Depends(require_admin)):
            ...
    """
```

### src/api.py

Agregar los siguientes schemas Pydantic y endpoints a la aplicación FastAPI existente. El archivo `src/api.py` ya contiene `app = FastAPI(...)`.

```python
# ---------------------------------------------------------------------------
# Schemas Pydantic (nuevos)
# ---------------------------------------------------------------------------

from pydantic import BaseModel, EmailStr, field_validator

class RegisterRequest(BaseModel):
    """
    Schema para POST /api/v1/auth/register.

    Campos:
        name: str           — nombre del usuario
        email: EmailStr     — email validado por Pydantic (debe ser email válido)
        password: str       — contraseña en texto plano (se hashea antes de guardar)
        role: str = "client" — debe ser 'client' o 'admin'

    Validación:
        validate_role: field_validator que lanza ValueError
        con mensaje "role must be 'client' or 'admin'" si role no es
        uno de esos dos valores.
    """

class LoginRequest(BaseModel):
    """
    Schema para POST /api/v1/auth/login.

    Campos:
        email: EmailStr     — email validado por Pydantic
        password: str       — contraseña en texto plano
    """

class TokenResponse(BaseModel):
    """
    Schema de respuesta para register y login.
    Campos:
        access_token: str
        token_type: str = "bearer"
    """

class UserResponse(BaseModel):
    """
    Schema de respuesta para GET /api/v1/auth/me.
    Campos:
        id: int
        name: str
        email: str
        credits: int
        role: str
        created_at: str     — ISO 8601
    """

# ---------------------------------------------------------------------------
# Helpers de inyección de repositorios
# ---------------------------------------------------------------------------

def _get_user_repo() -> UserRepository:
    """Factory para injectar UserRepository en las rutas."""
    return UserRepository()

# ---------------------------------------------------------------------------
# Endpoints de autenticación
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register", response_model=TokenResponse)
def register(
    payload: RegisterRequest,
    user_repo: UserRepository = Depends(_get_user_repo),
) -> dict[str, Any]:
    """
    Registra un nuevo usuario y retorna un token JWT.

    Flujo:
      1. Verifica duplicado de email con user_repo.find_by_email(payload.email).
         Si ya existe → HTTP 400 "Email already registered"
      2. Calcula el siguiente id: max(ids existentes) + 1. Si no hay usuarios, id=1.
      3. Crea instancia User con:
           - password_hash = hash_password(payload.password)
           - credits=0, created_at=datetime.now(timezone.utc) (por default del modelo)
      4. Persiste con user_repo.save_one(user).
      5. Crea token JWT con payload {"user_id": user.id, "role": user.role}.
      6. Retorna {"access_token": token, "token_type": "bearer"}.

    Raises:
        HTTPException(400): email duplicado.
        HTTPException(422): request body inválido (Pydantic validation).

    Nota: El password NUNCA se guarda en texto plano. Solo se persiste el hash.
    """

@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    user_repo: UserRepository = Depends(_get_user_repo),
) -> dict[str, Any]:
    """
    Autentica un usuario existente y retorna un token JWT.

    Flujo:
      1. Busca usuario por email con user_repo.find_by_email(payload.email).
      2. Si user es None O verify_password(payload.password, user.password_hash)
         es False → HTTP 401 "Invalid email or password"
         (mismo mensaje para ambos casos — no revela si el email existe o no).
      3. Crea token JWT con payload {"user_id": user.id, "role": user.role}.
      4. Retorna {"access_token": token, "token_type": "bearer"}.

    Raises:
        HTTPException(401): credenciales inválidas.

    Nota de seguridad: El mensaje de error es deliberadamente ambiguo
    ("Invalid email or password") para no filtrar si un email está registrado.
    """

@app.get("/api/v1/auth/me", response_model=UserResponse)
def get_me(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Retorna el perfil del usuario autenticado.

    Usa la dependencia get_current_user para validar el JWT.
    Retorna current_user.to_dict().

    Raises:
        HTTPException(401): token inválido, expirado, o ausente
                             (heredado de get_current_user).
    """
```

---

## Tests a escribir

### tests/test_auth.py

Se usa `pytest` con `TestClient` de FastAPI. El fixture `client` redirige `UserRepository` a un directorio temporal (`tmp_path`) mediante monkeypatching de `UserRepository.__init__`, para aislar completamente cada test del sistema de archivos real.

```python
# ---------------------------------------------------------------------------
# Fixture compartido
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    """
    Crea un TestClient con UserRepository aislado en tmp_path.

    IMPORTANTE: Se monkeypatchea UserRepository.__init__ para que TODA
    instanciación — tanto en api.py como en auth.py — use tmp_path sin
    importar el argumento data_dir que se pase. Esto es necesario porque
    get_current_user y las rutas instancian UserRepository() directamente
    (sin pasar data_dir).

    Estrategia:
      - Se guarda el __init__ original.
      - Se define patched_init(self, data_dir="data") que llama al
        original con data_dir=str(tmp_path).
      - Se aplica con monkeypatch.setattr.
      - Se retorna TestClient(app).
    """
```

#### Clase `TestAuthRegister`

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_register_success` | Repo vacío (tmp_path limpio) | `POST /api/v1/auth/register` con `{"name":"Alice","email":"alice@example.com","password":"secret123","role":"client"}` | `status_code == 200`; `access_token` presente en la respuesta; `token_type == "bearer"`; token no vacío |
| 2 | `test_register_admin` | Repo vacío | `POST /api/v1/auth/register` con `role: "admin"` | `status_code == 200`; respuesta contiene `access_token` válido |

#### Clase `TestAuthLogin`

Helper estático `_register(client, ...)` para pre-registrar un usuario antes de probar login.

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_login_success` | Usuario registrado con email="alice@example.com", password="secret123" | `POST /api/v1/auth/login` con email y password correctos | `status_code == 200`; `access_token` presente; `token_type == "bearer"` |
| 2 | `test_login_wrong_password_returns_401` | Usuario registrado | `POST /api/v1/auth/login` con password incorrecto ("wrongpassword") | `status_code == 401` |
| 3 | `test_login_nonexistent_email_returns_401` | Sin registrar ningún usuario con ese email | `POST /api/v1/auth/login` con email="nobody@example.com" | `status_code == 401` |

#### Clase `TestTokenValidation`

Helper estático `_register_and_get_token(client)` que registra un usuario y retorna su token JWT.

| # | Nombre del test | Precondición | Acción | Assertion |
|---|---|---|---|---|
| 1 | `test_invalid_token_returns_401` | — | `GET /api/v1/auth/me` con header `Authorization: Bearer this.is.not.valid` | `status_code == 401` |
| 2 | `test_missing_token_returns_401` | — | `GET /api/v1/auth/me` SIN header Authorization | `status_code == 401` |
| 3 | `test_valid_token_returns_user` | Usuario "Bob" (bob@example.com) registrado y token obtenido | `GET /api/v1/auth/me` con header `Authorization: Bearer {token}` | `status_code == 200`; `email == "bob@example.com"`; `name == "Bob"`; `role == "client"` |

---

## Dependencias

Las siguientes librerías deben estar instaladas (ya figuran en `requirements.txt`):

```
python-jose[cryptography]>=3.3.0   # JWT (jose.JWTError, jose.jwt)
bcrypt>=4.0.0                      # hash de passwords (bcrypt.hashpw, bcrypt.checkpw, bcrypt.gensalt)
python-multipart>=0.0.9            # necesario para OAuth2PasswordBearer (form data)
pydantic>=2.0.0                    # EmailStr, field_validator, BaseModel
fastapi>=0.111.0                   # FastAPI, Depends, HTTPException, OAuth2PasswordBearer
```

**Nota sobre bcrypt vs passlib:** La implementación usa `bcrypt` directamente (no `passlib`), aunque ambas librerías están en `requirements.txt`. Usar `bcrypt` directo simplifica el código y evita una capa de abstracción innecesaria para este caso de uso.

---

## Notas de implementación

1. **`auto_error=False` en OAuth2PasswordBearer**: Esto es crucial. Si `auto_error=True` (el default), FastAPI automáticamente retorna un 401 con un mensaje estándar cuando no hay header Authorization. Con `auto_error=False`, el token puede ser `None`, y `get_current_user` emite manualmente un 401 con el mensaje personalizado `"Not authenticated"` y el header `WWW-Authenticate: Bearer`.

2. **Mensaje de error ambiguo en login**: El endpoint de login retorna `"Invalid email or password"` tanto si el email no existe como si el password es incorrecto. Esto es una práctica de seguridad estándar para evitar enumeración de usuarios registrados.

3. **El password NUNCA se retorna en ninguna respuesta**: `UserResponse` no incluye `password_hash`. La serialización `current_user.to_dict()` sí incluye `password_hash`, pero `UserResponse` (Pydantic) solo expone los campos declarados: `id`, `name`, `email`, `credits`, `role`, `created_at`. El campo `password_hash` del dict es ignorado por Pydantic.

4. **`UserRepository` se instancia sin argumentos en las dependencias**: Tanto las rutas de `api.py` como `get_current_user` en `auth.py` crean `UserRepository()` sin pasar `data_dir`. Esto funciona en producción (usa `data/` por defecto), pero requiere monkeypatching en tests para redirigir a `tmp_path`. El fixture de test parchea `UserRepository.__init__` a nivel de clase, lo que afecta a TODAS las instanciaciones.

5. **El endpoint `/api/v1/auth/me`**: No es parte explícita de los requisitos mínimos de la feature #5, pero es necesario para que los tests de validación de token puedan verificar que `get_current_user` funciona correctamente (test `test_valid_token_returns_user`). Sin este endpoint, no habría forma de probar `get_current_user` de manera end-to-end con TestClient.

6. **Registro con email duplicado**: El endpoint register verifica duplicados llamando a `user_repo.find_by_email()` ANTES de crear el usuario. Si el email ya existe, retorna HTTP 400 con `"Email already registered"`. Esto depende de que `UserRepository.find_by_email()` esté implementado (feature #4).

7. **Asignación de IDs**: Los IDs se asignan secuencialmente como `max(ids) + 1`. Si no hay usuarios, el primer ID es 1. Esta lógica está en el endpoint (no en el repositorio) porque la asignación de IDs es una decisión de la capa de aplicación, no de persistencia.

8. **Configuración por variables de entorno**: `SECRET_KEY` se lee de `os.getenv("JWT_SECRET_KEY", ...)`. En producción DEBE configurarse `JWT_SECRET_KEY` con un valor criptográficamente seguro. El default `"dev-secret-key-change-in-production"` solo es apto para desarrollo local.

9. **Expiración del token**: 24 horas desde el momento de creación. El claim `exp` se calcula como `datetime.now(timezone.utc) + timedelta(hours=24)`. La librería `python-jose` maneja automáticamente la validación de expiración durante `jwt.decode()`: si el token expiró, lanza `JWTError`.

10. **Orden de features**: Esta feature (#5) depende de:
    - Feature #1: `src/models/user.py` (clase User con `to_dict()`, `from_dict()`, validación de email y role)
    - Feature #4: `src/repositories/users.py` (UserRepository con `find_by_email`, `find_by_id`, `save_one`)
    - Feature #4: `src/storage.py` (load/save) — implícitamente a través del repositorio
