"""E2E tests for JWT Authentication (Feature #5).

These tests verify the authentication API from an end-user
perspective (HTTP client), complementing unit tests in
tests/test_auth.py which cover JWT internals, password hashing,
and direct endpoint logic via TestClient.

What E2E tests add:
  - Real HTTP round-trips against a running server
  - Full register → login → /me lifecycle
  - Token handling across requests
  - Error responses for invalid credentials and missing tokens
  - Visual evidence via screenshots of raw JSON responses
"""

import os
import pytest
import json
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def api_screenshot(name: str, response_data: dict, status: int):
    """Save API response as JSON screenshot for evidence."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat5_{name}.json")
    with open(path, "w") as f:
        json.dump({"status": status, "body": response_data}, f, indent=2)
    return path


@pytest.fixture
def api(playwright):
    """Provide a Playwright APIRequestContext pointed at BASE_URL."""
    request_context = playwright.request.new_context(base_url=BASE_URL)
    yield request_context
    request_context.dispose()


class TestAuthHappyPath:
    """Full register → login → /me lifecycle."""

    def test_register_login_and_access_me(self, api):
        """Register a new user, login, and access /me with the token."""
        import time
        unique_email = f"e2e_{int(time.time())}@example.com"

        # 1. Register
        res = api.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "name": "E2E Tester",
                "email": unique_email,
                "password": "Str0ng!Pass"
            }),
            headers={"Content-Type": "application/json"}
        )
        api_screenshot("happy_01_register", res.json(), res.status)
        assert res.status == 200, f"Expected 201, got {res.status}: {res.text()}"
        reg_data = res.json()
        assert "access_token" in reg_data
        assert reg_data.get("token_type") == "bearer"

        # 2. Login with the same credentials
        res = api.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": unique_email,
                "password": "Str0ng!Pass"
            }),
            headers={"Content-Type": "application/json"}
        )
        api_screenshot("happy_02_login", res.json(), res.status)
        assert res.status == 200, f"Expected 200, got {res.status}: {res.text()}"
        login_data = res.json()
        assert "access_token" in login_data

        token = login_data["access_token"]

        # 3. Access /me with the token
        res = api.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        api_screenshot("happy_03_me", res.json(), res.status)
        assert res.status == 200, f"Expected 200, got {res.status}: {res.text()}"
        me = res.json()
        assert me["email"] == unique_email
        assert me["name"] == "E2E Tester"
        assert me["role"] == "client"


class TestAuthSadPaths:
    """Error scenarios for the auth flow."""

    def test_login_with_wrong_password(self, api):
        """Login with valid email but incorrect password returns 401."""
        res = api.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": "e2e_test@example.com",
                "password": "WrongPassword99!"
            }),
            headers={"Content-Type": "application/json"}
        )
        api_screenshot("sad_01_wrong_password", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"
        detail = res.json().get("detail", "")
        assert "incorrect" in detail.lower() or "invalid" in detail.lower(), \
            f"Unexpected error message: {detail}"

    def test_login_with_nonexistent_user(self, api):
        """Login with a non-existent email returns 401."""
        res = api.post(
            "/api/v1/auth/login",
            data=json.dumps({
                "email": "no_such_user@example.com",
                "password": "Whatever123!"
            }),
            headers={"Content-Type": "application/json"}
        )
        api_screenshot("sad_02_nonexistent_user", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"
        detail = res.json().get("detail", "")
        assert "incorrect" in detail.lower() or "invalid" in detail.lower(), \
            f"Unexpected error message: {detail}"

    def test_access_me_without_token(self, api):
        """Accessing /me without an Authorization header returns 401."""
        res = api.get("/api/v1/auth/me")
        api_screenshot("sad_03_no_token", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_access_me_with_invalid_token(self, api):
        """Accessing /me with a malformed token returns 401."""
        res = api.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not.a.valid.token"}
        )
        api_screenshot("sad_04_invalid_token", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_register_existing_email(self, api):
        """Registering an already-existing email returns 409 or 400."""
        res = api.post(
            "/api/v1/auth/register",
            data=json.dumps({
                "name": "Duplicate",
                "email": "e2e_test@example.com",
                "password": "SomePass1!"
            }),
            headers={"Content-Type": "application/json"}
        )
        api_screenshot("sad_05_duplicate_email", res.json(), res.status)
        assert res.status in (400, 409), \
            f"Expected 400/409, got {res.status}: {res.text()}"
