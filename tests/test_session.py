"""Tests for the Session domain model."""

from datetime import datetime

import pytest

from src.models.session import Session


class TestSessionCreation:
    """Tests for Session instantiation and validation."""

    def test_create_valid_session(self):
        """Should create a session with valid parameters."""
        dt = datetime(2025, 6, 15, 9, 0, 0)
        session = Session(
            id=1,
            title="Morning Yoga",
            instructor="Alice",
            style="Vinyasa",
            starts_at=dt,
            duration_minutes=60,
            capacity=20,
        )
        assert session.id == 1
        assert session.title == "Morning Yoga"
        assert session.instructor == "Alice"
        assert session.style == "Vinyasa"
        assert session.starts_at == dt
        assert session.duration_minutes == 60
        assert session.capacity == 20
        assert session.enrolled == 0

    def test_create_session_with_explicit_enrolled(self):
        """Should accept an explicit enrolled value."""
        dt = datetime(2025, 6, 15, 10, 0, 0)
        session = Session(
            id=2,
            title="Power Flow",
            instructor="Bob",
            style="Power",
            starts_at=dt,
            duration_minutes=45,
            capacity=15,
            enrolled=5,
        )
        assert session.enrolled == 5

    def test_create_session_minimum_valid_capacity(self):
        """Should accept capacity=1 (the minimum valid value)."""
        dt = datetime(2025, 7, 1, 8, 0, 0)
        session = Session(
            id=3,
            title="Solo Session",
            instructor="Charlie",
            style="Private",
            starts_at=dt,
            duration_minutes=30,
            capacity=1,
        )
        assert session.capacity == 1

    def test_create_session_minimum_valid_duration(self):
        """Should accept duration_minutes=15 (the minimum valid value)."""
        dt = datetime(2025, 7, 2, 12, 0, 0)
        session = Session(
            id=4,
            title="Quick Stretch",
            instructor="Diana",
            style="Hatha",
            starts_at=dt,
            duration_minutes=15,
            capacity=10,
        )
        assert session.duration_minutes == 15


class TestValidationErrors:
    """Tests for validation errors on invalid parameters."""

    def test_capacity_zero_raises_valueerror(self):
        """Should raise ValueError when capacity is 0."""
        dt = datetime(2025, 8, 1, 9, 0, 0)
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            Session(
                id=5,
                title="Invalid",
                instructor="Eve",
                style="Flow",
                starts_at=dt,
                duration_minutes=30,
                capacity=0,
            )

    def test_capacity_negative_raises_valueerror(self):
        """Should raise ValueError when capacity is negative."""
        dt = datetime(2025, 8, 2, 9, 0, 0)
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            Session(
                id=6,
                title="Invalid",
                instructor="Frank",
                style="Flow",
                starts_at=dt,
                duration_minutes=30,
                capacity=-1,
            )

    def test_duration_below_minimum_raises_valueerror(self):
        """Should raise ValueError when duration_minutes is below 15."""
        dt = datetime(2025, 8, 3, 9, 0, 0)
        with pytest.raises(ValueError, match="duration_minutes must be >= 15"):
            Session(
                id=7,
                title="Too Short",
                instructor="Grace",
                style="Yin",
                starts_at=dt,
                duration_minutes=10,
                capacity=10,
            )

    def test_duration_zero_raises_valueerror(self):
        """Should raise ValueError when duration_minutes is 0."""
        dt = datetime(2025, 8, 4, 9, 0, 0)
        with pytest.raises(ValueError, match="duration_minutes must be >= 15"):
            Session(
                id=8,
                title="Zero Duration",
                instructor="Hank",
                style="Yin",
                starts_at=dt,
                duration_minutes=0,
                capacity=10,
            )

    def test_duration_negative_raises_valueerror(self):
        """Should raise ValueError when duration_minutes is negative."""
        dt = datetime(2025, 8, 5, 9, 0, 0)
        with pytest.raises(ValueError, match="duration_minutes must be >= 15"):
            Session(
                id=9,
                title="Negative Duration",
                instructor="Ivy",
                style="Yin",
                starts_at=dt,
                duration_minutes=-5,
                capacity=10,
            )


