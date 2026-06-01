# Convenciones del proyecto

## Python

- Versión: **Python 3.9+**. Siempre usar `python3`, nunca `python`.
- Type hints obligatorios en funciones públicas.
- Docstrings en clases y métodos públicos (una línea es suficiente).
- Sin `print()` de debug en código final. Usar logging si es necesario.
- Sin TODOs sin contexto (`# TODO(quien): descripción + issue` si aplica).

## Modelos de dominio (`src/models/`)

- Clases puras: **nunca** importan `storage`, `repositories` ni `api`.
- Constructor valida invariantes y lanza `ValueError` con mensaje claro si fallan.
- Siempre implementar `to_dict() -> dict` y `from_dict(cls, data: dict)`.
- `created_at` y `updated_at` son `datetime` con timezone UTC.

## Repositorios (`src/repositories/`)

- Interfaz estándar: `find_all()`, `find_by_id(id)`, `save_one(obj)`, `delete(id)`.
- `find_by_id` retorna `None` si no encuentra (nunca lanza excepción).
- `delete` retorna `True` si eliminó, `False` si no existía.

## API REST (`src/api.py`)

- Prefijo de versión: `/api/v1/` para todos los endpoints.
- Respuestas de error con formato: `{"detail": "mensaje legible"}`.
- Códigos HTTP estándar: 200, 201, 400, 401, 403, 404, 409, 422.
- Endpoints públicos (sin auth): `GET /api/v1/sessions`, `POST /api/v1/auth/login`, `POST /api/v1/auth/register`.
- Endpoints de cliente: requieren token JWT válido (`get_current_user`).
- Endpoints de admin: requieren `role == "admin"` (`require_admin`).

## Tests

- Archivo por módulo: `tests/test_<módulo>.py`.
- Clases de test agrupadas por comportamiento: `class TestUserCreation`, `class TestUserValidation`.
- Nombres descriptivos: `test_create_user_with_invalid_email_raises_value_error`.
- Usar `pytest.raises(ValueError)` para validar excepciones esperadas.
- Tests E2E en `tests/e2e/test_<feature>.py` usando pytest-playwright.
- **No mockear** la capa de storage en tests de repositorios — usar archivos temporales con `tmp_path`.

## Frontend (React + Tailwind)

- Componentes en `frontend/src/components/`, páginas en `frontend/src/pages/`.
- Funciones fetch centralizadas en `frontend/src/api/` (un archivo por recurso).
- Diseño **mobile-first**: empezar con estilos base y agregar breakpoints `sm:`, `md:`, `lg:`.
- Estados de carga y error visibles al usuario en todos los formularios.
- No usar `any` en TypeScript si el proyecto migra a TS.

## Git

- Commits en inglés, formato: `tipo: descripción corta`.
  - `feat:`, `fix:`, `test:`, `refactor:`, `chore:`
- Un commit por feature completada.
