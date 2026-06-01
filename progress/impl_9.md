# Implementación Feature #9: API REST — créditos y panel de admin

## Archivos creados

| Archivo | Descripción |
|---------|-------------|
| `src/models/credit_transaction.py` | Modelo de dominio `CreditTransaction` con validación de amount (1-100), to_dict/from_dict |
| `src/repositories/credit_transactions.py` | Repositorio con `data_dir` configurable, métodos CRUD, `find_by_user_id()`, `next_id()` |

## Archivos modificados

| Archivo | Cambios |
|---------|---------|
| `src/api.py` | Agregados 4 endpoints: `POST /api/v1/users/{id}/credits`, `GET /api/v1/users/{id}/credits/history`, `GET /api/v1/admin/users`, `GET /api/v1/admin/sessions/{id}/attendees`. También se agregaron schemas Pydantic (`AddCreditsWithReasonRequest`, `CreditTransactionResponse`) y se integró `CreditTransactionRepository` como dependencia |
| `tests/test_credits.py` | Tests completos para los 4 endpoints (15 tests en 4 clases) |
| `tests/conftest.py` | Ya existía; no se modificó (el fixture `client` del test parchea los repositorios vía monkeypatch) |

## Endpoints implementados

1. **POST /api/v1/users/{id}/credits** (requiere admin)
   - Body: `{amount: int (1–100), reason: str}`
   - Valida el rango de amount con Pydantic (`@field_validator`)
   - Suma credits al usuario, registra `CreditTransaction`
   - Devuelve `UserAdminResponse` (sin `password_hash`)

2. **GET /api/v1/users/{id}/credits/history** (admin o propio usuario)
   - Admin puede ver historial de cualquier usuario
   - Usuario solo puede ver su propio historial (403 si intenta ver otro)

3. **GET /api/v1/admin/users** (requiere admin)
   - Lista todos los usuarios excluyendo `password_hash`

4. **GET /api/v1/admin/sessions/{id}/attendees** (requiere admin)
   - Lista bookings `confirmed` para una sesión con datos del usuario (name, email)
   - Devuelve 404 si la sesión no existe

## Decisiones de diseño

- El modelo `CreditTransaction` valida amount entre 1 y 100 en el constructor y lanza `ValueError`
- El repositorio `CreditTransactionRepository` acepta `data_dir` como argumento (igual que `UserRepository`) para permitir tests aislados con `tmp_path`
- Los endpoints de feature #9 y feature #12 coexisten sin conflicto (rutas distintas: `/admin/sessions/{id}/attendees` vs `/sessions/{id}/attendees`, `/admin/users` vs `/users`, `POST /users/{id}/credits` con reason vs `PUT /users/{id}/credits` sin reason)
- Se usa Pydantic `@field_validator` para validar el amount en el request body (422 en caso de rango inválido)
- El fixture `client` en el test parchea también `CreditTransactionRepository` para usar `tmp_path`

## Output de pytest

```
============================= test session starts ==============================
platform darwin -- Python 3.9.6, pytest-8.4.2, pluggy-1.6.0
rootdir: /Users/fmejiavi/Documents/agentes-harness-prueba/tests
configfile: pytest.ini
plugins: anyio-4.12.1, asyncio-1.2.0, base-url-2.1.0, playwright-0.7.1

tests/test_auth.py::TestAuthRegister::test_register_success PASSED
tests/test_auth.py::TestAuthRegister::test_register_admin PASSED
tests/test_auth.py::TestAuthLogin::test_login_success PASSED
tests/test_auth.py::TestAuthLogin::test_login_wrong_password_returns_401 PASSED
tests/test_auth.py::TestAuthLogin::test_login_nonexistent_email_returns_401 PASSED
tests/test_auth.py::TestTokenValidation::test_invalid_token_returns_401 PASSED
tests/test_auth.py::TestTokenValidation::test_missing_token_returns_401 PASSED
tests/test_auth.py::TestTokenValidation::test_valid_token_returns_user PASSED
tests/test_user.py ... 24 tests PASSED
tests/test_sessions.py ... 25 tests PASSED
tests/test_session.py ... 19 tests PASSED
tests/test_bookings.py ... 17 tests PASSED
tests/test_booking.py ... 15 tests PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_admin_can_add_credits PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_add_credits_amount_zero_returns_422 PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_add_credits_amount_out_of_range_upper PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_add_credits_amount_negative_returns_422 PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_client_cannot_add_credits_403 PASSED
tests/test_credits.py::TestAdminAddCreditsFeature9::test_unauthenticated_cannot_add_credits PASSED
tests/test_credits.py::TestCreditHistoryAccess::test_admin_can_view_any_user_history PASSED
tests/test_credits.py::TestCreditHistoryAccess::test_user_can_view_own_history PASSED
tests/test_credits.py::TestCreditHistoryAccess::test_user_cannot_view_another_user_history PASSED
tests/test_credits.py::TestAdminUsersList::test_admin_can_list_all_users PASSED
tests/test_credits.py::TestAdminUsersList::test_client_cannot_list_users_via_admin_endpoint PASSED
tests/test_credits.py::TestAdminUsersList::test_unauthenticated_cannot_list_users PASSED
tests/test_credits.py::TestAdminSessionAttendees::test_admin_can_list_attendees_for_session PASSED
tests/test_credits.py::TestAdminSessionAttendees::test_client_cannot_list_attendees PASSED
tests/test_credits.py::TestAdminSessionAttendees::test_attendees_nonexistent_session_404 PASSED
tests/test_sessions_api.py ... 24 tests PASSED
tests/test_stats.py ... 19 tests PASSED
tests/test_repositories.py ... 17 tests PASSED
tests/test_storage.py ... 6 tests PASSED
tests/test_feature9.py ... 7 tests PASSED
tests/test_feature10.py ... 8 tests PASSED
tests/test_feature_9.py ... 4 tests PASSED

============================= 225 passed in 57.62s ==============================
```

Todos los 225 tests pasan correctamente.
