"""Tests for the authentication module (src/auth.py) and its FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient
from src.api import app
import src.repositories.users as repo_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a TestClient with UserRepository redirected to a temp directory.

    Both ``api.py`` and ``auth.py`` instantiate ``UserRepository()`` directly,
    so we monkeypatch ``__init__`` on the class so every instance uses
    ``tmp_path`` regardless of what is passed as ``data_dir``.
    """
    original_init = repo_mod.UserRepository.__init__

    def patched_init(self, data_dir="data"):
        original_init(self, str(tmp_path))

    monkeypatch.setattr(repo_mod.UserRepository, "__init__", patched_init)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests — Registration
# ---------------------------------------------------------------------------

class TestAuthRegister:
    """Tests for POST /api/v1/auth/register."""

    def test_register_success(self, client):
        """Should register a new user and return a JWT token."""
        payload = {
            "name": "Alice",
            "email": "alice@example.com",
            "password": "secret123",
            "role": "client",
        }
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 200

        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert len(data["access_token"]) > 0

    def test_register_admin(self, client):
        """Should register an admin user successfully."""
        payload = {
            "name": "Admin",
            "email": "admin@example.com",
            "password": "admin123",
            "role": "admin",
        }
        response = client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data


# ---------------------------------------------------------------------------
# Tests — Login
# ---------------------------------------------------------------------------

class TestAuthLogin:
    """Tests for POST /api/v1/auth/login."""

    @staticmethod
    def _register(client, name="Alice", email="alice@example.com",
                  password="secret123", role="client"):
        payload = {"name": name, "email": email, "password": password, "role": role}
        return client.post("/api/v1/auth/register", json=payload)

    def test_login_success(self, client):
        """Should login with correct credentials and return a JWT token."""
        self._register(client)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "secret123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password_returns_401(self, client):
        """Should return 401 when password is incorrect."""
        self._register(client)
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_login_nonexistent_email_returns_401(self, client):
        """Should return 401 when email does not exist."""
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "secret123"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Tests — Token validation
# ---------------------------------------------------------------------------

class TestTokenValidation:
    """Tests for JWT token validation via the protected /api/v1/auth/me endpoint."""

    @staticmethod
    def _register_and_get_token(client):
        payload = {
            "name": "Bob",
            "email": "bob@example.com",
            "password": "password123",
            "role": "client",
        }
        client.post("/api/v1/auth/register", json=payload)
        login_resp = client.post(
            "/api/v1/auth/login",
            json={"email": "bob@example.com", "password": "password123"},
        )
        return login_resp.json()["access_token"]

    def test_invalid_token_returns_401(self, client):
        """Should return 401 when an invalid / tampered token is provided."""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer this.is.not.valid"},
        )
        assert response.status_code == 401

    def test_missing_token_returns_401(self, client):
        """Should return 401 when no Authorization header is present."""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_valid_token_returns_user(self, client):
        """Should return the current user when a valid token is sent."""
        token = self._register_and_get_token(client)
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "bob@example.com"
        assert data["name"] == "Bob"
        assert data["role"] == "client"
