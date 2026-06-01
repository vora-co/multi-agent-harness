"""Tests for the repository layer (users, sessions, bookings)."""

from datetime import datetime, timezone

import pytest

from src.models.user import User
from src.models.session import Session
from src.models.booking import Booking
from src.repositories.users import UserRepository
from src.repositories.sessions import SessionRepository
from src.repositories.bookings import BookingRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(id: int, name: str = "Test", email: str = "test@example.com") -> User:
    return User(id=id, name=name, email=email)


def _make_session(id: int, title: str = "Yoga") -> Session:
    return Session(
        id=id,
        title=title,
        instructor="Instructor",
        style="Hatha",
        starts_at=datetime(2025, 6, 1, 9, 0, 0),
        duration_minutes=60,
        capacity=20,
    )


def _make_booking(id: int, user_id: int = 1, session_id: int = 1) -> Booking:
    return Booking(id=id, user_id=user_id, session_id=session_id)


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------

class TestUserRepository:
    """Tests for UserRepository CRUD operations."""

    def test_save_and_find_all(self, tmp_path):
        """Should persist a user and retrieve it via find_all."""
        repo = UserRepository(data_dir=str(tmp_path))
        user = _make_user(1, "Alice", "alice@example.com")
        repo.save_one(user)

        all_users = repo.find_all()
        assert len(all_users) == 1
        assert all_users[0].id == 1
        assert all_users[0].name == "Alice"

    def test_save_and_find_by_id(self, tmp_path):
        """Should find a saved user by its id."""
        repo = UserRepository(data_dir=str(tmp_path))
        repo.save_one(_make_user(1, "Alice", "alice@example.com"))
        repo.save_one(_make_user(2, "Bob", "bob@example.com"))

        found = repo.find_by_id(2)
        assert found is not None
        assert found.name == "Bob"
        assert found.email == "bob@example.com"

    def test_find_by_id_returns_none_when_not_found(self, tmp_path):
        """Should return None when the id does not exist."""
        repo = UserRepository(data_dir=str(tmp_path))
        repo.save_one(_make_user(1, "Alice", "alice@example.com"))

        assert repo.find_by_id(999) is None

    def test_find_by_id_returns_none_when_empty(self, tmp_path):
        """Should return None for any id when no records exist."""
        repo = UserRepository(data_dir=str(tmp_path))
        assert repo.find_by_id(1) is None

    def test_delete_removes_only_the_correct_record(self, tmp_path):
        """Should delete the targeted user and leave others intact."""
        repo = UserRepository(data_dir=str(tmp_path))
        repo.save_one(_make_user(1, "Alice", "alice@example.com"))
        repo.save_one(_make_user(2, "Bob", "bob@example.com"))
        repo.save_one(_make_user(3, "Charlie", "charlie@example.com"))

        deleted = repo.delete(2)
        assert deleted is True

        all_users = repo.find_all()
        ids = {u.id for u in all_users}
        assert ids == {1, 3}

    def test_delete_returns_false_when_not_found(self, tmp_path):
        """Should return False when trying to delete a non-existent id."""
        repo = UserRepository(data_dir=str(tmp_path))
        repo.save_one(_make_user(1, "Alice", "alice@example.com"))

        assert repo.delete(999) is False

    def test_save_one_updates_existing_record(self, tmp_path):
        """Should update a record when saving with an existing id."""
        repo = UserRepository(data_dir=str(tmp_path))
        repo.save_one(_make_user(1, "Alice", "alice@example.com"))

        updated = User(id=1, name="Alice Updated", email="alice@example.com",
                       credits=10, role="admin")
        repo.save_one(updated)

        found = repo.find_by_id(1)
        assert found is not None
        assert found.name == "Alice Updated"
        assert found.credits == 10
        assert found.role == "admin"

        # Should still be only one record
        assert len(repo.find_all()) == 1


# ---------------------------------------------------------------------------
# SessionRepository
# ---------------------------------------------------------------------------

