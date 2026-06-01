"""E2E tests for Session API endpoints (Feature #6).

These tests verify the session REST API from an end-user
perspective (real HTTP round-trips), complementing unit tests in
tests/test_sessions_api.py which cover CRUD operations via TestClient.

What E2E tests add:
  - Real HTTP round-trips against a running server
  - Full admin lifecycle: register → create session → list → filter → update → delete
  - Role-based access control (admin vs client vs unauthenticated)
  - Error responses for invalid auth, invalid inputs, and not-found
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
    path = os.path.join(SCREENSHOT_DIR, f"feat6_{name}.json")
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


def _register_client(api) -> str:
    """Register a fresh client user and return the access token."""
    unique_email = f"e2e_client_{int(time.time() * 1000)}@example.com"
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
    return res.json()["access_token"]


SESSION_PAYLOAD = {
    "title": "Morning Yoga",
    "instructor": "Alice",
    "style": "Vinyasa",
    "starts_at": "2025-06-15T09:00:00",
    "duration_minutes": 60,
    "capacity": 20,
}


# ---------------------------------------------------------------------------
# Happy Path: Full admin lifecycle
# ---------------------------------------------------------------------------

class TestSessionHappyPath:
    """Full session CRUD lifecycle as an admin via real HTTP."""

    def test_full_admin_session_lifecycle(self, api):
        """Register admin → create → list → get → update → filter → delete."""
        admin_token = _register_admin(api)

        # ---- 1. Create a session (admin only) ----
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("happy_01_create_session", res.json(), res.status)
        assert res.status == 201, f"Create failed: {res.status} {res.text()}"
        session = res.json()
        assert session["title"] == "Morning Yoga"
        assert session["style"] == "Vinyasa"
        assert session["capacity"] == 20
        assert session["enrolled"] == 0
        session_id = session["id"]

        # ---- 2. List all sessions (public endpoint) ----
        res = api.get("/api/v1/sessions")
        api_screenshot("happy_02_list_all", res.json(), res.status)
        assert res.status == 200
        all_sessions = res.json()
        assert len(all_sessions) >= 1
        titles = [s["title"] for s in all_sessions]
        assert "Morning Yoga" in titles

        # ---- 3. Get session by ID (public endpoint) ----
        res = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_03_get_by_id", res.json(), res.status)
        assert res.status == 200
        assert res.json()["id"] == session_id
        assert res.json()["instructor"] == "Alice"

        # ---- 4. Update session (admin only) ----
        res = api.put(
            f"/api/v1/sessions/{session_id}",
            data=json.dumps({"title": "Advanced Morning Yoga", "capacity": 25}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        api_screenshot("happy_04_update", res.json(), res.status)
        assert res.status == 200, f"Update failed: {res.status} {res.text()}"
        updated = res.json()
        assert updated["title"] == "Advanced Morning Yoga"
        assert updated["capacity"] == 25
        # Unchanged fields should remain
        assert updated["instructor"] == "Alice"
        assert updated["style"] == "Vinyasa"

        # ---- 5. Create a second session for filter testing ----
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps({
                **SESSION_PAYLOAD,
                "title": "Evening Hatha",
                "style": "Hatha",
                "starts_at": "2025-06-16T18:00:00",
            }),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        assert res.status == 201
        second_session_id = res.json()["id"]

        # ---- 6a. Filter by style ----
        res = api.get("/api/v1/sessions?style=Vinyasa")
        api_screenshot("happy_06a_filter_style", res.json(), res.status)
        assert res.status == 200
        filtered = res.json()
        assert len(filtered) == 1, f"Expected 1 Vinyasa session, got {len(filtered)}: {filtered}"
        assert filtered[0]["style"] == "Vinyasa"

        # ---- 6b. Filter by date ----
        res = api.get("/api/v1/sessions?date=2025-06-16")
        api_screenshot("happy_06b_filter_date", res.json(), res.status)
        assert res.status == 200
        filtered = res.json()
        assert len(filtered) == 1
        assert filtered[0]["title"] == "Evening Hatha"

        # ---- 6c. Filter by both style and date ----
        res = api.get("/api/v1/sessions?style=Hatha&date=2025-06-16")
        api_screenshot("happy_06c_filter_both", res.json(), res.status)
        assert res.status == 200
        filtered = res.json()
        assert len(filtered) == 1
        assert filtered[0]["title"] == "Evening Hatha"

        # ---- 7. Delete session (enrolled=0, admin only) ----
        res = api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("happy_07_delete", {}, res.status)
        assert res.status == 204, f"Delete failed: {res.status} {res.text()}"

        # ---- 8. Verify deletion via GET ----
        res = api.get(f"/api/v1/sessions/{session_id}")
        api_screenshot("happy_08_verify_deleted", res.json(), res.status)
        assert res.status == 404

        # ---- 9. Clean up: delete the second session too ----
        res = api.delete(
            f"/api/v1/sessions/{second_session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert res.status == 204, f"Cleanup delete failed: {res.status} {res.text()}"


# ---------------------------------------------------------------------------
# Sad Paths: Authorization failures
# ---------------------------------------------------------------------------

class TestSessionAuthSadPaths:
    """Authorization error scenarios for session endpoints."""

    def test_unauthenticated_create_session(self, api):
        """POST without token → 401."""
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={"Content-Type": "application/json"},
        )
        api_screenshot("sad_01_unauthenticated_create", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_unauthenticated_update_session(self, api):
        """PUT without token → 401."""
        res = api.put(
            "/api/v1/sessions/1",
            data=json.dumps({"title": "Hacked"}),
            headers={"Content-Type": "application/json"},
        )
        api_screenshot("sad_02_unauthenticated_update", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_unauthenticated_delete_session(self, api):
        """DELETE without token → 401."""
        res = api.delete("/api/v1/sessions/1")
        api_screenshot("sad_03_unauthenticated_delete", res.json(), res.status)
        assert res.status == 401, f"Expected 401, got {res.status}: {res.text()}"

    def test_client_cannot_create_session(self, api):
        """Client token on POST → 403."""
        client_token = _register_client(api)
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_04_client_create_403", res.json(), res.status)
        assert res.status == 403, f"Expected 403, got {res.status}: {res.text()}"
        assert "Admin privileges required" in res.json().get("detail", "")

    def test_client_cannot_update_session(self, api):
        """Client token on PUT → 403."""
        admin_token = _register_admin(api)
        # Admin creates a session first
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        session_id = res.json()["id"]

        client_token = _register_client(api)
        res = api.put(
            f"/api/v1/sessions/{session_id}",
            data=json.dumps({"title": "Hacked by client"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {client_token}",
            },
        )
        api_screenshot("sad_05_client_update_403", res.json(), res.status)
        assert res.status == 403, f"Expected 403, got {res.status}: {res.text()}"

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_client_cannot_delete_session(self, api):
        """Client token on DELETE → 403."""
        admin_token = _register_admin(api)
        res = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        session_id = res.json()["id"]

        client_token = _register_client(api)
        res = api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        api_screenshot("sad_06_client_delete_403", res.json(), res.status)
        assert res.status == 403, f"Expected 403, got {res.status}: {res.text()}"

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )


# ---------------------------------------------------------------------------
# Sad Paths: Invalid input and not-found
# ---------------------------------------------------------------------------

class TestSessionInputSadPaths:
    """Input validation and not-found error scenarios."""

    def test_invalid_date_filter_returns_400(self, api):
        """Filtering with malformed date → 400."""
        res = api.get("/api/v1/sessions?date=not-a-valid-date")
        api_screenshot("sad_07_invalid_date_400", res.json(), res.status)
        assert res.status == 400, f"Expected 400, got {res.status}: {res.text()}"

    def test_nonexistent_session_returns_404(self, api):
        """GET non-existent session → 404."""
        res = api.get("/api/v1/sessions/99999")
        api_screenshot("sad_08_nonexistent_404", res.json(), res.status)
        assert res.status == 404, f"Expected 404, got {res.status}: {res.text()}"

    def test_delete_nonexistent_session_returns_404(self, api):
        """DELETE non-existent session → 404 (admin)."""
        admin_token = _register_admin(api)
        res = api.delete(
            "/api/v1/sessions/99999",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        api_screenshot("sad_09_delete_nonexistent_404", res.json(), res.status)
        assert res.status == 404, f"Expected 404, got {res.status}: {res.text()}"

    def test_filter_style_no_results(self, api):
        """Filtering by non-existent style → empty list."""
        admin_token = _register_admin(api)
        res_create = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        session_id = res_create.json()["id"]

        res = api.get("/api/v1/sessions?style=NonExistentStyle")
        api_screenshot("sad_10_filter_no_results", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    def test_filter_date_no_results(self, api):
        """Filtering by date with no sessions → empty list."""
        admin_token = _register_admin(api)
        res_create = api.post(
            "/api/v1/sessions",
            data=json.dumps(SESSION_PAYLOAD),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {admin_token}",
            },
        )
        session_id = res_create.json()["id"]

        res = api.get("/api/v1/sessions?date=2099-12-31")
        api_screenshot("sad_11_date_no_results", res.json(), res.status)
        assert res.status == 200
        assert res.json() == []

        # Cleanup
        api.delete(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
