"""Booking domain model."""

from datetime import datetime
from typing import Any, Dict, Optional


class Booking:
    """Booking entity linking a user to a session with a status."""

    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    WAITLIST = "waitlist"

    _VALID_STATUSES = {CONFIRMED, CANCELLED, WAITLIST}

    def __init__(
        self,
        id: int,
        user_id: int,
        session_id: int,
        status: str = WAITLIST,
        created_at: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.session_id = session_id
        self.status = status
        self.created_at = created_at or datetime.now()

        self._validate()

    def _validate(self) -> None:
        """Validate that status is one of the allowed values."""
        if self.status not in self._VALID_STATUSES:
            raise ValueError(
                f"Invalid status: {self.status!r}. "
                f"Must be one of {self._VALID_STATUSES}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize booking to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Booking":
        """Deserialize a dictionary into a Booking instance."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            session_id=data["session_id"],
            status=data.get("status", cls.WAITLIST),
            created_at=created_at,
        )

    def __repr__(self) -> str:
        return (
            f"<Booking id={self.id} user_id={self.user_id} "
            f"session_id={self.session_id} status={self.status!r}>"
        )
