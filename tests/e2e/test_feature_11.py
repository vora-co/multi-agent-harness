"""E2E tests for Client Pages: Schedule and MyBookings (Feature #11).

These tests verify the client-side journey through the /schedule and
/my-bookings pages from an end-user perspective (real HTTP round-trips).

Scenarios:
  - View schedule (list sessions and filter by style/date)
  - Book a session with available spots (confirmed booking)
  - Book a full session (waitlist, no credit deduction)
  - Cancel a booking (credit restored, status updated)
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
    path = os.path.join(SCREENSHOT_DIR, f"feat11_{name}.json")
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


def _get_me_credits(api, token: str) -> int:
    """Get current user's credits via GET /auth/me."""
    res = api.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status == 200, f"GET /me failed: {res.status} {res.text()}"
    return res.json()["credits"]


# ---------------------------------------------------------------------------
# Happy Path: Schedule page — view sessions with filters
# ---------------------------------------------------------------------------

class TestSchedulePage:
    """Client journey through the /schedule page: view sessions & filter."""

    def test_view_schedule_all_sessions(self, api):
        """A client can list all upcoming sessions on the schedule page."""
        # 1. Admin creates a few sessions
        admin_token = _register_admin(api)
        s1 = _create_session(api, admin_token, title="Yoga Flow", style="Vinyasa",
                             starts_at="2025-07-01T08:00:00")
        s2 = _create_session(api, admin_token, title="Power Pilates",
                             style="Pilates", starts_at="2025-07-02T10:00:00")
        s3 = _create_session(api, admin_token, title="Zen Meditation",
                             style="Meditation", starts_at="2025-07-03T12:00:00")

        # 2. Client registers and lists all sessions
        client_token, _ = _register_client(api, suffix="_schedule")

        res = api.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_01_schedule_all", res.json(), res.status)
        assert res.status == 200
        sessions = res.json()
        titles = {s["title"] for s in sessions}
        assert "Yoga Flow" in titles
        assert "Power Pilates" in titles
        assert "Zen Meditation" in titles

        # 3. Verify session data includes fields needed by the grid
        for s in sessions:
            assert "id" in s
            assert "title" in s
            assert "instructor" in s
            assert "style" in s
            assert "starts_at" in s
            assert "capacity" in s
            assert "enrolled" in s
            assert s["enrolled"] == 0, f"{s['title']} should have 0 enrolled"

        # Cleanup
        for sid in [s1["id"], s2["id"], s3["id"]]:
            api.delete(
                f"/api/v1/sessions/{sid}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    def test_filter_schedule_by_style(self, api):
        """A client filters the schedule grid by session style."""
        admin_token = _register_admin(api)
        _create_session(api, admin_token, title="Yoga Flow", style="Vinyasa",
                        starts_at="2025-08-01T08:00:00")
        _create_session(api, admin_token, title="Pilates Core",
                        style="Pilates", starts_at="2025-08-02T09:00:00")
        _create_session(api, admin_token, title="Yoga Nidra", style="Vinyasa",
                        starts_at="2025-08-03T10:00:00")

        client_token, _ = _register_client(api, suffix="_filter_style")

        # Filter by style=Vinyasa
        res = api.get(
            "/api/v1/sessions?style=Vinyasa",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_02_filter_style", res.json(), res.status)
        assert res.status == 200
        filtered = res.json()
        assert len(filtered) == 2, f"Expected 2 Vinyasa, got {len(filtered)}"
        for s in filtered:
            assert s["style"] == "Vinyasa"

        # Cleanup: get all sessions and delete
        all_res = api.get("/api/v1/sessions")
        for s in all_res.json():
            api.delete(
                f"/api/v1/sessions/{s['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )

    def test_filter_schedule_by_date(self, api):
        """A client filters the schedule grid by date."""
        admin_token = _register_admin(api)
        _create_session(api, admin_token, title="Morning Hatha",
                        style="Hatha", starts_at="2025-09-10T07:00:00")
        _create_session(api, admin_token, title="Evening Flow",
                        style="Vinyasa", starts_at="2025-09-11T18:00:00")

        client_token, _ = _register_client(api, suffix="_filter_date")

        # Filter by date=2025-09-10
        res = api.get(
            "/api/v1/sessions?date=2025-09-10",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_03_filter_date", res.json(), res.status)
        assert res.status == 200
        filtered = res.json()
        assert len(filtered) == 1
        assert filtered[0]["title"] == "Morning Hatha"

        # Cleanup
        all_res = api.get("/api/v1/sessions")
        for s in all_res.json():
            api.delete(
                f"/api/v1/sessions/{s['id']}",
                headers={"Authorization": f"Bearer {admin_token}"},
            )


# ---------------------------------------------------------------------------
# Happy Path: Reserve a session with available spots (boton Reservar)
# ---------------------------------------------------------------------------

class TestReserveWithSpot:
    """From the schedule page, client reserves a session that has spots."""

    def test_book_session_with_available_spots(self, api):
        """Client sees a session with spots -> clicks Reservar -> confirmed."""
        # 1. Admin creates a session with capacity=5
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=5)
        session_id = session["id"]

        # 2. Client registers and gets credits
        client_token, client_email = _register_client(api, suffix="_reserve")
        _add_credits(client_email, amount=3)

        credits_before = _get_me_credits(api, client_token)
        assert credits_before == 3

        # 3. Client views schedule -> sees session with enrolled=0 (< capacity)
        res = api.get(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert res.status == 200
        session_view = res.json()
        assert session_view["enrolled"] == 0
        assert session_view["capacity"] == 5
        assert session_view["enrolled"] < session_view["capacity"], \
            "Session should have available spots"

        # 4. Client clicks "Reservar" -> POST /bookings
        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("happy_04_reserve_confirmed", res.json(), res.status)
        assert res.status == 201, f"Reserve failed: {res.status} {res.text()}"
        booking = res.json()
        assert booking["status"] == "confirmed"
        assert booking["session_id"] == session_id
        booking_id = booking["id"]

        # 5. Credit deducted
        credits_after = _get_me_credits(api, client_token)
        assert credits_after == 2, f"Expected 2 credits, got {credits_after}"

        # 6. Session enrolled count updated
        res = api.get(f"/api/v1/sessions/{session_id}")
        assert res.status == 200
        assert res.json()["enrolled"] == 1

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
# Happy Path: Reserve a full session -> waitlist (badge 'Lista de espera')
# ---------------------------------------------------------------------------

class TestReserveFullSession:
    """When session is full, client gets waitlist badge."""

    def test_book_full_session_gets_waitlist(self, api):
        """Session with enrolled==capacity -> waitlist, no credit deducted."""
        # 1. Admin creates a session with capacity=1
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1,
                                  title="VIP Session")
        session_id = session["id"]

        # 2. First client fills the session
        token_a, email_a = _register_client(api, suffix="_filler")
        _add_credits(email_a, amount=3)
        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201
        assert res_a.json()["status"] == "confirmed"
        booking_a_id = res_a.json()["id"]

        # 3. Verify session is now full (enrolled == capacity)
        res = api.get(f"/api/v1/sessions/{session_id}")
        assert res.status == 200
        session_data = res.json()
        assert session_data["enrolled"] == 1
        assert session_data["capacity"] == 1
        assert session_data["enrolled"] >= session_data["capacity"], (
            "Session should be full -> frontend shows 'Lista de espera' badge"
        )

        # 4. Second client tries to book -> gets waitlist
        token_b, email_b = _register_client(api, suffix="_waiter")
        _add_credits(email_b, amount=3)
        credits_before = _get_me_credits(api, token_b)
        assert credits_before == 3

        res_b = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )
        api_screenshot("happy_05_full_waitlist", res_b.json(), res_b.status)
        assert res_b.status == 201
        assert res_b.json()["status"] == "waitlist"
        booking_b_id = res_b.json()["id"]

        # 5. Client B credits NOT deducted for waitlist
        credits_after = _get_me_credits(api, token_b)
        assert credits_after == 3, (
            f"Waitlist should not deduct credits, got {credits_after}"
        )

        # 6. Client B's my-bookings shows waitlist status
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_06_my_bookings_waitlist", res.json(), res.status)
        assert res.status == 200
        bookings = res.json()
        assert len(bookings) == 1
        assert bookings[0]["status"] == "waitlist"
        assert bookings[0]["session_id"] == session_id
        assert bookings[0]["session"]["title"] == "VIP Session"

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking_b_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Happy Path: MyBookings page — table with Cancel button and modal
# ---------------------------------------------------------------------------

class TestMyBookingsPage:
    """Client journey through /my-bookings: table, cancel with confirmation."""

    def test_list_my_bookings_with_session_details(self, api):
        """The my-bookings table shows booking + embedded session info."""
        # 1. Admin creates a session
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, title="Table Test Session",
                                  instructor="Master Yoda", style="Jedi",
                                  starts_at="2025-10-01T14:00:00")
        session_id = session["id"]

        # 2. Client books
        client_token, client_email = _register_client(api, suffix="_mytable")
        _add_credits(client_email, amount=2)
        res_book = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        assert res_book.status == 201
        booking_id = res_book.json()["id"]

        # 3. Client views /my-bookings
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_07_my_bookings_table", res.json(), res.status)
        assert res.status == 200
        bookings = res.json()
        assert len(bookings) == 1
        b = bookings[0]
        assert b["id"] == booking_id
        assert b["status"] == "confirmed"
        assert b["session"] is not None
        assert b["session"]["title"] == "Table Test Session"
        assert b["session"]["instructor"] == "Master Yoda"
        assert b["session"]["starts_at"] == "2025-10-01T14:00:00"

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_cancel_booking_from_my_bookings(self, api):
        """Client clicks Cancel -> modal confirmation -> booking cancelled."""
        # 1. Admin creates a session
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        # 2. Client books
        client_token, client_email = _register_client(api, suffix="_cancel")
        _add_credits(client_email, amount=3)
        res_book = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        assert res_book.status == 201
        booking_id = res_book.json()["id"]
        credits_before_cancel = _get_me_credits(api, client_token)
        assert credits_before_cancel == 2  # 3 - 1

        # 3. Client views /my-bookings -> sees the booking
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert res.status == 200
        assert len(res.json()) == 1
        assert res.json()[0]["id"] == booking_id

        # 4. Client clicks Cancel -> modal confirms -> DELETE /bookings/:id
        res = api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_08_cancel_confirmed", {}, res.status)
        assert res.status == 204

        # 5. Credits restored after cancellation
        credits_after_cancel = _get_me_credits(api, client_token)
        assert credits_after_cancel == 3, (
            f"Credits should be restored to 3, got {credits_after_cancel}"
        )

        # 6. Booking shows as cancelled in my-bookings
        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_09_cancelled_in_table", res.json(), res.status)
        assert res.status == 200
        bookings = res.json()
        assert len(bookings) == 1
        assert bookings[0]["status"] == "cancelled"
        assert bookings[0]["id"] == booking_id

        # 7. Session enrolled count decremented
        res = api.get(f"/api/v1/sessions/{session_id}")
        assert res.status == 200
        assert res.json()["enrolled"] == 0

        # Cleanup: delete session
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_my_bookings_empty_for_new_client(self, api):
        """A new client with no bookings sees an empty my-bookings table."""
        client_token, _ = _register_client(api, suffix="_empty_table")

        res = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("happy_10_empty_my_bookings", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []

    def test_cancel_waitlist_booking_no_credit_change(self, api):
        """Cancelling a waitlist booking does not affect credits."""
        # 1. Admin creates a full session (capacity=1)
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1,
                                  title="Full Cancel")
        session_id = session["id"]

        # 2. First client fills it
        token_a, email_a = _register_client(api, suffix="_fill_cancel")
        _add_credits(email_a, amount=3)
        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201
        booking_a_id = res_a.json()["id"]

        # 3. Second client gets waitlist
        token_b, email_b = _register_client(api, suffix="_wait_cancel")
        _add_credits(email_b, amount=5)
        res_b = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )
        assert res_b.status == 201
        assert res_b.json()["status"] == "waitlist"
        booking_b_id = res_b.json()["id"]
        credits_before = _get_me_credits(api, token_b)
        assert credits_before == 5

        # 4. Client B cancels waitlist booking from my-bookings
        res = api.delete(
            f"/api/v1/bookings/{booking_b_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_11_cancel_waitlist", {}, res.status)
        assert res.status == 204

        # 5. Credits unchanged (was never deducted for waitlist)
        credits_after = _get_me_credits(api, token_b)
        assert credits_after == 5, (
            f"Waitlist cancel should not change credits, got {credits_after}"
        )

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Sad Paths: Client page errors
# ---------------------------------------------------------------------------

