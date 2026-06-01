"""Mini FastAPI app to expose Session and Booking models for E2E testing."""

import sys
import os
from datetime import datetime

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from src.models.session import Session
from src.models.booking import Booking

app = FastAPI(title="E2E Test App - Sessions & Bookings")

# In-memory stores
sessions_db: dict[int, Session] = {}
bookings_db: dict[int, Booking] = {}


def _render_page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{title}</title>
    <style>
        body {{ font-family: sans-serif; max-width: 600px; margin: 2rem auto; padding: 0 1rem; }}
        h1 {{ color: #333; }}
        .session, .booking {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
        .field {{ margin: 0.3rem 0; }}
        .label {{ font-weight: bold; }}
        .error {{ color: #c00; background: #fee; padding: 0.5rem; border-radius: 4px; margin: 1rem 0; }}
        .success {{ color: #060; background: #efe; padding: 0.5rem; border-radius: 4px; margin: 1rem 0; }}
        form {{ display: flex; flex-direction: column; gap: 0.5rem; max-width: 400px; }}
        input, select, button {{ padding: 0.5rem; font-size: 1rem; }}
        button {{ background: #0066cc; color: white; border: none; border-radius: 4px; cursor: pointer; }}
        button:hover {{ background: #0055aa; }}
        .badge {{ display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.85rem; }}
        .badge-full {{ background: #fcc; color: #a00; }}
        .badge-available {{ background: #cfc; color: #060; }}
        .badge-confirmed {{ background: #cfc; color: #060; }}
        .badge-cancelled {{ background: #fcc; color: #a00; }}
        .badge-waitlist {{ background: #ffe; color: #aa0; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {body}
    <hr>
    <a href="/">Home</a> |
    <a href="/sessions/create">Create Session</a> | <a href="/sessions">List Sessions</a> |
    <a href="/bookings/create">Create Booking</a> | <a href="/bookings">List Bookings</a>
</body>
</html>"""


# ── Home ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home():
    body = "<p>E2E Test App for Session &amp; Booking models</p>"
    body += f"<p>Sessions in store: <strong>{len(sessions_db)}</strong></p>"
    body += f"<p>Bookings in store: <strong>{len(bookings_db)}</strong></p>"
    return _render_page("Home - E2E", body)


# ── Session endpoints ───────────────────────────────────────────────────────

@app.get("/sessions/create", response_class=HTMLResponse)
async def create_session_form():
    body = """
    <form method="post" action="/sessions/create">
        <label>ID: <input type="number" name="id" value="1" required></label>
        <label>Title: <input type="text" name="title" value="Morning Yoga" required></label>
        <label>Instructor: <input type="text" name="instructor" value="Alice" required></label>
        <label>Style: <input type="text" name="style" value="Vinyasa" required></label>
        <label>Starts At: <input type="text" name="starts_at" value="2025-06-15T09:00:00" required></label>
        <label>Duration (min): <input type="number" name="duration_minutes" value="60" required></label>
        <label>Capacity: <input type="number" name="capacity" value="20" required></label>
        <label>Enrolled: <input type="number" name="enrolled" value="0"></label>
        <button type="submit">Create Session</button>
    </form>
    """
    return _render_page("Create Session", body)


@app.post("/sessions/create", response_class=HTMLResponse)
async def create_session_submit(
    id: int = Form(...),
    title: str = Form(...),
    instructor: str = Form(...),
    style: str = Form(...),
    starts_at: str = Form(...),
    duration_minutes: int = Form(...),
    capacity: int = Form(...),
    enrolled: int = Form(0),
):
    try:
        dt = datetime.fromisoformat(starts_at)
        session = Session(
            id=id,
            title=title,
            instructor=instructor,
            style=style,
            starts_at=dt,
            duration_minutes=duration_minutes,
            capacity=capacity,
            enrolled=enrolled,
        )
        sessions_db[session.id] = session
        body = f"""
        <div class="success">Session created successfully!</div>
        <div class="session">
            <div class="field"><span class="label">ID:</span> {session.id}</div>
            <div class="field"><span class="label">Title:</span> {session.title}</div>
            <div class="field"><span class="label">Instructor:</span> {session.instructor}</div>
            <div class="field"><span class="label">Style:</span> {session.style}</div>
            <div class="field"><span class="label">Starts At:</span> {session.starts_at.isoformat()}</div>
            <div class="field"><span class="label">Duration:</span> {session.duration_minutes} min</div>
            <div class="field"><span class="label">Capacity:</span> {session.capacity}</div>
            <div class="field"><span class="label">Enrolled:</span> {session.enrolled}</div>
            <div class="field"><span class="label">Is Full:</span> <span class="badge {'badge-full' if session.is_full() else 'badge-available'}">{session.is_full()}</span></div>
            <div class="field"><span class="label">Spots Available:</span> {session.spots_available()}</div>
        </div>
        <p><a href="/sessions/{session.id}">View session details</a></p>
        """
        return _render_page("Session Created", body)
    except ValueError as e:
        body = f'<div class="error">Error: {e}</div>'
        body += '<p><a href="/sessions/create">Try again</a></p>'
        return _render_page("Error", body)


@app.get("/sessions", response_class=HTMLResponse)
async def list_sessions():
    if not sessions_db:
        body = "<p>No sessions created yet.</p>"
    else:
        body = ""
        for s in sessions_db.values():
            full_status = "FULL" if s.is_full() else f"{s.spots_available()} spots left"
            badge_class = "badge-full" if s.is_full() else "badge-available"
            body += f"""
            <div class="session">
                <div class="field"><span class="label">#{s.id}:</span> {s.title}</div>
                <div class="field"><span class="label">Instructor:</span> {s.instructor}</div>
                <div class="field"><span class="label">Status:</span> <span class="badge {badge_class}">{full_status}</span></div>
                <div class="field"><span class="label">Enrolled/Capacity:</span> {s.enrolled}/{s.capacity}</div>
                <p><a href="/sessions/{s.id}">View details</a></p>
            </div>
            """
    body += '<p><a href="/sessions/create">Create new session</a></p>'
    return _render_page("All Sessions", body)


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
async def view_session(session_id: int):
    session = sessions_db.get(session_id)
    if session is None:
        body = f'<div class="error">Session #{session_id} not found.</div>'
        return _render_page("Not Found", body)

    full_status = "Yes" if session.is_full() else "No"
    badge_class = "badge-full" if session.is_full() else "badge-available"
    body = f"""
    <div class="session">
        <div class="field"><span class="label">ID:</span> {session.id}</div>
        <div class="field"><span class="label">Title:</span> {session.title}</div>
        <div class="field"><span class="label">Instructor:</span> {session.instructor}</div>
        <div class="field"><span class="label">Style:</span> {session.style}</div>
        <div class="field"><span class="label">Starts At:</span> {session.starts_at.isoformat()}</div>
        <div class="field"><span class="label">Duration:</span> {session.duration_minutes} min</div>
        <div class="field"><span class="label">Capacity:</span> {session.capacity}</div>
        <div class="field"><span class="label">Enrolled:</span> {session.enrolled}</div>
        <div class="field"><span class="label">Is Full:</span> <span class="badge {badge_class}" id="is-full-badge">{full_status}</span></div>
        <div class="field"><span class="label">Spots Available:</span> <span id="spots-available">{session.spots_available()}</span></div>
    </div>
    """
    return _render_page(f"Session #{session.id}", body)


@app.get("/api/sessions/{session_id}", response_class=JSONResponse)
async def api_get_session(session_id: int):
    session = sessions_db.get(session_id)
    if session is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    data = session.to_dict()
    data["is_full"] = session.is_full()
    data["spots_available"] = session.spots_available()
    return data


@app.post("/api/sessions", response_class=JSONResponse)
async def api_create_session(request: Request):
    try:
        data = await request.json()
        session = Session.from_dict(data)
        sessions_db[session.id] = session
        result = session.to_dict()
        result["is_full"] = session.is_full()
        result["spots_available"] = session.spots_available()
        return result
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)


# ── Booking endpoints ───────────────────────────────────────────────────────

@app.get("/bookings/create", response_class=HTMLResponse)
async def create_booking_form():
    body = """
    <form method="post" action="/bookings/create">
        <label>ID: <input type="number" name="id" value="1" required></label>
        <label>User ID: <input type="number" name="user_id" value="10" required></label>
        <label>Session ID: <input type="number" name="session_id" value="100" required></label>
        <label>Status:
            <select name="status">
                <option value="waitlist">waitlist</option>
                <option value="confirmed">confirmed</option>
                <option value="cancelled">cancelled</option>
            </select>
        </label>
        <button type="submit">Create Booking</button>
    </form>
    """
    return _render_page("Create Booking", body)


@app.post("/bookings/create", response_class=HTMLResponse)
async def create_booking_submit(
    id: int = Form(...),
    user_id: int = Form(...),
    session_id: int = Form(...),
    status: str = Form("waitlist"),
):
    try:
        booking = Booking(
            id=id,
            user_id=user_id,
            session_id=session_id,
            status=status,
        )
        bookings_db[booking.id] = booking
        badge_class = f"badge-{booking.status}"
        body = f"""
        <div class="success">Booking created successfully!</div>
        <div class="booking" id="booking-detail">
            <div class="field"><span class="label">ID:</span> <span id="booking-id">{booking.id}</span></div>
            <div class="field"><span class="label">User ID:</span> <span id="booking-user-id">{booking.user_id}</span></div>
            <div class="field"><span class="label">Session ID:</span> <span id="booking-session-id">{booking.session_id}</span></div>
            <div class="field"><span class="label">Status:</span> <span class="badge {badge_class}" id="booking-status">{booking.status}</span></div>
            <div class="field"><span class="label">Created At:</span> <span id="booking-created-at">{booking.created_at.isoformat()}</span></div>
        </div>
        <p><a href="/bookings/{booking.id}">View booking details</a></p>
        """
        return _render_page("Booking Created", body)
    except ValueError as e:
        body = f'<div class="error" id="booking-error">Error: {e}</div>'
        body += '<p><a href="/bookings/create">Try again</a></p>'
        return _render_page("Error", body)


@app.get("/bookings", response_class=HTMLResponse)
async def list_bookings():
    if not bookings_db:
        body = "<p>No bookings created yet.</p>"
    else:
        body = ""
        for b in bookings_db.values():
            badge_class = f"badge-{b.status}"
            body += f"""
            <div class="booking" id="booking-{b.id}">
                <div class="field"><span class="label">#{b.id}:</span> User #{b.user_id} → Session #{b.session_id}</div>
                <div class="field"><span class="label">Status:</span> <span class="badge {badge_class}">{b.status}</span></div>
                <div class="field"><span class="label">Created:</span> {b.created_at.isoformat()}</div>
                <p><a href="/bookings/{b.id}">View details</a></p>
            </div>
            """
    body += '<p><a href="/bookings/create">Create new booking</a></p>'
    return _render_page("All Bookings", body)


@app.get("/bookings/{booking_id}", response_class=HTMLResponse)
async def view_booking(booking_id: int):
    booking = bookings_db.get(booking_id)
    if booking is None:
        body = f'<div class="error" id="booking-error">Booking #{booking_id} not found.</div>'
        return _render_page("Not Found", body)

    badge_class = f"badge-{booking.status}"
    body = f"""
    <div class="booking" id="booking-detail">
        <div class="field"><span class="label">ID:</span> <span id="booking-id">{booking.id}</span></div>
        <div class="field"><span class="label">User ID:</span> <span id="booking-user-id">{booking.user_id}</span></div>
        <div class="field"><span class="label">Session ID:</span> <span id="booking-session-id">{booking.session_id}</span></div>
        <div class="field"><span class="label">Status:</span> <span class="badge {badge_class}" id="booking-status">{booking.status}</span></div>
        <div class="field"><span class="label">Created At:</span> <span id="booking-created-at">{booking.created_at.isoformat()}</span></div>
    </div>
    <p><a href="/bookings">Back to all bookings</a></p>
    """
    return _render_page(f"Booking #{booking.id}", body)


@app.get("/api/bookings/{booking_id}", response_class=JSONResponse)
async def api_get_booking(booking_id: int):
    booking = bookings_db.get(booking_id)
    if booking is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return booking.to_dict()


@app.post("/api/bookings", response_class=JSONResponse)
async def api_create_booking(request: Request):
    try:
        data = await request.json()
        booking = Booking.from_dict(data)
        bookings_db[booking.id] = booking
        return booking.to_dict()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)

# ── User endpoints ──────────────────────────────────────────────────────────

from src.models.user import User

users_db: dict[int, User] = {}


@app.get("/users/create", response_class=HTMLResponse)
async def create_user_form():
    body = """
    <form method="post" action="/users/create">
        <label>ID: <input type="number" name="id" value="1" required></label>
        <label>Name: <input type="text" name="name" value="Alice" required></label>
        <label>Email: <input type="text" name="email" value="alice@example.com" required></label>
        <label>Credits: <input type="number" name="credits" value="0"></label>
        <label>Role:
            <select name="role">
                <option value="client">client</option>
                <option value="admin">admin</option>
            </select>
        </label>
        <button type="submit">Create User</button>
    </form>
    """
    return _render_page("Create User", body)


@app.post("/users/create", response_class=HTMLResponse)
async def create_user_submit(
    id: int = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    credits: int = Form(0),
    role: str = Form("client"),
):
    try:
        user = User(
            id=id,
            name=name,
            email=email,
            credits=credits,
            role=role,
        )
        users_db[user.id] = user
        body = f"""
        <div class="success" id="user-success">User created successfully!</div>
        <div class="session" id="user-detail">
            <div class="field"><span class="label">ID:</span> <span id="user-id">{user.id}</span></div>
            <div class="field"><span class="label">Name:</span> <span id="user-name">{user.name}</span></div>
            <div class="field"><span class="label">Email:</span> <span id="user-email">{user.email}</span></div>
            <div class="field"><span class="label">Credits:</span> <span id="user-credits">{user.credits}</span></div>
            <div class="field"><span class="label">Role:</span> <span id="user-role">{user.role}</span></div>
            <div class="field"><span class="label">Created At:</span> <span id="user-created-at">{user.created_at.isoformat()}</span></div>
        </div>
        <p><a href="/users/{user.id}">View user details</a></p>
        """
        return _render_page("User Created", body)
    except ValueError as e:
        body = f'<div class="error" id="user-error">Error: {e}</div>'
        body += '<p><a href="/users/create">Try again</a></p>'
        return _render_page("Error", body)


@app.get("/users", response_class=HTMLResponse)
async def list_users():
    if not users_db:
        body = "<p>No users created yet.</p>"
    else:
        body = ""
        for u in users_db.values():
            body += f"""
            <div class="session" id="user-{u.id}">
                <div class="field"><span class="label">#{u.id}:</span> {u.name}</div>
                <div class="field"><span class="label">Email:</span> {u.email}</div>
                <div class="field"><span class="label">Role:</span> {u.role}</div>
                <div class="field"><span class="label">Credits:</span> {u.credits}</div>
                <p><a href="/users/{u.id}">View details</a></p>
            </div>
            """
    body += '<p><a href="/users/create">Create new user</a></p>'
    return _render_page("All Users", body)


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def view_user(user_id: int):
    user = users_db.get(user_id)
    if user is None:
        body = f'<div class="error" id="user-error">User #{user_id} not found.</div>'
        return _render_page("Not Found", body)

    body = f"""
    <div class="session" id="user-detail">
        <div class="field"><span class="label">ID:</span> <span id="user-id">{user.id}</span></div>
        <div class="field"><span class="label">Name:</span> <span id="user-name">{user.name}</span></div>
        <div class="field"><span class="label">Email:</span> <span id="user-email">{user.email}</span></div>
        <div class="field"><span class="label">Credits:</span> <span id="user-credits">{user.credits}</span></div>
        <div class="field"><span class="label">Role:</span> <span id="user-role">{user.role}</span></div>
        <div class="field"><span class="label">Created At:</span> <span id="user-created-at">{user.created_at.isoformat()}</span></div>
    </div>
    <p><a href="/users">Back to all users</a></p>
    """
    return _render_page(f"User #{user.id}", body)


@app.get("/api/users/{user_id}", response_class=JSONResponse)
async def api_get_user(user_id: int):
    user = users_db.get(user_id)
    if user is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return user.to_dict()


@app.post("/api/users", response_class=JSONResponse)
async def api_create_user(request: Request):
    try:
        data = await request.json()
        user = User.from_dict(data)
        users_db[user.id] = user
        return user.to_dict()
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
