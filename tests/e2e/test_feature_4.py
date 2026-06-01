"""E2E tests for Storage layer + Repositories (Feature #4).

These tests verify the persistence layer end-to-end, complementing
the unit tests in tests/test_storage.py and tests/test_repositories.py
which cover individual CRUD operations in isolation.

What E2E tests add:
  - Full lifecycle flows (create -> read -> update -> read -> delete -> verify gone)
  - Multi-entity coexistence in the same data directory
  - Real file-system verification (JSON files exist, have correct content)
  - Atomic writes (no .tmp files left behind)
  - Data directory auto-creation
  - Cross-entity isolation (deleting one entity type doesn't affect others)
  - Visual evidence via Playwright screenshots of the running web app
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect as playwright_expect

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from src.storage import load, save
from src.models.user import User
from src.models.session import Session
from src.models.booking import Booking
from src.repositories.users import UserRepository
from src.repositories.sessions import SessionRepository
from src.repositories.bookings import BookingRepository

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def ensure_screenshot_dir():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def screen(page: Page, name: str):
    """Take a screenshot with the given name."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat4_{name}.png")
    page.screenshot(path=path, full_page=True)
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def data_dir():
    """Create a temporary directory for storage tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ---------------------------------------------------------------------------
# Storage layer E2E tests
# ---------------------------------------------------------------------------

class TestStorageE2E:
    """End-to-end tests for the storage layer with real file I/O."""

    def test_full_write_read_update_cycle(self, page: Page, data_dir: str):
        """Complete lifecycle: write -> read -> update -> read -> verify on disk."""
        # 1. Verify the web app is running
        page.goto(f"{BASE_URL}/")
        page.wait_for_selector("h1", timeout=10000)
        screen(page, "storage_01_app_running")

        # 2. Write records
        records = [
            {"id": 1, "name": "Alpha", "value": 100},
            {"id": 2, "name": "Beta", "value": 200},
        ]
        save("test_entity", records, data_dir=data_dir)

        # 3. Verify file exists on disk
        filepath = Path(data_dir) / "test_entity.json"
        assert filepath.exists(), f"Expected file at {filepath}"
        screen(page, "storage_02_data_written")

        # 4. Read back and verify
        loaded = load("test_entity", data_dir=data_dir)
        assert len(loaded) == 2
        assert loaded[0]["name"] == "Alpha"
        assert loaded[1]["name"] == "Beta"

        # 5. Update (overwrite)
        new_records = [
            {"id": 1, "name": "Alpha Updated", "value": 150},
            {"id": 3, "name": "Gamma", "value": 300},
        ]
        save("test_entity", new_records, data_dir=data_dir)
        loaded = load("test_entity", data_dir=data_dir)
        assert len(loaded) == 2
        assert loaded[0]["name"] == "Alpha Updated"
        assert loaded[1]["name"] == "Gamma"

        # 6. Verify raw JSON on disk
        raw = filepath.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed == new_records
        screen(page, "storage_03_full_cycle_ok")

    def test_data_directory_auto_created(self, page: Page, data_dir: str):
        """Storage should create the data directory if it doesn't exist."""
        page.goto(f"{BASE_URL}/")
        screen(page, "storage_04_app_running")

        nested_dir = os.path.join(data_dir, "nested", "deep", "storage")
        assert not os.path.exists(nested_dir)

        save("auto_create", [{"id": 1}], data_dir=nested_dir)
        assert os.path.exists(nested_dir)
        assert os.path.exists(os.path.join(nested_dir, "auto_create.json"))

        # Verify data is readable
        loaded = load("auto_create", data_dir=nested_dir)
        assert loaded == [{"id": 1}]
        screen(page, "storage_05_dir_created")

    def test_atomic_write_no_tmp_files(self, page: Page, data_dir: str):
        """After successful save, no .tmp files should remain."""
        page.goto(f"{BASE_URL}/")
        screen(page, "storage_06_app_running")

        # Multiple saves
        for i in range(5):
            save("atomic_test", [{"id": i, "data": f"record_{i}"}], data_dir=data_dir)

        # Verify data is correct
        loaded = load("atomic_test", data_dir=data_dir)
        assert len(loaded) == 1  # Each save overwrites
        assert loaded[0]["id"] == 4

        # No .tmp files
        tmp_files = list(Path(data_dir).glob("*.tmp"))
        assert len(tmp_files) == 0, f"Found .tmp files: {tmp_files}"
        screen(page, "storage_07_atomic_ok")

    def test_load_returns_empty_for_nonexistent_entity(self, page: Page, data_dir: str):
        """Loading a non-existent entity should return empty list, not error."""
        page.goto(f"{BASE_URL}/")
        screen(page, "storage_08_app_running")

        result = load("nonexistent_entity_xyz", data_dir=data_dir)
        assert result == []
        screen(page, "storage_09_empty_ok")


