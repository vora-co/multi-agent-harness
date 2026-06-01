"""E2E tests for Stats endpoints (Feature #8).

These tests verify the stats aggregation endpoints from an end-user
perspective (real HTTP round-trips), complementing unit tests in
tests/test_stats.py which cover the core select_top_users logic.

What E2E tests add:
  - Real HTTP round-trips against a running server
  - Full lifecycle: register → create sessions → book → verify stats
  - Auth protection on /stats/users (admin-only)
  - Visual evidence via JSON screenshots of API responses
"""

import os
import json
import time
import pytest

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def api_screenshot(name: str, response_data: dict, status: int):
    """Save API response as JSON screenshot for evidence."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat8_{name}.json")
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


def _register_client(api, name="E2E Client") -> tuple:
    """Register a fresh client user and return (access_token, user_dict)."""
    unique_email = f"e2e_client_{int(time.time() * 1000)}@example.com"
    res = api.post(
        "/api/v1/auth/register",
        data=json.dumps({
            "name": name,
            "email": unique_email,
            "password": "ClientPass123!",
            "role": "client",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert res.status == 200, f"Client registration failed: {res.status} {res.text()}"
    data = res.json()
    return data["access_token"], data


def _create_session(api, admin_token: str, title: str, instructor: str,
                    style: str, capacity: int = 10) -> dict:
    """Create a session and return its dict."""
    res = api.post(
        "/api/v1/sessions",
        data=json.dumps({
            "title": title,
            "instructor": instructor,
            "style": style,
            "starts_at": "2025-07-01T08:00:00",
            "duration_minutes": 60,
            "capacity": capacity,
        }),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
        },
    )
    assert res.status == 201, f"Session creation failed: {res.status} {res.text()}"
    return res.json()


def _book_session(api, client_token: str, session_id: int) -> dict:
    """Book a session as a client and return the booking dict."""
    res = api.post(
        "/api/v1/bookings",
        data=json.dumps({"session_id": session_id}),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {client_token}",
        },
    )
    assert res.status == 201, f"Booking failed: {res.status} {res.text()}"
    return res.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStatsEndpoints:
    """E2E tests for /stats/instructors, /stats/styles, and /stats/users."""

    def test_instructor_stats_happy_path(self, api):
        """Verify /stats/instructors correctly aggregates sessions per instructor."""
        admin_token = _register_admin(api)

        # Create sessions with two different instructors
        _create_session(api, admin_token, "Yoga Flow", "Alice", "Vinyasa")
        _create_session(api, admin_token, "Power Yoga", "Alice", "Vinyasa")
        _create_session(api, admin_token, "Hatha Basics", "Bob", "Hatha")
        _create_session(api, admin_token, "Morning Stretch", "Bob", "Hatha")

        # Call the public endpoint
        res = api.get("/stats/instructors")
        assert res.status == 200, f"Instructor stats failed: {res.status} {res.text()}"
        data = res.json()
        api_screenshot("instructor_stats_happy", data, res.status)

        # Should have two instructors
        assert len(data) == 2, f"Expected 2 instructors, got {len(data)}: {data}"

        # Build lookup
        stats_by_name = {entry["name"]: entry for entry in data}

        assert "Alice" in stats_by_name
        assert stats_by_name["Alice"]["sessions_count"] == 2
        assert stats_by_name["Alice"]["total_enrolled"] == 0  # no bookings yet

        assert "Bob" in stats_by_name
        assert stats_by_name["Bob"]["sessions_count"] == 2
        assert stats_by_name["Bob"]["total_enrolled"] == 0

        # Verify synthetic ids are assigned (sequential starting at 1)
        ids = [entry["id"] for entry in data]
        assert ids == [1, 2], f"Expected synthetic ids [1, 2], got {ids}"

    def test_style_stats_happy_path(self, api):
        """Verify /stats/styles correctly aggregates sessions per style."""
        admin_token = _register_admin(api)

        # Create sessions with different styles
        _create_session(api, admin_token, "Class A", "Alice", "Vinyasa")
        _create_session(api, admin_token, "Class B", "Alice", "Vinyasa")
        _create_session(api, admin_token, "Class C", "Bob", "Hatha")
        _create_session(api, admin_token, "Class D", "Bob", "Hatha")
        _create_session(api, admin_token, "Class E", "Charlie", "Yin")

        res = api.get("/stats/styles")
        assert res.status == 200, f"Style stats failed: {res.status} {res.text()}"
        data = res.json()
        api_screenshot("style_stats_happy", data, res.status)

        # Build lookup
        stats_by_style = {entry["style"]: entry for entry in data}

        assert len(stats_by_style) == 3, \
            f"Expected 3 styles, got {len(stats_by_style)}: {data}"

        assert stats_by_style["Vinyasa"]["sessions_count"] == 2
        assert stats_by_style["Vinyasa"]["total_enrolled"] == 0

        assert stats_by_style["Hatha"]["sessions_count"] == 2
        assert stats_by_style["Hatha"]["total_enrolled"] == 0

        assert stats_by_style["Yin"]["sessions_count"] == 1
        assert stats_by_style["Yin"]["total_enrolled"] == 0

    def test_instructor_stats_with_enrollments(self, api):
        """Verify /stats/instructors reflects enrolled counts after bookings."""
        admin_token = _register_admin(api)

        # Create sessions for one instructor
        s1 = _create_session(api, admin_token, "Yoga Flow", "Alice", "Vinyasa",
                             capacity=10)
        s2 = _create_session(api, admin_token, "Power Yoga", "Alice", "Vinyasa",
                             capacity=10)

        # Register a client and book both sessions
        client_token, _ = _register_client(api)
        _book_session(api, client_token, s1["id"])
        _book_session(api, client_token, s2["id"])

        res = api.get("/stats/instructors")
        assert res.status == 200
        data = res.json()
        api_screenshot("instructor_stats_with_enrollments", data, res.status)

        alice_stats = [e for e in data if e["name"] == "Alice"][0]
        assert alice_stats["sessions_count"] == 2
        assert alice_stats["total_enrolled"] == 2, \
            f"Expected 2 enrolled, got {alice_stats['total_enrolled']}"

    def test_user_stats_happy_path(self, api):
        """Verify /stats/users returns top-10 users sorted by booking count (admin only)."""
        admin_token = _register_admin(api)

        # Create sessions
        s1 = _create_session(api, admin_token, "Class 1", "Alice", "Vinyasa",
                             capacity=10)
        s2 = _create_session(api, admin_token, "Class 2", "Alice", "Vinyasa",
                             capacity=10)

        # Register two clients with known names
        c1_token, c1_user = _register_client(api, name="Top Booker")
        c2_token, _ = _register_client(api, name="Low Booker")

        # Client 1 books both sessions (2 bookings)
        _book_session(api, c1_token, s1["id"])
        _book_session(api, c1_token, s2["id"])
        # Client 2 books one session (1 booking)
        _book_session(api, c2_token, s1["id"])

        # Admin access
        res = api.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status == 200, f"User stats failed: {res.status} {res.text()}"
        data = res.json()
        api_screenshot("user_stats_happy", data, res.status)

        assert len(data) >= 2, f"Expected at least 2 users in stats, got {len(data)}"

        # First entry should be the top booker (2 bookings)
        top_user = data[0]
        assert top_user["total_bookings"] >= 2, \
            f"Expected top user to have >=2 bookings, got {top_user}"

        # Verify ordering: first user has more or equal bookings to second
        assert data[0]["total_bookings"] >= data[1]["total_bookings"], \
            "Results should be sorted descending by bookings"

    def test_user_stats_requires_auth(self, api):
        """Sad path: /stats/users without auth token returns 401."""
        res = api.get("/stats/users")
        assert res.status == 401, \
            f"Expected 401 without auth, got {res.status}: {res.text()}"
        api_screenshot("user_stats_unauthorized", res.json(), res.status)

    def test_user_stats_requires_admin(self, api):
        """Sad path: /stats/users with a client token returns 403."""
        client_token, _ = _register_client(api)

        res = api.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert res.status == 403, \
            f"Expected 403 for client access, got {res.status}: {res.text()}"
        api_screenshot("user_stats_forbidden", res.json(), res.status)

    def test_instructor_stats_empty(self, api):
        """Edge case: /stats/instructors returns empty list when no sessions exist."""
        res = api.get("/stats/instructors")
        assert res.status == 200
        data = res.json()
        api_screenshot("instructor_stats_empty", data, res.status)
        assert data == [], f"Expected empty list, got {data}"

    def test_style_stats_empty(self, api):
        """Edge case: /stats/styles returns empty list when no sessions exist."""
        res = api.get("/stats/styles")
        assert res.status == 200
        data = res.json()
        api_screenshot("style_stats_empty", data, res.status)
        assert data == [], f"Expected empty list, got {data}"
