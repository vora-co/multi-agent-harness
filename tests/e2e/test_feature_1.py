"""E2E tests for User model (Feature #1).

These tests verify User behaviour via a web frontend, complementing
the unit tests in tests/test_user.py which cover email validation,
role validation, to_dict, from_dict, defaults, and round-trip at
the Python level.

What E2E tests add:
  - User navigation flow (create -> view -> list)
  - Visual feedback on user creation success / error messages
  - Role display on user detail and list pages
  - Credits default (0) visible in the UI
  - 404 handling for non-existent users
  - API-level validation for invalid role (unreachable via form select)
  - Server-side validation of empty/malformed email (bypassing client-side required attr)
"""

import os
import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def screen(page: Page, name: str):
    """Take a screenshot with the given name."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat1_{name}.png")
    page.screenshot(path=path, full_page=True)
    return path


class TestHappyPath:
    """End-to-end happy path: create users and verify their details."""

    def test_create_user_client_and_view_details(self, page: Page):
        """Create a user with role 'client', then verify the detail page."""
        # 1. Go to create form
        page.goto(f"{BASE_URL}/users/create")
        screen(page, "happy_01_create_form")

        # 2. Fill in the form
        page.fill('input[name="id"]', "1")
        page.fill('input[name="name"]', "Alice")
        page.fill('input[name="email"]', "alice@example.com")
        page.fill('input[name="credits"]', "10")
        page.select_option('select[name="role"]', "client")
        screen(page, "happy_02_form_filled")

        # 3. Submit the form
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_03_user_created")

        # Verify success message and user data visible
        expect(page.locator(".success")).to_contain_text("User created successfully")
        expect(page.locator("#user-id")).to_contain_text("1")
        expect(page.locator("#user-name")).to_contain_text("Alice")
        expect(page.locator("#user-email")).to_contain_text("alice@example.com")
        expect(page.locator("#user-credits")).to_contain_text("10")
        expect(page.locator("#user-role")).to_contain_text("client")
        # created_at should be present (ISO format timestamp)
        expect(page.locator("#user-created-at")).not_to_be_empty()

        # 4. Navigate to user detail page
        page.click('a:has-text("View user details")')
        page.wait_for_url("**/users/1")
        screen(page, "happy_04_detail_page")

        # Verify detail page content
        expect(page.locator("h1")).to_contain_text("User #1")
        expect(page.locator("#user-id")).to_contain_text("1")
        expect(page.locator("#user-name")).to_contain_text("Alice")
        expect(page.locator("#user-email")).to_contain_text("alice@example.com")
        expect(page.locator("#user-credits")).to_contain_text("10")
        expect(page.locator("#user-role")).to_contain_text("client")

    def test_create_user_admin_and_list(self, page: Page):
        """Create a user with role 'admin', then verify in the list."""
        page.goto(f"{BASE_URL}/users/create")
        page.fill('input[name="id"]', "2")
        page.fill('input[name="name"]', "Bob")
        page.fill('input[name="email"]', "bob@admin.org")
        page.fill('input[name="credits"]', "50")
        page.select_option('select[name="role"]', "admin")
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_05_admin_created")

        expect(page.locator("#user-role")).to_contain_text("admin")
        expect(page.locator("#user-email")).to_contain_text("bob@admin.org")

        # Go to list page and verify both users appear
        page.goto(f"{BASE_URL}/users")
        screen(page, "happy_06_list_users")

        # User #1 (Alice) should appear
        user1_section = page.locator("#user-1")
        expect(user1_section).to_contain_text("Alice")
        expect(user1_section).to_contain_text("alice@example.com")
        expect(user1_section).to_contain_text("client")

        # User #2 (Bob) should appear
        user2_section = page.locator("#user-2")
        expect(user2_section).to_contain_text("Bob")
        expect(user2_section).to_contain_text("bob@admin.org")
        expect(user2_section).to_contain_text("admin")
        expect(user2_section).to_contain_text("50")

    def test_create_user_default_credits_is_zero(self, page: Page):
        """When credits is left at 0, the UI should show 0."""
        page.goto(f"{BASE_URL}/users/create")
        page.fill('input[name="id"]', "3")
        page.fill('input[name="name"]', "Charlie")
        page.fill('input[name="email"]', "charlie@test.com")
        # Leave credits at default (0) -- the form default is 0
        page.select_option('select[name="role"]', "client")
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_07_default_credits")

        expect(page.locator("#user-credits")).to_contain_text("0")
        expect(page.locator("#user-name")).to_contain_text("Charlie")


class TestSadPath:
    """End-to-end sad paths: errors visible to the user."""

    def test_invalid_email_shows_error(self, page: Page):
        """Submitting an email without @ should display a validation error."""
        page.goto(f"{BASE_URL}/users/create")
        page.fill('input[name="id"]', "10")
        page.fill('input[name="name"]', "BadEmail")
        page.fill('input[name="email"]', "notanemail")
        page.click('button[type="submit"]')
        page.wait_for_selector(".error", timeout=5000)
        screen(page, "sad_01_invalid_email")

        expect(page.locator("#user-error")).to_contain_text("Invalid email")

        # Verify user was NOT created
        page.goto(f"{BASE_URL}/users/10")
        expect(page.locator("#user-error")).to_contain_text("not found")
        screen(page, "sad_01b_not_created")

    def test_email_missing_domain_shows_error(self, page: Page):
        """Submitting an email without a dot in domain should show error."""
        page.goto(f"{BASE_URL}/users/create")
        page.fill('input[name="id"]', "11")
        page.fill('input[name="name"]', "NoDomain")
        page.fill('input[name="email"]', "user@localhost")
        page.click('button[type="submit"]')
        page.wait_for_selector(".error", timeout=5000)
        screen(page, "sad_02_no_domain")

        expect(page.locator("#user-error")).to_contain_text("Invalid email")

    def test_empty_email_shows_error(self, page: Page):
        """Server-side validation of empty email (bypass client-side required attr).

        The HTML form has required attribute on email, which blocks empty
        submission via browser.  We remove the attribute so we can exercise
        the server-side path an attacker or script could still reach.

        FastAPI Form(...) treats empty strings as missing, returning a 422
        JSON response before the User constructor ever runs.  We verify that
        the server rejects the request with an error (no success shown).
        """
        page.goto(f"{BASE_URL}/users/create")
        # Remove the client-side 'required' constraint
        page.evaluate("document.querySelector('input[name=\"email\"]').removeAttribute('required')")
        page.fill('input[name="id"]', "12")
        page.fill('input[name="name"]', "EmptyEmail")
        page.fill('input[name="email"]', "")
        page.click('button[type="submit"]')

        # The server returns 422 JSON (not an HTML error page).
        # Wait for the response to settle and verify no success.
        page.wait_for_load_state("networkidle")
        screen(page, "sad_03_empty_email")

        # The server rejects the form — success message must not appear.
        expect(page.locator(".success")).not_to_be_visible()
        # Page body contains the 422 JSON error detail.
        expect(page.locator("body")).to_contain_text("Field required")

        # Verify the user was NOT created.
        page.goto(f"{BASE_URL}/users/12")
        expect(page.locator("#user-error")).to_contain_text("not found")

    def test_nonexistent_user_shows_not_found(self, page: Page):
        """Navigating to a user that does not exist shows a not-found error."""
        page.goto(f"{BASE_URL}/users/99999")
        screen(page, "sad_04_not_found")

        expect(page.locator("#user-error")).to_contain_text("not found")
        expect(page.locator("h1")).to_contain_text("Not Found")

    def test_api_rejects_invalid_role(self, page: Page):
        """The API should reject a user with an invalid role via POST JSON."""
        page.goto(f"{BASE_URL}/users/create")
        screen(page, "sad_05_api_test_setup")

        # Use fetch to POST invalid data to the API
        result = page.evaluate("""async () => {
            const resp = await fetch('/api/users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: 99,
                    name: 'Hacker',
                    email: 'hacker@evil.com',
                    role: 'superadmin'
                })
            });
            return { status: resp.status, body: await resp.json() };
        }""")
        assert result["status"] == 422
        assert "Invalid role" in result["body"]["error"]

        # Verify the user was NOT created
        page.goto(f"{BASE_URL}/users/99")
        screen(page, "sad_06_invalid_role_not_created")
        expect(page.locator("#user-error")).to_contain_text("not found")

    def test_api_returns_404_for_nonexistent_user(self, page: Page):
        """The API should return 404 for a non-existent user."""
        page.goto(f"{BASE_URL}/users")
        screen(page, "sad_07_api_404_setup")

        result = page.evaluate("""async () => {
            const resp = await fetch('/api/users/77777');
            return { status: resp.status, body: await resp.json() };
        }""")
        assert result["status"] == 404
        assert result["body"]["error"] == "not_found"