# ---------------------------------------------------------------------------
# Repository E2E tests: UserRepository
# ---------------------------------------------------------------------------

class TestUserRepositoryE2E:
    """End-to-end lifecycle tests for UserRepository."""

    def test_full_user_lifecycle(self, page: Page, data_dir: str):
        """Create -> read -> update -> read -> delete -> verify gone."""
        page.goto(f"{BASE_URL}/")
        screen(page, "user_repo_01_app_running")

        repo = UserRepository(data_dir=data_dir)

        # 1. Create
        user = User(id=1, name="Alice", email="alice@example.com", credits=10, role="client")
        repo.save_one(user)

        # 2. Read by ID
        found = repo.find_by_id(1)
        assert found is not None
        assert found.name == "Alice"
        assert found.email == "alice@example.com"
        assert found.credits == 10
        assert found.role == "client"

        # 3. Read all
        all_users = repo.find_all()
        assert len(all_users) == 1
        screen(page, "user_repo_02_created_and_read")

        # 4. Update
        updated = User(id=1, name="Alice Updated", email="alice@example.com",
                       credits=25, role="admin")
        repo.save_one(updated)
        found = repo.find_by_id(1)
        assert found.name == "Alice Updated"
        assert found.credits == 25
        assert found.role == "admin"
        assert len(repo.find_all()) == 1  # Still just one
        screen(page, "user_repo_03_updated")

        # 5. Delete
        deleted = repo.delete(1)
        assert deleted is True

        # 6. Verify gone
        assert repo.find_by_id(1) is None
        assert repo.find_all() == []
        screen(page, "user_repo_04_deleted")

    def test_multiple_users_coexistence(self, page: Page, data_dir: str):
        """Multiple users should coexist and be independently accessible."""
        page.goto(f"{BASE_URL}/")
        repo = UserRepository(data_dir=data_dir)

        repo.save_one(User(id=1, name="Alice", email="alice@example.com"))
        repo.save_one(User(id=2, name="Bob", email="bob@example.com"))
        repo.save_one(User(id=3, name="Charlie", email="charlie@example.com"))

        all_users = repo.find_all()
        assert len(all_users) == 3
        ids = {u.id for u in all_users}
        assert ids == {1, 2, 3}
        screen(page, "user_repo_05_multiple")

    def test_update_preserves_json_on_disk(self, page: Page, data_dir: str):
        """After updating a user, the JSON file should be valid and complete."""
        page.goto(f"{BASE_URL}/")
        repo = UserRepository(data_dir=data_dir)

        repo.save_one(User(id=1, name="Original", email="orig@example.com"))
        repo.save_one(User(id=1, name="Updated", email="updated@example.com",
                           credits=99, role="admin"))

        # Verify JSON file is valid
        filepath = Path(data_dir) / "users.json"
        assert filepath.exists()
        raw = filepath.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert len(parsed) == 1
        assert parsed[0]["name"] == "Updated"
        assert parsed[0]["credits"] == 99
        assert parsed[0]["role"] == "admin"
        screen(page, "user_repo_06_json_ok")


# ---------------------------------------------------------------------------
# Repository E2E tests: SessionRepository
# ---------------------------------------------------------------------------

