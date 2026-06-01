"""Tests for the Stats API endpoints."""

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.repositories.bookings import BookingRepository
from src.repositories.sessions import SessionRepository
from src.repositories.users import UserRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _register_admin(client: TestClient) -> str:
    """Register an admin user and return the access token."""
    payload = {
        "name": "Admin User",
        "email": "admin@example.com",
        "password": "admin123",
        "role": "admin",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _register_client(
    client: TestClient,
    email="client@example.com",
    credits: int = 10,
) -> str:
    """Register a client user, give credits, and return the access token."""
    payload = {
        "name": f"Client {email}",
        "email": email,
        "password": "client123",
        "role": "client",
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    # Give credits so bookings can be created
    user_repo = UserRepository()
    user = user_repo.find_by_email(email)
    if user:
        user.credits = credits
        user_repo.save_one(user)

    return token


def _create_session(
    client: TestClient,
    admin_token: str,
    title="Morning Yoga",
    instructor="Alice",
    style="Vinyasa",
    starts_at="2025-06-15T09:00:00",
    capacity=20,
) -> dict:
    """Create a session via the API and return its data."""
    payload = {
        "title": title,
        "instructor": instructor,
        "style": style,
        "starts_at": starts_at,
        "duration_minutes": 60,
        "capacity": capacity,
    }
    resp = client.post(
        "/api/v1/sessions",
        json=payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 201
    return resp.json()


def _create_booking(
    client: TestClient,
    client_token: str,
    session_id: int,
) -> dict:
    """Create a booking for a session and return its data."""
    resp = client.post(
        "/api/v1/bookings",
        json={"session_id": session_id},
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert resp.status_code == 201, (
        f"Booking failed with {resp.status_code}: {resp.text}"
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Create a TestClient with repositories redirected to a temp directory."""
    import src.repositories.users as users_mod
    import src.repositories.sessions as sessions_mod
    import src.repositories.bookings as bookings_mod

    original_users_init = users_mod.UserRepository.__init__
    original_sessions_init = sessions_mod.SessionRepository.__init__
    original_bookings_init = bookings_mod.BookingRepository.__init__

    def patched_users_init(self, data_dir="data"):
        original_users_init(self, str(tmp_path))

    def patched_sessions_init(self, data_dir="data"):
        original_sessions_init(self, str(tmp_path))

    def patched_bookings_init(self, data_dir="data"):
        original_bookings_init(self, str(tmp_path))

    monkeypatch.setattr(users_mod.UserRepository, "__init__", patched_users_init)
    monkeypatch.setattr(
        sessions_mod.SessionRepository, "__init__", patched_sessions_init
    )
    monkeypatch.setattr(
        bookings_mod.BookingRepository, "__init__", patched_bookings_init
    )

    return TestClient(app)


@pytest.fixture
def admin_token(client):
    """Return a valid admin token."""
    return _register_admin(client)


@pytest.fixture
def client_token(client):
    """Return a valid client token (with credits)."""
    return _register_client(client)


# ---------------------------------------------------------------------------
# GET /stats/instructors
# ---------------------------------------------------------------------------


class TestInstructorStats:
    """Tests for the public /stats/instructors endpoint."""

    def test_empty_instructors_returns_empty_list(self, client):
        """When no sessions exist, should return an empty list."""
        resp = client.get("/stats/instructors")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_single_instructor_single_session(self, client, admin_token):
        """One instructor with one session should aggregate correctly."""
        _create_session(
            client, admin_token,
            title="Yoga Flow",
            instructor="Alice",
            style="Vinyasa",
        )
        resp = client.get("/stats/instructors")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Alice"
        assert data[0]["sessions_count"] == 1
        assert data[0]["total_enrolled"] == 0
        assert "id" in data[0]

    def test_multiple_instructors_aggregation(self, client, admin_token):
        """Multiple sessions with different instructors should produce
        one entry per instructor with proper counts."""
        _create_session(
            client, admin_token,
            title="Yoga Flow",
            instructor="Alice",
            style="Vinyasa",
        )
        _create_session(
            client, admin_token,
            title="Power Yoga",
            instructor="Alice",
            style="Hatha",
        )
        _create_session(
            client, admin_token,
            title="Morning Stretch",
            instructor="Bob",
            style="Pilates",
        )

        resp = client.get("/stats/instructors")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        alice = next(d for d in data if d["name"] == "Alice")
        bob = next(d for d in data if d["name"] == "Bob")

        assert alice["sessions_count"] == 2
        assert alice["total_enrolled"] == 0
        assert bob["sessions_count"] == 1
        assert bob["total_enrolled"] == 0

    def test_instructors_sorted_by_name(self, client, admin_token):
        """Results should be sorted by instructor name alphabetically."""
        _create_session(
            client, admin_token,
            instructor="Charlie",
        )
        _create_session(
            client, admin_token,
            instructor="Bob",
        )
        _create_session(
            client, admin_token,
            instructor="Alice",
        )

        resp = client.get("/stats/instructors")
        data = resp.json()
        names = [d["name"] for d in data]
        assert names == sorted(names)

    def test_instructors_total_enrolled_sum(self, client, admin_token):
        """When sessions have enrolled participants, total_enrolled
        should reflect the sum."""
        s1 = _create_session(
            client, admin_token,
            instructor="Alice",
            capacity=10,
        )
        s2 = _create_session(
            client, admin_token,
            instructor="Alice",
            capacity=10,
        )

        import src.repositories.sessions as smod
        srepo = smod.SessionRepository()
        for sid, enrol in [(s1["id"], 3), (s2["id"], 5)]:
            session_obj = srepo.find_by_id(sid)
            session_obj.enrolled = enrol
            srepo.save_one(session_obj)

        resp = client.get("/stats/instructors")
        data = resp.json()
        assert data[0]["total_enrolled"] == 8

    def test_instructors_no_auth_required(self, client):
        """The /stats/instructors endpoint should be public (no auth)."""
        resp = client.get("/stats/instructors")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_instructors_synthetic_ids_unique(self, client, admin_token):
        """Each instructor entry should have a unique synthetic id."""
        _create_session(client, admin_token, instructor="Alice")
        _create_session(client, admin_token, instructor="Bob")
        _create_session(client, admin_token, instructor="Charlie")

        resp = client.get("/stats/instructors")
        data = resp.json()
        ids = [d["id"] for d in data]
        assert len(ids) == len(set(ids))
        assert all(isinstance(i, int) for i in ids)


# ---------------------------------------------------------------------------
# GET /stats/styles
# ---------------------------------------------------------------------------


class TestStyleStats:
    """Tests for the public /stats/styles endpoint."""

    def test_empty_styles_returns_empty_list(self, client):
        """When no sessions exist, should return an empty list."""
        resp = client.get("/stats/styles")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_single_style_single_session(self, client, admin_token):
        """One style with one session should aggregate correctly."""
        _create_session(
            client, admin_token,
            title="Flow",
            instructor="Alice",
            style="Vinyasa",
        )
        resp = client.get("/stats/styles")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["style"] == "Vinyasa"
        assert data[0]["sessions_count"] == 1
        assert data[0]["total_enrolled"] == 0

    def test_multiple_styles_aggregation(self, client, admin_token):
        """Multiple sessions sharing the same style should be aggregated."""
        _create_session(
            client, admin_token,
            style="Vinyasa",
        )
        _create_session(
            client, admin_token,
            style="Vinyasa",
        )
        _create_session(
            client, admin_token,
            style="Hatha",
        )

        resp = client.get("/stats/styles")
        data = resp.json()
        assert len(data) == 2

        vinyasa = next(d for d in data if d["style"] == "Vinyasa")
        hatha = next(d for d in data if d["style"] == "Hatha")

        assert vinyasa["sessions_count"] == 2
        assert vinyasa["total_enrolled"] == 0
        assert hatha["sessions_count"] == 1
        assert hatha["total_enrolled"] == 0

    def test_styles_total_enrolled_sum(self, client, admin_token):
        """total_enrolled should sum enrolled across all sessions of a style."""
        s1 = _create_session(
            client, admin_token,
            style="Vinyasa",
            capacity=10,
        )
        s2 = _create_session(
            client, admin_token,
            style="Vinyasa",
            capacity=10,
        )

        import src.repositories.sessions as smod
        srepo = smod.SessionRepository()
        for sid, enrol in [(s1["id"], 4), (s2["id"], 6)]:
            session_obj = srepo.find_by_id(sid)
            session_obj.enrolled = enrol
            srepo.save_one(session_obj)

        resp = client.get("/stats/styles")
        data = resp.json()
        vinyasa = next(d for d in data if d["style"] == "Vinyasa")
        assert vinyasa["total_enrolled"] == 10

    def test_styles_no_auth_required(self, client):
        """The /stats/styles endpoint should be public (no auth)."""
        resp = client.get("/stats/styles")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_styles_field_names_correct(self, client, admin_token):
        """Each entry should have 'style', 'sessions_count', 'total_enrolled'."""
        _create_session(client, admin_token, style="Pilates")
        resp = client.get("/stats/styles")
        data = resp.json()
        assert len(data) == 1
        keys = set(data[0].keys())
        assert keys == {"style", "sessions_count", "total_enrolled"}


# ---------------------------------------------------------------------------
# GET /stats/users
# ---------------------------------------------------------------------------


class TestUserStats:
    """Tests for the admin-only /stats/users endpoint."""

    def test_no_auth_returns_401(self, client):
        """Unauthenticated requests should be rejected."""
        resp = client.get("/stats/users")
        assert resp.status_code == 401

    def test_client_role_returns_403(self, client, client_token):
        """A client user should not be allowed to access /stats/users."""
        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 403

    def test_admin_access_returns_200(self, client, admin_token):
        """An admin user should be able to access /stats/users."""
        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_empty_bookings_returns_empty_list(self, client, admin_token):
        """When no bookings exist, should return an empty list."""
        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_top_user_by_bookings(self, client, admin_token):
        """User with the most bookings should appear first."""
        client_a_token = _register_client(client, "a@example.com")
        client_b_token = _register_client(client, "b@example.com")

        # Create two different sessions for user A's two bookings
        s1 = _create_session(client, admin_token, title="S1", capacity=10,
                             starts_at="2025-07-01T09:00:00")
        s2 = _create_session(client, admin_token, title="S2", capacity=10,
                             starts_at="2025-07-02T09:00:00")
        # One session for user B
        s3 = _create_session(client, admin_token, title="S3", capacity=10,
                             starts_at="2025-07-03T09:00:00")

        _create_booking(client, client_a_token, s1["id"])
        _create_booking(client, client_a_token, s2["id"])
        _create_booking(client, client_b_token, s3["id"])

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        assert len(data) >= 2
        # First entry should be client A (2 bookings)
        assert data[0]["bookings"] == 2
        assert data[0]["email"] == "a@example.com"

    def test_cancelled_bookings_excluded(self, client, admin_token):
        """Cancelled bookings should not count toward user stats."""
        client_token = _register_client(client, "c@example.com")
        s1 = _create_session(client, admin_token, title="S1", capacity=10,
                             starts_at="2025-08-01T09:00:00")
        s2 = _create_session(client, admin_token, title="S2", capacity=10,
                             starts_at="2025-08-02T09:00:00")

        b1 = _create_booking(client, client_token, s1["id"])
        b2 = _create_booking(client, client_token, s2["id"])

        # Cancel the first booking
        resp = client.delete(
            f"/api/v1/bookings/{b1['id']}",
            headers={"Authorization": f"Bearer {client_token}"},
        )
        assert resp.status_code == 204

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        assert data[0]["bookings"] == 1

    def test_limit_10_users(self, client, admin_token):
        """Only the top 10 users should be returned."""
        tokens = []
        for i in range(15):
            email = f"user{i}@example.com"
            token = _register_client(client, email)
            tokens.append(token)
            # Create a unique session per user to avoid duplicate booking errors
            sess = _create_session(
                client, admin_token,
                title=f"S{i}",
                capacity=50,
                starts_at=f"2025-09-{i+1:02d}T09:00:00",
            )
            _create_booking(client, token, sess["id"])

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        assert len(data) == 10

    def test_result_sorted_by_bookings_desc(self, client, admin_token):
        """Results must be sorted by bookings descending."""
        # Client with 3 bookings
        tok_high = _register_client(client, "high@example.com")
        sessions_high = [
            _create_session(client, admin_token, title=f"H{i}", capacity=50,
                            starts_at=f"2025-10-{i+1:02d}T09:00:00")
            for i in range(3)
        ]
        for s in sessions_high:
            _create_booking(client, tok_high, s["id"])

        # Client with 1 booking
        tok_low = _register_client(client, "low@example.com")
        s_low = _create_session(client, admin_token, title="L", capacity=50,
                                starts_at="2025-11-01T09:00:00")
        _create_booking(client, tok_low, s_low["id"])

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        bookings = [d["bookings"] for d in data]
        assert bookings == sorted(bookings, reverse=True)

    def test_users_with_zero_bookings_excluded(self, client, admin_token):
        """Users with no bookings should not appear in stats."""
        _register_client(client, "nobook@example.com")
        tok = _register_client(client, "hasbook@example.com")
        session = _create_session(client, admin_token, capacity=10)
        _create_booking(client, tok, session["id"])

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        assert len(data) == 1
        assert data[0]["email"] == "hasbook@example.com"

    def test_response_structure(self, client, admin_token):
        """Each entry should have user_id, name, email, bookings."""
        tok = _register_client(client, "structure@example.com")
        session = _create_session(client, admin_token, capacity=10)
        _create_booking(client, tok, session["id"])

        resp = client.get(
            "/stats/users",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        data = resp.json()
        assert len(data) == 1
        keys = set(data[0].keys())
        assert keys == {"user_id", "name", "email", "bookings"}
