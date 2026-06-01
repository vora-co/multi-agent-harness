"""Session domain model."""

from datetime import datetime
from typing import Any, Dict, Optional


class Session:
    """Session entity with id, title, instructor, style, starts_at,
    duration_minutes, capacity, and enrolled."""

    def __init__(
        self,
        id: int,
        title: str,
        instructor: str,
        style: str,
        starts_at: datetime,
        duration_minutes: int,
        capacity: int,
        enrolled: int = 0,
    ) -> None:
        self.id = id
        self.title = title
        self.instructor = instructor
        self.style = style
        self.starts_at = starts_at
        self.duration_minutes = duration_minutes
        self.capacity = capacity
        self.enrolled = enrolled

        self._validate()

    def _validate(self) -> None:
        """Validate capacity and duration_minutes constraints."""
        if self.capacity < 1:
            raise ValueError(
                f"capacity must be >= 1, got {self.capacity}"
            )
        if self.duration_minutes < 15:
            raise ValueError(
                f"duration_minutes must be >= 15, got {self.duration_minutes}"
            )

    def is_full(self) -> bool:
        """Return True if enrolled equals or exceeds capacity."""
        return self.enrolled >= self.capacity

    def spots_available(self) -> int:
        """Return the number of remaining spots (capacity - enrolled)."""
        return max(self.capacity - self.enrolled, 0)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to a dictionary."""
        return {
            "id": self.id,
            "title": self.title,
            "instructor": self.instructor,
            "style": self.style,
            "starts_at": self.starts_at.isoformat(),
            "duration_minutes": self.duration_minutes,
            "capacity": self.capacity,
            "enrolled": self.enrolled,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Deserialize a dictionary into a Session instance."""
        starts_at = data.get("starts_at")
        if isinstance(starts_at, str):
            starts_at = datetime.fromisoformat(starts_at)
        return cls(
            id=data["id"],
            title=data["title"],
            instructor=data["instructor"],
            style=data["style"],
            starts_at=starts_at,
            duration_minutes=data["duration_minutes"],
            capacity=data["capacity"],
            enrolled=data.get("enrolled", 0),
        )

    def __repr__(self) -> str:
        return (
            f"<Session id={self.id} title={self.title!r} "
            f"instructor={self.instructor!r}>"
        )