class TestSessionRepositoryE2E:
    """End-to-end lifecycle tests for SessionRepository."""

    def test_full_session_lifecycle(self, page: Page, data_dir: str):
        """Create -> read -> update -> read -> delete -> verify gone."""
        page.goto(f"{BASE_URL}/")
        screen(page, "session_repo_01_app_running")

        repo = SessionRepository(data_dir=data_dir)

        # 1. Create
        session = Session(
            id=100,
            title="Morning Yoga",
            instructor="Aria",
            style="Vinyasa",
            starts_at=datetime(2025, 6, 15, 9, 0, 0),
            duration_minutes=60,
            capacity=20,
            enrolled=5,
        )
        repo.save_one(session)

        # 2. Read
        found = repo.find_by_id(100)
        assert found is not None
        assert found.title == "Morning Yoga"
        assert found.instructor == "Aria"
        assert found.capacity == 20
        assert found.enrolled == 5
        assert found.is_full() is False
        assert found.spots_available() == 15

        screen(page, "session_repo_02_created")

        # 3. Update
        updated = Session(
            id=100,
            title="Advanced Yoga",
            instructor="Aria",
            style="Power",
            starts_at=datetime(2025, 7, 1, 10, 0, 0),
            duration_minutes=90,
            capacity=15,
            enrolled=15,  # full
        )
        repo.save_one(updated)
        found = repo.find_by_id(100)
        assert found.title == "Advanced Yoga"
        assert found.style == "Power"
        assert found.is_full() is True
        assert found.spots_available() == 0
        screen(page, "session_repo_03_updated")

        # 4. Delete
        deleted = repo.delete(100)
        assert deleted is True
        assert repo.find_by_id(100) is None
        screen(page, "session_repo_04_deleted")


# ---------------------------------------------------------------------------
# Repository E2E tests: BookingRepository
# ---------------------------------------------------------------------------

class TestBookingRepositoryE2E:
    """End-to-end lifecycle tests for BookingRepository."""

    def test_full_booking_lifecycle(self, page: Page, data_dir: str):
        """Create -> read -> update -> read -> delete -> verify gone."""
        page.goto(f"{BASE_URL}/")
        screen(page, "booking_repo_01_app_running")

        repo = BookingRepository(data_dir=data_dir)

        # 1. Create
        booking = Booking(id=1, user_id=10, session_id=100, status="waitlist")
        repo.save_one(booking)

        # 2. Read
        found = repo.find_by_id(1)
        assert found is not None
        assert found.user_id == 10
        assert found.session_id == 100
        assert found.status == "waitlist"
        screen(page, "booking_repo_02_created")

        # 3. Update status
        updated = Booking(id=1, user_id=10, session_id=100, status="confirmed")
        repo.save_one(updated)
        found = repo.find_by_id(1)
        assert found.status == "confirmed"
        screen(page, "booking_repo_03_updated")

        # 4. Delete
        deleted = repo.delete(1)
        assert deleted is True
        assert repo.find_by_id(1) is None
        screen(page, "booking_repo_04_deleted")

    def test_multiple_bookings_different_statuses(self, page: Page, data_dir: str):
        """Bookings with different statuses should coexist."""
        page.goto(f"{BASE_URL}/")
        repo = BookingRepository(data_dir=data_dir)

        repo.save_one(Booking(id=1, user_id=10, session_id=100, status="confirmed"))
        repo.save_one(Booking(id=2, user_id=20, session_id=100, status="waitlist"))
        repo.save_one(Booking(id=3, user_id=30, session_id=200, status="cancelled"))

        all_bookings = repo.find_all()
        assert len(all_bookings) == 3

        statuses = {b.status for b in all_bookings}
        assert statuses == {"confirmed", "waitlist", "cancelled"}
        screen(page, "booking_repo_05_multiple")


# ---------------------------------------------------------------------------
# Sad path E2E tests
# ---------------------------------------------------------------------------

