"""Tests for the Booking API endpoints."""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.repositories.bookings import BookingRepository
from src.repositories.sessions import SessionRepository
from src.repositories.users import UserRepository
from src.models.booking import Booking
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
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _register_client(client: TestClient, email="client@example.com") -> str:
    """Register a client user and return the access token."""
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
    """Create a session and return its data."""
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
    """Directly modify the user to have credits."""
    repo = UserRepository(data_dir=str(tmp_path))
    user = repo.find_by_email(user_email)
    user.credits = amount
    repo.save_one(user)


def _patch_notify_user_for_tmp(monkeypatch, tmp_path):
    """Patch notify_user so notifications are written to tmp_path."""
    import src.core as core_mod

    def patched_notify_user(user_id: int, message: str) -> None:
        from src.repositories.notifications import NotificationRepository
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
    """Return a valid admin token."""
    return _register_admin(client)


@pytest.fixture
def client_token(client):
    """Return a valid client token."""
    return _register_client(client)


@pytest.fixture
def session_data(client, admin_token):
    """Return a created session's data."""
    return _create_session(client, admin_token)


# ---------------------------------------------------------------------------
# POST /bookings — successful booking
# ---------------------------------------------------------------------------


class TestCreateBookingSuccess:
    """Tests for successful booking creation."""

    def test_create_booking_confirmed(self, client, admin_token, session_data, tmp_path):
        """A user with credits should get a confirmed booking and have credits deducted."""
        # Register a client and give them credits
        token = _register_client(client, "rich@example.com")
        _add_credits(client, "rich@example.com", tmp_path, amount=3)

        # Create booking
        resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.json()
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["session_id"] == session_data["id"]
        assert "id" in data
        assert data["session"] is not None
        assert data["session"]["enrolled"] == 1

        # Verify credit was deducted
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.json()["credits"] == 2


# ---------------------------------------------------------------------------
# POST /bookings — no credits → 402
# ---------------------------------------------------------------------------


class TestCreateBookingNoCredits:
    """Tests for booking creation with insufficient credits."""

    def test_create_booking_no_credits_returns_402(
        self, client, admin_token, session_data
    ):
        """A user with 0 credits should get 402 when session has spots."""
        token = _register_client(client, "poor@example.com")

        resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 402
        assert "credits" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# POST /bookings — session full → waitlist
# ---------------------------------------------------------------------------


