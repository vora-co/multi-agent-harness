"""Session management endpoints.

GET endpoints (list, get) are public.
POST, PUT, DELETE endpoints are protected by require_admin.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator

from src.auth import require_admin
from src.models.session import Session
from src.models.user import User
from src.repositories.sessions import SessionRepository

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def _get_session_repo() -> SessionRepository:
    """Dependency: create a SessionRepository instance."""
    return SessionRepository()


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    """Schema for creating a new session (POST)."""
    title: str
    instructor: str
    style: str
    starts_at: str
    duration_minutes: int
    capacity: int

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration_minutes(cls, v: int) -> int:
        if v < 15:
            raise ValueError("duration_minutes must be >= 15")
        return v

    @field_validator("capacity")
    @classmethod
    def validate_capacity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("capacity must be >= 1")
        return v


class SessionUpdate(BaseModel):
    """Schema for updating an existing session (PUT)."""
    title: Optional[str] = None
    instructor: Optional[str] = None
    style: Optional[str] = None
    starts_at: Optional[str] = None
    duration_minutes: Optional[int] = None
    capacity: Optional[int] = None

    @field_validator("duration_minutes")
    @classmethod
    def validate_duration_minutes(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 15:
            raise ValueError("duration_minutes must be >= 15")
        return v

    @field_validator("capacity")
    @classmethod
    def validate_capacity(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 1:
            raise ValueError("capacity must be >= 1")
        return v


class SessionResponse(BaseModel):
    """Schema for session responses."""
    id: int
    title: str
    instructor: str
    style: str
    starts_at: str
    duration_minutes: int
    capacity: int
    enrolled: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=List[SessionResponse])
def list_sessions(
    style: Optional[str] = Query(None, description="Filter by session style"),
    date: Optional[str] = Query(None, description="Filter by start date (YYYY-MM-DD)"),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> List[Dict[str, Any]]:
    """List all sessions, optionally filtered by style and/or date. Public."""
    sessions = session_repo.find_all()

    if style is not None:
        sessions = [s for s in sessions if s.style == style]

    if date is not None:
        try:
            filter_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )
        sessions = [
            s for s in sessions if s.starts_at.date() == filter_date
        ]

    return [s.to_dict() for s in sessions]


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: int,
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> Dict[str, Any]:
    """Return a single session by its id. Public."""
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    return session.to_dict()


@router.post("", response_model=SessionResponse, status_code=201)
def create_session(
    payload: SessionCreate,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> Dict[str, Any]:
    """Create a new session. Admin only."""
    # Determine next id
    all_sessions = session_repo.find_all()
    next_id = max((s.id for s in all_sessions), default=0) + 1

    # Parse starts_at
    try:
        starts_at = datetime.fromisoformat(payload.starts_at)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid starts_at format. Use ISO 8601 (e.g. 2025-06-15T09:00:00).",
        )

    session = Session(
        id=next_id,
        title=payload.title,
        instructor=payload.instructor,
        style=payload.style,
        starts_at=starts_at,
        duration_minutes=payload.duration_minutes,
        capacity=payload.capacity,
    )
    session_repo.save_one(session)
    return session.to_dict()


@router.put("/{session_id}", response_model=SessionResponse)
def update_session(
    session_id: int,
    payload: SessionUpdate,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> Dict[str, Any]:
    """Update an existing session. Admin only."""
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Apply partial updates
    update_data = payload.model_dump(exclude_unset=True)

    if "starts_at" in update_data and update_data["starts_at"] is not None:
        try:
            update_data["starts_at"] = datetime.fromisoformat(update_data["starts_at"])
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid starts_at format. Use ISO 8601.",
            )

    # Build updated dict from the existing session and merge updates
    current_dict = session.to_dict()
    current_dict.update(update_data)

    # Re-parse starts_at if it's a string
    if isinstance(current_dict["starts_at"], str):
        current_dict["starts_at"] = datetime.fromisoformat(current_dict["starts_at"])

    updated_session = Session.from_dict(current_dict)
    session_repo.save_one(updated_session)
    return updated_session.to_dict()


@router.delete("/{session_id}", status_code=204)
def delete_session(
    session_id: int,
    _admin: User = Depends(require_admin),
    session_repo: SessionRepository = Depends(_get_session_repo),
) -> None:
    """Delete a session. Admin only. Only allowed if enrolled == 0."""
    session = session_repo.find_by_id(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.enrolled > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete session with enrolled participants",
        )

    session_repo.delete(session_id)
    return None
