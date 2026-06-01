"""Tests for the User domain model."""

from datetime import datetime, timezone

import pytest

from src.models.user import User


class TestUserCreation:
    """Tests for User instantiation and validation."""

    def test_create_user_with_role_client(self):
        """Should create a user with role 'client'."""
        user = User(id=1, name="Alice", email="alice@example.com", role="client")
        assert user.role == "client"
        assert user.name == "Alice"
        assert user.email == "alice@example.com"
        assert isinstance(user.created_at, datetime)
        assert user.created_at.tzinfo == timezone.utc

    def test_create_user_with_role_admin(self):
        """Should create a user with role 'admin'."""
        user = User(id=2, name="Bob", email="bob@example.com", role="admin")
        assert user.role == "admin"
        assert user.name == "Bob"
        assert user.email == "bob@example.com"

    def test_create_user_default_role_is_client(self):
        """Should default role to 'client' when not provided."""
        user = User(id=3, name="Charlie", email="charlie@example.com")
        assert user.role == "client"

    def test_create_user_default_credits_is_zero(self):
        """Should default credits to 0."""
        user = User(id=4, name="Diana", email="diana@example.com")
        assert user.credits == 0

    def test_create_user_with_explicit_credits(self):
        """Should accept an explicit credits value."""
        user = User(
            id=5, name="Eve", email="eve@example.com", credits=10, role="client"
        )
        assert user.credits == 10

    def test_create_user_with_explicit_created_at(self):
        """Should accept an explicit created_at datetime (naive → made UTC)."""
        dt = datetime(2025, 1, 1, 12, 0, 0)
        user = User(
            id=6,
            name="Frank",
            email="frank@example.com",
            created_at=dt,
        )
        assert user.created_at == dt.replace(tzinfo=timezone.utc)


class TestEmailValidation:
    """Tests for email validation logic."""

    def test_valid_email_with_dot_com(self):
        """Should accept email with standard .com domain."""
        user = User(id=7, name="Grace", email="grace@example.com")
        assert user.email == "grace@example.com"

    def test_valid_email_with_subdomain(self):
        """Should accept email with subdomain."""
        user = User(id=8, name="Hank", email="hank@mail.example.co")
        assert user.email == "hank@mail.example.co"

    def test_email_missing_at_symbol_raises_valueerror(self):
        """Should raise ValueError for email without @."""
        with pytest.raises(ValueError, match="Invalid email"):
            User(id=9, name="Ivy", email="ivyexample.com")

    def test_email_missing_domain_raises_valueerror(self):
        """Should raise ValueError for email without domain (no dot)."""
        with pytest.raises(ValueError, match="Invalid email"):
            User(id=10, name="Jack", email="jack@localhost")

    def test_email_empty_string_raises_valueerror(self):
        """Should raise ValueError for empty email string."""
        with pytest.raises(ValueError, match="Invalid email"):
            User(id=11, name="Kate", email="")

    def test_email_with_spaces_raises_valueerror(self):
        """Should raise ValueError for email with spaces."""
        with pytest.raises(ValueError, match="Invalid email"):
            User(id=12, name="Liam", email="liam @example.com")

    def test_email_only_at_symbol_raises_valueerror(self):
        """Should raise ValueError for '@' only."""
        with pytest.raises(ValueError, match="Invalid email"):
            User(id=13, name="Mia", email="@")


class TestRoleValidation:
    """Tests for role validation logic."""

    def test_invalid_role_raises_valueerror(self):
        """Should raise ValueError for a role not in {client, admin}."""
        with pytest.raises(ValueError, match="Invalid role"):
            User(id=14, name="Noah", email="noah@example.com", role="superadmin")

    def test_empty_role_raises_valueerror(self):
        """Should raise ValueError for an empty role string."""
        with pytest.raises(ValueError, match="Invalid role"):
            User(id=15, name="Olivia", email="olivia@example.com", role="")


class TestToDict:
    """Tests for the to_dict serialization method."""

    def test_to_dict_contains_all_fields(self):
        """Should return a dict with all expected keys."""
        dt = datetime(2025, 6, 15, 9, 30, 0, tzinfo=timezone.utc)
        user = User(
            id=16,
            name="Paul",
            email="paul@example.com",
            credits=5,
            role="admin",
            created_at=dt,
        )
        d = user.to_dict()
        assert d["id"] == 16
        assert d["name"] == "Paul"
        assert d["email"] == "paul@example.com"
        assert d["credits"] == 5
        assert d["role"] == "admin"
        assert d["created_at"] == "2025-06-15T09:30:00+00:00"

    def test_to_dict_default_values(self):
        """Should serialize with default credits and role."""
        user = User(id=17, name="Quinn", email="quinn@example.com")
        d = user.to_dict()
        assert d["credits"] == 0
        assert d["role"] == "client"


class TestFromDict:
    """Tests for the from_dict deserialization method."""

    def test_from_dict_basic(self):
        """Should reconstruct a User from a dict."""
        data = {
            "id": 18,
            "name": "Rachel",
            "email": "rachel@example.com",
            "credits": 20,
            "role": "admin",
            "created_at": "2025-07-01T14:00:00+00:00",
        }
        user = User.from_dict(data)
        assert user.id == 18
        assert user.name == "Rachel"
        assert user.email == "rachel@example.com"
        assert user.credits == 20
        assert user.role == "admin"
        assert user.created_at == datetime(2025, 7, 1, 14, 0, 0, tzinfo=timezone.utc)

    def test_from_dict_with_defaults(self):
        """Should use defaults when credits and role are missing."""
        data = {
            "id": 19,
            "name": "Sam",
            "email": "sam@example.com",
            "created_at": "2025-08-10T08:00:00+00:00",
        }
        user = User.from_dict(data)
        assert user.credits == 0
        assert user.role == "client"

    def test_from_dict_accepts_datetime_object(self):
        """Should handle created_at as a datetime object directly."""
        dt = datetime(2025, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        data = {
            "id": 20,
            "name": "Tina",
            "email": "tina@example.com",
            "created_at": dt,
        }
        user = User.from_dict(data)
        assert user.created_at == dt

    def test_to_dict_from_dict_roundtrip(self):
        """Should survive a to_dict -> from_dict round-trip unchanged."""
        original = User(
            id=21,
            name="Uma",
            email="uma@example.com",
            credits=30,
            role="client",
        )
        serialized = original.to_dict()
        restored = User.from_dict(serialized)
        assert restored.id == original.id
        assert restored.name == original.name
        assert restored.email == original.email
        assert restored.credits == original.credits
        assert restored.role == original.role
        assert restored.created_at == original.created_at

    def test_from_dict_validates_email(self):
        """Should raise ValueError when from_dict data has invalid email."""
        data = {
            "id": 22,
            "name": "Victor",
            "email": "invalid-email",
        }
        with pytest.raises(ValueError, match="Invalid email"):
            User.from_dict(data)

    def test_from_dict_validates_role(self):
        """Should raise ValueError when from_dict data has invalid role."""
        data = {
            "id": 23,
            "name": "Wendy",
            "email": "wendy@example.com",
            "role": "superadmin",
        }
        with pytest.raises(ValueError, match="Invalid role"):
            User.from_dict(data)
