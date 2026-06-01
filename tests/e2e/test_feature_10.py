"""E2E tests for Waitlist Promotion & Notifications (Feature #10).

These tests verify the admin waitlist promotion endpoint and the
notifications listing/mark-read endpoints from an end-user perspective
(real HTTP round-trips against a running server), complementing unit
tests in tests/test_feature10.py which cover business logic via TestClient.
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


def api_screenshot(name, response_data, status):
    """Save API response as JSON screenshot for evidence."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat10_{name}.json")
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


def _register_admin(api):
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


def _register_client(api, suffix=""):
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


def _create_session(api, admin_token, **overrides):
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


def _add_credits(email, amount=5):
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


def _get_me_credits(api, token):
    res = api.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status == 200, f"GET /me failed: {res.status} {res.text()}"
    return res.json()["credits"]


def _get_user_id(api, token):
    res = api.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status == 200, f"GET /me failed: {res.status} {res.text()}"
    return res.json()["id"]

# ---------------------------------------------------------------------------
# Happy Path: Admin enrolls from waitlist
# ---------------------------------------------------------------------------


class TestEnrollFromWaitlistE2E:
    """E2E tests verifying admin promotes a waitlisted user to confirmed."""

    def test_enroll_from_waitlist_happy_path(self, api):
        """Full happy path: admin promotes waitlisted user -> confirmed,
        credit deducted, notification sent, enrolled incremented."""
        # 1. Register admin and create session with capacity=1
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="Exclusive Class")
        session_id = session["id"]

        # 2. User A books -> confirmed (fills the only spot)
        token_a, email_a = _register_client(api, suffix="_conf")
        _add_credits(email_a, amount=5)
        assert _get_me_credits(api, token_a) == 5

        res_a = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )
        assert res_a.status == 201, f"User A booking failed: {res_a.status} {res_a.text()}"
        assert res_a.json()["status"] == "confirmed"
        assert _get_me_credits(api, token_a) == 4

        # 3. User B books -> goes to waitlist (session full)
        token_b, email_b = _register_client(api, suffix="_wait")
        _add_credits(email_b, amount=5)
        user_b_id = _get_user_id(api, token_b)

        res_b = api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )
        api_screenshot("happy_01_waitlist_booking", res_b.json(), res_b.status)
        assert res_b.status == 201, f"User B booking failed: {res_b.status} {res_b.text()}"
        assert res_b.json()["status"] == "waitlist"
        assert _get_me_credits(api, token_b) == 5

        # 4. Verify session enrolled count = 1
        res_session_before = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_02_session_before_enroll",
                       res_session_before.json(), res_session_before.status)
        assert res_session_before.json()["enrolled"] == 1

        # 5. Admin promotes from waitlist
        enroll_res = api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("happy_03_enroll_response",
                       enroll_res.json(), enroll_res.status)
        assert enroll_res.status == 200, \
            f"enroll_from_waitlist failed: {enroll_res.status} {enroll_res.text()}"
        enroll_data = enroll_res.json()
        assert enroll_data["booking_id"] is not None
        assert enroll_data["user_id"] == user_b_id

        # 6. User B's booking should now be confirmed
        res_b_list = api.get(
            "/api/v1/bookings/me",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_04_promoted_booking",
                       res_b_list.json(), res_b_list.status)
        assert res_b_list.status == 200
        bookings_b = res_b_list.json()
        promoted = [b for b in bookings_b if b["id"] == enroll_data["booking_id"]]
        assert len(promoted) == 1
        assert promoted[0]["status"] == "confirmed"

        # 7. User B credit deducted
        assert _get_me_credits(api, token_b) == 4

        # 8. Session enrolled should be 2
        res_session_after = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_05_session_after_enroll",
                       res_session_after.json(), res_session_after.status)
        assert res_session_after.json()["enrolled"] == 2

        # 9. Notification created for User B
        notifications_res = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_06_notifications",
                       notifications_res.json(), notifications_res.status)
        assert notifications_res.status == 200
        notifs = notifications_res.json()
        promotion_msgs = [
            n for n in notifs
            if "promoted from the waitlist" in n.get("message", "").lower()
        ]
        assert len(promotion_msgs) >= 1

        # 10. Clean up
        for token, booking_data in [(token_a, res_a.json()),
                                     (token_b, res_b.json())]:
            api.delete(
                f"/api/v1/bookings/{booking_data['id']}",
                headers={"Authorization": f"Bearer {token}"},
            )
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Happy Path: Notifications listing & mark read
# ---------------------------------------------------------------------------


