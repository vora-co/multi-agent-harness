"""Repository for CreditTransaction persistence."""

from typing import List, Optional

from src.models.credit_transaction import CreditTransaction
from src.storage import load, save


class CreditTransactionRepository:
    """Manages persistence of CreditTransaction records."""

    def __init__(self, data_dir: str = "data") -> None:
        """Initialize repository with a configurable data directory.

        Args:
            data_dir: Directory where JSON files reside. Defaults to 'data'.
        """
        self._entity = "credit_transactions"
        self._data_dir = data_dir

    def find_all(self) -> List[CreditTransaction]:
        """Return all credit transactions."""
        data = load(self._entity, self._data_dir)
        return [CreditTransaction.from_dict(item) for item in data]

    def find_by_id(self, id: int) -> Optional[CreditTransaction]:
        """Return a single credit transaction by id, or None if not found."""
        data = load(self._entity, self._data_dir)
        for item in data:
            if item["id"] == id:
                return CreditTransaction.from_dict(item)
        return None

    def find_by_user_id(self, user_id: int) -> List[CreditTransaction]:
        """Return all credit transactions for a given user."""
        data = load(self._entity, self._data_dir)
        return [
            CreditTransaction.from_dict(item)
            for item in data
            if item["user_id"] == user_id
        ]

    def save_one(self, transaction: CreditTransaction) -> CreditTransaction:
        """Insert or update a credit transaction record."""
        records = load(self._entity, self._data_dir)
        updated = False
        for i, item in enumerate(records):
            if item["id"] == transaction.id:
                records[i] = transaction.to_dict()
                updated = True
                break
        if not updated:
            records.append(transaction.to_dict())
        save(self._entity, records, self._data_dir)
        return transaction

    def next_id(self) -> int:
        """Return the next available id."""
        data = load(self._entity, self._data_dir)
        if not data:
            return 1
        return max(item["id"] for item in data) + 1
