"""Tests for credits and admin panel endpoints (Feature #9)."""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.models.booking import Booking
from src.models.credit_transaction import CreditTransaction
from src.models.user import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_admin(client: TestClient, suffix: str = "") -> str:
    email = f"admin_{suffix}@example.com"
    resp = client.post("/api/v1/auth/register", json={
        "name": f"Admin {suffix}",
        "email": email,
        "password": "AdminPass123!",
        "role": "admin",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _register_client(client: TestClient, suffix: str = "") -> str:
    email = f"client_{suffix}@example.com"
    resp = client.post("/api/v1/auth/register", json={
        "name": f"Client {suffix}",
        "email": email,
        "password": "ClientPass123!",
        "role": "client",
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _admin_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


def _client_headers(client_token: str) -> dict:
    return {"Authorization": f"Bearer {client_token}"}


def _create_session(
    client: TestClient,
    admin_token: str,
    title: str = "Test Session",
    capacity: int = 5,
) -> dict:
    resp = client.post(
        "/api/v1/sessions",
        json={
            "title": title,
            "instructor": "Alice",
            "style": "Vinyasa",
            "starts_at": "2025-06-15T09:00:00",
            "duration_minutes": 60,
            "capacity": capacity,
        },
        headers=_admin_headers(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Fixture: redirect repositories to tmp_path
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a TestClient with repositories using a temp directory."""
    import src.repositories.users as users_mod
    import src.repositories.sessions as sessions_mod
    import src.repositories.bookings as bookings_mod
    import src.repositories.credit_transactions as ct_mod
    import src.repositories.notifications as notifications_mod

    original_users_init = users_mod.UserRepository.__init__
    original_sessions_init = sessions_mod.SessionRepository.__init__
    original_bookings_init = bookings_mod.BookingRepository.__init__
    original_ct_init = ct_mod.CreditTransactionRepository.__init__
    original_notifications_init = notifications_mod.NotificationRepository.__init__

    def patched_users_init(self, data_dir="data"):
        original_users_init(self, str(tmp_path))

    def patched_sessions_init(self, data_dir="data"):
        original_sessions_init(self, str(tmp_path))

    def patched_bookings_init(self, data_dir="data"):
        original_bookings_init(self, str(tmp_path))

    def patched_ct_init(self, data_dir="data"):
        original_ct_init(self, str(tmp_path))

    def patched_notifications_init(self, data_dir="data"):
        original_notifications_init(self, str(tmp_path))

    monkeypatch.setattr(users_mod.UserRepository, "__init__", patched_users_init)
    monkeypatch.setattr(sessions_mod.SessionRepository, "__init__", patched_sessions_init)
    monkeypatch.setattr(bookings_mod.BookingRepository, "__init__", patched_bookings_init)
    monkeypatch.setattr(ct_mod.CreditTransactionRepository, "__init__", patched_ct_init)
    monkeypatch.setattr(notifications_mod.NotificationRepository, "__init__", patched_notifications_init)

    return TestClient(app)


# ---------------------------------------------------------------------------
# Test: Admin can add credits via POST /api/v1/users/{id}/credits
# ---------------------------------------------------------------------------

class TestAdminAddCreditsFeature9:
    """Tests for POST /api/v1/users/{id}/credits (feature #9)."""

    def test_admin_can_add_credits(self, client: TestClient):
        """Admin adds credits to a client user successfully."""
        admin_token = _register_admin(client, "addcred")
        client_token = _register_client(client, "target")

        # Get target user info
        client_email = f"client_target@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        assert resp.status_code == 200
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        assert target is not None
        user_id = target["id"]
        original_credits = target["credits"]

        # Admin adds credits with reason
        resp = client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 5, "reason": "Welcome bonus"},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()
        assert updated["credits"] == original_credits + 5
        assert updated["id"] == user_id
        assert "password_hash" not in updated

        # Verify credit transaction was recorded
        resp_hist = client.get(
            f"/api/v1/users/{user_id}/credits/history",
            headers=_admin_headers(admin_token),
        )
        assert resp_hist.status_code == 200, resp_hist.text
        history = resp_hist.json()
        assert len(history) == 1
        assert history[0]["amount"] == 5
        assert history[0]["reason"] == "Welcome bonus"
        assert history[0]["user_id"] == user_id

    def test_add_credits_amount_zero_returns_422(self, client: TestClient):
        """Amount 0 is invalid (must be between 1 and 100)."""
        admin_token = _register_admin(client, "zero")
        _register_client(client, "zero_target")

        client_email = "client_zero_target@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        user_id = target["id"]

        resp = client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 0, "reason": "invalid zero"},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_add_credits_amount_out_of_range_upper(self, client: TestClient):
        """Amount > 100 is invalid (must be between 1 and 100)."""
        admin_token = _register_admin(client, "high")
        _register_client(client, "high_target")

        client_email = "client_high_target@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        user_id = target["id"]

        resp = client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 101, "reason": "Too much"},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_add_credits_amount_negative_returns_422(self, client: TestClient):
        """Negative amount is invalid."""
        admin_token = _register_admin(client, "neg")
        _register_client(client, "neg_target")

        client_email = "client_neg_target@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        user_id = target["id"]

        resp = client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": -5, "reason": "Negative test"},
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    def test_client_cannot_add_credits_403(self, client: TestClient):
        """A client user cannot add credits (admin-only endpoint)."""
        client_token = _register_client(client, "forbidden")

        # Try to add credits to themselves
        resp = client.get("/api/v1/auth/me", headers=_client_headers(client_token))
        user_id = resp.json()["id"]

        resp = client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 5, "reason": "Self serve?"},
            headers=_client_headers(client_token),
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
        assert "admin" in resp.json().get("detail", "").lower()

    def test_unauthenticated_cannot_add_credits(self, client: TestClient):
        """No token returns 401 for credit addition."""
        resp = client.post(
            "/api/v1/users/1/credits",
            json={"amount": 5, "reason": "No auth"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Test: Credit history access control
# ---------------------------------------------------------------------------

class TestCreditHistoryAccess:
    """Tests for GET /api/v1/users/{id}/credits/history."""

    def test_admin_can_view_any_user_history(self, client: TestClient):
        """Admin can view credit history of any user."""
        admin_token = _register_admin(client, "histadmin")
        _register_client(client, "histtarget")

        # Get target user id
        client_email = "client_histtarget@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        user_id = target["id"]

        # Admin adds credits first
        client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 10, "reason": "History test"},
            headers=_admin_headers(admin_token),
        )

        # Admin views history
        resp = client.get(
            f"/api/v1/users/{user_id}/credits/history",
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 200, resp.text
        history = resp.json()
        assert len(history) >= 1
        assert any(t["reason"] == "History test" for t in history)

    def test_user_can_view_own_history(self, client: TestClient):
        """A user can view their own credit history."""
        admin_token = _register_admin(client, "ownadmin")
        client_token = _register_client(client, "ownhist")

        # Get target user id
        client_email = "client_ownhist@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        user_id = target["id"]

        # Admin adds credits
        client.post(
            f"/api/v1/users/{user_id}/credits",
            json={"amount": 7, "reason": "Self view test"},
            headers=_admin_headers(admin_token),
        )

        # Client views own history
        resp = client.get(
            f"/api/v1/users/{user_id}/credits/history",
            headers=_client_headers(client_token),
        )
        assert resp.status_code == 200, resp.text
        history = resp.json()
        assert len(history) >= 1

    def test_user_cannot_view_another_user_history(self, client: TestClient):
        """A regular client cannot view another user's credit history."""
        admin_token = _register_admin(client, "crossadmin")
        client_token_a = _register_client(client, "userA")
        client_token_b = _register_client(client, "userB")

        # Get user A id
        client_email_a = "client_userA@example.com"
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target_a = next((u for u in users if u["email"] == client_email_a), None)
        user_a_id = target_a["id"]

        # User B tries to view user A's history
        resp = client.get(
            f"/api/v1/users/{user_a_id}/credits/history",
            headers=_client_headers(client_token_b),
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Test: Admin users list via GET /api/v1/admin/users
# ---------------------------------------------------------------------------

class TestAdminUsersList:
    """Tests for GET /api/v1/admin/users."""

    def test_admin_can_list_all_users(self, client: TestClient):
        """Admin can list all users via /api/v1/admin/users."""
        admin_token = _register_admin(client, "listadmin")
        _register_client(client, "listclient")

        resp = client.get(
            "/api/v1/admin/users",
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 200, resp.text
        users = resp.json()
        assert isinstance(users, list)
        assert len(users) >= 2  # admin + client

        for u in users:
            assert "id" in u
            assert "name" in u
            assert "email" in u
            assert "credits" in u
            assert "role" in u
            assert "created_at" in u
            assert "password_hash" not in u, "password_hash must be excluded"

    def test_client_cannot_list_users_via_admin_endpoint(self, client: TestClient):
        """A client cannot access GET /api/v1/admin/users."""
        client_token = _register_client(client, "noadminlist")

        resp = client.get(
            "/api/v1/admin/users",
            headers=_client_headers(client_token),
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_unauthenticated_cannot_list_users(self, client: TestClient):
        """No token returns 401 for admin users list."""
        resp = client.get("/api/v1/admin/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test: Admin session attendees via GET /api/v1/admin/sessions/{id}/attendees
# ---------------------------------------------------------------------------

class TestAdminSessionAttendees:
    """Tests for GET /api/v1/admin/sessions/{id}/attendees."""

    def test_admin_can_list_attendees_for_session(self, client: TestClient):
        """Admin can list confirmed attendees for a session."""
        admin_token = _register_admin(client, "attadmin")
        client_token = _register_client(client, "attclient")

        client_email = "client_attclient@example.com"

        # Get client id and add credits for booking
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        client_id = target["id"]

        # Admin adds credits to client first
        client.post(
            f"/api/v1/users/{client_id}/credits",
            json={"amount": 5, "reason": "For booking"},
            headers=_admin_headers(admin_token),
        )

        # Create a session
        session = _create_session(client, admin_token, title="Attendee Session")

        # Client books the session
        resp_book = client.post("/api/v1/bookings", json={
            "session_id": session["id"],
        }, headers=_client_headers(client_token))
        assert resp_book.status_code == 201, resp_book.text

        # Admin lists attendees via /api/v1/admin/sessions/{id}/attendees
        resp = client.get(
            f"/api/v1/admin/sessions/{session['id']}/attendees",
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 200, resp.text
        attendees = resp.json()
        assert len(attendees) == 1
        attendee = attendees[0]
        assert attendee["user_email"] == client_email
        assert attendee["status"] == "confirmed"
        assert "booking_id" in attendee
        assert "user_id" in attendee
        assert "user_name" in attendee

    def test_client_cannot_list_attendees(self, client: TestClient):
        """A client cannot access the attendees endpoint."""
        admin_token = _register_admin(client, "attforbid")
        client_token = _register_client(client, "attforbidclient")

        client_email = "client_attforbidclient@example.com"

        # Get client id and add credits
        resp = client.get("/api/v1/users", headers=_admin_headers(admin_token))
        users = resp.json()
        target = next((u for u in users if u["email"] == client_email), None)
        client_id = target["id"]
        client.post(
            f"/api/v1/users/{client_id}/credits",
            json={"amount": 5, "reason": "For booking"},
            headers=_admin_headers(admin_token),
        )

        session = _create_session(client, admin_token)

        resp_book = client.post("/api/v1/bookings", json={
            "session_id": session["id"],
        }, headers=_client_headers(client_token))
        assert resp_book.status_code == 201

        # Client tries to list attendees
        resp = client.get(
            f"/api/v1/admin/sessions/{session['id']}/attendees",
            headers=_client_headers(client_token),
        )
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"

    def test_attendees_nonexistent_session_404(self, client: TestClient):
        """Admin requesting attendees for non-existent session returns 404."""
        admin_token = _register_admin(client, "att404")
        resp = client.get(
            "/api/v1/admin/sessions/99999/attendees",
            headers=_admin_headers(admin_token),
        )
        assert resp.status_code == 404