class TestIsFull:
    """Tests for the is_full method."""

    def test_is_full_false_when_enrolled_below_capacity(self):
        """Should return False when enrolled < capacity."""
        dt = datetime(2025, 9, 1, 10, 0, 0)
        session = Session(
            id=10,
            title="Has Spots",
            instructor="Jack",
            style="Vinyasa",
            starts_at=dt,
            duration_minutes=60,
            capacity=20,
            enrolled=10,
        )
        assert session.is_full() is False

    def test_is_full_true_when_enrolled_equals_capacity(self):
        """Should return True when enrolled == capacity."""
        dt = datetime(2025, 9, 2, 10, 0, 0)
        session = Session(
            id=11,
            title="Full",
            instructor="Kate",
            style="Hatha",
            starts_at=dt,
            duration_minutes=45,
            capacity=15,
            enrolled=15,
        )
        assert session.is_full() is True

    def test_is_full_true_when_enrolled_exceeds_capacity(self):
        """Should return True when enrolled > capacity (overbooked edge case)."""
        dt = datetime(2025, 9, 3, 10, 0, 0)
        session = Session(
            id=12,
            title="Overbooked",
            instructor="Liam",
            style="Power",
            starts_at=dt,
            duration_minutes=30,
            capacity=10,
            enrolled=12,
        )
        assert session.is_full() is True

    def test_is_full_false_when_enrolled_zero(self):
        """Should return False when no one is enrolled."""
        dt = datetime(2025, 9, 4, 10, 0, 0)
        session = Session(
            id=13,
            title="Empty",
            instructor="Mia",
            style="Yin",
            starts_at=dt,
            duration_minutes=60,
            capacity=5,
            enrolled=0,
        )
        assert session.is_full() is False


class TestSpotsAvailable:
    """Tests for the spots_available method."""

    def test_spots_available_returns_remaining(self):
        """Should return capacity - enrolled when there are spots left."""
        dt = datetime(2025, 10, 1, 9, 0, 0)
        session = Session(
            id=14,
            title="Almost Full",
            instructor="Noah",
            style="Vinyasa",
            starts_at=dt,
            duration_minutes=60,
            capacity=20,
            enrolled=13,
        )
        assert session.spots_available() == 7

    def test_spots_available_zero_when_full(self):
        """Should return 0 when session is exactly full."""
        dt = datetime(2025, 10, 2, 9, 0, 0)
        session = Session(
            id=15,
            title="Completely Full",
            instructor="Olivia",
            style="Hatha",
            starts_at=dt,
            duration_minutes=45,
            capacity=10,
            enrolled=10,
        )
        assert session.spots_available() == 0

    def test_spots_available_zero_when_overbooked(self):
        """Should return 0 when enrolled exceeds capacity."""
        dt = datetime(2025, 10, 3, 9, 0, 0)
        session = Session(
            id=16,
            title="Overbooked",
            instructor="Paul",
            style="Power",
            starts_at=dt,
            duration_minutes=30,
            capacity=8,
            enrolled=10,
        )
        assert session.spots_available() == 0

    def test_spots_available_all_when_none_enrolled(self):
        """Should return full capacity when enrolled is 0."""
        dt = datetime(2025, 10, 4, 9, 0, 0)
        session = Session(
            id=17,
            title="Empty Room",
            instructor="Quinn",
            style="Yin",
            starts_at=dt,
            duration_minutes=30,
            capacity=25,
            enrolled=0,
        )
        assert session.spots_available() == 25


