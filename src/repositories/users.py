"""Repository for User entities."""

from typing import List, Optional

from src.models.user import User
from src.storage import load, save


class UserRepository:
    """Data access for User entities, backed by JSON storage."""

    def __init__(self, data_dir: str = "data") -> None:
        """Initialize repository with a configurable data directory.

        Args:
            data_dir: Directory where JSON files reside. Defaults to 'data'.
        """
        self._entity = "users"
        self._data_dir = data_dir

    def find_all(self) -> List[User]:
        """Return all users."""
        records = load(self._entity, data_dir=self._data_dir)
        return [User.from_dict(r) for r in records]

    def find_by_id(self, id: int) -> Optional[User]:
        """Return the user with the given id, or None if not found."""
        records = load(self._entity, data_dir=self._data_dir)
        for r in records:
            if r.get("id") == id:
                return User.from_dict(r)
        return None

    def save_one(self, obj: User) -> None:
        """Insert or update a user.

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
        """Delete the user with the given id.

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


    def find_by_email(self, email: str) -> Optional[User]:
        """Return the user with the given email, or None if not found."""
        records = load(self._entity, data_dir=self._data_dir)
        for r in records:
            if r.get("email") == email:
                return User.from_dict(r)
        return None
