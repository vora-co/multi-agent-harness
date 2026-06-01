"""Tests for the Session management endpoints.

GET endpoints are public (no auth required).
POST, PUT, DELETE endpoints require admin privileges (require_admin).
"""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.repositories.sessions import SessionRepository
from src.models.session import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_admin(client: TestClient) -> str:
    """Register an admin user and return the access token."""
    payload = {
        "name": "Admin User",
        "email": "admin@test.com",
        "password": "admin123",
        "role": "admin",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _register_client(client: TestClient) -> str:
    """Register a client user and return the access token."""
    payload = {
        "name": "Client User",
        "email": "client@test.com",
        "password": "client123",
        "role": "client",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    """TestClient with repositories isolated to a temp directory."""
    import src.repositories.users as users_mod
    import src.repositories.sessions as sessions_mod

    original_users_init = users_mod.UserRepository.__init__
    original_sessions_init = sessions_mod.SessionRepository.__init__

    def patched_users_init(self, data_dir="data"):
        original_users_init(self, str(tmp_path))

    def patched_sessions_init(self, data_dir="data"):
        original_sessions_init(self, str(tmp_path))

    monkeypatch.setattr(users_mod.UserRepository, "__init__", patched_users_init)
    monkeypatch.setattr(sessions_mod.SessionRepository, "__init__", patched_sessions_init)

    return TestClient(app)


@pytest.fixture
def admin_token(client):
    """Return a valid admin token."""
    return _register_admin(client)


@pytest.fixture
def client_token(client):
    """Return a valid client (non-admin) token."""
    return _register_client(client)


@pytest.fixture
def session_payload():
    """Return a valid session creation payload."""
    return {
        "title": "Morning Yoga",
        "instructor": "Alice",
        "style": "Vinyasa",
        "starts_at": "2025-06-15T09:00:00",
        "duration_minutes": 60,
        "capacity": 20,
    }


# ---------------------------------------------------------------------------
# CRUD with admin token
# ---------------------------------------------------------------------------

class TestSessionCRUD:
    """Full CRUD cycle using an admin token."""

    def test_create_session_201(self, client, admin_token, session_payload):
        """Admin creates a session -> 201 with correct fields."""
        resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Morning Yoga"
        assert data["instructor"] == "Alice"
        assert data["style"] == "Vinyasa"
        assert data["starts_at"] == "2025-06-15T09:00:00"
        assert data["duration_minutes"] == 60
        assert data["capacity"] == 20
        assert data["enrolled"] == 0
        assert "id" in data

    def test_list_sessions_200(self, client, admin_token, session_payload):
        """Admin lists sessions -> sees all created sessions (public endpoint)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Evening Yoga", "style": "Hatha"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        titles = {s["title"] for s in data}
        assert titles == {"Morning Yoga", "Evening Yoga"}

    def test_get_session_by_id_200(self, client, admin_token, session_payload):
        """Admin gets a single session by id -> 200 (public endpoint)."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        resp = client.get(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Morning Yoga"

    def test_get_nonexistent_session_404(self, client, admin_token):
        """Admin gets a nonexistent session -> 404 (public endpoint)."""
        resp = client.get(
            "/api/v1/sessions/9999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"

    def test_update_session_200(self, client, admin_token, session_payload):
        """Admin updates a session -> 200 with merged fields."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        update = {"title": "Updated Morning Yoga", "capacity": 25}
        resp = client.put(
            f"/api/v1/sessions/{sid}",
            json=update,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Updated Morning Yoga"
        assert data["capacity"] == 25
        # Unchanged fields preserved
        assert data["instructor"] == "Alice"
        assert data["style"] == "Vinyasa"

    def test_update_nonexistent_session_404(self, client, admin_token):
        """Admin updates nonexistent session -> 404."""
        resp = client.put(
            "/api/v1/sessions/9999",
            json={"title": "Nope"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_delete_session_204(self, client, admin_token, session_payload):
        """Admin deletes a session with enrolled=0 -> 204."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 204

        # Verify it's gone (public endpoint, no auth needed)
        get_resp = client.get(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert get_resp.status_code == 404

    def test_delete_nonexistent_session_404(self, client, admin_token):
        """Admin deletes nonexistent session -> 404."""
        resp = client.delete(
            "/api/v1/sessions/9999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_delete_session_with_enrolled_409(
        self, client, admin_token, session_payload, tmp_path
    ):
        """Admin cannot delete a session with enrolled > 0 -> 409."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        # Bypass API to set enrolled > 0 in the stored data
        repo = SessionRepository(data_dir=str(tmp_path))
        s = repo.find_by_id(sid)
        s = Session(
            id=s.id,
            title=s.title,
            instructor=s.instructor,
            style=s.style,
            starts_at=s.starts_at,
            duration_minutes=s.duration_minutes,
            capacity=s.capacity,
            enrolled=5,
        )
        repo.save_one(s)

        resp = client.delete(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409
        assert "Cannot delete session with enrolled participants" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Access denied: no token (401) for mutations, public for GET
# ---------------------------------------------------------------------------

class TestAccessDeniedNoToken:
    """POST/PUT/DELETE endpoints reject requests without a token (401).
    GET endpoints are public and succeed without auth."""

    def test_list_without_token_200(self, client):
        """GET list is public — returns 200 without auth."""
        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200

    def test_get_without_token_404(self, client):
        """GET by id is public — returns 404 for nonexistent without auth."""
        resp = client.get("/api/v1/sessions/1")
        assert resp.status_code == 404

    def test_create_without_token_401(self, client, session_payload):
        resp = client.post("/api/v1/sessions", json=session_payload)
        assert resp.status_code == 401

    def test_update_without_token_401(self, client):
        resp = client.put("/api/v1/sessions/1", json={"title": "X"})
        assert resp.status_code == 401

    def test_delete_without_token_401(self, client):
        resp = client.delete("/api/v1/sessions/1")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Access denied: client token (403) for mutations, public for GET
# ---------------------------------------------------------------------------

class TestAccessDeniedClientToken:
    """POST/PUT/DELETE endpoints reject non-admin tokens (403).
    GET endpoints are public and succeed with client token."""

    def test_list_with_client_token_200(self, client, client_token):
        """GET list is public — client token returns 200."""
        resp = client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 200

    def test_get_with_client_token_200(
        self, client, admin_token, client_token, session_payload
    ):
        """GET by id is public — client token returns 200."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        resp = client.get(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 200

    def test_create_with_client_token_403(self, client, client_token, session_payload):
        resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_update_with_client_token_403(
        self, client, admin_token, client_token, session_payload
    ):
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/sessions/{sid}",
            json={"title": "Hacked"},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_delete_with_client_token_403(
        self, client, admin_token, client_token, session_payload
    ):
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        sid = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/sessions/{sid}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Filters: style and date (public endpoints)
# ---------------------------------------------------------------------------

class TestFilters:
    """Filtering sessions by style and/or date query parameters (public)."""

    def test_filter_by_style(self, client, admin_token, session_payload):
        """?style=Vinyasa returns only Vinyasa sessions."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Hatha Flow", "style": "Hatha"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get(
            "/api/v1/sessions?style=Vinyasa",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["style"] == "Vinyasa"

    def test_filter_by_date(self, client, admin_token, session_payload):
        """?date=YYYY-MM-DD returns sessions starting on that date."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Next Day", "starts_at": "2025-06-16T09:00:00"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get(
            "/api/v1/sessions?date=2025-06-15",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["starts_at"] == "2025-06-15T09:00:00"

    def test_filter_by_style_and_date(self, client, admin_token, session_payload):
        """?style=Vinyasa&date=2025-06-15 returns only matching sessions."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Same date, different style
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Hatha Morning", "style": "Hatha"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Same style, different date
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Vinyasa Next", "starts_at": "2025-06-16T09:00:00"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get(
            "/api/v1/sessions?style=Vinyasa&date=2025-06-15",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Morning Yoga"

    def test_filter_style_no_results(self, client, admin_token, session_payload):
        """Filter by nonexistent style returns [ ]."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = client.get(
            "/api/v1/sessions?style=Kundalini",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_date_no_results(self, client, admin_token, session_payload):
        """Filter by date with no matches returns [ ]."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        resp = client.get(
            "/api/v1/sessions?date=2025-12-25",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_invalid_date_400(self, client, admin_token):
        """Invalid date format returns 400."""
        resp = client.get(
            "/api/v1/sessions?date=not-a-date",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 400
