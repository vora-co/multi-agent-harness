"""CreditTransaction domain model."""

from datetime import datetime, timezone
from typing import Any, Dict, Optional


class CreditTransaction:
    """Records a credit addition to a user's account.

    Attributes:
        id: Unique identifier.
        user_id: The user receiving credits.
        amount: Number of credits added (1-100).
        reason: Why credits were added.
        created_at: Timestamp of the transaction.
    """

    def __init__(
        self,
        id: int,
        user_id: int,
        amount: int,
        reason: str,
        created_at: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.amount = amount
        self.reason = reason
        if created_at is None:
            self.created_at = datetime.now(timezone.utc)
        elif created_at.tzinfo is None:
            self.created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            self.created_at = created_at

        self._validate()

    def _validate(self) -> None:
        """Validate that amount is between 1 and 100 and reason is non-empty."""
        if not isinstance(self.amount, int) or self.amount < 1 or self.amount > 100:
            raise ValueError(f"amount must be between 1 and 100, got {self.amount}")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """Serialize credit transaction to a dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "amount": self.amount,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CreditTransaction":
        """Deserialize a dictionary into a CreditTransaction instance."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            amount=data["amount"],
            reason=data["reason"],
            created_at=created_at,
        )

    def __repr__(self) -> str:
        return (
            f"<CreditTransaction id={self.id} user_id={self.user_id} "
            f"amount={self.amount}>"
        )
