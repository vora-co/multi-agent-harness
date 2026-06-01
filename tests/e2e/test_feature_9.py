"""E2E tests for Waitlist Promotion & Session Cancellation (Feature #9).

These tests verify the waitlist auto-promotion on booking cancel and the
admin session cancellation endpoint from an end-user perspective (real
HTTP round-trips against a running server), complementing unit tests in
tests/test_feature9.py which cover business logic via TestClient.

What E2E tests add:
  - Real HTTP round-trips against a running server
  - Full waitlist promotion lifecycle: book → waitlist → cancel → promote
  - Full session cancellation lifecycle with credit refund verification
  - Notification file verification (notifications.json)
  - Auth protection on PUT /sessions/{id}/cancel (admin-only)
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
    path = os.path.join(SCREENSHOT_DIR, f"feat9_{name}.json")
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


def _read_notifications() -> list:
    """Read notifications from data/notifications.json. Returns [] if missing."""
    path = os.path.join(DATA_DIR, "notifications.json")
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Happy Path: Waitlist promotion on booking cancel
# ---------------------------------------------------------------------------


class TestWaitlistPromotionE2E:
    """E2E tests verifying waitlist auto-promotion when a confirmed
    booking is cancelled."""

    def test_cancel_confirmed_promotes_waitlist_user(self, api):
        """Happy path: cancel a confirmed booking → waitlisted user
        gets promoted to confirmed, credit deducted, notification sent."""
        # 1. Register admin and create a session with capacity=1
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="Tiny Class")
        session_id = session["id"]

        # 2. User A books and gets confirmed (only spot)
        token_a, email_a = _register_client(api, suffix="_a")
        _add_credits(email_a, amount=5)

        # Verify credits
        credits_a_before = _get_me_credits(api, token_a)
        assert credits_a_before == 5

        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201, f"User A booking failed: {res_a.status} {res_a.text()}"
        booking_a = res_a.json()
        assert booking_a["status"] == "confirmed", f"Expected confirmed, got {booking_a['status']}"
        booking_a_id = booking_a["id"]

        # Credit deducted
        credits_a_after_book = _get_me_credits(api, token_a)
        assert credits_a_after_book == 4, f"Expected 4 credits, got {credits_a_after_book}"

        # 3. User B books → goes to waitlist (session full)
        token_b, email_b = _register_client(api, suffix="_b")
        _add_credits(email_b, amount=5)

        credits_b_before = _get_me_credits(api, token_b)
        assert credits_b_before == 5

        res_b = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )
        api_screenshot("happy_01_waitlist_created", res_b.json(), res_b.status)
        assert res_b.status == 201, f"User B booking failed: {res_b.status} {res_b.text()}"
        booking_b = res_b.json()
        assert booking_b["status"] == "waitlist", f"Expected waitlist, got {booking_b['status']}"
        booking_b_id = booking_b["id"]

        # User B credits unchanged (waitlist = no deduction)
        credits_b_after_waitlist = _get_me_credits(api, token_b)
        assert credits_b_after_waitlist == 5, f"Expected 5 credits, got {credits_b_after_waitlist}"

        # 4. User A cancels their booking
        cancel_res = api.delete(
            f"/api/v1/bookings/{booking_a_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api_screenshot("happy_02_booking_cancelled", {}, cancel_res.status)
        assert cancel_res.status == 204, f"Cancel failed: {cancel_res.status} {cancel_res.text()}"

        # 5. User A gets their credit back
        credits_a_after_cancel = _get_me_credits(api, token_a)
        assert credits_a_after_cancel == 5, f"Expected 5 credits restored, got {credits_a_after_cancel}"

        # 6. User B should now be confirmed (promoted from waitlist)
        res_b_list = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_03_promoted_to_confirmed", res_b_list.json(), res_b_list.status)
        assert res_b_list.status == 200
        bookings_b = res_b_list.json()
        assert len(bookings_b) == 1
        assert bookings_b[0]["status"] == "confirmed", \
            f"Expected promoted to confirmed, got {bookings_b[0]['status']}"

        # 7. User B credit deducted (1 credit for promotion)
        credits_b_after_promotion = _get_me_credits(api, token_b)
        assert credits_b_after_promotion == 4, \
            f"Expected 4 credits after promotion, got {credits_b_after_promotion}"

        # 8. Verify notification file was created with promotion message
        notifications = _read_notifications()
        api_screenshot("happy_04_notifications", notifications, 200)
        promotion_msgs = [
            n for n in notifications
            if "promoted from the waitlist" in n.get("message", "").lower()
        ]
        assert len(promotion_msgs) >= 1, \
            f"Expected at least 1 promotion notification, got {len(promotion_msgs)}: {notifications}"

        # 9. Clean up
        api.delete(
            f"/api/v1/bookings/{booking_b_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Happy Path: Admin cancels session
# ---------------------------------------------------------------------------


class TestSessionCancelE2E:
    """E2E tests for admin session cancellation endpoint."""

    def test_cancel_session_returns_credits_and_notifies(self, api):
        """Full happy path: admin cancels session → confirmed users get
        credits back, all bookings cancelled, notifications created."""
        # 1. Register admin and create session
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=2, title="Cancelled Yoga")
        session_id = session["id"]

        # 2. User A books (confirmed)
        token_a, email_a = _register_client(api, suffix="_a")
        _add_credits(email_a, amount=5)
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

        # 3. User B books (confirmed)
        token_b, email_b = _register_client(api, suffix="_b")
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
        assert res_b.json()["status"] == "confirmed"

        # 4. Verify credits deducted
        assert _get_me_credits(api, token_a) == 4
        assert _get_me_credits(api, token_b) == 4

        # 5. Verify session enrolled == 2
        res_session = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_05_session_before_cancel", res_session.json(), res_session.status)
        assert res_session.json()["enrolled"] == 2

        # 6. Admin cancels the session
        cancel_res = api.put(
            f"/api/v1/sessions/{session_id}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("happy_06_session_cancelled", {}, cancel_res.status)
        assert cancel_res.status == 204, \
            f"Session cancel failed: {cancel_res.status} {cancel_res.text()}"

        # 7. Both users should have credits back
        assert _get_me_credits(api, token_a) == 5, "User A credits not restored"
        assert _get_me_credits(api, token_b) == 5, "User B credits not restored"

        # 8. Both bookings should be cancelled
        for token, label in [(token_a, "A"), (token_b, "B")]:
            res = api.get(
                "/api/v1/bookings/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.status == 200
            assert res.json()[0]["status"] == "cancelled", \
                f"User {label} booking not cancelled: {res.json()}"

        # 9. Session enrolled should be 0
        res_session_after = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_07_session_after_cancel", res_session_after.json(), res_session_after.status)
        assert res_session_after.json()["enrolled"] == 0, \
            f"Expected enrolled=0, got {res_session_after.json()['enrolled']}"

        # 10. Notifications should exist
        notifications = _read_notifications()
        api_screenshot("happy_08_session_cancel_notifications", notifications, 200)
        assert len(notifications) >= 2, \
            f"Expected at least 2 notifications, got {len(notifications)}"

        credit_returned_msgs = [
            n for n in notifications
            if "credit has been returned" in n.get("message", "").lower()
        ]
        assert len(credit_returned_msgs) >= 2, \
            f"Expected at least 2 credit-returned notifications, got {len(credit_returned_msgs)}"

        # 11. Clean up
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_cancel_session_waitlist_no_credit_return(self, api):
        """Waitlist users get no credit refund when session is cancelled."""
        # 1. Register admin and create session with capacity=1
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="Solo Session")
        session_id = session["id"]

        # 2. User A fills the session (confirmed)
        token_a, email_a = _register_client(api, suffix="_conf")
        _add_credits(email_a, amount=5)
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

        # 3. User B goes to waitlist
        token_b, email_b = _register_client(api, suffix="_wait")
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
        api_screenshot("happy_09_waitlist_before_cancel", res_b.json(), res_b.status)

        # User B credits unchanged (no deduction for waitlist)
        assert _get_me_credits(api, token_b) == 5

        # 4. Admin cancels session
        cancel_res = api.put(
            f"/api/v1/sessions/{session_id}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert cancel_res.status == 204

        # 5. User A gets credit back (5)
        assert _get_me_credits(api, token_a) == 5, "Confirmed user should get credit back"

        # 6. User B still has 5 credits (no refund for waitlist)
        credits_b_after = _get_me_credits(api, token_b)
        api_screenshot("happy_10_waitlist_credits_unchanged",
                        {"credits": credits_b_after}, 200)
        assert credits_b_after == 5, \
            f"Waitlist user should still have 5 credits, got {credits_b_after}"

        # 7. Both bookings cancelled
        for token, label in [(token_a, "A"), (token_b, "B")]:
            res = api.get(
                "/api/v1/bookings/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert res.json()[0]["status"] == "cancelled", \
                f"User {label} booking not cancelled"

        # 8. Verify waitlist notification was created
        notifications = _read_notifications()
        waitlist_notifications = [
            n for n in notifications
            if "waitlist" in n.get("message", "").lower()
        ]
        assert len(waitlist_notifications) >= 1, \
            f"Expected at least 1 waitlist notification, got {len(waitlist_notifications)}"

        # 9. Clean up
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Sad Paths: Auth & authorization for session cancel
# ---------------------------------------------------------------------------


class TestSessionCancelSadPaths:
    """E2E tests for error scenarios on session cancellation."""

    def test_cancel_session_unauthenticated_returns_401(self, api):
        """PUT /sessions/{id}/cancel without auth → 401."""
        res = api.put("/api/v1/sessions/1/cancel")
        api_screenshot("sad_01_unauth_cancel", res.json(), res.status)
        assert res.status == 401, \
            f"Expected 401 for unauthenticated cancel, got {res.status}: {res.text()}"

    def test_cancel_session_as_client_returns_403(self, api):
        """PUT /sessions/{id}/cancel as client → 403."""
        # 1. Register admin and create a session
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        # 2. Register a client
        client_token, _ = _register_client(api, suffix="_forbid")

        # 3. Client tries to cancel session
        res = api.put(
            f"/api/v1/sessions/{session_id}/cancel",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("sad_02_client_cancel_403", res.json(), res.status)
        assert res.status == 403, \
            f"Expected 403 for client cancel, got {res.status}: {res.text()}"

        # 4. Clean up
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_cancel_nonexistent_session_returns_404(self, api):
        """PUT /sessions/{id}/cancel with non-existent id → 404."""
        admin_token = _register_admin(api)

        res = api.put(
            "/api/v1/sessions/99999/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("sad_03_nonexistent_404", res.json(), res.status)
        assert res.status == 404, \
            f"Expected 404 for non-existent session, got {res.status}: {res.text()}"
        assert "Session not found" in res.json().get("detail", "")
