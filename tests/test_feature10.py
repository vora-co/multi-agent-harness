"""Tests for Feature #10: notifications and enroll_from_waitlist."""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.repositories.notifications import NotificationRepository


# ---------------------------------------------------------------------------
# Helpers
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
    import src.core as core_mod

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

    # Also patch the notify_user function so notifications go to tmp_path
    original_notify_user = core_mod.notify_user

    def patched_notify_user(user_id: int, message: str) -> None:
        from src.models.notification import Notification
        repo = NotificationRepository(data_dir=str(tmp_path))
        all_notifications = repo.find_all()
        next_id = max((n.id for n in all_notifications), default=0) + 1
        notification = Notification(
            id=next_id,
            user_id=user_id,
            message=message,
        )
        repo.save_one(notification)

    monkeypatch.setattr(core_mod, "notify_user", patched_notify_user)

    return TestClient(app)


@pytest.fixture
def admin_token(client):
    return _register_admin(client)


@pytest.fixture
def client_token(client):
    return _register_client(client)


# ---------------------------------------------------------------------------
# Tests: GET /users/me/notifications
# ---------------------------------------------------------------------------


class TestListMyNotifications:
    """Tests for GET /users/me/notifications."""

    def test_list_notifications_empty(self, client, client_token):
        """A user with no notifications should get an empty list."""
        resp = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_notifications_ordered_by_created_at_desc(
        self, client, admin_token, tmp_path
    ):
        """Notifications should be returned ordered by created_at descending."""
        # Create a session with capacity=1 so we get a waitlist + notifications
        session = _create_session(client, admin_token, capacity=1, title="Notify Test")

        # User A (confirmed)
        token_a = _register_client(client, "alpha@example.com")
        _add_credits(client, "alpha@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B (waitlist)
        token_b = _register_client(client, "beta@example.com")
        _add_credits(client, "beta@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )

        # Admin cancels session -> creates notifications for both users
        cancel_resp = client.put(
            f"/api/v1/sessions/{session['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert cancel_resp.status_code == 204

        # User A should have at least 1 notification, ordered by created_at desc
        resp = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 200
        notifications = resp.json()
        assert len(notifications) >= 1

        # Verify each notification has expected keys
        for n in notifications:
            assert "id" in n
            assert "user_id" in n
            assert "message" in n
            assert "created_at" in n
            # read_at may be absent or null

        # Verify ordering: first item's created_at >= second item's
        if len(notifications) >= 2:
            assert notifications[0]["created_at"] >= notifications[1]["created_at"]

    def test_list_notifications_requires_auth(self, client):
        """Unauthenticated access should return 401."""
        resp = client.get("/api/v1/users/me/notifications")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: PUT /users/me/notifications/{id}/read
# ---------------------------------------------------------------------------


class TestMarkNotificationRead:
    """Tests for PUT /users/me/notifications/{id}/read."""

    def test_mark_own_notification_read(self, client, admin_token, tmp_path):
        """Marking your own notification as read should succeed and set read_at."""
        # Create a session with capacity=1, then cancel to generate notifications
        session = _create_session(client, admin_token, capacity=1, title="Read Test")

        token_client = _register_client(client, "reader@example.com")
        _add_credits(client, "reader@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_client}"},
        )

        # Another user to fill waitlist
        token_b = _register_client(client, "other@example.com")
        _add_credits(client, "other@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )

        # Cancel session to generate notification for reader
        client.put(
            f"/api/v1/sessions/{session['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        # Get reader's notifications
        resp = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_client}"},
        )
        notifications = resp.json()
        assert len(notifications) >= 1
        notification_id = notifications[0]["id"]

        # Before marking: read_at should be null/absent
        assert notifications[0].get("read_at") is None

        # Mark as read
        mark_resp = client.put(
            f"/api/v1/users/me/notifications/{notification_id}/read",
            headers={"Authorization": f"Bearer {token_client}"},
        )
        assert mark_resp.status_code == 200
        marked = mark_resp.json()
        assert marked["id"] == notification_id
        assert marked["read_at"] is not None
        assert marked["user_id"] == notifications[0]["user_id"]

        # Verify the read state persisted
        resp2 = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_client}"},
        )
        updated_notifications = resp2.json()
        updated = [n for n in updated_notifications if n["id"] == notification_id][0]
        assert updated["read_at"] is not None

    def test_mark_other_user_notification_returns_403(
        self, client, admin_token, tmp_path
    ):
        """Trying to mark another user's notification as read should return 403."""
        # Create scenario with notifications for two different users
        session = _create_session(client, admin_token, capacity=1, title="Other Test")

        token_a = _register_client(client, "alice_a@example.com")
        _add_credits(client, "alice_a@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        token_b = _register_client(client, "bob_b@example.com")
        _add_credits(client, "bob_b@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )

        # Cancel session to generate notifications for both
        client.put(
            f"/api/v1/sessions/{session['id']}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        # Get user A's notifications
        resp_a = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        notif_a_id = resp_a.json()[0]["id"]

        # User B tries to mark user A's notification as read -> 403
        mark_resp = client.put(
            f"/api/v1/users/me/notifications/{notif_a_id}/read",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert mark_resp.status_code == 403

    def test_mark_nonexistent_notification_returns_404(self, client, client_token):
        """Marking a notification that does not exist should return 404."""
        resp = client.put(
            "/api/v1/users/me/notifications/99999/read",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 404

    def test_mark_notification_read_requires_auth(self, client):
        """Unauthenticated access should return 401."""
        resp = client.put("/api/v1/users/me/notifications/1/read")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests: PUT /sessions/{id}/enroll_from_waitlist
# ---------------------------------------------------------------------------


class TestEnrollFromWaitlist:
    """Tests for PUT /sessions/{id}/enroll_from_waitlist (admin only)."""

    def test_enroll_from_waitlist_successful(
        self, client, admin_token, tmp_path
    ):
        """Admin promotes the first waitlisted user to confirmed successfully."""
        session = _create_session(client, admin_token, capacity=1, title="Enroll Test")

        # User A (confirmed, fills the only spot)
        token_a = _register_client(client, "conf_a@example.com")
        _add_credits(client, "conf_a@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # User B (waitlist)
        token_b = _register_client(client, "wait_b@example.com")
        _add_credits(client, "wait_b@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "waitlist"
        booking_b_id = resp_b.json()["id"]

        # Verify user B has 5 credits (no deduction for waitlist)
        me_b_before = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_before.json()["credits"] == 5

        # Admin promotes from waitlist
        enroll_resp = client.put(
            f"/api/v1/sessions/{session['id']}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert enroll_resp.status_code == 200
        result = enroll_resp.json()
        assert result["message"] == "User promoted from waitlist successfully"
        assert result["booking_id"] == booking_b_id

        # Verify user B's booking is now 'confirmed'
        bookings_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert bookings_b.json()[0]["status"] == "confirmed"

        # Verify user B now has 4 credits (1 deducted)
        me_b_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b_after.json()["credits"] == 4

        # Verify notification was created
        resp_notifications = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        notifs = resp_notifications.json()
        promotion_notifs = [
            n for n in notifs
            if "promoted from the waitlist" in n["message"].lower()
        ]
        assert len(promotion_notifs) >= 1

    def test_enroll_from_waitlist_no_waitlist_returns_400(
        self, client, admin_token
    ):
        """If there are no waitlisted users, the endpoint returns 400."""
        session = _create_session(client, admin_token, capacity=5, title="No Wait")

        # No one has booked this session yet
        enroll_resp = client.put(
            f"/api/v1/sessions/{session['id']}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert enroll_resp.status_code == 400
        assert "no waitlisted" in enroll_resp.json()["detail"].lower()

    def test_enroll_from_waitlist_requires_admin(self, client, client_token):
        """Non-admin users should get 403."""
        resp = client.put(
            "/api/v1/sessions/1/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_enroll_from_waitlist_unauthenticated_returns_401(self, client):
        """Unauthenticated access should return 401."""
        resp = client.put("/api/v1/sessions/1/enroll_from_waitlist")
        assert resp.status_code == 401

    def test_enroll_from_waitlist_nonexistent_session_returns_404(self, client, admin_token):
        """Enrolling from waitlist for a nonexistent session returns 404."""
        resp = client.put(
            "/api/v1/sessions/9999/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 404