class TestNotificationsE2E:
    """E2E tests verifying notification listing and mark-read endpoints."""

    def test_list_notifications_ordered_desc(self, api):
        """Notifications are returned in descending order by created_at."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="NotifyClass")
        session_id = session["id"]

        token_a, email_a = _register_client(api, suffix="_a")
        _add_credits(email_a, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )

        token_b, email_b = _register_client(api, suffix="_b")
        _add_credits(email_b, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )

        token_c, email_c = _register_client(api, suffix="_c")
        _add_credits(email_c, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_c}",
            },
        )

        # Admin promotes User B from waitlist (first in FIFO)
        enroll_res = api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert enroll_res.status == 200

        notifs_res = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_07_notifs_ordered",
                       notifs_res.json(), notifs_res.status)
        assert notifs_res.status == 200
        notifs = notifs_res.json()
        assert len(notifs) >= 1

        if len(notifs) >= 2:
            for i in range(len(notifs) - 1):
                assert notifs[i]["created_at"] >= notifs[i + 1]["created_at"], \
                    f"Notifs not ordered desc: {notifs[i]['created_at']} < {notifs[i+1]['created_at']}"

        assert any(
            "promoted from the waitlist" in n.get("message", "").lower()
            for n in notifs
        )

        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_mark_notification_read_persists(self, api):
        """Mark a notification as read and verify read_at is persisted."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="ReadNotify")
        session_id = session["id"]

        token_a, email_a = _register_client(api, suffix="_a")
        _add_credits(email_a, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )

        token_b, email_b = _register_client(api, suffix="_b")
        _add_credits(email_b, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )

        enroll_res = api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert enroll_res.status == 200

        notifs_res = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert notifs_res.status == 200
        notifs = notifs_res.json()
        assert len(notifs) >= 1
        first_notif = notifs[0]
        notification_id = first_notif["id"]

        api_screenshot("happy_08_unread_notification", first_notif, 200)
        assert first_notif.get("read_at") is None

        mark_res = api.put(
            f"/api/v1/users/me/notifications/{notification_id}/read",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        api_screenshot("happy_09_mark_read_response",
                       mark_res.json(), mark_res.status)
        assert mark_res.status == 200
        mark_data = mark_res.json()
        assert mark_data.get("read_at") is not None

        # Re-list to verify persistence
        notifs_res2 = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert notifs_res2.status == 200
        notifs2 = notifs_res2.json()
        api_screenshot("happy_10_read_persisted", notifs2, 200)
        same_notif = [n for n in notifs2 if n["id"] == notification_id]
        assert len(same_notif) == 1
        assert same_notif[0].get("read_at") is not None

        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_empty_notifications_returns_empty_list(self, api):
        """A user with no notifications gets an empty list."""
        token, _ = _register_client(api, suffix="_empty")
        res = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token}"},
        )
        api_screenshot("happy_11_empty_notifications", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []


# ---------------------------------------------------------------------------
# Sad Paths: enroll_from_waitlist
# ---------------------------------------------------------------------------


class TestEnrollFromWaitlistSadPaths:
    """E2E tests for error scenarios on enroll_from_waitlist."""

    def test_enroll_unauthenticated_returns_401(self, api):
        """PUT /sessions/{id}/enroll_from_waitlist without auth -> 401."""
        res = api.put("/api/v1/sessions/1/enroll_from_waitlist")
        api_screenshot("sad_01_unauth_enroll", res.json(), res.status)
        assert res.status == 401, \
            f"Expected 401, got {res.status}: {res.text()}"

    def test_enroll_as_client_returns_403(self, api):
        """PUT /sessions/{id}/enroll_from_waitlist as client -> 403."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token)
        session_id = session["id"]

        client_token, _ = _register_client(api, suffix="_forbid")
        res = api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("sad_02_client_enroll_403", res.json(), res.status)
        assert res.status == 403, \
            f"Expected 403, got {res.status}: {res.text()}"

        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_enroll_no_waitlist_returns_400(self, api):
        """PUT /sessions/{id}/enroll_from_waitlist with no waitlist -> 400."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=10, title="OpenClass")
        session_id = session["id"]

        res = api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("sad_03_no_waitlist_400", res.json(), res.status)
        assert res.status == 400, \
            f"Expected 400, got {res.status}: {res.text()}"
        assert "waitlist" in res.json().get("detail", "").lower()

        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_enroll_nonexistent_session_returns_404(self, api):
        """PUT /sessions/{id}/enroll_from_waitlist nonexistent -> 404."""
        admin_token = _register_admin(api)
        res = api.put(
            "/api/v1/sessions/99999/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("sad_04_nonexistent_session_404",
                       res.json(), res.status)
        assert res.status == 404, \
            f"Expected 404, got {res.status}: {res.text()}"
        assert "not found" in res.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# Sad Paths: notifications
# ---------------------------------------------------------------------------


class TestNotificationsSadPaths:
    """E2E tests for error scenarios on notifications endpoints."""

    def test_list_notifications_unauthenticated_returns_401(self, api):
        """GET /users/me/notifications without auth -> 401."""
        res = api.get("/api/v1/users/me/notifications")
        api_screenshot("sad_05_unauth_list_notif", res.json(), res.status)
        assert res.status == 401, \
            f"Expected 401, got {res.status}: {res.text()}"

    def test_mark_read_unauthenticated_returns_401(self, api):
        """PUT /users/me/notifications/{id}/read without auth -> 401."""
        res = api.put("/api/v1/users/me/notifications/1/read")
        api_screenshot("sad_06_unauth_mark_read", res.json(), res.status)
        assert res.status == 401, \
            f"Expected 401, got {res.status}: {res.text()}"

    def test_mark_read_other_user_notification_returns_403(self, api):
        """Marking another user's notification as read -> 403."""
        admin_token = _register_admin(api)
        session = _create_session(api, admin_token, capacity=1, title="ForbidClass")
        session_id = session["id"]

        token_a, email_a = _register_client(api, suffix="_a")
        _add_credits(email_a, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_a}",
            },
        )

        token_b, email_b = _register_client(api, suffix="_b")
        _add_credits(email_b, amount=5)
        api.post(
            "/api/v1/bookings",
            data=json.dumps({"session_id": session_id}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_b}",
            },
        )

        api.put(
            f"/api/v1/sessions/{session_id}/enroll_from_waitlist",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

        notifs_res = api.get(
            "/api/v1/users/me/notifications",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert notifs_res.status == 200
        notifs_b = notifs_res.json()
        assert len(notifs_b) >= 1
        notification_id = notifs_b[0]["id"]

        res = api.put(
            f"/api/v1/users/me/notifications/{notification_id}/read",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        api_screenshot("sad_07_other_user_notif_403",
                       res.json(), res.status)
        assert res.status == 403, \
            f"Expected 403, got {res.status}: {res.text()}"

        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_mark_read_nonexistent_notification_returns_404(self, api):
        """Marking a non-existent notification as read -> 404."""
        token, _ = _register_client(api, suffix="_nonexist")
        res = api.put(
            "/api/v1/users/me/notifications/99999/read",
            headers={"Authorization": f"Bearer {token}"},
        )
        api_screenshot("sad_08_nonexistent_notif_404",
                       res.json(), res.status)
        assert res.status == 404, \
            f"Expected 404, got {res.status}: {res.text()}"
        assert "not found" in res.json().get("detail", "").lower()
