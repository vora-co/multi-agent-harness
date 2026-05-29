"""Modelo de dominio Note para la aplicación de notas."""

from datetime import datetime, timezone
from typing import Union


class Note:
    """Representa una nota con id, título, cuerpo y fecha de creación."""

    def __init__(
        self,
        id: Union[int, str],
        title: str,
        body: str = "",
        created_at: Union[datetime, str, None] = None,
    ):
        self.id = id
        self.title = title
        self.body = body

        if created_at is None:
            self.created_at = datetime.now(timezone.utc)
        elif isinstance(created_at, str):
            self.created_at = datetime.fromisoformat(created_at)
        else:
            self.created_at = created_at

    def to_dict(self) -> dict:
        """Devuelve un dict serializable a JSON."""
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        """Construye un Note desde un dict (como el que devuelve storage.load())."""
        return cls(
            id=data["id"],
            title=data["title"],
            body=data.get("body", ""),
            created_at=data.get("created_at", None),
        )

    def __repr__(self) -> str:
        return (
            f"Note(id={self.id!r}, title={self.title!r}, "
            f"body={self.body!r}, created_at={self.created_at!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Note):
            return NotImplemented
        return (
            self.id == other.id
            and self.title == other.title
            and self.body == other.body
            and self.created_at == other.created_at
        )
