"""E2E tests for Booking API endpoints (Feature #7).

These tests verify the booking REST API from an end-user
perspective (real HTTP round-trips), complementing unit tests in
tests/test_bookings.py which cover business logic via TestClient.

What E2E tests add:
  - Real HTTP round-trips against a running server
  - Full booking lifecycle: create → list → cancel
  - Credit deduction and restoration verified via /auth/me
  - Waitlist flow when session is full
  - Error responses for auth, not-found, insufficient credits, duplicate
  - Visual evidence via JSON screenshots of API responses
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
    path = os.path.join(SCREENSHOT_DIR, f"feat7_{name}.json")
    # Handle case where response_data is a string (e.g. empty body)
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
    unique_email = f"e2e_admin_{int(time.time() * 1000)}@example.com"
    res = api.post(
        "/api/v1/auth/register",
        data=json.dumps({
            "name": "E2E Admin",
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
    unique_email = f"e2e_client_{int(time.time() * 1000)}{suffix}@example.com"
    res = api.post(
        "/api/v1/auth/register",
        data=json.dumps({
            "name": "E2E Client",
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
        "title": "Morning Yoga",
        "instructor": "Alice",
        "style": "Vinyasa",
        "starts_at": "2025-06-15T09:00:00",
        "duration_minutes": 60,
        "capacity": 20,
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


def _add_credits(email: str, amount: int = 5) -> None:
    """Directly modify data/users.json to give a user credits.
    
    This is necessary because there is no admin API endpoint to manage
    user credits. Modifying the data file is legitimate E2E test setup.
    """
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


def _get_me_credits(api, token: str) -> int:
    """Get current user's credits via GET /auth/me."""
    res = api.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status == 200, f"GET /me failed: {res.status} {res.text()}"
    return res.json()["credits"]


# ---------------------------------------------------------------------------
# Happy Path: Full confirmed booking lifecycle
# ---------------------------------------------------------------------------

class TestBookingHappyPath:
    """Full booking lifecycle as a client via real HTTP."""

    def test_full_confirmed_booking_lifecycle(self, api):
        """Register → add credits → book confirmed → list → cancel → verify."""
        # 1. Register admin and create a session
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        # 2. Register a client and give them credits
        client_token, client_email = _register_client(api, suffix="_happy")
        _add_credits(client_email, amount=3)

        # Verify credits are there
        credits_before = _get_me_credits(api, client_token)
        assert credits_before == 3, f"Expected 3 credits, got {credits_before}"

        # 3. Create a booking → should be confirmed
        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("happy_01_create_confirmed", res.json(), res.status)
        assert res.status == 201, f"Create booking failed: {res.status} {res.text()}"
        booking = res.json()
        assert booking["status"] == "confirmed"
        assert booking["session_id"] == session_id
        assert "id" in booking
        assert booking["session"] is not None
        assert booking["session"]["title"] == "Morning Yoga"
        booking_id = booking["id"]

        # 4. Verify credit was deducted
        credits_after_book = _get_me_credits(api, client_token)
        assert credits_after_book == 2, f"Expected 2 credits, got {credits_after_book}"

        # 5. List my bookings → should contain the booking
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_02_list_my_bookings", res.json(), res.status)
        assert res.status == 200
        bookings = res.json()
        assert len(bookings) == 1
        assert bookings[0]["id"] == booking_id
        assert bookings[0]["status"] == "confirmed"
        assert bookings[0]["session"] is not None

        # 6. Cancel the booking
        res = api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_03_cancel", {}, res.status)
        assert res.status == 204, f"Cancel failed: {res.status} {res.text()}"

        # 7. Verify credit restored
        credits_after_cancel = _get_me_credits(api, client_token)
        assert credits_after_cancel == 3, f"Expected 3 credits, got {credits_after_cancel}"

        # 8. Verify booking status is now cancelled
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_04_verify_cancelled", res.json(), res.status)
        assert res.status == 200
        assert res.json()[0]["status"] == "cancelled"

        # 9. Clean up: delete session
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_waitlist_when_session_full(self, api):
        """When session is full, booking goes to waitlist (no credit deduction)."""
        # 1. Register admin and create session with capacity=1
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1)
        session_id = session["id"]

        # 2. First client with credits fills the session
        token_a, email_a = _register_client(api, suffix="_filler")
        _add_credits(email_a, amount=5)
        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201, f"Client A booking failed: {res_a.status} {res_a.text()}"
        assert res_a.json()["status"] == "confirmed"
        booking_a_id = res_a.json()["id"]

        # 3. Second client tries to book → should get waitlist
        token_b, email_b = _register_client(api, suffix="_waiter")
        _add_credits(email_b, amount=5)
        res_b = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )
        api_screenshot("happy_05_waitlist_created", res_b.json(), res_b.status)
        assert res_b.status == 201, f"Client B booking failed: {res_b.status} {res_b.text()}"
        assert res_b.json()["status"] == "waitlist"
        booking_b_id = res_b.json()["id"]

        # 4. Client B's credits should NOT have been deducted
        credits_b = _get_me_credits(api, token_b)
        assert credits_b == 5, f"Expected 5 credits, got {credits_b}"

        # 5. Client B cancels their waitlist booking
        res = api.delete(
            f"/api/v1/bookings/{booking_b_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_06_waitlist_cancelled", {}, res.status)
        assert res.status == 204

        # 6. Client B credits still unchanged
        credits_b_after = _get_me_credits(api, token_b)
        assert credits_b_after == 5, f"Expected 5 credits, got {credits_b_after}"

        # 7. Clean up: cancel client A's booking, then delete session
        api.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_list_my_bookings_empty(self, api):
        """New client with no bookings should get an empty list."""
        client_token, _ = _register_client(api, suffix="_empty")
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_07_empty_list", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []


