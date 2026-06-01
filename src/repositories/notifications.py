"""Repository for Notification entities."""

from typing import List, Optional

from src.models.notification import Notification
from src.storage import load, save


class NotificationRepository:
    """Data access for Notification entities, backed by JSON storage."""

    def __init__(self, data_dir: str = "data") -> None:
        self._entity = "notifications"
        self._data_dir = data_dir

    def find_all(self) -> List[Notification]:
        """Return all notifications."""
        records = load(self._entity, data_dir=self._data_dir)
        return [Notification.from_dict(r) for r in records]

    def find_by_id(self, id: int) -> Optional[Notification]:
        """Return the notification with the given id, or None if not found."""
        records = load(self._entity, data_dir=self._data_dir)
        for r in records:
            if r.get("id") == id:
                return Notification.from_dict(r)
        return None

    def find_by_user(self, user_id: int) -> List[Notification]:
        """Return all notifications for a given user."""
        records = load(self._entity, data_dir=self._data_dir)
        return [
            Notification.from_dict(r)
            for r in records
            if r.get("user_id") == user_id
        ]

    def save_one(self, obj: Notification) -> None:
        """Insert or update a notification."""
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
