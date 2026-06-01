"""E2E tests for Booking model (Feature #3).

These tests verify Booking behaviour via a web frontend, complementing
the unit tests in tests/test_booking.py which cover status validation,
to_dict, from_dict, and round-trip at the Python level.

What E2E tests add:
  - User navigation flow (create -> view -> list)
  - Visual feedback on booking status badges (confirmed, cancelled, waitlist)
  - Error messages visible to the user on invalid status
  - 404 handling for non-existent bookings
  - Default status behaviour via the UI
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
    path = os.path.join(SCREENSHOT_DIR, f"feat3_{name}.png")
    page.screenshot(path=path, full_page=True)
    return path


class TestHappyPath:
    """End-to-end happy path: create a booking and verify its details."""

    def test_create_booking_confirmed_and_view_details(self, page: Page):
        """Create a confirmed booking via the form, then verify the detail page."""
        # 1. Go to create form
        page.goto(f"{BASE_URL}/bookings/create")
        screen(page, "happy_01_create_form")

        # 2. Fill in the form with confirmed status
        page.fill('input[name="id"]', "1")
        page.fill('input[name="user_id"]', "10")
        page.fill('input[name="session_id"]', "100")
        page.select_option('select[name="status"]', "confirmed")
        screen(page, "happy_02_form_filled")

        # 3. Submit the form
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_03_booking_created")

        # Verify success message and booking data visible
        expect(page.locator(".success")).to_contain_text("Booking created successfully")
        expect(page.locator("#booking-id")).to_contain_text("1")
        expect(page.locator("#booking-user-id")).to_contain_text("10")
        expect(page.locator("#booking-session-id")).to_contain_text("100")
        expect(page.locator("#booking-status")).to_contain_text("confirmed")
        # Status badge should have the right CSS class
        expect(page.locator(".badge-confirmed")).to_be_visible()
        # created_at should be present
        expect(page.locator("#booking-created-at")).not_to_be_empty()

        # 4. Navigate to booking detail page
        page.click('a:has-text("View booking details")')
        page.wait_for_url(f"**/bookings/1")
        screen(page, "happy_04_detail_page")

        # Verify detail page content
        expect(page.locator("#booking-id")).to_contain_text("1")
        expect(page.locator("#booking-user-id")).to_contain_text("10")
        expect(page.locator("#booking-session-id")).to_contain_text("100")
        expect(page.locator("#booking-status")).to_contain_text("confirmed")
        expect(page.locator(".badge-confirmed")).to_be_visible()
        expect(page.locator("h1")).to_contain_text("Booking #1")

    def test_create_booking_defaults_to_waitlist(self, page: Page):
        """When no status is selected, the booking should default to waitlist."""
        page.goto(f"{BASE_URL}/bookings/create")
        page.fill('input[name="id"]', "2")
        page.fill('input[name="user_id"]', "20")
        page.fill('input[name="session_id"]', "200")
        # Do NOT select a status — let it default (waitlist is the first option,
        # but we want to test the model default, so we leave the select as-is)
        screen(page, "happy_05_default_status_form")

        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_06_default_waitlist")

        # Status should be waitlist (default from the model)
        expect(page.locator("#booking-status")).to_contain_text("waitlist")
        expect(page.locator(".badge-waitlist")).to_be_visible()

    def test_create_booking_cancelled_and_list(self, page: Page):
        """Create a cancelled booking and verify it appears in the list."""
        page.goto(f"{BASE_URL}/bookings/create")
        page.fill('input[name="id"]', "3")
        page.fill('input[name="user_id"]', "30")
        page.fill('input[name="session_id"]', "300")
        page.select_option('select[name="status"]', "cancelled")
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "happy_07_cancelled_created")

        expect(page.locator("#booking-status")).to_contain_text("cancelled")
        expect(page.locator(".badge-cancelled")).to_be_visible()

        # 5. Go to list page and verify booking #3 appears
        page.goto(f"{BASE_URL}/bookings")
        screen(page, "happy_08_list_page")

        # Booking #3 should appear with cancelled status
        booking_section = page.locator("#booking-3")
        expect(booking_section).to_contain_text("User #30")
        expect(booking_section).to_contain_text("Session #300")
        expect(booking_section).to_contain_text("cancelled")
        expect(booking_section.locator(".badge-cancelled")).to_be_visible()

        # Booking #1 (confirmed) should also be in list
        expect(page.locator("#booking-1")).to_contain_text("confirmed")
        expect(page.locator("#booking-1 .badge-confirmed")).to_be_visible()

    def test_full_crud_flow_across_statuses(self, page: Page):
        """Create bookings with all three statuses, list them, and view details."""
        statuses = [
            ("10", "waitlist"),
            ("11", "confirmed"),
            ("12", "cancelled"),
        ]
        for bid, status in statuses:
            page.goto(f"{BASE_URL}/bookings/create")
            page.fill('input[name="id"]', bid)
            page.fill('input[name="user_id"]', "50")
            page.fill('input[name="session_id"]', "500")
            page.select_option('select[name="status"]', status)
            page.click('button[type="submit"]')
            page.wait_for_selector(".success", timeout=5000)
            expect(page.locator("#booking-status")).to_contain_text(status)

        # List all
        page.goto(f"{BASE_URL}/bookings")
        screen(page, "happy_09_all_statuses_list")
        for bid, _ in statuses:
            expect(page.locator(f"#booking-{bid}")).to_be_visible()

        # Detail view for one
        page.goto(f"{BASE_URL}/bookings/11")
        screen(page, "happy_10_detail_confirmed_11")
        expect(page.locator("#booking-status")).to_contain_text("confirmed")


class TestSadPath:
    """End-to-end sad paths: errors visible to the user."""

    def test_nonexistent_booking_shows_not_found(self, page: Page):
        """Navigating to a booking that does not exist shows a not-found error."""
        page.goto(f"{BASE_URL}/bookings/99999")
        screen(page, "sad_01_not_found")

        expect(page.locator("#booking-error")).to_contain_text("not found")
        expect(page.locator("h1")).to_contain_text("Not Found")
        # The error should be styled with .error class
        expect(page.locator(".error")).to_be_visible()

    def test_api_returns_422_for_invalid_status(self, page: Page):
        """The API should reject a booking with an invalid status via POST JSON."""
        # We test the API directly by navigating and using fetch via evaluate
        # But since we're testing from user perspective, let's test the form
        # by manipulating the HTML to submit an invalid status.
        # Actually, the form only allows valid statuses via <select>.
        # So we test: what if someone crafts an invalid request?
        # We'll use the API endpoint via page.evaluate to simulate this.
        page.goto(f"{BASE_URL}/bookings/create")
        screen(page, "sad_02_api_test_setup")

        # Use fetch to POST invalid data to the API
        result = page.evaluate("""async () => {
            const resp = await fetch('/api/bookings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    id: 99,
                    user_id: 1,
                    session_id: 1,
                    status: 'invalid_status'
                })
            });
            return { status: resp.status, body: await resp.json() };
        }""")
        assert result["status"] == 422
        assert "Invalid status" in result["body"]["error"]

        # Verify the booking was NOT created
        page.goto(f"{BASE_URL}/bookings/99")
        screen(page, "sad_03_invalid_not_created")
        expect(page.locator("#booking-error")).to_contain_text("not found")

    def test_api_returns_404_for_nonexistent_booking(self, page: Page):
        """The API should return 404 for a non-existent booking."""
        page.goto(f"{BASE_URL}/bookings")
        screen(page, "sad_04_api_404_setup")

        result = page.evaluate("""async () => {
            const resp = await fetch('/api/bookings/77777');
            return { status: resp.status, body: await resp.json() };
        }""")
        assert result["status"] == 404
        assert result["body"]["error"] == "not_found"