# ---------------------------------------------------------------------------
# Sad Paths: Authentication & authorization
# ---------------------------------------------------------------------------

class TestBookingAuthSadPaths:
    """Authentication and authorization error scenarios."""

    def test_create_booking_unauthenticated_returns_401(self, api):
        """POST /bookings without token → 401."""
        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": 1}),
            headers={"Content-Type": "application/json"},
        )
        api_screenshot("sad_01_unauth_create", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_list_bookings_unauthenticated_returns_401(self, api):
        """GET /bookings/me without token → 401."""
        res = api.get("/api/v1/bookings/me")
        api_screenshot("sad_02_unauth_list", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_cancel_booking_unauthenticated_returns_401(self, api):
        """DELETE /bookings/{id} without token → 401."""
        res = api.delete("/api/v1/bookings/1")
        api_screenshot("sad_03_unauth_cancel", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_cancel_another_users_booking_returns_403(self, api):
        """A client cannot cancel another client's booking."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)

        # Client A books
        token_a, email_a = _register_client(api, suffix="_owner")
        _add_credits(email_a, amount=5)
        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session["id"]}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201
        booking_id = res_a.json()["id"]

        # Client B tries to cancel Client A's booking
        token_b, _ = _register_client(api, suffix="_intruder")
        res = api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("sad_04_cancel_other_403", res.json(), res.status)
        assert res.status == 403, f"Expected 403, got {res.status}: {res.text()}"
        assert "another user" in res.json().get("detail", "").lower()

        # Clean up
        api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api.delete(
            f"/api/v1/sessions/{session['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Sad Paths: Business logic errors
# ---------------------------------------------------------------------------

class TestBookingBusinessSadPaths:
    """Business logic error scenarios for bookings."""

    def test_create_booking_no_credits_returns_402(self, api):
        """User with 0 credits booking a session with spots → 402."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)

        client_token, _ = _register_client(api, suffix="_poor")
        # Client has 0 credits (default)

        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session["id"]}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_05_no_credits_402", res.json(), res.status)
        assert res.status == 402, f"Expected 402, got {res.status}: {res.text()}"
        assert "credits" in res.json().get("detail", "").lower()

        # Clean up
        api.delete(
            f"/api/v1/sessions/{session['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_create_booking_nonexistent_session_returns_404(self, api):
        """Booking a session that doesn't exist → 404."""
        client_token, _ = _register_client(api, suffix="_ghost")

        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": 99999}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_06_nonexistent_session_404", res.json(), res.status)
        assert res.status == 404, f"Expected 404, got {res.status}: {res.text()}"
        assert "Session not found" in res.json().get("detail", "")

    def test_create_duplicate_active_booking_returns_400(self, api):
        """Booking the same session twice (while first is active) → 400."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)

        client_token, client_email = _register_client(api, suffix="_dup")
        _add_credits(client_email, amount=5)

        # First booking
        res1 = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session["id"]}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        assert res1.status == 201, f"First booking failed: {res1.status} {res1.text()}"
        booking_id = res1.json()["id"]

        # Second booking for same session
        res2 = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session["id"]}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_07_duplicate_400", res2.json(), res2.status)
        assert res2.status == 400, f"Expected 400, got {res2.status}: {res2.text()}"
        assert "already has an active booking" in res2.json().get("detail", "")

        # Clean up
        api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api.delete(
            f"/api/v1/sessions/{session['id']}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_cancel_nonexistent_booking_returns_404(self, api):
        """Cancelling a booking that doesn't exist → 404."""
        client_token, _ = _register_client(api, suffix="_nobook")

        res = api.delete(
            "/api/v1/bookings/99999",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("sad_08_cancel_nonexistent_404", res.json(), res.status)
        assert res.status == 404, f"Expected 404, got {res.status}: {res.text()}"
