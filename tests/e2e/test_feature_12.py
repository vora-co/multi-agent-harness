"""E2E tests for Admin Panel (Feature #12).

These tests verify the admin panel's core functionality via API round-trips:
  - Admin creates a session (CRUD via API)
  - Client access to admin endpoints is denied (403)
  - Admin adds credits to a user
  - Admin lists users
  - Admin lists session attendees
"""

import os
import json
import time
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def api_screenshot(name: str, response_data, status: int):
    """Save API response as JSON screenshot for evidence."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat12_{name}.json")
    if isinstance(response_data, str):
        try:
            response_data = json.loads(response_data) if response_data else {}
        except json.JSONDecodeError:
            response_data = {"raw": response_data}
    with open(path, "w") as f:
        json.dump({"status": status, "body": response_data}, f, indent=2)
    return path


@pytest.fixture
def api(playwright):
    """Provide a Playwright APIRequestContext pointed at BASE_URL."""
    request_context = playwright.request.new_context(base_url=BASE_URL)
    yield request_context
    request_context.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_admin(api) -> str:
    """Register a fresh admin user and return the access token."""
    unique_email = f"e2e_admin12_{int(time.time() * 1000)}@example.com"
    res = api.post(
        "/api/v1/auth/register",
        data=json.dumps({
            "name": "E2E Admin12",
            "email": unique_email,
            "password": "AdminPass123!",
            "role": "admin",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert res.status == 200, f"Admin registration failed: {res.status} {res.text()}"
    return res.json()["access_token"]


def _register_client(api, suffix="") -> tuple:
    """Register a fresh client user and return (token, email)."""
    unique_email = f"e2e_client12_{int(time.time() * 1000)}{suffix}@example.com"
    res = api.post(
        "/api/v1/auth/register",
        data=json.dumps({
            "name": "E2E Client12",
            "email": unique_email,
            "password": "ClientPass123!",
            "role": "client",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert res.status == 200, f"Client registration failed: {res.status} {res.text()}"
    return res.json()["access_token"], unique_email


def _create_session(api, admin_token: str, **overrides) -> dict:
    """Create a session (admin) and return its data."""
    payload = {
        "title": "Admin Test Session",
        "instructor": "Bob Admin",
        "style": "Hatha",
        "starts_at": "2025-12-01T10:00:00",
        "duration_minutes": 60,
        "capacity": 15,
    }
    payload.update(overrides)
    res = api.post(
        "/api/v1/sessions",
        data=json.dumps(payload),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert res.status == 201, f"Session creation failed: {res.status} {res.text()}"
    return res.json()


def _add_credits_direct(email: str, amount: int = 5) -> None:
    """Directly modify data/users.json to give a user credits."""
    users_path = os.path.join(DATA_DIR, "users.json")
    with open(users_path, "r") as f:
        users = json.load(f)

    found = False
    for user in users:
        if user.get("email") == email:
            user["credits"] = amount
            found = True
            break

    if not found:
        raise RuntimeError(f"User with email {email} not found in users.json")

    with open(users_path, "w") as f:
        json.dump(users, f, indent=2)


# ---------------------------------------------------------------------------
# Test: Admin CRUD on sessions
# ---------------------------------------------------------------------------

class TestAdminSessionCRUD:
    """Admin creates, reads, updates, and deletes sessions."""

    def test_create_session_as_admin(self, api):
        """An admin can create a new session via POST /api/v1/sessions."""
        admin_token = _register_admin(api)

        session = _create_session(
            api, admin_token,
            title="Monday Vinyasa",
            instructor="Alice",
            style="Vinyasa",
            starts_at="2025-12-15T08:00:00",
            duration_minutes=45,
            capacity=10,
        )
        api_screenshot("admin_01_create_session", session, 201)

        assert session["title"] == "Monday Vinyasa"
        assert session["instructor"] == "Alice"
        assert session["style"] == "Vinyasa"
        assert session["capacity"] == 10
        assert session["enrolled"] == 0
        assert "id" in session
        session_id = session["id"]

        # Verify session appears in listing
        res = api.get("/api/v1/sessions")
        assert res.status == 200
        sessions = res.json()
        titles = {s["title"] for s in sessions}
        assert "Monday Vinyasa" in titles

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_update_session_as_admin(self, api):
        """An admin can update an existing session via PUT."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Original Title")
        session_id = session["id"]

        update_payload = {
            "title": "Updated Title",
            "instructor": "Updated Instructor",
            "style": "Bikram",
            "starts_at": "2025-12-20T14:00:00",
            "duration_minutes": 90,
            "capacity": 25,
        }
        res = api.put(
            f"/api/v1/sessions/{session_id}",
            data=json.dumps(update_payload),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("admin_02_update_session", res.json(), res.status)
        assert res.status == 200
        updated = res.json()
        assert updated["title"] == "Updated Title"
        assert updated["instructor"] == "Updated Instructor"
        assert updated["style"] == "Bikram"
        assert updated["duration_minutes"] == 90
        assert updated["capacity"] == 25

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_delete_session_as_admin(self, api):
        """An admin can delete a session with zero enrolled via DELETE."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Session To Delete")
        session_id = session["id"]

        res = api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_03_delete_session", {}, res.status)
        assert res.status == 204

        # Verify deleted
        res = api.get(f"/api/v1/sessions/{session_id}")
        assert res.status == 404

    def test_cannot_delete_session_with_enrolled(self, api):
        """An admin cannot delete a session that has enrolled participants (409)."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Has Enrolled", capacity=5)
        session_id = session["id"]

        # Client books the session
        client_token, client_email = _register_client(api, suffix="_del_enrolled")
        _add_credits_direct(client_email, 5)

        res_book = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        assert res_book.status == 201, f"Booking failed: {res_book.status} {res_book.text()}"
        booking_id = res_book.json()["id"]

        # Try to delete as admin — should fail with 409
        res = api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_04_delete_enrolled_409", res.json(), res.status)
        assert res.status == 409
        assert "enrolled" in res.json().get("detail", "").lower()

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Test: Client access to admin endpoints is denied
# ---------------------------------------------------------------------------