class TestToDict:
    """Tests for the to_dict serialization method."""

    def test_to_dict_contains_all_fields(self):
        """Should return a dict with all expected keys and values."""
        dt = datetime(2025, 11, 1, 8, 30, 0)
        session = Session(
            id=18,
            title="Sunrise Flow",
            instructor="Rachel",
            style="Vinyasa",
            starts_at=dt,
            duration_minutes=60,
            capacity=30,
            enrolled=12,
        )
        d = session.to_dict()
        assert d["id"] == 18
        assert d["title"] == "Sunrise Flow"
        assert d["instructor"] == "Rachel"
        assert d["style"] == "Vinyasa"
        assert d["starts_at"] == "2025-11-01T08:30:00"
        assert d["duration_minutes"] == 60
        assert d["capacity"] == 30
        assert d["enrolled"] == 12

    def test_to_dict_default_enrolled(self):
        """Should serialize with default enrolled=0."""
        dt = datetime(2025, 11, 2, 9, 0, 0)
        session = Session(
            id=19,
            title="New Class",
            instructor="Sam",
            style="Hatha",
            starts_at=dt,
            duration_minutes=45,
            capacity=15,
        )
        d = session.to_dict()
        assert d["enrolled"] == 0


class TestFromDict:
    """Tests for the from_dict deserialization method."""

    def test_from_dict_basic(self):
        """Should reconstruct a Session from a dict."""
        data = {
            "id": 20,
            "title": "Evening Relax",
            "instructor": "Tina",
            "style": "Yin",
            "starts_at": "2025-12-01T18:00:00",
            "duration_minutes": 75,
            "capacity": 20,
            "enrolled": 8,
        }
        session = Session.from_dict(data)
        assert session.id == 20
        assert session.title == "Evening Relax"
        assert session.instructor == "Tina"
        assert session.style == "Yin"
        assert session.starts_at == datetime(2025, 12, 1, 18, 0, 0)
        assert session.duration_minutes == 75
        assert session.capacity == 20
        assert session.enrolled == 8

    def test_from_dict_with_default_enrolled(self):
        """Should use default enrolled=0 when not provided."""
        data = {
            "id": 21,
            "title": "Morning Burn",
            "instructor": "Uma",
            "style": "Power",
            "starts_at": "2025-12-02T07:00:00",
            "duration_minutes": 50,
            "capacity": 25,
        }
        session = Session.from_dict(data)
        assert session.enrolled == 0

    def test_from_dict_accepts_datetime_object(self):
        """Should handle starts_at as a datetime object directly."""
        dt = datetime(2025, 12, 3, 12, 0, 0)
        data = {
            "id": 22,
            "title": "Lunch Yoga",
            "instructor": "Victor",
            "style": "Hatha",
            "starts_at": dt,
            "duration_minutes": 40,
            "capacity": 12,
        }
        session = Session.from_dict(data)
        assert session.starts_at == dt

    def test_to_dict_from_dict_roundtrip(self):
        """Should survive a to_dict -> from_dict round-trip unchanged."""
        dt = datetime(2025, 12, 10, 9, 0, 0)
        original = Session(
            id=23,
            title="Roundtrip",
            instructor="Wendy",
            style="Vinyasa",
            starts_at=dt,
            duration_minutes=60,
            capacity=18,
            enrolled=3,
        )
        serialized = original.to_dict()
        restored = Session.from_dict(serialized)
        assert restored.id == original.id
        assert restored.title == original.title
        assert restored.instructor == original.instructor
        assert restored.style == original.style
        assert restored.starts_at == original.starts_at
        assert restored.duration_minutes == original.duration_minutes
        assert restored.capacity == original.capacity
        assert restored.enrolled == original.enrolled

    def test_from_dict_validates_capacity(self):
        """Should raise ValueError when from_dict data has invalid capacity."""
        data = {
            "id": 24,
            "title": "Bad Capacity",
            "instructor": "Xander",
            "style": "Flow",
            "starts_at": "2025-12-15T10:00:00",
            "duration_minutes": 30,
            "capacity": 0,
        }
        with pytest.raises(ValueError, match="capacity must be >= 1"):
            Session.from_dict(data)

    def test_from_dict_validates_duration(self):
        """Should raise ValueError when from_dict data has invalid duration."""
        data = {
            "id": 25,
            "title": "Bad Duration",
            "instructor": "Yara",
            "style": "Flow",
            "starts_at": "2025-12-16T10:00:00",
            "duration_minutes": 5,
            "capacity": 10,
        }
        with pytest.raises(ValueError, match="duration_minutes must be >= 15"):
            Session.from_dict(data)
