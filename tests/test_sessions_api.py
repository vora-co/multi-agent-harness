"""Tests for the Session API endpoints (GET endpoints are public, mutations admin-only)."""

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
        "email": "admin@example.com",
        "password": "admin123",
        "role": "admin",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    return resp.json()["access_token"]


def _register_client(client: TestClient) -> str:
    """Register a client user and return the access token."""
    payload = {
        "name": "Client User",
        "email": "client@example.com",
        "password": "client123",
        "role": "client",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a TestClient with repositories redirected to a temp directory."""
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
    """Return a valid client token."""
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
# CRUD Admin
# ---------------------------------------------------------------------------

class TestAdminCrud:
    """Tests for admin CRUD operations on sessions."""

    def test_admin_create_session(self, client, admin_token, session_payload):
        """Admin should be able to create a session successfully."""
        resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["title"] == "Morning Yoga"
        assert data["instructor"] == "Alice"
        assert data["style"] == "Vinyasa"
        assert data["starts_at"] == "2025-06-15T09:00:00"
        assert data["duration_minutes"] == 60
        assert data["capacity"] == 20
        assert data["enrolled"] == 0
        assert "id" in data

    def test_admin_get_all_sessions(self, client, admin_token, session_payload):
        """Anyone should be able to list all sessions (public endpoint)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        payload2 = {**session_payload, "title": "Evening Yoga", "style": "Hatha"}
        client.post(
            "/api/v1/sessions",
            json=payload2,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get("/api/v1/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        titles = {s["title"] for s in data}
        assert titles == {"Morning Yoga", "Evening Yoga"}

    def test_admin_get_session_by_id(self, client, admin_token, session_payload):
        """Anyone should be able to get a single session by id (public endpoint)."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/sessions/{session_id}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Morning Yoga"

    def test_admin_update_session(self, client, admin_token, session_payload):
        """Admin should be able to update an existing session."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        update_payload = {
            "title": "Advanced Morning Yoga",
            "capacity": 25,
        }
        resp = client.put(
            f"/api/v1/sessions/{session_id}",
            json=update_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Advanced Morning Yoga"
        assert data["capacity"] == 25
        assert data["instructor"] == "Alice"
        assert data["style"] == "Vinyasa"

    def test_admin_delete_session_with_no_enrolled(self, client, admin_token, session_payload):
        """Admin should be able to delete a session with enrolled == 0."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 204

        # Verify it's gone (public endpoint, no auth needed)
        get_resp = client.get(f"/api/v1/sessions/{session_id}")
        assert get_resp.status_code == 404

    def test_get_nonexistent_session_returns_404(self, client, admin_token):
        """Getting a nonexistent session should return 404 (public endpoint)."""
        resp = client.get("/api/v1/sessions/9999")
        assert resp.status_code == 404

    def test_update_nonexistent_session_returns_404(self, client, admin_token):
        """Admin updating nonexistent session should return 404."""
        resp = client.put(
            "/api/v1/sessions/9999",
            json={"title": "Nope"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_delete_nonexistent_session_returns_404(self, client, admin_token):
        """Admin deleting nonexistent session should return 404."""
        resp = client.delete(
            "/api/v1/sessions/9999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Client cannot create sessions (403)
# ---------------------------------------------------------------------------

class TestClientForbidden:
    """Tests that client users cannot access admin-only endpoints."""

    def test_client_cannot_create_session(self, client, client_token, session_payload):
        """A client should receive 403 when trying to create a session."""
        resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403
        assert "Admin privileges required" in resp.json()["detail"]

    def test_client_cannot_update_session(self, client, admin_token, client_token, session_payload):
        """A client should receive 403 when trying to update a session."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        resp = client.put(
            f"/api/v1/sessions/{session_id}",
            json={"title": "Hacked"},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_client_cannot_delete_session(self, client, admin_token, client_token, session_payload):
        """A client should receive 403 when trying to delete a session."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        resp = client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_unauthenticated_cannot_create_session(self, client, session_payload):
        """Unauthenticated request should receive 401 when creating a session."""
        resp = client.post("/api/v1/sessions", json=session_payload)
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Filters: style and date (public endpoints)
# ---------------------------------------------------------------------------

class TestSessionFilters:
    """Tests for filtering sessions by style and date (public endpoints)."""

    def test_filter_by_style(self, client, admin_token, session_payload):
        """Should filter sessions by style query parameter (public)."""
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

        resp = client.get("/api/v1/sessions?style=Vinyasa")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["style"] == "Vinyasa"
        assert data[0]["title"] == "Morning Yoga"

    def test_filter_by_date(self, client, admin_token, session_payload):
        """Should filter sessions by date query parameter (YYYY-MM-DD) (public)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Next Day Yoga", "starts_at": "2025-06-16T09:00:00"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get("/api/v1/sessions?date=2025-06-15")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["starts_at"] == "2025-06-15T09:00:00"

    def test_filter_by_style_and_date(self, client, admin_token, session_payload):
        """Should filter by both style and date simultaneously (public)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Hatha Morning", "style": "Hatha"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        client.post(
            "/api/v1/sessions",
            json={**session_payload, "title": "Vinyasa Next", "starts_at": "2025-06-16T09:00:00"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get("/api/v1/sessions?style=Vinyasa&date=2025-06-15")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["title"] == "Morning Yoga"

    def test_filter_by_style_no_results(self, client, admin_token, session_payload):
        """Should return empty list when no sessions match the style filter (public)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get("/api/v1/sessions?style=Kundalini")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_by_date_no_results(self, client, admin_token, session_payload):
        """Should return empty list when no sessions match the date filter (public)."""
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        resp = client.get("/api/v1/sessions?date=2025-12-25")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_invalid_date_returns_400(self, client, admin_token):
        """Should return 400 for an invalid date format (public)."""
        resp = client.get("/api/v1/sessions?date=not-a-date")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# DELETE with enrolled > 0 returns 409
# ---------------------------------------------------------------------------

class TestDeleteConflict:
    """Tests for the DELETE conflict scenario when enrolled > 0."""

    def test_delete_session_with_enrolled_participants_returns_409(
        self, client, admin_token, session_payload, monkeypatch, tmp_path
    ):
        """Should return 409 when trying to delete a session with enrolled > 0."""
        create_resp = client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        session_id = create_resp.json()["id"]

        repo = SessionRepository(data_dir=str(tmp_path))
        session = repo.find_by_id(session_id)
        session = Session(
            id=session.id,
            title=session.title,
            instructor=session.instructor,
            style=session.style,
            starts_at=session.starts_at,
            duration_minutes=session.duration_minutes,
            capacity=session.capacity,
            enrolled=5,
        )
        repo.save_one(session)

        resp = client.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 409
        assert "Cannot delete session with enrolled participants" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Public access: GET endpoints work without authentication
# ---------------------------------------------------------------------------

class TestPublicAccess:
    """Tests that GET endpoints are accessible without authentication."""

    def test_list_sessions_without_auth(self, client, admin_token, session_payload):
        """GET /api/v1/sessions without token returns 200 and session list."""
        # Create a session as admin first
        client.post(
            "/api/v1/sessions",
            json=session_payload,
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        # Read without token
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
