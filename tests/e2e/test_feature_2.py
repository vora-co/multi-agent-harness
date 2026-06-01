"""E2E tests for Session model (Feature #2).

These tests verify Session behaviour via a web frontend, complementing
the unit tests in tests/test_session.py which cover validation,
is_full, spots_available, to_dict, and from_dict at the Python level.

What E2E tests add:
  - User navigation flow (create -> view -> list)
  - Visual feedback on is_full / spots_available badges
  - Error messages visible to the user
  - 404 handling for non-existent sessions
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
    path = os.path.join(SCREENSHOT_DIR, f"feat2_{name}.png")
    page.screenshot(path=path, full_page=True)
    return path


class TestHappyPath:
    """End-to-end happy path: create a session and verify its details."""

    def test_create_session_and_view_details(self, page: Page):
        """Create a valid session via the form, then verify the detail page."""
        # 1. Go to create form
        page.goto(f"{BASE_URL}/sessions/create")
        screen(page, "happy_01_create_form")

        # 2. Fill in the form
        page.fill('input[name="id"]', "100")
        page.fill('input[name="title"]', "E2E Morning Flow")
        page.fill('input[name="instructor"]', "Aria")
        page.fill('input[name="style"]', "Vinyasa")
        page.fill('input[name="starts_at"]', "2025-06-15T09:00:00")
        page.fill('input[name="duration_minutes"]', "60")
        page.fill('input[name="capacity"]', "20")
        page.fill('input[name="enrolled"]', "12")
        screen(page, "happy_02_form_filled")

        # 3. Submit the form
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_03_session_created")

        # Verify success message and session data visible
        expect(page.locator(".success")).to_contain_text("Session created successfully")
        expect(page.locator(".session")).to_contain_text("E2E Morning Flow")
        expect(page.locator(".session")).to_contain_text("Aria")
        expect(page.locator(".session")).to_contain_text("Vinyasa")
        expect(page.locator(".session")).to_contain_text("2025-06-15T09:00:00")
        expect(page.locator(".session")).to_contain_text("60 min")
        expect(page.locator(".session")).to_contain_text("20")   # capacity
        expect(page.locator(".session")).to_contain_text("12")   # enrolled

        # is_full should be False (12 < 20)
        expect(page.locator(".badge-available")).to_contain_text("False")
        # spots_available should be 8
        expect(page.locator(".session")).to_contain_text("8")

        # 4. Navigate to session detail page
        page.click('a:has-text("View session details")')
        page.wait_for_url(f"**/sessions/100")
        screen(page, "happy_04_detail_page")

        # Verify detail page content
        expect(page.locator("#is-full-badge")).to_contain_text("No")
        expect(page.locator("#spots-available")).to_contain_text("8")
        expect(page.locator("h1")).to_contain_text("Session #100")

    def test_list_sessions_shows_correct_status(self, page: Page):
        """List sessions page should show correct is_full / spots for each session."""
        page.goto(f"{BASE_URL}/sessions")
        screen(page, "happy_05_list_page")

        # Session #100 should appear in the list
        expect(page.locator(".session")).to_contain_text("#100: E2E Morning Flow")
        # Status should show spots available (not FULL)
        expect(page.locator(".session")).to_contain_text("8 spots left")
        # badge should be badge-available
        expect(page.locator(".badge-available")).to_be_visible()

    def test_full_session_shows_full_badge(self, page: Page):
        """Create a fully-booked session and verify FULL badge everywhere."""
        page.goto(f"{BASE_URL}/sessions/create")
        page.fill('input[name="id"]', "200")
        page.fill('input[name="title"]', "Full House")
        page.fill('input[name="instructor"]', "Max")
        page.fill('input[name="style"]', "Power")
        page.fill('input[name="starts_at"]', "2025-07-01T10:00:00")
        page.fill('input[name="duration_minutes"]', "45")
        page.fill('input[name="capacity"]', "10")
        page.fill('input[name="enrolled"]', "10")  # exactly full
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_06_full_session_created")

        # is_full should be True
        expect(page.locator(".badge-full")).to_contain_text("True")
        # spots_available should be 0
        expect(page.locator(".session")).to_contain_text("0")

        # Navigate to detail
        page.click('a:has-text("View session details")')
        page.wait_for_url("**/sessions/200")
        screen(page, "happy_07_full_detail")

        expect(page.locator("#is-full-badge")).to_contain_text("Yes")
        expect(page.locator("#spots-available")).to_contain_text("0")
        expect(page.locator(".badge-full")).to_be_visible()

        # Go to list, verify FULL badge there too
        page.goto(f"{BASE_URL}/sessions")
        screen(page, "happy_08_full_in_list")
        expect(page.locator(".session").filter(has_text="Full House")).to_contain_text("FULL")


class TestSadPath:
    """End-to-end sad paths: errors visible to the user."""

    def test_create_session_with_capacity_zero_shows_error(self, page: Page):
        """Submitting capacity=0 should display a validation error."""
        page.goto(f"{BASE_URL}/sessions/create")
        page.fill('input[name="id"]', "300")
        page.fill('input[name="title"]', "Bad Capacity")
        page.fill('input[name="instructor"]', "Error")
        page.fill('input[name="style"]', "Flow")
        page.fill('input[name="starts_at"]', "2025-08-01T09:00:00")
        page.fill('input[name="duration_minutes"]', "30")
        page.fill('input[name="capacity"]', "0")
        page.click('button[type="submit"]')
        page.wait_for_selector(".error", timeout=5000)
        screen(page, "sad_01_capacity_zero_error")

        expect(page.locator(".error")).to_contain_text("capacity must be >= 1")
        # Session #300 should NOT exist
        page.goto(f"{BASE_URL}/sessions/300")
        expect(page.locator(".error")).to_contain_text("not found")
        screen(page, "sad_01b_not_found_capacity")

    def test_create_session_with_duration_five_shows_error(self, page: Page):
        """Submitting duration_minutes=5 should display a validation error."""
        page.goto(f"{BASE_URL}/sessions/create")
        page.fill('input[name="id"]', "400")
        page.fill('input[name="title"]', "Too Short")
        page.fill('input[name="instructor"]', "Quick")
        page.fill('input[name="style"]', "Express")
        page.fill('input[name="starts_at"]', "2025-08-02T12:00:00")
        page.fill('input[name="duration_minutes"]', "5")
        page.fill('input[name="capacity"]', "10")
        page.click('button[type="submit"]')
        page.wait_for_selector(".error", timeout=5000)
        screen(page, "sad_02_duration_error")

        expect(page.locator(".error")).to_contain_text("duration_minutes must be >= 15")

    def test_nonexistent_session_shows_404(self, page: Page):
        """Navigating to a session that does not exist shows a not-found error."""
        page.goto(f"{BASE_URL}/sessions/99999")
        screen(page, "sad_03_not_found")

        expect(page.locator(".error")).to_contain_text("not found")
        expect(page.locator("h1")).to_contain_text("Not Found")
