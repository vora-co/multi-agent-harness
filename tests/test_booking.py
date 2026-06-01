"""Tests for the Booking domain model."""

from datetime import datetime

import pytest

from src.models.booking import Booking


class TestBookingCreation:
    """Tests for Booking instantiation and status validation."""

    def test_create_booking_with_status_confirmed(self):
        """Should create a booking with status 'confirmed'."""
        booking = Booking(
            id=1,
            user_id=10,
            session_id=100,
            status="confirmed",
        )
        assert booking.id == 1
        assert booking.user_id == 10
        assert booking.session_id == 100
        assert booking.status == "confirmed"
        assert isinstance(booking.created_at, datetime)

    def test_create_booking_with_status_cancelled(self):
        """Should create a booking with status 'cancelled'."""
        booking = Booking(
            id=2,
            user_id=20,
            session_id=200,
            status="cancelled",
        )
        assert booking.status == "cancelled"

    def test_create_booking_with_status_waitlist(self):
        """Should create a booking with status 'waitlist'."""
        booking = Booking(
            id=3,
            user_id=30,
            session_id=300,
            status="waitlist",
        )
        assert booking.status == "waitlist"

    def test_create_booking_default_status_is_waitlist(self):
        """Should default status to 'waitlist' when not provided."""
        booking = Booking(
            id=4,
            user_id=40,
            session_id=400,
        )
        assert booking.status == "waitlist"

    def test_create_booking_with_explicit_created_at(self):
        """Should accept an explicit created_at datetime."""
        dt = datetime(2025, 3, 15, 14, 30, 0)
        booking = Booking(
            id=5,
            user_id=50,
            session_id=500,
            status="confirmed",
            created_at=dt,
        )
        assert booking.created_at == dt


class TestStatusValidation:
    """Tests for status validation on invalid values."""

    def test_invalid_status_raises_valueerror(self):
        """Should raise ValueError for a status not in valid set."""
        with pytest.raises(ValueError, match="Invalid status"):
            Booking(
                id=6,
                user_id=60,
                session_id=600,
                status="pending",
            )

    def test_empty_status_raises_valueerror(self):
        """Should raise ValueError for an empty status string."""
        with pytest.raises(ValueError, match="Invalid status"):
            Booking(
                id=7,
                user_id=70,
                session_id=700,
                status="",
            )

    def test_none_status_raises_valueerror(self):
        """Should raise ValueError for None status."""
        with pytest.raises(ValueError, match="Invalid status"):
            Booking(
                id=8,
                user_id=80,
                session_id=800,
                status=None,
            )

    def test_arbitrary_string_status_raises_valueerror(self):
        """Should raise ValueError for an arbitrary string."""
        with pytest.raises(ValueError, match="Invalid status"):
            Booking(
                id=9,
                user_id=90,
                session_id=900,
                status="active",
            )


class TestToDict:
    """Tests for the to_dict serialization method."""

    def test_to_dict_contains_all_fields(self):
        """Should return a dict with all expected keys and values."""
        dt = datetime(2025, 4, 10, 9, 15, 0)
        booking = Booking(
            id=10,
            user_id=11,
            session_id=12,
            status="confirmed",
            created_at=dt,
        )
        d = booking.to_dict()
        assert d["id"] == 10
        assert d["user_id"] == 11
        assert d["session_id"] == 12
        assert d["status"] == "confirmed"
        assert d["created_at"] == "2025-04-10T09:15:00"

    def test_to_dict_default_values(self):
        """Should serialize with default status and created_at."""
        booking = Booking(
            id=11,
            user_id=22,
            session_id=33,
        )
        d = booking.to_dict()
        assert d["status"] == "waitlist"
        assert "created_at" in d


class TestFromDict:
    """Tests for the from_dict deserialization method."""

    def test_from_dict_basic(self):
        """Should reconstruct a Booking from a dict."""
        data = {
            "id": 12,
            "user_id": 34,
            "session_id": 56,
            "status": "cancelled",
            "created_at": "2025-05-20T11:00:00",
        }
        booking = Booking.from_dict(data)
        assert booking.id == 12
        assert booking.user_id == 34
        assert booking.session_id == 56
        assert booking.status == "cancelled"
        assert booking.created_at == datetime(2025, 5, 20, 11, 0, 0)

    def test_from_dict_with_default_status(self):
        """Should use default status='waitlist' when not provided."""
        data = {
            "id": 13,
            "user_id": 45,
            "session_id": 67,
            "created_at": "2025-06-01T08:00:00",
        }
        booking = Booking.from_dict(data)
        assert booking.status == "waitlist"

    def test_from_dict_accepts_datetime_object(self):
        """Should handle created_at as a datetime object directly."""
        dt = datetime(2025, 7, 4, 16, 0, 0)
        data = {
            "id": 14,
            "user_id": 78,
            "session_id": 89,
            "status": "confirmed",
            "created_at": dt,
        }
        booking = Booking.from_dict(data)
        assert booking.created_at == dt

    def test_to_dict_from_dict_roundtrip(self):
        """Should survive a to_dict -> from_dict round-trip unchanged."""
        original = Booking(
            id=15,
            user_id=99,
            session_id=88,
            status="confirmed",
        )
        serialized = original.to_dict()
        restored = Booking.from_dict(serialized)
        assert restored.id == original.id
        assert restored.user_id == original.user_id
        assert restored.session_id == original.session_id
        assert restored.status == original.status
        assert restored.created_at == original.created_at

    def test_from_dict_validates_status(self):
        """Should raise ValueError when from_dict data has invalid status."""
        data = {
            "id": 16,
            "user_id": 100,
            "session_id": 200,
            "status": "invalid_status",
        }
        with pytest.raises(ValueError, match="Invalid status"):
            Booking.from_dict(data)