class TestSessionRepository:
    """Tests for SessionRepository CRUD operations."""

    def test_save_and_find_all(self, tmp_path):
        """Should persist a session and retrieve it via find_all."""
        repo = SessionRepository(data_dir=str(tmp_path))
        session = _make_session(1, "Morning Yoga")
        repo.save_one(session)

        all_sessions = repo.find_all()
        assert len(all_sessions) == 1
        assert all_sessions[0].id == 1
        assert all_sessions[0].title == "Morning Yoga"

    def test_find_by_id_returns_none_when_not_found(self, tmp_path):
        """Should return None for a non-existent session id."""
        repo = SessionRepository(data_dir=str(tmp_path))
        repo.save_one(_make_session(1, "Yoga"))

        assert repo.find_by_id(999) is None

    def test_delete_removes_only_the_correct_record(self, tmp_path):
        """Should delete only the session with the matching id."""
        repo = SessionRepository(data_dir=str(tmp_path))
        repo.save_one(_make_session(1, "Yoga"))
        repo.save_one(_make_session(2, "Pilates"))
        repo.save_one(_make_session(3, "Meditation"))

        deleted = repo.delete(2)
        assert deleted is True

        all_sessions = repo.find_all()
        ids = {s.id for s in all_sessions}
        assert ids == {1, 3}

    def test_delete_returns_false_when_not_found(self, tmp_path):
        """Should return False when deleting a non-existent session."""
        repo = SessionRepository(data_dir=str(tmp_path))
        repo.save_one(_make_session(1, "Yoga"))

        assert repo.delete(999) is False

    def test_save_one_updates_existing_record(self, tmp_path):
        """Should update an existing session on save with same id."""
        repo = SessionRepository(data_dir=str(tmp_path))
        repo.save_one(_make_session(1, "Yoga"))

        updated = Session(
            id=1,
            title="Advanced Yoga",
            instructor="New Instructor",
            style="Vinyasa",
            starts_at=datetime(2025, 7, 1, 10, 0, 0),
            duration_minutes=90,
            capacity=15,
            enrolled=5,
        )
        repo.save_one(updated)

        found = repo.find_by_id(1)
        assert found is not None
        assert found.title == "Advanced Yoga"
        assert found.instructor == "New Instructor"
        assert found.style == "Vinyasa"
        assert found.duration_minutes == 90
        assert found.capacity == 15
        assert found.enrolled == 5


# ---------------------------------------------------------------------------
# BookingRepository
# ---------------------------------------------------------------------------

class TestBookingRepository:
    """Tests for BookingRepository CRUD operations."""

    def test_save_and_find_all(self, tmp_path):
        """Should persist a booking and retrieve it via find_all."""
        repo = BookingRepository(data_dir=str(tmp_path))
        booking = _make_booking(1, user_id=10, session_id=100)
        repo.save_one(booking)

        all_bookings = repo.find_all()
        assert len(all_bookings) == 1
        assert all_bookings[0].id == 1
        assert all_bookings[0].user_id == 10
        assert all_bookings[0].session_id == 100

    def test_find_by_id_returns_none_when_not_found(self, tmp_path):
        """Should return None for a non-existent booking id."""
        repo = BookingRepository(data_dir=str(tmp_path))
        repo.save_one(_make_booking(1))

        assert repo.find_by_id(999) is None

    def test_delete_removes_only_the_correct_record(self, tmp_path):
        """Should delete only the booking with the matching id."""
        repo = BookingRepository(data_dir=str(tmp_path))
        repo.save_one(_make_booking(1, user_id=10, session_id=100))
        repo.save_one(_make_booking(2, user_id=20, session_id=200))
        repo.save_one(_make_booking(3, user_id=30, session_id=300))

        deleted = repo.delete(2)
        assert deleted is True

        all_bookings = repo.find_all()
        ids = {b.id for b in all_bookings}
        assert ids == {1, 3}

    def test_delete_returns_false_when_not_found(self, tmp_path):
        """Should return False when deleting a non-existent booking."""
        repo = BookingRepository(data_dir=str(tmp_path))
        repo.save_one(_make_booking(1))

        assert repo.delete(999) is False

    def test_save_one_updates_existing_record(self, tmp_path):
        """Should update an existing booking on save with same id."""
        repo = BookingRepository(data_dir=str(tmp_path))
        repo.save_one(_make_booking(1, user_id=10, session_id=100))

        updated = Booking(
            id=1,
            user_id=10,
            session_id=200,
            status="confirmed",
        )
        repo.save_one(updated)

        found = repo.find_by_id(1)
        assert found is not None
        assert found.session_id == 200
        assert found.status == "confirmed"
        assert len(repo.find_all()) == 1
