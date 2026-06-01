"""FastAPI application with authentication, session, and booking endpoints."""

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, field_validator

from src.auth import (
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from src.models.booking import Booking
from src.models.credit_transaction import CreditTransaction
from src.models.user import User
from src.core import notify_user, promote_from_waitlist, select_top_users
from src.repositories.bookings import BookingRepository
from src.repositories.credit_transactions import CreditTransactionRepository
from src.repositories.notifications import NotificationRepository
from src.repositories.sessions import SessionRepository
from src.repositories.users import UserRepository
from src.sessions import router as sessions_router


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "client"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("client", "admin"):
            raise ValueError("role must be 'client' or 'admin'")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    credits: int
    role: str
    created_at: str


class BookingCreate(BaseModel):
    """Schema for creating a new booking (POST /bookings)."""
    session_id: int


class SessionDetail(BaseModel):
    """Nested session info inside a booking response."""
    id: int
    title: str
    instructor: str
    style: str
    starts_at: str
    duration_minutes: int
    capacity: int
    enrolled: int


class BookingResponse(BaseModel):
    id: int
    user_id: int
    session_id: int
    status: str
    created_at: str
    session: Optional[SessionDetail] = None


class NotificationResponse(BaseModel):
    """Schema for notification responses."""
    id: int
    user_id: int
    message: str
    created_at: str
    read_at: Optional[str] = None


class EnrollFromWaitlistResponse(BaseModel):
    """Schema for the enroll_from_waitlist response."""
    message: str
    booking_id: int
    user_id: int


# ---------------------------------------------------------------------------
# Feature #9 schemas (credits & admin panel)
# ---------------------------------------------------------------------------

class AddCreditsWithReasonRequest(BaseModel):
    """Schema for POST /api/v1/users/{id}/credits (admin-only credit addition)."""
    amount: int
    reason: str

    @field_validator("amount")
    @classmethod
    def validate_amount_range(cls, v: int) -> int:
        if v < 1 or v > 100:
            raise ValueError("amount must be between 1 and 100")
        return v


class CreditTransactionResponse(BaseModel):
    """Schema for credit transaction responses."""
    id: int
    user_id: int
    amount: int
    reason: str
    created_at: str


# ---------------------------------------------------------------------------
# Feature #12 schemas
# ---------------------------------------------------------------------------

class UserAdminResponse(BaseModel):
    """Schema for admin user listing (without password_hash)."""
    id: int
    name: str
    email: str
    credits: int
    role: str
    created_at: str


class AddCreditsRequest(BaseModel):
    """Schema for adding credits to a user."""
    credits: int

    @field_validator("credits")
    @classmethod
    def validate_credits_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("credits must be positive")
        return v


class AttendeeResponse(BaseModel):
    """Schema for an attendee of a session."""
    booking_id: int
    user_id: int
    user_name: str
    user_email: str
    status: str
    created_at: str


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Agentes Demo API", version="0.1.0")

app.include_router(sessions_router)


@app.get("/api/v1/health")
def health_check() -> Dict[str, str]:
    """Health check endpoint for E2E tests."""
    return {"status": "ok"}


def _get_user_repo() -> UserRepository:
    return UserRepository()


def _get_session_repo() -> SessionRepository:
    return SessionRepository()


def _get_booking_repo() -> BookingRepository:
    return BookingRepository()


def _get_notification_repo() -> NotificationRepository:
    return NotificationRepository()


def _get_credit_transaction_repo() -> CreditTransactionRepository:
    return CreditTransactionRepository()


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register", response_model=TokenResponse)
def register(payload: RegisterRequest, user_repo: UserRepository = Depends(_get_user_repo)) -> Dict[str, Any]:
    """Register a new user and return a JWT token."""
    # Check if email already in use
    if user_repo.find_by_email(payload.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Determine next id
    all_users = user_repo.find_all()
    next_id = max((u.id for u in all_users), default=0) + 1

    # Create user
    user = User(
        id=next_id,
        name=payload.name,
        email=payload.email,
        role=payload.role,
        password_hash=hash_password(payload.password),
    )
    user_repo.save_one(user)

    # Issue token
    token = create_access_token({"user_id": user.id, "role": user.role})
    return {"access_token": token, "token_type": "bearer"}


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, user_repo: UserRepository = Depends(_get_user_repo)) -> Dict[str, Any]:
    """Authenticate an existing user and return a JWT token."""
    user = user_repo.find_by_email(payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = create_access_token({"user_id": user.id, "role": user.role})
    return {"access_token": token, "token_type": "bearer"}


@app.get("/api/v1/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Return the currently authenticated user's profile."""
    return current_user.to_dict()


# ---------------------------------------------------------------------------
# Session cancel (admin) — Feature #9
# ---------------------------------------------------------------------------

@app.put("/api/v1/sessions/{session_id}/cancel", status_code=204)
def cancel_session(
    session_id: int,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> None:
    """Cancel a session (admin only).

    * All 'confirmed' bookings become 'cancelled', credits are returned,
      and a notification is sent to the user.
    * All 'waitlist' bookings become 'cancelled' with no credit return.
    """
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    bookings = booking_repo.find_by_session(session_id)
    for booking in bookings:
        if booking.status == Booking.CANCELLED:
            continue

        if booking.status == Booking.CONFIRMED:
            # Return the credit
            user = user_repo.find_by_id(booking.user_id)
            if user is not None:
                user.credits += 1
                user_repo.save_one(user)
            # Send notification
            notify_user(
                booking.user_id,
                f"Session {session_id} ({session.title}) has been cancelled. Your credit has been returned.",
            )

        elif booking.status == Booking.WAITLIST:
            # No credit refund for waitlist
            notify_user(
                booking.user_id,
                f"Session {session_id} ({session.title}) has been cancelled. You were on the waitlist.",
            )

        booking.status = Booking.CANCELLED
        booking_repo.save_one(booking)

    # Zero out enrolled
    session.enrolled = 0
    session_repo.save_one(session)

    return None


# ---------------------------------------------------------------------------
# Session enroll from waitlist (admin) — Feature #10
# ---------------------------------------------------------------------------

@app.put(
    "/api/v1/sessions/{session_id}/enroll_from_waitlist",
    response_model=EnrollFromWaitlistResponse,
)
def enroll_from_waitlist(
    session_id: int,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> Dict[str, Any]:
    """Admin manually promotes the first waitlisted user to confirmed.

    If no waitlisted bookings exist for the session, returns 400.
    If waitlisted users exist but none have credits, also returns 400.
    """
    # Verify session exists
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Check if there are any waitlisted bookings
    all_bookings = booking_repo.find_by_session(session_id)
    waitlist_bookings = [b for b in all_bookings if b.status == Booking.WAITLIST]

    if not waitlist_bookings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No waitlisted users to promote",
        )

    # Delegate to core promotion logic
    promoted = promote_from_waitlist(
        session_id,
        booking_repo,
        session_repo,
        user_repo,
    )

    if not promoted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No eligible waitlisted users (all lack credits)",
        )

    # After promotion, find the newly promoted booking
    refreshed = booking_repo.find_by_session(session_id)
    promoted_booking = next(
        (b for b in refreshed if b.status == Booking.WAITLIST),
        None,
    )
    # Actually, we need the one that was promoted. The original waitlist
    # is sorted by created_at asc. Let's find the first originally-waitlisted
    # that is now confirmed.
    # Simpler: the first waitlisted booking (by created_at) should now be confirmed.
    waitlist_bookings_sorted = sorted(
        waitlist_bookings, key=lambda b: b.created_at
    )
    first_waitlisted = waitlist_bookings_sorted[0]

    # Reload the booking to see its updated status
    updated_booking = booking_repo.find_by_id(first_waitlisted.id)
    if updated_booking is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Booking disappeared during promotion",
        )

    return {
        "message": "User promoted from waitlist successfully",
        "booking_id": updated_booking.id,
        "user_id": updated_booking.user_id,
    }


# ---------------------------------------------------------------------------
# Booking routes (authenticated)
# ---------------------------------------------------------------------------

@app.post("/api/v1/bookings", response_model=BookingResponse, status_code=201)
def create_booking(
    payload: BookingCreate,
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> Dict[str, Any]:
    """Create a booking for a session (authenticated)."""
    # Look up the session
    session = session_repo.find_by_id(payload.session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Check if user already has an active booking for this session
    existing_bookings = booking_repo.find_by_user(current_user.id)
    for b in existing_bookings:
        if b.session_id == payload.session_id and b.status != Booking.CANCELLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already has an active booking for this session",
            )

    # Determine next booking id
    all_bookings = booking_repo.find_all()
    next_id = max((b.id for b in all_bookings), default=0) + 1

    # Determine booking status
    if session.enrolled < session.capacity:
        # Session has spots available
        if current_user.credits <= 0:
            raise HTTPException(
                status_code=402,
                detail="Insufficient credits to confirm booking",
            )
        # Confirmed: deduct credit and increment enrolled
        booking_status = Booking.CONFIRMED
        current_user.credits -= 1
        user_repo.save_one(current_user)
        session.enrolled += 1
        session_repo.save_one(session)
    else:
        # Session is full: goes to waitlist
        booking_status = Booking.WAITLIST

    booking = Booking(
        id=next_id,
        user_id=current_user.id,
        session_id=payload.session_id,
        status=booking_status,
    )
    booking_repo.save_one(booking)

    result = booking.to_dict()
    result["session"] = session.to_dict()
    return result


@app.get("/api/v1/bookings/me", response_model=List[BookingResponse])
def list_my_bookings(
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> List[Dict[str, Any]]:
    """List bookings of the authenticated user with session details."""
    bookings = booking_repo.find_by_user(current_user.id)
    result = []
    for b in bookings:
        b_dict = b.to_dict()
        session = session_repo.find_by_id(b.session_id)
        b_dict["session"] = session.to_dict() if session else None
        result.append(b_dict)
    return result


@app.delete("/api/v1/bookings/{booking_id}", status_code=204)
def cancel_booking(
    booking_id: int,
    current_user: User = Depends(get_current_user),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    session_repo: SessionRepository = Depends(_get_session_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> None:
    """Cancel a booking (authenticated). Only the owner may cancel.

    If the booking was 'confirmed', the credit is returned, enrolled is
    decremented, and a waitlisted user may be automatically promoted.
    """
    booking = booking_repo.find_by_id(booking_id)
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Booking not found",
        )

    # Verify ownership
    if booking.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot cancel another user's booking",
        )

    # If already cancelled, treat as no-op (still return 204)
    if booking.status == Booking.CANCELLED:
        return None

    # Remember original status for waitlist promotion decision
    was_confirmed = booking.status == Booking.CONFIRMED

    # If confirmed: return credit and decrement enrolled
    if was_confirmed:
        current_user.credits += 1
        user_repo.save_one(current_user)

        session = session_repo.find_by_id(booking.session_id)
        if session is not None and session.enrolled > 0:
            session.enrolled -= 1
            session_repo.save_one(session)

    # Cancel the booking
    booking.status = Booking.CANCELLED
    booking_repo.save_one(booking)

    # Feature #9: promote from waitlist if a spot opened up
    if was_confirmed:
        promote_from_waitlist(
            booking.session_id,
            booking_repo,
            session_repo,
            user_repo,
        )

    return None


# ---------------------------------------------------------------------------
# Notification routes (authenticated) — Feature #10
# ---------------------------------------------------------------------------

@app.get("/api/v1/users/me/notifications", response_model=List[NotificationResponse])
def list_my_notifications(
    current_user: User = Depends(get_current_user),
    notification_repo: NotificationRepository = Depends(_get_notification_repo),
) -> List[Dict[str, Any]]:
    """Return all notifications for the current user, ordered by created_at desc."""
    all_notifications = notification_repo.find_by_user(current_user.id)
    # Sort descending by created_at
    all_notifications.sort(key=lambda n: n.created_at, reverse=True)
    return [n.to_dict() for n in all_notifications]


@app.put(
    "/api/v1/users/me/notifications/{notification_id}/read",
    response_model=NotificationResponse,
)
def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    notification_repo: NotificationRepository = Depends(_get_notification_repo),
) -> Dict[str, Any]:
    """Mark a notification as read (read_at = now)."""
    notification = notification_repo.find_by_id(notification_id)
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    # Forbid reading another user's notification
    if notification.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot mark another user's notification as read",
        )

    # Set read_at to now
    notification.read_at = datetime.utcnow()
    notification_repo.save_one(notification)
    return notification.to_dict()


# ---------------------------------------------------------------------------
# Credits & Admin Panel routes — Feature #9
# ---------------------------------------------------------------------------

@app.post(
    "/api/v1/users/{user_id}/credits",
    response_model=UserAdminResponse,
    status_code=200,
)
def add_credits_with_reason(
    user_id: int,
    payload: AddCreditsWithReasonRequest,
    _admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(_get_user_repo),
    ct_repo: CreditTransactionRepository = Depends(_get_credit_transaction_repo),
) -> Dict[str, Any]:
    """Add credits to a user and record the transaction. Admin only.

    Body: {amount: int (1-100), reason: str}.
    """
    user = user_repo.find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Add credits to user
    user.credits += payload.amount
    user_repo.save_one(user)

    # Record credit transaction
    ct_id = ct_repo.next_id()
    ct = CreditTransaction(
        id=ct_id,
        user_id=user_id,
        amount=payload.amount,
        reason=payload.reason,
    )
    ct_repo.save_one(ct)

    result = user.to_dict()
    result.pop("password_hash", None)
    return result


@app.get(
    "/api/v1/users/{user_id}/credits/history",
    response_model=List[CreditTransactionResponse],
)
def get_credit_history(
    user_id: int,
    current_user: User = Depends(get_current_user),
    ct_repo: CreditTransactionRepository = Depends(_get_credit_transaction_repo),
) -> List[Dict[str, Any]]:
    """List credit transactions for a user. Admin or the user themselves."""
    # Authorization: admin or the same user
    if current_user.role != "admin" and current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required to view another user's credit history",
        )

    transactions = ct_repo.find_by_user_id(user_id)
    return [t.to_dict() for t in transactions]


@app.get("/api/v1/admin/users", response_model=List[UserAdminResponse])
def list_users_admin_panel(
    _admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> List[Dict[str, Any]]:
    """List all users. Admin only. Excludes password_hash."""
    users = user_repo.find_all()
    result = []
    for u in users:
        d = u.to_dict()
        d.pop("password_hash", None)
        result.append(d)
    return result


@app.get(
    "/api/v1/admin/sessions/{session_id}/attendees",
    response_model=List[AttendeeResponse],
)
def list_attendees_admin_panel(
    session_id: int,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> List[Dict[str, Any]]:
    """List confirmed attendees for a session. Admin only."""
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    bookings = booking_repo.find_by_session(session_id)
    result: List[Dict[str, Any]] = []
    for b in bookings:
        # Only confirmed bookings are considered attendees
        if b.status != Booking.CONFIRMED:
            continue
        user = user_repo.find_by_id(b.user_id)
        created_at_str = b.created_at.isoformat() if hasattr(b.created_at, "isoformat") else str(b.created_at)
        result.append({
            "booking_id": b.id,
            "user_id": b.user_id,
            "user_name": user.name if user else "Unknown",
            "user_email": user.email if user else "unknown@example.com",
            "status": b.status,
            "created_at": created_at_str,
        })
    return result


# ---------------------------------------------------------------------------
# Admin users routes — Feature #12
# ---------------------------------------------------------------------------

@app.get("/api/v1/users", response_model=List[UserAdminResponse])
def list_users_admin(
    _admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> List[Dict[str, Any]]:
    """List all users. Admin only. Excludes password_hash."""
    users = user_repo.find_all()
    result = []
    for u in users:
        d = u.to_dict()
        d.pop("password_hash", None)
        result.append(d)
    return result


@app.put("/api/v1/users/{user_id}/credits", response_model=UserAdminResponse)
def add_credits(
    user_id: int,
    payload: AddCreditsRequest,
    _admin: User = Depends(require_admin),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> Dict[str, Any]:
    """Add credits to a user. Admin only."""
    user = user_repo.find_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.credits += payload.credits
    user_repo.save_one(user)

    result = user.to_dict()
    result.pop("password_hash", None)
    return result


# ---------------------------------------------------------------------------
# Admin session attendees route — Feature #12
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/sessions/{session_id}/attendees",
    response_model=List[AttendeeResponse],
)
def list_session_attendees(
    session_id: int,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
    booking_repo: BookingRepository = Depends(_get_booking_repo),
    user_repo: UserRepository = Depends(_get_user_repo),
) -> List[Dict[str, Any]]:
    """List all confirmed attendees for a session. Admin only."""
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    bookings = booking_repo.find_by_session(session_id)
    result: List[Dict[str, Any]] = []
    for b in bookings:
        # Only confirmed bookings are considered attendees
        if b.status != Booking.CONFIRMED:
            continue
        user = user_repo.find_by_id(b.user_id)
        created_at_str = b.created_at.isoformat() if hasattr(b.created_at, "isoformat") else str(b.created_at)
        result.append({
            "booking_id": b.id,
            "user_id": b.user_id,
            "user_name": user.name if user else "Unknown",
            "user_email": user.email if user else "unknown@example.com",
            "status": b.status,
            "created_at": created_at_str,
        })
    return result


# ---------------------------------------------------------------------------
# Stats endpoints
# ---------------------------------------------------------------------------


@app.get("/stats/instructors")
def get_instructor_stats():
    """Public endpoint: aggregated stats per instructor."""
    session_repo = SessionRepository()
    sessions = session_repo.find_all()

    # Group by instructor name and assign synthetic ids
    aggregated: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        name = s.instructor
        if name not in aggregated:
            aggregated[name] = {
                "name": name,
                "sessions_count": 0,
                "total_enrolled": 0,
            }
        aggregated[name]["sessions_count"] += 1
        aggregated[name]["total_enrolled"] += s.enrolled

    # Build result list with synthetic ids (sorted by name for determinism)
    result: List[Dict[str, Any]] = []
    for idx, (name, data) in enumerate(sorted(aggregated.items()), start=1):
        result.append({
            "id": idx,
            "name": data["name"],
            "sessions_count": data["sessions_count"],
            "total_enrolled": data["total_enrolled"],
        })

    return result


@app.get("/stats/styles")
def get_style_stats():
    """Public endpoint: aggregated stats per session style."""
    session_repo = SessionRepository()
    sessions = session_repo.find_all()

    stats: Dict[str, Dict[str, Any]] = {}
    for s in sessions:
        if s.style not in stats:
            stats[s.style] = {
                "style": s.style,
                "sessions_count": 0,
                "total_enrolled": 0,
            }
        stats[s.style]["sessions_count"] += 1
        stats[s.style]["total_enrolled"] += s.enrolled

    return list(stats.values())


@app.get("/stats/users")
def get_user_stats(
    current_user: User = Depends(require_admin),
):
    """Admin endpoint: top 10 users by non-cancelled bookings."""
    return select_top_users(10)