class TestClientPageSadPaths:
    """Error scenarios for client page interactions."""

    def test_cannot_book_without_credits(self, api):
        """Client with 0 credits clicking Reservar -> 402 Payment Required."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        # Client with 0 credits (default)
        client_token, _ = _register_client(api, suffix="_nocredits")

        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_01_no_credits_402", res.json(), res.status)
        assert res.status == 402

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_cannot_book_nonexistent_session(self, api):
        """Client tries to book a session that doesn't exist -> 404."""
        client_token, _ = _register_client(api, suffix="_ghostbook")

        res = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": 99999}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_02_nonexistent_404", res.json(), res.status)
        assert res.status == 404

    def test_cannot_cancel_another_users_booking(self, api):
        """Client A cannot cancel Client B's booking -> 403."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        # Client A books
        token_a, email_a = _register_client(api, suffix="_owner_cancel")
        _add_credits(email_a, amount=3)
        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201
        booking_id = res_a.json()["id"]

        # Client B tries to cancel Client A's booking
        token_b, _ = _register_client(api, suffix="_intruder_cancel")
        res = api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("sad_03_cancel_other_403", res.json(), res.status)
        assert res.status == 403
        assert "another user" in res.json().get("detail", "").lower()

        # Cleanup
        api.delete(
            f"/api/v1/bookings/{booking_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_unauthenticated_cannot_view_my_bookings(self, api):
        """Unauthenticated access to /my-bookings -> 401."""
        res = api.get("/api/v1/bookings/me")
        api_screenshot("sad_04_unauth_my_bookings", res.json(), res.status)
        assert res.status == 401
