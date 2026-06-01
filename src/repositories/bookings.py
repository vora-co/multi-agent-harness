"""Repository for Booking entities."""

from typing import List, Optional

from src.models.booking import Booking
from src.storage import load, save


class BookingRepository:
    """Data access for Booking entities, backed by JSON storage."""

    def __init__(self, data_dir: str = "data") -> None:
        """Initialize repository with a configurable data directory.

        Args:
            data_dir: Directory where JSON files reside. Defaults to 'data'.
        """
        self._entity = "bookings"
        self._data_dir = data_dir

    def find_all(self) -> List[Booking]:
        """Return all bookings."""
        records = load(self._entity, data_dir=self._data_dir)
        return [Booking.from_dict(r) for r in records]

    def find_by_id(self, id: int) -> Optional[Booking]:
        """Return the booking with the given id, or None if not found."""
        records = load(self._entity, data_dir=self._data_dir)
        for r in records:
            if r.get("id") == id:
                return Booking.from_dict(r)
        return None

    def find_by_user(self, user_id: int) -> List[Booking]:
        """Return all bookings for a given user."""
        records = load(self._entity, data_dir=self._data_dir)
        return [Booking.from_dict(r) for r in records if r.get("user_id") == user_id]

    def find_by_session(self, session_id: int) -> List[Booking]:
        """Return all bookings for a given session."""
        records = load(self._entity, data_dir=self._data_dir)
        return [Booking.from_dict(r) for r in records if r.get("session_id") == session_id]

    def save_one(self, obj: Booking) -> None:
        """Insert or update a booking.

        If a record with the same id already exists, it is replaced.
        Otherwise the record is appended.
        """
        records = load(self._entity, data_dir=self._data_dir)
        replaced = False
        for i, r in enumerate(records):
            if r.get("id") == obj.id:
                records[i] = obj.to_dict()
                replaced = True
                break
        if not replaced:
            records.append(obj.to_dict())
        save(self._entity, records, data_dir=self._data_dir)

    def delete(self, id: int) -> bool:
        """Delete the booking with the given id.

        Returns:
            True if the record was deleted, False if it was not found.
        """
        records = load(self._entity, data_dir=self._data_dir)
        initial_len = len(records)
        records = [r for r in records if r.get("id") != id]
        if len(records) < initial_len:
            save(self._entity, records, data_dir=self._data_dir)
            return True
        return False