class TestClientAccessAdminDenied:
    """A regular client cannot access admin-only API endpoints."""

    def test_client_cannot_list_users(self, api):
        """Client GET /api/v1/users returns 403 Forbidden."""
        client_token, _ = _register_client(api, suffix="_users403")

        res = api.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("admin_05_client_users_403", res.json(), res.status)
        assert res.status == 403
        assert "admin" in res.json().get("detail", "").lower()

    def test_client_cannot_add_credits(self, api):
        """Client PUT /api/v1/users/{id}/credits returns 403 Forbidden."""
        client_token, _ = _register_client(api, suffix="_credits403")

        res = api.put(
            "/api/v1/users/1/credits",
            data=json.dumps({"credits": 10}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("admin_06_client_credits_403", res.json(), res.status)
        assert res.status == 403
        assert "admin" in res.json().get("detail", "").lower()

    def test_client_cannot_view_attendees(self, api):
        """Client GET /api/v1/sessions/{id}/attendees returns 403 Forbidden."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Attendee Session")
        session_id = session["id"]

        client_token, _ = _register_client(api, suffix="_attend403")

        res = api.get(
            f"/api/v1/sessions/{session_id}/attendees",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("admin_07_client_attendees_403", res.json(), res.status)
        assert res.status == 403
        assert "admin" in res.json().get("detail", "").lower()

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_unauthenticated_cannot_access_admin_endpoints(self, api):
        """No token → 401 for admin endpoints."""
        res = api.get("/api/v1/users")
        api_screenshot("admin_08_unauth_users_401", res.json(), res.status)
        assert res.status == 401


# ---------------------------------------------------------------------------
# Test: Admin adds credits to a user
# ---------------------------------------------------------------------------

class TestAdminAddCredits:
    """Admin can add credits to any user."""

    def test_add_credits_to_user(self, api):
        """Admin adds credits to a client user successfully."""
        admin_token = _register_admin(api)
        client_token, client_email = _register_client(api, suffix="_addcred")

        # Get user list to find the client ID
        res = api.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status == 200
        users = res.json()
        client_user = next((u for u in users if u["email"] == client_email), None)
        assert client_user is not None, "Client user not found in admin user list"
        client_id = client_user["id"]
        original_credits = client_user["credits"]

        # Admin adds 7 credits
        res = api.put(
            f"/api/v1/users/{client_id}/credits",
            data=json.dumps({"credits": 7}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("admin_09_add_credits_success", res.json(), res.status)
        assert res.status == 200
        updated_user = res.json()
        assert updated_user["credits"] == original_credits + 7
        assert updated_user["id"] == client_id
        assert updated_user["email"] == client_email
        # Verify password_hash is excluded
        assert "password_hash" not in updated_user

        # Verify via GET /auth/me for the client
        res_me = api.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert res_me.status == 200
        assert res_me.json()["credits"] == original_credits + 7

    def test_add_credits_to_nonexistent_user(self, api):
        """Admin adding credits to non-existent user returns 404."""
        admin_token = _register_admin(api)

        res = api.put(
            "/api/v1/users/99999/credits",
            data=json.dumps({"credits": 5}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("admin_10_add_credits_404", res.json(), res.status)
        assert res.status == 404

    def test_add_credits_invalid_amount(self, api):
        """Admin adding zero or negative credits returns 422."""
        admin_token = _register_admin(api)
        client_token, client_email = _register_client(api, suffix="_badcred")

        res = api.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        users = res.json()
        client_user = next((u for u in users if u["email"] == client_email), None)
        client_id = client_user["id"]

        # Negative
        res = api.put(
            f"/api/v1/users/{client_id}/credits",
            data=json.dumps({"credits": -3}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("admin_11_credits_negative_422", res.json(), res.status)
        assert res.status == 422

        # Zero
        res = api.put(
            f"/api/v1/users/{client_id}/credits",
            data=json.dumps({"credits": 0}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        assert res.status == 422

    def test_admin_can_list_all_users(self, api):
        """Admin GET /api/v1/users returns all users without password_hash."""
        admin_token = _register_admin(api)

        res = api.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_12_list_users", res.json(), res.status)
        assert res.status == 200
        users = res.json()
        assert isinstance(users, list)
        assert len(users) > 0, "There should be at least one user (the admin)"

        for u in users:
            assert "id" in u
            assert "name" in u
            assert "email" in u
            assert "credits" in u
            assert "role" in u
            assert "created_at" in u
            assert "password_hash" not in u, "password_hash must be excluded"


# ---------------------------------------------------------------------------
# Test: Admin lists session attendees
# ---------------------------------------------------------------------------

class TestAdminListAttendees:
    """Admin can view attendees of a session."""

    def test_list_attendees_empty_session(self, api):
        """Admin lists attendees for a session with no bookings."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="No Attendees")
        session_id = session["id"]

        res = api.get(
            f"/api/v1/sessions/{session_id}/attendees",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_13_empty_attendees", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_list_attendees_with_bookings(self, api):
        """Admin lists attendees after some clients have booked."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Has Attendees", capacity=5)
        session_id = session["id"]

        # Register two clients and book
        client1_token, client1_email = _register_client(api, suffix="_attend1")
        client2_token, client2_email = _register_client(api, suffix="_attend2")
        _add_credits_direct(client1_email, 3)
        _add_credits_direct(client2_email, 3)

        # Book both
        res1 = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client1_token}",
            },
        )
        assert res1.status == 201
        booking1_id = res1.json()["id"]

        res2 = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client2_token}",
            },
        )
        assert res2.status == 201
        booking2_id = res2.json()["id"]

        # Admin lists attendees
        res = api.get(
            f"/api/v1/sessions/{session_id}/attendees",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_14_attendees_with_bookings", res.json(), res.status)
        assert res.status == 200
        attendees = res.json()
        assert len(attendees) == 2

        emails = {a["user_email"] for a in attendees}
        assert client1_email in emails
        assert client2_email in emails

        for a in attendees:
            assert "booking_id" in a
            assert "user_id" in a
            assert "user_name" in a
            assert "user_email" in a
            assert "status" in a
            assert a["status"] == "confirmed"
            assert "created_at" in a

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking1_id}",
            headers={"Authorization": f"Bearer {client1_token}"},
        )
        api.delete(
            f"/api/v1/bookings/{booking2_id}",
            headers={"Authorization": f"Bearer {client2_token}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_list_attendees_nonexistent_session(self, api):
        """Admin requesting attendees for non-existent session returns 404."""
        admin_token = _register_admin(api)

        res = api.get(
            "/api/v1/sessions/99999/attendees",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("admin_15_attendees_404", res.json(), res.status)
        assert res.status == 404