class TestCreateBookingWaitlist:
    """Tests for booking when session is full."""

    def test_create_booking_waitlist_when_session_full(
        self, client, admin_token, tmp_path
    ):
        """When session is full, new booking goes to waitlist (no credit deduction)."""
        # Create a session with capacity=1
        session = _create_session(client, admin_token, capacity=1)

        # First user books (should be confirmed)
        token_a = _register_client(client, "user_a@example.com")
        _add_credits(client, "user_a@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        assert resp_a.json()["status"] == "confirmed"

        # Second user with credits tries to book (session should be full)
        token_b = _register_client(client, "user_b@example.com")
        _add_credits(client, "user_b@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "waitlist"

        # Second user's credits should NOT have been deducted
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_resp.json()["credits"] == 5


# ---------------------------------------------------------------------------
# POST /bookings — edge cases
# ---------------------------------------------------------------------------


class TestCreateBookingEdgeCases:
    """Edge case tests for booking creation."""

    def test_create_booking_nonexistent_session_returns_404(
        self, client, client_token
    ):
        """Booking a nonexistent session should return 404."""
        resp = client.post(
            "/api/v1/bookings",
            json={"session_id": 9999},
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["detail"]

    def test_create_duplicate_active_booking_returns_400(
        self, client, admin_token, session_data, tmp_path
    ):
        """Booking the same session twice while first booking is active should return 400."""
        token = _register_client(client, "dup@example.com")
        _add_credits(client, "dup@example.com", tmp_path, amount=5)

        # First booking
        resp1 = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp1.status_code == 201

        # Second booking for same session
        resp2 = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 400
        assert "already has an active booking" in resp2.json()["detail"]

    def test_create_booking_unauthenticated_returns_401(self, client, session_data):
        """Unauthenticated request should return 401."""
        resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /bookings/me
# ---------------------------------------------------------------------------


class TestListMyBookings:
    """Tests for listing the authenticated user's bookings."""

    def test_list_my_bookings(self, client, admin_token, session_data, tmp_path):
        """Should list all bookings for the authenticated user with session details."""
        token = _register_client(client, "lister@example.com")
        _add_credits(client, "lister@example.com", tmp_path, amount=5)

        # Book a session
        client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )

        resp = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["status"] == "confirmed"
        assert data[0]["session_id"] == session_data["id"]
        assert data[0]["session"] is not None
        assert data[0]["session"]["title"] == "Morning Yoga"

    def test_list_my_bookings_empty(self, client, client_token):
        """Should return empty list when user has no bookings."""
        resp = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_my_bookings_unauthenticated_returns_401(self, client):
        """Unauthenticated request should return 401."""
        resp = client.get("/api/v1/bookings/me")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /bookings/{id}
# ---------------------------------------------------------------------------


class TestCancelBooking:
    """Tests for cancelling (deleting) bookings."""

    def test_cancel_own_confirmed_booking(
        self, client, admin_token, session_data, tmp_path
    ):
        """Cancelling own confirmed booking should return 204, restore credit, and
        decrement enrolled."""
        token = _register_client(client, "canceller@example.com")
        _add_credits(client, "canceller@example.com", tmp_path, amount=3)

        # Book a session
        create_resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert create_resp.status_code == 201
        booking_id = create_resp.json()["id"]

        # Verify credits were deducted
        me_before = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_before.json()["credits"] == 2

        # Cancel the booking
        resp = client.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 204

        # Verify credit restored
        me_after = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_after.json()["credits"] == 3

        # Verify enrolled decremented on session
        session_resp = client.get(f"/api/v1/sessions/{session_data['id']}")
        assert session_resp.json()["enrolled"] == 0

        # Verify booking status is cancelled
        bookings_resp = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert bookings_resp.json()[0]["status"] == "cancelled"

    def test_cancel_own_waitlist_booking(
        self, client, admin_token, tmp_path
    ):
        """Cancelling own waitlist booking should succeed without affecting credits."""
        # Create session with capacity=1
        session = _create_session(client, admin_token, capacity=1)

        # First user fills the session
        token_a = _register_client(client, "filler@example.com")
        _add_credits(client, "filler@example.com", tmp_path, amount=5)
        client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )

        # Second user goes to waitlist
        token_b = _register_client(client, "waiter@example.com")
        _add_credits(client, "waiter@example.com", tmp_path, amount=5)
        create_resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert create_resp.json()["status"] == "waitlist"
        booking_id = create_resp.json()["id"]

        # Cancel the waitlist booking
        resp = client.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 204

        # Credits should remain unchanged (never deducted for waitlist)
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_resp.json()["credits"] == 5

    def test_cancel_another_users_booking_returns_403(
        self, client, admin_token, session_data, tmp_path
    ):
        """Cancelling another user's booking should return 403."""
        # User A books
        token_a = _register_client(client, "owner@example.com")
        _add_credits(client, "owner@example.com", tmp_path, amount=5)
        create_resp = client.post(
            "/api/v1/bookings",
            json={"session_id": session_data["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        booking_id = create_resp.json()["id"]

        # User B tries to cancel User A's booking
        token_b = _register_client(client, "intruder@example.com")
        resp = client.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 403
        assert "another user" in resp.json()["detail"].lower()

    def test_cancel_nonexistent_booking_returns_404(self, client, client_token):
        """Cancelling a nonexistent booking should return 404."""
        resp = client.delete(
            "/api/v1/bookings/9999",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 404

    def test_cancel_booking_unauthenticated_returns_401(self, client):
        """Unauthenticated cancel request should return 401."""
        resp = client.delete("/api/v1/bookings/1")
        assert resp.status_code == 401

    # -----------------------------------------------------------------------
    # Feature #8: auto-promotion from waitlist on confirmed cancellation
    # -----------------------------------------------------------------------

    def test_cancel_confirmed_promotes_first_waitlisted_with_credits(
        self, client, admin_token, monkeypatch, tmp_path
    ):
        """Cancelling a confirmed booking promotes the first waitlisted user
        with credits to confirmed."""
        _patch_notify_user_for_tmp(monkeypatch, tmp_path)

        # Create a session with capacity=1
        session = _create_session(client, admin_token, capacity=1)

        # User A (confirmed, fills the only spot)
        token_a = _register_client(client, "user_a8@example.com")
        _add_credits(client, "user_a8@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        assert resp_a.json()["status"] == "confirmed"
        booking_a_id = resp_a.json()["id"]

        # User B (waitlist, has credits)
        token_b = _register_client(client, "user_b8@example.com")
        _add_credits(client, "user_b8@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "waitlist"
        booking_b_id = resp_b.json()["id"]

        # User A cancels → User B should be promoted
        resp = client.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 204

        # Booking A: cancelled
        booking_a = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert booking_a.json()[0]["status"] == "cancelled"

        # Booking B: promoted to confirmed
        booking_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert booking_b.json()[0]["status"] == "confirmed"

        # User A credits: restored to 5 (had 4 after booking, now 5)
        me_a = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert me_a.json()["credits"] == 5

        # User B credits: 4 (had 5, deducted 1 on promotion)
        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b.json()["credits"] == 4

        # Session enrolled: 1 (A left → 0, B entered → 1)
        session_resp = client.get(f"/api/v1/sessions/{session['id']}")
        assert session_resp.json()["enrolled"] == 1

        # Notification for User B about promotion
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

    def test_cancel_confirmed_skips_waitlisted_without_credits_and_promotes_next(
        self, client, admin_token, monkeypatch, tmp_path
    ):
        """When the first waitlisted user has no credits, skip them and
        promote the next eligible user."""
        _patch_notify_user_for_tmp(monkeypatch, tmp_path)

        # Create a session with capacity=1
        session = _create_session(client, admin_token, capacity=1)

        # User A (confirmed)
        token_a = _register_client(client, "conf_skip@example.com")
        _add_credits(client, "conf_skip@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        booking_a_id = resp_a.json()["id"]

        # User B (waitlist, 0 credits — first in queue)
        token_b = _register_client(client, "poor_wait@example.com")
        _add_credits(client, "poor_wait@example.com", tmp_path, amount=0)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "waitlist"
        booking_b_id = resp_b.json()["id"]

        # User C (waitlist, has credits — second in queue)
        token_c = _register_client(client, "rich_wait@example.com")
        _add_credits(client, "rich_wait@example.com", tmp_path, amount=5)
        resp_c = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert resp_c.status_code == 201
        assert resp_c.json()["status"] == "waitlist"
        booking_c_id = resp_c.json()["id"]

        # User A cancels
        resp = client.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp.status_code == 204

        # Booking A: cancelled
        booking_a = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert booking_a.json()[0]["status"] == "cancelled"

        # Booking B: still waitlist (skipped, no credits)
        booking_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert booking_b.json()[0]["status"] == "waitlist"

        # Booking C: promoted to confirmed
        booking_c = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert booking_c.json()[0]["status"] == "confirmed"

        # User B credits: still 0
        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b.json()["credits"] == 0

        # User C credits: 4 (was 5, deducted 1)
        me_c = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert me_c.json()["credits"] == 4

        # Session enrolled: 1
        session_resp = client.get(f"/api/v1/sessions/{session['id']}")
        assert session_resp.json()["enrolled"] == 1

        # Notification for User C (promoted), not User B
        resp_notifications_c = client.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        notifs_c = resp_notifications_c.json()
        promotion_c = [
            n for n in notifs_c
            if "promoted from the waitlist" in n["message"].lower()
        ]
        assert len(promotion_c) >= 1

    def test_cancel_waitlist_does_not_trigger_promotion(
        self, client, admin_token, monkeypatch, tmp_path
    ):
        """Cancelling a waitlist booking should not trigger auto-promotion."""
        _patch_notify_user_for_tmp(monkeypatch, tmp_path)

        # Create a session with capacity=1
        session = _create_session(client, admin_token, capacity=1)

        # User A (confirmed)
        token_a = _register_client(client, "conf_stay@example.com")
        _add_credits(client, "conf_stay@example.com", tmp_path, amount=5)
        resp_a = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert resp_a.status_code == 201
        assert resp_a.json()["status"] == "confirmed"
        booking_a_id = resp_a.json()["id"]

        # User B (waitlist)
        token_b = _register_client(client, "wait_cancel@example.com")
        _add_credits(client, "wait_cancel@example.com", tmp_path, amount=5)
        resp_b = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp_b.status_code == 201
        assert resp_b.json()["status"] == "waitlist"
        booking_b_id = resp_b.json()["id"]

        # User C (waitlist)
        token_c = _register_client(client, "wait_stay@example.com")
        _add_credits(client, "wait_stay@example.com", tmp_path, amount=5)
        resp_c = client.post(
            "/api/v1/bookings",
            json={"session_id": session["id"]},
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert resp_c.status_code == 201
        assert resp_c.json()["status"] == "waitlist"

        # User B cancels their waitlist booking
        resp = client.delete(
            f"/api/v1/bookings/{booking_b_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 204

        # Booking B: cancelled
        booking_b = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert booking_b.json()[0]["status"] == "cancelled"

        # Booking A: still confirmed
        booking_a = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert booking_a.json()[0]["status"] == "confirmed"

        # Booking C: still waitlist (no promotion triggered)
        booking_c = client.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert booking_c.json()[0]["status"] == "waitlist"

        # User A credits: 4 (unchanged — 5 minus 1 for confirmed booking)
        me_a = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert me_a.json()["credits"] == 4

        # User B credits: 5 (never deducted for waitlist)
        me_b = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert me_b.json()["credits"] == 5

        # User C credits: 5 (unchanged)
        me_c = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token_c}"},
        )
        assert me_c.json()["credits"] == 5

        # Session enrolled: 1 (unchanged)
        session_resp = client.get(f"/api/v1/sessions/{session['id']}")
        assert session_resp.json()["enrolled"] == 1