class TestSadPaths:
    """End-to-end sad paths for repositories and storage."""

    def test_find_by_id_returns_none_for_all_repos(self, page: Page, data_dir: str):
        """All three repositories should return None for non-existent IDs."""
        page.goto(f"{BASE_URL}/")
        screen(page, "sad_01_app_running")

        user_repo = UserRepository(data_dir=data_dir)
        session_repo = SessionRepository(data_dir=data_dir)
        booking_repo = BookingRepository(data_dir=data_dir)

        assert user_repo.find_by_id(99999) is None
        assert session_repo.find_by_id(99999) is None
        assert booking_repo.find_by_id(99999) is None

        screen(page, "sad_02_none_ok")

    def test_delete_returns_false_when_not_found(self, page: Page, data_dir: str):
        """Delete should return False for non-existent IDs."""
        page.goto(f"{BASE_URL}/")
        screen(page, "sad_03_app_running")

        user_repo = UserRepository(data_dir=data_dir)
        session_repo = SessionRepository(data_dir=data_dir)
        booking_repo = BookingRepository(data_dir=data_dir)

        # Populate some data first
        user_repo.save_one(User(id=1, name="Test", email="test@example.com"))
        session_repo.save_one(Session(
            id=1, title="Test", instructor="T", style="S",
            starts_at=datetime(2025, 1, 1), duration_minutes=30, capacity=10,
        ))
        booking_repo.save_one(Booking(id=1, user_id=1, session_id=1))

        # Now try to delete non-existent
        assert user_repo.delete(99999) is False
        assert session_repo.delete(99999) is False
        assert booking_repo.delete(99999) is False

        # Existing data should still be there
        assert user_repo.find_by_id(1) is not None
        assert session_repo.find_by_id(1) is not None
        assert booking_repo.find_by_id(1) is not None

        screen(page, "sad_04_delete_false_ok")

    def test_cross_entity_isolation(self, page: Page, data_dir: str):
        """Deleting from one repository should not affect others."""
        page.goto(f"{BASE_URL}/")
        screen(page, "sad_05_app_running")

        user_repo = UserRepository(data_dir=data_dir)
        session_repo = SessionRepository(data_dir=data_dir)
        booking_repo = BookingRepository(data_dir=data_dir)

        # Populate all three
        user_repo.save_one(User(id=1, name="Alice", email="alice@example.com"))
        session_repo.save_one(Session(
            id=1, title="Yoga", instructor="A", style="Hatha",
            starts_at=datetime(2025, 6, 1, 9, 0), duration_minutes=60, capacity=20,
        ))
        booking_repo.save_one(Booking(id=1, user_id=1, session_id=1))

        # Delete user
        user_repo.delete(1)

        # Session and booking should still exist
        assert session_repo.find_by_id(1) is not None
        assert booking_repo.find_by_id(1) is not None

        # User should be gone
        assert user_repo.find_by_id(1) is None
        screen(page, "sad_06_isolation_ok")

    def test_load_nonexistent_file_after_other_operations(self, page: Page, data_dir: str):
        """After writing some entities, loading a different non-existent entity returns []."""
        page.goto(f"{BASE_URL}/")
        repo = UserRepository(data_dir=data_dir)
        repo.save_one(User(id=1, name="Test", email="test@example.com"))

        # Load a different entity that was never saved
        result = load("non_existent_entity", data_dir=data_dir)
        assert result == []
        screen(page, "sad_07_nonexistent_ok")


# ---------------------------------------------------------------------------
# Integration: Repository + Web app coexistence
# ---------------------------------------------------------------------------

class TestRepoWebIntegration:
    """Verify repositories work alongside the running web app."""

    def test_repo_and_web_app_independent(self, page: Page, data_dir: str):
        """Repository operations should not interfere with web app,
        and web app should remain functional during repo operations."""
        page.goto(f"{BASE_URL}/")
        page.wait_for_selector("h1", timeout=10000)
        screen(page, "integ_01_app_running")

        # Create a user via web UI
        page.goto(f"{BASE_URL}/users/create")
        page.fill('input[name="id"]', "50")
        page.fill('input[name="name"]', "WebUser")
        page.fill('input[name="email"]', "webuser@example.com")
        page.click('button[type="submit"]')
        page.wait_for_selector(".success", timeout=5000)
        screen(page, "integ_02_web_user_created")

        # Verify web app still works
        page.goto(f"{BASE_URL}/users/50")
        playwright_expect(page.locator("#user-name")).to_contain_text("WebUser")

        # Meanwhile, repository operations work fine in the background
        repo = UserRepository(data_dir=data_dir)
        repo.save_one(User(id=99, name="RepoUser", email="repouser@example.com"))
        found = repo.find_by_id(99)
        assert found is not None
        assert found.name == "RepoUser"

        screen(page, "integ_03_both_ok")
