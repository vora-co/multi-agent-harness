"""User domain model."""

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class User:
    """User entity with id, name, email, credits, role, password_hash and created_at."""

    _EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
    _VALID_ROLES = {"client", "admin"}

    def __init__(
        self,
        id: int,
        name: str,
        email: str,
        credits: int = 0,
        role: str = "client",
        password_hash: str = "",
        created_at: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.name = name
        self.email = email
        self.credits = credits
        self.role = role
        self.password_hash = password_hash
        if created_at is None:
            self.created_at = datetime.now(timezone.utc)
        elif created_at.tzinfo is None:
            self.created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            self.created_at = created_at

        self._validate()

    def _validate(self) -> None:
        """Validate email format and role value."""
        if not self._EMAIL_REGEX.match(self.email):
            raise ValueError(f"Invalid email: {self.email}")
        if self.role not in self._VALID_ROLES:
            raise ValueError(
                f"Invalid role: {self.role}. Must be one of {self._VALID_ROLES}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize user to a dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "credits": self.credits,
            "role": self.role,
            "password_hash": self.password_hash,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "User":
        """Deserialize a dictionary into a User instance."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            id=data["id"],
            name=data["name"],
            email=data["email"],
            credits=data.get("credits", 0),
            role=data.get("role", "client"),
            password_hash=data.get("password_hash", ""),
            created_at=created_at,
        )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"
