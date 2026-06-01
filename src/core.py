"""Core business logic shared across the application."""

from typing import List, Dict, Any

from src.repositories.bookings import BookingRepository
from src.repositories.users import UserRepository
from src.models.booking import Booking


def select_top_users(limit: int) -> List[Dict[str, Any]]:
    """Return the top users sorted by their non-cancelled booking count.

    Args:
        limit: Maximum number of users to return.

    Returns:
        A list of dicts with keys user_id, name, email, and bookings (count).
        The list is sorted by bookings descending, then user_id ascending.
    """
    booking_repo = BookingRepository()
    user_repo = UserRepository()

    bookings = booking_repo.find_all()
    users = user_repo.find_all()

    # Count non-cancelled bookings per user
    user_booking_count: Dict[int, int] = {}
    for b in bookings:
        if b.status != Booking.CANCELLED:
            user_booking_count[b.user_id] = (
                user_booking_count.get(b.user_id, 0) + 1
            )

    # Map user id -> user object for quick lookup
    user_map = {u.id: u for u in users}

    # Build result list
    result: List[Dict[str, Any]] = []
    for user_id, count in user_booking_count.items():
        user = user_map.get(user_id)
        if user is not None:
            result.append({
                "user_id": user.id,
                "name": user.name,
                "email": user.email,
                "bookings": count,
            })

    # Sort by booking count descending, then by user_id ascending for stability
    result.sort(key=lambda x: (-x["bookings"], x["user_id"]))

    return result[:limit]



def notify_user(user_id: int, message: str) -> None:
    """Store a notification for a user.

    Args:
        user_id: The recipient user's id.
        message: The notification message text.
    """
    from src.repositories.notifications import NotificationRepository
    from src.models.notification import Notification

    repo = NotificationRepository()
    all_notifications = repo.find_all()
    next_id = max((n.id for n in all_notifications), default=0) + 1

    notification = Notification(
        id=next_id,
        user_id=user_id,
        message=message,
    )
    repo.save_one(notification)


def promote_from_waitlist(session_id: int, booking_repo, session_repo, user_repo) -> bool:
    """Promote the first eligible waitlisted user for a session.

    Scans bookings for the given session that are in 'waitlist' status,
    ordered by created_at ascending. The first user with at least 1 credit
    is promoted to 'confirmed': 1 credit deducted, session.enrolled is
    incremented by 1, and a notification is sent.

    Args:
        session_id: The session whose waitlist to scan.
        booking_repo: BookingRepository instance.
        session_repo: SessionRepository instance.
        user_repo: UserRepository instance.

    Returns:
        True if a promotion happened, False otherwise.
    """
    from src.models.booking import Booking

    waitlisted = [
        b for b in booking_repo.find_by_session(session_id)
        if b.status == Booking.WAITLIST
    ]
    # Sort by created_at ascending (earliest first)
    waitlisted.sort(key=lambda b: b.created_at)

    for booking in waitlisted:
        user = user_repo.find_by_id(booking.user_id)
        if user is None:
            continue
        if user.credits >= 1:
            # Promote to confirmed
            user.credits -= 1
            user_repo.save_one(user)

            booking.status = Booking.CONFIRMED
            booking_repo.save_one(booking)

            # Increment enrolled — freed spot is now taken by promoted user
            session = session_repo.find_by_id(session_id)
            if session is not None:
                session.enrolled += 1
                session_repo.save_one(session)

            notify_user(
                booking.user_id,
                f"You have been promoted from the waitlist for session {session_id}.",
            )
            return True

    return False
