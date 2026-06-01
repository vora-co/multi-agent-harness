"""Notification domain model."""

from datetime import datetime
from typing import Any, Dict, Optional


class Notification:
    """A notification stored for a specific user."""

    def __init__(
        self,
        id: int,
        user_id: int,
        message: str,
        created_at: Optional[datetime] = None,
        read_at: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.message = message
        self.created_at = created_at if created_at is not None else datetime.utcnow()
        self.read_at = read_at  # None means unread

    def to_dict(self) -> Dict[str, Any]:
        """Serialise the notification to a plain dictionary."""
        result: Dict[str, Any] = {
            "id": self.id,
            "user_id": self.user_id,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
        }
        if self.read_at is not None:
            result["read_at"] = self.read_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Notification":
        """Reconstruct a notification from a dictionary."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.utcnow()

        read_at = data.get("read_at")
        if isinstance(read_at, str):
            read_at = datetime.fromisoformat(read_at)

        return cls(
            id=data["id"],
            user_id=data["user_id"],
            message=data["message"],
            created_at=created_at,
            read_at=read_at,
        )
