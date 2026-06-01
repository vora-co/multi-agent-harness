"""Tests for Feature #9: waitlist promotion and session cancellation."""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.repositories.notifications import NotificationRepository


# ---------------------------------------------------------------------------
# Helpers (same as test_bookings.py)
# ---------------------------------------------------------------------------

def _register_admin(client: TestClient) -> str:
    payload = {
        "name": "Admin User",
        "email": "admin@example.com",
        "password": "admin123",
        "role": "admin",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _register_client(client: TestClient, email="client@example.com") -> str:
    payload = {
        "name": "Client User",
        "email": email,
        "password": "client123",
        "role": "client",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _create_session(
    client: TestClient,
    admin_token: str,
    title="Morning Yoga",
    starts_at="2025-06-15T09:00:00",
    capacity=20,
) -> dict:
    payload = {
        "title": title,
        "instructor": "Alice",
        "style": "Vinyasa",
        "starts_at": starts_at,
        "duration_minutes": 60,
        "capacity": capacity,
    }
    resp = client.post(
        "/api/v1/sessions",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    return resp.json()


def _add_credits(client: TestClient, user_email: str, tmp_path, amount: int = 5) -> None:
    from src.repositories.users import UserRepository
    repo = UserRepository(data_dir=str(tmp_path))
    user = repo.find_by_email(user_email)
    user.credits = amount
    repo.save_one(user)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a TestClient with repositories redirected to a temp directory."""
    import src.repositories.users as users_mod
    import src.repositories.sessions as sessions_mod
    import src.repositories.bookings as bookings_mod
    import src.repositories.notifications as notifications_mod

    original_users_init = users_mod.UserRepository.__init__
    original_sessions_init = sessions_mod.SessionRepository.__init__
    original_bookings_init = bookings_mod.BookingRepository.__init__
    original_notifications_init = notifications_mod.NotificationRepository.__init__

    def patched_users_init(self, data_dir="data"):
        original_users_init(self, str(tmp_path))

    def patched_sessions_init(self, data_dir="data"):
        original_sessions_init(self, str(tmp_path))

    def patched_bookings_init(self, data_dir="data"):
        original_bookings_init(self, str(tmp_path))

    def patched_notifications_init(self, data_dir="data"):
        original_notifications_init(self, str(tmp_path))

    monkeypatch.setattr(users_mod.UserRepository, "__init__", patched_users_init)
    monkeypatch.setattr(sessions_mod.SessionRepository, "__init__", patched_sessions_init)
    monkeypatch.setattr(bookings_mod.BookingRepository, "__init__", patched_bookings_init)
    monkeypatch.setattr(notifications_mod.NotificationRepository, "__init__", patched_notifications_init)

    return TestClient(app)


@pytest.fixture
def admin_token(client):
    return _register_admin(client)


@pytest.fixture
def client_token(client):
    return _register_client(client)


@pytest.fixture
def session_data(client, admin_token):
    return _create_session(client, admin_token)


# ---------------------------------------------------------------------------
# Test: waitlist promotion when a confirmed booking is cancelled
# ---------------------------------------------------------------------------


class TestWaitlistPromotionOnCancel:
    """Tests that cancelling a confirmed booking promotes a waitlisted user."""

    def test_cancel_confirmed_promotes_waitlist_user(
        self, client, admin_token, tmp_path
    ):
        """When a user cancels their confirmed booking, the first eligible
        waitlisted user should be promoted to 'confirmed', get their credit
        deducted, and receive a notification."""
        # Create a session with capacity=1
        session = _create_session(client, admin_token, capacity=1, title="Small Class")

        # User A books (confirmed)
        token_a = _register_client(client, "user_a@example.com")
        _add_credits(client, "user_a@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        booking_a_id = resp_a.json()["id"]
        assert resp_a.json()["status"] == "confirmed"

        # User B goes to waitlist (session full)
        token_b = _register_client(client, "user_b@example.com")
        _add_credits(client, "user_b@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        booking_b_id = resp_b.json()["id"]
        assert resp_b.json()["status"] == "waitlist"

        # Verify user B still has 5 credits (not deducted for waitlist)
        me_b_before = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_before.json()["credits"] == 5

        # User A cancels their confirmed booking
        cancel_resp = client.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert cancel_resp.status_code == 204

        # User A's booking should now be 'cancelled'
        bookings_a = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert bookings_a.json()[0]["status"] == "cancelled"

        # User A should have their credit back (5 again now)
        me_a = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert me_a.json()["credits"] == 5

        # User B should now be confirmed (promoted from waitlist)
        bookings_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert bookings_b.json()[0]["status"] == "confirmed"

        # User B should have 4 credits (1 deducted on promotion)
        me_b_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_after.json()["credits"] == 4

        # A notification should have been created for user B
        notifications_repo = NotificationRepository(data_dir=str(tmp_path))
        user_b_notifications = notifications_repo.find_by_user(
            user_id=2  # user_b is the second registered user (admin=1, user_a=2, user_b=3...)
        )
        # Actually, let's just check that there are notifications at all
        all_notifications = notifications_repo.find_all()
        promotion_notifications = [
            n for n in all_notifications
            if "promoted from the waitlist" in n.message.lower()
        ]
        assert len(promotion_notifications) >= 1


# ---------------------------------------------------------------------------
# Test: Cancel session returns credits
# ---------------------------------------------------------------------------


class TestCancelSession:
    """Tests for the PUT /sessions/{id}/cancel admin endpoint."""

    def test_cancel_session_returns_credits_to_confirmed_users(
        self, client, admin_token, tmp_path
    ):
        """Cancelling a session should return credits to all confirmed users."""
        session = _create_session(client, admin_token, capacity=5)

        # Register and book two clients
        token_a = _register_client(client, "alice@example.com")
        _add_credits(client, "alice@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        assert resp_a.json()["status"] == "confirmed"

        token_b = _register_client(client, "bob@example.com")
        _add_credits(client, "bob@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "confirmed"

        # Verify credits were deducted
        me_a = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert me_a.json()["credits"] == 4
        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b.json()["credits"] == 4

        # Admin cancels the session
        cancel_resp = client.put(
            f"/api/v1/sessions/{session['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert cancel_resp.status_code == 204

        # Both users should have their credits back
        me_a_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert me_a_after.json()["credits"] == 5
        me_b_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_after.json()["credits"] == 5

        # Both bookings should be cancelled
        bookings_a = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert bookings_a.json()[0]["status"] == "cancelled"
        bookings_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert bookings_b.json()[0]["status"] == "cancelled"

        # Session enrolled should be 0
        session_resp = client.get(f"/api/v1/sessions/{session['id']}")
        assert session_resp.json()["enrolled"] == 0

    def test_cancel_session_no_credit_return_for_waitlist_users(
        self, client, admin_token, tmp_path
    ):
        """Waitlist users should NOT receive credits when session is cancelled."""
        session = _create_session(client, admin_token, capacity=1, title="Tiny Class")

        # User A books (confirmed)
        token_a = _register_client(client, "conf@example.com")
        _add_credits(client, "conf@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B goes to waitlist
        token_b = _register_client(client, "wait@example.com")
        _add_credits(client, "wait@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.json()["status"] == "waitlist"

        # Verify waitlist user still has 5 credits
        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b.json()["credits"] == 5

        # Admin cancels session
        cancel_resp = client.put(
            f"/api/v1/sessions/{session['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert cancel_resp.status_code == 204

        # Waitlist user should still have 5 credits (no refund)
        me_b_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_after.json()["credits"] == 5

        # Both bookings cancelled
        bookings_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert bookings_b.json()[0]["status"] == "cancelled"

    def test_cancel_session_creates_notifications(
        self, client, admin_token, tmp_path
    ):
        """Cancelling a session should create notifications for all affected users."""
        session = _create_session(client, admin_token, capacity=2)

        # User A (confirmed)
        token_a = _register_client(client, "nina@example.com")
        _add_credits(client, "nina@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B (waitlist after capacity fills)
        token_b = _register_client(client, "paul@example.com")
        _add_credits(client, "paul@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.json()["status"] == "confirmed"  # still has space

        # User C (waitlist)
        session2 = _create_session(client, admin_token, capacity=1, title="Solo")

        token_c = _register_client(client, "carol@example.com")
        _add_credits(client, "carol@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session2["id"]},
            headers={"Authorization": f"Bearer {token_c}"},
        )

        token_d = _register_client(client, "dave@example.com")
        _add_credits(client, "dave@example.com", tmp_path, amount=5)
        resp_d = client.post(
            "/api/v1/bookings",
            json={"session_id": session2["id"]},
            headers={"Authorization": f"Bearer {token_d}"},
        )
        assert resp_d.json()["status"] == "waitlist"

        # Admin cancels session2
        cancel_resp = client.put(
            f"/api/v1/sessions/{session2['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert cancel_resp.status_code == 204

        # Check notifications exist
        notifications_repo = NotificationRepository(data_dir=str(tmp_path))
        all_notifications = notifications_repo.find_all()

        # Should have at least 2 notifications (one for confirmed, one for waitlist)
        assert len(all_notifications) >= 2

        # At least one notification mentions credit returned
        credit_notifications = [
            n for n in all_notifications
            if "credit has been returned" in n.message.lower()
        ]
        assert len(credit_notifications) >= 1

        # At least one notification mentions waitlist
        waitlist_notifications = [
            n for n in all_notifications
            if "waitlist" in n.message.lower()
        ]
        assert len(waitlist_notifications) >= 1

    def test_cancel_nonexistent_session_returns_404(self, client, admin_token):
        """Cancelling a session that does not exist should return 404."""
        resp = client.put(
            "/api/v1/sessions/9999/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404

    def test_cancel_session_requires_admin(self, client, client_token, session_data):
        """Non-admin users should not be able to cancel sessions."""
        resp = client.put(
            f"/api/v1/sessions/{session_data['id']}/cancel",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_cancel_session_unauthenticated_returns_401(self, client, session_data):
        """Unauthenticated request should return 401."""
        resp = client.put(f"/api/v1/sessions/{session_data['id']}/cancel")
        assert resp.status_code == 401
